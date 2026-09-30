#!/usr/bin/python3
"""grammar-doctor — is the whole chain actually working?

This product fails *silently*. If the watcher is not on the accessibility bus, or the card cannot
load, nothing appears and nothing says why: the person simply has an application that never
suggests anything, and no error to search for. Every real defect in this project so far was found
by someone going and looking. This is the command that does the looking.

    grammar-doctor            check everything, exit non-zero when the chain is broken
    grammar-doctor --quiet    only what failed, plus the verdict

The chain, in order, each line carrying the fix rather than just the symptom:

    install      the binaries that must exist
    engine       grammar-server answering, and what version
    engine lints the engine really flags a broken sentence — the end-to-end check
    rewrite      the rephrase backend (optional by design: checking never needs a model)
    watcher      the typing watcher unit
    bus          the accessibility bus, and what it can actually see
    card         Qt and the card file, which is what the pop-up needs

Exit 0 when the essential chain works, 1 otherwise. Optional pieces (the model, the menu entries)
are reported and never decide the verdict: rewriting being off is a supported state, not a fault.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

# The gate pattern this repo uses everywhere: `python3` is not necessarily the interpreter with
# gi/Atspi, and a doctor that cannot look at the bus must not report the bus as broken.
try:
    import gi  # noqa: F401
except ImportError:
    if not os.environ.get("GRAMMAR_DOCTOR_REEXEC") and os.path.exists("/usr/bin/python3"):
        os.environ["GRAMMAR_DOCTOR_REEXEC"] = "1"
        os.execv("/usr/bin/python3", ["/usr/bin/python3", os.path.abspath(__file__)] + sys.argv[1:])

HERE = os.path.dirname(os.path.abspath(__file__))
BINDIR = os.path.expanduser("~/.local/bin")
APPDIR = os.path.expanduser("~/.local/share/applications")
TIMEOUT = 6

# The clients' headless half, for the one thing this script needs from it: where the engine is by
# default. One source for that value rather than a second literal. The path insert is needed
# because this file gets run by path as often as it is run installed.
sys.path.insert(0, HERE)
from grammar_core import DEFAULT_API  # noqa: E402

# The one sentence this project has measured the engine against all along. Proved to be flagged
# (HE_VERB_AGR + UPPERCASE_SENTENCE_START) while "This sentence have an error." is not — the
# engine's agreement coverage has a hole, so the sample has to be one that is known to work.
SAMPLE = "She go to the office."


def http_json(url, body=None, timeout=TIMEOUT):
    """GET, or POST when given a body. Returns (status, parsed) and never raises."""
    try:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, data=data, method="POST" if body is not None else "GET",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode(errors="replace") or "{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def api_base():
    """Where the engine is, from the client that owns that default — never a second copy of it."""
    for path in (os.path.join(HERE, "grammar-lookup.py"), os.path.join(BINDIR, "grammar-lookup"),
                 os.path.expanduser("~/.local/share/grammar-ui/grammar-lookup.py")):
        if os.path.exists(path):
            import importlib.util
            spec = importlib.util.spec_from_file_location("grammar_lookup", path)
            if spec is None or spec.loader is None:
                break
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:  # noqa: BLE001 - a client that cannot load is reported, not fatal
                break
            base = getattr(module, "API", "")
            if base:
                return base.rstrip("/")
    return DEFAULT_API.rstrip("/")


def verdict(rows):
    """The whole decision, pure: a check that must pass and did not makes the verdict broken.

    rows is a list of (essential, ok). Pure so the rule can be tested without a machine to test on.
    """
    return all(ok for essential, ok in rows if essential)


# ---- the checks -------------------------------------------------------------------------------
# Each returns (essential, ok, detail, fix). `fix` is empty when nothing needs doing.

def check_install(bindir=None):
    """The installed pieces, including the module the clients import and the card's own QML.

    grammar_core.py is not decoration: the watcher and the card both import it, and the card loads
    grammar-card.qml, so an install that predates either change has binaries that look fine and die
    on startup. Checking the files an install must have is the cheapest way to catch that — and it
    is exactly the failure this command exists for.
    """
    bindir = bindir or BINDIR
    wanted = ("grammar-lookup", "grammar-watch", "grammar-popup.py", "grammar-doctor",
              "grammar_core.py", "grammar-card.qml")
    missing = [name for name in wanted if not os.path.exists(os.path.join(bindir, name))]
    if missing:
        return (True, False, "missing from %s: %s" % (bindir, ", ".join(missing)),
                "run `make install` — the clients import grammar_core.py and the card loads "
                "grammar-card.qml, so both must sit beside them")
    return (True, True, "%d installed files, including grammar_core.py" % len(wanted), "")


def check_engine(base):
    status, body = http_json(base + "/status")
    if status != 200:
        return (True, False, "no answer from %s" % base,
                "start it: `systemctl --user start grammar-server`, or run grammar-server by hand")
    return (True, True, "grammar-server %s (%s), status %s"
            % (body.get("version", "?"), body.get("dialect", "?"), body.get("status", "?")), "")


def check_lints(base):
    """The end-to-end one: a known-broken sentence must come back with findings."""
    status, body = http_json(base + "/v2/check", {"text": SAMPLE, "language": "en-US"})
    if status == 0:
        # Not "up but not checking": nothing answered at all. Sending someone to the journal for a
        # server that is not running is a wrong fix, which is worse than no fix.
        return (True, False, "nothing answered at %s/v2/check" % base,
                "start it: `systemctl --user start grammar-server`")
    if status != 200:
        return (True, False, "POST /v2/check answered HTTP %d" % status,
                "the engine is up but not checking: look at `journalctl --user -u grammar-server -n 30`")
    matches = body.get("matches")
    if not matches:
        return (True, False, "the engine found nothing wrong with %r" % SAMPLE,
                "the engine has lost its rules: check the harper pair in ~/.cache/grammar-server/")
    rules = ", ".join(sorted({str(m.get("rule", {}).get("id", "?")) for m in matches}))
    return (True, True, "%d finding(s) for %r: %s" % (len(matches), SAMPLE, rules), "")


def check_rewrite(base):
    """Optional: rewriting is an enhancement, and every other endpoint works while it is off."""
    status, body = http_json(base + "/v1/ai")
    if status != 200:
        return (False, False, "no answer from /v1/ai", "")
    provider = body.get("provider") or "none"
    if provider == "none":
        return (False, True, "off — every check still works, Rephrase is simply not offered", "")
    if not body.get("reachable"):
        return (False, False, "%s configured at %s, not answering"
                % (provider, body.get("url", "?")),
                "start it, or pick another runner: grammar-popup.py --settings")
    models = body.get("models") or []
    return (False, True, "%s · %s · answering (%d model%s)"
            % (provider, body.get("model", "?"), len(models), "" if len(models) == 1 else "s"), "")


def check_watcher():
    if not shutil.which("systemctl"):
        return (True, False, "no systemctl to ask", "run grammar-watch by hand")
    out = subprocess.run(["systemctl", "--user", "is-active", "grammar-watch"],
                         capture_output=True, text=True)
    active = out.stdout.strip()
    if active != "active":
        return (True, False, "the grammar-watch unit is %s" % (active or "unknown"),
                "systemctl --user enable --now grammar-watch")
    enabled = subprocess.run(["systemctl", "--user", "is-enabled", "grammar-watch"],
                             capture_output=True, text=True).stdout.strip()
    return (True, True, "grammar-watch active%s" % ("" if enabled == "enabled" else " (but not enabled at login)"), "")


def check_bus():
    """The failure that produces *nothing at all*: no bus, no suggestions, no error."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception as exc:  # noqa: BLE001
        return (True, False, "no Atspi bindings here (%s)" % exc,
                "install python-atspi / at-spi2-core, and run this with /usr/bin/python3")
    try:
        Atspi.init()
        desktop = Atspi.get_desktop(0)
        apps = desktop.get_child_count()
    except Exception as exc:  # noqa: BLE001
        return (True, False, "the accessibility bus did not answer (%s)" % exc,
                "start it: `systemctl --user status at-spi-dbus-bus`, or enable "
                "accessibility in System Settings — without it nothing can be watched")
    if apps == 0:
        return (True, False, "the bus answers but reports no applications",
                "the bus is running but nothing is publishing: restart the session, or start "
                "at-spi-bus-launcher by hand")
    return (True, True, "bus reachable, %d application%s visible" % (apps, "" if apps == 1 else "s"), "")


