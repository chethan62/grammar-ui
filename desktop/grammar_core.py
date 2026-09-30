#!/usr/bin/python3
"""The headless half of the desktop clients: the finding logic and the card's wire contract.

Nothing here imports gi, Qt or subprocess. That is the whole point of the module: the parts of
these clients that are just rules — which finding is being shown, what the card is allowed to
show, where it fits on a screen, how a rewrite request is shaped, what the settings panel draws —
can be read, reasoned about and tested with any Python, on a machine with no display, no
accessibility bus and no toolkit. Before this existed, every pure assertion in the gates had to
launch the *system* python through a re-exec dance, because importing the client module meant
importing gi (grammar-watch.py) or exiting 2 when Qt is absent (grammar-popup.py).

Leaves-first: this module imports only the standard library, and the clients import it. There is
no cycle and there is nothing here that talks to a bus, a screen or a process.

The two clients keep their own halves: grammar-watch.py owns the accessibility bus, the daemon and
the toast; grammar-popup.py owns the Qt surface and the process contract. Both re-export what they
use from here, because callers and gates have always reached these names through the client.
"""

import json
import os
import urllib.error
import urllib.request

# ---- reading the text you are typing into (pure arithmetic over a string) ----------------------

# ponytail: fixed-size read around the caret — one cheap read per pause instead of reading a
# whole 50-page document. Widen BACK if suggestions ever miss a sentence that starts further up.
BACK, FORWARD, TAIL = 500, 300, 240
MIN_CHARS = 12


def snippet_window(text, caret, back=BACK, forward=FORWARD):
    """The whole sentences around `caret` as (start, end) offsets into `text`.

    Snapping back to a sentence boundary keeps the engine from being handed a fragment, which
    it duly reports as an incomplete sentence.
    """
    start = max(0, caret - back)
    # From caret - 2, not caret - 1: while typing, the caret sits right after the final period
    # of the sentence just written, and snapping to that boundary returns an empty window.
    for i in range(caret - 2, start - 1, -1):
        if text[i] in ".!?\n":
            start = i + 1
            break
    end = min(len(text), caret + forward)
    limit = min(len(text), end + TAIL)
    for i in range(end, limit):
        if text[i] in ".!?\n":
            end = i + 1
            break
        end = i + 1
    while start < end and text[start].isspace():
        start += 1
    return start, end


# ---- the engine's findings, turned into something a card can show -------------------------------

def suggestions(text, matches):
    """(old, new, reason) per finding, in document order.

    Structured rather than pre-rendered, because the card shows the diff and the reason apart,
    while a notification only has room for "old → new".
    """
    out = []
    for m in sorted(matches, key=lambda m: m["offset"]):
        if not m.get("replacements"):
            continue
        old = text[m["offset"]:m["offset"] + m["length"]].strip()
        out.append((old, m["replacements"][0]["value"], m["message"].strip().replace("\n", " ")))
    return out


def shown(matches):
    """The one finding the card is showing: the first in document order that carries a replacement.

    This rule had three copies — in alternatives(), in first_span() and in the count for "more
    issues" — and two of them carried a comment warning that they must not drift, because a chip
    that picked a different finding than the span would rewrite the text somewhere else in the
    sentence. It lives here once now. Pure.
    """
    for m in sorted(matches, key=lambda m: m["offset"]):
        if m.get("replacements"):
            return m
    return None


