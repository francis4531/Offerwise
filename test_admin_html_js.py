"""
test_admin_html_js.py — the inline JS in admin.html must parse. A dropped
declaration or top-level await halts the whole <script> block and white-screens
the admin panel (this happened in production). This runs the same build guard
(scripts/check_html_js.py) that gates deploys, so the failure is caught in the
test suite too, not only at deploy time.

Skips gracefully if Node isn't installed (the guard itself fails closed at deploy;
here we don't want a node-less CI box to red-fail unrelated runs).
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "scripts", "check_html_js.py")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_admin_html_inline_js_parses():
    assert os.path.exists(GUARD), "build guard scripts/check_html_js.py is missing"
    proc = subprocess.run(
        [sys.executable, GUARD, "static/admin.html"],
        cwd=HERE, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "admin.html inline JS failed to parse:\n" + proc.stdout + proc.stderr
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_guard_actually_catches_broken_inline_js(tmp_path):
    # A guard that only ever passes is worthless. Prove it FAILS closed on the
    # exact incident class: a dropped declaration turning a body's `await` into
    # top-level await. Reproduce it in a temp file and assert non-zero exit.
    bad = tmp_path / "broken.html"
    bad.write_text(
        "<html><body>\n"
        "<script>\n"
        "async function ok() { await fetch('/x'); }\n"
        "  const y = await fetch('/y');\n"   # top-level await — illegal
        "</script>\n"
        "</body></html>\n"
    )
    proc = subprocess.run(
        [sys.executable, GUARD, str(bad)],
        cwd=HERE, capture_output=True, text=True,
    )
    assert proc.returncode != 0, (
        "guard did NOT fail on broken inline JS — the safety net is inert:\n"
        + proc.stdout + proc.stderr
    )
