"""Unit tests for permit_lookup — v5.87.83.

Tests cover the deterministic parts of the permit lookup pipeline:
  - Prompt construction and jurisdiction key formatting
  - JSON parsing happy path + degraded paths
  - Cache hit/miss splitting
  - Normalization edge cases
  - Fallback when Claude call fails
  - System key collision handling
  - The verify_locally callout logic

The Anthropic API call itself is mocked — we don't validate Claude's
output quality here, just that we handle its responses correctly.
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from permit_lookup import (
    lookup_permits,
    _system_key,
    _jurisdiction_key,
    _format_jurisdiction,
    _empty_result,
    _uncertain_fallback,
)


# ---------------------------------------------------------------------------
# Helper: standard jurisdiction
# ---------------------------------------------------------------------------

SAN_JOSE = {
    'state': 'CA',
    'county': 'Santa Clara',
    'city': 'San Jose',
    'zip': '95126',
}


# ---------------------------------------------------------------------------
# _system_key — stable cache keys for repair categories
# ---------------------------------------------------------------------------

def test_system_key_basic():
    assert _system_key({'category': 'HVAC'}) == 'hvac'


def test_system_key_handles_dict_category():
    """Some places in the codebase pass category as {value: ..., label: ...}."""
    assert _system_key({'category': {'value': 'plumbing', 'label': 'Plumbing'}}) == 'plumbing'


def test_system_key_normalizes_whitespace_and_punctuation():
    assert _system_key({'category': 'HVAC · Furnace'}) == 'hvac_furnace'


def test_system_key_collapses_multiple_separators():
    assert _system_key({'category': 'Roof - Tile / Asphalt'}) == 'roof_tile_asphalt'


def test_system_key_falls_back_to_system_field():
    """If 'category' is missing but 'system' is present, use that."""
    assert _system_key({'system': 'Electrical'}) == 'electrical'


def test_system_key_returns_unknown_for_empty():
    assert _system_key({}) == 'unknown'
    assert _system_key({'category': ''}) == 'unknown'
    assert _system_key({'category': None}) == 'unknown'


def test_system_key_strips_leading_trailing_underscores():
    """Normalization shouldn't emit underscores at the boundary."""
    assert _system_key({'category': ' HVAC '}) == 'hvac'
    assert _system_key({'category': '... HVAC ...'}) == 'hvac'


# ---------------------------------------------------------------------------
# _jurisdiction_key — stable cache key for jurisdictions
# ---------------------------------------------------------------------------

def test_jurisdiction_key_full():
    assert _jurisdiction_key(SAN_JOSE) == 'CA:santa_clara:san_jose'


def test_jurisdiction_key_state_only():
    assert _jurisdiction_key({'state': 'CA'}) == 'CA'


def test_jurisdiction_key_state_and_city_no_county():
    """Some jurisdictions don't pass through with a county. Should still key cleanly."""
    assert _jurisdiction_key({'state': 'TX', 'city': 'Austin'}) == 'TX:austin'


def test_jurisdiction_key_handles_missing_state():
    assert _jurisdiction_key({'city': 'San Jose'}) == 'san_jose'


def test_jurisdiction_key_lowercases_county_and_city_uppercases_state():
    """State is canonical 2-letter uppercase; city/county are lowercased."""
    j = {'state': 'ca', 'county': 'SANTA CLARA', 'city': 'San JOSE'}
    assert _jurisdiction_key(j) == 'CA:santa_clara:san_jose'


def test_jurisdiction_key_empty_input():
    assert _jurisdiction_key({}) == ''


# ---------------------------------------------------------------------------
# _format_jurisdiction — human-readable label
# ---------------------------------------------------------------------------

def test_format_jurisdiction_full():
    assert _format_jurisdiction(SAN_JOSE) == 'San Jose, CA · Santa Clara County'


def test_format_jurisdiction_no_county():
    assert _format_jurisdiction({'state': 'TX', 'city': 'Austin'}) == 'Austin, TX'


def test_format_jurisdiction_state_only():
    assert _format_jurisdiction({'state': 'CA'}) == 'CA'


def test_format_jurisdiction_unknown():
    assert _format_jurisdiction({}) == 'Unknown jurisdiction'


