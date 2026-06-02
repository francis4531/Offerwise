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


def fetch_search_terms(days=30, max_rows=500):
    """v5.87.89: Fetch user search terms (actual queries that triggered ads).

    Different from fetch_daily_metrics, which is campaign-level. This pulls
    the search_term_view resource, which is the actual query the user typed
    that matched our keywords. Aggregated over the window so each unique
    query gets one row with totals.

    Returns list of dicts:
      [{ 'query': 'cost to fix water damage', 'clicks': 12, 'impressions': 88,
         'cost': 4.32, 'conversions': 1, 'ctr': 0.136, 'avg_cpc': 0.36 }, ...]

    The data answers: "what did paid converters actually search for, before
    they clicked our ad?" — much higher signal than organic GSC at low
    organic volumes.
    """
    if not is_configured():
        logger.info("Google Ads not configured — skipping search terms fetch")
        return []

    client = _get_client()
    if not client:
        return []

    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    # GAQL: aggregate by search term across the window. Order by clicks desc
    # so the most-clicked queries show first; cap at max_rows for safety.
    query = f"""
        SELECT
            search_term_view.search_term,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.ctr,
            metrics.average_cpc
        FROM search_term_view
        WHERE segments.date BETWEEN '{start_str}' AND '{end_str}'
        ORDER BY metrics.clicks DESC
        LIMIT {max_rows}
    """

    try:
        ga_service = client.get_service("GoogleAdsService")
        response = ga_service.search(customer_id=CUSTOMER_ID, query=query)

        # The API returns one row per (search_term, day, ad_group). Aggregate
        # by search_term across days.
        agg = {}
        for row in response:
            term = row.search_term_view.search_term
            if term not in agg:
                agg[term] = {
                    'query': term, 'clicks': 0, 'impressions': 0,
                    'cost': 0.0, 'conversions': 0,
                }
            agg[term]['clicks'] += row.metrics.clicks
            agg[term]['impressions'] += row.metrics.impressions
            agg[term]['cost'] += row.metrics.cost_micros / 1_000_000
            agg[term]['conversions'] += row.metrics.conversions

        # Compute derived metrics per aggregated row
        results = []
        for r in agg.values():
            r['ctr'] = round(r['clicks'] / r['impressions'], 4) if r['impressions'] else 0.0
            r['avg_cpc'] = round(r['cost'] / r['clicks'], 2) if r['clicks'] else 0.0
            r['cost'] = round(r['cost'], 2)
            results.append(r)

        # Sort by clicks descending
        results.sort(key=lambda x: x['clicks'], reverse=True)

        logger.info(f"📊 Google Ads: fetched {len(results)} unique search terms over {days}d")
        return results

    except Exception as e:
        logger.error(f"❌ Google Ads search-terms query failed: {e}")
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


