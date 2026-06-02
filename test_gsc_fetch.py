"""Unit tests for gsc_fetch — v5.87.86

Tests the deterministic logic (intent classification, env-var checks,
result aggregation) with mocked Google API responses for the network
calls. The actual GSC API call itself is not tested live; that requires
a real refresh token in env.
"""
import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# classify_query — intent rules
# ---------------------------------------------------------------------------

def test_classify_cost_queries():
    from gsc_fetch import classify_query
    assert classify_query('how much does it cost to fix water damage') == 'cost'
    assert classify_query('repair budget for older home') == 'cost'
    assert classify_query('cost to replace water heater') == 'cost'


def test_classify_contradiction_queries():
    from gsc_fetch import classify_query
    assert classify_query('seller hid water damage') == 'contradiction'
    assert classify_query('lying seller real estate') == 'contradiction'
    assert classify_query('undisclosed roof problems') == 'contradiction'


def test_classify_permit_queries():
    from gsc_fetch import classify_query
    assert classify_query('does i need a permit to replace water heater') == 'permit'
    assert classify_query('unpermitted addition resale') == 'permit'
    assert classify_query('code violation in real estate') == 'permit'


def test_classify_risk_queries():
    from gsc_fetch import classify_query
    assert classify_query('flood zone check') == 'risk'
    assert classify_query('earthquake fault line san francisco') == 'risk'
    assert classify_query('wildfire risk california') == 'risk'
    assert classify_query('mold in house') == 'risk'


def test_classify_negotiation_queries():
    from gsc_fetch import classify_query
    assert classify_query('how much should i offer for a house') == 'negotiation'
    assert classify_query('counter offer real estate') == 'negotiation'
    assert classify_query('credit at closing') == 'negotiation'


def test_classify_comps_queries():
    from gsc_fetch import classify_query
    assert classify_query('comparable sales near me') == 'comps'
    assert classify_query('home value lookup') == 'comps'
    assert classify_query('is this house overpriced') == 'comps'


def test_classify_branded_queries():
    from gsc_fetch import classify_query
    assert classify_query('offerwise reviews') == 'branded'
    assert classify_query('getofferwise') == 'branded'


def test_classify_tools_queries():
    """v5.87.90: catch product/service searches that previously fell into Other."""
    from gsc_fetch import classify_query
    assert classify_query('best home buying app') == 'tools'
    assert classify_query('ai for real estate') == 'tools'
    assert classify_query('property analysis platform') == 'tools'
    assert classify_query('disclosure analyzer') == 'tools'


def test_classify_buying_advice_queries():
    """v5.87.90: catch generic homebuyer-help searches."""
    from gsc_fetch import classify_query
    assert classify_query('first time home buyer tips') == 'buying_advice'
    assert classify_query('how to buy a house') == 'buying_advice'
    assert classify_query('home buyer red flags') == 'buying_advice'
    assert classify_query('mistakes to avoid when buying a home') == 'buying_advice'


def test_classify_zestimate_lands_in_comps():
    """v5.87.90: Zestimate is a value-lookup query, should be in comps."""
    from gsc_fetch import classify_query
    assert classify_query('zestimate accuracy') == 'comps'
    assert classify_query('zillow estimate vs redfin') == 'comps'


def test_classify_other_falls_through():
    from gsc_fetch import classify_query
    assert classify_query('best mortgage rates today') == 'other'
    assert classify_query('how to wash windows') == 'other'


def test_classify_empty_returns_other():
    from gsc_fetch import classify_query
    assert classify_query('') == 'other'
    assert classify_query(None) == 'other'


def test_classify_first_match_wins():
    """A query that hits multiple buckets should land in the first
    matching bucket per the rule order. Permit is checked before cost,
    so 'water heater permit cost' should land in 'permit', not 'cost'."""
    from gsc_fetch import classify_query
    assert classify_query('water heater permit cost') == 'permit'


def test_classify_case_insensitive():
    from gsc_fetch import classify_query
    assert classify_query('PERMIT REQUIRED FOR ROOF REPLACEMENT') == 'permit'


# ---------------------------------------------------------------------------
# is_configured / auth_mode / missing_env_vars  (v5.87.87 dual-mode)
# ---------------------------------------------------------------------------

def test_is_configured_false_when_no_env_vars():
    from gsc_fetch import is_configured
    with patch.dict('os.environ', {}, clear=True):
        assert is_configured() is False


