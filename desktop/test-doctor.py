#!/usr/bin/env python3
"""Gate for grammar-doctor.

Two halves, and the second is the point. The verdict rule is pure and asserted directly; then the
doctor is *made to fail* by pointing it at an engine that is not there, because a diagnostic that
cannot report a fault is worse than none — it says the chain is healthy while nothing works.

Run with the system python (the one with gi); it re-execs itself like the other gates.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

try:
    import gi  # noqa: F401
except ImportError:
    if not os.environ.get("GRAMMAR_TEST_REEXEC") and os.path.exists("/usr/bin/python3"):
        os.environ["GRAMMAR_TEST_REEXEC"] = "1"
        os.execv("/usr/bin/python3", ["/usr/bin/python3", os.path.abspath(__file__)] + sys.argv[1:])

HERE = os.path.dirname(os.path.abspath(__file__))
DOCTOR = os.path.join(HERE, "grammar-doctor.py")

PASS = 0
FAIL = 0


def ok(condition, message):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok: %s" % message)
    else:
        FAIL += 1
        print("  FAIL: %s" % message)


def load():
    # The doctor's import guard re-execs for gi, which would replace this test process, so the
    # module is loaded with the guard already satisfied (we are that interpreter).
    os.environ["GRAMMAR_DOCTOR_REEXEC"] = "1"
    spec = importlib.util.spec_from_file_location("grammar_doctor", DOCTOR)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load %s" % DOCTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verdict(doctor):
    """The rule that turns rows into an exit code. Pure."""
    ok(doctor.verdict([]), "no checks at all is not a broken chain")
    ok(doctor.verdict([(True, True), (False, False)]),
       "a failing *optional* check does not break the chain (rewriting is off by design)")
    ok(not doctor.verdict([(True, True), (True, False)]),
       "a failing essential check breaks it")
    ok(not doctor.verdict([(True, False), (False, True)]),
       "and it breaks it even when every optional check passes")
    ok(doctor.verdict([(False, False), (False, False)]),
       "only essentials are consulted")


def run(env=None, args=()):
    environ = dict(os.environ)
    environ.pop("GRAMMAR_DOCTOR_REEXEC", None)      # let the child do its own re-exec
    if env:
        environ.update(env)
    return subprocess.run([sys.executable, DOCTOR] + list(args),
                          capture_output=True, text=True, env=environ, timeout=120)


def test_it_can_fail():
    """Point it at an engine that is not there: it must say so and exit non-zero.

    This is the assertion that matters. A doctor that only ever passes would have told someone
    their chain was healthy while they stared at an application that never suggested anything.
    """
    broken = run({"GRAMMAR_API": "http://127.0.0.1:9"})
    out = broken.stdout + broken.stderr
    ok(broken.returncode == 1, "with no engine it exits 1 (got %d)" % broken.returncode)
    ok("FAIL" in out and "engine" in out,
       "and it names the engine as the failure")
    ok("no answer from http://127.0.0.1:9" in out,
       "in the terms of what it asked: %r" % [l for l in out.splitlines() if "no answer" in l][:1])
    # Both engine checks must fail, not just the first: the lint check reaches the same server and
    # would otherwise report a healthy engine that cannot check anything. Asserted by row name, not
    # by count: a runner with nothing installed fails five rows, and an earlier version of this
    # demanded exactly two, which was this machine's state rather than the rule.
    ok("FAIL engine " in out and "FAIL engine lints" in out,
       "both engine rows fail with it, rather than the lint check reporting a working engine")


def test_it_can_pass():
    """On a machine where the chain works, it exits 0 and prints what it found."""
    good = run()
    out = good.stdout + good.stderr
    ok("install" in out and "engine" in out and "bus" in out and "card" in out,
       "it reports every part of the chain")
    # Quiet mode is for a person who only wants to hear about problems.
    quiet = run(args=("--quiet",))
    quiet_out = quiet.stdout + quiet.stderr
    ok("ok  " not in quiet_out, "quiet mode prints no ok lines: %r" % quiet_out.strip()[:60])
    if good.returncode == 0:
        ok("All good" in out, "and a working chain says so")
    else:
        ok("FAIL" in out or "warn" in out, "and a broken one says which part")


def test_install_check(doctor):
    """The install check has to notice the module the clients import.

    An install predating the grammar_core split has three binaries that look fine and die on
    startup with an ImportError — the silent breakage this command exists for. Proved by pointing
    the check at a directory with nothing in it, because a check that cannot fail is decoration.
    """
    essential, okd, detail, fix = doctor.check_install("/nonexistent-bindir-for-test")
    ok(essential is True, "the installed files are an essential part of the chain")
    ok(okd is False, "a directory with nothing in it fails the check")
    ok("grammar_core.py" in detail, "and the report names the module: %r" % detail)
    ok("make install" in fix, "with the fix, not just the finding: %r" % fix)
    # On the machine running the tests, the module and the card file sit where the clients look
    # for them. Skipped where nothing is installed, which is how CI runs.
    state = doctor.check_install()
    if state[1]:
        ok(all(os.path.exists(os.path.join(doctor.BINDIR, name))
               for name in ("grammar_core.py", "grammar-card.qml")),
           "this install has the module and the card file beside the scripts")
    else:
        print("  install: nothing installed here — the file list is asserted above")


def test_listen_rule(doctor):
    """The bind rule, pure: every-interface is not loopback.

    Asserted rather than trusted because a report about this machine stated "127.0.0.1:8875"
    while the unit was passing --host 0.0.0.0 — the value was read wrong, and the reading is
    the only part of this that is logic rather than a request.
    """
    ok(doctor.listen_mode("0.0.0.0:8875") == "lan", "0.0.0.0 is every interface")
    ok(doctor.listen_mode("[::]:8875") == "lan", "and so is the IPv6 any-address")
    ok(doctor.listen_mode("0.0.0.0:8875") != "loopback", "neither is loopback")
    ok(doctor.listen_mode("127.0.0.1:8875") == "loopback", "127.0.0.1 is loopback")
    ok(doctor.listen_mode("localhost:8875") == "loopback", "and so is the name for it")
    ok(doctor.listen_mode("") == "unknown", "an engine that reports nothing is unknown, not a fault")


def test_listen_row(doctor):
    """The row itself: an engine that is not there must not be reported as reachable."""
    essential, okd, detail, fix = doctor.check_listen("http://127.0.0.1:9")
    ok(essential is False, "who can reach the engine is not essential to the chain")
    ok(okd is False, "with no engine it does not claim a healthy one: %r" % detail)
    ok("no answer" in detail, "it says what it asked and got nothing")


def test_entries_check(doctor):
    """The entries check, including the icon an entry names.

    Run against a temporary tree rather than the real one: the real install is the install's business,
    and what has to be proved here is that a missing entry is reported, that an icon which resolves
    nowhere is reported and is only a warning (a blank menu square is not a broken suggestion chain),
    and that a complete pair passes.
    """
    tmp = tempfile.mkdtemp(prefix="grammar-entries-")
    data = os.path.join(tmp, "share")
    old_apdir, old_data = doctor.APPDIR, os.environ.get("XDG_DATA_HOME")
    names = ("grammar-lookup.desktop", "grammar-settings.desktop", "grammar-accept.desktop",
             "grammar-dismiss.desktop")
    try:
        doctor.APPDIR = os.path.join(tmp, "applications")
        os.makedirs(doctor.APPDIR)
        essential, okd, detail, fix = doctor.check_entries()
        ok(okd is False and "grammar-lookup.desktop" in detail,
           "an empty applications directory reports the missing entries: %r" % detail)
        ok("make install" in fix, "with the fix rather than the bare finding: %r" % fix)

        for name in names:
            with open(os.path.join(doctor.APPDIR, name), "w") as fh:
                fh.write("[Desktop Entry]\nIcon=grammar-ui\nExec=/bin/true\n")
        os.environ["XDG_DATA_HOME"] = data
        essential, okd, detail, fix = doctor.check_entries()
        ok(okd is False and "grammar-ui" in detail,
           "an icon nothing installed is reported, by name: %r" % detail)
        ok(essential is False,
           "and it is a warning: a blank menu square does not stop suggestions")

        icons = os.path.join(data, "icons", "hicolor", "scalable", "apps")
        os.makedirs(icons)
        with open(os.path.join(icons, "grammar-ui.svg"), "w") as fh:
            fh.write("<svg/>")
        essential, okd, detail, fix = doctor.check_entries()
        ok(okd is True, "with the icon installed the check passes: %r" % detail)
        ok("4 installed" in detail, "and it says how many entries it looked at: %r" % detail)
    finally:
        doctor.APPDIR = old_apdir
        if old_data is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_data
        shutil.rmtree(tmp, ignore_errors=True)


def test_pause_row(doctor):
    """A pause is reported, with when it ends and how to end it early.

    A checker that has gone quiet with no explanation is the worst version of this feature, and the
    doctor is exactly where "why is nothing showing up?" is supposed to be answered.
    """
    tmp = tempfile.mkdtemp(prefix="grammar-pause-")
    path = os.path.join(tmp, "paused-until")
    try:
        essential, okd, detail, fix = doctor.check_pause(path)
        ok(okd is True and "not paused" in detail, "with no pause the row says so: %r" % detail)
        with open(path, "w") as fh:
            fh.write("%d\n" % (time.time() + 600))
        essential, okd, detail, fix = doctor.check_pause(path)
        ok(okd is False and "paused until" in detail,
           "a pause is reported, with the time it ends: %r" % detail)
        ok(essential is False, "and it is a warning: the checker was told to be quiet, not broken")
        ok("grammar-pause off" in fix, "with the command that ends it early: %r" % fix)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    doctor = load()
    test_verdict(doctor)
    test_install_check(doctor)
    test_entries_check(doctor)
    test_pause_row(doctor)
    test_listen_rule(doctor)
    test_listen_row(doctor)
    test_it_can_fail()
    test_it_can_pass()
    print("grammar-doctor: %d assertions - %s" % (PASS + FAIL, "passed" if not FAIL else "FAILED"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
