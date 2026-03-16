"""
OfferWise Conversion Intel v1.0
================================
GA4 integration placeholder, funnel tracking, and ad performance aggregation.

This is a skeleton that defines the data structures and API surface.
Full GA4 Data API integration requires a service account JSON key
set in GOOGLE_ANALYTICS_KEY_JSON env var.

Funnel stages:
    visit → game_play → email_capture → signup → analysis → purchase

Ad channels tracked:
    - Google Ads (via GA4 utm_source/medium)
    - Reddit Ads (via GA4 utm_source/medium)
    - Organic / Direct
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# GA4 property ID (set in environment)
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "")
GA4_KEY_JSON = os.environ.get("GOOGLE_ANALYTICS_KEY_JSON", "")

FUNNEL_STAGES = [
    # Stage 1: Top of funnel (free tools)
    "visit",                # Any page load
    "risk_check_start",     # Started a Risk Check
    "risk_check_complete",  # Got Risk Check results
    "truth_check_start",    # Started a Truth Check
    "truth_check_complete", # Got Truth Check results
    # Stage 2: Email capture / signup
    "email_capture",        # Waitlist signup or email collected
    "signup",               # Created an account (any method)
    # Stage 3: Core product
    "app_page_view",        # Landed on /app (analysis tool)
    "address_entered",      # Typed property address
    "upload_started",       # Began uploading a document
    "upload_complete",      # At least one doc uploaded
    "analysis_started",     # Clicked analyze / processing began
    "analysis_complete",    # Got results back
    # Monetization
    "pricing_view",         # Viewed pricing page
    "purchase",             # Completed payment
]

AD_CHANNELS = ["google_ads", "reddit_ads", "organic", "direct", "referral"]


# ---------------------------------------------------------------------------
# GA4 Data API integration (placeholder)
# ---------------------------------------------------------------------------

def _get_ga4_client():
    """Initialize the GA4 Data API client.
    
    Requires:
        pip install google-analytics-data
        GOOGLE_ANALYTICS_KEY_JSON env var with service account JSON
    
    Returns None if not configured.
    """
    if not GA4_PROPERTY_ID:
        logger.debug("GA4 not configured: GA4_PROPERTY_ID env var not set")
        return None
    if not GA4_KEY_JSON:
        logger.debug("GA4 not configured: GOOGLE_ANALYTICS_KEY_JSON env var not set")
        return None
    
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.oauth2 import service_account
        
        creds_info = json.loads(GA4_KEY_JSON)
        credentials = service_account.Credentials.from_service_account_info(creds_info)
        client = BetaAnalyticsDataClient(credentials=credentials)
        return client
    except ImportError:
        logger.warning("google-analytics-data package not installed — run: pip install google-analytics-data")
        return None
    except json.JSONDecodeError:
        logger.error("GOOGLE_ANALYTICS_KEY_JSON is not valid JSON")
        return None
    except Exception as e:
        logger.error(f"GA4 client init failed: {e}")
        return None


def get_ga4_status() -> dict:
    """Return detailed GA4 connection status for the admin dashboard.

    Makes a real metadata call to verify credentials are valid and the
    property ID is accessible — not just that the client object was created.
    """
    status = {
        "connected": False,
        "property_id": GA4_PROPERTY_ID or None,
        "has_property_id": bool(GA4_PROPERTY_ID),
        "has_key_json": bool(GA4_KEY_JSON),
        "package_installed": False,
        "api_reachable": False,
        "error": None,
        "service_account_email": None,
    }

    # Extract service account email for fix instructions
    if GA4_KEY_JSON:
        try:
            status["service_account_email"] = json.loads(GA4_KEY_JSON).get("client_email")
        except Exception:
            pass

    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        status["package_installed"] = True
    except ImportError:
        status["error"] = "google-analytics-data package not installed"
        return status

    if not GA4_PROPERTY_ID:
        status["error"] = "GA4_PROPERTY_ID env var not set in Render"
        return status
    if not GA4_KEY_JSON:
        status["error"] = "GOOGLE_ANALYTICS_KEY_JSON env var not set in Render"
        return status

    client = _get_ga4_client()
    if not client:
        status["error"] = "Failed to build GA4 client — check GOOGLE_ANALYTICS_KEY_JSON format"
        return status

    # Make a real lightweight API call: fetch property metadata.
    # This confirms the service account has the Analytics Viewer role on the property.
    try:
        from google.analytics.data_v1beta.types import GetMetadataRequest
        metadata = client.get_metadata(
            request=GetMetadataRequest(name=f"properties/{GA4_PROPERTY_ID}/metadata")
        )
        status["api_reachable"] = True
        status["connected"] = True
        status["metric_count"] = len(metadata.metrics)
    except Exception as e:
        err = str(e)
        if "PERMISSION_DENIED" in err or "403" in err:
            status["error"] = (
                "Permission denied — grant the service account 'Viewer' access "
                "in GA4 Admin → Property Access Management"
            )
        elif "NOT_FOUND" in err or "404" in err:
            status["error"] = (
                f"Property {GA4_PROPERTY_ID} not found — verify GA4_PROPERTY_ID "
                "matches your GA4 property (numeric ID, not Measurement ID)"
            )
        else:
            status["error"] = f"GA4 API error: {err[:200]}"

    return status


def fetch_ga4_funnel(days: int = 30) -> dict | None:
    """Fetch funnel event counts from GA4 via the Data API.

    Queries event counts for each FUNNEL_STAGES event name, grouped by
    sessionSource so we can attribute each stage to a channel.

    Returns:
        {
            "stages": {"visit": 1234, "signup": 56, ...},
            "by_channel": {"google_ads": {"visit": 300, ...}, ...},
        }
        or None if GA4 is not configured / unreachable.
    """
    client = _get_ga4_client()
    if not client:
        return None

    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Dimension, Metric,
            FilterExpression, Filter, FilterExpressionList,
        )

        # Map our internal stage names to the GA4 event names we fire
        # page_view is the proxy for "visit"; all others match exactly.
        STAGE_TO_EVENT = {
            "visit":               "page_view",
            "risk_check_start":    "risk_check_start",
            "risk_check_complete": "risk_check_complete",
            "truth_check_start":   "truth_check_start",
            "truth_check_complete":"truth_check_complete",
            "email_capture":       "email_capture",
            "signup":              "sign_up",
            "app_page_view":       "app_page_view",
            "address_entered":     "address_entered",
            "upload_started":      "upload_started",
            "upload_complete":     "upload_complete",
            "analysis_started":    "analysis_started",
            "analysis_complete":   "analysis_complete",
            "pricing_view":        "pricing_view",
            "purchase":            "purchase",
        }
        event_to_stage = {v: k for k, v in STAGE_TO_EVENT.items()}

        request = RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            dimensions=[
                Dimension(name="eventName"),
                Dimension(name="sessionSource"),
                Dimension(name="sessionMedium"),
            ],
            metrics=[Metric(name="eventCount")],
            date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
            # Filter to only the events we care about
            dimension_filter=FilterExpression(
                or_group=FilterExpressionList(
                    expressions=[
                        FilterExpression(
                            filter=Filter(
                                field_name="eventName",
                                string_filter=Filter.StringFilter(
                                    match_type=Filter.StringFilter.MatchType.EXACT,
                                    value=event_name,
                                )
                            )
                        )
                        for event_name in STAGE_TO_EVENT.values()
                    ]
                )
            ),
        )

        response = client.run_report(request)

        stages = {}
        by_channel = {}

        for row in response.rows:
            event_name = row.dimension_values[0].value
            source      = row.dimension_values[1].value
            medium      = row.dimension_values[2].value
            count       = int(row.metric_values[0].value or 0)

            stage = event_to_stage.get(event_name)
            if not stage:
                continue

            # Aggregate total stage counts
            stages[stage] = stages.get(stage, 0) + count

            # Aggregate by channel
            channel = _normalize_channel(source, medium)
            if channel not in by_channel:
                by_channel[channel] = {}
            by_channel[channel][stage] = by_channel[channel].get(stage, 0) + count

        logger.info(
            "GA4 funnel fetch: %d stage types, %d channels over %d days",
            len(stages), len(by_channel), days,
        )
        return {"stages": stages, "by_channel": by_channel}

    except Exception as e:
        logger.error("GA4 funnel fetch failed: %s", e)
        return None


def fetch_ga4_channel_performance(days: int = 30) -> dict | None:
    """Fetch per-channel session and conversion data from GA4 Data API."""
    client = _get_ga4_client()
    if not client:
        return None
    
    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Dimension, Metric
        )
        
        request = RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            dimensions=[
                Dimension(name="sessionSource"),
                Dimension(name="sessionMedium"),
            ],
            metrics=[
                Metric(name="sessions"),
                Metric(name="conversions"),
                Metric(name="totalRevenue"),
            ],
            date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        )
        response = client.run_report(request)
        
        channels = {}
        for row in response.rows:
            source = row.dimension_values[0].value
            medium = row.dimension_values[1].value
            channel = _normalize_channel(source, medium)

            if channel not in channels:
                channels[channel] = {"sessions": 0, "conversions": 0, "revenue": 0.0}

            channels[channel]["sessions"] += int(row.metric_values[0].value or 0)
            channels[channel]["conversions"] += int(row.metric_values[1].value or 0)
            channels[channel]["revenue"] += float(row.metric_values[2].value or 0)

        return channels
    except Exception as e:
        logger.error(f"GA4 channel performance fetch failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Local funnel event tracking (DB-backed, always available)
# ---------------------------------------------------------------------------

def record_funnel_event(db_session, models, stage: str, source: str = "direct",
                        medium: str = "none", user_id: int = None,
                        session_id: str = None, metadata: dict = None):
    """Record a funnel event in the local DB.
    
    This runs regardless of GA4 configuration, giving us always-on funnel data.
    """
    FunnelEvent = models["FunnelEvent"]
    
    if stage not in FUNNEL_STAGES:
        logger.warning(f"Unknown funnel stage: {stage}")
        return None
    
    event = FunnelEvent(
        stage=stage,
        source=source,
        medium=medium,
        user_id=user_id,
        session_id=session_id,
        metadata_json=json.dumps(metadata) if metadata else None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.commit()
    return event


def get_funnel_snapshot(db_session, models, days: int = 30) -> dict:
    """Get a funnel snapshot, merging GA4 data with local DB events.

    GA4 is the preferred source when connected — it captures visits and events
    that never touch our backend (e.g., bounced sessions, JS-fired events).
    Local DB events fill in any gaps for backend-only stages.

    Returns:
        {
            "stages": {"visit": 1234, "signup": 56, ...},
            "by_channel": {"google_ads": {"visit": 100, ...}, ...},
            "conversion_rates": {"visit_to_signup": 12.5, ...},
            "period_days": 30,
            "ga4_connected": bool,
            "ga4_property": str | None,
            "data_source": "ga4" | "local" | "ga4+local",
        }
    """
    from sqlalchemy import func
    FunnelEvent = models["FunnelEvent"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # ── Local DB events (always available) ─────────────────────────────────
    local_stages = {}
    rows = (
        db_session.query(FunnelEvent.stage, func.count(FunnelEvent.id))
        .filter(FunnelEvent.created_at >= cutoff)
        .group_by(FunnelEvent.stage)
        .all()
    )
    for stage, cnt in rows:
        local_stages[stage] = cnt

    local_by_channel = {}
    rows = (
        db_session.query(
            FunnelEvent.source, FunnelEvent.stage, func.count(FunnelEvent.id)
        )
        .filter(FunnelEvent.created_at >= cutoff)
        .group_by(FunnelEvent.source, FunnelEvent.stage)
        .all()
    )
    for source, stage, cnt in rows:
        channel = _normalize_channel(source)
        if channel not in local_by_channel:
            local_by_channel[channel] = {}
        local_by_channel[channel][stage] = cnt

    # ── GA4 (preferred when connected) ─────────────────────────────────────
    ga4_data = fetch_ga4_funnel(days=days)
    ga4_connected = bool(_get_ga4_client())

    if ga4_data:
        # GA4 wins on any stage it covers; local DB fills gaps
        stage_counts = dict(local_stages)
        for stage, cnt in ga4_data["stages"].items():
            stage_counts[stage] = max(stage_counts.get(stage, 0), cnt)

        by_channel = dict(local_by_channel)
        for channel, stage_map in ga4_data["by_channel"].items():
            if channel not in by_channel:
                by_channel[channel] = {}
            for stage, cnt in stage_map.items():
                by_channel[channel][stage] = max(
                    by_channel[channel].get(stage, 0), cnt
                )

        data_source = "ga4+local" if local_stages else "ga4"
    else:
        stage_counts = local_stages
        by_channel = local_by_channel
        data_source = "local"

    # ── Conversion rates ────────────────────────────────────────────────────
    conversion_rates = {}
    for i in range(len(FUNNEL_STAGES) - 1):
        curr = stage_counts.get(FUNNEL_STAGES[i], 0)
        nxt = stage_counts.get(FUNNEL_STAGES[i + 1], 0)
        key = f"{FUNNEL_STAGES[i]}_to_{FUNNEL_STAGES[i + 1]}"
        conversion_rates[key] = round(nxt / max(curr, 1) * 100, 1)

    return {
        "stages": stage_counts,
        "by_channel": by_channel,
        "conversion_rates": conversion_rates,
        "period_days": days,
        "ga4_connected": ga4_connected,
        "ga4_property": GA4_PROPERTY_ID or None,
        "data_source": data_source,
    }


def get_ad_performance_summary(db_session, models, days: int = 30) -> dict:
    """Get ad performance summary merging GA4 data with manual spend entries.

    GA4 provides: sessions, conversions, revenue (automatic).
    Google Ads sync / manual entries provide: impressions, clicks, spend.

    Always returns a non-empty channels dict with at least google_ads and
    reddit_ads stubs — so the table renders with correct columns and a
    "no data yet" state for individual cells rather than a blank page.
    """
    AdPerformance = models["AdPerformance"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # ── Seed stubs for the two active channels so the table always renders ──
    ACTIVE_CHANNELS = ("google_ads", "reddit_ads")
    summary = {
        ch: {
            "sessions": 0, "impressions": 0, "clicks": 0, "spend": 0.0,
            "conversions": 0, "revenue": 0.0, "source": "none",
        }
        for ch in ACTIVE_CHANNELS
    }

    # ── GA4 channel data (sessions, conversions, revenue) ──────────────────
    ga4_data = fetch_ga4_channel_performance(days=days)
    if ga4_data:
        for ch, data in ga4_data.items():
            if ch not in summary:
                summary[ch] = {
                    "sessions": 0, "impressions": 0, "clicks": 0, "spend": 0.0,
                    "conversions": 0, "revenue": 0.0, "source": "ga4",
                }
            summary[ch]["sessions"] = data.get("sessions", 0)
            summary[ch]["conversions"] = data.get("conversions", 0)
            summary[ch]["revenue"] = data.get("revenue", 0.0)
            summary[ch]["source"] = "ga4"

    # ── Manual / synced spend data from DB (impressions, clicks, spend) ────
    rows = (
        db_session.query(AdPerformance)
        .filter(AdPerformance.date >= cutoff.date())
        .order_by(AdPerformance.date.desc())
        .all()
    )
    for row in rows:
        ch = row.channel
        if ch not in summary:
            summary[ch] = {
                "sessions": 0, "impressions": 0, "clicks": 0, "spend": 0.0,
                "conversions": 0, "revenue": 0.0, "source": "manual",
            }
        summary[ch]["impressions"] += row.impressions or 0
        summary[ch]["clicks"] += row.clicks or 0
        summary[ch]["spend"] += float(row.spend or 0)
        # Update source label
        if summary[ch]["source"] == "ga4":
            summary[ch]["source"] = "ga4+sync"
        elif summary[ch]["source"] == "none":
            summary[ch]["source"] = "manual"
        # Only pull conversions/revenue from DB rows if GA4 didn't supply them
        if summary[ch]["source"] not in ("ga4", "ga4+sync"):
            summary[ch]["conversions"] += row.conversions or 0
            summary[ch]["revenue"] += float(row.revenue or 0)

    # ── Derived metrics (safe against divide-by-zero) ───────────────────────
    for data in summary.values():
        imp    = data["impressions"] or 0
        clicks = data["clicks"] or 0
        spend  = data["spend"] or 0.0
        conv   = data["conversions"] or 0
        rev    = data["revenue"] or 0.0
        data["ctr"]  = round(clicks / max(imp, 1) * 100, 2) if imp else 0
        data["cpc"]  = round(spend / clicks, 2) if clicks else 0
        data["cpa"]  = round(spend / conv, 2) if conv else 0
        data["roas"] = round(rev / spend, 2) if spend else 0

    # ── GA4 real connectivity check (cached in get_ga4_status) ─────────────
    ga4_status = get_ga4_status()

    return {
        "channels": summary,
        "period_days": days,
        "ga4_connected": ga4_status["connected"],
        "ga4_status": ga4_status,
        "data_source": "ga4+sync" if ga4_data else "sync_only",
    }


def record_ad_performance(db_session, models, channel: str, date,
                          impressions: int = 0, clicks: int = 0,
                          spend: float = 0, conversions: int = 0,
                          revenue: float = 0):
    """Record daily ad performance metrics (for manual entry or API import)."""
    AdPerformance = models["AdPerformance"]
    
    # Upsert: update existing record for this channel+date or create new
    existing = db_session.query(AdPerformance).filter_by(
        channel=channel, date=date
    ).first()
    
    if existing:
        existing.impressions = impressions
        existing.clicks = clicks
        existing.spend = spend
        existing.conversions = conversions
        existing.revenue = revenue
        existing.updated_at = datetime.now(timezone.utc)
    else:
        record = AdPerformance(
            channel=channel, date=date,
            impressions=impressions, clicks=clicks,
            spend=spend, conversions=conversions, revenue=revenue,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(record)
    
    db_session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_channel(source: str, medium: str = "") -> str:
    """Normalize UTM source/medium to a channel name."""
    source = (source or "").lower().strip()
    medium = (medium or "").lower().strip()

    # Reddit (always paid/social)
    if "reddit" in source:
        return "reddit_ads"

    # Google: organic only when medium is explicitly 'organic'
    if "google" in source:
        if medium == "organic":
            return "organic"
        return "google_ads"  # cpc, ppc, paid, empty, unknown → paid

    # Direct
    if source in ("", "direct", "(direct)", "(none)") or medium in ("(none)",):
        return "direct"

    # Explicit organic signals
    if source in ("organic", "(organic)") or medium == "organic":
        return "organic"

    return "referral"


def add_ad_entry(db_session, models, data: dict) -> dict:
    """Add or update a manual ad spend entry.

    Accepts:
        channel, date (YYYY-MM-DD, defaults to today), impressions,
        clicks, spend, conversions, revenue
    """
    from datetime import date as date_type

    AdPerformance = models["AdPerformance"]

    channel = data.get("channel", "").strip()
    if channel not in ("google_ads", "reddit_ads"):
        return {"error": f"Unknown channel: {channel}"}

    date_str = data.get("date")
    if date_str:
        try:
            entry_date = date_type.fromisoformat(date_str)
        except ValueError:
            return {"error": f"Invalid date format: {date_str}"}
    else:
        entry_date = date_type.today()

    record_ad_performance(
        db_session, models,
        channel=channel,
        date=entry_date,
        impressions=int(data.get("impressions", 0)),
        clicks=int(data.get("clicks", 0)),
        spend=float(data.get("spend", 0)),
        conversions=int(data.get("conversions", 0)),
        revenue=float(data.get("revenue", 0)),
    )

    return {
        "status": "saved",
        "channel": channel,
        "date": entry_date.isoformat(),
    }
