"""
OfferWise Agentic Monitoring Engine (v5.75.92)
==============================================
Autonomous background jobs that watch active properties and alert buyers,
realtors, and inspectors when market conditions change — without being asked.

Jobs:
  _job_comps_monitor()      — daily: new comparable sales → leverage recalculation
  _job_earthquake_monitor() — daily: USGS M4.0+ within 50km → structural re-check alert
  _job_price_monitor()      — daily: listing price drop vs asking → updated offer math
  _job_permit_monitor()     — daily: new county permits → seller pre-close repair flag
  fire_buyer_concern_signal() — event-driven: buyer views report → notify professional

All jobs are idempotent. Each watch tracks last_*_check_at so no duplicate alerts.
"""

import logging
import json
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
PT = ZoneInfo('America/Los_Angeles')

RENTCAST_API_KEY = os.environ.get('RENTCAST_API_KEY', '')

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow()

def _get_db():
    from app import db
    return db

def _send(to_email, subject, html, email_type='agent_alert', user_id=None):
    try:
        from email_service import send_email
        send_email(to_email=to_email, subject=subject, html_content=html,
                   email_type=email_type, user_id=user_id)
    except Exception as e:
        logger.warning(f"Alert email failed to {to_email}: {e}")

def _save_alert(watch_id, user_id, alert_type, severity, title, body, detail, email_sent=False):
    """Persist an alert and optionally mark email sent."""
    try:
        from models import AgentAlert
        db = _get_db()
        alert = AgentAlert(
            watch_id=watch_id, user_id=user_id,
            alert_type=alert_type, severity=severity,
            title=title, body=body,
            detail_json=json.dumps(detail) if detail else None,
            email_sent=email_sent,
            email_sent_at=_now() if email_sent else None,
        )
        db.session.add(alert)
        db.session.commit()
        return alert
    except Exception as e:
        logger.error(f"Failed to save alert: {e}")

def _format_price(v):
    if v is None:
        return "N/A"
    return f"${v:,.0f}"


def _notify_linked_professionals(watch, title, body_html, address, emoji="🔔"):
    """
    Notify inspector, agent, and/or contractor linked to this watch.
    Each gets a professionally-framed version of the alert.
    Also saves an AgentAlert record keyed to the professional's user_id.
    """
    try:
        from models import InspectorReport, AgentShare, ContractorLead, Contractor, User, AgentAlert
        db = _get_db()

        recipients = []   # list of (email, user_id, persona)

        # Inspector linked to this watch
        if watch.inspector_report_id:
            report = InspectorReport.query.get(watch.inspector_report_id)
            if report:
                insp_user = User.query.get(report.inspector_user_id)
                if insp_user and insp_user.email:
                    recipients.append((insp_user.email, insp_user.id, 'inspector'))

        # Agent linked to this watch
        if watch.agent_share_id:
            share = AgentShare.query.get(watch.agent_share_id)
            if share:
                agent_user = User.query.get(share.agent_user_id)
                if agent_user and agent_user.email:
                    recipients.append((agent_user.email, agent_user.id, 'agent'))

        # Contractor linked to this watch (claimed the buyer's lead)
        if watch.contractor_lead_id:
            lead = ContractorLead.query.get(watch.contractor_lead_id)
            if lead:
                # Find all contractors who claimed this lead
                from models import ContractorLeadClaim
                claims = ContractorLeadClaim.query.filter_by(
                    lead_id=lead.id, status='claimed'
                ).all()
                for claim in claims:
                    ctor = Contractor.query.get(claim.contractor_id)
                    if ctor and ctor.email:
                        # Find User record by email for alert storage
                        ctor_user = User.query.filter_by(email=ctor.email).first()
                        uid = ctor_user.id if ctor_user else None
                        recipients.append((ctor.email, uid, 'contractor'))

        seen_emails = set()
        for email, uid, persona in recipients:
            if email in seen_emails:
                continue
            seen_emails.add(email)
            # Add persona-specific portal CTA to professional alerts
            portal_urls = {
                'inspector': 'https://www.getofferwise.ai/inspector-portal',
                'agent': 'https://www.getofferwise.ai/agent-portal',
                'contractor': 'https://www.getofferwise.ai/contractor-portal',
            }
            portal_url = portal_urls.get(persona, 'https://www.getofferwise.ai/settings')
            portal_label = {
                'inspector': 'View in inspector portal →',
                'agent': 'View in agent portal →',
                'contractor': 'View in contractor portal →',
            }.get(persona, 'View in your portal →')
            pro_cta = (
                f"<div style='margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,.06);text-align:center;'>"
                f"<a href='{portal_url}' style='display:inline-block;padding:12px 28px;background:linear-gradient(90deg,#f97316,#f59e0b);color:#fff;text-decoration:none;font-weight:700;font-size:14px;border-radius:9px;'>"
                f"{portal_label}</a></div>"
            )
            _send(email,
                  f"{emoji} {title}",
                  _email_html(title, body_html + pro_cta, address, emoji),
                  email_type='agent_alert', user_id=uid)
            # Save alert record so it appears in their portal
            if uid:
                try:
                    alert = AgentAlert(
                        watch_id=watch.id, user_id=uid,
                        alert_type='market_intel', severity='info',
                        title=title, body=body_html[:500].replace('<','').replace('>',''),
                        email_sent=True, email_sent_at=_now(),
                    )
                    db.session.add(alert)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            logger.info(f"📡 [{persona}] alert sent to {email}: {title[:60]}")

    except Exception as e:
        logger.warning(f"_notify_linked_professionals failed: {e}")

def _email_html(title, body_html, address, alert_emoji="🔔", cta_label=None, cta_url=None):
    return f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:32px 24px;background:#060d1a;color:#f1f5f9;border-radius:16px;">
  <div style="font-size:2rem;margin-bottom:12px;">{alert_emoji}</div>
  <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#f97316;margin-bottom:8px;">OfferWise AI — Property Alert</div>
  <h2 style="font-size:20px;font-weight:800;margin-bottom:8px;color:#f1f5f9;">{title}</h2>
  <div style="font-size:13px;color:#94a3b8;margin-bottom:16px;">📍 {address}</div>
  <div style="background:#0f1e35;border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:20px;font-size:13px;color:#cbd5e1;line-height:1.7;">
    {body_html}
  </div>
  <div style="margin-top:24px;text-align:center;">
    <a href="{cta_url or 'https://www.getofferwise.ai/app?utm_source=offerwatch&utm_medium=email'}"
       style="display:inline-block;padding:14px 32px;background:linear-gradient(90deg,#f97316,#f59e0b);color:#fff;text-decoration:none;font-weight:700;font-size:15px;border-radius:10px;">
      {cta_label or 'Open OfferWise →'}
    </a>
  </div>
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%"
         style="margin-top:12px;background:rgba(249,115,22,.06);border:1px solid rgba(249,115,22,.15);border-radius:8px;">
    <tr><td style="padding:10px 14px;">
      <span style="font-size:11px;color:#94a3b8;">
        💰 Know someone buying a home?
        <a href="https://www.getofferwise.ai/settings?tab=referrals&utm_source=offerwatch&utm_medium=email"
           style="color:#f97316;font-weight:700;">Refer them — you both get a free analysis.</a>
      </span>
    </td></tr>
  </table>
  <div style="margin-top:12px;font-size:11px;color:#475569;text-align:center;">
    OfferWise AI agentic monitoring · {address[:40]}
    &nbsp;·&nbsp; <a href="https://www.getofferwise.ai/settings?utm_source=offerwatch&utm_medium=email" style="color:#475569;">Manage alerts</a>
  </div>