def test_is_configured_true_when_oauth_env_vars_set():
    """OAuth path — all four oauth vars set, no service account."""
    from gsc_fetch import is_configured
    with patch.dict('os.environ', {
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }, clear=True):
        assert is_configured() is True


def test_is_configured_true_when_service_account_env_vars_set():
    """Service account path — JSON + site URL, no OAuth vars needed."""
    from gsc_fetch import is_configured
    with patch.dict('os.environ', {
        'GSC_SERVICE_ACCOUNT_JSON': '{"type":"service_account","client_email":"x@y.iam"}',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }, clear=True):
        assert is_configured() is True


def test_auth_mode_returns_service_account_when_both_paths_configured():
    """Service account wins when both modes are set up."""
    from gsc_fetch import auth_mode, AUTH_MODE_SERVICE_ACCOUNT
    with patch.dict('os.environ', {
        'GSC_SERVICE_ACCOUNT_JSON': '{"type":"service_account"}',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
    }, clear=True):
        assert auth_mode() == AUTH_MODE_SERVICE_ACCOUNT


def test_auth_mode_returns_oauth_when_only_oauth_configured():
    from gsc_fetch import auth_mode, AUTH_MODE_OAUTH
    with patch.dict('os.environ', {
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }, clear=True):
        assert auth_mode() == AUTH_MODE_OAUTH


def test_auth_mode_returns_none_when_neither_configured():
    from gsc_fetch import auth_mode
    with patch.dict('os.environ', {}, clear=True):
        assert auth_mode() is None


def test_auth_mode_partial_oauth_returns_none():
    """If user only set OAuth client ID but not the rest, auth_mode is None."""
    from gsc_fetch import auth_mode
    with patch.dict('os.environ', {
        'GSC_CLIENT_ID': 'cid',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }, clear=True):
        assert auth_mode() is None


def test_auth_mode_service_account_needs_site_url():
    """Service account JSON alone isn't enough — site URL still required."""
    from gsc_fetch import auth_mode
    with patch.dict('os.environ', {
        'GSC_SERVICE_ACCOUNT_JSON': '{"type":"service_account"}',
    }, clear=True):
        assert auth_mode() is None


def test_missing_env_vars_recommends_service_account_when_nothing_set():
    """Default recommendation when neither path started: service account."""
    from gsc_fetch import missing_env_vars
    with patch.dict('os.environ', {}, clear=True):
        missing = missing_env_vars()
        assert 'GSC_SERVICE_ACCOUNT_JSON' in missing
        assert 'GSC_SITE_URL' in missing


def test_missing_env_vars_lists_oauth_when_oauth_partially_set():
    """If user clearly started OAuth path, list its missing pieces."""
    from gsc_fetch import missing_env_vars
    with patch.dict('os.environ', {
        'GSC_CLIENT_ID': 'cid',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }, clear=True):
        missing = missing_env_vars()
        assert 'GSC_CLIENT_SECRET' in missing
        assert 'GSC_REFRESH_TOKEN' in missing
        # OAuth-related missing, not service-account
        assert 'GSC_SERVICE_ACCOUNT_JSON' not in missing


def test_missing_env_vars_returns_only_site_url_when_sa_json_set():
    """Service account JSON set but no site URL — only site URL is missing."""
    from gsc_fetch import missing_env_vars
    with patch.dict('os.environ', {
        'GSC_SERVICE_ACCOUNT_JSON': '{"type":"service_account"}',
    }, clear=True):
        missing = missing_env_vars()
        assert missing == ['GSC_SITE_URL']


# ---------------------------------------------------------------------------
# _get_access_token — auth mode dispatch
# ---------------------------------------------------------------------------

@patch('gsc_fetch._get_access_token_service_account', return_value='sa-token-abc')
@patch('gsc_fetch._get_access_token_oauth', return_value='oauth-token-xyz')
def test_get_access_token_prefers_service_account(mock_oauth, mock_sa):
    """When both modes configured, service account is used."""
    from gsc_fetch import _get_access_token
    with patch.dict('os.environ', {
        'GSC_SERVICE_ACCOUNT_JSON': '{"type":"service_account"}',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
    }, clear=True):
        token = _get_access_token()
    assert token == 'sa-token-abc'
    mock_sa.assert_called_once()
    mock_oauth.assert_not_called()


