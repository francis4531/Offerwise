"""
Reddit Ads API v3 — Automatic Daily Spend Sync
================================================
Pulls account-level metrics (impressions, clicks, spend, conversions)
from the Reddit Ads API v3 and writes them into GTMAdPerformance rows.

Runs as part of the existing _ads_job scheduler every 6 hours.

Required env vars:
  REDDIT_ADS_CLIENT_ID      — App ID from ads.reddit.com Developer Applications
  REDDIT_ADS_CLIENT_SECRET  — Secret from ads.reddit.com Developer Applications
  REDDIT_ADS_REFRESH_TOKEN  — OAuth2 refresh token (permanent, generated once)
  REDDIT_ADS_ACCOUNT_ID     — Ad account ID (e.g. a2_ihx65g62d5n6)
"""

import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
# NOTE: credentials are intentionally NOT captured at import time.
# Reading them lazily (inside functions) ensures env vars set on Render
# after the last deploy are always picked up without requiring a redeploy.

USER_AGENT = 'OfferWise/1.0 (by /u/offerwiseAi)'
TOKEN_URL  = 'https://www.reddit.com/api/v1/access_token'
ADS_BASE   = 'https://ads-api.reddit.com/api/v3'


def _creds():
    """Read credentials fresh from env at call time — never cached at module level."""
    return {
        'client_id':     os.environ.get('REDDIT_ADS_CLIENT_ID', '').strip(),
        'client_secret': os.environ.get('REDDIT_ADS_CLIENT_SECRET', '').strip(),
        'refresh_token': os.environ.get('REDDIT_ADS_REFRESH_TOKEN', '').strip(),
        'account_id':    os.environ.get('REDDIT_ADS_ACCOUNT_ID', '').strip(),
    }


def is_configured():
    """Return True if all required env vars are set."""
    c = _creds()
    return all([c['client_id'], c['client_secret'], c['refresh_token'], c['account_id']])


def _get_access_token() -> str:
    """Exchange refresh token for a fresh access token."""
    import requests as _req
    c = _creds()
    resp = _req.post(
        TOKEN_URL,
        auth=(c['client_id'], c['client_secret']),
        headers={'User-Agent': USER_AGENT},
        data={
            'grant_type':    'refresh_token',
            'refresh_token': c['refresh_token'],
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if 'access_token' not in data:
        raise RuntimeError(f"Token refresh failed: {data}")
    return data['access_token']


def _fetch_report(access_token: str, start: date, end: date) -> list[dict]:
    """
    Fetch daily performance report from Reddit Ads API v3.
    Returns list of dicts with keys: date, spend, clicks, impressions.

    Reddit Ads API v3 report endpoint requires:
    - breakdown: ["date"] to get per-day rows (without this you get ONE aggregated row with no date)
    - fields: list of metric names
    - date_range: {"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"}
    """
    import requests as _req
    import json as _json

    c = _creds()
    url = f"{ADS_BASE}/ad_accounts/{c['account_id']}/reports"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'User-Agent':    USER_AGENT,
        'Content-Type':  'application/json',
    }

    # v3 payload — ends_at MUST be T23:00:00Z (hourly granularity, API enforces this)
    # no breakdown field (API rejects with 400), no extra fields beyond what we store
    payload = {
        'data': {
            'starts_at': start.isoformat() + 'T00:00:00Z',
            'ends_at':   end.isoformat()   + 'T23:00:00Z',
            'fields':    ['spend', 'clicks', 'impressions'],
        }
    }

    logger.info(f"Reddit Ads report request to {url}: {_json.dumps(payload)}")
    resp = _req.post(url, json=payload, headers=headers, timeout=30)

    # Log every non-2xx response in full for debugging
    if not resp.ok:
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text[:1000]
        logger.error(f"Reddit Ads API error {resp.status_code}: {err_body}")
        if resp.status_code == 401:
            raise RuntimeError("Reddit Ads: Unauthorized — check CLIENT_ID/SECRET/REFRESH_TOKEN")
        if resp.status_code == 403:
            raise RuntimeError("Reddit Ads: Forbidden — check ACCOUNT_ID and account permissions")
        if resp.status_code == 400:
            raise RuntimeError(f"Reddit Ads 400: {err_body}")
        resp.raise_for_status()

    raw = resp.text
    logger.info(f"Reddit Ads raw response ({resp.status_code}): {raw[:800]}")

    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"Reddit Ads: could not parse JSON: {raw[:300]}")

    # Handle scalar (Reddit returns 0 when no data)
    if not isinstance(data, (dict, list)):
        logger.info(f"Reddit Ads: scalar response '{data}' — no spend data")
        return []

    # v3 wraps everything in {"data": { ... }}
    # Unwrap the outer data envelope first if present
    if isinstance(data, dict) and 'data' in data and isinstance(data['data'], dict):
        inner = data['data']
        # rows are in inner['data'] (yes, nested) or inner['rows'] or inner['results']
        rows = inner.get('data') or inner.get('metrics') or inner.get('rows') or inner.get('results') or []
        # Some v3 responses put rows directly in the outer data array
        if not rows and isinstance(data.get('data'), list):
            rows = data['data']
    elif isinstance(data, dict):
        rows = data.get('data') or data.get('results') or data.get('rows') or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []

    if not rows:
        logger.warning(f"Reddit Ads: no rows extracted. Full response: {_json.dumps(data)[:800]}")

    logger.info(f"Reddit Ads: {len(rows)} rows for {start} → {end}. Sample: {rows[0] if rows else 'none'}")

    # Normalise rows — v3 DATE breakdown wraps each row as:
    # {"dimensions": {"DATE": "2026-03-27"}, "metrics": {"spend": 1.23, ...}}
    # OR flat: {"date": "2026-03-27", "spend": 1.23, ...}
    normalised = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        # Handle nested dimensions/metrics structure (v3 DATE breakdown)
        if 'dimensions' in row and 'metrics' in row:
            dims    = row.get('dimensions', {})
            metrics = row.get('metrics', {})
            flat = {**metrics}
            flat['date'] = (dims.get('DATE') or dims.get('date') or
                            dims.get('day') or end.isoformat())
            row = flat

        # Try every possible date field key
        raw_date = (row.get('date') or row.get('report_date') or
                    row.get('day') or row.get('dimension_date') or
                    row.get('starts_at', ''))
        if raw_date:
            row['date'] = str(raw_date)[:10]
        else:
            logger.info(f"Reddit Ads: aggregate row (no date). Keys={list(row.keys())}")
            row['date'] = end.isoformat()

        normalised.append(row)

    return normalised