</div>"""


# ── 1. COMPARABLE SALES MONITOR ───────────────────────────────────────────────

def _job_comps_monitor():
    """
    Daily job. For each active watch, fetches current RentCast comps.
    If any comp closed AFTER the analysis date and within 15% of asking price,
    fires an alert with updated leverage calculation.
    """
    logger.info("🏠 [CompsMonitor] Starting comparable sales scan...")
    if not RENTCAST_API_KEY:
        logger.warning("🏠 [CompsMonitor] RENTCAST_API_KEY not set — skipping")
        return

    try:
        from models import PropertyWatch, User
        db = _get_db()
        watches = PropertyWatch.query.filter_by(is_active=True).all()
        logger.info(f"🏠 [CompsMonitor] Scanning {len(watches)} active watches")

        for watch in watches:
            try:
                _check_comps_for_watch(watch, db)
            except Exception as e:
                logger.error(f"🏠 [CompsMonitor] Error on watch {watch.id}: {e}")

        logger.info("🏠 [CompsMonitor] Complete")
    except Exception as e:
        logger.error(f"🏠 [CompsMonitor] Fatal error: {e}", exc_info=True)


def _check_comps_for_watch(watch, db):
    from models import User
    if not RENTCAST_API_KEY:
        return
    # Only check once per day
    if watch.last_comps_check_at and (_now() - watch.last_comps_check_at).total_seconds() < 82800:
        return

    if not watch.asking_price:
        return

    # Fetch comps from RentCast
    try:
        resp = requests.get(
            'https://api.rentcast.io/v1/avm/value',
            params={'address': watch.address},
            headers={'X-Api-Key': RENTCAST_API_KEY},
            timeout=15
        )
        if resp.status_code != 200:
            return
        data = resp.json()
    except Exception as e:
        logger.warning(f"RentCast comps fetch failed for {watch.address}: {e}")
        return

    comps_raw = data.get('comparables', []) or []
    current_avm = data.get('price') or 0

    # Find comps that closed recently (after the watch was created)
    watch_created = watch.created_at
    new_comps = []
    for c in comps_raw:
        sale_date_str = c.get('lastSaleDate', '') or ''
        if not sale_date_str:
            continue
        try:
            sale_date = datetime.fromisoformat(sale_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        except Exception:
            continue
        if sale_date > watch_created:
            sale_price = c.get('lastSalePrice') or c.get('price') or 0
            new_comps.append({
                'address': c.get('formattedAddress', c.get('address', 'Unknown')),
                'sale_price': sale_price,
                'sale_date': sale_date_str[:10],
                'sqft': c.get('squareFootage') or 0,
                'beds': c.get('bedrooms') or 0,
            })

    watch.last_comps_check_at = _now()
    db.session.commit()

    if not new_comps:
        return

    # Check if any comp is meaningfully below asking (buyer leverage signal)
    asking = watch.asking_price
    below_comps = [c for c in new_comps if c['sale_price'] and c['sale_price'] < asking * 0.97]

    if not below_comps:
        return

    # Build alert
    user = db.query(User).get(watch.user_id) if hasattr(db, 'query') else User.query.get(watch.user_id)
    if not user:
        return

    avg_comp = sum(c['sale_price'] for c in below_comps) / len(below_comps)
    diff = asking - avg_comp
    diff_pct = diff / asking * 100

    title = f"New comp closed {diff_pct:.1f}% below your asking price"
    comp_rows = ''.join(
        f"<div style='margin-bottom:8px;padding:8px 12px;background:rgba(255,255,255,.04);border-radius:6px;'>"
        f"<strong style='color:#f1f5f9;'>{_format_price(c['sale_price'])}</strong> — {c['address']} "
        f"<span style='color:#64748b;font-size:11px;'>closed {c['sale_date']}</span></div>"
        for c in below_comps[:3]
    )
    body_html = f"""
    <p style='margin-bottom:12px;'><strong>{len(below_comps)} comparable sale{'s' if len(below_comps)>1 else ''}</strong> closed after your analysis — all below your asking price of <strong>{_format_price(asking)}</strong>.</p>
    {comp_rows}
    <div style='margin-top:14px;padding:12px 14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;'>
      <strong style='color:#f97316;'>Updated leverage:</strong> Market data now supports asking for a price reduction of <strong style='color:#f97316;'>{_format_price(diff)}</strong> ({diff_pct:.1f}%) based on comparable sales.
    </div>"""

    # Notify buyer
    _analysis_url = (f"https://www.getofferwise.ai/app?analysis={watch.analysis_id}&utm_source=comps_alert&utm_medium=email" if watch.analysis_id else "https://www.getofferwise.ai/app?utm_source=comps_alert&utm_medium=email")
    _send(user.email,
          f"📊 Market update: New comps support lower offer on {watch.address[:40]}",
          _email_html(title, body_html, watch.address, "📊", cta_label="Recalculate your offer →", cta_url=_analysis_url),
          user_id=user.id)

    # Notify linked professionals (inspector + agent) with pro-specific framing
    pro_title  = f"Market intel: New comp supports price reduction for your client at {watch.address[:50]}"
    pro_body   = f"""
    <p style='margin-bottom:12px;'>Your client's OfferWise watch detected <strong>{len(below_comps)} new comparable sale{'s' if len(below_comps)>1 else ''}</strong> below asking price. This is actionable leverage for their negotiation.</p>
    {comp_rows}
    <div style='margin-top:14px;padding:12px 14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;'>
      <strong style='color:#f97316;'>Recommended action:</strong> Call your client now. Market data supports requesting <strong>{_format_price(diff)}</strong> off asking ({diff_pct:.1f}%). Your client already has this data — be the one who acts on it first.
    </div>"""
    _notify_linked_professionals(watch, pro_title, pro_body, watch.address, "📊")

    _save_alert(watch.id, user.id, 'new_comp', 'warning', title,
                f"{len(below_comps)} new comps closed below asking price. Avg comp: {_format_price(avg_comp)}",
                {'new_comps': below_comps, 'avg_comp': avg_comp, 'diff': diff, 'diff_pct': diff_pct},
                email_sent=True)

    logger.info(f"🏠 [CompsMonitor] Alert sent to buyer + professionals: {len(below_comps)} new comps for watch {watch.id}")


# ── 2. EARTHQUAKE MONITOR ─────────────────────────────────────────────────────

def _job_earthquake_monitor():
    """
    Daily job. Checks USGS for M4.0+ earthquakes within 50km of any watched
    property in the last 24 hours. Fires structural re-check alert.
    """
    logger.info("🌍 [EarthquakeMonitor] Starting USGS scan...")
    try:
        from models import PropertyWatch, User
        db = _get_db()
        watches = PropertyWatch.query.filter_by(is_active=True).filter(
            PropertyWatch.latitude.isnot(None)
        ).all()
        logger.info(f"🌍 [EarthquakeMonitor] Scanning {len(watches)} geo-located watches")

        for watch in watches:
            try:
                _check_earthquake_for_watch(watch, db)
            except Exception as e:
                logger.error(f"🌍 [EarthquakeMonitor] Error on watch {watch.id}: {e}")

        logger.info("🌍 [EarthquakeMonitor] Complete")
    except Exception as e:
        logger.error(f"🌍 [EarthquakeMonitor] Fatal error: {e}", exc_info=True)


def _check_earthquake_for_watch(watch, db):
    from models import User
    if not watch.latitude or not watch.longitude:
        return
    if watch.last_earthquake_check_at and (_now() - watch.last_earthquake_check_at).total_seconds() < 82800:
        return

    yesterday = (_now() - timedelta(days=1)).strftime('%Y-%m-%d')
    try:
        resp = requests.get(
            'https://earthquake.usgs.gov/fdsnws/event/1/query',
            params={
                'format': 'geojson',
                'latitude': watch.latitude,
                'longitude': watch.longitude,
                'maxradiuskm': 50,
                'minmagnitude': 4.0,
                'starttime': yesterday,
                'limit': 5,
                'orderby': 'magnitude',
            },
            timeout=15
        )
        if resp.status_code != 200:
            return
        data = resp.json()
    except Exception as e:
        logger.warning(f"USGS fetch failed for watch {watch.id}: {e}")
        return

    watch.last_earthquake_check_at = _now()
    db.session.commit()

    features = data.get('features', [])
    if not features:
        return

    user = db.query(User).get(watch.user_id) if hasattr(db, 'query') else User.query.get(watch.user_id)
    if not user:
        return

    top = features[0]
    props = top.get('properties', {})
    mag = props.get('mag', 0)
    place = props.get('place', 'unknown location')
    quake_time = datetime.utcfromtimestamp(props.get('time', 0) / 1000).strftime('%b %d at %I:%M %p UTC')

    severity = 'critical' if mag >= 5.5 else 'warning'
    title = f"M{mag} earthquake detected near your property"

    body_html = f"""
    <p style='margin-bottom:12px;'>A <strong style='color:#ef4444;'>magnitude {mag} earthquake</strong> occurred {place} on {quake_time}.</p>
    <div style='padding:12px 14px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:8px;margin-bottom:12px;'>
      <strong style='color:#ef4444;'>What this means for your purchase:</strong><br>
      <span style='color:#94a3b8;'>Earthquakes of this magnitude can cause structural movement, foundation shifts, and pipe stress that may not be immediately visible. This property's structural risk assessment may need to be revisited.</span>
    </div>
    <p style='font-size:12px;color:#64748b;'>Recommended: Request an updated structural inspection before close. Reference this seismic event when negotiating your inspection contingency.</p>"""

    # Notify buyer
    _analysis_url = (f"https://www.getofferwise.ai/app?analysis={watch.analysis_id}&utm_source=earthquake_alert&utm_medium=email" if watch.analysis_id else "https://www.getofferwise.ai/app?utm_source=earthquake_alert&utm_medium=email")
    _send(user.email,
          f"⚠️ Seismic alert: M{mag} earthquake near {watch.address[:35]}",
          _email_html(title, body_html, watch.address, "🌍", cta_label="Review structural risk →", cta_url=_analysis_url),
          user_id=user.id)

    # Notify linked professionals — inspector especially needs to know
    pro_title = f"Seismic event near your client's property: {watch.address[:50]}"
    pro_body  = f"""
    <p style='margin-bottom:12px;'>A <strong style='color:#ef4444;'>M{mag} earthquake</strong> occurred {place} on {quake_time} — within 50km of a property your client is under contract on.</p>
    <div style='padding:12px 14px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:8px;margin-bottom:12px;'>
      <strong style='color:#ef4444;'>Professional action:</strong> Consider whether your inspection findings need to be re-evaluated in light of this seismic event. Your client has been notified and may contact you about a re-inspection request.
    </div>"""
    _notify_linked_professionals(watch, pro_title, pro_body, watch.address, "🌍")

    _save_alert(watch.id, user.id, 'earthquake', severity, title,
                f"M{mag} at {place} on {quake_time}. Structural re-check recommended.",
                {'magnitude': mag, 'place': place, 'time': quake_time, 'quake_count': len(features)},
                email_sent=True)

    logger.info(f"🌍 [EarthquakeMonitor] Alert sent to buyer + professionals: M{mag} for watch {watch.id}")

    # ── P2.4: Trigger targeted structural re-analysis ────────────────────────
    _run_seismic_reanalysis(watch, mag, place, quake_time)


# ── 3. PRICE DROP MONITOR ────────────────────────────────────────────────────

def _job_price_monitor():
    """
    Daily job. Compares RentCast AVM to the original asking price stored on the watch.
    If AVM dropped more than 2% or listing shows a price cut, fires a leverage alert.
    """
    logger.info("💰 [PriceMonitor] Starting price drop scan...")
    if not RENTCAST_API_KEY:
        logger.warning("💰 [PriceMonitor] RENTCAST_API_KEY not set — skipping")
        return

    try:
        from models import PropertyWatch, User
        db = _get_db()
        watches = PropertyWatch.query.filter_by(is_active=True).filter(
            PropertyWatch.asking_price.isnot(None)
        ).all()
        logger.info(f"💰 [PriceMonitor] Scanning {len(watches)} priced watches")

        for watch in watches:
            try:
                _check_price_for_watch(watch, db)
            except Exception as e:
                logger.error(f"💰 [PriceMonitor] Error on watch {watch.id}: {e}")

        logger.info("💰 [PriceMonitor] Complete")
    except Exception as e:
        logger.error(f"💰 [PriceMonitor] Fatal error: {e}", exc_info=True)


def _check_price_for_watch(watch, db):
    from models import User
    if not RENTCAST_API_KEY:
        return
    if watch.last_price_check_at and (_now() - watch.last_price_check_at).total_seconds() < 82800:
        return

    try:
        resp = requests.get(
            'https://api.rentcast.io/v1/avm/value',
            params={'address': watch.address},
            headers={'X-Api-Key': RENTCAST_API_KEY},
            timeout=15
        )
        if resp.status_code != 200:
            return
        data = resp.json()
    except Exception as e:
        logger.warning(f"RentCast price check failed: {e}")
        return

    current_avm = data.get('price') or 0
    watch.last_price_check_at = _now()
    db.session.commit()

    if not current_avm or not watch.asking_price:
        return

    baseline = watch.avm_at_analysis or watch.asking_price
    drop = baseline - current_avm
    drop_pct = drop / baseline * 100

    # Only alert if AVM dropped more than 2%
    if drop_pct < 2.0:
        return

    user = db.query(User).get(watch.user_id) if hasattr(db, 'query') else User.query.get(watch.user_id)
    if not user:
        return

    title = f"Listing value dropped {drop_pct:.1f}% since your analysis"
    body_html = f"""
    <p style='margin-bottom:12px;'>The estimated market value for this property has declined since your analysis.</p>
    <div style='display:flex;gap:12px;margin-bottom:14px;'>
      <div style='flex:1;padding:12px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;'>
        <div style='font-size:11px;color:#64748b;margin-bottom:4px;'>At analysis</div>
        <div style='font-size:18px;font-weight:800;color:#f1f5f9;'>{_format_price(baseline)}</div>
      </div>
      <div style='flex:1;padding:12px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.15);border-radius:8px;text-align:center;'>
        <div style='font-size:11px;color:#64748b;margin-bottom:4px;'>Current AVM</div>
        <div style='font-size:18px;font-weight:800;color:#ef4444;'>{_format_price(current_avm)}</div>
      </div>
    </div>
    <div style='padding:12px 14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;'>
      <strong style='color:#f97316;'>Leverage update:</strong> A {drop_pct:.1f}% market value decline ({_format_price(drop)}) strengthens your negotiating position. Consider submitting a revised offer or requesting a seller concession.
    </div>"""

    # Notify buyer
    _analysis_url = (f"https://www.getofferwise.ai/app?analysis={watch.analysis_id}&utm_source=price_drop_alert&utm_medium=email" if watch.analysis_id else "https://www.getofferwise.ai/app?utm_source=price_drop_alert&utm_medium=email")
    _send(user.email,
          f"💰 Price drop: {watch.address[:40]} value fell {drop_pct:.1f}%",
          _email_html(title, body_html, watch.address, "💰", cta_label="See your updated leverage →", cta_url=_analysis_url),
          user_id=user.id)

    # Notify linked professionals
    pro_title = f"Listing value dropped {drop_pct:.1f}% — market update on {watch.address[:40]}"
    pro_body  = f"""
    <p style='margin-bottom:12px;'>The estimated market value for this property has declined <strong style='color:#ef4444;'>{drop_pct:.1f}%</strong> — from {_format_price(baseline)} to {_format_price(current_avm)}.</p>
    <div style='padding:12px 14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;margin-bottom:10px;'>
      <strong style='color:#f97316;'>For agents:</strong> This is a strong opening to revisit the offer price. The AVM moved {_format_price(drop)} in your client's favour. Your client has this data now — be the one who acts on it first.
    </div>
    <div style='padding:12px 14px;background:rgba(96,165,250,.06);border:1px solid rgba(96,165,250,.2);border-radius:8px;'>
      <strong style='color:#60a5fa;'>For contractors:</strong> A lower property value may affect the buyer's budget for repairs. Consider whether your quote is still competitive given the revised property valuation. A proactive follow-up to the buyer can secure the job.
    </div>"""
    _notify_linked_professionals(watch, pro_title, pro_body, watch.address, "💰")

    _save_alert(watch.id, user.id, 'price_drop', 'warning', title,
                f"AVM dropped {drop_pct:.1f}% from {_format_price(baseline)} to {_format_price(current_avm)}.",
                {'baseline': baseline, 'current_avm': current_avm, 'drop': drop, 'drop_pct': drop_pct},
                email_sent=True)

    logger.info(f"💰 [PriceMonitor] Alert sent to buyer + professionals: {drop_pct:.1f}% drop for watch {watch.id}")

    # ── P2.5: Trigger targeted offer strategy re-analysis ───────────────────
    _run_price_reanalysis(watch, drop_pct, current_avm)


# ── 4. PERMIT MONITOR ────────────────────────────────────────────────────────

COUNTY_PERMIT_APIS = {
    # California — Socrata open data portals
    'Santa Clara': 'https://data.sccgov.org/resource/bwxt-4fh4.json',
    'San Mateo':   'https://data.smcgov.org/resource/building-permits.json',
    'Alameda':     'https://data.acgov.org/resource/building-permits.json',
    'San Francisco': 'https://data.sfgov.org/resource/p4e4-a99a.json',
    'Los Angeles': 'https://data.lacity.org/resource/nbyu-2ha9.json',
    'San Diego':   'https://data.sandiego.gov/resource/h5mc-7k4b.json',
    'Sacramento':  'https://data.cityofsacramento.org/resource/building-permits.json',
    # Texas
    'Travis':      'https://data.austintexas.gov/resource/3syk-w9eu.json',
    'Harris':      'https://cohgis-mycity.opendata.arcgis.com/datasets/houston-permits.json',
    # Florida
    'Miami-Dade':  'https://opendata.miamidade.gov/resource/building-permits.json',
    'Broward':     'https://opendata.broward.org/resource/building-permits.json',
    # New York
    'New York':    'https://data.cityofnewyork.us/resource/ipu4-2q9a.json',
    # Illinois
    'Cook':        'https://data.cityofchicago.org/resource/ydr8-5enu.json',
    # Washington
    'King':        'https://data.seattle.gov/resource/k44w-2dcq.json',
    # Colorado
    'Denver':      'https://www.denvergov.org/resource/building-permits.json',
    # Arizona
    'Maricopa':    'https://data.phoenix.gov/resource/building-permits.json',
    # Oregon
    'Multnomah':   'https://opendata.portland.gov/resource/building-permits.json',
    # Massachusetts
    'Suffolk':     'https://data.boston.gov/resource/rjbq-npuf.json',
}

# ── USA-wide permit lookup via PermitData.io (free tier) ─────────────────────
# Covers 20,000+ jurisdictions nationwide when Socrata is unavailable.
PERMITDATA_BASE = 'https://api.permitdata.io/v1'

def _job_permit_monitor():
    """
    Daily job. Checks for NEW permits on watched properties AFTER the analysis date.
    New permits = seller doing pre-close repairs = red flag that issues were worse than disclosed.

    Strategy (in order, stops at first success):
      1. Known Socrata county portal (fast, structured, no key required)
      2. PermitData.io API (nationwide, free tier, PERMITDATA_API_KEY env var)
      3. OpenPermit.org fallback (nationwide, no key required)
    """
    logger.info("🔨 [PermitMonitor] Starting permit scan...")
    try:
        from models import PropertyWatch, User
        db = _get_db()

        watches = PropertyWatch.query.filter_by(is_active=True).all()
        logger.info(f"🔨 [PermitMonitor] Checking {len(watches)} watches")

        for watch in watches:
            try:
                _check_permits_for_watch(watch, db)
            except Exception as e:
                logger.error(f"🔨 [PermitMonitor] Error on watch {watch.id}: {e}")

        logger.info("🔨 [PermitMonitor] Complete")
    except Exception as e:
        logger.error(f"🔨 [PermitMonitor] Fatal error: {e}", exc_info=True)


def _resolve_county(address: str) -> str:
    """
    Resolve county name from address string.
    First tries simple keyword match against known counties.
    Falls back to Census Bureau geocoder API for unknown addresses.
    """
    address_upper = (address or '').upper()

    # Fast path: keyword match for known counties
    for county in COUNTY_PERMIT_APIS:
        if county.upper() in address_upper:
            return county

    # Slow path: Census geocoder → returns county name
    try:
        resp = requests.get(
            'https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress',
            params={
                'address': address,
                'benchmark': 'Public_AR_Current',
                'vintage': 'Current_Current',
                'layers': 'Counties',
                'format': 'json',
            },
            timeout=10,
        )
        if resp.status_code == 200:
            matches = resp.json().get('result', {}).get('addressMatches', [])
            if matches:
                geographies = matches[0].get('geographies', {})
                counties = geographies.get('Counties', [])
                if counties:
                    return counties[0].get('NAME', '')
    except Exception as e:
        logger.debug(f"Census geocoder failed for county resolution: {e}")

    return ''


def _fetch_permits_socrata(county: str, address: str) -> list:
    """Fetch permits from known Socrata county portal."""
    api_url = COUNTY_PERMIT_APIS.get(county)
    if not api_url:
        return []

    addr_parts = address.split(',')[0].strip()
    try:
        resp = requests.get(
            api_url,
            params={
                '$where': f"upper(address) like upper('%{addr_parts}%')",
                '$limit': 10,
                '$order': ':id DESC',
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json() or []
    except Exception as e:
        logger.debug(f"Socrata permit fetch failed for {address}: {e}")
    return []


def _fetch_permits_permitdata(address: str) -> list:
    """
    Fetch permits from PermitData.io — covers 20,000+ US jurisdictions.
    Free tier: 100 requests/day. Set PERMITDATA_API_KEY env var to enable.
    """
    api_key = os.environ.get('PERMITDATA_API_KEY', '')
    if not api_key:
        return []
    try:
        resp = requests.get(
            f'{PERMITDATA_BASE}/permits',
            params={'address': address, 'limit': 10, 'days': 30},
            headers={'X-API-Key': api_key},
            timeout=12,
        )
        if resp.status_code == 200:
            data = resp.json()
            permits = data.get('permits') or data.get('results') or []
            # Normalise field names to internal standard
            normalised = []
            for p in permits:
                normalised.append({
                    'id': p.get('permit_number') or p.get('id', ''),
                    'type': p.get('permit_type') or p.get('type', 'Unknown'),
                    'description': (p.get('description') or p.get('work_description', ''))[:120],
                    'date': (p.get('issue_date') or p.get('filed_date') or p.get('date', 'Unknown'))[:10],
                    'status': p.get('status', ''),
                })
            return normalised
    except Exception as e:
        logger.debug(f"PermitData.io fetch failed for {address}: {e}")
    return []


def _fetch_permits_openpermit(address: str) -> list:
    """
    Fetch permits from OpenPermit.org — free, no key required, nationwide.
    """
    try:
        encoded = requests.utils.quote(address)
        resp = requests.get(
            f'https://api.openpermit.org/permits?address={encoded}&limit=10',
            timeout=12,
        )
        if resp.status_code == 200:
            data = resp.json()
            permits = data.get('data') or data.get('permits') or []
            normalised = []
            for p in permits:
                normalised.append({
                    'id': p.get('permit_number') or p.get('id', ''),
                    'type': p.get('permit_type') or p.get('type', 'Unknown'),
                    'description': (p.get('description') or '')[:120],
                    'date': (p.get('issue_date') or p.get('date', 'Unknown'))[:10],
                    'status': p.get('status', ''),
                })
            return normalised
    except Exception as e:
        logger.debug(f"OpenPermit fetch failed for {address}: {e}")
    return []


def _check_permits_for_watch(watch, db):
    from models import User
    if watch.last_permit_check_at and (_now() - watch.last_permit_check_at).total_seconds() < 82800:
        return

    watch.last_permit_check_at = _now()
    db.session.commit()

    # ── Source cascade: Socrata → PermitData.io → OpenPermit ────────────────
    permits = []

    # 1. Try known Socrata county portal first (fast, structured)
    county = _resolve_county(watch.address)
    if county and county in COUNTY_PERMIT_APIS:
        permits = _fetch_permits_socrata(county, watch.address)
        if permits:
            logger.debug(f"🔨 Permit source: Socrata ({county}) for {watch.address}")

    # 2. PermitData.io — nationwide, requires API key
    if not permits:
        permits = _fetch_permits_permitdata(watch.address)
        if permits:
            logger.debug(f"🔨 Permit source: PermitData.io for {watch.address}")

    # 3. OpenPermit.org — nationwide, free fallback
    if not permits:
        permits = _fetch_permits_openpermit(watch.address)
        if permits:
            logger.debug(f"🔨 Permit source: OpenPermit.org for {watch.address}")

    if not permits:
        return

    # ── Diff against baseline ────────────────────────────────────────────────
    baseline = []
    if watch.baseline_permits_json:
        try:
            baseline = json.loads(watch.baseline_permits_json)
        except Exception:
            pass
    baseline_ids = {str(p.get('id', p.get('permit_number', ''))) for p in baseline}

    new_permits = []
    for p in permits:
        pid = str(p.get('id', p.get('permit_number', p.get('objectid', ''))))
        if pid and pid not in baseline_ids:
            new_permits.append({
                'id': pid,
                'type': p.get('type', p.get('permit_type', 'Unknown')),
                'description': p.get('description', p.get('work_description', ''))[:120],
                'date': p.get('date', p.get('issue_date', 'Unknown'))[:10]
                    if (p.get('date') or p.get('issue_date')) else 'Unknown',
                'status': p.get('status', ''),
            })

    if not new_permits:
        return

    user = db.query(User).get(watch.user_id) if hasattr(db, 'query') else User.query.get(watch.user_id)
    if not user:
        return

    title = f"{len(new_permits)} new permit{'s' if len(new_permits)>1 else ''} filed on this property"
    permit_rows = ''.join(
        f"<div style='margin-bottom:8px;padding:8px 12px;background:rgba(255,255,255,.04);border-radius:6px;'>"
        f"<strong style='color:#f97316;'>{p['type']}</strong> — {p['description'] or 'No description'} "
        f"<span style='color:#64748b;font-size:11px;'>{p['date']}</span></div>"
        for p in new_permits[:3]
    )
    body_html = f"""
    <div style='padding:10px 14px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:8px;margin-bottom:14px;'>
      <strong style='color:#ef4444;'>🚩 Red flag:</strong> Permits filed after your analysis may indicate the seller is making emergency repairs before closing — possibly for issues not fully disclosed.
    </div>
    {permit_rows}
    <p style='font-size:12px;color:#64748b;margin-top:12px;'>Recommended: Request copies of all permit documentation and verify the scope of work against your inspection findings. Consider whether these repairs affect your negotiation position.</p>"""

    # Notify buyer
    _analysis_url = (f"https://www.getofferwise.ai/app?analysis={watch.analysis_id}&utm_source=permit_alert&utm_medium=email" if watch.analysis_id else "https://www.getofferwise.ai/app?utm_source=permit_alert&utm_medium=email")
    _send(user.email,
          f"🚩 Alert: New permit filed on {watch.address[:40]}",
          _email_html(title, body_html, watch.address, "🔨", cta_label="Review permit implications →", cta_url=_analysis_url),
          user_id=user.id)

    # Notify linked professionals — inspector especially; permits may relate to their findings
    pro_title = f"🚩 New permit filed on your client's property: {watch.address[:40]}"
    pro_body  = f"""
    <p style='margin-bottom:12px;'><strong>{len(new_permits)} new building permit{'s' if len(new_permits)>1 else ''}</strong> filed on this property after your client's inspection. This may indicate the seller is making repairs related to findings in your report.</p>
    {permit_rows}
    <div style='padding:12px 14px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:8px;margin-bottom:10px;'>
      <strong style='color:#ef4444;'>For inspectors / agents:</strong> Review these permit types against your inspection findings. If the work matches issues you flagged, this validates your report and may give your client grounds to re-negotiate or request a re-inspection.
    </div>
    <div style='padding:12px 14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;'>
      <strong style='color:#f97316;'>For contractors with an active quote:</strong> If the permit covers the same repair you quoted, the seller may be doing this work themselves — your lead may be closing. Contact the buyer now to confirm the job status before your 48-hour window expires.
    </div>"""
    _notify_linked_professionals(watch, pro_title, pro_body, watch.address, "🔨")

    _save_alert(watch.id, user.id, 'new_permit', 'critical', title,
                f"{len(new_permits)} new permits filed: {', '.join(p['type'] for p in new_permits[:3])}",
                {'new_permits': new_permits},
                email_sent=True)

    logger.info(f"🔨 [PermitMonitor] Alert sent to buyer + professionals: {len(new_permits)} permits for watch {watch.id}")


# ── 5. BUYER CONCERN SIGNAL (event-driven) ────────────────────────────────────

def fire_buyer_concern_signal(report_type: str, report_id: int, buyer_name: str,
                               buyer_email: str, address: str, view_count: int,
                               top_findings: list = None):
    """
    Called when a buyer opens an inspector report or agent share for the first time
    (or on their 3rd view, indicating deep engagement).
    Notifies the inspector/agent that their buyer is actively engaged.

    Args:
        report_type:  'inspector' or 'agent'
        report_id:    InspectorReport.id or AgentShare.id
        buyer_name:   Name of the buyer
        buyer_email:  Buyer's email
        address:      Property address
        view_count:   Current view count (fire on 1 and 3)
        top_findings: List of finding dicts from analysis_json
    """
    if view_count not in (1, 3):
        return

    try:
        professional_email = None
        professional_name = None
        professional_type = None

        if report_type == 'inspector':
            from models import InspectorReport, Inspector, User
            report = InspectorReport.query.get(report_id)
            if not report:
                return
            insp_user = User.query.get(report.inspector_user_id)
            if not insp_user:
                return
            professional_email = insp_user.email
            professional_name  = insp_user.name or 'Inspector'
            professional_type  = 'inspector'

        elif report_type == 'agent':
            from models import AgentShare, Agent, User
            share = AgentShare.query.get(report_id)
            if not share:
                return
            agent_user = User.query.get(share.agent_user_id)
            if not agent_user:
                return
            professional_email = agent_user.email
            professional_name  = agent_user.name or 'Agent'
            professional_type  = 'agent'

        else:
            return

        if not professional_email:
            return

        # Build signal email
        is_first_view = view_count == 1
        engagement_label = "just opened" if is_first_view else "is actively reviewing (3rd view)"
        emoji = "👁️" if is_first_view else "🔥"

        # Top concern extraction
        concern_html = ""
        if top_findings and len(top_findings) > 0:
            top = top_findings[:3]
            items = ''.join(
                f"<li style='margin-bottom:4px;color:#94a3b8;'><strong style='color:#f1f5f9;'>{f.get('title','Finding')}</strong>"
                f"{' — ' + f.get('detail','')[:80] if f.get('detail') else ''}</li>"
                for f in top
            )
            concern_html = f"""
            <div style='margin-top:14px;'>
              <div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:8px;'>
                Key findings in this report
              </div>
              <ul style='list-style:none;padding:0;margin:0;'>{items}</ul>
            </div>"""

        subject = f"{emoji} {buyer_name or 'Your buyer'} {engagement_label} your OfferWise report"
        body_html = f"""
        <p style='margin-bottom:12px;'>
          <strong style='color:#f1f5f9;'>{buyer_name or 'Your buyer'}</strong> {engagement_label} the OfferWise analysis you prepared for <strong style='color:#f97316;'>{address}</strong>.
        </p>
        <div style='padding:12px 14px;background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2);border-radius:8px;margin-bottom:14px;'>
          <strong style='color:#22c55e;'>Good time to reach out.</strong> They are actively reading the analysis right now. A quick call to walk through the findings can make the difference between a confident buyer and a nervous one.
        </div>
        {concern_html}
        <p style='font-size:12px;color:#64748b;margin-top:14px;'>
          OfferWise gives you real-time visibility into your client's engagement — so you can step in at exactly the right moment.
        </p>"""

        html = _email_html(subject.replace(emoji + " ", ""), body_html, address, emoji)
        _send(professional_email, subject, html, user_id=None)

        logger.info(f"📡 [ConcernSignal] Sent {professional_type} alert: "
                    f"{buyer_name} view #{view_count} on {address}")

    except Exception as e:
        logger.error(f"📡 [ConcernSignal] Failed: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# P2 AGENTIC FEATURES
# ══════════════════════════════════════════════════════════════════════════════

# ── P2.4: SEISMIC RE-ANALYSIS ─────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

def _run_seismic_reanalysis(watch, magnitude, place, quake_time_str):
    """
    P2.4 — The agent's reasoning loop:

    1. Pull original structural findings from Analysis.result_json
    2. Cross-reference M-magnitude with structural risk tier
    3. DECIDE: is re-analysis warranted? (M4.0–4.9 + low-risk = advisory only;
       M5.0+ OR already high structural risk = full targeted rewrite)
    4. Call Claude with targeted prompt → seismic_reanalysis section
    5. Write back to Analysis record
    6. Email buyer a diff card (original structural risk → updated assessment)
    7. Notify linked professionals with actionable framing
    """
    if not ANTHROPIC_API_KEY or not watch.analysis_id:
        return

    try:
        import anthropic
        import json as _json
        from models import Analysis, User

        analysis = Analysis.query.get(watch.analysis_id)
        if not analysis or not analysis.result_json:
            return

        result = _json.loads(analysis.result_json)

        # Extract structural context from original analysis
        categories = result.get('categories', result.get('category_scores', []))
        structural_cat = next(
            (c for c in categories if 'foundation' in c.get('name', '').lower()
             or 'structural' in c.get('name', '').lower()), None
        )
        overall_risk = result.get('risk_score', {}).get('overall_risk_score',
                       result.get('overall_risk_score', 50))
        risk_tier    = result.get('risk_score', {}).get('risk_tier',
                       result.get('risk_tier', 'MEDIUM'))
        struct_score = structural_cat.get('score', 0) if structural_cat else 0
        struct_issues = structural_cat.get('key_issues', []) if structural_cat else []

        # ── Agent decision: is a full re-analysis warranted? ──────────────────
        # Logic: serious re-analysis if M5.0+ OR property already has structural flags
        serious = magnitude >= 5.0 or struct_score >= 40 or risk_tier in ('HIGH', 'CRITICAL')
        advisory_only = not serious

        logger.info(
            f"🌍 [SeismicP2] Watch {watch.id}: M{magnitude}, struct_score={struct_score}, "
            f"risk_tier={risk_tier} → {'full re-analysis' if serious else 'advisory note'}"
        )

        # ── AI targeted re-analysis call (Anthropic → OpenAI fallback) ─────

        issues_text = '\n'.join(f"- {i}" for i in struct_issues[:5]) if struct_issues else \
                      '- No specific structural issues flagged in original inspection'

        prompt = f"""You are OfferWise AI's seismic risk specialist. A real earthquake just occurred near a property that a buyer is under contract on. You need to update their structural risk assessment based on this new information.

