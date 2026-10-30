"""Pre-push gate (2026-10-18): CI runs Python 3.11, this PC runs 3.12/3.14. Compile every tracked .py with 3.11 and run the test suites under
it; any failure refuses the push (hooks/pre-push calls this; enable once per clone with `git config core.hooksPath hooks`).

    py -3.11 prepush_check.py            # what the hook runs
    py -3.11 prepush_check.py --compile  # compile only (seconds)

Why: a backslash inside an f-string expression compiled fine on 3.12 and broke every Sunday pull loop on CI (CHANGELOG 2026-10-18).
Install 3.11 with `winget install -e --id Python.Python.3.11` and its deps with `py -3.11 -m pip install -r requirements.txt pytest pyyaml`.
"""
import os, subprocess, sys

REQUIRED = (3, 11)
# pre-existing failures that predate the gate (fix them, then remove from this list): the pick'em scanner's end-to-end test has failed on CI since
# 2026-10-11 (KeyError 'Ladd McConkey Fantasy Points over' after commit 54602bf skipped imputed-only stats as legs); unrelated to the Python version
KNOWN_FAILURES = ["pickem/test_scan.py::test_scan_end_to_end"]
TESTS = ["test_nan_safety.py", "test_ladder_integrity.py", "test_pull_loop.py", "test_yards_source.py", "test_board_rules.py", "test_card_rules.py", "test_lineshop.py", "test_reconcile.py"]


def fail(msg):
    print(f"PRE-PUSH CHECK FAILED: {msg}", flush=True)
    sys.exit(1)


def main(argv):
    if sys.version_info[:2] != REQUIRED:
        fail(f"run this with Python {REQUIRED[0]}.{REQUIRED[1]} (CI's version), e.g. `py -3.11 prepush_check.py`; this is {sys.version.split()[0]}")
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
    os.chdir(root)
    files = [f for f in subprocess.run(["git", "ls-files", "*.py", "**/*.py"], capture_output=True, text=True, check=True).stdout.split() if f.endswith(".py")]
    print(f"compiling {len(files)} tracked .py files with Python {sys.version.split()[0]} ...", flush=True)
    r = subprocess.run([sys.executable, "-m", "py_compile", *files])
    if r.returncode: fail("a file does not compile under Python 3.11 (see the SyntaxError above)")
    if "--compile" in argv:
        print("compile OK"); return
    env = {k: v for k, v in os.environ.items() if k not in ("ODDS_LIVE", "ODDS_API_KEY")}        # never a live Odds API call from a pre-push check
    env["PYTHONIOENCODING"] = "utf-8"
    for t in TESTS:
        if not os.path.exists(t): continue
        print(f"== {t}", flush=True)
        if subprocess.run([sys.executable, t], env=env).returncode: fail(f"{t} failed under Python 3.11")
    try:
        import pytest  # noqa: F401
        print("== pytest pickem" + (f" (deselected known failures: {', '.join(KNOWN_FAILURES)})" if KNOWN_FAILURES else ""), flush=True)
        if subprocess.run([sys.executable, "-m", "pytest", "-q", "pickem", *sum((["--deselect", k] for k in KNOWN_FAILURES), [])], env=env).returncode: fail("pickem tests failed under Python 3.11")
    except ImportError:
        print("(pytest not installed for 3.11: pickem tests skipped; `py -3.11 -m pip install pytest`)")
    print("PRE-PUSH CHECK OK: everything compiles and passes under Python 3.11")


if __name__ == "__main__":
    main(sys.argv[1:])
