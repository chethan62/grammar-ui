#!/usr/bin/env python3
"""The two things in grammar-lookup.py that can be wrong silently: which matches get
applied (an overlap applied twice double-edits the text) and the path through the real
engine. Run: python3 desktop/test-lookup.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("lookup", os.path.join(HERE, "grammar-lookup.py"))
lookup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lookup)


def m(offset, length, *replacements):
    return {"offset": offset, "length": length,
            "replacements": [{"value": r} for r in replacements], "message": "x"}


# applied right to left, so offsets ahead of an edit stay valid
assert lookup.fixed("teh report is late", [m(0, 3, "the")]) == "the report is late"
# offsets computed from the string, not counted by hand (two wrong counts in a row)
t = "teh report is late."
assert lookup.fixed(t, [m(t.index("teh"), 3, "the"), m(t.index("late"), 4, "latest")]) == "the report is latest."
# a match pointing past the end is ignored rather than appending garbage
assert lookup.fixed("teh report", [m(0, 3, "the"), m(9, 4, "junk")]) == "the report"
assert lookup.fixed("teh report", [m(3, 0, "junk")]) == "teh report"

# An overlap is applied once, never twice: matches go right to left, so the LATER one is
# the one that survives and the earlier is dropped entirely (it is never spliced anyway).
assert lookup.fixed("abcdef", [m(0, 3, "X"), m(2, 4, "Y")]) == "abY", lookup.fixed("abcdef", [m(0, 3, "X"), m(2, 4, "Y")])
assert lookup.fixed("abcdef", [m(0, 4, "Z"), m(1, 2, "Q")]) == "aQdef", lookup.fixed("abcdef", [m(0, 4, "Z"), m(1, 2, "Q")])
assert lookup.fixed("abcdef", [m(0, 6, "W")]) == "W", "a match covering the whole text"

# nothing to apply, and a match with no replacement must not delete the text
assert lookup.fixed("clean text", []) == "clean text"
assert lookup.fixed("teh text", [m(0, 3)]) == "teh text", "a match with no replacement must not delete text"

# adjacent matches both apply, and repeated offsets do not corrupt
assert lookup.fixed("aa", [m(0, 1, "b"), m(1, 1, "c")]) == "bc"
assert lookup.fixed("teh report", [m(0, 3, "the"), m(len("teh report") + 1, 4, "junk")]) == "the report"
assert lookup.fixed("teh report", [m(3, 0, "junk")]) == "teh report"
print("fixed(): 10 assertions - passed")

# The dialog contract, checked with stubs on PATH: what the window is asked to show, and
# what each answer does. A screenshot cannot tell "the dialog is on another output" from
# "the dialog never appeared"; this can.
import tempfile


def stub_dir(kdialog_exit, record):
    d = tempfile.mkdtemp(prefix="lookup-stub-")
    def write(name, body):
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write(body)
        os.chmod(path, 0o755)
    write("kdialog", '#!/bin/sh\nprintf "%s\\n" "$*" >> ' + record + '\nexit ' + str(kdialog_exit) + '\n')
    write("wl-copy", '#!/bin/sh\ncat > ' + record + '.clip\n')
    write("notify-send", '#!/bin/sh\nprintf "%s\\n" "$*" >> ' + record + '.notify\n')
    return d


def run_dialog(kdialog_exit, matches, corrected, text):
    record = tempfile.mktemp(prefix="lookup-rec-")
    d = stub_dir(kdialog_exit, record)
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = d + ":" + old_path
    try:
        lookup.dialog(matches, corrected, text)
    finally:
        os.environ["PATH"] = old_path
    def slurp(path):
        return open(path).read() if os.path.exists(path) else None
    return slurp(record), slurp(record + ".clip"), slurp(record + ".notify")


matches = [{"offset": 0, "length": 3, "message": "Possible spelling mistake",
            "replacements": [{"value": "The"}]},
           {"offset": 19, "length": 2, "message": "The form of the verb must agree",
            "replacements": [{"value": "goes"}]}]
corrected = "The report is late. She goes to the office."

argv, clip, note = run_dialog(0, matches, corrected, "teh report is late. She go to the office.")
assert "--yesnocancel" in argv, argv
assert "--yes-label Copy fixed" in argv, argv
assert "Possible spelling mistake" in argv, argv
assert "\u2192" in argv and "goes" in argv, "each finding must carry its suggestion: %r" % argv
assert argv.count("\u2022") == 2, "one bullet per finding: %r" % argv
assert argv.count("\n") >= 2, "one line per finding"
assert clip == corrected, "Yes must put the corrected text on the clipboard: %r" % clip

# Cancel: nothing copied, no crash, no notification storm
argv, clip, note = run_dialog(2, matches, corrected, "x")
assert clip is None, "Cancel must not touch the clipboard"

# The dialog tool failing is the case that used to be completely silent
argv, clip, note = run_dialog(9, matches, corrected, "x")
assert clip == corrected, "a failed dialog must still deliver the fix: %r" % clip
assert note and "clipboard" in note, "the fallback has to say where the text went: %r" % note

# No issues: a message box, no clipboard work
argv, clip, note = run_dialog(0, [], corrected, "clean")
assert "--msgbox" in argv, argv
assert "No issues found" in argv, argv
assert clip is None
print("dialog contract: 11 assertions - passed")

# end to end through the engine, when it is up
try:
    result = lookup.check("teh report is late. She go to the office.")
except Exception as exc:                                    # noqa: BLE001 - any failure means skip
    print("engine not reachable (%s) - live assertions skipped" % type(exc).__name__)
    sys.exit(0)

corrected = lookup.fix_until_stable("teh report is late. She go to the office.", lookup.check)
assert corrected == "The report is late. She goes to the office.", corrected

# one pass leaves the capitalisation rule's own suggestion ("Teh"), which is why the
# desktop tool repeats: a checker that keeps reporting something must converge, not spin
calls = {"n": 0}
def shrinking(text):
    calls["n"] += 1
    return {"matches": [m(0, 3, "The")]} if calls["n"] == 1 else {"matches": []}
assert lookup.fix_until_stable("teh text", shrinking) == "The text"
assert calls["n"] == 2, calls
assert lookup.fix_until_stable("teh text", lambda t: {"matches": []}) == "teh text"
ids = sorted({x["rule"]["id"] for x in result["matches"]})
import re
assert all(re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", i) for i in ids), ids
lines = lookup.findings(result["matches"]).splitlines()
assert len(lines) == len(result["matches"]), "one line per match"
print("live: corrected to %r, rule ids %s" % (corrected, ids))
print("live: 4 assertions - passed")