PROPERTY: {watch.address}
ORIGINAL OVERALL RISK SCORE: {overall_risk}/100 ({risk_tier})
ORIGINAL STRUCTURAL RISK SCORE: {struct_score}/100
ORIGINAL STRUCTURAL FINDINGS:
{issues_text}

SEISMIC EVENT: Magnitude {magnitude} earthquake, {place}, occurred {quake_time_str}
DISTANCE: Within 50km of the property
DECISION: {"Full targeted re-analysis required" if serious else "Advisory assessment (property appears structurally sound, moderate event)"}

Write a concise seismic impact assessment in this exact JSON format:
{{
  "updated_structural_risk": <integer 0-100, adjusted score>,
  "risk_direction": "<unchanged|elevated|significantly_elevated>",
  "agent_reasoning": "<2-3 sentences: why you adjusted the score this way given the specific magnitude and original findings>",
  "immediate_actions": ["<action 1>", "<action 2>", "<action 3>"],
  "negotiation_impact": "<1 sentence: how this affects the buyer's leverage or contingency position>",
  "re_inspection_warranted": <true|false>,
  "confidence": "<low|medium|high>"
}}

Respond ONLY with valid JSON. Be precise and clinically accurate — this is used for real financial decisions."""

        from ai_client import get_ai_response as _get_ai
        raw = _get_ai(prompt, max_tokens=600).strip()
        # Strip markdown fences if present
        if raw.startswith('```'):
            raw = '\n'.join(raw.split('\n')[1:-1])
        reanalysis = _json.loads(raw)

        # ── Write back to Analysis ────────────────────────────────────────────
        result['seismic_reanalysis'] = {
            'earthquake': {
                'magnitude': magnitude,
                'place': place,
                'time': quake_time_str,
            },
            'original_structural_score': struct_score,
            'updated_structural_risk':   reanalysis['updated_structural_risk'],
            'risk_direction':            reanalysis['risk_direction'],
            'agent_reasoning':           reanalysis['agent_reasoning'],
            'immediate_actions':         reanalysis['immediate_actions'],
            'negotiation_impact':        reanalysis['negotiation_impact'],
            're_inspection_warranted':   reanalysis['re_inspection_warranted'],
            'confidence':                reanalysis['confidence'],
            'generated_at':              _now().isoformat(),
        }
        analysis.result_json = _json.dumps(result)
        db = _get_db()
        db.session.commit()
        logger.info(f"🌍 [SeismicP2] Re-analysis written to Analysis {watch.analysis_id}")

        # ── Build diff email ──────────────────────────────────────────────────
        user = User.query.get(watch.user_id)
        if not user:
            return

        score_before = struct_score
        score_after  = reanalysis['updated_structural_risk']
        direction    = reanalysis['risk_direction']
        arrow        = '↑' if direction != 'unchanged' else '→'
        color_after  = '#ef4444' if direction == 'significantly_elevated' else \
                       '#f97316' if direction == 'elevated' else '#22c55e'

        actions_html = ''.join(
            f"<li style='margin-bottom:6px;color:#cbd5e1;'>{a}</li>"
            for a in reanalysis['immediate_actions']
        )
        reinspect_banner = """
        <div style='padding:12px 14px;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);
                    border-radius:8px;margin-bottom:14px;'>
          <strong style='color:#ef4444;'>Re-inspection recommended</strong> —
          <span style='color:#94a3b8;'>Request a structural-focused re-inspection before
          removing your inspection contingency. Reference this seismic event explicitly.</span>
        </div>""" if reanalysis['re_inspection_warranted'] else ""

        body_html = f"""
        <p style='margin-bottom:14px;'>OfferWise AI has updated its structural risk assessment
        for <strong style='color:#f97316;'>{watch.address}</strong> following a
        <strong>M{magnitude}</strong> earthquake {place} on {quake_time_str}.</p>

        <div style='display:flex;gap:12px;margin-bottom:16px;align-items:stretch;'>
          <div style='flex:1;padding:14px;background:rgba(255,255,255,.04);
                      border:1px solid rgba(255,255,255,.08);border-radius:10px;text-align:center;'>
            <div style='font-size:10px;font-weight:700;text-transform:uppercase;
                        letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>Before earthquake</div>
            <div style='font-size:28px;font-weight:900;color:#f1f5f9;'>{score_before}</div>
            <div style='font-size:11px;color:#64748b;'>structural risk score</div>
          </div>
          <div style='display:flex;align-items:center;font-size:22px;color:#64748b;'>{arrow}</div>
          <div style='flex:1;padding:14px;background:rgba(239,68,68,.07);
                      border:1px solid rgba(239,68,68,.2);border-radius:10px;text-align:center;'>
            <div style='font-size:10px;font-weight:700;text-transform:uppercase;
                        letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>Updated assessment</div>
            <div style='font-size:28px;font-weight:900;color:{color_after};'>{score_after}</div>
            <div style='font-size:11px;color:#64748b;'>structural risk score</div>
          </div>
        </div>

        <div style='padding:12px 14px;background:rgba(255,255,255,.04);border-radius:8px;
                    margin-bottom:14px;font-size:13px;color:#94a3b8;line-height:1.6;'>
          <strong style='color:#f1f5f9;'>AI reasoning:</strong> {reanalysis['agent_reasoning']}
        </div>

        {reinspect_banner}

        <div style='margin-bottom:14px;'>
          <div style='font-size:11px;font-weight:700;text-transform:uppercase;
                      letter-spacing:.08em;color:#64748b;margin-bottom:8px;'>Recommended actions</div>
          <ul style='list-style:none;padding:0;margin:0;'>{actions_html}</ul>
        </div>

        <div style='padding:10px 14px;background:rgba(96,165,250,.07);
                    border:1px solid rgba(96,165,250,.2);border-radius:8px;font-size:13px;'>
          <strong style='color:#60a5fa;'>Negotiation impact:</strong>
          <span style='color:#94a3b8;'> {reanalysis['negotiation_impact']}</span>
        </div>"""

        subject = f"🌍 AI update: Structural risk re-assessed after M{magnitude} earthquake"
        _analysis_url = (
            f"https://www.getofferwise.ai/app?analysis={watch.analysis_id}&utm_source=seismic_reanalysis&utm_medium=email"
            if watch.analysis_id else
            "https://www.getofferwise.ai/app?utm_source=seismic_reanalysis&utm_medium=email"
        )
        _send(user.email, subject,
              _email_html("Seismic Re-Analysis Complete", body_html, watch.address, "🌍",
                          cta_label="View updated structural risk →", cta_url=_analysis_url),
              user_id=user.id)

        # Professionals get a tighter action-oriented version
        pro_body = f"""
        <p style='margin-bottom:12px;'>OfferWise AI ran a targeted structural re-analysis after
        a <strong style='color:#ef4444;'>M{magnitude} earthquake</strong> near
        <strong>{watch.address}</strong>.</p>
        <div style='display:flex;gap:10px;margin-bottom:12px;'>
          <div style='flex:1;padding:10px;background:rgba(255,255,255,.04);border-radius:8px;text-align:center;'>
            <div style='font-size:11px;color:#64748b;'>Before</div>
            <div style='font-size:22px;font-weight:800;color:#f1f5f9;'>{score_before}</div>
          </div>
          <div style='flex:1;padding:10px;background:rgba(239,68,68,.07);
                      border:1px solid rgba(239,68,68,.2);border-radius:8px;text-align:center;'>
            <div style='font-size:11px;color:#64748b;'>After</div>
            <div style='font-size:22px;font-weight:800;color:{color_after};'>{score_after}</div>
          </div>
        </div>
        <p style='font-size:13px;color:#94a3b8;'>{reanalysis['agent_reasoning']}</p>
        <div style='padding:10px 14px;background:rgba(249,115,22,.07);border-radius:8px;
                    font-size:13px;color:#94a3b8;margin-top:10px;'>
          <strong style='color:#f97316;'>Your client has been notified.</strong>
          {'Re-inspection has been recommended.' if reanalysis["re_inspection_warranted"] else
           'Re-inspection not flagged as required at this magnitude.'}
        </div>"""

        _notify_linked_professionals(
            watch,
            f"AI structural re-analysis: M{magnitude} earthquake impact on {watch.address[:40]}",
            pro_body, watch.address, "🌍"
        )

        _save_alert(
            watch.id, user.id, 'seismic_reanalysis',
            'critical' if reanalysis['re_inspection_warranted'] else 'warning',
            f"M{magnitude} seismic re-analysis: structural risk {score_before}→{score_after}",
            reanalysis['agent_reasoning'],
            {'magnitude': magnitude, 'score_before': score_before,
             'score_after': score_after, 'direction': direction,
             're_inspection': reanalysis['re_inspection_warranted']},
            email_sent=True
        )
        logger.info(
            f"🌍 [SeismicP2] ✅ Re-analysis complete for watch {watch.id}: "
            f"{score_before}→{score_after} ({direction})"
        )

    except Exception as e:
        logger.error(f"🌍 [SeismicP2] Re-analysis failed for watch {watch.id}: {e}", exc_info=True)


# ── P2.5: PRICE CUT RE-ANALYSIS ───────────────────────────────────────────────

def _run_price_reanalysis(watch, drop_pct, current_avm):
    """
    P2.5 — The agent's reasoning loop:

    1. Pull original offer strategy + top inspection findings from Analysis.result_json
    2. Determine if the drop materially changes the offer recommendation
       (< 2%: cosmetic, 2–5%: moderate update, > 5%: full offer rewrite)
    3. Call Claude to recompute only the offer section + generate leverage talking points
    4. Write updated offer recommendation back to Analysis
    5. Email buyer a before/after diff with 3 specific negotiating lines they can use TODAY
    """
    if not ANTHROPIC_API_KEY or not watch.analysis_id:
        return

    try:
        import anthropic
        import json as _json
        from models import Analysis, User

        analysis = Analysis.query.get(watch.analysis_id)
        if not analysis or not analysis.result_json:
            return

        result      = _json.loads(analysis.result_json)
        orig_offer  = result.get('offer_strategy', {}).get('recommended_offer', 0)
        orig_asking = watch.asking_price or result.get('property_price', 0)
        orig_risk   = result.get('risk_score', {}).get('overall_risk_score',
                      result.get('overall_risk_score', 50))

        # Extract top inspection issues for context
        categories = result.get('categories', result.get('category_scores', []))
        top_issues = []
        for cat in sorted(categories, key=lambda c: c.get('score', 0), reverse=True)[:3]:
            issues = cat.get('key_issues', [])
            if issues:
                top_issues.append(f"{cat.get('name','Unknown')}: {issues[0]}")

        issues_ctx = '\n'.join(f"- {i}" for i in top_issues) if top_issues else \
                     '- No major issues flagged'

        # ── Agent decision: materiality threshold ─────────────────────────────
        materiality = 'major' if drop_pct >= 5.0 else \
                      'moderate' if drop_pct >= 2.0 else 'minor'
        logger.info(
            f"💰 [PriceP2] Watch {watch.id}: {drop_pct:.1f}% drop → {materiality} update"
        )

        prompt = f"""You are OfferWise AI's offer strategy specialist. A listing price has dropped