# ---------------------------------------------------------------------------
# _empty_result and _uncertain_fallback — defensive paths
# ---------------------------------------------------------------------------

def test_empty_result_shape():
    """Verifies the response shape when no repairs are passed in."""
    r = _empty_result(SAN_JOSE)
    assert r['findings'] == []
    assert r['total_low'] == 0
    assert r['total_high'] == 0
    assert r['jurisdiction_label'] == 'San Jose, CA · Santa Clara County'
    assert r['cache_hits'] == 0
    assert r['cache_misses'] == 0


def test_uncertain_fallback_includes_verify_locally():
    """When the LLM fails entirely, we degrade to 'uncertain' with a verify callout."""
    f = _uncertain_fallback({'category': 'HVAC · Furnace'}, 'San Jose, CA')
    assert f['permit_required'] == 'uncertain'
    assert f['confidence'] == 'low'
    assert f['permit_cost_low'] is None
    assert f['permit_cost_high'] is None
    assert 'San Jose' in f['consequences']
    assert f['verify_locally'] is not None
    assert 'department' in f['verify_locally']['dept'].lower()


# ---------------------------------------------------------------------------
# lookup_permits — empty input
# ---------------------------------------------------------------------------

def test_lookup_permits_empty_breakdown():
    """Empty repair_breakdown returns the empty result without making API calls."""
    r = lookup_permits([], SAN_JOSE)
    assert r['findings'] == []
    assert r['total_low'] == 0
    assert r['total_high'] == 0


# ---------------------------------------------------------------------------
# lookup_permits — full happy path with mocked Claude
# ---------------------------------------------------------------------------

def _make_claude_response(findings_payload):
    """Build a mocked Anthropic response object that returns the given JSON."""
    response = MagicMock()
    response.content = [MagicMock()]
    response.content[0].text = json.dumps(findings_payload)
    return response


@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_happy_path(mock_get_client, mock_cache_set, mock_cache_get):
    """All cache misses, Claude returns valid JSON for every category."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_claude_response([
        {
            'system_key': 'hvac_furnace',
            'system': 'HVAC · Furnace',
            'permit_required': 'required',
            'confidence': 'high',
            'permit_cost_low': 320,
            'permit_cost_high': 480,
            'consequences': 'CO safety inspection bypassed.',
            'verify_locally_dept': None,
        },
        {
            'system_key': 'cosmetic_paint',
            'system': 'Cosmetic · Paint',
            'permit_required': 'not_required',
            'confidence': 'high',
            'permit_cost_low': None,
            'permit_cost_high': None,
            'consequences': 'Cosmetic interior work is exempt.',
            'verify_locally_dept': None,
        },
    ])
    mock_get_client.return_value = mock_client

    breakdown = [
        {'category': 'HVAC · Furnace', 'estimated_cost_low': 5800, 'estimated_cost_high': 9200},
        {'category': 'Cosmetic · Paint', 'estimated_cost_low': 800, 'estimated_cost_high': 1400},
    ]
    r = lookup_permits(breakdown, SAN_JOSE)

    assert len(r['findings']) == 2
    # Total only counts required/likely findings
    assert r['total_low'] == 320
    assert r['total_high'] == 480
    assert r['cache_misses'] == 2
    assert r['cache_hits'] == 0
    # Ordering matches input order
    assert r['findings'][0]['system_key'] == 'hvac_furnace'
    assert r['findings'][1]['system_key'] == 'cosmetic_paint'
    # cache_set was called for each new finding
    assert mock_cache_set.call_count == 2


# ---------------------------------------------------------------------------
# lookup_permits — fully cached
# ---------------------------------------------------------------------------

@patch('permit_lookup._cache_get')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_full_cache_hit(mock_get_client, mock_cache_get):
    """All findings cached. No Claude call. Cost: zero."""
    cached_finding = {
        'system_key': 'hvac_furnace',
        'system': 'HVAC · Furnace',
        'permit_required': 'required',
        'confidence': 'high',
        'permit_cost_low': 320,
        'permit_cost_high': 480,
        'consequences': 'cached',
        'verify_locally': None,
    }
    mock_cache_get.return_value = cached_finding

    breakdown = [
        {'category': 'HVAC · Furnace', 'estimated_cost_low': 5800, 'estimated_cost_high': 9200},
    ]
    r = lookup_permits(breakdown, SAN_JOSE)

    assert r['cache_hits'] == 1
    assert r['cache_misses'] == 0
    # Claude was never called
    mock_get_client.assert_not_called()
    assert r['findings'][0]['consequences'] == 'cached'


# ---------------------------------------------------------------------------
# lookup_permits — partial cache (mix of hits and misses)
# ---------------------------------------------------------------------------

@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_partial_cache(mock_get_client, mock_cache_set):
    """First repair is cached, second isn't. Only one Claude call for the miss."""
    def cache_get_side_effect(juris_key, system_key):
        if system_key == 'hvac_furnace':
            return {
                'system_key': 'hvac_furnace',
                'system': 'HVAC · Furnace',
                'permit_required': 'required',
                'confidence': 'high',
                'permit_cost_low': 320,
                'permit_cost_high': 480,
                'consequences': 'cached',
                'verify_locally': None,
            }
        return None

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_claude_response([
        {
            'system_key': 'electrical_panel',
            'system': 'Electrical · Panel',
            'permit_required': 'required',
            'confidence': 'high',
            'permit_cost_low': 420,
            'permit_cost_high': 650,
            'consequences': 'fresh from claude',
            'verify_locally_dept': None,
        },
    ])
    mock_get_client.return_value = mock_client

    with patch('permit_lookup._cache_get', side_effect=cache_get_side_effect):
        breakdown = [
            {'category': 'HVAC · Furnace', 'estimated_cost_low': 5800, 'estimated_cost_high': 9200},
            {'category': 'Electrical · Panel', 'estimated_cost_low': 3200, 'estimated_cost_high': 5800},
        ]
        r = lookup_permits(breakdown, SAN_JOSE)

    assert r['cache_hits'] == 1
    assert r['cache_misses'] == 1
    assert mock_client.messages.create.call_count == 1  # one batched call for misses
    assert r['total_low'] == 320 + 420
    assert r['total_high'] == 480 + 650


