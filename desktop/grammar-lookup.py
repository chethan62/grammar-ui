#!/usr/bin/env python3
"""Check the text you have selected in ANY application, without a browser.

Bound to a global shortcut, this reads the primary selection — whatever is highlighted
right now, in an editor, a chat window, a terminal, a PDF, a text field — sends it to the
local engine and shows the findings in a native dialog:

  Yes     the corrected text is on your clipboard, one Ctrl+V from replacing the selection
  No      the corrected text is shown (kdialog --textbox), nothing is copied
  Cancel  nothing happens

The corrected text is every match's first replacement, applied right to left and
skipping any that overlaps one already applied.

Why the clipboard instead of typing the fix in: KWin does not implement the Wayland
virtual-keyboard protocol, so wtype cannot synthesise a paste. If a working synthesiser is
found (wtype, or ydotool with its daemon running) the paste is done for you and only a
notification is shown. That is a runtime check, not an assumption.

  GRAMMAR_NO_UI=1        print the corrected text instead of showing a dialog (scripts, tests)
  GRAMMAR_API=<url>      engine to use (default http://127.0.0.1:8875)
  GRAMMAR_LANG=<code>    language to check as (default en-US)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

API = os.environ.get("GRAMMAR_API", "http://127.0.0.1:8875").rstrip("/")
LANG = os.environ.get("GRAMMAR_LANG", "en-US")


def selection():
    """The highlighted text: primary selection first, then the clipboard."""
    for cmd in (["wl-paste", "-p", "--no-newline"], ["xclip", "-o", "-selection", "primary"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=5).stdout.decode("utf-8", "replace")
        except (subprocess.SubprocessError, OSError):
            continue
        if out.strip():
            return out
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=5).stdout.decode("utf-8", "replace")
        except (subprocess.SubprocessError, OSError):
            continue
        if out.strip():
            return out
    return ""


def check(text):
    body = json.dumps({"text": text, "language": LANG, "level": "picky"}).encode()
    req = urllib.request.Request(API + "/v2/check", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fixed(text, matches):
    """Apply the first replacement of each match, right to left, skipping overlaps."""
    applied = []
    for m in sorted(matches, key=lambda m: m["offset"], reverse=True):
        if not m["replacements"]:
            continue
        start, end = m["offset"], m["offset"] + m["length"]
        # The engine's offsets are over the text just sent, so this should never fire — but an
        # out-of-range splice in the deleted browser UI once ate a sentence, and the guard is two
        # lines against corrupting someone's writing.
        if start < 0 or end > len(text) or start >= end:
            continue
        if any(start < a_end and end > a_start for a_start, a_end in applied):
            continue
        text = text[:start] + m["replacements"][0]["value"] + text[end:]
        applied.append((start, end))
    return text


def fix_until_stable(text, checker, rounds=4):
    """Apply, re-check, apply again — one pass is not always the fix.

    The engine's sentence-capitalisation rule suggests "Teh" for a typo at the start of a
    sentence (it is fixing the capital, not the spelling), and only the next pass sees the
    remaining typo and turns it into "The". That is why this repeats until a pass changes
    nothing, rather than stopping after one.
    """
    for _ in range(rounds):
        matches = checker(text).get("matches", [])
        if not matches:
            break
        nxt = fixed(text, matches)
        if nxt == text:
            break
        text = nxt
    return text


def findings(matches):
    lines = []
    for m in sorted(matches, key=lambda m: m["offset"]):
        suggestion = " / ".join(r["value"] for r in m["replacements"][:3])
        what = m["message"].strip().replace("\n", " ")
        lines.append("• " + what + (("  →  " + suggestion) if suggestion else ""))
    return "\n".join(lines)


def copy(text):
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), timeout=5, check=True)
                return True
            except (subprocess.SubprocessError, OSError):
                continue
    return False


def notify(summary, body=""):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "grammar", summary, body], timeout=5)


def can_synthesise_paste():
    """True only when a working key synthesiser is actually present."""
    if shutil.which("wtype"):
        probe = subprocess.run(["wtype", ""], capture_output=True)
        if probe.returncode == 0:
            return True
    if shutil.which("ydotool") and subprocess.run(["pgrep", "-x", "ydotoold"],
                                                  capture_output=True).returncode == 0:
        return True
    return False


def paste_back():
    if shutil.which("wtype"):
        return subprocess.run(["wtype", "-M", "ctrl", "v"], capture_output=True).returncode == 0
    if shutil.which("ydotool"):
        return subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
                              capture_output=True).returncode == 0
    return False


def dialog(matches, corrected, text):
    """kdialog is on KDE (zenity elsewhere). Yes copies, No shows the text."""
    tool = "kdialog" if shutil.which("kdialog") else ("zenity" if shutil.which("zenity") else "")
    if not tool:
        # No dialog tool, so the clipboard is the only way to hand this over — and that has to be
        # checked. "The corrected text is on your clipboard" when neither wl-copy nor xclip exists
        # is a claim that leaves the user with nothing, which is worse than a refusal. Same
        # fallback the Yes branch uses below.
        if copy(corrected):
            notify("%d issue(s) fixed" % len(matches), "The corrected text is on your clipboard.")
        else:
            print(corrected)
            notify("Could not write to the clipboard", corrected[:200])
        return
    if not matches:
        subprocess.run([tool, "--msgbox", "No issues found.", "--title", "grammar"], timeout=120)
        return
    question = "%d issue%s found\n\n%s\n\nCopy the corrected text? (No = show it)" % (
        len(matches), "" if len(matches) == 1 else "s", findings(matches))
    if tool == "kdialog":
        code = subprocess.run([tool, "--yesnocancel", question, "--title", "grammar",
                               "--yes-label", "Copy fixed", "--no-label", "Show fixed",
                               "--cancel-label", "Close"], timeout=300).returncode
    else:
        code = subprocess.run([tool, "--question", "--text", question, "--title", "grammar",
                               "--ok-label", "Copy fixed", "--cancel-label", "Close"],
                              timeout=300).returncode
    if code not in (0, 1):  # cancelled, or the dialog tool failed — never leave nothing behind
        if code not in (2, 1) and copy(corrected):
            notify("%d issue(s) fixed" % len(matches), "The dialog could not be shown; the fixed "
                   "text is on your clipboard — Ctrl+V to replace the selection.")
        return
    if code == 0:
        if copy(corrected):
            if can_synthesise_paste() and paste_back():
                notify("Fixed %d issue(s)" % len(matches), "Pasted over your selection.")
            else:
                notify("Fixed %d issue(s)" % len(matches), "Ctrl+V to replace the selection.")
        else:
            notify("Could not write to the clipboard", corrected[:200])
    elif code == 1:  # "No" — show the corrected text
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(corrected)
            path = fh.name
        if tool == "kdialog":
            subprocess.run([tool, "--textbox", path, "--title", "grammar · corrected"], timeout=300)
        else:
            subprocess.run([tool, "--text-info", "--filename", path, "--title", "grammar · corrected"],
                           timeout=300)
        os.unlink(path)


def main():
    text = selection()
    if not text.strip():
        notify("Nothing selected", "Select the text to check, then press the shortcut again.")
        return 1
    try:
        result = check(text)
    except urllib.error.URLError as exc:
        notify("Engine unreachable", "%s (%s)" % (API, exc))
        return 1
    matches = result.get("matches", [])
    corrected = fix_until_stable(text, check) if matches else text
    if os.environ.get("GRAMMAR_NO_UI") == "1":
        sys.stdout.write(corrected)
        if not corrected.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    dialog(matches, corrected, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