and you need to update the buyer's offer recommendation and negotiating position.

PROPERTY: {watch.address}
ORIGINAL ASKING PRICE: ${orig_asking:,.0f}
CURRENT AVM (after drop): ${current_avm:,.0f}
PRICE DROP: {drop_pct:.1f}% (${orig_asking - current_avm:,.0f})
ORIGINAL RECOMMENDED OFFER: ${orig_offer:,.0f}
ORIGINAL RISK SCORE: {orig_risk}/100
CHANGE MATERIALITY: {materiality}

TOP INSPECTION FINDINGS ALREADY IN PLAY:
{issues_ctx}

Produce an updated offer strategy in this exact JSON format:
{{
  "updated_recommended_offer": <integer, revised offer in dollars>,
  "updated_discount_from_ask": <float, new discount as percentage e.g. 0.07 for 7%>,
  "offer_changed_materially": <true|false>,
  "agent_reasoning": "<2 sentences explaining why the offer moved and by how much>",
  "talking_points": [
    "<specific negotiating line 1 the buyer can say verbatim to their realtor>",
    "<specific negotiating line 2>",
    "<specific negotiating line 3>"
  ],
  "urgency": "<low|medium|high — how quickly should buyer act on this>",
  "seller_motivation_signal": "<1 sentence: what this price drop reveals about the seller>"
}}