@patch('gsc_fetch._get_access_token_service_account', return_value='sa-token-abc')
@patch('gsc_fetch._get_access_token_oauth', return_value='oauth-token-xyz')
def test_get_access_token_falls_back_to_oauth_when_no_sa(mock_oauth, mock_sa):
    """No service account configured → use OAuth."""
    from gsc_fetch import _get_access_token
    with patch.dict('os.environ', {
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }, clear=True):
        token = _get_access_token()
    assert token == 'oauth-token-xyz'
    mock_oauth.assert_called_once()
    mock_sa.assert_not_called()


def test_get_access_token_returns_none_when_neither_configured():
    from gsc_fetch import _get_access_token
    with patch.dict('os.environ', {}, clear=True):
        assert _get_access_token() is None


def test_service_account_token_handles_invalid_json():
    """Bad JSON in env var → logged warning, None returned, no crash."""
    from gsc_fetch import _get_access_token_service_account
    with patch.dict('os.environ', {
        'GSC_SERVICE_ACCOUNT_JSON': 'this is not json {{{',
    }, clear=True):
        assert _get_access_function_safe() is None


def _get_access_function_safe():
    """Inline helper because the real fn name is long."""
    from gsc_fetch import _get_access_token_service_account
    return _get_access_token_service_account()


# ---------------------------------------------------------------------------
# classify_queries — aggregation
# ---------------------------------------------------------------------------

def test_classify_queries_empty_input():
    from gsc_fetch import classify_queries
    r = classify_queries([])
    assert r['intent_breakdown'] == []
    assert r['top_queries_per_intent'] == {}
    assert r['total_clicks'] == 0


def test_classify_queries_aggregates_correctly():
    from gsc_fetch import classify_queries
    queries = [
        {'query': 'cost to replace water heater', 'clicks': 100, 'impressions': 1000, 'ctr': 0.1, 'position': 5.0},
        {'query': 'how much to fix roof', 'clicks': 50, 'impressions': 500, 'ctr': 0.1, 'position': 6.0},
        {'query': 'does i need a permit', 'clicks': 30, 'impressions': 600, 'ctr': 0.05, 'position': 8.0},
        {'query': 'flood zone san jose', 'clicks': 20, 'impressions': 400, 'ctr': 0.05, 'position': 7.0},
    ]
    r = classify_queries(queries)

    assert r['total_clicks'] == 200
    assert r['total_impressions'] == 2500

    # Cost should dominate (100 + 50 = 150 clicks)
    cost_bucket = next(b for b in r['intent_breakdown'] if b['intent'] == 'cost')
    assert cost_bucket['clicks'] == 150
    assert cost_bucket['click_share_pct'] == 75.0
    assert cost_bucket['query_count'] == 2

    permit_bucket = next(b for b in r['intent_breakdown'] if b['intent'] == 'permit')
    assert permit_bucket['clicks'] == 30
    assert permit_bucket['click_share_pct'] == 15.0


def test_classify_queries_orders_by_clicks_desc():
    """The intent_breakdown list should be sorted by clicks descending
    so the dominant intent is first."""
    from gsc_fetch import classify_queries
    queries = [
        {'query': 'flood', 'clicks': 5, 'impressions': 50, 'ctr': 0.1, 'position': 5.0},
        {'query': 'cost to fix', 'clicks': 100, 'impressions': 800, 'ctr': 0.1, 'position': 5.0},
        {'query': 'permit needed', 'clicks': 50, 'impressions': 400, 'ctr': 0.1, 'position': 5.0},
    ]
    r = classify_queries(queries)
    intents_in_order = [b['intent'] for b in r['intent_breakdown']]
    assert intents_in_order[0] == 'cost'
    assert intents_in_order[1] == 'permit'
    assert intents_in_order[2] == 'risk'


def test_classify_queries_top_queries_per_intent():
    """Drill-down should return queries within each intent, sorted by clicks."""
    from gsc_fetch import classify_queries
    queries = [
        {'query': 'low click cost q', 'clicks': 5, 'impressions': 100, 'ctr': 0.05, 'position': 8.0},
        {'query': 'high click cost q', 'clicks': 50, 'impressions': 500, 'ctr': 0.1, 'position': 5.0},
    ]
    r = classify_queries(queries)
    cost_top = r['top_queries_per_intent']['cost']
    assert cost_top[0]['query'] == 'high click cost q'
    assert cost_top[1]['query'] == 'low click cost q'