def check_card():
    try:
        import PySide6  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return (True, False, "no Qt bindings here (%s)" % exc,
                "install PySide6: `python -m pip install PySide6` or the distro package")
    for path in (os.path.join(HERE, "grammar-card.qml"),
                 os.path.join(BINDIR, "grammar-card.qml")):
        if os.path.exists(path):
            # The QML beside the host is what the host loads; a host without it fails to load the
            # card entirely, which is exactly what happened once today.
            return (True, True, "Qt %s and %s" % (PySide6.__version__, os.path.basename(path)), "")
    return (True, False, "PySide6 is here but grammar-card.qml is not",
            "run `make install` beside the host — the card cannot load without its QML")


def check_entries():
    wanted = ("grammar-lookup.desktop", "grammar-settings.desktop")
    found = [name for name in wanted if os.path.exists(os.path.join(APPDIR, name))]
    if len(found) == len(wanted):
        return (False, True, "menu entries: %s" % ", ".join(found), "")
    return (False, False, "menu entries missing: %s" % ", ".join(set(wanted) - set(found)),
            "run `make install` (they live in %s)" % APPDIR)


def main():
    quiet = "--quiet" in sys.argv[1:]
    base = api_base()
    rows = [
        ("install", check_install()),
        ("engine", check_engine(base)),
        ("engine lints", check_lints(base)),
        ("rewrite", check_rewrite(base)),
        ("watcher", check_watcher()),
        ("bus", check_bus()),
        ("card", check_card()),
        ("entries", check_entries()),
    ]
    if not quiet:
        print("grammar-doctor — engine at %s\n" % base)
    for name, (essential, ok, detail, fix) in rows:
        if ok and quiet:
            continue
        mark = "ok  " if ok else ("FAIL" if essential else "warn")
        print("  %s %-13s %s" % (mark, name, detail))
        if fix:
            print("       %-13s -> %s" % ("", fix))
    essential_rows = [(essential, ok) for _, (essential, ok, _, _) in rows]
    if not verdict(essential_rows):
        print("\n  The chain is broken: the checks marked FAIL stop suggestions from working at all.")
        return 1
    print("\n  All good. Suggestions appear a second or so after you stop typing, wherever the caret is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