Talking points must be specific, concrete, and immediately usable — not generic advice.
Respond ONLY with valid JSON."""

        from ai_client import get_ai_response as _get_ai
        raw = _get_ai(prompt, max_tokens=700).strip()
        if raw.startswith('```'):
            raw = '\n'.join(raw.split('\n')[1:-1])
        reanalysis = _json.loads(raw)

        # ── Write back to Analysis ────────────────────────────────────────────
        offer_strategy = result.get('offer_strategy', {})
        offer_strategy['recommended_offer_prev']    = orig_offer
        offer_strategy['recommended_offer']         = reanalysis['updated_recommended_offer']
        offer_strategy['discount_from_ask']         = reanalysis['updated_discount_from_ask']
        offer_strategy['price_drop_reanalysis']     = {
            'drop_pct':               drop_pct,
            'avm_before':             orig_asking,
            'avm_after':              current_avm,
            'offer_changed_materially': reanalysis['offer_changed_materially'],
            'agent_reasoning':        reanalysis['agent_reasoning'],
            'talking_points':         reanalysis['talking_points'],
            'urgency':                reanalysis['urgency'],
            'seller_motivation':      reanalysis['seller_motivation_signal'],
            'generated_at':           _now().isoformat(),
        }
        result['offer_strategy'] = offer_strategy
        analysis.result_json = _json.dumps(result)
        db = _get_db()
        db.session.commit()
        logger.info(f"💰 [PriceP2] Offer strategy written to Analysis {watch.analysis_id}")

        # ── Build diff email ──────────────────────────────────────────────────
        user = User.query.get(watch.user_id)
        if not user:
            return

        new_offer = reanalysis['updated_recommended_offer']
        offer_delta = new_offer - orig_offer
        delta_sign  = '+' if offer_delta >= 0 else ''
        urgency_color = {'high': '#ef4444', 'medium': '#f97316', 'low': '#22c55e'}.get(
            reanalysis['urgency'], '#f97316')

        talking_html = ''.join(
            f"""<div style='padding:10px 14px;background:rgba(96,165,250,.06);
                border-left:3px solid #60a5fa;border-radius:0 8px 8px 0;
                margin-bottom:8px;font-size:13px;color:#cbd5e1;'>"{tp}"</div>"""
            for tp in reanalysis['talking_points']
        )

        body_html = f"""
        <p style='margin-bottom:14px;'>The estimated value of
        <strong style='color:#f97316;'>{watch.address}</strong> has dropped
        <strong style='color:#ef4444;'>{drop_pct:.1f}%</strong>.
        OfferWise AI has recalculated your offer strategy.</p>

        <div style='display:flex;gap:12px;margin-bottom:16px;'>
          <div style='flex:1;padding:14px;background:rgba(255,255,255,.04);
                      border:1px solid rgba(255,255,255,.08);border-radius:10px;text-align:center;'>
            <div style='font-size:10px;font-weight:700;text-transform:uppercase;
                        letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>Original offer</div>
            <div style='font-size:22px;font-weight:900;color:#f1f5f9;'>{_format_price(orig_offer)}</div>
            <div style='font-size:11px;color:#64748b;'>asking: {_format_price(orig_asking)}</div>
          </div>
          <div style='flex:1;padding:14px;background:rgba(34,197,94,.07);
                      border:1px solid rgba(34,197,94,.2);border-radius:10px;text-align:center;'>
            <div style='font-size:10px;font-weight:700;text-transform:uppercase;
                        letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>Updated offer</div>
            <div style='font-size:22px;font-weight:900;color:#22c55e;'>{_format_price(new_offer)}</div>
            <div style='font-size:11px;color:#22c55e;'>{delta_sign}{_format_price(abs(offer_delta))} from original</div>
          </div>
        </div>

        <div style='padding:12px 14px;background:rgba(255,255,255,.04);border-radius:8px;
                    margin-bottom:14px;font-size:13px;color:#94a3b8;line-height:1.6;'>
          <strong style='color:#f1f5f9;'>AI reasoning:</strong> {reanalysis['agent_reasoning']}
        </div>

        <div style='padding:10px 14px;background:rgba(96,165,250,.07);
                    border:1px solid rgba(96,165,250,.2);border-radius:8px;
                    font-size:13px;margin-bottom:14px;'>
          <strong style='color:#60a5fa;'>Seller signal:</strong>
          <span style='color:#94a3b8;'> {reanalysis['seller_motivation_signal']}</span>
        </div>

        <div style='margin-bottom:14px;'>
          <div style='font-size:11px;font-weight:700;text-transform:uppercase;
                      letter-spacing:.08em;color:#64748b;margin-bottom:8px;'>
            3 talking points — use these with your realtor today
          </div>
          {talking_html}
        </div>

        <div style='padding:8px 14px;background:rgba({urgency_color.lstrip("#")[:2]},{urgency_color.lstrip("#")[2:4]},{urgency_color.lstrip("#")[4:]}, .06) 0 0;
                    border-radius:8px;font-size:12px;'>
          <strong style='color:{urgency_color};'>Urgency: {reanalysis["urgency"].upper()}</strong>
          {"— Act before other buyers see this data." if reanalysis["urgency"] == "high" else
           "— Review this before your next communication with the seller." if reanalysis["urgency"] == "medium"
           else ""}
        </div>"""

        subject = f"💰 AI update: Offer strategy revised — {_format_price(new_offer)} recommended"
        analysis_url = (
            f"https://www.getofferwise.ai/app?analysis={watch.analysis_id}&utm_source=price_cut_alert&utm_medium=email"
            if watch.analysis_id
            else "https://www.getofferwise.ai/app?utm_source=price_cut_alert&utm_medium=email"
        )
        _send(user.email, subject,
              _email_html("Offer Strategy Re-Analysis", body_html, watch.address, "💰",
                          cta_label="See updated offer strategy →", cta_url=analysis_url),
              user_id=user.id)

        # Professionals: agent-first framing
        pro_body = f"""
        <p style='margin-bottom:12px;'>OfferWise AI revised the offer strategy for
        <strong>{watch.address}</strong> following a
        <strong style='color:#ef4444;'>{drop_pct:.1f}% AVM drop</strong>.</p>
        <div style='display:flex;gap:10px;margin-bottom:12px;'>
          <div style='flex:1;padding:10px;background:rgba(255,255,255,.04);
                      border-radius:8px;text-align:center;'>
            <div style='font-size:11px;color:#64748b;'>Old offer</div>
            <div style='font-size:18px;font-weight:800;color:#f1f5f9;'>{_format_price(orig_offer)}</div>
          </div>
          <div style='flex:1;padding:10px;background:rgba(34,197,94,.07);
                      border:1px solid rgba(34,197,94,.2);border-radius:8px;text-align:center;'>
            <div style='font-size:11px;color:#64748b;'>New offer</div>
            <div style='font-size:18px;font-weight:800;color:#22c55e;'>{_format_price(new_offer)}</div>
          </div>
        </div>
        <p style='font-size:13px;color:#94a3b8;margin-bottom:10px;'>{reanalysis['agent_reasoning']}</p>
        <div style='padding:10px 14px;background:rgba(249,115,22,.07);border-radius:8px;
                    font-size:13px;color:#94a3b8;'>
          <strong style='color:#f97316;'>Your buyer has this analysis.</strong>
          They have 3 new talking points to bring to the negotiating table — be ready.
        </div>"""

        _notify_linked_professionals(
            watch,
            f"AI offer update: {watch.address[:40]} — new recommended offer {_format_price(new_offer)}",
            pro_body, watch.address, "💰"
        )

        _save_alert(
            watch.id, user.id, 'price_reanalysis', 'warning',
            f"Offer strategy updated: {_format_price(orig_offer)} → {_format_price(new_offer)}",
            reanalysis['agent_reasoning'],
            {'drop_pct': drop_pct, 'avm_before': orig_asking, 'avm_after': current_avm,
             'offer_before': orig_offer, 'offer_after': new_offer,
             'urgency': reanalysis['urgency']},
            email_sent=True
        )
        logger.info(
            f"💰 [PriceP2] ✅ Offer reanalysis complete for watch {watch.id}: "
            f"{_format_price(orig_offer)}→{_format_price(new_offer)}"
        )

    except Exception as e:
        logger.error(f"💰 [PriceP2] Re-analysis failed for watch {watch.id}: {e}", exc_info=True)


# ── P2.6: INSPECTOR → REALTOR ONE-CLICK SEND ─────────────────────────────────

