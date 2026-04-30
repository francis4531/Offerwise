"""
Google Ads API Integration — Automatic Daily Spend Sync
=========================================================
Pulls campaign-level metrics (impressions, clicks, cost, conversions)
from the Google Ads API and writes them into GTMAdPerformance rows.

Runs as a background job every 6 hours (deduped by date + channel).

Required env vars:
  GOOGLE_ADS_DEVELOPER_TOKEN   — from Google Ads Manager Account
  GOOGLE_ADS_CLIENT_ID         — OAuth2 client ID
  GOOGLE_ADS_CLIENT_SECRET     — OAuth2 client secret
  GOOGLE_ADS_REFRESH_TOKEN     — OAuth2 refresh token (generated once)
  GOOGLE_ADS_CUSTOMER_ID       — 10-digit customer ID (no dashes)
  GOOGLE_ADS_LOGIN_CUSTOMER_ID — (optional) MCC manager account ID
"""

import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────
# Reuse existing Google OAuth2 credentials (same as Google Sign-In)

DEVELOPER_TOKEN = os.environ.get('GOOGLE_ADS_DEVELOPER_TOKEN', '').strip()
CLIENT_ID = (os.environ.get('GOOGLE_ADS_CLIENT_ID', '') or os.environ.get('GOOGLE_CLIENT_ID', '')).strip()
CLIENT_SECRET = (os.environ.get('GOOGLE_ADS_CLIENT_SECRET', '') or os.environ.get('GOOGLE_CLIENT_SECRET', '')).strip()
REFRESH_TOKEN = os.environ.get('GOOGLE_ADS_REFRESH_TOKEN', '').strip()
CUSTOMER_ID = os.environ.get('GOOGLE_ADS_CUSTOMER_ID', '').replace('-', '')
LOGIN_CUSTOMER_ID = os.environ.get('GOOGLE_ADS_LOGIN_CUSTOMER_ID', '').replace('-', '')


def is_configured():
    """Check if all required Google Ads API credentials are set."""
    return all([DEVELOPER_TOKEN, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, CUSTOMER_ID])


def _get_client():
    """Build a GoogleAdsClient from env vars."""
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError:
        logger.warning("⚠️ google-ads package not installed — run: pip install google-ads")
        return None

    config = {
        'developer_token': DEVELOPER_TOKEN,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': REFRESH_TOKEN,
        'use_proto_plus': True,
    }
    if LOGIN_CUSTOMER_ID:
        config['login_customer_id'] = LOGIN_CUSTOMER_ID

    try:
        return GoogleAdsClient.load_from_dict(config)
    except Exception as e:
        logger.error(f"❌ Google Ads client init failed: {e}")
        return None