# ---------------------------------------------------------------------------
# lookup_permits — Claude returns malformed JSON
# ---------------------------------------------------------------------------

@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_malformed_json(mock_get_client, mock_cache_set, mock_cache_get):
    """Malformed JSON → fallback to uncertain findings, not crash."""
    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock()]
    response.content[0].text = 'this is not valid json {{{'
    mock_client.messages.create.return_value = response
    mock_get_client.return_value = mock_client

    breakdown = [{'category': 'HVAC', 'estimated_cost_low': 5000, 'estimated_cost_high': 9000}]
    r = lookup_permits(breakdown, SAN_JOSE)

    # Should fall back gracefully — one uncertain finding, not crash
    assert len(r['findings']) == 1
    assert r['findings'][0]['permit_required'] == 'uncertain'
    assert r['findings'][0]['confidence'] == 'low'


# ---------------------------------------------------------------------------
# lookup_permits — Claude returns JSON-fenced response
# ---------------------------------------------------------------------------

@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_strips_code_fences(mock_get_client, mock_cache_set, mock_cache_get):
    """Claude wrapped its output in ```json ... ``` — we should strip cleanly."""
    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock()]
    response.content[0].text = '```json\n[{"system_key":"hvac","system":"HVAC","permit_required":"required","confidence":"high","permit_cost_low":300,"permit_cost_high":500,"consequences":"x"}]\n```'
    mock_client.messages.create.return_value = response
    mock_get_client.return_value = mock_client

    breakdown = [{'category': 'HVAC', 'estimated_cost_low': 5000, 'estimated_cost_high': 9000}]
    r = lookup_permits(breakdown, SAN_JOSE)

    assert len(r['findings']) == 1
    assert r['findings'][0]['system_key'] == 'hvac'
    assert r['findings'][0]['permit_required'] == 'required'


# ---------------------------------------------------------------------------
# lookup_permits — Claude returns a non-array (single object)
# ---------------------------------------------------------------------------