def forward_report_to_realtor(inspector_report_id: int, realtor_email: str,
                               realtor_name: str = None):
    """
    P2.6 — Called when the inspector clicks "Send to Realtor" in their portal.

    1. Load the InspectorReport + its analysis_json
    2. Create an AgentShare record (or find existing) for the realtor
    3. Send the realtor a branded email with:
       - Full analysis summary card
       - "Your client is already analysing this property" framing
       - Buyer concern signal pre-wired (realtor will get notified when buyer opens it)
    4. Return the share URL so the inspector can see confirmation
    """
    try:
        import json as _json
        import secrets
        from models import InspectorReport, AgentShare, User, Agent

        db = _get_db()
        report = InspectorReport.query.get(inspector_report_id)
        if not report:
            logger.error(f"P2.6: Report {inspector_report_id} not found")
            return None

        inspector_user = User.query.get(report.inspector_user_id)
        insp_name = inspector_user.name if inspector_user else (report.inspector_name_on_report or 'Your inspector')
        insp_biz  = report.inspector_biz_on_report or ''

        # ── Parse analysis for summary card ──────────────────────────────────
        analysis = {}
        if report.analysis_json:
            try:
                analysis = _json.loads(report.analysis_json)
            except Exception:
                pass

        risk_score = analysis.get('risk_score', {}).get('overall_risk_score',
                     analysis.get('overall_risk_score', 0))
        risk_tier  = analysis.get('risk_score', {}).get('risk_tier',
                     analysis.get('risk_tier', 'UNKNOWN'))
        categories = analysis.get('categories', analysis.get('category_scores', []))
        top_issues = [
            c for c in sorted(categories, key=lambda x: x.get('score', 0), reverse=True)
            if c.get('score', 0) > 0
        ][:3]

        # ── Create or reuse AgentShare for this realtor+report ────────────────
        # We create a synthetic AgentShare so the realtor gets buyer-open signals
        share_token = secrets.token_urlsafe(24)[:32]
        share = AgentShare(
            # We can't link to a real Agent record without registration,
            # so we create a ghost entry linked to inspector's user for now
            agent_id=None,                          # realtor not yet registered
            agent_user_id=report.inspector_user_id, # routed through inspector until realtor signs up
            property_address=report.property_address,
            property_price=report.property_price,
            buyer_name=report.buyer_name,
            buyer_email=report.buyer_email,
            analysis_json=report.analysis_json,
            share_token=share_token,
            agent_name_on_report=realtor_name or realtor_email.split('@')[0].replace('.', ' ').title(),
            agent_biz_on_report='',
            has_text=bool(report.analysis_json),
        )
        db.session.add(share)
        db.session.flush()   # get share.id

        # ── Build top-issues rows for realtor email ───────────────────────────
        tier_color = {'LOW': '#22c55e', 'MEDIUM': '#f59e0b',
                      'HIGH': '#f97316', 'CRITICAL': '#ef4444'}.get(risk_tier, '#64748b')

        issue_rows = ''
        for cat in top_issues:
            cost_lo = cat.get('cost_low', cat.get('estimated_cost_low', 0))
            cost_hi = cat.get('cost_high', cat.get('estimated_cost_high', 0))
            cost_str = f"{_format_price(cost_lo)}–{_format_price(cost_hi)}" if cost_hi else "—"
            issue_rows += f"""
            <tr>
              <td style='padding:8px 10px;color:#f1f5f9;font-size:13px;font-weight:600;border-bottom:1px solid rgba(255,255,255,.06);'>
                {cat.get('name','').replace('_',' ').title()}</td>
              <td style='padding:8px 10px;color:#94a3b8;font-size:12px;border-bottom:1px solid rgba(255,255,255,.06);'>
                {cost_str}</td>
              <td style='padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.06);'>
                <span style='font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;
                             background:rgba(249,115,22,.12);color:#f97316;'>{cat.get('severity','').upper()}</span>
              </td>
            </tr>"""

        share_url = f"https://www.getofferwise.ai/agent-report/{share_token}"

        body_html = f"""
        <p style='margin-bottom:14px;'>
          <strong style='color:#f1f5f9;'>{insp_name}</strong>
          {f"of {insp_biz}" if insp_biz else ""} has completed an inspection analysis for
          <strong style='color:#f97316;'>{report.property_address}</strong>
          {"for your client " + ("<strong>" + report.buyer_name + "</strong>") if report.buyer_name else ""}.
          They're sharing the full AI-powered analysis with you as the buyer's realtor.
        </p>

        <div style='display:flex;gap:12px;margin-bottom:16px;'>
          <div style='flex:1;padding:14px;background:rgba(255,255,255,.04);
                      border:1px solid rgba(255,255,255,.08);border-radius:10px;text-align:center;'>
            <div style='font-size:10px;font-weight:700;text-transform:uppercase;
                        letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>Overall Risk</div>
            <div style='font-size:28px;font-weight:900;color:#f1f5f9;'>{int(risk_score)}</div>
            <div style='font-size:11px;font-weight:700;color:{tier_color};'>{risk_tier}</div>
          </div>
          <div style='flex:1;padding:14px;background:rgba(255,255,255,.04);
                      border:1px solid rgba(255,255,255,.08);border-radius:10px;text-align:center;'>
            <div style='font-size:10px;font-weight:700;text-transform:uppercase;
                        letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>Property Value</div>
            <div style='font-size:22px;font-weight:900;color:#f1f5f9;'>{_format_price(report.property_price)}</div>
          </div>
        </div>

        {f'''<table style='width:100%;border-collapse:collapse;margin-bottom:16px;'>
          <thead>
            <tr>
              <th style='text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;
                         letter-spacing:.08em;color:#64748b;padding:6px 10px;'>Category</th>
              <th style='text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;
                         letter-spacing:.08em;color:#64748b;padding:6px 10px;'>Est. Repair Cost</th>
              <th style='text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;
                         letter-spacing:.08em;color:#64748b;padding:6px 10px;'>Severity</th>
            </tr>
          </thead>
          <tbody>{issue_rows}</tbody>
        </table>''' if issue_rows else ''}

        <div style='padding:12px 14px;background:rgba(96,165,250,.07);
                    border:1px solid rgba(96,165,250,.2);border-radius:8px;margin-bottom:16px;
                    font-size:13px;color:#94a3b8;'>
          <strong style='color:#60a5fa;'>You will be notified</strong> the moment
          {report.buyer_name or 'the buyer'} opens this report — so you can reach out
          at exactly the right time.
        </div>

        <a href='{share_url}'
           style='display:block;text-align:center;padding:14px;
                  background:linear-gradient(90deg,#f97316,#f59e0b);
                  border-radius:10px;color:white;font-size:15px;font-weight:700;
                  text-decoration:none;'>
          View Full Analysis →
        </a>"""

        realtor_display = realtor_name or realtor_email.split('@')[0]
        subject = f"📋 Inspection analysis ready: {report.property_address[:45]}"

        full_html = _email_html(
            f"Inspection analysis shared by {insp_name}",
            body_html, report.property_address, "📋"
        )
        _send(realtor_email, subject, full_html, user_id=None)

        db.session.commit()

        logger.info(
            f"📋 [RealtorSend P2.6] Inspector {report.inspector_user_id} → "
            f"realtor {realtor_email} | report {inspector_report_id} | share {share_token}"
        )
        return {
            'share_token': share_token,
            'share_url': share_url,
            'share_id': share.id,
        }

    except Exception as e:
        logger.error(f"📋 [RealtorSend P2.6] Failed: {e}", exc_info=True)
        return None


# ── Job registration helper ──────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════════
# DEADLINE MONITOR — Dynamic escrow deadline alerting
# ═══════════════════════════════════════════════════════════════════════════════
#
# Runs at 6:45am PT (before all other monitors). For each active watch that has
# at least one deadline date set, Claude reasons about the full escrow timeline:
# which deadlines are approaching, what's happened so far (existing alerts),
# and what the buyer should do RIGHT NOW. Not a template — Claude decides.
#
# Deadlines tracked:
#   inspection_contingency_date  — remove / complete inspection by
#   loan_contingency_date        — financing must be confirmed by
#   appraisal_contingency_date   — appraisal contingency removal
#   seller_response_deadline     — seller must respond to repair request by
#   repair_completion_deadline   — agreed repairs must be done before
#   close_of_escrow_date         — COE
#
# Alert thresholds (days before deadline):
#   14d — "Heads up" info alert for all deadlines
#    7d — "Action required" warning + Claude drafts specific action
#    3d — "Critical" alert + Claude drafts urgent escalation
#    1d — "Final notice" critical + Claude drafts same-day action
# ═══════════════════════════════════════════════════════════════════════════════

_DEADLINE_LABELS = {
    'inspection_contingency_date': 'Inspection Contingency Removal',
    'loan_contingency_date':       'Loan / Financing Contingency',
    'appraisal_contingency_date':  'Appraisal Contingency Removal',
    'seller_response_deadline':    'Seller Response to Repair Request',
    'repair_completion_deadline':  'Agreed Repair Completion',
    'close_of_escrow_date':        'Close of Escrow',
}

_DEADLINE_THRESHOLDS = [
    (1,  'critical', 'Final notice'),
    (3,  'critical', 'Urgent action required'),
    (7,  'warning',  'Action required'),
    (14, 'info',     'Heads up'),
]

_DEADLINE_COOLDOWN_HOURS = 20  # Don't re-alert same deadline within 20 hours


def _job_deadline_monitor():
    """
    Daily job (6:45am PT). Checks all active watches for approaching deadlines.
    Fires Claude reasoning for each deadline within threshold.
    """
    logger.info("📅 [DeadlineMonitor] Starting deadline scan...")
    try:
        from models import PropertyWatch, User, AgentAlert
        db = _get_db()
        today = datetime.utcnow().date()

        watches = PropertyWatch.query.filter_by(is_active=True).all()
        watches_with_deadlines = [
            w for w in watches
            if any(getattr(w, field, None) for field in _DEADLINE_LABELS)
        ]
        logger.info(f"📅 [DeadlineMonitor] {len(watches_with_deadlines)} watches have deadlines set")

        for watch in watches_with_deadlines:
            try:
                _check_deadlines_for_watch(watch, db, today)
            except Exception as e:
                logger.error(f"📅 [DeadlineMonitor] Error on watch {watch.id}: {e}", exc_info=True)

        logger.info("📅 [DeadlineMonitor] Deadline scan complete")
    except Exception as e:
        logger.error(f"📅 [DeadlineMonitor] Fatal error: {e}", exc_info=True)


def _check_deadlines_for_watch(watch, db, today):
    """Check all deadline fields on a single watch and alert as needed."""
    from models import User, AgentAlert

    # Cooldown: skip if checked very recently
    if (watch.last_deadline_check_at and
            (_now() - watch.last_deadline_check_at).total_seconds() < _DEADLINE_COOLDOWN_HOURS * 3600):
        return

    watch.last_deadline_check_at = _now()
    db.session.commit()

    user = User.query.get(watch.user_id)
    if not user:
        return

    # Build a list of all approaching deadlines that need alerts
    approaching = []
    for field, label in _DEADLINE_LABELS.items():
        deadline_date = getattr(watch, field, None)
        if not deadline_date:
            continue

        days_remaining = (deadline_date - today).days

        # Skip if already passed (we don't alert on expired deadlines — too late)
        if days_remaining < 0:
            continue

        # Find the highest-priority threshold this deadline hits
        for threshold_days, severity, urgency_label in _DEADLINE_THRESHOLDS:
            if days_remaining <= threshold_days:
                # Check if we already sent this exact alert recently
                recent = AgentAlert.query.filter(
                    AgentAlert.watch_id == watch.id,
                    AgentAlert.alert_type == 'deadline_alert',
                    AgentAlert.created_at >= datetime.utcnow() - timedelta(hours=_DEADLINE_COOLDOWN_HOURS),
                    AgentAlert.title.contains(label),
                ).first()
                if not recent:
                    approaching.append({
                        'field': field,
                        'label': label,
                        'date': deadline_date,
                        'days_remaining': days_remaining,
                        'severity': severity,
                        'urgency_label': urgency_label,
                        'threshold': threshold_days,
                    })
                break  # Only alert at the highest threshold hit

    if not approaching:
        return

    # ── Claude reasons about the full deadline picture ──────────────────────
    logger.info(f"📅 [DeadlineMonitor] {len(approaching)} approaching deadlines on watch {watch.id}")
    _run_deadline_reasoning(watch, user, approaching, today, db)


