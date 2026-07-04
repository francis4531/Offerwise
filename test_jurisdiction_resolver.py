"""
test_jurisdiction_resolver.py — the single source of truth for turning a
property into (jurisdiction_path, property_type).

These lock the national-correctness contract: the resolver must derive the state
from real signals and fall back to the national base ('*') — never a guessed
state — and must resolve property type onto the checklist vocabulary rather than
assuming SFH. Regression guard against the CA/SFH hardcodes this work removed.
"""

from jurisdiction_resolver import (
    resolve_state,
    resolve_property_type,
    resolve_jurisdiction_path,
    NATIONAL_BASE,
)
from reasoning.composition import all_authored_ids, compose


def test_state_from_address_tail_case_insensitive():
    assert resolve_state(address="2839 Pendleton Dr, San Jose, CA 95148") == "CA"
    assert resolve_state(address="100 Main St, Austin, TX 78701") == "TX"
    assert resolve_state(address="456 Beach Blvd, Miami, FL 33139") == "FL"
    # lowercase must resolve identically (real user-entered addresses)
    assert resolve_state(address="san jose, ca 95148") == "CA"


def test_state_falls_back_to_zip_then_national_base():
    # ZIP inside the address, no ", ST" tail
    assert resolve_state(address="789 Nowhere Rd 95148") == "CA"
    # ZIP passed explicitly
    assert resolve_state(zip_code="75001") == "TX"
    # nothing resolvable -> national base, never a guessed state
    assert resolve_state(address="789 Nowhere Rd") == NATIONAL_BASE
    assert resolve_state() == NATIONAL_BASE


def test_property_type_normalizes_to_checklist_vocabulary():
    for raw in ("Single Family", "Single-Family Residence", "SFR", "Detached", "house"):
        assert resolve_property_type(raw) == "SFH"
    for raw in ("Condominium", "Condo", "Co-op", "stock cooperative"):
        assert resolve_property_type(raw) == "condo"
    for raw in ("Townhouse", "Townhome", "Row House"):
        assert resolve_property_type(raw) == "townhouse"
    # unknown/empty -> documented broad floor (SFH), never a crash
    assert resolve_property_type(None) == "SFH"
    assert resolve_property_type("") == "SFH"
    assert resolve_property_type("some vendor gobbledygook") == "SFH"


def test_property_type_actually_reshapes_the_checklist():
    # condo resolves a materially different (smaller) checklist than SFH — proving
    # the type is a real filter axis, so hardcoding SFH for a condo is a real bug.
    sfh = set(compose("*", "SFH").ids())
    condo = set(compose("*", "condo").ids())
    assert condo != sfh
    assert len(condo) < len(sfh)


def test_jurisdiction_path_adds_municipal_depth_from_authored_keys():
    # San Jose has an authored municipal overlay; it must be reached from a bare
    # city name (no hardcoded city list — derived from the asset's overlay keys).
    assert resolve_jurisdiction_path(
        address="2839 Pendleton Dr, San Jose, CA 95148"
    ) == "CA:santa_clara:san_jose"
    # authoritative structured state+city (as from a research profile)
    assert resolve_jurisdiction_path(state="CA", city="San Jose") == "CA:santa_clara:san_jose"
    # a CA city with no authored municipal overlay resolves to the bare state
    assert resolve_jurisdiction_path(state="CA", city="Fresno") == "CA"
    # non-CA resolves to the bare state (no overlay authored) ...
    assert resolve_jurisdiction_path(address="1 Main St, Austin, TX 78701") == "TX"
    # ... and an unresolvable address to the national base
    assert resolve_jurisdiction_path(address="nowhere at all") == NATIONAL_BASE


def test_all_authored_ids_is_a_superset_of_every_jurisdiction_and_type():
    # The extraction universe must contain every id any composed checklist needs,
    # so context-free extraction (the PDF worker) never withholds an id.
    universe = set(all_authored_ids())
    for jur, typ in [
        ("*", "SFH"), ("CA", "SFH"), ("CA:santa_clara:san_jose", "SFH"),
        ("*", "condo"), ("TX", "SFH"), ("FL", "townhouse"),
    ]:
        assert set(compose(jur, typ).ids()) <= universe, f"{jur}/{typ} not covered"
