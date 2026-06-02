"""
gsc_fetch.py — v5.87.87

Google Search Console fetcher for query intent analysis.

Pulls top organic search queries that landed users on getofferwise.ai over
a configurable window (default 90 days), groups them by intent buckets
(cost, contradiction, permit, risk, negotiation, comps, branded, other),
and returns aggregate clicks + impressions per bucket.

This answers the question: "what do users actually search for that brings
them to OfferWise?" — empirical demand-side data instead of guesses.

═══════════════════════════════════════════════════════════════════════════
AUTHENTICATION — TWO MODES (v5.87.87)
═══════════════════════════════════════════════════════════════════════════

The module supports two auth paths. It checks them in order and uses
whichever is configured:

MODE 1 — Service account (RECOMMENDED)
  Required env var:
    - GSC_SERVICE_ACCOUNT_JSON   (full JSON of the service account key,
                                  as a single-line string)
  Required Search Console step:
    The service account's email (e.g. offerwise@offerwise-482502.iam
    .gserviceaccount.com) must be granted access to the GSC property
    via Search Console → Settings → Users and permissions → Add User.
    "Restricted" (read-only) is sufficient.

  Why preferred: no refresh token to manage, no expiration, no consent
  screen, no test-users list. Permanent until you revoke the key.

MODE 2 — OAuth refresh token (fallback)
  Required env vars:
    - GSC_REFRESH_TOKEN
    - GSC_CLIENT_ID
    - GSC_CLIENT_SECRET

  Use when: service account isn't an option (rare). Refresh tokens can
  be revoked unexpectedly when Google detects unusual activity.

COMMON env var (required either way):
  - GSC_SITE_URL    e.g. "https://getofferwise.ai/" or
                    "sc-domain:getofferwise.ai"
                    (must match the verified property in Search Console)

OPTIONAL:
  - GSC_DAYS                    (default 90) — query window
  - GSC_MAX_ROWS                (default 1000) — top N queries pulled

═══════════════════════════════════════════════════════════════════════════

Public API:
  is_configured() -> bool
  auth_mode() -> 'service_account' | 'oauth' | None
  fetch_top_queries(days, max_rows) -> {queries, total_clicks, ...}
  classify_queries(queries) -> {intent_breakdown, top_queries_per_intent, ...}
  fetch_and_classify(days, max_rows) -> combined result for admin UI

The classification rules are intentionally simple keyword matches. The goal
is not perfect linguistics — it's a directional view of dominant intent.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

GSC_DEFAULT_DAYS = int(os.environ.get('GSC_DAYS', '90'))
GSC_DEFAULT_MAX_ROWS = int(os.environ.get('GSC_MAX_ROWS', '1000'))

# Auth mode strings used by auth_mode() and admin UI status display.
AUTH_MODE_SERVICE_ACCOUNT = 'service_account'
AUTH_MODE_OAUTH = 'oauth'


# ── Intent classification rules ──────────────────────────────────────────
#
# Order matters — a query like "permit cost" lands in "permit" not "cost"
# because permit is checked first. Rules are evaluated top-to-bottom; first
# match wins. Bucket order roughly matches our product strategy:
#
#   cost              — repair cost estimates (the obvious-but-not-everything bucket)
#   contradiction     — disclosure / inspection inconsistency (our differentiator)
#   permit            — permit requirements (v5.87.82 feature)
#   risk              — flood, earthquake, hazards (free-tools surface)
#   negotiation       — offer price, counteroffer guidance (intelligence layer)
#   comps             — comparable sales, pricing
#   inspection        — inspection report literacy
#   disclosure        — seller disclosure literacy
#   branded           — searches for "offerwise" specifically
#   other             — everything else
#
# A query may match multiple keywords; first-match-wins gives us a clean
# allocation without double-counting.

INTENT_RULES = [
    ('permit',         ['permit', 'unpermitted', 'permitting', 'code violation', 'code enforcement']),
    ('contradiction',  ['contradiction', 'lying seller', 'seller hid', 'undisclosed', 'didn\'t disclose',
                        'didnt disclose', 'cross reference', 'cross-reference']),
    ('negotiation',    ['negotiate', 'counter offer', 'counteroffer', 'offer price',
                        'how much should i offer', 'how much to offer', 'lowball', 'concession',
                        'credit at closing']),
    ('risk',           ['flood', 'earthquake', 'fault line', 'wildfire', 'fire risk', 'hazard',
                        'fema', 'environmental risk', 'contamination', 'asbestos', 'lead paint',
                        'mold', 'radon']),
    ('cost',           ['cost', 'how much', 'price to fix', 'price to repair',
                        'cost to replace', 'repair budget', 'replacement cost',
                        'repair estimate', 'cost estimate', 'estimate cost',
                        'estimate to fix', 'estimate to repair']),
    ('comps',          ['comp', 'comparable', 'home value', 'market value', 'fair market value',
                        'is this house worth', 'what is this house worth', 'overpriced',
                        'underpriced', 'appraisal', 'zestimate', 'zillow estimate', 'redfin estimate',
                        'property value', 'house value', 'home worth', 'house worth']),
    ('inspection',     ['inspection report', 'home inspection', 'inspector found', 'inspector report',
                        'inspection results', 'how to read inspection']),
    ('disclosure',     ['seller disclosure', 'tds', 'transfer disclosure', 'how to read disclosure',
                        'sellers disclosure', 'disclosure form']),
    # v5.87.90: 'tools' captures product/service search ("best home buying app",
    # "ai for real estate", etc). Goes BEFORE 'buying_advice' so "best app for
    # buying a house" lands in tools, not advice.
    ('tools',          ['app', 'apps', 'tool', 'tools', 'platform', 'software',
                        'ai for real estate', 'ai real estate', 'real estate ai',
                        'home buying ai', 'ai home buying', 'ai analysis', 'machine learning',
                        'analyzer', 'checker', 'calculator', 'analyze', 'review tool']),
    # v5.87.90: 'buying_advice' captures generic homebuyer help that doesn't
    # fit cost/comps/inspection ("first time home buyer", "how to buy a house",
    # "what to look for", "homebuyer guide", "things to know")
    ('buying_advice',  ['first time home buyer', 'first-time home buyer',
                        'how to buy a house', 'how to buy a home', 'buying a house',
                        'buying a home', 'home buyer guide', 'homebuyer guide',
                        'what to look for', 'things to know', 'red flag',
                        'home buyer checklist', 'homebuyer checklist',
                        'mistake', 'mistakes', 'tips for buying',
                        'before you buy', 'before buying', 'should i buy',
                        'home buyer tips', 'homebuyer tips']),
    ('branded',        ['offerwise', 'offer wise', 'getofferwise']),
]


def classify_query(q: str) -> str:
    """Return the first matching intent bucket for a query string.

    Returns 'other' if no rule matches. Lowercase comparison; whitespace
    is normalized but no stemming.
    """
    if not q:
        return 'other'
    q_lower = q.lower().strip()
    for bucket, keywords in INTENT_RULES:
        for kw in keywords:
            if kw in q_lower:
                return bucket
    return 'other'


# ── Configuration check ──────────────────────────────────────────────────

def auth_mode() -> str | None:
    """Return which auth mode is configured, or None if neither is.

    Service account wins when both are configured (preferred path).
    """
    if os.environ.get('GSC_SERVICE_ACCOUNT_JSON', '').strip() \
            and os.environ.get('GSC_SITE_URL', '').strip():
        return AUTH_MODE_SERVICE_ACCOUNT
    oauth_vars = ['GSC_REFRESH_TOKEN', 'GSC_CLIENT_ID', 'GSC_CLIENT_SECRET', 'GSC_SITE_URL']
    if all(os.environ.get(k, '').strip() for k in oauth_vars):
        return AUTH_MODE_OAUTH
    return None


def is_configured() -> bool:
    """Return True if either auth mode is fully configured."""
    return auth_mode() is not None


def missing_env_vars() -> list[str]:
    """Return list of missing env var names for the chosen path.

    If GSC_SERVICE_ACCOUNT_JSON is set, only checks the service-account
    path. If neither path is started (no service-account JSON, no client
    ID), defaults to listing the service-account requirements since that's
    the recommended path.
    """
    site_url_set = bool(os.environ.get('GSC_SITE_URL', '').strip())
    sa_json_set = bool(os.environ.get('GSC_SERVICE_ACCOUNT_JSON', '').strip())
    oauth_started = bool(
        os.environ.get('GSC_CLIENT_ID', '').strip() or
        os.environ.get('GSC_REFRESH_TOKEN', '').strip()
    )

    # If user already started the OAuth path, list OAuth's missing pieces.
    if oauth_started and not sa_json_set:
        oauth_required = ['GSC_REFRESH_TOKEN', 'GSC_CLIENT_ID',
                          'GSC_CLIENT_SECRET', 'GSC_SITE_URL']
        return [k for k in oauth_required if not os.environ.get(k, '').strip()]

    # Otherwise default to recommending the service-account path.
    sa_required = ['GSC_SERVICE_ACCOUNT_JSON', 'GSC_SITE_URL']
    return [k for k in sa_required if not os.environ.get(k, '').strip()]


# ── OAuth token refresh (Mode 2 — fallback path) ─────────────────────────

def _get_access_token_oauth() -> str | None:
    """Exchange OAuth refresh token for a fresh access token (Mode 2)."""
    try:
        import requests
        resp = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': os.environ['GSC_CLIENT_ID'],
                'client_secret': os.environ['GSC_CLIENT_SECRET'],
                'refresh_token': os.environ['GSC_REFRESH_TOKEN'],
                'grant_type': 'refresh_token',
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get('access_token')
    except Exception as e:
        logger.warning(f'GSC OAuth refresh failed: {e}')
        return None


# ── Service account auth (Mode 1 — preferred path) ───────────────────────

def _get_access_token_service_account() -> str | None:
    """Exchange service account credentials for an access token (Mode 1).

    Uses google-auth library which handles JWT signing and token caching.
    The library is part of google-auth, already a transitive dep of any
    Google API SDK. We import lazily so a missing dep on legacy deploys
    doesn't break import time.
    """
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as _AuthRequest

        sa_json_str = os.environ['GSC_SERVICE_ACCOUNT_JSON']
        try:
            sa_info = json.loads(sa_json_str)
        except json.JSONDecodeError as e:
            logger.warning(f'GSC service account JSON is not valid JSON: {e}')
            return None

        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=['https://www.googleapis.com/auth/webmasters.readonly'],
        )
        # Force a fresh token — google-auth caches automatically but we
        # want to surface auth failures here, not later in the API call.
        creds.refresh(_AuthRequest())
        return creds.token
    except ImportError as e:
        logger.warning(
            'GSC service account auth requires google-auth library: %s. '
            'pip install google-auth google-auth-httplib2', e
        )
        return None
    except Exception as e:
        logger.warning(f'GSC service account token exchange failed: {e}')
        return None


def _get_access_token() -> str | None:
    """Get an access token using whichever auth mode is configured.

    Tries service account first, then OAuth refresh token. Returns the
    token string or None on any failure.
    """
    mode = auth_mode()
    if mode == AUTH_MODE_SERVICE_ACCOUNT:
        return _get_access_token_service_account()
    elif mode == AUTH_MODE_OAUTH:
        return _get_access_token_oauth()
    return None


# ── Fetch top queries from the Search Analytics API ──────────────────────

def fetch_top_queries(
    days: int = GSC_DEFAULT_DAYS,
    max_rows: int = GSC_DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Pull top organic search queries from Search Console.

    Returns:
        {
          'queries': [
            {'query': 'how much to fix water damage', 'clicks': 23,
             'impressions': 412, 'ctr': 0.0558, 'position': 8.4},
            ...
          ],
          'total_clicks': 1234,
          'total_impressions': 56789,
          'window_days': 90,
          'site_url': 'https://getofferwise.ai/',
          'fetched_at': '2026-05-06T12:00:00Z',
          'data_source': 'live' | 'error' | 'not_configured',
          'error': None | str,
        }
    """
    if not is_configured():
        return {
            'queries': [],
            'total_clicks': 0,
            'total_impressions': 0,
            'window_days': days,
            'site_url': os.environ.get('GSC_SITE_URL', ''),
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'not_configured',
            'error': f'Missing env vars: {", ".join(missing_env_vars())}',
        }

    access_token = _get_access_token()
    if not access_token:
        return {
            'queries': [],
            'total_clicks': 0,
            'total_impressions': 0,
            'window_days': days,
            'site_url': os.environ.get('GSC_SITE_URL', ''),
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'error',
            'error': 'Could not obtain access token from refresh token',
        }

    site_url = os.environ['GSC_SITE_URL']
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)

    try:
        import requests
        # GSC Search Analytics API endpoint
        # https://developers.google.com/webmaster-tools/v1/searchanalytics/query
        url = (
            'https://searchconsole.googleapis.com/webmasters/v3/sites/'
            f'{requests.utils.quote(site_url, safe="")}/searchAnalytics/query'
        )
        body = {
            'startDate': start.isoformat(),
            'endDate': end.isoformat(),
            'dimensions': ['query'],
            'rowLimit': max_rows,
            'dataState': 'final',
        }
        resp = requests.post(
            url,
            json=body,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=30,
        )

        # v5.87.88: Surface Google API error details. raise_for_status()
        # only includes the URL, not the response body. Google's 403/4xx
        # responses include a "reason" field that distinguishes between
        # "API not enabled in this project", "service account lacks
        # access to property", "quota exceeded", etc. We extract that
        # before raising so the admin sees the actionable cause.
        if not resp.ok:
            api_reason = None
            api_message = None
            try:
                err_body = resp.json()
                err_obj = err_body.get('error', {})
                api_message = err_obj.get('message')
                # The first error in the 'errors' array has the most
                # actionable reason code (e.g. "accessNotConfigured")
                errors = err_obj.get('errors') or []
                if errors:
                    api_reason = errors[0].get('reason')
            except Exception:
                pass
            detail = f' [{api_reason}]' if api_reason else ''
            msg = api_message or resp.reason
            raise RuntimeError(
                f'{resp.status_code} {resp.reason}{detail}: {msg}'
            )

        data = resp.json()
        rows = data.get('rows', [])

        queries = []
        total_clicks = 0
        total_impressions = 0
        for r in rows:
            clicks = int(r.get('clicks', 0))
            impr = int(r.get('impressions', 0))
            queries.append({
                'query': r.get('keys', [''])[0],
                'clicks': clicks,
                'impressions': impr,
                'ctr': round(r.get('ctr', 0.0), 4),
                'position': round(r.get('position', 0.0), 1),
            })
            total_clicks += clicks
            total_impressions += impr

        return {
            'queries': queries,
            'total_clicks': total_clicks,
            'total_impressions': total_impressions,
            'window_days': days,
            'site_url': site_url,
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'live',
            'error': None,
        }

    except Exception as e:
        logger.warning(f'GSC fetch failed: {e}')
        return {
            'queries': [],
            'total_clicks': 0,
            'total_impressions': 0,
            'window_days': days,
            'site_url': site_url,
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'error',
            'error': str(e),
        }