def _run_deadline_reasoning(watch, user, approaching, today, db):
    """
    Claude receives the full escrow timeline and all approaching deadlines,
    then decides: what is the buyer's most urgent action right now?
    Produces a specific, actionable alert for each approaching deadline.
    """
    client = _get_anthropic_client()

    # Load the original analysis for context
    original_result = {}
    if watch.analysis_id:
        try:
            from models import Analysis
            rec = Analysis.query.get(watch.analysis_id)
            if rec and rec.result_json:
                original_result = json.loads(rec.result_json)
        except Exception:
            pass

    # Build the full timeline for Claude's context
    all_deadlines_text = "\n".join([
        f"  - {label}: {getattr(watch, field).isoformat() if getattr(watch, field) else 'not set'} "
        f"({(getattr(watch, field) - today).days if getattr(watch, field) else '?'} days from today)"
        for field, label in _DEADLINE_LABELS.items()
        if getattr(watch, field, None)
    ])

    approaching_text = "\n".join([
        f"  ⚠ {d['label']}: {d['days_remaining']} day(s) remaining [{d['urgency_label']}]"
        for d in approaching
    ])

    repair_estimate = original_result.get('repair_estimate', {})
    total_repairs = repair_estimate.get('total_low', 0)
    offer_strategy = original_result.get('offer_strategy', {})
    rec_offer = offer_strategy.get('recommended_offer', watch.asking_price or 0)

    # Previous alerts on this watch (context for Claude)
    from models import AgentAlert
    past_alerts = AgentAlert.query.filter_by(watch_id=watch.id).order_by(
        AgentAlert.created_at.desc()
    ).limit(10).all()
    past_context = "\n".join([
        f"  [{a.alert_type}] {a.title} ({a.created_at.strftime('%Y-%m-%d')})"
        for a in past_alerts
    ]) or "  None yet"

    try:
        prompt = f"""You are an expert real estate transaction advisor monitoring a buyer's escrow timeline.

PROPERTY: {watch.address}
ASKING PRICE: ${(watch.asking_price or 0):,.0f}
RECOMMENDED OFFER: ${(rec_offer or 0):,.0f}
TOTAL DOCUMENTED REPAIRS: ${(total_repairs or 0):,.0f}
TODAY: {today.isoformat()}

FULL ESCROW TIMELINE:
{all_deadlines_text}

DEADLINES REQUIRING IMMEDIATE ATTENTION:
{approaching_text}

PREVIOUS ALERTS SENT TO THIS BUYER:
{past_context}

For each approaching deadline above, provide:
1. A one-sentence URGENCY ASSESSMENT: why does this specific deadline matter RIGHT NOW?
2. THE SINGLE MOST IMPORTANT ACTION the buyer should take TODAY (be specific — who to call, what to say, what to send)
3. The CONSEQUENCE of missing this deadline (in plain language, no legal jargon)
4. If relevant: how does this deadline interact with the documented repair issues (${{total_repairs:,.0f}} in repairs)?

Format your response as a JSON array with one object per approaching deadline:
[
  {{
    "deadline_label": "...",
    "urgency_assessment": "...",
    "action_today": "...",
    "consequence_if_missed": "...",
    "repair_interaction": "..." // null if not relevant
  }}
]

Return ONLY the JSON array. No preamble, no explanation."""

        raw = _ai_call(prompt, max_tokens=1500)

        # Parse response
        import re as _re
        json_match = _re.search(r'\[.*\]', raw, _re.DOTALL)
        reasoned = json.loads(json_match.group(0)) if json_match else []

    except Exception as e:
        logger.warning(f"📅 AI deadline reasoning failed: {e} — falling back to template alerts")
        reasoned = []

    # Build and send one alert per approaching deadline
    for i, deadline in enumerate(approaching):
        reasoning = reasoned[i] if i < len(reasoned) else {}
        label = deadline['label']
        days = deadline['days_remaining']
        severity = deadline['severity']
        deadline_date = deadline['date']

        # Compose alert content
        if reasoning:
            action = reasoning.get('action_today', '')
            consequence = reasoning.get('consequence_if_missed', '')
            urgency = reasoning.get('urgency_assessment', '')
            repair_note = reasoning.get('repair_interaction')
        else:
            # Fallback template when Claude is unavailable
            action = f"Contact your agent immediately to confirm status of the {label}."
            consequence = "Missing this deadline may remove critical buyer protections."
            urgency = f"This deadline is {days} day(s) away."
            repair_note = None

        emoji = '🚨' if severity == 'critical' else '⚠️' if severity == 'warning' else '📅'
        days_str = f"{days} day{'s' if days != 1 else ''}"
        title = f"{emoji} {label} — {days_str} remaining"

        body_html = f"""
    <p style='margin-bottom:12px;font-size:13px;color:#94a3b8;'>{urgency}</p>

    <div style='padding:14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:10px;margin-bottom:12px;'>
      <div style='font-size:11px;font-weight:700;color:#f97316;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;'>Action required today</div>
      <div style='font-size:13px;color:#f1f5f9;line-height:1.6;'>{action}</div>
    </div>

    <div style='padding:12px 14px;background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.15);border-radius:8px;margin-bottom:12px;'>
      <div style='font-size:11px;font-weight:700;color:#ef4444;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;'>If you miss this deadline</div>
      <div style='font-size:12px;color:#94a3b8;line-height:1.5;'>{consequence}</div>
    </div>

    {f"<div style='margin-top:12px;padding:12px;background:rgba(96,165,250,.06);border-radius:8px;'><strong style='color:#60a5fa;'>Repair estimates:</strong> {repair_note}</div>" if repair_note else ""}

    <div style='padding:10px 12px;background:rgba(255,255,255,.03);border-radius:6px;font-size:11px;color:#475569;'>
      Deadline: <strong style='color:#f1f5f9;'>{deadline_date.strftime("%B %d, %Y")}</strong>
      &nbsp;·&nbsp; Property: {watch.address[:50]}
    </div>"""

        # Send email
        _send(
            user.email,
            f"{emoji} Deadline alert: {label} in {days_str} — {watch.address[:40]}",
            _email_html(title, body_html, watch.address, emoji,
                        cta_label="View deadline details →",
                        cta_url=(f"https://www.getofferwise.ai/settings?tab=alerts&utm_source=deadline_alert&utm_medium=email")),
            user_id=user.id
        )

        # Save to AgentAlert for dashboard display
        _save_alert(
            watch.id, user.id,
            alert_type='deadline_alert',
            severity=severity,
            title=title,
            body=f"{action} — {consequence}",
            detail={
                'deadline_field': deadline['field'],
                'deadline_label': label,
                'deadline_date': deadline_date.isoformat(),
                'days_remaining': days,
                'action_today': action,
                'consequence': consequence,
                'claude_reasoned': bool(reasoning),
            },
            email_sent=True
        )

        logger.info(f"📅 [DeadlineMonitor] Alert sent: {label} ({days}d) for watch {watch.id}")

    # Notify linked professionals (agents, inspectors) with a summary
    if approaching:
        pro_title = f"📅 Deadline alert: {watch.address[:40]} has {len(approaching)} approaching deadline(s)"
        most_urgent = min(approaching, key=lambda d: d['days_remaining'])
        pro_body = f"""
    <p style='margin-bottom:12px;'>Your client has <strong>{len(approaching)}</strong> approaching escrow deadline(s) on <strong>{watch.address}</strong>.</p>
    <div style='padding:12px 14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;margin-bottom:10px;'>
      <strong style='color:#f97316;'>Most urgent:</strong> {most_urgent['label']} — <strong>{most_urgent['days_remaining']} day(s)</strong> remaining.
    </div>
    <p style='font-size:12px;color:#94a3b8;'>Your client has been notified directly. If you need to coordinate on any of these deadlines, contact them today.</p>"""
        _notify_linked_professionals(watch, pro_title, pro_body, watch.address, "📅")


def register_monitoring_jobs(scheduler):
    """Call this from app.py after the existing scheduler jobs are registered.

    v5.86.95 fix: wrap every job in Flask app_context. APScheduler fires jobs
    on a background thread with no context, which breaks SQLAlchemy session
    resolution (`Model.query` raises "Working outside of application context").
    Observed in production 2026-04-23 as EarthquakeMonitor fatal errors at 07:15.
    """
    from flask import current_app
    from functools import wraps

    # Capture the app object once, now, while we're still in the calling context.
    # current_app is a proxy — we need the actual object to bind into the
    # background thread that APScheduler will use later.
    app_obj = current_app._get_current_object()

    def _with_ctx(fn):
        """Wrap a no-arg job function so it runs inside Flask app_context."""
        @wraps(fn)
        def wrapped():
            with app_obj.app_context():
                return fn()
        return wrapped

    scheduler.add_job(
        _with_ctx(_job_deadline_monitor), 'cron', hour=6, minute=45,
        id='agentic_deadlines', replace_existing=True
    )
    scheduler.add_job(
        _with_ctx(_job_comps_monitor), 'cron', hour=7, minute=0,
        id='agentic_comps', replace_existing=True
    )
    scheduler.add_job(
        _with_ctx(_job_earthquake_monitor), 'cron', hour=7, minute=15,
        id='agentic_earthquake', replace_existing=True
    )
    scheduler.add_job(
        _with_ctx(_job_price_monitor), 'cron', hour=7, minute=30,
        id='agentic_price', replace_existing=True
    )
    scheduler.add_job(
        _with_ctx(_job_permit_monitor), 'cron', hour=7, minute=45,
        id='agentic_permit', replace_existing=True
    )
    logger.info("✅ Agentic monitoring jobs registered (deadlines@6:45, comps, earthquake, price, permit) — v5.86.95 app_context wrapped")


# ═══════════════════════════════════════════════════════════════════════════════
# P2 AGENTIC FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

# ── P2.4: EARTHQUAKE → STRUCTURAL REASSESSMENT ──────────────────────────────
# After detecting M4.0+, Claude reads the original risk tier, decides whether
# the quake magnitude + structural risk tier warrants a partial AI re-analysis,
# then generates a targeted structural diff and delivers before/after to buyer.

def _get_anthropic_client():
    """Return an Anthropic client or None if key not set. Kept for compatibility."""
    import anthropic, os
    key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not key:
        return None
    try:
        return anthropic.Anthropic(api_key=key)
    except Exception as e:
        logger.warning(f"Anthropic client init failed: {e}")
        return None


def _ai_call(prompt: str, max_tokens: int = 600) -> str:
    """Resilient AI call — Anthropic primary, OpenAI GPT-4o fallback."""
    from ai_client import get_ai_response
    return get_ai_response(prompt, max_tokens=max_tokens)


def _run_structural_reassessment(watch, mag: float, place: str, original_result: dict) -> dict | None:
    """
    Agent decision: should a seismic event trigger a structural re-analysis?
    If yes, call Claude with only the structural context and return a reassessment dict.
    Returns None if agent decides re-analysis is not warranted.
    """
    client = _get_anthropic_client()
    if not client:
        return None

    # Extract structural context from original analysis
    categories = original_result.get('categories') or original_result.get('category_scores') or []
    structural_cat = next(
        (c for c in categories if 'foundation' in str(c.get('name','')).lower()
         or 'structural' in str(c.get('name','')).lower()),
        None
    )
    original_risk_tier  = original_result.get('risk_score', {}).get('risk_tier') or \
                          original_result.get('risk_tier', 'UNKNOWN')
    original_risk_score = original_result.get('risk_score', {}).get('overall_risk_score') or \
                          original_result.get('overall_risk_score', 50)
    structural_score    = structural_cat.get('score', 0) if structural_cat else 0
    structural_issues   = structural_cat.get('key_issues', []) if structural_cat else []
    address             = watch.address

    try:
        prompt = f"""
Property: {address}
Earthquake: M{mag} at {place}

Existing structural assessment:
- Risk tier: {original_risk_tier}
- Overall risk score: {original_risk_score}/100
- Structural/foundation category score: {structural_score}/100
- Known structural issues: {json.dumps(structural_issues)}

Reply with this exact JSON schema:
{{
  "reanalysis_warranted": true/false,
  "reasoning": "one sentence explaining the decision",
  "urgency": "critical|high|medium",
  "revised_structural_risk": "higher|unchanged|uncertain",
  "buyer_action": "concrete 1-2 sentence action item for the buyer",
  "negotiation_leverage": "how this event changes their leverage — 1-2 sentences",
  "re_inspection_recommended": true/false
}}
"""
        system = (
            "You are OfferWise's seismic risk agent. Given an earthquake event and a property's "
            "existing structural assessment, decide: (1) should a structural re-analysis be triggered? "
            "(2) what does the buyer need to know right now? "
            "Reply ONLY with valid JSON — no markdown, no preamble."
        )
        raw = _ai_call(prompt, max_tokens=1200)
        raw = raw.strip().replace('```json', '').replace('```', '').strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"Structural reassessment AI call failed: {e}")
        return None


def _check_earthquake_p2(watch, db, mag: float, place: str, quake_time: str):
    """
    P2.4: After detecting an earthquake, run agentic structural reassessment.
    Only called when M4.0+ is confirmed. Supplements (does not replace) the
    base earthquake alert already sent by _check_earthquake_for_watch.
    """
    from models import Analysis, User
    if not watch.analysis_id:
        return

    try:
        analysis = Analysis.query.get(watch.analysis_id)
        if not analysis or not analysis.result_json:
            return
        result = json.loads(analysis.result_json)
    except Exception as e:
        logger.warning(f"P2.4: Could not load analysis {watch.analysis_id}: {e}")
        return

    reassessment = _run_structural_reassessment(watch, mag, place, result)
    if not reassessment:
        return
    if not reassessment.get('reanalysis_warranted', False):
        logger.info(f"🌍 [P2.4] Agent decided re-analysis NOT warranted for M{mag} at watch {watch.id}: {reassessment.get('reasoning')}")
        return

    logger.info(f"🌍 [P2.4] Agent triggered structural reassessment for M{mag} at watch {watch.id}")

    user = User.query.get(watch.user_id)
    if not user:
        return

    urgency_color = {'critical': '#ef4444', 'high': '#f97316', 'medium': '#f59e0b'}.get(
        reassessment.get('urgency', 'medium'), '#f59e0b')
    risk_changed   = reassessment.get('revised_structural_risk', 'uncertain')
    buyer_action   = reassessment.get('buyer_action', '')
    leverage       = reassessment.get('negotiation_leverage', '')
    re_insp        = reassessment.get('re_inspection_recommended', False)
    reasoning      = reassessment.get('reasoning', '')

    title = f"⚠️ AI structural reassessment after M{mag} earthquake"
    reinsp_html = (
        '<div style="padding:10px 14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);'
        'border-radius:8px;"><strong style="color:#f97316;">Re-inspection recommended</strong>'
        ' — request a structural re-inspection before your contingency deadline. Reference the M'
        + str(mag) + ' event of ' + quake_time + '.</div>'
    ) if re_insp else ''
    body_html = f"""
    <div style='padding:12px 14px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:8px;margin-bottom:14px;'>
      <strong style='color:{urgency_color};'>Structural risk: {risk_changed.upper()}</strong><br>
      <span style='font-size:13px;color:#94a3b8;'>{reasoning}</span>
    </div>
    <div style='margin-bottom:12px;'>
      <div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>What you should do now</div>
      <p style='font-size:13px;color:#f1f5f9;'>{buyer_action}</p>
    </div>
    <div style='margin-bottom:12px;'>
      <div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>Negotiation impact</div>
      <p style='font-size:13px;color:#f1f5f9;'>{leverage}</p>
    </div>
    {reinsp_html}
    """

    _send(user.email,
          f"🧠 AI re-analysis: M{mag} earthquake changes your structural risk at {watch.address[:35]}",
          _email_html(title, body_html, watch.address, "🌍", cta_url=(f"https://www.getofferwise.ai/app?analysis={watch.analysis_id}&utm_source=earthquake_alert&utm_medium=email" if watch.analysis_id else "https://www.getofferwise.ai/app?utm_source=earthquake_alert&utm_medium=email"), cta_label="View structural analysis →"),
          user_id=user.id)

    _save_alert(
        watch.id, user.id, 'structural_reassessment', reassessment.get('urgency', 'warning'),
        title,
        f"AI decided re-analysis warranted after M{mag}. Risk: {risk_changed}. Re-inspection: {re_insp}.",
        {'magnitude': mag, 'place': place, 'reassessment': reassessment},
        email_sent=True
    )

    # Notify inspector — they may need to file a supplemental report
    _notify_linked_professionals(watch, title, body_html, watch.address, "🌍")
    logger.info(f"🌍 [P2.4] Structural reassessment delivered to buyer + professionals: watch {watch.id}")