def fetch_campaign_diagnostics():
    """v5.89.66: Pull campaign-level diagnostic data from Google Ads.

    Different from fetch_daily_metrics (which is performance numbers).
    This returns serving health: status, primary status reasons (why a
    campaign might be 'Limited' or 'Not eligible'), bid strategy, budget,
    and conversion-action linkage. Used by /api/admin/google-ads-diagnostics
    to surface in admin UI.

    Returns dict:
      {
        'configured': bool,
        'customer_id': str,
        'campaigns': [
          { 'id', 'name', 'status', 'primary_status', 'primary_status_reasons',
            'serving_status', 'bidding_strategy_type', 'budget_micros',
            'budget_dollars', 'optimization_score', 'advertising_channel_type' },
          ...
        ],
        'conversion_actions': [
          { 'id', 'name', 'status', 'category', 'counting_type',
            'click_through_lookback_window_days',
            'view_through_lookback_window_days', 'all_conversions_last_30d' },
          ...
        ],
        'error': str or None,
      }
    """
    if not is_configured():
        return {
            'configured': False,
            'error': 'Google Ads API not configured. Check env vars.',
            'campaigns': [],
            'conversion_actions': [],
        }

    client = _get_client()
    if not client:
        return {
            'configured': True,
            'error': 'google-ads package not installed or client failed.',
            'campaigns': [],
            'conversion_actions': [],
        }

    out = {
        'configured': True,
        'customer_id': CUSTOMER_ID[:4] + '****' + CUSTOMER_ID[-2:] if len(CUSTOMER_ID) >= 6 else None,
        'campaigns': [],
        'conversion_actions': [],
        'error': None,
    }

    try:
        ga_service = client.get_service("GoogleAdsService")

        # --- Campaigns: status + serving health -------------------------
        campaigns_query = """
            SELECT
                campaign.id,
                campaign.name,
                campaign.status,
                campaign.primary_status,
                campaign.primary_status_reasons,
                campaign.serving_status,
                campaign.bidding_strategy_type,
                campaign.advertising_channel_type,
                campaign.optimization_score,
                campaign_budget.amount_micros
            FROM campaign
            WHERE campaign.status != 'REMOVED'
            ORDER BY campaign.status, campaign.name
        """

        try:
            response = ga_service.search(customer_id=CUSTOMER_ID, query=campaigns_query)
            for row in response:
                budget_micros = getattr(row.campaign_budget, 'amount_micros', 0) or 0
                # primary_status_reasons is a repeated enum
                reasons = []
                try:
                    for r in row.campaign.primary_status_reasons:
                        reasons.append(r.name if hasattr(r, 'name') else str(r))
                except Exception:
                    pass
                out['campaigns'].append({
                    'id': str(row.campaign.id),
                    'name': row.campaign.name,
                    'status': row.campaign.status.name if hasattr(row.campaign.status, 'name') else str(row.campaign.status),
                    'primary_status': getattr(row.campaign.primary_status, 'name', str(row.campaign.primary_status)) if hasattr(row.campaign, 'primary_status') else 'UNKNOWN',
                    'primary_status_reasons': reasons,
                    'serving_status': getattr(row.campaign.serving_status, 'name', str(row.campaign.serving_status)) if hasattr(row.campaign, 'serving_status') else 'UNKNOWN',
                    'bidding_strategy_type': getattr(row.campaign.bidding_strategy_type, 'name', str(row.campaign.bidding_strategy_type)),
                    'advertising_channel_type': getattr(row.campaign.advertising_channel_type, 'name', str(row.campaign.advertising_channel_type)),
                    'optimization_score': round(float(getattr(row.campaign, 'optimization_score', 0) or 0), 3),
                    'budget_micros': budget_micros,
                    'budget_dollars': round(budget_micros / 1_000_000, 2) if budget_micros else 0,
                })
        except Exception as e:
            out['error'] = f"Campaign query failed: {e}"
            logger.warning(f"⚠️ Diagnostic campaign query failed: {e}")

        # --- Conversion actions: are they recording? --------------------
        # If a conversion action exists in Google Ads UI but isn't recording
        # conversions, this is where we'll see it. The conversion_label
        # we have in env (oGw1CJOl04wcEPvUh50D) should match the
        # 'tag_snippets' or 'resource_name' of one of these rows.
        conv_query = """
            SELECT
                conversion_action.id,
                conversion_action.name,
                conversion_action.status,
                conversion_action.category,
                conversion_action.counting_type,
                conversion_action.click_through_lookback_window_days,
                conversion_action.view_through_lookback_window_days,
                conversion_action.tag_snippets,
                conversion_action.resource_name
            FROM conversion_action
            WHERE conversion_action.status != 'REMOVED'
            ORDER BY conversion_action.name
        """

        try:
            response = ga_service.search(customer_id=CUSTOMER_ID, query=conv_query)
            for row in response:
                ca = row.conversion_action
                # Extract the conversion label from tag_snippets if available
                label_hint = ''
                try:
                    for ts in ca.tag_snippets:
                        et = getattr(ts, 'event_snippet', '') or ''
                        # event_snippet typically contains "send_to: 'AW-XXX/LABEL'"
                        if 'send_to' in et:
                            label_hint = et.split('send_to')[1][:200]
                            break
                except Exception:
                    pass
                out['conversion_actions'].append({
                    'id': str(ca.id),
                    'name': ca.name,
                    'status': getattr(ca.status, 'name', str(ca.status)),
                    'category': getattr(ca.category, 'name', str(ca.category)),
                    'counting_type': getattr(ca.counting_type, 'name', str(ca.counting_type)),
                    'click_through_lookback_window_days': int(getattr(ca, 'click_through_lookback_window_days', 0) or 0),
                    'view_through_lookback_window_days': int(getattr(ca, 'view_through_lookback_window_days', 0) or 0),
                    'label_hint': label_hint.strip()[:200],
                    'resource_name': ca.resource_name,
                })
        except Exception as e:
            logger.warning(f"⚠️ Diagnostic conversion-action query failed: {e}")
            # Don't overwrite earlier error if present
            if not out['error']:
                out['error'] = f"Conversion action query failed: {e}"

    except Exception as e:
        out['error'] = f"Diagnostic fetch failed: {e}"
        logger.error(f"❌ Diagnostic fetch failed: {e}")

    return out