@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_non_array_response(mock_get_client, mock_cache_set, mock_cache_get):
    """Claude returned an object instead of an array → fall back gracefully."""
    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock()]
    response.content[0].text = '{"system_key":"hvac","permit_required":"required"}'
    mock_client.messages.create.return_value = response
    mock_get_client.return_value = mock_client

    breakdown = [{'category': 'HVAC', 'estimated_cost_low': 5000, 'estimated_cost_high': 9000}]
    r = lookup_permits(breakdown, SAN_JOSE)

    assert len(r['findings']) == 1
    assert r['findings'][0]['permit_required'] == 'uncertain'


# ---------------------------------------------------------------------------
# lookup_permits — Claude client unavailable
# ---------------------------------------------------------------------------

@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client', return_value=None)
def test_lookup_permits_no_client(mock_get_client, mock_cache_set, mock_cache_get):
    """No Anthropic client → fall back to uncertain findings."""
    breakdown = [{'category': 'HVAC', 'estimated_cost_low': 5000, 'estimated_cost_high': 9000}]
    r = lookup_permits(breakdown, SAN_JOSE)

    assert len(r['findings']) == 1
    assert r['findings'][0]['permit_required'] == 'uncertain'


# ---------------------------------------------------------------------------
# lookup_permits — verify_locally callout for low-confidence findings
# ---------------------------------------------------------------------------

@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_verify_locally_for_low_confidence(mock_get_client, mock_cache_set, mock_cache_get):
    """Low-confidence finding with a dept name → verify_locally object populated."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_claude_response([
        {
            'system_key': 'insulation',
            'system': 'Insulation',
            'permit_required': 'uncertain',
            'confidence': 'low',
            'permit_cost_low': None,
            'permit_cost_high': None,
            'consequences': 'depends',
            'verify_locally_dept': 'San Jose Building Department',
        },
    ])
    mock_get_client.return_value = mock_client

    breakdown = [{'category': 'Insulation', 'estimated_cost_low': 1400, 'estimated_cost_high': 2200}]
    r = lookup_permits(breakdown, SAN_JOSE)

    assert r['findings'][0]['verify_locally'] is not None
    assert r['findings'][0]['verify_locally']['dept'] == 'San Jose Building Department'


@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_no_verify_locally_for_high_confidence(mock_get_client, mock_cache_set, mock_cache_get):
    """High-confidence findings should NOT get a verify_locally callout."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_claude_response([
        {
            'system_key': 'hvac',
            'system': 'HVAC',
            'permit_required': 'required',
            'confidence': 'high',
            'permit_cost_low': 300,
            'permit_cost_high': 500,
            'consequences': 'standard',
            'verify_locally_dept': None,
        },
    ])
    mock_get_client.return_value = mock_client

    breakdown = [{'category': 'HVAC', 'estimated_cost_low': 5000, 'estimated_cost_high': 9000}]
    r = lookup_permits(breakdown, SAN_JOSE)

    assert r['findings'][0]['verify_locally'] is None


# ---------------------------------------------------------------------------
# lookup_permits — total only counts required/likely
# ---------------------------------------------------------------------------

