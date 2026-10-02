#!/usr/bin/env python3
"""What the window copies from grammar_core must still match it.

The app is CSS and JS in a webview and cannot import Python, so it copies two things: the palette and the
debounce bands. A copy nothing checks is a copy that drifts, which is what this file is for.

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

# The debounce bands in src/main.js are a port of grammar_core.debounce_ms: same four answers, or the
# window waits a different length of time than the product it is part of does.
py_bands = {i: core.debounce_ms(i) for i in (None, 0, 39, 40, 249, 250, 10000)}
mjs = (HERE / "src/main.js").read_text()
js_start = mjs.index("function debounceMs(")
js_body = mjs[js_start:mjs.index("\n}\n", js_start)]
for probe, want in py_bands.items():
    if ("return %d;" % want) not in js_body:
        drift.append("debounce_ms(%s)=%s is missing from src/main.js" % (probe, want))

# A stylesheet stops being extensible the moment a colour is written where a token belongs: the next theme
# is then a hunt through every rule instead of one block. Every literal lives in a :root block, and this is
# what keeps the tokens a system rather than a habit.
outside_roots = re.sub(r":root\s*\{[^}]*\}", "", css, flags=re.S)
for stray in sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", outside_roots))):
    drift.append("%s is written outside a :root block — use a var(--token)" % stray)

# The label's sizing is keyed on a class, not on its tag. It was a <span> and became a <button>, and the
# selector written for `span` then stopped applying — which clipped every long finding message to one line at
# the window edge. This is the check that keeps the two ends agreeing.
if '"flat jump"' in mjs and ".finding .jump" not in css:
    drift.append("the finding label is a button but no `.finding .jump` rule sizes it — long messages will "
                 "be clipped instead of wrapping")

if drift:
    print("  app: PALETTE DRIFTED from grammar_core.card_colors(): " + ", ".join(drift))
    sys.exit(1)
print("  app: the window's palette is grammar_core.card_colors(), both themes")
