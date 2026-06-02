"""Unit tests for infra_invoice_parser — v5.87.72.

Tests cover the deterministic parts of the parser:
  - Date parsing from various formats
  - Vendor name normalization and matching
  - JSON cleanup (stripping markdown fences)
  - Confidence-based needs_review decision logic
  - Edge cases: empty input, oversize input, missing fields

The Anthropic API call itself is mocked — we don't validate Claude's output
quality here, just that we handle its responses correctly.
"""
import json
import pytest
from datetime import date
from unittest.mock import MagicMock

from infra_invoice_parser import (
    parse_invoice_email,
    _parse_date,
    _normalize_vendor,
    _match_vendor,
    REVIEW_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def test_parse_date_iso_format():
    assert _parse_date('2026-05-04') == date(2026, 5, 4)


def test_parse_date_iso_datetime():
    assert _parse_date('2026-05-04T10:30:00Z') == date(2026, 5, 4)


def test_parse_date_iso_datetime_with_tz():
    assert _parse_date('2026-05-04T10:30:00+00:00') == date(2026, 5, 4)


def test_parse_date_handles_none():
    assert _parse_date(None) is None


def test_parse_date_handles_empty_string():
    assert _parse_date('') is None


def test_parse_date_handles_garbage():
    assert _parse_date('not a date') is None


def test_parse_date_handles_non_string():
    assert _parse_date(12345) is None


# ---------------------------------------------------------------------------
# Vendor normalization
# ---------------------------------------------------------------------------

def test_normalize_lowercases():
    assert _normalize_vendor('Render') == 'render'


def test_normalize_strips_special_chars():
    assert _normalize_vendor('Better.com') == 'bettercom'


def test_normalize_strips_spaces():
    assert _normalize_vendor('First American Title') == 'firstamericantitle'


def test_normalize_handles_empty():
    assert _normalize_vendor('') == ''


def test_normalize_handles_none_safely():
    assert _normalize_vendor(None) == ''


# ---------------------------------------------------------------------------
# Vendor matching
# ---------------------------------------------------------------------------

VENDORS = [
    {'id': 1, 'name': 'Render'},
    {'id': 2, 'name': 'Anthropic'},
    {'id': 3, 'name': 'RentCast'},
    {'id': 4, 'name': 'Resend'},
    {'id': 5, 'name': 'Google Cloud'},
    {'id': 6, 'name': 'Sentry'},
]


def test_match_exact_name():
    vid, vname = _match_vendor('Render', VENDORS)
    assert vid == 1
    assert vname == 'Render'


def test_match_case_insensitive():
    vid, vname = _match_vendor('render', VENDORS)
    assert vid == 1


def test_match_substring():
    # "Render Inc." should match "Render"
    vid, vname = _match_vendor('Render Inc.', VENDORS)
    assert vid == 1


def test_match_with_dots_and_punctuation():
    # "Anthropic, PBC" matches "Anthropic"
    vid, vname = _match_vendor('Anthropic, PBC', VENDORS)
    assert vid == 2


def test_match_returns_none_for_unknown():
    vid, vname = _match_vendor('Heroku', VENDORS)
    assert vid is None
    assert vname is None


def test_match_returns_none_for_empty():
    vid, vname = _match_vendor('', VENDORS)
    assert vid is None


def test_match_returns_none_for_none_input():
    vid, vname = _match_vendor(None, VENDORS)
    assert vid is None


def test_match_returns_none_with_no_vendors():
    vid, vname = _match_vendor('Render', [])
    assert vid is None


def test_match_substring_in_either_direction():
    # Vendor list has "Google Cloud", parsed name is just "Google"
    vid, vname = _match_vendor('Google', VENDORS)
    assert vid == 5  # "google" is in "googlecloud"


# ---------------------------------------------------------------------------
# Full parse path with mocked Claude
# ---------------------------------------------------------------------------

def _mock_anthropic(json_response: dict):
    """Build a mock Anthropic client returning the given JSON."""
    client = MagicMock()
    msg = MagicMock()
    block = MagicMock()
    block.text = json.dumps(json_response)
    msg.content = [block]
    client.messages.create.return_value = msg
    return client


def test_parse_high_confidence_render_invoice():
    client = _mock_anthropic({
        'vendor': 'Render',
        'amount_usd': 75.55,
        'period_start': '2026-03-01',
        'period_end': '2026-03-31',
        'invoice_ref': 'INV-2026-0311',
        'description': 'Web service + Postgres',
        'confidence': 0.95,
    })
    result = parse_invoice_email(
        'Subject: Render Invoice\nFrom: billing@render.com\n\nYour March 2026 invoice is $75.55',
        VENDORS, anthropic_client=client,
    )
    assert result.vendor_name_raw == 'Render'
    assert result.matched_vendor_id == 1
    assert result.matched_vendor_name == 'Render'
    assert result.amount_usd == 75.55
    assert result.period_start == date(2026, 3, 1)
    assert result.period_end == date(2026, 3, 31)
    assert result.invoice_ref == 'INV-2026-0311'
    assert result.confidence == 0.95
    assert result.needs_review is False
    assert result.parse_errors == []


def test_parse_low_confidence_flags_review():
    client = _mock_anthropic({
        'vendor': 'Render',
        'amount_usd': 75.55,
        'period_start': '2026-03-01',
        'period_end': '2026-03-31',
        'confidence': 0.50,  # below threshold
    })
    result = parse_invoice_email(
        'Some email content here that is a vendor invoice',
        VENDORS, anthropic_client=client,
    )
    assert result.needs_review is True
    assert any('low_confidence' in e for e in result.parse_errors)


def test_parse_unknown_vendor_flags_review():
    client = _mock_anthropic({
        'vendor': 'NewVendor',  # not in VENDORS list
        'amount_usd': 50.0,
        'period_start': '2026-03-01',
        'period_end': '2026-03-31',
        'confidence': 0.95,
    })
    result = parse_invoice_email(
        'Subject: Invoice from NewVendor\nAmount: $50',
        VENDORS, anthropic_client=client,
    )
    assert result.matched_vendor_id is None
    assert result.needs_review is True
    assert 'no_vendor_match' in result.parse_errors


def test_parse_missing_amount_flags_review():
    client = _mock_anthropic({
        'vendor': 'Render',
        'amount_usd': None,
        'period_start': '2026-03-01',
        'period_end': '2026-03-31',
        'confidence': 0.95,
    })
    result = parse_invoice_email(
        'Render email with no amount mentioned',
        VENDORS, anthropic_client=client,
    )
    assert result.amount_usd is None
    assert result.needs_review is True
    assert 'no_amount' in result.parse_errors


def test_parse_strips_json_markdown_fences():
    """Claude sometimes wraps JSON in ```json fences despite instructions."""
    client = MagicMock()
    msg = MagicMock()
    block = MagicMock()
    block.text = '```json\n' + json.dumps({
        'vendor': 'Render',
        'amount_usd': 75.0,
        'period_start': '2026-03-01',
        'period_end': '2026-03-31',
        'confidence': 0.95,
    }) + '\n```'
    msg.content = [block]
    client.messages.create.return_value = msg
    result = parse_invoice_email(
        'A real-looking invoice email with enough content for the parser',
        VENDORS, anthropic_client=client,
    )
    assert result.amount_usd == 75.0
    assert result.matched_vendor_id == 1


def test_parse_marketing_email_returns_zero_confidence():
    """If Claude correctly identifies a non-invoice email, confidence is 0."""
    client = _mock_anthropic({
        'vendor': None,
        'amount_usd': None,
        'period_start': None,
        'period_end': None,
        'confidence': 0.0,
    })
    result = parse_invoice_email(
        'Subject: New features at Render!\nCheck out our new dashboard...',
        VENDORS, anthropic_client=client,
    )
    assert result.confidence == 0.0
    assert result.needs_review is True


def test_parse_too_short_input_returns_failed():
    """Empty or near-empty input shouldn't even reach Claude."""
    result = parse_invoice_email('hi', VENDORS, anthropic_client=MagicMock())
    assert result.confidence == 0.0
    assert 'email_content_too_short' in result.parse_errors


def test_parse_oversize_input_truncated():
    """Inputs over 50K chars are truncated, with an error logged."""
    huge_content = 'x' * 60000
    client = _mock_anthropic({
        'vendor': 'Render',
        'amount_usd': 75.0,
        'period_start': '2026-03-01',
        'period_end': '2026-03-31',
        'confidence': 0.95,
    })
    result = parse_invoice_email(huge_content, VENDORS, anthropic_client=client)
    assert 'email_truncated_to_50k' in result.parse_errors
    # But still parses successfully
    assert result.amount_usd == 75.0


def test_parse_anthropic_error_returns_zero_confidence():
    """If Anthropic call raises, return result with confidence 0 and error logged."""
    client = MagicMock()
    client.messages.create.side_effect = Exception('rate limit')
    result = parse_invoice_email(
        'A normal invoice email with plenty of content for parsing',
        VENDORS, anthropic_client=client,
    )
    assert result.confidence == 0.0
    assert any('anthropic_error' in e for e in result.parse_errors)


def test_parse_malformed_json_returns_failure():
    """If Claude returns non-JSON, we don't crash."""
    client = MagicMock()
    msg = MagicMock()
    block = MagicMock()
    block.text = 'this is not json at all just prose'
    msg.content = [block]
    client.messages.create.return_value = msg
    result = parse_invoice_email(
        'A normal invoice email with plenty of content',
        VENDORS, anthropic_client=client,
    )
    assert result.confidence == 0.0
    assert any('json_decode_error' in e for e in result.parse_errors)


def test_parse_amount_string_handled():
    """Claude might return amount as string '75.50' instead of float 75.50."""
    client = _mock_anthropic({
        'vendor': 'Render',
        'amount_usd': '75.55',  # string
        'period_start': '2026-03-01',
        'period_end': '2026-03-31',
        'confidence': 0.95,
    })
    result = parse_invoice_email(
        'A normal invoice email with adequate content',
        VENDORS, anthropic_client=client,
    )
    assert result.amount_usd == 75.55  # float-coerced


def test_review_threshold_constant_is_strict():
    """REVIEW_THRESHOLD should be high — we'd rather false-flag than miss errors."""
    assert REVIEW_THRESHOLD >= 0.80
