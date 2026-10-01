#!/usr/bin/python3
"""The headless half of the desktop clients: the finding logic and the card's wire contract.

Nothing here imports gi, Qt or subprocess. That is the whole point of the module: the parts of
these clients that are just rules — which finding is being shown, what the card is allowed to
show, where it fits on a screen, how a rewrite request is shaped, what the settings panel draws —
can be read, reasoned about and tested with any Python, on a machine with no display, no
accessibility bus and no toolkit. Before this existed, every pure assertion in the gates had to
launch the *system* python through a re-exec dance, because importing the client module meant
importing gi (grammar-watch.py).

Leaves-first: this module imports only the standard library, and the clients import it. There is
no cycle and there is nothing here that talks to a bus, a screen or a process.

The two clients keep their own halves: grammar-watch.py owns the accessibility bus, the daemon and
the toast. Both re-export what they
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


# ---- the two HTTP calls the card makes (stdlib urllib, no display needed) ----------------------


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


# ---- the settings panel's view of GET /v1/ai ---------------------------------------------------