# ── P2.5: PRICE CUT → OFFER RECALCULATION ───────────────────────────────────
# When AVM drops ≥2%, Claude reads the original offer_strategy, decides if the
# drop materially changes the recommended offer, regenerates only the offer
# section, and delivers a before/after diff with updated talking points.

def _run_offer_recalculation(watch, original_result: dict,
                             current_avm: float, drop: float, drop_pct: float) -> dict | None:
    """
    Agent decision: does this price drop materially change the offer recommendation?
    Returns updated offer strategy dict, or None if no material change.
    """
    client = _get_anthropic_client()
    if not client:
        return None

    original_offer_strategy = original_result.get('offer_strategy', {})
    original_recommended    = original_offer_strategy.get('recommended_offer', 0)
    original_aggressive     = original_offer_strategy.get('aggressive_offer', 0)
    original_discount_pct   = original_offer_strategy.get('discount_percentage', 0)
    asking_price            = watch.asking_price or 0
    risk_tier               = original_result.get('risk_score', {}).get('risk_tier') or \
                              original_result.get('risk_tier', 'MEDIUM')
    repair_cost_high        = original_result.get('risk_score', {}).get('total_repair_cost_high') or 0

    try:
        _system = (
            "You are OfferWise's offer strategy agent. Given a market value drop on a property "
            "under contract, recalculate the buyer's optimal offer position. "
            "Think like the best buyer's agent in Silicon Valley — specific, aggressive, actionable. "
            "Reply ONLY with valid JSON — no markdown, no preamble."
        )
        _prompt = f"""Property: {watch.address}
Asking price: ${asking_price:,.0f}
Original recommended offer: ${original_recommended:,.0f} ({original_discount_pct:.1f}% below ask)
Original aggressive offer: ${original_aggressive:,.0f}
Risk tier at analysis: {risk_tier}
Known repair costs: ${repair_cost_high:,.0f} (high estimate)

Market value change:
- AVM dropped {drop_pct:.1f}% (${drop:,.0f}) since your analysis
- Current AVM: ${current_avm:,.0f}
- Original baseline: ${(current_avm + drop):,.0f}

Recalculate the offer strategy given this new market data.
Reply with this exact JSON schema:
{{
  "material_change": true/false,
  "new_recommended_offer": <integer dollar amount>,
  "new_aggressive_offer": <integer dollar amount>,
  "new_discount_pct": <float, e.g. 8.5>,
  "delta_vs_original": <integer, new_recommended minus original_recommended — negative means lower offer>,
  "reasoning": "2 sentences explaining the revised position",
  "talking_points": ["point 1", "point 2", "point 3"],
  "opening_script": "exact words the buyer should say or email to their agent — 2-3 sentences"
}}"""
        _full_prompt = _system + "\n\n" + _prompt
        raw = _ai_call(_full_prompt, max_tokens=1400)
        raw = raw.strip().replace('```json', '').replace('```', '').strip()
        result = json.loads(raw)
        if not result.get('material_change', False):
            return None
        return result
    except Exception as e:
        logger.warning(f"Offer recalculation Claude call failed: {e}")
        return None


def _check_price_p2(watch, db, current_avm: float, drop: float, drop_pct: float):
    """
    P2.5: After detecting a price drop, run agentic offer recalculation.
    Only called when drop ≥ 2%. Supplements the base price alert.
    """
    from models import Analysis, User
    if not watch.analysis_id:
        return

    try:
        analysis = Analysis.query.get(watch.analysis_id)
        if not analysis or not analysis.result_json:
            return
        result = json.loads(analysis.result_json)
    except Exception as e:
        logger.warning(f"P2.5: Could not load analysis {watch.analysis_id}: {e}")
        return

    recalc = _run_offer_recalculation(watch, result, current_avm, drop, drop_pct)
    if not recalc:
        logger.info(f"💰 [P2.5] Agent decided no material offer change for {drop_pct:.1f}% drop at watch {watch.id}")
        return

    logger.info(f"💰 [P2.5] Agent triggered offer recalculation for watch {watch.id}")

    user = User.query.get(watch.user_id)
    if not user:
        return

    new_offer     = recalc.get('new_recommended_offer', 0)
    new_aggr      = recalc.get('new_aggressive_offer', 0)
    delta         = recalc.get('delta_vs_original', 0)
    reasoning     = recalc.get('reasoning', '')
    talking_pts   = recalc.get('talking_points', [])
    script        = recalc.get('opening_script', '')
    new_disc_pct  = recalc.get('new_discount_pct', 0)

    delta_str = f"${abs(delta):,.0f} {'lower' if delta < 0 else 'higher'} than your original offer"
    tp_html   = ''.join(f"<li style='margin-bottom:6px;'>✓ {pt}</li>" for pt in talking_pts)

    title = f"🧠 Your offer strategy just changed — AI updated your numbers"
    body_html = f"""
    <p style='margin-bottom:14px;'>The market moved. OfferWise re-ran your offer calculation with the latest data.</p>
    <div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px;'>
      <div style='padding:14px;background:rgba(255,255,255,.04);border-radius:10px;text-align:center;'>
        <div style='font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:6px;'>Original offer</div>
        <div style='font-size:20px;font-weight:800;color:#94a3b8;'>{_format_price(result.get("offer_strategy",{{}}).get("recommended_offer",0))}</div>
      </div>
      <div style='padding:14px;background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.2);border-radius:10px;text-align:center;'>
        <div style='font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#22c55e;margin-bottom:6px;'>Updated offer</div>
        <div style='font-size:20px;font-weight:800;color:#22c55e;'>{_format_price(new_offer)}</div>
        <div style='font-size:11px;color:#64748b;margin-top:4px;'>{new_disc_pct:.1f}% below ask</div>
      </div>
    </div>
    <div style='padding:10px 14px;background:rgba(96,165,250,.06);border-left:3px solid #60a5fa;border-radius:0 8px 8px 0;margin-bottom:14px;'>
      <strong style='color:#60a5fa;'>Delta: {delta_str}</strong><br>
      <span style='font-size:13px;color:#94a3b8;'>{reasoning}</span>
    </div>
    <div style='margin-bottom:14px;'>
      <div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:8px;'>Negotiation talking points</div>
      <ul style='list-style:none;font-size:13px;color:#f1f5f9;padding:0;'>{tp_html}</ul>
    </div>
    <div style='padding:12px 14px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:8px;'>
      <div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#f97316;margin-bottom:6px;'>What to say to your agent right now</div>
      <p style='font-size:13px;color:#f1f5f9;font-style:italic;'>"{script}"</p>
    </div>
    <p style='margin-top:12px;font-size:11px;color:#475569;'>Aggressive offer: {_format_price(new_aggr)} · Based on AVM drop of {drop_pct:.1f}% (${drop:,.0f})</p>
    """

    _send(user.email,
          f"🧠 Offer updated: {watch.address[:40]} — new target is {_format_price(new_offer)}",
          _email_html(title, body_html, watch.address, "💰", cta_url=(f"https://www.getofferwise.ai/app?analysis={watch.analysis_id}&utm_source=price_drop_alert&utm_medium=email" if watch.analysis_id else "https://www.getofferwise.ai/app?utm_source=price_drop_alert&utm_medium=email"), cta_label="See your updated offer →"),
          user_id=user.id)

    _save_alert(
        watch.id, user.id, 'offer_recalculation', 'warning',
        title,
        f"Offer updated from {_format_price(result.get('offer_strategy',{{}}).get('recommended_offer',0))} "
        f"to {_format_price(new_offer)} ({delta_str}).",
        {'recalculation': recalc, 'drop_pct': drop_pct, 'drop': drop, 'current_avm': current_avm},
        email_sent=True
    )

    _notify_linked_professionals(watch, title, body_html, watch.address, "💰")
    logger.info(f"💰 [P2.5] Offer recalculation delivered: {_format_price(new_offer)} for watch {watch.id}")


# ── P2.6: INSPECTOR → REALTOR ONE-CLICK SEND ────────────────────────────────
# After inspector generates a report, one click sends it to the linked realtor.
# The realtor gets their own branded share token + real-time buyer engagement alerts.
# Closes the professional loop: inspector action → coordinated workflow across personas.

def fire_inspector_to_agent_send(
    inspector_report_id: int,
    agent_email: str,
    agent_name: str = '',
    message: str = '',
):
    """
    P2.6: Inspector sends their completed report to a realtor with one click.
    Creates an AgentShare record linked to the inspector report, emails the
    agent with a branded link, and pre-wires buyer concern signal notifications.
    """
    try:
        from models import InspectorReport, AgentShare, Inspector, User, Agent
        from app import db
        import secrets

        report = InspectorReport.query.get(inspector_report_id)
        if not report:
            logger.warning(f"P2.6: InspectorReport {inspector_report_id} not found")
            return {'success': False, 'error': 'Report not found'}

        inspector = Inspector.query.filter_by(user_id=report.inspector_user_id).first()
        inspector_user = User.query.get(report.inspector_user_id) if report.inspector_user_id else None
        inspector_name = (inspector.business_name or
                          (inspector_user.name if inspector_user else '')) or 'Your Inspector'

        # Find or create an Agent record for this email
        agent_user = User.query.filter_by(email=agent_email).first()
        agent_record = Agent.query.filter_by(
            user_id=agent_user.id).first() if agent_user else None

        # Build analysis JSON from inspector report
        analysis_json = report.analysis_json  # already stored on the report

        # Create AgentShare record
        share_token = secrets.token_urlsafe(20)
        share = AgentShare(
            agent_id=agent_record.id if agent_record else 0,
            agent_user_id=agent_user.id if agent_user else 0,
            property_address=report.property_address,
            property_price=report.property_price,
            buyer_name=report.buyer_name,
            buyer_email=report.buyer_email,
            analysis_json=analysis_json,
            share_token=share_token,
            agent_name_on_report=agent_name or agent_email,
            agent_biz_on_report='',
            has_text=bool(analysis_json),
        )
        db.session.add(share)
        db.session.commit()

        share_url = f"https://getofferwise.ai/agent-report/{share_token}"

        # Compose email to agent — branded, specific, actionable
        buyer_display = report.buyer_name or report.buyer_email or 'your client'
        property_short = (report.property_address or '')[:50]
        inspector_msg_block = (
            f"<div style='padding:12px 14px;background:rgba(96,165,250,.06);border-left:3px solid #60a5fa;"
            f"border-radius:0 8px 8px 0;margin-bottom:16px;font-size:13px;color:#f1f5f9;font-style:italic;'>"
            f"&ldquo;{message}&rdquo;</div>"
        ) if message else ''

        html = f"""
<div style='font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:560px;margin:0 auto;background:#060d1a;color:#f1f5f9;border-radius:14px;overflow:hidden;'>
  <div style='padding:24px 28px;background:linear-gradient(135deg,#0f1e35,#131f33);border-bottom:1px solid rgba(255,255,255,.08);'>
    <div style='font-size:13px;color:#94a3b8;margin-bottom:4px;'>From {inspector_name} via OfferWise</div>
    <h2 style='font-size:20px;font-weight:800;margin:0;'>Inspection report ready for {property_short}</h2>
  </div>
  <div style='padding:24px 28px;'>
    <p style='margin-bottom:16px;font-size:14px;color:#94a3b8;'>{inspector_name} has completed an inspection analysis for <strong style='color:#f1f5f9;'>{buyer_display}</strong> and forwarded it to you directly.</p>
    {inspector_msg_block}
    <div style='padding:16px;background:rgba(249,115,22,.06);border:1px solid rgba(249,115,22,.2);border-radius:10px;margin-bottom:20px;'>
      <div style='font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#f97316;margin-bottom:8px;'>🔭 OfferWatch — you're now watching this property</div>
      <p style='font-size:12px;color:#94a3b8;margin:0;'>You'll get a real-time alert the moment {buyer_display} opens this report — so you can call them while the inspection is top of mind. You'll also receive alerts if the price drops, new permits are filed, or a seismic event occurs near the property.</p>
    </div>
    <a href='{share_url}' style='display:block;text-align:center;padding:14px;background:linear-gradient(90deg,#f97316,#f59e0b);border-radius:10px;color:white;font-size:15px;font-weight:700;text-decoration:none;margin-bottom:16px;'>View Report → {property_short}</a>
    <p style='font-size:11px;color:#475569;text-align:center;'>This report was prepared by {inspector_name} using OfferWise AI. Forward to {buyer_display} or share directly from your portal.</p>
  </div>
</div>"""

        _send(
            agent_email,
            f"🏠 Inspection report ready: {property_short} — {buyer_display}",
            html,
            email_type='inspector_to_agent',
        )

        logger.info(f"✅ [P2.6] Inspector→Agent send: report {inspector_report_id} → {agent_email}, token {share_token}")
        return {'success': True, 'share_url': share_url, 'share_token': share_token}

    except Exception as e:
        logger.error(f"P2.6: fire_inspector_to_agent_send failed: {e}")
        return {'success': False, 'error': str(e)}
