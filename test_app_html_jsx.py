"""
test_app_html_jsx.py — the buyer report's JSX (app.html <script type="text/babel">)
must compile. A JSX syntax error white-screens the whole report. This runs the
same Babel-based build guard (scripts/check_jsx.js) that gates deploys, so a broken
report is caught in the test suite too.

Skips gracefully if node or the Babel deps aren't available (the deploy guard
still fails closed; we don't want a bare CI box to red-fail unrelated runs).
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "scripts", "check_jsx.js")


def _babel_available():
    if shutil.which("node") is None:
        return False
    check = subprocess.run(
        ["node", "-e", "require('@babel/core');require.resolve('@babel/preset-react')"],
        cwd=HERE, capture_output=True,
    )
    return check.returncode == 0


@pytest.mark.skipif(not _babel_available(), reason="node or @babel not installed")
def test_app_html_jsx_compiles():
    assert os.path.exists(GUARD), "build guard scripts/check_jsx.js is missing"
    proc = subprocess.run(
        ["node", GUARD, "static/app.html"],
        cwd=HERE, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "app.html JSX failed to compile:\n" + proc.stdout + proc.stderr
    )


@pytest.mark.skipif(not _babel_available(), reason="node or @babel not installed")
def test_guard_actually_catches_broken_jsx(tmp_path):
    # Prove the JSX guard FAILS closed on a malformed tag — otherwise it's a
    # safety net that never catches anything.
    bad = tmp_path / "broken.html"
    bad.write_text(
        '<html><body>\n'
        '<script type="text/babel">\n'
        'function App(){ return (<div><span>oops</div>); }\n'  # mismatched tags
        '</script>\n'
        '</body></html>\n'
    )
    proc = subprocess.run(
        ["node", GUARD, str(bad)],
        cwd=HERE, capture_output=True, text=True,
    )
    assert proc.returncode != 0, (
        "JSX guard did NOT fail on broken JSX — the safety net is inert:\n"
        + proc.stdout + proc.stderr
    )
