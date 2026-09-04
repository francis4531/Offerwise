"""
address_utils.py — v5.89.339

One place to pull a ZIP code out of a free-form US address.

Why this exists: the analysis path had ~15 copies of `re.search(r'\b(\d{5})\b',
address)`, which returns the FIRST five-digit token. For "13180 Edgemont Ln, Frisco,
Texas 75035" that is the HOUSE NUMBER. 13180 is a Syracuse NY ZIP prefix, so the
report priced repairs at "Syracuse rates", resolved permits for "Frisco, NY",
attached NY disclosure law, and asked RentCast for market stats on ZIP 13180.
Any address whose street number has five digits (most of the Sun Belt) hit this.

Rule: the ZIP is the LAST 5-digit (or 5+4) token, and it must not be the leading
street number. A single leading 5-digit token with nothing after it is not a ZIP.
"""
import re

_ZIP_TOKEN = re.compile(r'(?<![\d-])(\d{5})(?:-\d{4})?(?!\d)')
_LEADING_NUMBER = re.compile(r'^\s*(\d{1,6})\b')


def extract_zip(address) -> str:
    """Return the 5-digit ZIP in `address`, or '' when there is none.

    >>> extract_zip("13180 Edgemont Ln, Frisco, Texas 75035")
    '75035'
    >>> extract_zip("13180 Edgemont Ln, Frisco, Texas")
    ''
    >>> extract_zip("2839 Pendleton Dr, San Jose, CA 95148-1234")
    '95148'
    >>> extract_zip("95148")
    '95148'
    """
    if not address:
        return ''
    s = str(address)
    matches = list(_ZIP_TOKEN.finditer(s))
    if not matches:
        return ''
    lead = _LEADING_NUMBER.match(s)
    lead_span = lead.span(1) if lead else None
    # Walk from the end: the last 5-digit token that is not the leading street number.
    for m in reversed(matches):
        if lead_span and m.span(1) == lead_span:
            # The leading number is only a ZIP if it is the whole address (bare ZIP input).
            if s.strip() == m.group(1) or s.strip() == m.group(0):
                return m.group(1)
            continue
        return m.group(1)
    return ''


def extract_state(address) -> str:
    """Two-letter state from an address tail (", TX 75035" / ", tx"), else ''.
    Full state names are resolved by callers via ZIP -> state."""
    if not address:
        return ''
    m = re.search(r',\s*([A-Za-z]{2})\s*(?:\d{5}(?:-\d{4})?)?\s*$', str(address))
    if m:
        return m.group(1).upper()
    m = re.search(r'\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b', str(address))
    return m.group(1) if m else ''
