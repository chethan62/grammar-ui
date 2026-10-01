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

import difflib
import json
import os
import time
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
            # see that half of the seam. "app" was the same trap one field later, and "word" is the
            # third — the card's "Ignore this word" button is drawn from it, so without this line the
            # button never appears no matter what the watcher sends.
            "others": as_count("others"), "app": as_text("app"), "word": as_text("word")}


# ---- per-app pause ------------------------------------------------------------------------------
# Some applications are not worth checking: a password manager's fields are secrets, and a
# terminal's text is commands, where "misspellings" are mostly false. The list is a plain text file
# — one application name per line, '#' comments, because it is user data rather than config — and
# these defaults are always in force, so the first run already spares the obvious places.
# The file the per-app pause is kept in: beside the engine's own config, because it asks the same
# question ("what does this machine want?") about a different thing. One name per line, and the
# defaults below are always in force, so this starts empty and only grows by choice. Owned here rather
# than by the watcher because the tray menu reads and rewrites it too, and two copies of a path drift.
BLOCKED_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "grammar-server", "blocked-apps")

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


def read_blocked(path=None):
    """The blocklist file as text. A missing file is an empty list, not an error: nothing paused is the
    normal state, and a panel that could not draw itself over a missing file would be worse than one
    that draws nothing.

    The *user's* lines are what a settings screen may offer to undo — `block_list` also carries the
    defaults, and those are deliberate and cannot be removed from here.
    """
    try:
        with open(path or BLOCKED_PATH) as fh:
            return fh.read()
    except OSError:
        return ""


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


def remove_blocked(text, app):
    """A blocklist file with this application taken out.

    Only the user's own lines can be removed from here, and that is the point: the defaults above are
    deliberate (password managers, terminals) and never appear in this file, so the menu that offers
    this can only ever offer to end a pause the user asked for. Comments survive — a person wrote
    them — while blank lines do not: nothing in this file reads them, and leaving the gaps behind
    would make the file look like it still had something in it.
    """
    name = (app or "").strip().lower()
    if not name:
        return text or ""
    kept = [line for line in (text or "").splitlines()
            if line.strip() and line.strip().lower() != name]
    return "\n".join(kept) + ("\n" if kept else "")


# ---- pausing the whole thing for a while ---------------------------------------------------------
# The per-app pause above answers "not this application". This answers "not now" — the meeting, the
# draft, the deadline — and it is a timestamp rather than a flag on purpose: the silence ends by
# itself, so nothing has to be remembered or undone the next morning, and a machine that reboots
# comes back checking.
PAUSE_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "grammar-server", "paused-until")

# The durations worth naming. Not "off": ending a pause is not a length of time.
PAUSE_SPECS = {"15m": 900, "1h": 3600, "4h": 14400}


def pause_seconds(spec):
    """A duration like "1h" as seconds, or None when it is not one.

    Junk is refused rather than guessed at: a shortcut with a typo in it must not silence the checker
    for some default nobody asked for. Bare seconds are accepted so a script can say 90.
    """
    text = (spec or "").strip().lower()
    if text in PAUSE_SPECS:
        return PAUSE_SPECS[text]
    return int(text) if text.isdigit() else None


def write_pause(seconds, path=None, now=None):
    """Start a pause of `seconds`, or end it when that is 0 or None. Returns when it ends."""
    path = path or PAUSE_PATH
    if not seconds:
        clear_pause(path)
        return 0.0
    until = (time.time() if now is None else now) + seconds
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("%d\n" % until)
    return until


def clear_pause(path=None):
    """End any pause: the file is the pause, so removing it is ending it."""
    try:
        os.remove(path or PAUSE_PATH)
    except OSError:
        pass


def paused_until(path=None, now=None):
    """When the pause ends, or 0.0 when the checker is not paused.

    An expired timestamp counts as not paused — the silence ends by itself — and a file that cannot be
    read or parsed is not a pause either: a corrupt timestamp must never be able to silence the
    checker forever, which is the one failure mode this design could have had.
    """
    try:
        with open(path or PAUSE_PATH) as fh:
            until = float(fh.read().strip())
    except (OSError, ValueError):
        return 0.0
    return until if until > (time.time() if now is None else now) else 0.0


# The engine's own id for a spelling rule, as LanguageTool shows it, plus harper's native name in case
# a rule ever arrives unmapped. Only these can be ignored by word: a grammar rule that happens to span
# one word ("She go") is not a spelling, and hiding it by word would hide every later use of that word.
SPELLING_RULES = ("MORFOLOGIK_RULE_EN_US", "SpellCheck")


def debounce_ms(last_engine_ms):
    """How long to wait after the last keystroke before asking, given how fast the engine was.

    The wait was a fixed 1200 ms, and that timer was most of what a user felt: measured on the machine
    this was built for, /v2/check answers in 0-1 ms once the connection is warm (49 ms on the first
    call, which pays for the TCP and the harper cycle). A debounce a thousand times the thing it waits
    for is not patience, it is the whole latency. So the wait follows the last measurement.

    Bands rather than a formula: a formula needs its reasoning carried around, and a band is one
    sentence in a journal line. A slow engine is given a *longer* wait, not a shorter one — the way to
    make a slow engine feel worse is to hand it more requests.

    `None` means nothing has been measured yet (a cold start, or a check that failed), which is not
    the same as fast.
    """
    if last_engine_ms is None:
        return 600
    if last_engine_ms < 40:
        return 300
    if last_engine_ms < 250:
        return 900
    return 1500


def list_max_for(screen_height):
    """How tall a scrollable settings list may be on a screen this tall. Pure, so the arithmetic that
    keeps the footer on screen is testable without a small screen to test it on.

    The numbers are measured, not chosen: the panel's fixed content came to ~520 px with the lists at
    their full 132, so the budget left for two of them is (height - 560) / 2, floored at 60 so a tiny
    display still scrolls inside something rather than collapsing a list to a sliver.
    """
    return max(60, min(132, (screen_height - 560) // 2))


def pause_note(until, now=None):
    """When suggestions come back, in words.

    A timestamp is not an answer to "why is it quiet?": this is what a panel says instead of a number,
    and the phrase for "not paused" is deliberately the same one the tray used to print.
    """
    now = time.time() if now is None else now
    left = int(until) - int(now)
    if left <= 0:
        return "not paused"
    if left < 90:
        return "paused for another %d seconds" % left
    if left < 5400:
        return "paused for another %d minutes" % round(left / 60.0)
    return "paused for another %d hours" % round(left / 3600.0)


def finding_word(piece, offset, match):
    """The single word a finding is about, or "" when it is not one word.

    What the card needs before it offers "Ignore this word": the list behind that button is a word
    list, so anything else — a phrase, a clause, a whole sentence — would be a promise the feature
    cannot keep, and the button is hidden rather than wrong.
    """
    if not match or str((match.get("rule") or {}).get("id", "")) not in SPELLING_RULES:
        return ""
    span = piece[offset:offset + int(match.get("length") or 0)]
    if not span or any(ch.isspace() for ch in span):
        return ""
    return span.strip()


# ---- the keyboard route to a card's answer -------------------------------------------------------
# The card can never take the keyboard — that is the whole point of it, the caret has to stay in the
# text you are typing into — so Enter and Escape have to arrive from outside. A shortcut runs a tiny
# command that leaves a marker, and the card's own process, the only one that can answer for it, picks
# the marker up and exits exactly as a click would. Two shortcuts, one word each.
CARD_ACTION_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "grammar-server", "card-action")

# What the shortcut says, and the answer the card gives it. "sentence" is the primary action the card
# already offers ("Fix sentence"), and "" is how every dismissal looks: no action, nothing printed.
KEYBOARD_ACTIONS = {"accept": ("sentence", ""), "dismiss": ("", "")}


def write_card_action(verb, path=None):
    """Leave the marker a card will pick up.

    False for anything that is not one of ours: a typo in a shortcut must do nothing at all, rather
    than something surprising.
    """
    if verb not in KEYBOARD_ACTIONS:
        return False
    path = path or CARD_ACTION_PATH
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(verb + "\n")
    return True


def take_card_action(path=None):
    """The verb a card should act on — consumed as it is read.

    Read-and-remove in one place is what makes the marker safe: a card clears any stale one before it
    starts, and picking one up deletes it, so a press can never be applied twice or land on the next
    card. An unknown verb is dropped, not acted on.
    """
    path = path or CARD_ACTION_PATH
    try:
        with open(path) as fh:
            verb = fh.read().strip()
    except OSError:
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return verb if verb in KEYBOARD_ACTIONS else None


def clear_card_action(path=None):
    """Throw away whatever is sitting there: a card calls this as it starts, so only presses made
    while it is on screen can ever reach it."""
    try:
        os.remove(path or CARD_ACTION_PATH)
    except OSError:
        pass


# The space left between the caret's line and the card. Both ends need it: the watcher places the
# request at the caret's bottom edge plus this, and clamp() undoes it to find the caret's top when it
# has to hang the card above instead.
CARET_GAP = 4


def clamp(x, y, w, h, monitors, caret_h=0):
    """Keep the card on the monitor the caret is on; edge carets happen constantly.

    monitors is a list of (x, y, width, height) so this is testable without a display.

    y is the position just *below* the caret, so a card with no room before the bottom of the screen
    would be pulled up over the line being typed into — the one place it must not go, because that is
    the text you are looking at. When the caret's own height is known (caret_h, from the same
    accessibility rect) and there is room above, the card hangs above the caret instead. Without that
    number nothing is guessed: the old behaviour stands, which is why it defaults to 0.
    """
    def fit_y(y, my, mh):
        """Below if it fits, above the caret if it must, and the best clamp if it fits nowhere."""
        below = min(max(y, my), my + mh - h)
        if caret_h <= 0 or below == y:
            return below
        # y is the caret's bottom edge plus CARET_GAP, so the caret's top is y - caret_h - CARET_GAP,
        # and the card's bottom edge wants to rest one gap above that.
        above = y - caret_h - 2 * CARET_GAP - h
        return above if above >= my else below

    for mx, my, mw, mh in monitors:
        if mx <= x < mx + mw and my <= y < my + mh:
            return (min(max(x, mx), mx + mw - w), fit_y(y, my, mh))
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
        return (min(max(x, mx), mx + mw - w), fit_y(y, my, mh))
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
    """The body for POST /v2/rewrite. Pure: the card's own contract with its server.

    `stream` is always asked for: the words then arrive as the model writes them, and a backend that
    cannot stream answers in one body, which this client reads the same way. Asking costs nothing and
    not asking means a card that sits still for two seconds.
    """
    body = {"text": sentence, "language": "en-US", "stream": True}
    if tone:
        body["tone"] = tone
    if intent:
        body["intent"] = intent
    return body


def candidates_from(response):
    """The alternatives out of a rewrite response. Pure, and tolerant: that JSON is another
    process's. An error response carries no candidates, only a message worth showing.

    A `candidates` that is not a list is that process's bug, and iterating it is our crash: a number
    raises, a dict yields its *keys* as alternatives, and a string yields its letters. So the shape is
    checked before anything is read out of it — nothing here is worth dying for.
    """
    if not isinstance(response, dict) or response.get("message"):
        return []
    values = response.get("candidates")
    if not isinstance(values, (list, tuple)):
        return []
    out = []
    for value in values:
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


def change_summary(before, after, limit=4):
    """What a rephrase changed: the words that left, and the words that arrived.

    difflib rather than a word-by-word walk, because a rephrase reorders and re-inflects and only an
    aligned diff says what a person would call the change. Compared case-insensitively, so a sentence
    that merely gained a capital does not read as changed; the words reported are the original
    spellings, from whichever side they came.

    Both sides are capped at `limit` words with "…": the card is small, and this is a hint about what
    to look at, not the diff itself. Words are joined with ", " and carry their own punctuation, and a
    word the model merely *moved* is reported on both sides — it did move, and pretending otherwise
    would hide a reordered sentence. Pure, so the hint can be checked without a card.
    """
    left = (before or "").split()
    right = (after or "").split()
    matcher = difflib.SequenceMatcher(a=[word.lower() for word in left],
                                      b=[word.lower() for word in right])
    removed, added = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            removed.extend(left[i1:i2])
        if tag in ("insert", "replace"):
            added.extend(right[j1:j2])
    return _clipped(removed, limit), _clipped(added, limit)


def _clipped(words, limit):
    """A few words as one line, saying so when there were more."""
    if len(words) > limit:
        return ", ".join(words[:limit]) + " …"
    return ", ".join(words)


def ai_note(provider, model, local, ms=None):
    """Where a rephrase goes, or went — one sentence for both moments.

    It is the same question either way, so it gets the same answer: *before* the click, from the
    engine's own /v1/ai (which backend is configured, and whether the text leaves this machine) and
    *after* it, from the rewrite's own answer (which model actually replied, and how long it took).
    The wording matches the settings panel's on purpose — two surfaces disagreeing about whether your
    text leaves the machine would be worse than either one alone.

    Everything here is another process's JSON, so it is read defensively rather than trusted: a
    number where a name belongs is that process's bug, not a reason for the card to die mid-rephrase.
    `local` is the exception — it must be a real boolean, because it is the one line in the product
    that may not guess, and "true" as a string would make the card *claim* locality it was never told.
    """
    provider = str(provider or "").strip()
    if not provider:
        return ""
    model = str(model or "").strip()
    who = "%s · %s" % (provider, model) if model else provider
    if isinstance(ms, (int, float)) and ms:
        who += " · %d ms" % round(ms)
    if not isinstance(local, bool):
        return who
    where = "nothing leaves this machine" if local else "what you rephrase leaves this machine"
    return "%s — %s" % (who, where)


def read_stream(lines):
    """Turn the rewrite stream into events, without a socket in sight. Pure.

    Yields ("delta", text) while the model writes, then ("done", response) or ("error", message). Lines
    are told apart by *which key is present*, never by position or count: a backend that ignores the
    stream flag answers in one body, and that body arrives here as a single line carrying candidates —
    so asking to stream is never a way to break a call, and this is the reason it cannot be.

    A line that is not an object with one of those keys is an error rather than something to skip: it
    means the framing broke, and a half-read sentence presented as an answer is worse than a failure.
    """
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            yield ("error", "the rewrite stream was unreadable: %s" % raw[:120])
            return
        if not isinstance(event, dict):
            yield ("error", "the rewrite stream sent %r, which is not an object" % raw[:120])
            return
        if event.get("message"):
            yield ("error", str(event["message"]))
            return
        if event.get("delta"):
            yield ("delta", str(event["delta"]))
            continue
        if "candidates" in event:
            yield ("done", event)
            return
    yield ("error", "the rewrite stream ended without an answer")


def stream_rewrite(url, body, on_delta=None, timeout=REPHRASE_TIMEOUT, should_stop=None):
    """POST /v2/rewrite asking for the answer as it is written, handing the words over as they come.

    Returns (status, final) exactly like post_json, so a caller's handling of the answer does not
    depend on how it arrived. `should_stop` is asked between lines: when it says yes the connection is
    dropped, which is also how the model is stopped — the request's context reaches the backend, so
    there is no second cancel path to keep in step.
    """
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for kind, value in read_stream(response):
                if should_stop is not None and should_stop():
                    return 200, {"message": "cancelled"}
                if kind == "delta":
                    if on_delta is not None:
                        on_delta(value)
                    continue
                if kind == "error":
                    return response.status, {"message": value}
                return response.status, value
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"message": "cannot reach the engine at %s (%s)" % (url, exc)}
    return 0, {"message": "the rewrite stream ended unexpectedly"}


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
    # ---- the rest of this product's settings, so one window can hold them all ----
    # They come from three places — the engine owns the ignored words, this client owns the blocklist
    # and the pause — and every one of them already has a reader somewhere else: the watcher reads the
    # blocklist per check, the engine filters by its own list. Nothing here decides anything; it
    # arranges what those readers already know, which is why the panel needs no state of its own.
    #
    # Neither list is capped: the panel scrolls them now. The cap this replaced hid entries in a file
    # the panel was named as the place to look, which is a worse lie than a scrollbar.
    words = sorted({str(w) for w in (state.get("words") or []) if str(w).strip()}, key=str.lower)
    apps = sorted({str(a) for a in (state.get("pausedApps") or []) if str(a).strip()}, key=str.lower)

    return {"provider": provider, "url": str(state.get("url") or ""),
            "model": str(state.get("model") or ""), "presets": presets, "models": models,
            "hint": str(state.get("hint") or ""), "warnings": warnings,
            "reachable": bool(state.get("reachable")),
            "writable": state.get("writable") is not False, "status": status, "tone": tone,
            "keyEnv": key_env, "keySet": key_set, "needsKey": bool(key_env),
            # What the key field says under it. The value itself is never sent anywhere: the
            # server accepts one and reports only whether it has one, and now where it keeps it.
            "keyNote": key_note(key_set, key_env, str(state.get("keySource") or "")),
            # ---- the rest of this product's settings, so one window can hold them all ----
            "words": words,
            "pausedApps": apps,
            # How tall the two scrolling lists may be. The host measures it against the screen, so a
            # short display shrinks the lists instead of pushing the panel's footer off the bottom.
            "listMax": max(60, int(state.get("listMax") or 132)),
            "pauseNote": pause_note(state.get("pauseUntil") or 0)}
