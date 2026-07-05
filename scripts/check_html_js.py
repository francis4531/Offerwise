#!/usr/bin/env python3
"""
check_html_js.py — build guard for inline JavaScript in server-rendered HTML.

Exists because of a real production incident: an edit to static/admin.html dropped
a function declaration, which turned the next function's body into orphaned
top-level code whose `await` is illegal at the top level. That SyntaxError halted
the entire script block, so every function defined after it never registered and
the admin panel white-screened. The mistake survived because validation had run
`node --check` on the *edited function in isolation* — which cannot see that an
insertion broke the surrounding structure. Top-level-await and dropped-sibling
errors only surface when the WHOLE script block is parsed.

This guard does exactly that: for each target HTML file it extracts every inline
<script> block (those without a src=), runs `node --check` on each block IN
CONTEXT, and maps any error line back to the real line in the source file. It also
checks <div> balance (a separate admin.html failure mode). It FAILS CLOSED — a
missing `node`, or any block that doesn't parse, exits non-zero — so a broken file
can never be packaged silently.

Usage:
    python3 scripts/check_html_js.py                 # checks the default targets
    python3 scripts/check_html_js.py static/foo.html # checks specific files

Only plain-JS HTML belongs here. Do NOT add JSX/Babel files (e.g. static/app.html
uses in-browser Babel) — node --check can't parse JSX and would false-positive.
"""
import os
import re
import subprocess
import sys

# Server-rendered HTML whose inline <script> is plain JS (no JSX/Babel).
DEFAULT_TARGETS = ["static/admin.html"]

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)


def _has_node() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def _line_of_offset(text: str, offset: int) -> int:
    """1-based line number of a character offset in text."""
    return text.count("\n", 0, offset) + 1


def _check_file(path: str) -> list:
    """Return a list of human-readable failure strings (empty = clean)."""
    failures = []
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()

    # 1) <div> balance (net) — the other admin.html footgun.
    opens = len(re.findall(r"<div\b", src))
    closes = src.count("</div>")
    if opens != closes:
        failures.append(f"{path}: <div> imbalance — {opens} open vs {closes} close")

    # 2) node --check every inline, non-src <script> block IN CONTEXT.
    blocks = []
    for m in _SCRIPT_RE.finditer(src):
        attrs, body = m.group(1) or "", m.group(2) or ""
        if re.search(r"\bsrc\s*=", attrs, re.I):
            continue  # external script, nothing inline to parse
        if not body.strip():
            continue
        start_line = _line_of_offset(src, m.start(2))
        blocks.append((start_line, body))

    if blocks and not _has_node():
        failures.append(
            f"{path}: node is not available — cannot validate {len(blocks)} inline "
            f"script block(s). Install Node so the build can parse admin JS."
        )
        return failures

    for start_line, body in blocks:
        # Check in CommonJS SCRIPT context via stdin — this matches how the browser
        # runs an inline <script> (classic script), where top-level `await` is a
        # SyntaxError. A plain temp-file `node --check` treats an ambiguous file as
        # an ES module and would MISS top-level await — the exact incident class
        # this guard exists to catch. Piping with --input-type=commonjs forces the
        # correct semantics.
        proc = subprocess.run(
            ["node", "--check", "--input-type=commonjs"],
            input=body, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            # drop the noisy ESM warning lines; keep the real error.
            err = "\n".join(l for l in err.split("\n")
                            if "Failed to load the ES module" not in l
                            and "trace-warnings" not in l).strip()
            mapped = err
            mm = re.search(r"(?:\[stdin\]|:)(\d+)\b", err)
            if mm:
                real = start_line + int(mm.group(1)) - 1
                mapped = f"{path}:{real} (block starting at line {start_line})\n{err}"
            failures.append(f"{path}: inline script block failed node --check ->\n{mapped}")

    return failures


def main(argv):
    targets = argv[1:] or DEFAULT_TARGETS
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    all_failures = []
    checked = 0
    for t in targets:
        p = t if os.path.isabs(t) else os.path.join(here, t)
        if not os.path.exists(p):
            all_failures.append(f"{t}: file not found")
            continue
        checked += 1
        all_failures.extend(_check_file(p))

    if all_failures:
        print("check_html_js: FAILED")
        for f in all_failures:
            print("  ✗ " + f.replace("\n", "\n    "))
        return 1
    print(f"check_html_js: OK — {checked} file(s), all inline script blocks parse; <div> balanced")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
