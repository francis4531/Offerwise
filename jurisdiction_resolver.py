"""
Shared jurisdiction + property-type resolver — the single source of truth.

Before this module, the codebase inferred a property's jurisdiction three
different ways: the robust national path in the permit flow
(detect_state_from_zip -> ZIP3_TO_STATE, 51 states / 896 prefixes), a weaker
regex in the reasoning shadow, and a bare hardcoded 'CA' in the buyer-facing
report path. That is exactly the kind of drift the single-source-of-truth
principle exists to prevent, and it is why a Texas property could be scored
against California's checklist.

This module unifies all three into one resolver, reusing the existing national
ZIP table (no duplicate data), and adds the parallel property-type resolution
the checklist genuinely needs (compose('*','SFH') resolves 57 items;
compose('*','condo') resolves 19 — property type reshapes the checklist, so it
must be derived, not assumed).

Design principles:
  * Derive, never assume. State comes from the address tail, then the ZIP, then
    the document text, and only then falls back to the national base ('*') — we
    never guess a specific state.
  * National-safe. compose() applies a state overlay only where one is authored
    (today: CA), so any unresolved or overlay-less state correctly resolves to
    the national floor. Returning '*' is honest, not lossy.
  * Municipal depth without a hardcoded city list. The municipal index is built
    from whatever overlay keys the checklist actually authors, so San Jose gets
    its overlay because the asset declares 'CA:santa_clara:san_jose', not because
    a city name is baked in here.

Pure and side-effect-free. Safe to import anywhere. Never raises.
"""
from __future__ import annotations

import re
from typing import Optional

# Reuse the national resolvers — do NOT reimplement the ZIP table here.
try:
    from state_disclosures import detect_state_from_zip, detect_state_from_text
except Exception:  # pragma: no cover - import guard
    def detect_state_from_zip(_z):  # type: ignore
        return None

    def detect_state_from_text(_t):  # type: ignore
        return None


NATIONAL_BASE = "*"

# Address tail: ", ST 12345" or ", ST" — tolerant of case and spacing. We keep it
# permissive on the read side and uppercase the capture, so "san jose, ca 95148"
# resolves the same as "San Jose, CA 95148".
_STATE_TAIL_RE = re.compile(r",\s*([A-Za-z]{2})\s*(?:\d{5}(?:-\d{4})?)?\s*$")
_STATE_ANY_RE = re.compile(r",\s*([A-Za-z]{2})\b")
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")

# Property-type normalization. Maps the many vendor spellings (RentCast, MLS,
# county records) onto the checklist's controlled vocabulary
# ("SFH" | "condo" | "townhouse" | ...). Extend as new authored types appear.
_PROPERTY_TYPE_MAP = {
    "single family": "SFH",
    "single-family": "SFH",
    "single family residence": "SFH",
    "single family residential": "SFH",
    "singlefamily": "SFH",
    "sfr": "SFH",
    "sfh": "SFH",
    "detached": "SFH",
    "house": "SFH",
    "condo": "condo",
    "condominium": "condo",
    "apartment": "condo",
    "coop": "condo",
    "co-op": "condo",
    "stock cooperative": "condo",
    "townhouse": "townhouse",
    "townhome": "townhouse",
    "town house": "townhouse",
    "rowhouse": "townhouse",
    "row house": "townhouse",
    "attached": "townhouse",
}

# When the type is genuinely unknown, default to the broad floor. SFH resolves
# the widest applicable set (57 of 58 national-base items) and its extra items
# (e.g. crawlspace) simply resolve to "no finding" on a property they don't apply
# to — over-inclusion is noise, whereas under-inclusion would MISS risks. This is
# a documented conservative fallback, not a jurisdiction/type assumption: any
# time a real type is available it is used instead.
_UNKNOWN_TYPE_DEFAULT = "SFH"


def _muni_index():
    """Build {normalized_city: full_muni_key} from the authored municipal
    overlays, so a bare city name (all we usually have from an address) can be
    matched to a full 'STATE:county:city' overlay key without a hardcoded list.
    Cached on the module after first build."""
    global _MUNI_INDEX_CACHE
    if _MUNI_INDEX_CACHE is not None:
        return _MUNI_INDEX_CACHE
    idx = {}
    try:
        from reasoning.checklist_loader import load_checklist
        asset = load_checklist()
        for key in asset.municipal_overlays.keys():
            parts = key.split(":")
            if len(parts) >= 3:
                state = parts[0].upper()
                city = parts[-1].replace("_", " ").strip().lower()
                idx[(state, city)] = key
    except Exception:
        idx = {}
    _MUNI_INDEX_CACHE = idx
    return idx


