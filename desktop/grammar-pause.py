#!/usr/bin/python3
"""Silence the checker for a while, or bring it back.

    grammar-pause 15m        quiet for a quarter of an hour
    grammar-pause 1h         quiet for an hour — what the card's own button does
    grammar-pause off        checking again now
    grammar-pause            print the state, and exit 1 when paused
    grammar-pause --blocks   which applications are ignored, and exit 0
    grammar-pause --unblock Firefox
                             check in one application again — the way back from the card's
                             "Ignore in <app>", exit 1 when there was nothing of yours to undo

For a meeting, a deadline or a draft, and worth binding to a shortcut: a checker you cannot silence is
one you turn off for good. The pause is a timestamp in the runtime directory rather than a flag, so it
ends by itself — nothing to remember, and a machine that reboots comes back checking.

The two ignore verbs are here, next to the pause, because the card's buttons that *set* those files
are the same kind of thing: one click, and then no way back except finding the file. This is the way
back, and it is a command rather than an icon because a menu needs a panel to live in and this needs
nothing at all.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grammar_core import (BLOCKED_ALWAYS, BLOCKED_PATH, PAUSE_SPECS, app_blocked, blocked_apps,
                          clear_pause, pause_seconds, paused_until, remove_blocked,
                          write_pause)  # noqa: E402


def report_blocks():
    """The applications this machine ignores, as the file has them, in its own order."""
    try:
        with open(BLOCKED_PATH) as fh:
            names = blocked_apps(fh.read())
    except OSError:
        names = []
    if names:
        print("ignored in: %s" % ", ".join(names))
    else:
        print("no application is ignored")
    return 0


def unblock(name):
    """Bring checking back in one application.

    0 when a line was removed, 1 when there was nothing of the user's to remove — either a name that
    was never there or one of the defaults, which are deliberate and not removable. The two are told
    apart in the message, because "nothing to undo" on its own reads like a bug.
    """
    name = (name or "").strip()
    if not name:
        print("usage: grammar-pause --unblock <application>", file=sys.stderr)
        return 2
    try:
        with open(BLOCKED_PATH) as fh:
            text = fh.read()
    except OSError:
        text = ""
    if not app_blocked(name, blocked_apps(text)):
        if app_blocked(name, BLOCKED_ALWAYS):
            print("%s is always spared (password managers and terminals are) — that is a default, "
                  "not an ignore of yours" % name, file=sys.stderr)
        else:
            print("%s is not ignored in any application you added" % name, file=sys.stderr)
        return 1
    left = remove_blocked(text, name)
    if not left.strip():
        # An empty list is better as no file: the pause itself is a file that *is* the state, so a
        # stray empty one reads as something that was set, and the directory is a place people look.
        try:
            os.remove(BLOCKED_PATH)
        except OSError as exc:
            print("could not write %s: %s" % (BLOCKED_PATH, exc), file=sys.stderr)
            return 2
    else:
        try:
            os.makedirs(os.path.dirname(BLOCKED_PATH), exist_ok=True)
            with open(BLOCKED_PATH, "w") as fh:
                fh.write(left)
        except OSError as exc:
            print("could not write %s: %s" % (BLOCKED_PATH, exc), file=sys.stderr)
            return 2
    print("checking is on in %s again" % name)
    return 0


def main(argv):
    spec = (argv[0] if argv else "").strip().lower()
    if spec in ("--blocks", "blocks"):
        return report_blocks()
    if spec in ("--unblock", "unblock"):
        return unblock(argv[1] if len(argv) > 1 else "")
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
        print("usage: grammar-pause {%s|off|--blocks|--unblock <application>}    (%r is not a length "
              "of time)" % ("|".join(sorted(PAUSE_SPECS)), spec), file=sys.stderr)
        return 2
    write_pause(seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