def sync_to_db(db_session, lookback_days: int = 3) -> dict:
    """
    Sync Reddit Ads spend for the last `lookback_days` days into GTMAdPerformance.
    Uses upsert logic (delete + insert) to handle late-arriving data corrections.
    Returns a summary dict.
    """
    if not is_configured():
        return {'status': 'skipped', 'reason': 'not_configured'}

    try:
        from models import GTMAdPerformance
    except ImportError:
        return {'status': 'error', 'reason': 'GTMAdPerformance model not found'}

    try:
        access_token = _get_access_token()
    except Exception as e:
        logger.error(f"Reddit Ads token refresh failed: {e}")
        return {'status': 'error', 'reason': f'token_refresh: {e}'}

    today     = date.today()
    start     = today - timedelta(days=lookback_days)
    end       = today - timedelta(days=1)  # yesterday is always complete

    try:
        rows = _fetch_report(access_token, start, end)
    except Exception as e:
        err_str = str(e)
        # Reddit returns 0 for no data — not a real error
        if err_str.strip() in ('0', 'None', ''):
            logger.info("Reddit Ads: no spend data for this period (API returned empty)")
            return {'status': 'ok', 'rows_synced': 0, 'spend': 0}
        logger.error(f"Reddit Ads report fetch failed: {e}")
        return {'status': 'error', 'reason': f'report_fetch: {e}'}

    if not rows:
        return {'status': 'ok', 'rows_synced': 0, 'spend': 0}

    total_spend = 0
    rows_synced = 0

    logger.info(f"Reddit Ads: processing {len(rows)} rows. First row keys: {list(rows[0].keys()) if rows else []}")
    logger.info(f"Reddit Ads: first row sample: {rows[0] if rows else {}}")

    for row in rows:
        if not isinstance(row, dict):
            logger.warning(f"Reddit Ads: skipping non-dict row: {row}")
            continue

        # _fetch_report normaliser already sets row['date'] for aggregate rows
        # Try every possible date field, fall back to end of period
        raw_date = (row.get('date') or row.get('report_date') or
                    row.get('day') or row.get('starts_at', ''))
        if not raw_date:
            logger.warning(f"Reddit Ads: row has no date field, using end date. Keys={list(row.keys())}")
            raw_date = end.isoformat()

        try:
            row_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            row_date = end

        # v3 may return metrics nested under 'metrics' key or flat
        # _fetch_report normaliser flattens dimensions/metrics, but be defensive
        metrics_src = row.get('metrics', row)  # use nested if present, else whole row

        # v3 spend is in dollars (float), not microdollars
        raw_spend = (metrics_src.get('spend') or row.get('spend') or 0)
        logger.info(f"Reddit Ads row: date={row_date} raw_spend={raw_spend} "
                    f"type={type(raw_spend).__name__} keys={list(row.keys())}")

        spend_raw = float(raw_spend or 0)
        # Safety: if spend > 10,000 it's probably microdollars (shouldn't happen in v3)
        spend = spend_raw / 1_000_000 if spend_raw > 10_000 else spend_raw

        clicks      = int(metrics_src.get('clicks', 0) or row.get('clicks', 0) or 0)
        impressions = int(metrics_src.get('impressions', 0) or row.get('impressions', 0) or 0)

        # Upsert: remove existing record for this date + channel, then insert fresh
        existing = GTMAdPerformance.query.filter_by(
            channel='reddit_ads',
            date=row_date,
        ).first()

        if existing:
            existing.spend       = spend
            existing.clicks      = clicks
            existing.impressions = impressions
        else:
            record = GTMAdPerformance(
                channel='reddit_ads',
                date=row_date,
                spend=spend,
                clicks=clicks,
                impressions=impressions,
                conversions=0,
                revenue=0,
            )
            db_session.add(record)

        total_spend += spend
        rows_synced += 1

    try:
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        logger.error(f"Reddit Ads DB commit failed: {e}")
        return {'status': 'error', 'reason': f'db_commit: {e}'}

    logger.info(
        f"✅ Reddit Ads sync: {rows_synced} days synced, "
        f"${total_spend:.2f} total spend ({start} → {end})"
    )
    return {
        'status':      'ok',
        'rows_synced': rows_synced,
        'spend':       round(total_spend, 2),
        'period':      f"{start} → {end}",
    }
