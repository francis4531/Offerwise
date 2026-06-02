"""
meta_inspector.py — v5.89.75

Scans static guides and surfaces meta-quality issues for batch SEO work.

Read-only. No file mutation. Returns structured findings per page.

Used by /api/admin/meta-audit endpoint in admin_routes.py.
"""

import os
import re
import datetime
from html.parser import HTMLParser

# ── Limits per SERP truncation rules ────────────────────────────
TITLE_MAX = 60         # Google truncates titles around 55-60 chars (pixel-based)
DESC_MAX_IDEAL = 160   # ~155-160 chars is the truncation ceiling
DESC_MIN_IDEAL = 110   # Below this, Google may rewrite the description

# Placeholder patterns commonly found in templated pages that didn't substitute.
# The bar is: would this appear in natural prose? If yes, exclude from pattern.
# "in your state" / "your state's" — natural English, NOT a placeholder.
# "your state law" / "your state requires" / "your state Civil Code" — broken templating.
PLACEHOLDER_PATTERNS = [
    # "your state" used as a noun in a way that doesn't work in English:
    r'\byour state law\b',          # "your state law requires" — clearly broken
    r'\byour state has some\b',     # "your state has some of the strictest..."
    r'\byour state Civil Code\b',
    r'\byour state mandates\b',
    r'\byour state Inspection\b',   # link anchor text
    r'\bThe your state\b',          # "The your state Legal Context" (H2)
    r'\bIn your state, sellers\b',  # "In your state, sellers must..."
    r'\bIn your state, buyers\b',
    r'\bcity inspection requirements in your state\b',
    # Other clear templating markers
    r'\{\{[^}]+\}\}',
    r'\{[A-Z_][A-Z_0-9]{2,}\}',
    r'\$\{[^}]+\}',
    r'\bTODO:?\s',
    r'\bFIXME:?\s',
    r'\[INSERT[^\]]*\]',
    r'\bLorem ipsum\b',
]

# Stale: page hasn't been touched in 90+ days (rolling window)
STALE_DAYS = 90


class _MetaParser(HTMLParser):
    """Tiny parser to pull out <title>, <meta name|property=...>, and first <h1>."""
    def __init__(self):
        super().__init__()
        self.title = None
        self._in_title = False
        self._title_done = False     # don't capture nested SVG <title> tags
        self.meta = {}            # name → content
        self.og = {}              # og:* → content
        self.twitter = {}         # twitter:* → content
        self.h1 = None
        self._in_h1 = False
        self._h1_buf = []
        self.has_canonical = False
        self.has_schema = False
        self.h2s = []
        self._in_h2 = False
        self._h2_buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'title' and not self._title_done:
            self._in_title = True
        elif tag == 'meta':
            name = (a.get('name') or '').strip().lower()
            prop = (a.get('property') or '').strip().lower()
            content = (a.get('content') or '').strip()
            if name and content:
                if name.startswith('twitter:'):
                    self.twitter[name[8:]] = content
                else:
                    self.meta[name] = content
            elif prop and content:
                if prop.startswith('og:'):
                    self.og[prop[3:]] = content
                else:
                    self.meta[prop] = content
        elif tag == 'link' and (a.get('rel') or '').lower() == 'canonical':
            self.has_canonical = True
        elif tag == 'script' and (a.get('type') or '').lower() == 'application/ld+json':
            self.has_schema = True
        elif tag == 'h1' and self.h1 is None:
            self._in_h1 = True
            self._h1_buf = []
        elif tag == 'h2':
            self._in_h2 = True
            self._h2_buf = []

    def handle_endtag(self, tag):
        if tag == 'title' and self._in_title:
            self._in_title = False
            self._title_done = True
        elif tag == 'h1' and self._in_h1:
            self._in_h1 = False
            self.h1 = ''.join(self._h1_buf).strip()
        elif tag == 'h2' and self._in_h2:
            self._in_h2 = False
            txt = ''.join(self._h2_buf).strip()
            if txt:
                self.h2s.append(txt)

    def handle_data(self, data):
        if self._in_title and not self._title_done:
            if self.title is None:
                self.title = data.strip()
            else:
                self.title += data.strip()
        elif self._in_h1:
            self._h1_buf.append(data)
        elif self._in_h2:
            self._h2_buf.append(data)