# ---------------------------------------------------------------------------
# fetch_top_queries — error paths
# ---------------------------------------------------------------------------

def test_fetch_top_queries_returns_not_configured_when_env_missing():
    from gsc_fetch import fetch_top_queries
    with patch.dict('os.environ', {}, clear=True):
        r = fetch_top_queries(days=90)
    assert r['data_source'] == 'not_configured'
    assert r['queries'] == []
    assert 'Missing env vars' in r['error']


@patch('gsc_fetch._get_access_token', return_value=None)
def test_fetch_top_queries_returns_error_when_oauth_fails(mock_token):
    from gsc_fetch import fetch_top_queries
    with patch.dict('os.environ', {
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }):
        r = fetch_top_queries(days=90)
    assert r['data_source'] == 'error'
    assert 'access token' in r['error'].lower()


@patch('gsc_fetch._get_access_token', return_value='fake_token')
def test_fetch_top_queries_handles_api_error(mock_token):
    from gsc_fetch import fetch_top_queries
    import requests
    with patch.dict('os.environ', {
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }):
        with patch('requests.post') as mock_post:
            mock_post.side_effect = RuntimeError('network down')
            r = fetch_top_queries(days=90)
    assert r['data_source'] == 'error'


@patch('gsc_fetch._get_access_token', return_value='fake_token')
def test_fetch_top_queries_extracts_google_error_reason(mock_token):
    """v5.87.88: when Google returns 403 with a JSON body, surface the
    'reason' field (e.g. 'accessNotConfigured' or 'forbidden') so the
    admin can act on it."""
    from gsc_fetch import fetch_top_queries
    with patch.dict('os.environ', {
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
        'GSC_SITE_URL': 'sc-domain:getofferwise.ai',
    }):
        # Mock a 403 response with the standard Google error envelope
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 403
        mock_response.reason = 'Forbidden'
        mock_response.json.return_value = {
            'error': {
                'code': 403,
                'message': 'Search Console API has not been used in project 482502',
                'errors': [{'reason': 'accessNotConfigured', 'message': 'Search Console API not enabled'}],
            }
        }
        with patch('requests.post', return_value=mock_response):
            r = fetch_top_queries(days=90)

    assert r['data_source'] == 'error'
    assert 'accessNotConfigured' in r['error']
    assert 'Search Console API has not been used' in r['error']


@patch('gsc_fetch._get_access_token', return_value='fake_token')
def test_fetch_top_queries_happy_path(mock_token):
    """API returns valid rows → parsed correctly."""
    from gsc_fetch import fetch_top_queries
    with patch.dict('os.environ', {
        'GSC_REFRESH_TOKEN': 'token',
        'GSC_CLIENT_ID': 'cid',
        'GSC_CLIENT_SECRET': 'secret',
        'GSC_SITE_URL': 'https://getofferwise.ai/',
    }):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            'rows': [
                {'keys': ['cost to fix water damage'], 'clicks': 23, 'impressions': 412, 'ctr': 0.0558, 'position': 8.4},
                {'keys': ['flood zone check'], 'clicks': 15, 'impressions': 280, 'ctr': 0.0535, 'position': 6.2},
            ]
        }
        with patch('requests.post', return_value=mock_response):
            r = fetch_top_queries(days=90)

    assert r['data_source'] == 'live'
    assert len(r['queries']) == 2
    assert r['queries'][0]['query'] == 'cost to fix water damage'
    assert r['queries'][0]['clicks'] == 23
    assert r['total_clicks'] == 38
    assert r['total_impressions'] == 692
    assert r['error'] is None


# ---------------------------------------------------------------------------
# v5.87.89 — fetch_and_classify_paid (Google Ads search terms)
# ---------------------------------------------------------------------------

@patch('google_ads_sync.is_configured', return_value=False)
def test_fetch_and_classify_paid_returns_not_configured_when_gads_off(mock_cfg):
    """Google Ads not configured → returns shape with not_configured."""
    from gsc_fetch import fetch_and_classify_paid
    r = fetch_and_classify_paid(days=30)
    assert r['data_source'] == 'not_configured'
    assert r['queries'] == []
    assert r['intent_breakdown'] == []
    assert 'GOOGLE_ADS' in (r.get('error') or '')