@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_total_excludes_not_required(mock_get_client, mock_cache_set, mock_cache_get):
    """Permit total should not include not_required or uncertain findings."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_claude_response([
        {'system_key': 'a', 'system': 'A', 'permit_required': 'required',
         'confidence': 'high', 'permit_cost_low': 300, 'permit_cost_high': 500,
         'consequences': 'x'},
        {'system_key': 'b', 'system': 'B', 'permit_required': 'likely',
         'confidence': 'medium', 'permit_cost_low': 200, 'permit_cost_high': 400,
         'consequences': 'x'},
        {'system_key': 'c', 'system': 'C', 'permit_required': 'not_required',
         'confidence': 'high', 'permit_cost_low': None, 'permit_cost_high': None,
         'consequences': 'exempt'},
        {'system_key': 'd', 'system': 'D', 'permit_required': 'uncertain',
         'confidence': 'low', 'permit_cost_low': 999, 'permit_cost_high': 999,
         'consequences': 'unclear'},
    ])
    mock_get_client.return_value = mock_client

    breakdown = [
        {'category': 'A', 'estimated_cost_low': 1, 'estimated_cost_high': 2},
        {'category': 'B', 'estimated_cost_low': 1, 'estimated_cost_high': 2},
        {'category': 'C', 'estimated_cost_low': 1, 'estimated_cost_high': 2},
        {'category': 'D', 'estimated_cost_low': 1, 'estimated_cost_high': 2},
    ]
    r = lookup_permits(breakdown, SAN_JOSE)

    # Only 'required' (a) + 'likely' (b) count toward the total
    assert r['total_low'] == 300 + 200
    assert r['total_high'] == 500 + 400


# ---------------------------------------------------------------------------
# v5.87.83: Web-search fee enrichment
# ---------------------------------------------------------------------------

from permit_lookup import _enrich_finding_with_web_fee


def _make_web_search_response(parsed_json):
    """Build a mocked web-search-tool response. The final text block
    contains the JSON payload (after Claude's tool_use blocks)."""
    response = MagicMock()
    text_block = MagicMock()
    text_block.type = 'text'
    text_block.text = json.dumps(parsed_json)
    response.content = [text_block]
    return response


@patch('permit_lookup.get_anthropic_client')
def test_enrich_finding_skips_when_no_client(mock_get_client):
    """No Anthropic client → finding returned unchanged."""
    mock_get_client.return_value = None
    finding = {
        'system': 'HVAC',
        'system_key': 'hvac',
        'permit_required': 'required',
        'permit_cost_low': 300,
        'permit_cost_high': 500,
    }
    out = _enrich_finding_with_web_fee(finding, SAN_JOSE, 'San Jose, CA')
    assert out == finding  # unchanged


@patch('permit_lookup.get_anthropic_client')
def test_enrich_finding_skips_already_enriched(mock_get_client):
    """If finding already has fee_source_url, skip the API call entirely."""
    finding = {
        'system': 'HVAC',
        'system_key': 'hvac',
        'permit_required': 'required',
        'permit_cost_low': 300,
        'permit_cost_high': 500,
        'fee_source_url': 'https://sanjoseca.gov/fees',
    }
    out = _enrich_finding_with_web_fee(finding, SAN_JOSE, 'San Jose, CA')
    # Client was not invoked
    mock_get_client.assert_not_called()
    assert out == finding


@patch('permit_lookup.get_anthropic_client')
def test_enrich_finding_skips_uncertain_status(mock_get_client):
    """Don't enrich findings with permit_required='uncertain' or 'not_required'."""
    finding = {
        'system': 'Insulation',
        'system_key': 'insulation',
        'permit_required': 'uncertain',
        'permit_cost_low': None,
        'permit_cost_high': None,
    }
    out = _enrich_finding_with_web_fee(finding, SAN_JOSE, 'San Jose, CA')
    mock_get_client.assert_not_called()
    assert out == finding


@patch('permit_lookup.get_anthropic_client')
def test_enrich_finding_happy_path(mock_get_client):
    """Web search returns a valid fee + URL → finding gets updated with citation."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_web_search_response({
        'fee_low': 285,
        'fee_high': 415,
        'source_url': 'https://sanjoseca.gov/permit-fees-2025.pdf',
        'source_label': 'City of San Jose 2025 Building Permit Fee Schedule',
        'notes': None,
    })
    mock_get_client.return_value = mock_client

    finding = {
        'system': 'HVAC · Furnace',
        'system_key': 'hvac_furnace',
        'permit_required': 'required',
        'permit_cost_low': 300,  # original LLM estimate
        'permit_cost_high': 500,
    }
    out = _enrich_finding_with_web_fee(finding, SAN_JOSE, 'San Jose, CA · Santa Clara County')

    # Web-cited values replace estimates
    assert out['permit_cost_low'] == 285
    assert out['permit_cost_high'] == 415
    assert out['fee_source_url'] == 'https://sanjoseca.gov/permit-fees-2025.pdf'
    assert 'San Jose' in out['fee_source_label']
    # Original finding object is not mutated
    assert finding['permit_cost_low'] == 300


@patch('permit_lookup.get_anthropic_client')
def test_enrich_finding_keeps_estimate_when_search_returns_null(mock_get_client):
    """Web search couldn't find an authoritative fee → keep LLM estimate, no citation."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_web_search_response({
        'fee_low': None,
        'fee_high': None,
        'source_url': None,
        'source_label': None,
        'notes': 'Fee not located in official sources',
    })
    mock_get_client.return_value = mock_client

    finding = {
        'system': 'HVAC',
        'system_key': 'hvac',
        'permit_required': 'required',
        'permit_cost_low': 300,
        'permit_cost_high': 500,
    }
    out = _enrich_finding_with_web_fee(finding, SAN_JOSE, 'San Jose, CA')

    # Original LLM estimate retained
    assert out['permit_cost_low'] == 300
    assert out['permit_cost_high'] == 500
    # No citation added
    assert 'fee_source_url' not in out