_MUNI_INDEX_CACHE = None


def resolve_state(address: Optional[str] = None,
                  zip_code: Optional[str] = None,
                  document_text: Optional[str] = None) -> str:
    """Resolve the 2-letter state, or the national base ('*') if it can't be
    determined. Order of trust: explicit address tail -> ZIP (from arg or parsed
    from the address) -> document form markers -> national base. Never a guess."""
    # 1) address tail (", ST 12345" or ", ST")
    if address:
        m = _STATE_TAIL_RE.search(address) or _STATE_ANY_RE.search(address)
        if m:
            st = (m.group(1) or "").upper()
            if st:
                return st

    # 2) ZIP -> state, from the passed zip or one parsed out of the address
    z = (zip_code or "").strip()
    if not z and address:
        zm = _ZIP_RE.search(address)
        if zm:
            z = zm.group(1)
    if z:
        st = detect_state_from_zip(z)
        if st:
            return st.upper()

    # 3) document form markers (TDS/TREC/Form 17/...) as a last positive signal
    if document_text:
        st = detect_state_from_text(document_text)
        if st:
            return st.upper()

    # 4) honest national floor — never a guessed state
    return NATIONAL_BASE


def resolve_property_type(profile_type: Optional[str] = None,
                          *_ignored, **_ignored_kw) -> str:
    """Normalize a vendor/profile property-type string onto the checklist
    vocabulary. Falls back to the documented broad floor when unknown."""
    if profile_type:
        key = re.sub(r"\s+", " ", str(profile_type).strip().lower())
        if key in _PROPERTY_TYPE_MAP:
            return _PROPERTY_TYPE_MAP[key]
        # substring tolerance for compound vendor strings
        for frag, norm in _PROPERTY_TYPE_MAP.items():
            if frag in key:
                return norm
    return _UNKNOWN_TYPE_DEFAULT


def resolve_report_jurisdiction(result_dict, address="", document_text=None):
    """Resolve everything the buyer report's reasoning attach needs —
    (jurisdiction path, property type, zip, year_built) — from the research
    profile (authoritative: state/county/city/type/zip/year from RentCast) with
    address fallback. This is the single source for the analysis wiring, so that
    glue is unit-testable rather than buried inline in the analyze route.

    Returns a dict: {jurisdiction, property_type, zip_code, year_built}. Never
    raises; missing profile -> address/national fallback.
    """
    result_dict = result_dict or {}
    profile = (result_dict.get("research_data") or {}).get("profile") or {}
    zip_code = str(profile.get("zip_code") or "").strip()[:5]
    if not zip_code and address:
        m = _ZIP_RE.search(address)
        zip_code = m.group(1) if m else ""
    jur_path = resolve_jurisdiction_path(
        address=address, zip_code=zip_code,
        city=profile.get("city"), state=profile.get("state"),
        document_text=document_text,
    )
    return {
        "jurisdiction": jur_path,
        "property_type": resolve_property_type(profile.get("property_type")),
        "zip_code": zip_code,
        "year_built": profile.get("year_built") or result_dict.get("year_built"),
    }


def resolve_jurisdiction_path(address: Optional[str] = None,
                              zip_code: Optional[str] = None,
                              city: Optional[str] = None,
                              state: Optional[str] = None,
                              document_text: Optional[str] = None) -> str:
    """Resolve the full jurisdiction path compose() consumes:
    '*' | 'CA' | 'CA:county:city'. Adds the municipal segment only when a known
    authored municipal overlay matches the (state, city) — otherwise returns the
    bare state (or national base). The municipal match is driven by the authored
    overlay keys, not a hardcoded city list.

    When an authoritative `state` (e.g. from property research) is supplied it is
    trusted directly; otherwise the state is resolved from address/zip/text."""
    state = (state or "").strip().upper() or resolve_state(
        address=address, zip_code=zip_code, document_text=document_text)
    if not state or state == NATIONAL_BASE:
        return NATIONAL_BASE

    # derive the city if not supplied: the comma-segment before the state/zip tail
    if not city and address:
        parts = [p.strip() for p in address.split(",") if p.strip()]
        if len(parts) >= 3:
            city = parts[-2]
        elif len(parts) == 2:
            tail = parts[-1]
            city = re.sub(r"\b[A-Za-z]{2}\b.*$", "", tail).strip() or None

    if city:
        key = _muni_index().get((state, city.strip().lower()))
        if key:
            return key
    return state