@patch('google_ads_sync.fetch_search_terms')
@patch('google_ads_sync.is_configured', return_value=True)
def test_fetch_and_classify_paid_handles_empty_terms(mock_cfg, mock_fetch):
    """No paid search terms returned → live but empty intent breakdown."""
    from gsc_fetch import fetch_and_classify_paid
    mock_fetch.return_value = []
    r = fetch_and_classify_paid(days=30)
    assert r['data_source'] == 'live'
    assert r['queries'] == []
    assert r['total_clicks'] == 0
    assert r['intent_breakdown'] == []


@patch('google_ads_sync.fetch_search_terms')
@patch('google_ads_sync.is_configured', return_value=True)
def test_fetch_and_classify_paid_classifies_correctly(mock_cfg, mock_fetch):
    """Paid search terms returned → classified into intent buckets."""
    from gsc_fetch import fetch_and_classify_paid
    mock_fetch.return_value = [
        {'query': 'cost to fix water damage', 'clicks': 50, 'impressions': 400,
         'cost': 18.50, 'conversions': 2, 'ctr': 0.125, 'avg_cpc': 0.37},
        {'query': 'flood zone san jose', 'clicks': 20, 'impressions': 200,
         'cost': 7.20, 'conversions': 0, 'ctr': 0.1, 'avg_cpc': 0.36},
        {'query': 'how much should i offer for a house', 'clicks': 10, 'impressions': 50,
         'cost': 5.50, 'conversions': 1, 'ctr': 0.2, 'avg_cpc': 0.55},
    ]
    r = fetch_and_classify_paid(days=30)
    assert r['data_source'] == 'live'
    assert r['total_clicks'] == 80
    assert r['total_impressions'] == 650
    assert r['total_cost'] == 31.20
    assert r['total_conversions'] == 3
    # Intent breakdown should sort by clicks desc — cost (50) leads
    intents = [b['intent'] for b in r['intent_breakdown']]
    assert intents[0] == 'cost'
    assert 'risk' in intents
    assert 'negotiation' in intents


@patch('google_ads_sync.fetch_search_terms')
@patch('google_ads_sync.is_configured', return_value=True)
def test_fetch_and_classify_paid_handles_fetch_exception(mock_cfg, mock_fetch):
    """Exception during fetch → returns error shape, doesn't crash."""
    from gsc_fetch import fetch_and_classify_paid
    mock_fetch.side_effect = RuntimeError('Google Ads API timeout')
    r = fetch_and_classify_paid(days=30)
    assert r['data_source'] == 'error'
    assert 'Google Ads API timeout' in r['error']
    assert r['queries'] == []

@patch('gsc_fetch.fetch_top_queries')
def test_fetch_and_classify_combines_correctly(mock_fetch):
    """fetch_and_classify should call fetch + classify and merge the results."""
    from gsc_fetch import fetch_and_classify
    mock_fetch.return_value = {
        'queries': [
            {'query': 'cost to fix roof', 'clicks': 100, 'impressions': 800, 'ctr': 0.125, 'position': 5.0},
            {'query': 'flood zone', 'clicks': 20, 'impressions': 200, 'ctr': 0.1, 'position': 4.0},
        ],
        'total_clicks': 120,
        'total_impressions': 1000,
        'window_days': 90,
        'site_url': 'https://getofferwise.ai/',
        'fetched_at': '2026-05-06T12:00:00Z',
        'data_source': 'live',
        'error': None,
    }

    r = fetch_and_classify(days=90)

    assert r['data_source'] == 'live'
    assert 'intent_breakdown' in r
    assert 'top_queries_per_intent' in r
    intents = [b['intent'] for b in r['intent_breakdown']]
    assert 'cost' in intents
    assert 'risk' in intents


@patch('gsc_fetch.fetch_top_queries')
def test_fetch_and_classify_passes_through_errors(mock_fetch):
    """When fetch fails, classify should return error shape with empty buckets."""
    from gsc_fetch import fetch_and_classify
    mock_fetch.return_value = {
        'queries': [],
        'total_clicks': 0,
        'total_impressions': 0,
        'window_days': 90,
        'site_url': '',
        'fetched_at': '2026-05-06T12:00:00Z',
        'data_source': 'not_configured',
        'error': 'Missing env vars',
    }
    r = fetch_and_classify(days=90)
    assert r['data_source'] == 'not_configured'
    assert r['intent_breakdown'] == []
    assert r['top_queries_per_intent'] == {}