def suggest_title(title: str, max_len: int = TITLE_MAX) -> str | None:
    """v5.89.76: produce a tightened version of a too-long title.

    Conservative — applies known-safe transformations only (strip site
    brand suffix, strip year-tag trailers, replace common bloat phrases).
    Returns the tightened title if it fits under max_len; returns None
    if more aggressive cutting would be needed (caller should surface
    the page for manual review).

    The conservative design is intentional: aggressive cutting on " : "
    or " — " separators can drop searchable keywords from the title's
    tail, which is worse than leaving the title slightly long.
    """
    if not title or len(title) <= max_len:
        return None

    s = title
    # Strip site brand suffix
    s = re.sub(r'\s*\|\s*OfferWise\s*$', '', s)
    # Strip year trailers
    s = re.sub(r'\s*\(2026[^)]*\)\s*', ' ', s)
    s = re.sub(r'\s*\(Updated\s+2026\)\s*', ' ', s, flags=re.IGNORECASE)
    # Common phrase shortenings
    s = re.sub(r'Frequently Asked Questions', 'FAQ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bComplete Guide to\b', 'Guide:', s)
    s = re.sub(r'\bA Complete Guide\b', 'Guide', s, flags=re.IGNORECASE)
    s = re.sub(r":\s*A Buyer's Guide", '', s)
    s = re.sub(r'\bWhat You Need to Know\b', '', s)
    s = re.sub(r'\bThe Complete\b', '', s, flags=re.IGNORECASE)
    s = re.sub(
        r'\bUpdated\s+(May|June|July|Aug|Sept?|Oct|Nov|Dec|Jan|Feb|Mar|Apr)\w*\s+2026\b',
        '', s, flags=re.IGNORECASE,
    )
    # Whitespace and punctuation cleanup
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\s+:', ':', s)
    s = re.sub(r':\s+$', '', s)
    s = s.strip().strip(':').strip()

    return s if len(s) <= max_len else None


