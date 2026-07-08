#!/usr/bin/env python3
"""
prepackage_guard.py — v5.89.274

The two CI breaks in v5.89.272 and .273 shared a root cause: py_compile validates
SYNTAX but not IMPORT or the fast gate suite, so a module-level NameError (a bad
decorator) and an API-coverage regression both passed local checks and only failed
in CI — after a deploy was already attempted.

This guard closes that gap. It runs BEFORE the tarball is built and checks the two
proven failure classes:

  1. Import every route module (and the blueprints they define). A module-level
     NameError / AttributeError from a decorator or a missing symbol crashes the
     gunicorn worker at boot — exactly the '_dev_only_gate is not defined' failure.
     py_compile can't see it; importing the module can.

  2. Run the API-coverage regression floor gate. Adding a route without a
     route-referencing test drops coverage below the recorded floor and fails CI.

Exit 0 = safe to package. Exit 1 = a real problem CI would also catch.

Note on dependencies: this must run in an environment where the app's Python deps
are installed (flask, flask-login, sqlalchemy, etc.). A ModuleNotFoundError for one
of those is reported as an ENVIRONMENT problem (exit 2), distinct from a code bug
(exit 1), so a bare sandbox doesn't masquerade as a code failure.
"""
import importlib
import os
import subprocess
import sys

# Route modules whose blueprints register at app import. A NameError here = dead boot.
ROUTE_MODULES = [
    "admin_routes", "analysis_routes", "testing_routes", "bug_routes",
    "docrepo_routes", "survey_routes", "waitlist_routes", "sharing_routes",
    "payment_routes",
]

# Well-known third-party deps: if THESE are missing it's an env problem, not a bug.
_ENV_DEPS = {
    "flask", "flask_login", "flask_sqlalchemy", "flask_cors", "flask_limiter",
    "flask_migrate", "flask_compress", "sqlalchemy", "authlib", "stripe",
    "resend", "anthropic", "pyyaml", "yaml", "PyPDF2", "pdfplumber", "sentry_sdk",
}


def _missing_module_name(exc: BaseException):
    if isinstance(exc, ModuleNotFoundError):
        return getattr(exc, "name", None)
    return None


def check_imports():
    """Returns (code_failures, env_missing). code_failures are real bugs."""
    code_failures, env_missing = [], []
    for mod in ROUTE_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001 — we classify below
            missing = _missing_module_name(e)
            if missing and (missing.split(".")[0] in _ENV_DEPS):
                env_missing.append((mod, missing))
            else:
                code_failures.append((mod, f"{type(e).__name__}: {e}"))
    return code_failures, env_missing


def check_coverage_gate():
    env = dict(os.environ, FLASK_ENV="testing", SECRET_KEY="prepackage-guard",
               PYTEST_ISOLATED="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest",
         "test_coverage_final.py::TestCoverageGate::test_api_coverage_regression_floor",
         "-p", "no:cacheprovider", "-q"],
        env=env, capture_output=True, text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(repo_root)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    print("== prepackage guard ==")

    print("-- importing route modules (catches boot-time NameErrors) --")
    code_failures, env_missing = check_imports()
    if code_failures:
        print("❌ IMPORT FAILURE — this would crash the gunicorn worker at boot:")
        for mod, err in code_failures:
            print(f"     {mod}: {err}")
        return 1
    if env_missing:
        # Couldn't fully verify — a required dep isn't installed here.
        print("⚠️  could not verify imports — missing environment deps:")
        for mod, dep in env_missing:
            print(f"     {mod}: needs '{dep}'")
        print("   install app deps and re-run; NOT treating as a code failure.")
        return 2
    print(f"✅ all {len(ROUTE_MODULES)} route modules import clean")

    print("-- API-coverage regression floor gate --")
    rc, out = check_coverage_gate()
    if rc != 0:
        print("❌ API-coverage floor gate FAILED "
              "(a new route needs a route-referencing test):")
        print("   " + "\n   ".join(out.strip().splitlines()[-8:]))
        return 1
    print("✅ coverage floor gate passes")

    print("== guard passed — safe to package ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