@patch('permit_lookup.get_anthropic_client')
def test_enrich_finding_handles_malformed_response(mock_get_client):
    """Web search returned non-JSON garbage → keep estimate, no crash."""
    mock_client = MagicMock()
    bad_response = MagicMock()
    bad_block = MagicMock()
    bad_block.type = 'text'
    bad_block.text = 'this is not json {{{{'
    bad_response.content = [bad_block]
    mock_client.messages.create.return_value = bad_response
    mock_get_client.return_value = mock_client

    finding = {
        'system': 'HVAC',
        'system_key': 'hvac',
        'permit_required': 'required',
        'permit_cost_low': 300,
        'permit_cost_high': 500,
    }
    out = _enrich_finding_with_web_fee(finding, SAN_JOSE, 'San Jose, CA')

    # Original retained, no crash
    assert out['permit_cost_low'] == 300
    assert 'fee_source_url' not in out


@patch.dict('os.environ', {'PERMIT_FEE_WEB_SEARCH': 'false'})
@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup._enrich_finding_with_web_fee')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_does_not_enrich_when_env_off(
    mock_get_client, mock_enrich, mock_cache_set, mock_cache_get
):
    """When PERMIT_FEE_WEB_SEARCH=false, enrichment is skipped entirely.

    Note: we patch the env AND we must reload the module-level constant.
    For this test, we directly patch the module constant.
    """
    import permit_lookup
    original = permit_lookup.FEE_WEB_SEARCH_ENABLED
    permit_lookup.FEE_WEB_SEARCH_ENABLED = False
    try:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response([{
            'system_key': 'hvac', 'system': 'HVAC',
            'permit_required': 'required', 'confidence': 'high',
            'permit_cost_low': 300, 'permit_cost_high': 500,
            'consequences': 'x',
        }])
        mock_get_client.return_value = mock_client

        breakdown = [{'category': 'HVAC', 'estimated_cost_low': 5000, 'estimated_cost_high': 9000}]
        lookup_permits(breakdown, SAN_JOSE)

        # Enrichment was never called
        mock_enrich.assert_not_called()
    finally:
        permit_lookup.FEE_WEB_SEARCH_ENABLED = original


@patch('permit_lookup._cache_get', return_value=None)
@patch('permit_lookup._cache_set')
@patch('permit_lookup._enrich_finding_with_web_fee')
@patch('permit_lookup.get_anthropic_client')
def test_lookup_permits_enriches_when_env_on(
    mock_get_client, mock_enrich, mock_cache_set, mock_cache_get
):
    """When env is on, enrichment IS called for required findings."""
    import permit_lookup
    original = permit_lookup.FEE_WEB_SEARCH_ENABLED
    permit_lookup.FEE_WEB_SEARCH_ENABLED = True
    try:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_claude_response([{
            'system_key': 'hvac', 'system': 'HVAC',
            'permit_required': 'required', 'confidence': 'high',
            'permit_cost_low': 300, 'permit_cost_high': 500,
            'consequences': 'x',
        }])
        mock_get_client.return_value = mock_client
        # Enrichment returns same finding unchanged for the test
        mock_enrich.side_effect = lambda f, j, l: f

        breakdown = [{'category': 'HVAC', 'estimated_cost_low': 5000, 'estimated_cost_high': 9000}]
        lookup_permits(breakdown, SAN_JOSE)

        mock_enrich.assert_called_once()
    finally:
        permit_lookup.FEE_WEB_SEARCH_ENABLED = original