# ── Classify and aggregate ───────────────────────────────────────────────

def classify_queries(queries: list[dict]) -> dict[str, Any]:
    """Group queries by intent bucket and aggregate clicks + impressions.

    Returns:
        {
          'intent_breakdown': [
            {'intent': 'cost', 'clicks': 450, 'impressions': 8200,
             'query_count': 24, 'click_share_pct': 38.2},
            ...
          ],
          'top_queries_per_intent': {
            'cost': [{'query': 'how much to fix water damage', 'clicks': 23, ...},
                     ...],
            ...
          },
          'total_clicks': 1234,
          'total_impressions': 56789,
        }
    """
    if not queries:
        return {
            'intent_breakdown': [],
            'top_queries_per_intent': {},
            'total_clicks': 0,
            'total_impressions': 0,
        }

    # Bucket the queries
    buckets: dict[str, list[dict]] = {}
    for q in queries:
        intent = classify_query(q.get('query', ''))
        q_with_intent = dict(q, intent=intent)
        buckets.setdefault(intent, []).append(q_with_intent)

    total_clicks = sum(q.get('clicks', 0) for q in queries)
    total_impressions = sum(q.get('impressions', 0) for q in queries)

    intent_breakdown = []
    top_queries_per_intent: dict[str, list[dict]] = {}
    for intent, items in buckets.items():
        b_clicks = sum(q['clicks'] for q in items)
        b_impr = sum(q['impressions'] for q in items)
        intent_breakdown.append({
            'intent': intent,
            'clicks': b_clicks,
            'impressions': b_impr,
            'query_count': len(items),
            'click_share_pct': round(100 * b_clicks / total_clicks, 1)
                                if total_clicks else 0.0,
            'impr_share_pct': round(100 * b_impr / total_impressions, 1)
                               if total_impressions else 0.0,
        })
        # Top 10 queries within each intent, sorted by clicks
        items_sorted = sorted(items, key=lambda x: x.get('clicks', 0), reverse=True)
        top_queries_per_intent[intent] = items_sorted[:10]

    # Sort buckets by clicks descending so the dominant intent is first
    intent_breakdown.sort(key=lambda x: x['clicks'], reverse=True)

    return {
        'intent_breakdown': intent_breakdown,
        'top_queries_per_intent': top_queries_per_intent,
        'total_clicks': total_clicks,
        'total_impressions': total_impressions,
    }


