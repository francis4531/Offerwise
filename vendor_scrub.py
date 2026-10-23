"""
vendor_scrub.py — v5.89.347

Commercial data vendors must never appear in anything a user can see: rendered
pages, PDF text, or the JSON the report is built from (visible in devtools and in
shared-report payloads). Government sources (FEMA, USGS, EPA, Census) are public
data and stay attributed.

Three jobs:
  scrub_text(s)    — replace vendor names in a string with neutral wording.
  scrub_result(d)  — walk an analysis result: rename the valuation tool, drop
                     vendor URLs, neutralize vendor names in every string,
                     drop rows and fields from retired integrations.

Applied at every point an analysis leaves the server: fresh /api/analyze
responses, the stored Analysis row, cache writes, /api/analysis/<id>, and the
shared-report view (so analyses saved by older builds are cleaned on the way out).
"""
import re

# vendor pattern -> neutral replacement. Case-insensitive, word-bounded.
_REPLACEMENTS = [
    (r"\bRent\s?Cast(?:\.io)?\b", "our valuation provider"),
    (r"\bapp\.rentcast\.io\S*", "the provider console"),
    (r"\bapi\.rentcast\.io\b", "the valuation API"),
    (r"\bGreat\s?Schools(?:\.org)?\b", "school ratings"),
    (r"\bRSMeans\b", "industry cost data"),
    (r"\bHome\s?Advisor\b", "industry cost data"),
    (r"\bHome\s?Guide(?:\.com)?\b", "industry cost data"),
    (r"\bAngi\b", "industry cost data"),
    (r"\bAnthropic\b", "our AI provider"),
    (r"\bClaude(?:\s+(?:Opus|Sonnet|Haiku)(?:\s+[\d.]+)?)?\b", "our AI model"),
    (r"\bGPT-?\d(?:\.\d)?\b", "a leading model"),
    (r"\bOpenAI\b", "a model vendor"),
    (r"\bResend\b(?=.*(?:email|API))", "our email service"),
    (r"\bSentry\b", "error monitoring"),
    (r"\bTesseract\b|\bPaddleOCR\b|\bGoogle\s+Vision\b", "OCR"),
    (r"\bOpen\s?Street\s?Map\b|\bOverpass\b", "open map data"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in _REPLACEMENTS]

# "industry cost data + industry cost data" after two replacements reads badly.
_DEDUP = re.compile(r"(industry cost data)(?:\s*(?:,|\+|and|/)\s*industry cost data)+", re.IGNORECASE)

_VENDOR_HOSTS = ('rentcast.io', 'greatschools.org', 'anthropic.com',
                 'openai.com', 'resend.com', 'sentry.io', 'homeadvisor.com', 'angi.com',
                 'homeguide.com', 'rsmeans.com')

_TOOL_RENAMES = {'rentcast': 'valuation', 'redfin': 'listings'}

# v5.89.347: integrations we no longer run. Analyses stored by older builds still
# carry their tool rows and profile fields; those are dropped on the way out
# rather than renamed, so the retired vendor never reaches a user or a payload.
_RETIRED_TOOLS = ('walk_score',)
_RETIRED_KEYS = ('walk_score', 'transit_score', 'bike_score', 'walk_description')


def scrub_text(s):
    if not isinstance(s, str) or not s:
        return s
    out = s
    for pat, rep in _COMPILED:
        out = pat.sub(rep, out)
    return _DEDUP.sub(r"\1", out)


def _is_vendor_url(v):
    return isinstance(v, str) and any(h in v.lower() for h in _VENDOR_HOSTS)


def scrub_result(obj):
    """Return a scrubbed deep copy of an analysis result (dict/list/str tree)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _RETIRED_KEYS:
                continue
            if k == 'source_url' and _is_vendor_url(v):
                continue
            if k == 'tool_name' and isinstance(v, str) and v.lower() in _TOOL_RENAMES:
                out[k] = _TOOL_RENAMES[v.lower()]
                continue
            if k in ('source', 'methodology', 'error', 'consequences', 'description',
                     'rationale', 'market_rationale', 'market_discount_basis', 'explanation',
                     'reasoning', 'observable_indicators', 'summary', 'title', 'detail',
                     'finding', 'evidence', 'recommendation', 'sources'):
                out[k] = scrub_result(v)
                continue
            out[k] = scrub_result(v)
        return out
    if isinstance(obj, list):
        return [scrub_result(x) for x in obj
                if not (isinstance(x, dict) and str(x.get('tool_name', '')).lower() in _RETIRED_TOOLS)]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj
