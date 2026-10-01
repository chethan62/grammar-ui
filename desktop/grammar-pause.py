#!/usr/bin/python3
"""Silence the checker for a while, or bring it back.

    grammar-pause 15m      quiet for a quarter of an hour
    grammar-pause 1h       quiet for an hour — what the card's own button does
    grammar-pause off      checking again now
    grammar-pause          print the state, and exit 1 when paused

For a meeting, a deadline or a draft, and worth binding to a shortcut: a checker you cannot silence
is one you turn off for good. The pause is a timestamp in the runtime directory rather than a flag, so
it ends by itself — nothing to remember, and a machine that reboots comes back checking.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grammar_core import PAUSE_SPECS, clear_pause, pause_seconds, paused_until, write_pause  # noqa: E402


def main(argv):
    spec = (argv[0] if argv else "").strip().lower()
    if not spec:
        until = paused_until()
        if not until:
            print("checking")
            return 0
        print("paused until %s" % time.strftime("%H:%M", time.localtime(until)))
        return 1                      # a state a script can test for, the way grep reports one
    if spec in ("off", "end", "resume"):
        clear_pause()
        return 0
    seconds = pause_seconds(spec)
    if seconds is None:
        print("usage: grammar-pause {%s|off}    (%r is not a length of time)"
              % ("|".join(sorted(PAUSE_SPECS)), spec), file=sys.stderr)
        return 2
    write_pause(seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
