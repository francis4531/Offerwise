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


DUP_GUARD = os.path.join(HERE, "scripts", "check_dup_declarations.js")


@pytest.mark.skipif(not _babel_available(), reason="node or @babel not installed")
def test_no_duplicate_or_self_ref_declarations():
    """app.html must have no duplicate/self-referential let/const in any scope —
    the bug class that white-screened analysis in v5.89.293/294 (a self-referential
    `const progressMessages = window.__x || progressMessages` on the retry path).
    Valid JS syntax, so check_jsx compiles it; only this static-scope check catches it."""
    assert os.path.exists(DUP_GUARD), "build guard scripts/check_dup_declarations.js is missing"
    proc = subprocess.run(["node", DUP_GUARD], cwd=HERE, capture_output=True, text=True)
    assert proc.returncode == 0, (
        "app.html has a duplicate/self-referential declaration:\n" + proc.stdout + proc.stderr
    )


@pytest.mark.skipif(not _babel_available(), reason="node or @babel not installed")
def test_dup_guard_catches_the_real_bug(tmp_path, monkeypatch):
    """Prove the guard FAILS on the exact shipped bug — otherwise it's a net that
    never catches anything. We run the guard against a copy of app.html with the
    v5.89.294 bug re-injected, via an env override the guard honors for testing."""
    import shutil
    src = os.path.join(HERE, "static", "app.html")
    html = open(src, encoding="utf-8").read()
    # re-inject the exact bug next to the (single) sseSource declaration
    broken = html.replace(
        "let sseSource = null;",
        "let sseSource = null;\n                const progressMessages = "
        "window.__owProgressMessages || progressMessages;",
        1,
    )
    assert broken != html, "could not locate injection point"
    bad_dir = tmp_path / "static"
    bad_dir.mkdir()
    (bad_dir / "app.html").write_text(broken, encoding="utf-8")
    # the guard reads static/app.html relative to its own dir; point it at the copy
    proc = subprocess.run(
        ["node", DUP_GUARD],
        cwd=HERE, capture_output=True, text=True,
        env={**os.environ, "OW_APP_HTML_OVERRIDE": str(bad_dir / "app.html")},
    )
    assert proc.returncode != 0, "guard did NOT catch the re-injected duplicate declaration"
    assert "progressMessages" in (proc.stdout + proc.stderr)
