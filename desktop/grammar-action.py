#!/usr/bin/python3
"""Accept or dismiss the suggestion card that is on screen — for a keyboard shortcut to run.

    grammar-action accept     apply the fix the card is offering (the same as "Fix sentence")
    grammar-action dismiss    put the card away (the same as Ignore, or Escape)

Why a command and not a key the card listens for: the card never takes the keyboard, because the
caret has to stay in the text you are typing into — so the key press comes from the desktop's own
shortcut system, and this is what a shortcut runs. Nothing is printed and nothing has to be running
for it to be safe: the marker it leaves is cleared by the next card, and consumed by the one that is
on screen.

Binding it (KDE): System Settings → Shortcuts → the two entries this ships
("Accept the suggestion" / "Dismiss the suggestion"), then give each a key. Ctrl+Alt+Return and
Ctrl+Alt+Escape are what the card's own documentation suggests, and neither is claimed by anything
here by default.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grammar_core import KEYBOARD_ACTIONS, write_card_action     # noqa: E402


def main(argv):
    verb = (argv[0] if argv else "").strip().lower()
    if not write_card_action(verb):
        # A shortcut with a typo in it says so in the journal rather than doing something arbitrary.
        print("usage: grammar-action {%s}" % "|".join(sorted(KEYBOARD_ACTIONS)), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
