#!/usr/bin/env python3
"""
stamp_assets.py - cache-busting + version-stamp for OfferWise static assets.

Run as a standard packaging step (before building the deploy tarball). It does
two things, both driven by the single source of truth in ./VERSION:

  1. Stamps `?v=<VERSION>` onto every LOCAL `/static/*.js` and `/static/*.css`
     reference in static/*.html and templates/*.html. Existing `?v=...` stamps
     are replaced, so re-running is idempotent. External URLs (fonts, CDNs) are
     never touched - only paths beginning with `/static/`.

  2. Refreshes the `component (vX.Y.Z)` header comment inside shared JS files so
     a file never again reports a version older than the build it ships in.

Why: browsers (and any CDN keyed on URL) cache `/static/ask-widget.js` forever
because the URL never changes. A version query string makes each deploy a new
URL, so the new file is fetched immediately - no hard refresh, no stale styling,
and the loaded version is visible in the URL / network tab.

Usage:  python3 scripts/stamp_assets.py
Exit:   0 on success; prints a summary of what changed.
"""
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text().strip()

# 1) HTML asset references -------------------------------------------------
HTML_DIRS = [ROOT / "static", ROOT / "templates"]
# (src|href)="/static/<path>.<js|css>"  with optional existing ?v=... stamp
ASSET_RE = re.compile(r'(\b(?:src|href)=")(/static/[^"?]+\.(?:js|css))(?:\?v=[^"]*)?(")')


def _stamp(m):
    return f'{m.group(1)}{m.group(2)}?v={VERSION}{m.group(3)}'


def stamp_html():
    changed, refs = 0, 0
    for d in HTML_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.html")):
            txt = f.read_text()
            new, n = ASSET_RE.subn(_stamp, txt)
            refs += n
            if new != txt:
                f.write_text(new)
                changed += 1
    return changed, refs


# 2) JS header version comments -------------------------------------------
VER_COMMENT_RE = re.compile(r'(component \(v)\d+\.\d+\.\d+(\))')


def stamp_js_comments():
    changed = 0
    for f in sorted((ROOT / "static").glob("*.js")):
        txt = f.read_text()
        new, n = VER_COMMENT_RE.subn(rf'\g<1>{VERSION}\g<2>', txt)
        if n and new != txt:
            f.write_text(new)
            changed += 1
    return changed


def main():
    html_files_changed, refs_stamped = stamp_html()
    js_comments_changed = stamp_js_comments()
    print(f"stamp_assets: VERSION={VERSION}")
    print(f"  HTML files updated : {html_files_changed}")
    print(f"  asset refs stamped : {refs_stamped}")
    print(f"  JS comments bumped : {js_comments_changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