def alternatives(matches):
    """Every replacement the engine offered for the finding the card is showing, in its own order.

    The card used to receive only replacements[0], so a spelling fix offered "Teh" and hid "the",
    "tea" and "tech" — the engine had already found them. Pure, so the ordering and the
    de-duplication are testable without a display.
    """
    match = shown(matches)
    if match is None:
        return []
    out = []
    for rep in match["replacements"]:
        value = (rep.get("value") or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def first_span(matches):
    """Where the finding the card is showing sits, as (offset, length) in the checked window.

    It must pick the same match alternatives() does — both now call shown() — or a chip would
    rewrite the text somewhere else in the sentence. The pair is asserted together in
    test-watch.py for that reason.
    """
    match = shown(matches)
    if match is None:
        return 0, 0
    return int(match["offset"]), int(match["length"])


def others(matches):
    """How many *other* findings with a fix the same sentence has, for the card to mention.

    A sentence carrying two issues showed one and said nothing about the second, so fixing what
    the card named left the line still underlined with no explanation — measured on this project's
    own sample, "She go to the office.", which the engine flags twice. Pure.
    """
    with_fix = [m for m in matches if m.get("replacements")]
    return max(0, len(with_fix) - (1 if shown(matches) is not None else 0))


def parse_reply(text):
    """The card's answer: its JSON line, or the older plain word. Pure.

    Tolerant because this crosses a process boundary, and anything unrecognised is a dismissal
    rather than a fix — a garbled line must never edit the user's document.
    """
    text = (text or "").strip()
    if not text:
        return None
    if not text.startswith("{"):
        # The pre-JSON contract, still accepted: "fix" meant the sentence, "copy" the clipboard.
        return {"action": "sentence", "text": ""} if text == "fix" else (
            {"action": "copy", "text": ""} if text == "copy" else None)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("action"):
        return None
    return {"action": str(data["action"]), "text": str(data.get("text") or "")}


# ---- the card's own contracts: its input, its output, and its settings --------------------------

MAX_CHIPS = 6          # a card, not a menu: the engine's first few are the useful ones
MAX_CANDIDATES = 3     # the model's alternatives, shown as rows in the rephrase section
REPHRASE_TIMEOUT = 90  # a cold local model on this CPU has taken 6s; the server caps it anyway
TONES = ("", "professional", "casual", "formal")
INTENTS = ("", "concise", "clear", "simple")
# Only --settings needs this: a finding card is told where the engine is by the watcher's payload.
# It mirrors the client's own GRAMMAR_API default, which is where that value is really owned.
DEFAULT_API = os.environ.get("GRAMMAR_API") or "http://127.0.0.1:8875"


def parse_payload(raw, argv=None):
    """The card's content, from stdin JSON or the argv flags. Pure, so it needs no display.

    Tolerant on purpose: a card that refuses to render because one field is the wrong type is worse
    than a card with a missing line. Unknown keys are ignored, so the watcher may grow the payload
    without breaking an older card.
    """
    data = {}
    if isinstance(raw, dict):
        data = dict(raw)
    elif raw and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                data = loaded
        except ValueError:
            data = {}
    if not data:                                     # a shell invocation, or junk on stdin
        flags = argv or []
        for i, a in enumerate(flags):
            key = a.lstrip("-")
            if key in ("old", "new", "reason", "badge", "more") and i + 1 < len(flags):
                data[key] = flags[i + 1]

    def as_text(key):
        value = data.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    def as_count(key):
        """A number the card acts on (only "others" today). Junk is 0, which draws nothing."""
        try:
            return max(0, int(data.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    alts = []
    for value in data.get("alts") or []:
        if isinstance(value, str) and value.strip() and value.strip() not in alts:
            alts.append(value.strip())
    # The single --new of the old contract is just the first alternative.
    if not alts and as_text("new"):
        alts = [as_text("new")]
    if alts and not as_text("new"):
        data["new"] = alts[0]
    return {"old": as_text("old"), "new": as_text("new"), "reason": as_text("reason"),
            "badge": as_text("badge"), "more": as_text("more"), "alts": alts[:MAX_CHIPS],
            "api": as_text("api").rstrip("/"), "sentence": as_text("sentence"),
            # Every field the watcher sends has to be named here or it never reaches the card:
            # this whitelist is exactly where "others" was dropped, and the QML seam test cannot
            # see that half of the seam. "app" is the same trap, one field later — the pause button
            # reads it, and without this line the button never appears.
            "others": as_count("others"), "app": as_text("app")}


# ---- per-app pause ------------------------------------------------------------------------------
# Some applications are not worth checking: a password manager's fields are secrets, and a
# terminal's text is commands, where "misspellings" are mostly false. The list is a plain text file
# — one application name per line, '#' comments, because it is user data rather than config — and
# these defaults are always in force, so the first run already spares the obvious places.
BLOCKED_ALWAYS = ("keepassxc", "keepass", "bitwarden", "1password", "gnome-keyring", "kwallet",
                  "konsole", "yakuake", "alacritty", "kitty", "wezterm", "foot", "xterm",
                  "gnome-terminal")


def blocked_apps(text):
    """The names in a blocklist file: one per line, '#' comments and blank lines ignored."""
    names = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line.lower())
    return names


def block_list(text):
    """The list in force: the defaults, plus whatever the user has added."""
    return list(BLOCKED_ALWAYS) + blocked_apps(text)


def app_blocked(app, listed):
    """Is checking paused in this application?

    Matched case-insensitively, a listed name counting if it appears anywhere in the application's
    name — the accessibility bus says "Konsole" and "firefox", and the same application can present
    itself differently from desktop to desktop. ponytail: substring matching can over-match (an
    entry of "mail" would pause in everything with mail in its name); it is the honest first cut,
    and the alternative — never matching the application the user meant — is worse.
    """
    name = (app or "").strip().lower()
    return bool(name) and any(entry in name for entry in listed)


def add_blocked(text, app):
    """A blocklist file with this application added, once.

    Adding a name that is already there (or already covered by a default) would grow the file on
    every click, and that file is what a person reads to undo this.
    """
    name = (app or "").strip()
    if not name or app_blocked(name, block_list(text)):
        return text or ""
    return (text or "").rstrip("\n") + ("\n" if text else "") + name + "\n"


def clamp(x, y, w, h, monitors):
    """Keep the card on the monitor the caret is on; edge carets happen constantly.

    monitors is a list of (x, y, width, height) so this is testable without a display.
    """
    for mx, my, mw, mh in monitors:
        if mx <= x < mx + mw and my <= y < my + mh:
            return (min(max(x, mx), mx + mw - w), min(max(y, my), my + mh - h))
    if monitors:
        # Nothing contains it, which happens for real: a caret can report a negative or
        # beyond-the-edge position (a window partly off-screen, a stale AT-SPI rect). Park the card
        # on the *nearest* monitor and pull it inside, rather than always on the last one —
        # measured: an off-the-top-left caret put the card at 3056,936, the far corner of the other
        # monitor, which is the one place a card is least useful.
        def centre_distance(m):
            mx, my, mw, mh = m
            return (x - (mx + mw / 2)) ** 2 + (y - (my + mh / 2)) ** 2
        mx, my, mw, mh = min(monitors, key=centre_distance)
        return (min(max(x, mx), mx + mw - w), min(max(y, my), my + mh - h))
    return (x, y)


def action_json(action, text=""):
    """One line the watcher can parse. Pure, and the whole output contract."""
    out = {"action": action}
    if text:
        out["text"] = text
    return json.dumps(out, ensure_ascii=False)


def card_colors(dark):
    """The card's palette. Pure: the host decides light or dark from the desktop, and this decides
    what that means. Keeping the colours here rather than in the QML is what makes them testable."""
    if dark:
        return {"surface": "#1a1f26", "chip": "#242b34", "hover": "#2c343e",
                "border": "#2f3844", "text": "#e7eaee", "muted": "#9aa4b2", "faint": "#6f7a88",
                "accent": "#e8ebef", "accentInk": "#11151a", "accentHover": "#ffffff"}
    return {"surface": "#ffffff", "chip": "#f4f5f7", "hover": "#eef0f3",
            "border": "#e2e5ea", "text": "#14181d", "muted": "#5a6472", "faint": "#8a93a0",
            "accent": "#1c2127", "accentInk": "#ffffff", "accentHover": "#2c333b"}


# ---- the two HTTP calls the card makes (stdlib urllib, no display needed) ----------------------

def rephrase_body(sentence, tone="", intent=""):
    """The body for POST /v2/rewrite. Pure: the card's own contract with its server."""
    body = {"text": sentence, "language": "en-US"}
    if tone:
        body["tone"] = tone
    if intent:
        body["intent"] = intent
    return body


def candidates_from(response):
    """The alternatives out of a rewrite response. Pure, and tolerant: that JSON is another
    process's. An error response carries no candidates, only a message worth showing."""
    if not isinstance(response, dict) or response.get("message"):
        return []
    out = []
    for value in response.get("candidates") or []:
        if isinstance(value, str) and value.strip() and value.strip() not in out:
            out.append(value.strip())
    return out[:MAX_CANDIDATES]


def api_error_message(response, status=0):
    """Why a call failed, in the server's own words when it has them. Pure."""
    if isinstance(response, dict) and response.get("message"):
        return str(response["message"])
    if status:
        return "the rewrite backend answered HTTP %d" % status
    return "the backend did not answer"


def post_json(url, body, timeout=REPHRASE_TIMEOUT):
    """POST, and read the JSON back either way: an error body is the instruction to show."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"message": "cannot reach the engine at %s (%s)" % (url, exc)}


def get_json(url, timeout=REPHRASE_TIMEOUT):
    """GET, with the same tolerance as post_json: the body is another process's."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"message": "cannot reach the engine at %s (%s)" % (url, exc)}


# ---- the settings panel's view of GET /v1/ai ---------------------------------------------------

def key_note(key_set, key_env, key_source=""):
    """One line under the key field: where the key actually is.

    The engine reports the source, so this never claims a location it was not told about — a keyring
    is not a file, and telling someone their key sits in a 0600 file when it is in their wallet is
    the kind of small lie that costs trust in the rest of the panel. An engine too old to report a
    source gets the sentence that was true then.
    """
    if key_set and key_source == "env":
        return "the key comes from %s in the environment" % key_env
    if not key_set:
        return "a key is needed" if key_env else "not needed for this runner"
    where = {"keyring": "a key is saved in your desktop keyring",
             "file": "a key is saved in a 0600 file on the engine's machine",
             }.get(key_source, "a key is saved")
    return where + (" (%s in the environment overrides it)" % key_env if key_env else "")


def settings_view(state):
    """What the AI-runner settings panel shows, from the server's GET /v1/ai answer. Pure.

    The server already knows all of it: the presets, which one is configured, whether it answers,
    which models it has, whether a key is present, and whether this machine may change any of it.
    This turns that into exactly what the panel draws, so the panel holds no opinions of its own
    and the same JSON could feed any other surface.
    """
    if not isinstance(state, dict):
        state = {}
    presets = []
    for p in state.get("presets") or []:
        if isinstance(p, dict) and p.get("id"):
            presets.append({"id": str(p["id"]), "label": str(p.get("label") or p["id"]),
                            "url": str(p.get("url") or ""), "model": str(p.get("model") or ""),
                            "hint": str(p.get("hint") or ""), "local": bool(p.get("local")),
                            "keyEnv": str(p.get("keyEnv") or "")})
    # Off is a real choice and belongs last: it is not a preset (there is nothing to configure),
    # but it is how you stop every rephrase without touching a config file.
    presets.append({"id": "none", "label": "Off — no rewriting", "url": "", "model": "",
                    "hint": "Replacing text never calls a model.", "local": True, "keyEnv": ""})

    provider = str(state.get("provider") or "")
    models = [str(m) for m in (state.get("models") or []) if str(m).strip()]
    if state.get("model") and str(state["model"]) not in models:
        models.insert(0, str(state["model"]))     # the configured one is always pickable

    key_env = str(state.get("keyEnv") or "")
    key_set = bool(state.get("keySet"))

    warnings = []
    if state.get("writable") is False:
        warnings.append("Read-only here: the backend can only be changed on the machine the "
                        "server runs on.")
    if provider and provider != "none" and state.get("local") is False:
        warnings.append("Cloud backend: the sentence you rephrase leaves this machine.")
    if key_env and not key_set:
        # Names the fix, not just the fault: the panel below is where the key goes.
        warnings.append("No %s in the server's environment and none saved yet — rewriting will "
                        "answer 503 until you add one below." % key_env)

    if state.get("reachable"):
        status = "answering — %d model%s, ready to choose" % (len(models), "" if len(models) == 1 else "s")
        tone = "good"
    elif not provider or provider == "none":
        status, tone = "", "idle"
    elif provider == "openai":
        # The "any other OpenAI-compatible server" case: many such servers expose no /v1/models,
        # so an empty list is not the same fault as nothing answering. Saying "not answering" here
        # would call a perfectly good endpoint dead and send someone hunting.
        status = ("no model list at %s — normal for a custom endpoint. Name the model and Save; "
                  "Test checks it again." % (state.get("url") or "the address"))
        tone = "warn"
    else:
        status = "not answering at %s" % (state.get("url") or "the address")
        tone = "bad"
    return {"provider": provider, "url": str(state.get("url") or ""),
            "model": str(state.get("model") or ""), "presets": presets, "models": models,
            "hint": str(state.get("hint") or ""), "warnings": warnings,
            "reachable": bool(state.get("reachable")),
            "writable": state.get("writable") is not False, "status": status, "tone": tone,
            "keyEnv": key_env, "keySet": key_set, "needsKey": bool(key_env),
            # What the key field says under it. The value itself is never sent anywhere: the
            # server accepts one and reports only whether it has one, and now where it keeps it.
            "keyNote": key_note(key_set, key_env, str(state.get("keySource") or ""))}