def inspect_file(filepath: str, base_url: str = 'https://www.getofferwise.ai') -> dict:
    """Return meta inspection results for a single HTML file.

    Args:
        filepath: absolute or repo-relative path to the HTML file
        base_url: site root for building canonical URLs

    Returns a dict with structure:
        {
            'path': '/guides/example',
            'filename': 'example.html',
            'title': str | None,
            'title_length': int,
            'title_truncates': bool,
            'description': str | None,
            'description_length': int,
            'description_too_long': bool,
            'description_too_short': bool,
            'h1': str | None,
            'h2_count': int,
            'has_canonical': bool,
            'has_schema': bool,
            'has_og_title': bool,
            'has_og_description': bool,
            'has_twitter_card': bool,
            'placeholders_found': List[str],  # matched placeholder text
            'last_modified_iso': str,
            'stale': bool,
            'issues': List[str],  # human-readable list of problems
            'score': int,  # 0-100, lower = worse
        }
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()
    except Exception as e:
        return {
            'path': filepath,
            'filename': os.path.basename(filepath),
            'error': f'read_error: {e}',
            'issues': ['Could not read file'],
            'score': 0,
        }

    parser = _MetaParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    # File modification time
    try:
        mtime = os.path.getmtime(filepath)
        last_modified = datetime.datetime.fromtimestamp(mtime)
        last_modified_iso = last_modified.isoformat()
        days_since = (datetime.datetime.now() - last_modified).days
        stale = days_since > STALE_DAYS
    except Exception:
        last_modified_iso = None
        stale = False
        days_since = 0

    # Placeholder detection across the full HTML body, not just meta
    placeholders = []
    for pattern in PLACEHOLDER_PATTERNS:
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            # Dedupe but keep first 3 occurrences for reporting
            uniq = list(dict.fromkeys(matches))[:3]
            placeholders.extend(uniq)

    title = parser.title or ''
    description = parser.meta.get('description', '') or ''

    title_length = len(title)
    description_length = len(description)
    title_truncates = title_length > TITLE_MAX
    description_too_long = description_length > DESC_MAX_IDEAL
    description_too_short = 0 < description_length < DESC_MIN_IDEAL

    # Derived path
    filename = os.path.basename(filepath)
    # /guides/example.html → /guides/example
    if filepath.endswith('.html'):
        slug = filename[:-5]
    else:
        slug = filename
    if 'guides/' in filepath:
        path = f'/guides/{slug}'
    else:
        path = f'/{slug}'

    # Score it: start at 100, dock points for each issue
    score = 100
    issues = []

    if not title:
        issues.append('Missing title tag')
        score -= 25
    elif title_truncates:
        issues.append(f'Title too long ({title_length} chars, truncates at ~60)')
        score -= 8

    if not description:
        issues.append('Missing meta description')
        score -= 20
    elif description_too_long:
        issues.append(f'Description too long ({description_length} chars)')
        score -= 5
    elif description_too_short:
        issues.append(f'Description too short ({description_length} chars; Google may rewrite)')
        score -= 5

    if not parser.has_canonical:
        issues.append('Missing canonical link')
        score -= 5

    if not parser.has_schema:
        issues.append('Missing schema.org JSON-LD')
        score -= 5

    if 'title' not in parser.og or 'description' not in parser.og:
        issues.append('Missing or incomplete OpenGraph tags')
        score -= 5

    if 'card' not in parser.twitter and 'title' not in parser.twitter:
        issues.append('Missing Twitter card tags')
        score -= 3

    if not parser.h1:
        issues.append('Missing H1')
        score -= 10

    if parser.h2s and len(parser.h2s) < 3:
        issues.append(f'Only {len(parser.h2s)} H2 sections (thin content)')
        score -= 5

    if placeholders:
        issues.append(f'Placeholder text found: {", ".join(placeholders[:3])}')
        score -= 30  # Critical — looks broken in SERP

    if stale and not placeholders:
        # Don't double-dock for stale + placeholder (the placeholder is the bigger crime)
        issues.append(f'Stale (not modified in {days_since} days)')
        score -= 5

    score = max(0, score)

    # v5.89.76: auto-suggested shorter title if current one truncates
    suggested_title = suggest_title(title) if title_truncates else None

    return {
        'path': path,
        'filename': filename,
        'title': title,
        'title_length': title_length,
        'title_truncates': title_truncates,
        'suggested_title': suggested_title,
        'suggested_title_length': len(suggested_title) if suggested_title else 0,
        'description': description,
        'description_length': description_length,
        'description_too_long': description_too_long,
        'description_too_short': description_too_short,
        'h1': parser.h1,
        'h2_count': len(parser.h2s),
        'has_canonical': parser.has_canonical,
        'has_schema': parser.has_schema,
        'has_og_title': 'title' in parser.og,
        'has_og_description': 'description' in parser.og,
        'has_twitter_card': bool(parser.twitter),
        'placeholders_found': placeholders[:5],
        'last_modified_iso': last_modified_iso,
        'days_since_modified': days_since,
        'stale': stale,
        'issues': issues,
        'score': score,
    }


def inspect_all_guides(guides_dir: str = 'static/guides') -> dict:
    """Run inspect_file on every .html file in the guides directory.

    Returns:
        {
            'guides': List[file_inspection],  # sorted by score ascending (worst first)
            'total': int,
            'critical_count': int,   # placeholders or score <40
            'warning_count': int,    # 40 <= score < 70
            'healthy_count': int,    # score >= 70
            'placeholder_count': int,
            'fetched_at': str,
        }
    """
    if not os.path.isdir(guides_dir):
        return {
            'guides': [],
            'total': 0,
            'error': f'Directory not found: {guides_dir}',
        }

    results = []
    for fname in sorted(os.listdir(guides_dir)):
        if not fname.endswith('.html'):
            continue
        if fname == 'index.html':
            continue
        full = os.path.join(guides_dir, fname)
        results.append(inspect_file(full))

    # Sort: critical issues first (placeholders), then by score ascending
    def sort_key(r):
        has_placeholder = bool(r.get('placeholders_found'))
        return (0 if has_placeholder else 1, r.get('score', 100))
    results.sort(key=sort_key)

    critical = sum(1 for r in results if r.get('placeholders_found') or r.get('score', 100) < 40)
    warning = sum(1 for r in results if 40 <= r.get('score', 100) < 70)
    healthy = sum(1 for r in results if r.get('score', 100) >= 70)
    placeholders = sum(1 for r in results if r.get('placeholders_found'))

    return {
        'guides': results,
        'total': len(results),
        'critical_count': critical,
        'warning_count': warning,
        'healthy_count': healthy,
        'placeholder_count': placeholders,
        'fetched_at': datetime.datetime.utcnow().isoformat() + 'Z',
    }
