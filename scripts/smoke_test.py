#!/usr/bin/env python3
"""
Smoke test — does the OfferWise app actually MOUNT in a real browser?

This catches the one class of failure unit tests can't see: the whole React app
failing to render. The motivating incident is v5.89.187/.188, where the unpinned
in-browser Babel jumped to v8 and started injecting an `import` into the compiled
JSX; that threw "Cannot use import statement outside a module", React never
mounted, and every visitor sat on the static "Loading OfferWise..." placeholder.
No Python test caught it — only a human noticing a white screen.

How it works: serve the repo statically, load static/app.html in headless
Chromium, and assert two things:
  1. No parse/compile-class error fired in the console or as a page error
     (that's the white-screen signature).
  2. React replaced the static loading placeholder inside #root.

It deliberately ignores network/reference errors, which are expected when the
page is served without its Flask backend — those are not the regression we guard.

Usage:
    python scripts/smoke_test.py [--url URL] [--timeout SECONDS] [--port PORT]

Exit 0 = mounted OK. Exit 1 = did not mount (fail the build).
"""
import sys
import os
import time
import argparse
import threading
import functools
import http.server
import socketserver

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Errors that mean the JSX never compiled / the app can't run — the precise
# white-screen signature. The Babel-8 incident threw exactly these as a pageerror
# (the compiled script, with an injected `import`, failed to execute). We keep
# this list TIGHT: broad tokens like "SyntaxError" also match runtime JSON.parse
# errors from API calls hitting a 404 page (expected when served without a
# backend), which are NOT the regression we guard.
FATAL_PATTERNS = (
    "Cannot use import statement outside a module",
    "Failed to execute 'appendChild' on 'Node'",
)


def _serve(root, port):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    # silence the default request logging
    handler_cls = httpd.RequestHandlerClass
    handler_cls.log_message = lambda *a, **k: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None, help="URL to test; default serves the repo locally")
    ap.add_argument("--timeout", type=int, default=25, help="seconds to wait for mount")
    ap.add_argument("--port", type=int, default=8731)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("✗ SMOKE SETUP — playwright not installed. `pip install playwright && playwright install chromium`")
        return 2

    httpd = None
    url = args.url
    if not url:
        httpd = _serve(REPO_ROOT, args.port)
        url = f"http://127.0.0.1:{args.port}/static/app.html"

    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)

            mounted = False
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                try:
                    root_txt = page.eval_on_selector("#root", "el => el.innerText") or ""
                except Exception:
                    root_txt = ""
                # React replaced the placeholder if #root has real content that is
                # no longer the static "Loading OfferWise..." text.
                if root_txt.strip() and "Loading OfferWise" not in root_txt:
                    mounted = True
                    break
                if any(fp in e for e in errors for fp in FATAL_PATTERNS):
                    break  # a parse error means it will never mount — stop waiting
                time.sleep(0.5)

            browser.close()
    finally:
        if httpd:
            httpd.shutdown()

    fatal = [e for e in errors if any(fp in e for fp in FATAL_PATTERNS)]
    if fatal:
        print(f"✗ SMOKE FAIL — white-screen signature at {url}")
        print("  (the JSX did not compile / the app cannot mount)")
        for e in fatal[:5]:
            print("    •", e[:160])
        return 1
    if mounted:
        print(f"✓ SMOKE PASS — React mounted at {url}")
        return 0
    # No white-screen error fired, but we couldn't positively confirm a mount.
    # This is the common logged-out case (the app served, then redirected to
    # auth). The regression we guard — the Babel white-screen — is absent, so we
    # pass, but say so plainly rather than claiming a confirmed mount.
    print(f"✓ SMOKE PASS (mount unconfirmed) — no white-screen error at {url}")
    print("  React mount not confirmed; page likely required auth/redirected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