# ── Combined fetch + classify (one-call admin convenience) ───────────────

def fetch_and_classify(
    days: int = GSC_DEFAULT_DAYS,
    max_rows: int = GSC_DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Fetch top queries and return classified results in one call.

    The shape is what the admin UI consumes directly.
    """
    raw = fetch_top_queries(days=days, max_rows=max_rows)
    if raw.get('data_source') != 'live':
        # Fetch failed — return raw fields with empty classification
        return {
            **raw,
            'intent_breakdown': [],
            'top_queries_per_intent': {},
        }

    classified = classify_queries(raw['queries'])
    return {
        **raw,
        'intent_breakdown': classified['intent_breakdown'],
        'top_queries_per_intent': classified['top_queries_per_intent'],
    }


# ── v5.87.89: Paid (Google Ads) search-term intent analysis ──────────────

def fetch_and_classify_paid(
    days: int = 30,
    max_rows: int = 500,
) -> dict[str, Any]:
    """Fetch Google Ads search terms and classify by intent.

    Mirrors fetch_and_classify but for paid traffic. Useful when organic
    volume (GSC) is too low to draw conclusions — paid converters tend
    to give clearer signal at lower volumes because they self-selected
    by clicking.

    Returns:
        {
          'queries': [...],          # raw search terms with metrics
          'total_clicks': int,
          'total_impressions': int,
          'total_cost': float,
          'total_conversions': float,
          'window_days': int,
          'fetched_at': str,
          'data_source': 'live' | 'not_configured' | 'error',
          'error': None | str,
          'intent_breakdown': [...],
          'top_queries_per_intent': {...},
        }
    """
    fetched_at = datetime.utcnow().isoformat() + 'Z'

    # Lazy import keeps gsc_fetch self-contained when google_ads_sync
    # is unavailable (e.g. in tests without the google-ads SDK installed)
    try:
        from google_ads_sync import fetch_search_terms, is_configured as gads_configured
    except ImportError as e:
        return {
            'queries': [], 'total_clicks': 0, 'total_impressions': 0,
            'total_cost': 0.0, 'total_conversions': 0,
            'window_days': days, 'fetched_at': fetched_at,
            'data_source': 'not_configured',
            'error': f'google_ads_sync unavailable: {e}',
            'intent_breakdown': [], 'top_queries_per_intent': {},
        }

    if not gads_configured():
        return {
            'queries': [], 'total_clicks': 0, 'total_impressions': 0,
            'total_cost': 0.0, 'total_conversions': 0,
            'window_days': days, 'fetched_at': fetched_at,
            'data_source': 'not_configured',
            'error': 'Google Ads env vars not set (GOOGLE_ADS_*)',
            'intent_breakdown': [], 'top_queries_per_intent': {},
        }

    try:
        terms = fetch_search_terms(days=days, max_rows=max_rows)
    except Exception as e:
        logger.warning(f'Paid search-terms fetch failed: {e}')
        return {
            'queries': [], 'total_clicks': 0, 'total_impressions': 0,
            'total_cost': 0.0, 'total_conversions': 0,
            'window_days': days, 'fetched_at': fetched_at,
            'data_source': 'error', 'error': str(e),
            'intent_breakdown': [], 'top_queries_per_intent': {},
        }

    if not terms:
        return {
            'queries': [], 'total_clicks': 0, 'total_impressions': 0,
            'total_cost': 0.0, 'total_conversions': 0,
            'window_days': days, 'fetched_at': fetched_at,
            'data_source': 'live', 'error': None,
            'intent_breakdown': [], 'top_queries_per_intent': {},
        }

    total_clicks = sum(t.get('clicks', 0) for t in terms)
    total_impressions = sum(t.get('impressions', 0) for t in terms)
    total_cost = round(sum(t.get('cost', 0.0) for t in terms), 2)
    total_conversions = sum(t.get('conversions', 0) for t in terms)

    # classify_queries works on this shape too — it reads 'query' and
    # 'clicks', which fetch_search_terms also produces.
    classified = classify_queries(terms)

    return {
        'queries': terms,
        'total_clicks': total_clicks,
        'total_impressions': total_impressions,
        'total_cost': total_cost,
        'total_conversions': total_conversions,
        'window_days': days,
        'fetched_at': fetched_at,
        'data_source': 'live',
        'error': None,
        'intent_breakdown': classified['intent_breakdown'],
        'top_queries_per_intent': classified['top_queries_per_intent'],
    }


# ============================================================================
# v5.89.67: Page-level fetch + audit for SEO content strategy
# ============================================================================

def fetch_top_pages(
    days: int = GSC_DEFAULT_DAYS,
    max_rows: int = 500,
) -> dict[str, Any]:
    """Pull organic search performance grouped by landing page URL.

    Returns same envelope as fetch_top_queries but with 'pages' instead of
    'queries'. Each row:
      {'page': 'https://www.getofferwise.ai/guides/mold-...',
       'path': '/guides/mold-found-home-inspection',
       'clicks': 3,
       'impressions': 42,
       'ctr': 0.0714,
       'position': 12.3}
    """
    if not is_configured():
        return {
            'pages': [],
            'total_clicks': 0,
            'total_impressions': 0,
            'window_days': days,
            'site_url': os.environ.get('GSC_SITE_URL', ''),
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'not_configured',
            'error': f'Missing env vars: {", ".join(missing_env_vars())}',
        }

    access_token = _get_access_token()
    if not access_token:
        return {
            'pages': [],
            'total_clicks': 0,
            'total_impressions': 0,
            'window_days': days,
            'site_url': os.environ.get('GSC_SITE_URL', ''),
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'error',
            'error': 'Could not obtain access token from refresh token',
        }

    site_url = os.environ['GSC_SITE_URL']
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)

    try:
        import requests
        url = (
            'https://searchconsole.googleapis.com/webmasters/v3/sites/'
            f'{requests.utils.quote(site_url, safe="")}/searchAnalytics/query'
        )
        body = {
            'startDate': start.isoformat(),
            'endDate': end.isoformat(),
            'dimensions': ['page'],
            'rowLimit': max_rows,
            'dataState': 'final',
        }
        resp = requests.post(
            url,
            json=body,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=30,
        )

        if not resp.ok:
            api_reason = None
            api_message = None
            try:
                err_body = resp.json()
                err_obj = err_body.get('error', {})
                api_message = err_obj.get('message')
                errors = err_obj.get('errors') or []
                if errors:
                    api_reason = errors[0].get('reason')
            except Exception:
                pass
            detail = f' [{api_reason}]' if api_reason else ''
            msg = api_message or resp.reason
            raise RuntimeError(f'{resp.status_code} {resp.reason}{detail}: {msg}')

        data = resp.json()
        rows = data.get('rows', [])

        pages = []
        total_clicks = 0
        total_impressions = 0
        for r in rows:
            page_url = r.get('keys', [''])[0]
            # Extract path for easier display
            path = page_url
            for prefix in ('https://www.getofferwise.ai', 'https://getofferwise.ai'):
                if page_url.startswith(prefix):
                    path = page_url[len(prefix):] or '/'
                    break
            clicks = int(r.get('clicks', 0))
            impr = int(r.get('impressions', 0))
            pages.append({
                'page': page_url,
                'path': path,
                'clicks': clicks,
                'impressions': impr,
                'ctr': round(r.get('ctr', 0.0), 4),
                'position': round(r.get('position', 0.0), 1),
            })
            total_clicks += clicks
            total_impressions += impr

        return {
            'pages': pages,
            'total_clicks': total_clicks,
            'total_impressions': total_impressions,
            'window_days': days,
            'site_url': site_url,
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'live',
            'error': None,
        }
    except Exception as e:
        import traceback
        return {
            'pages': [],
            'total_clicks': 0,
            'total_impressions': 0,
            'window_days': days,
            'site_url': site_url,
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'error',
            'error': str(e),
            'traceback': traceback.format_exc()[:2000],
        }


def audit_pages(days: int = GSC_DEFAULT_DAYS, max_rows: int = 500) -> dict[str, Any]:
    """Categorize each page by its SEO improvement potential.

    Returns the fetch_top_pages result, plus each page tagged with:
      'category': one of
         - 'almost_winner'  (position 8-20, fix meta+depth to hit page 1)
         - 'low_ctr'        (position 1-7 but CTR < 2% — meta title/desc
                             needs work)
         - 'hidden_gem'     (position 1-7 with decent clicks — keep doing
                             what works, consider expanding)
         - 'dead_weight'    (position 30+, low impressions — kill or rewrite)
         - 'no_data'        (under 5 impressions, can't classify yet)
         - 'mid_pack'       (position 20-30, marginal, deprioritize)

    Also returns summary counts per category and a recommended_top_5
    list of pages ranked by 'effort × impact' heuristic (almost-winners
    with high impressions first).
    """
    result = fetch_top_pages(days=days, max_rows=max_rows)

    if result.get('data_source') != 'live':
        # Pass-through error or not-configured; nothing to audit
        result['recommended_top_5'] = []
        result['category_counts'] = {}
        return result

    for p in result['pages']:
        pos = p.get('position', 999)
        impr = p.get('impressions', 0)
        ctr = p.get('ctr', 0.0)
        clicks = p.get('clicks', 0)

        if impr < 5:
            cat = 'no_data'
        elif pos <= 7 and clicks > 0 and ctr >= 0.02:
            cat = 'hidden_gem'
        elif pos <= 7 and ctr < 0.02:
            cat = 'low_ctr'
        elif 7 < pos <= 20:
            cat = 'almost_winner'
        elif 20 < pos <= 30:
            cat = 'mid_pack'
        else:
            cat = 'dead_weight'
        p['category'] = cat

        # Add a heuristic "potential lift" score: estimated extra clicks
        # if this page moved to position 3 (assuming reasonable CTR by pos)
        # CTRs by position: pos1≈30%, pos2≈15%, pos3≈10%, pos5≈5%, pos10≈2%
        target_ctr = 0.10 if cat in ('almost_winner', 'low_ctr') else 0.30 if cat == 'hidden_gem' else 0.0
        if target_ctr > 0:
            est_clicks_at_target = round(impr * target_ctr)
            p['estimated_clicks_at_pos3'] = est_clicks_at_target
            p['potential_lift'] = max(0, est_clicks_at_target - clicks)
        else:
            p['estimated_clicks_at_pos3'] = 0
            p['potential_lift'] = 0

    # Category counts
    from collections import Counter
    cat_counts = dict(Counter(p['category'] for p in result['pages']))

    # Top 5 recommendations: almost_winner with highest potential_lift,
    # then low_ctr with high impressions, then highest-impression dead_weight
    # (the latter being candidates for full rewrites, not just tweaks)
    almost_winners = [p for p in result['pages'] if p['category'] == 'almost_winner']
    almost_winners.sort(key=lambda p: p.get('potential_lift', 0), reverse=True)

    low_ctr = [p for p in result['pages'] if p['category'] == 'low_ctr']
    low_ctr.sort(key=lambda p: p.get('potential_lift', 0), reverse=True)

    top_5 = (almost_winners[:5] + low_ctr[:5])[:5]

    result['recommended_top_5'] = top_5
    result['category_counts'] = cat_counts
    return result


def fetch_queries_for_page(
    page_url: str,
    days: int = GSC_DEFAULT_DAYS,
    max_rows: int = 20,
) -> dict[str, Any]:
    """v5.89.68: Pull top queries that triggered impressions for a specific
    page URL. Used to know what to optimize a page for, instead of guessing.

    Args:
        page_url: full URL e.g. 'https://www.getofferwise.ai/guides/X'
        days: lookback window
        max_rows: top N queries

    Returns same envelope as fetch_top_queries but with 'queries' filtered
    to only those that drove impressions for the given page.
    """
    if not is_configured():
        return {
            'queries': [],
            'page': page_url,
            'window_days': days,
            'data_source': 'not_configured',
            'error': f'Missing env vars: {", ".join(missing_env_vars())}',
        }

    access_token = _get_access_token()
    if not access_token:
        return {
            'queries': [],
            'page': page_url,
            'window_days': days,
            'data_source': 'error',
            'error': 'Could not obtain access token from refresh token',
        }

    site_url = os.environ['GSC_SITE_URL']
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)

    try:
        import requests
        url = (
            'https://searchconsole.googleapis.com/webmasters/v3/sites/'
            f'{requests.utils.quote(site_url, safe="")}/searchAnalytics/query'
        )
        body = {
            'startDate': start.isoformat(),
            'endDate': end.isoformat(),
            'dimensions': ['query'],
            'dimensionFilterGroups': [{
                'filters': [{
                    'dimension': 'page',
                    'operator': 'equals',
                    'expression': page_url,
                }],
            }],
            'rowLimit': max_rows,
            'dataState': 'final',
        }
        resp = requests.post(
            url,
            json=body,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=30,
        )

        if not resp.ok:
            api_reason = None
            api_message = None
            try:
                err_body = resp.json()
                err_obj = err_body.get('error', {})
                api_message = err_obj.get('message')
                errors = err_obj.get('errors') or []
                if errors:
                    api_reason = errors[0].get('reason')
            except Exception:
                pass
            detail = f' [{api_reason}]' if api_reason else ''
            msg = api_message or resp.reason
            raise RuntimeError(f'{resp.status_code} {resp.reason}{detail}: {msg}')

        data = resp.json()
        rows = data.get('rows', [])

        queries = []
        for r in rows:
            queries.append({
                'query': r.get('keys', [''])[0],
                'clicks': int(r.get('clicks', 0)),
                'impressions': int(r.get('impressions', 0)),
                'ctr': round(r.get('ctr', 0.0), 4),
                'position': round(r.get('position', 0.0), 1),
            })

        return {
            'queries': queries,
            'page': page_url,
            'window_days': days,
            'site_url': site_url,
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'data_source': 'live',
            'error': None,
        }
    except Exception as e:
        return {
            'queries': [],
            'page': page_url,
            'window_days': days,
            'data_source': 'error',
            'error': str(e),
        }