def fetch_daily_metrics(target_date=None):
    """
    Fetch campaign-level metrics for a single day from Google Ads API.

    Returns list of dicts:
      [{ 'date': '2026-03-03', 'campaign_name': '...', 'campaign_id': '...',
         'impressions': 123, 'clicks': 45, 'cost': 12.34,
         'conversions': 2, 'ctr': 0.365, 'avg_cpc': 0.27 }, ...]
    """
    if not is_configured():
        logger.info("Google Ads not configured — skipping fetch")
        return []

    client = _get_client()
    if not client:
        return []

    if target_date is None:
        target_date = date.today() - timedelta(days=1)  # Yesterday (data may lag)

    date_str = target_date.strftime('%Y-%m-%d')

    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            segments.date,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.ctr,
            metrics.average_cpc
        FROM campaign
        WHERE segments.date = '{date_str}'
          AND campaign.status != 'REMOVED'
        ORDER BY metrics.cost_micros DESC
    """

    try:
        ga_service = client.get_service("GoogleAdsService")
        response = ga_service.search(customer_id=CUSTOMER_ID, query=query)

        results = []
        for row in response:
            results.append({
                'date': date_str,
                'campaign_id': str(row.campaign.id),
                'campaign_name': row.campaign.name,
                'campaign_status': row.campaign.status.name,
                'impressions': row.metrics.impressions,
                'clicks': row.metrics.clicks,
                'cost': row.metrics.cost_micros / 1_000_000,  # micros → dollars
                'conversions': row.metrics.conversions,
                'ctr': round(row.metrics.ctr, 4),
                'avg_cpc': row.metrics.average_cpc / 1_000_000,
            })

        logger.info(f"📊 Google Ads: fetched {len(results)} campaigns for {date_str}")
        return results

    except Exception as e:
        logger.error(f"❌ Google Ads API query failed: {e}")
        return []


def sync_to_db(db_session, target_date=None):
    """
    Fetch Google Ads metrics and upsert into GTMAdPerformance table.
    Aggregates all campaigns into a single 'google_ads' channel entry per day.
    Returns stats dict.
    """
    from models import GTMAdPerformance

    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    date_str = target_date.strftime('%Y-%m-%d')

    # Check if we already have data for this date
    existing = GTMAdPerformance.query.filter_by(
        channel='google_ads',
        date=target_date
    ).first()

    if existing and existing.impressions > 0:
        logger.info(f"📊 Google Ads data for {date_str} already synced — skipping")
        return {'status': 'already_synced', 'date': date_str}

    # Fetch from API
    campaigns = fetch_daily_metrics(target_date)
    if not campaigns:
        return {'status': 'no_data', 'date': date_str, 'campaigns': 0}

    # Aggregate all campaigns into one row
    total_impressions = sum(c['impressions'] for c in campaigns)
    total_clicks = sum(c['clicks'] for c in campaigns)
    total_cost = sum(c['cost'] for c in campaigns)
    total_conversions = sum(c['conversions'] for c in campaigns)

    if existing:
        # Update existing row (safe — no race condition on update)
        existing.impressions = total_impressions
        existing.clicks      = total_clicks
        existing.spend       = total_cost
        existing.conversions = int(total_conversions)
        try:
            db_session.commit()
        except Exception as e:
            db_session.rollback()
            logger.warning(f"Google Ads update failed (non-critical): {e}")
    else:
        # Use raw SQL upsert to avoid race condition between workers
        try:
            from sqlalchemy import text as _text
            db_session.execute(_text("""
                INSERT INTO gtm_ad_performance
                    (channel, date, impressions, clicks, spend, conversions, revenue, created_at)
                VALUES
                    (:channel, :date, :impressions, :clicks, :spend, :conversions, 0, NOW())
                ON CONFLICT (channel, date) DO UPDATE SET
                    impressions = EXCLUDED.impressions,
                    clicks      = EXCLUDED.clicks,
                    spend       = EXCLUDED.spend,
                    conversions = EXCLUDED.conversions,
                    updated_at  = NOW()
            """), {
                'channel':     'google_ads',
                'date':        target_date,
                'impressions': total_impressions,
                'clicks':      total_clicks,
                'spend':       total_cost,
                'conversions': int(total_conversions),
            })
            db_session.commit()
        except Exception as e:
            db_session.rollback()
            logger.warning(f"Google Ads DB commit failed (non-critical): {e}")

    logger.info(
        f"✅ Google Ads synced for {date_str}: "
        f"{total_impressions} imp, {total_clicks} clicks, "
        f"${total_cost:.2f} spend, {total_conversions:.0f} conv "
        f"({len(campaigns)} campaigns)"
    )
    return {
        'status': 'synced',
        'date': date_str,
        'campaigns': len(campaigns),
        'impressions': total_impressions,
        'clicks': total_clicks,
        'spend': round(total_cost, 2),
        'conversions': int(total_conversions),
    }


def backfill(db_session, days=7):
    """Sync the last N days of Google Ads data."""
    results = []
    for i in range(days, 0, -1):
        target = date.today() - timedelta(days=i)
        result = sync_to_db(db_session, target)
        results.append(result)
    return results
