#!/usr/bin/env python3
"""The window's palette must be grammar_core.card_colors().

The app cannot import the Python module — it is CSS in a webview — so the tokens are copied, and a copy
that nothing checks is a copy that drifts. This is that check, and it is a script rather than a recipe in
the Makefile for a reason learned the hard way: a heredoc inside a recipe is a hang, because make gives
each line its own shell and `python3 - <<EOF` then waits on a stdin that never comes.
"""

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop"))
import grammar_core as core  # noqa: E402

css = (HERE / "src/styles.css").read_text()
light_part, _, dark_part = css.partition("@media (prefers-color-scheme: dark)")
found = [dict(re.findall(r"--(\w+):\s*(#[0-9a-fA-F]{6})", part)) for part in (light_part, dark_part)]

drift = []
for shade, tokens in zip((False, True), found):
    for name, value in core.card_colors(shade).items():
        if tokens.get(name) != value:
            drift.append("%s %s=%s should be %s" % ("dark" if shade else "light", name, tokens.get(name), value))

def luminance(colour):
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(one, two):
    a, b = luminance(one), luminance(two)
    return round((max(a, b) + 0.05) / (min(a, b) + 0.05), 2)


# Every pair that carries text, in both themes: the rule is 4.5:1, and this is what stops a palette
# tweak — the `faint` one that had to be darkened, say — from being good in one theme and missed in the
# other.
for shade in (False, True):
    tokens = core.card_colors(shade)
    for fg, bg, what in (("text", "surface", "body text"), ("muted", "surface", "notes"),
                         ("faint", "surface", "11px notes"), ("accentInk", "accent", "the primary label")):
        got = ratio(tokens[fg], tokens[bg])
        if got < 4.5:
            drift.append("%s %s on %s is %.2f, under 4.5" % ("dark" if shade else "light", fg, bg, got))

if drift:
    print("  app: PALETTE DRIFTED from grammar_core.card_colors(): " + ", ".join(drift))
    sys.exit(1)
print("  app: the window's palette is grammar_core.card_colors(), both themes")
