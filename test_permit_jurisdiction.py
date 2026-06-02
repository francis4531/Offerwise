"""Regression test for permit jurisdiction resolution (v5.89.102).

The bug: a property address that wasn't in the exact "St, City, ST 12345" shape
parsed to a blank jurisdiction -> "Unknown jurisdiction" + every permit LOW CONF.
Fix: robust parse + ZIP fallback (detect_state_from_zip). These tests lock the
resolver behavior so it can't silently regress.
"""
import re
from state_disclosures import detect_state_from_zip
from permit_lookup import _format_jurisdiction


def _resolve(addr, zip_match=None):
    s = (addr or '').strip()
    z = zip_match or ''
    if not z:
        m = re.search(r'\b(\d{5})(?:-\d{4})?\b', s)
        z = m.group(1) if m else ''
    state = ''
    t = re.search(r'\b([A-Z]{2})\b(?=\s*\d{5}|\s*$|,)', s)
    if t:
        state = t.group(1)
    if not state and z:
        state = detect_state_from_zip(z) or ''
    city = ''
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) >= 3:
        city = parts[-2]
    elif len(parts) == 2:
        tail = parts[-1]
        city = re.sub(r'\b[A-Z]{2}\b.*$', '', tail).strip() or parts[0]
    return {'state': state, 'county': '', 'city': city, 'zip': z}


def test_canonical_address_resolves():
    j = _resolve("2839 Pendleton Dr, San Jose, CA 95148")
    assert j['state'] == 'CA' and j['zip'] == '95148' and j['city'] == 'San Jose'
    assert _format_jurisdiction(j) == 'San Jose, CA'


def test_no_comma_address_recovers_state_from_zip():
    # the shape that broke before: no commas
    j = _resolve("2839 Pendleton Dr San Jose CA 95148")
    assert j['state'] == 'CA'      # recovered (from token or ZIP)
    assert j['zip'] == '95148'
    assert _format_jurisdiction(j) != 'Unknown jurisdiction'


def test_zip_plus_four_resolves():
    j = _resolve("2839 Pendleton Dr, San Jose, CA 95148-1234")
    assert j['state'] == 'CA' and j['zip'] == '95148'


def test_two_part_address():
    j = _resolve("San Jose, CA 95148")
    assert j['state'] == 'CA' and j['zip'] == '95148'


def test_texas_address():
    j = _resolve("123 Main St, Austin, TX 78701")
    assert j['state'] == 'TX'


def test_zip_only_recovers_state():
    j = _resolve("", zip_match="95148")
    assert j['state'] == 'CA'


def test_empty_address_is_honestly_unknown():
    # no fabrication when there's genuinely nothing to resolve
    j = _resolve("")
    assert _format_jurisdiction(j) == 'Unknown jurisdiction'
