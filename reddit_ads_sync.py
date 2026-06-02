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


class RedditAdsRateLimited(RuntimeError):
    """Reddit Ads API returned HTTP 429 (rate-limited).

    This is transient, not a failure: the next scheduled sync (or a later
    manual run) will pick the window back up. It is a RuntimeError subclass so
    existing broad `except Exception` handlers still catch it, but callers that
    care can treat it distinctly and avoid error-level logging (which pages).
    """
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__(f"Reddit Ads API rate-limited (429); retry_after={retry_after}")


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
        # v5.89.124: HTTP 429 is a transient rate-limit, NOT a failure. The
        # generic logger.error below is captured by the logging→Sentry
        # integration and pages as a high-priority error even though the caller
        # (sync_to_db / _ads_job) already tolerates a skipped window. Handle 429
        # first: log at WARNING and raise a typed exception so callers can skip
        # this run cleanly and retry on the next scheduled sync. Reddit may send
        # a Retry-After header; we surface it but do not busy-retry here (the
        # 6-hour _ads_job and the hourly cadence already provide the retry).
        if resp.status_code == 429:
            retry_after = resp.headers.get('Retry-After')
            logger.warning(
                f"Reddit Ads API rate-limited (429); retry_after={retry_after}. "
                f"Skipping this window — next scheduled sync will retry."
            )
            raise RedditAdsRateLimited(retry_after)
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

    # v5.87.76: fetch 1 day at a time. Reddit's report endpoint without a
    # date breakdown (which we can't use — API rejects it as 400) returns
    # ONE aggregated row covering the requested window. So a 3-day request
    # gives back 1 row of 3-day totals, dated to `end`. Successive 6-hour
    # syncs would write overlapping aggregated totals, multiplying recent
    # days' spend by 2-3x. Looping per-day means each response represents
    # exactly one day's metrics.
    rows: list[dict] = []
    fetch_errors: list[str] = []
    rate_limited = False
    rate_limited_retry_after = None
    cur = start
    while cur <= end:
        try:
            day_rows = _fetch_report(access_token, cur, cur)
            # Each call returns at most 1 aggregated row. Force its date to
            # this iteration's date so the upsert key is unambiguous.
            for r in day_rows:
                if isinstance(r, dict):
                    r['date'] = cur.isoformat()
                    rows.append(r)
        except RedditAdsRateLimited as e:
            # Transient — Reddit is throttling us. No point walking the rest of
            # the window (we'd just get more 429s); stop and let the next
            # scheduled sync retry. Already logged at WARNING in _fetch_report.
            rate_limited = True
            rate_limited_retry_after = e.retry_after
            break
        except Exception as e:
            err_str = str(e)
            # Reddit returns scalar 0 for days with no data — not an error
            if err_str.strip() in ('0', 'None', ''):
                logger.info(f"Reddit Ads: no spend on {cur} (API returned empty)")
            else:
                logger.warning(f"Reddit Ads: fetch failed for {cur}: {e}")
                fetch_errors.append(f'{cur}: {e}')
        cur += timedelta(days=1)

    # Rate-limited with nothing fetched → report it distinctly (not an error),
    # so the admin endpoint returns 200 with a clear message and the scheduler
    # logs it at INFO. If we DID fetch some days before the throttle, fall
    # through and persist them — partial data is still useful.
    if not rows and rate_limited:
        return {
            'status':      'rate_limited',
            'rows_synced': 0,
            'retry_after': rate_limited_retry_after,
            'reason':      'Reddit Ads API rate-limited (429); will retry on the next scheduled sync.',
        }

    # Tolerate partial failures: if SOME days fetched and some didn't, we
    # still write the days we have. Only abort if ALL days failed AND we
    # got nothing.
    if not rows and fetch_errors:
        return {
            'status':      'error',
            'reason':      f'all per-day fetches failed: {fetch_errors[:3]}',
            'rows_synced': 0,
        }
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

        # v5.89.125: Reddit Ads API v3 returns `spend` in MICROCURRENCY
        # (millionths of the account currency), confirmed against a live report:
        # spend=99575446 for 12,954 impressions / 62 clicks on a paused traffic
        # campaign → 99575446 / 1e6 = $99.58 (~$1.61 CPC, sane). The previous
        # code assumed dollars and only divided by 1e6 as a ">10,000 looks like
        # micros" safety net — which SILENTLY corrupted any genuinely small day:
        # e.g. $0.008 = 8000 micros is under the threshold and would have been
        # stored as $8,000. clicks and impressions are raw counts (62, 12954),
        # NOT scaled, so only spend is converted.
        REDDIT_MICRO = 1_000_000
        raw_spend = (metrics_src.get('spend') or row.get('spend') or 0)
        logger.info(f"Reddit Ads row: date={row_date} raw_spend={raw_spend} "
                    f"type={type(raw_spend).__name__} keys={list(row.keys())}")

        spend = float(raw_spend or 0) / REDDIT_MICRO

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
