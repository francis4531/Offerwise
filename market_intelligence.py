"""
Market Intelligence Engine (v5.62.92)
=====================================
Generates nightly snapshots of market conditions for active users.
One scheduled job produces three outputs:
  1. Matched listings with preference scores (email alerts)
  2. Market deltas (Market Pulse on dashboard)
  3. New comparable sales vs analysed properties (Living Analysis cards)

Usage:
    from market_intelligence import run_nightly_intelligence
    stats = run_nightly_intelligence(db_session)
"""

import json
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger('market_intel')


def run_nightly_intelligence(db_session):
    """Run market intelligence for all active users.
    
    Returns dict: {users_processed, snapshots_created, alerts_generated, errors}.
    """
    from models import User, Property, MarketSnapshot, ListingPreference

    today = date.today()
    stats = {'users_processed': 0, 'snapshots_created': 0, 'alerts_generated': 0, 'errors': 0}

    active_user_ids = _find_active_users(db_session)
    logger.info(f"Market intel: {len(active_user_ids)} active users to process")

    for user_id in active_user_ids:
        try:
            snapshot = _process_user(db_session, user_id, today)
            if snapshot:
                stats['users_processed'] += 1
                stats['snapshots_created'] += 1
                stats['alerts_generated'] += snapshot.alerts_generated
                # Send alert email if warranted
                if snapshot.alerts_generated > 0:
                    try:
                        from models import User
                        user = db_session.query(User).get(user_id)
                        if user:
                            from drip_campaign import send_market_intelligence_email
                            send_market_intelligence_email(db_session, user, snapshot)
                    except Exception as email_err:
                        logger.warning(f"Market intel email error for user {user_id}: {email_err}")
        except Exception as e:
            logger.error(f"Market intel error for user {user_id}: {e}")
            stats['errors'] += 1

    db_session.commit()
    logger.info(f"Market intel complete: {stats}")
    return stats


def _find_active_users(db_session):
    """Find users who should receive market intelligence."""
    from models import User, Property, ListingPreference

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    ninety_days_ago = datetime.utcnow() - timedelta(days=90)
    active = set()

    for (uid,) in db_session.query(User.id).filter(User.last_login >= thirty_days_ago).all():
        active.add(uid)
    for (uid,) in db_session.query(Property.user_id).filter(Property.analyzed_at >= ninety_days_ago).distinct().all():
        active.add(uid)
    for (uid,) in db_session.query(ListingPreference.user_id).filter(ListingPreference.action == 'save').distinct().all():
        active.add(uid)

    return active


def _process_user(db_session, user_id, today):
    """Generate a market snapshot for one user. Returns the snapshot or None."""
    from models import User, MarketSnapshot, ListingPreference
    from nearby_listings import get_nearby_listings, extract_preferences, apply_preference_boost

    user = db_session.query(User).get(user_id)
    if not user:
        return None

    zip_code = _get_user_zip(db_session, user)
    if not zip_code:
        return None

    # Skip if already have snapshot for today
    if db_session.query(MarketSnapshot).filter_by(
        user_id=user_id, zip_code=zip_code, snapshot_date=today
    ).first():
        return None

    # Fetch listings
    result = get_nearby_listings(zip_code=zip_code, limit=10)
    if result.get('error'):
        return None

    listings = result.get('listings', [])
    market = result.get('market', {})

    # Apply preferences
    pref_records = db_session.query(ListingPreference).filter_by(
        user_id=user_id
    ).order_by(ListingPreference.created_at.desc()).limit(100).all()

    prefs = extract_preferences(pref_records) if pref_records else None
    if prefs:
        for l in listings:
            l['score'] = apply_preference_boost(l, prefs)
        listings.sort(key=lambda x: x.get('score', 0), reverse=True)

    # Previous snapshot for deltas
    prev = db_session.query(MarketSnapshot).filter(
        MarketSnapshot.user_id == user_id,
        MarketSnapshot.zip_code == zip_code,
        MarketSnapshot.snapshot_date < today
    ).order_by(MarketSnapshot.snapshot_date.desc()).first()

    # Market stats
    median = market.get('median_price')
    avg_dom = market.get('avg_dom')
    inventory = len(listings)
    new_count = sum(1 for l in listings if (l.get('days_on_market') or 999) <= 7)

    # Compute deltas
    median_delta = None
    inv_delta = None
    dom_delta = None
    if prev:
        if median and prev.median_price and prev.median_price > 0:
            median_delta = round((median - prev.median_price) / prev.median_price * 100, 1)
        if prev.active_inventory is not None:
            inv_delta = inventory - prev.active_inventory
        if avg_dom and prev.avg_dom:
            dom_delta = avg_dom - prev.avg_dom

    # Top matches
    matched = [{
        'address': l.get('address', ''),
        'price': l.get('price'),
        'score': l.get('score'),
        'risk_tier': l.get('risk_tier'),
        'offer_range_low': l.get('offer_range_low'),
        'offer_range_high': l.get('offer_range_high'),
        'days_on_market': l.get('days_on_market'),
    } for l in listings[:5]]

    top = matched[0] if matched else None

    # New comps
    new_comps = _find_new_comps(db_session, user_id, listings, prev)

    # Alert scoring
    alerts = 0
    if top and (top.get('score') or 0) >= 75:
        alerts += 1
    if median_delta and abs(median_delta) >= 1.5:
        alerts += 1
    if new_comps:
        alerts += 1

    snapshot = MarketSnapshot(
        user_id=user_id,
        zip_code=zip_code,
        snapshot_date=today,
        median_price=median,
        active_inventory=inventory,
        avg_dom=avg_dom,
        new_listings_count=new_count,
        median_price_delta_pct=median_delta,
        inventory_delta=inv_delta,
        avg_dom_delta=dom_delta,
        matched_listings_json=json.dumps(matched, default=str),
        top_match_score=top.get('score') if top else None,
        top_match_address=top.get('address') if top else None,
        new_comps_json=json.dumps(new_comps, default=str) if new_comps else None,
        new_comps_count=len(new_comps) if new_comps else 0,
        alerts_generated=alerts,
    )
    db_session.add(snapshot)
    return snapshot


def _get_user_zip(db_session, user):
    """Get primary ZIP for a user from their most recent analysis or waitlist."""
    from models import Property, Waitlist
    import re

    prop = db_session.query(Property).filter_by(user_id=user.id).filter(
        Property.analyzed_at.isnot(None)
    ).order_by(Property.analyzed_at.desc()).first()

    if prop and prop.address:
        m = re.search(r'\b(\d{5})\b', prop.address)
        if m:
            return m.group(1)

    wl = db_session.query(Waitlist).filter_by(email=user.email).filter(
        Waitlist.result_zip.isnot(None)
    ).order_by(Waitlist.created_at.desc()).first()

    return wl.result_zip if wl else None


def _find_new_comps(db_session, user_id, current_listings, prev_snapshot):
    """Detect listings that vanished (likely sold) and cross-reference
    against the user's analysed properties as comparable sales."""
    from models import Property, Analysis

    if not prev_snapshot or not prev_snapshot.matched_listings_json:
        return []

    try:
        prev_listings = json.loads(prev_snapshot.matched_listings_json)
    except (json.JSONDecodeError, TypeError):
        return []

    current_addrs = {l.get('address', '').lower().strip() for l in current_listings}
    vanished = [l for l in prev_listings
                if l.get('address', '').lower().strip() not in current_addrs]

    if not vanished:
        return []

    analyses = db_session.query(Property, Analysis).join(
        Analysis, Analysis.property_id == Property.id
    ).filter(
        Property.user_id == user_id,
        Property.analyzed_at.isnot(None),
        Analysis.status == 'completed'
    ).order_by(Property.analyzed_at.desc()).limit(5).all()

    if not analyses:
        return []

    comps = []
    for v in vanished:
        comp_price = v.get('price', 0)
        if not comp_price:
            continue
        for prop, analysis in analyses:
            try:
                result = json.loads(analysis.result_json) if analysis.result_json else {}
                recommended = result.get('offer_strategy', {}).get('recommended_offer', 0)
                if recommended:
                    comps.append({
                        'property_address': prop.address,
                        'comp_address': v.get('address'),
                        'comp_price': comp_price,
                        'recommended_offer': recommended,
                        'vs_recommended': 'below' if comp_price < recommended else 'above',
                        'difference': comp_price - recommended,
                    })
            except (json.JSONDecodeError, TypeError):
                continue
    return comps[:10]


# ── Dashboard API Helpers ───────────────────────────────────────

def get_latest_snapshot(db_session, user_id, zip_code=None):
    """Get most recent snapshot for dashboard Market Pulse."""
    from models import MarketSnapshot
    q = db_session.query(MarketSnapshot).filter_by(user_id=user_id)
    if zip_code:
        q = q.filter_by(zip_code=zip_code)
    return q.order_by(MarketSnapshot.snapshot_date.desc()).first()


def get_comp_updates(db_session, user_id):
    """Get comp updates grouped by property address.
    
    Returns dict: {property_address: {comps, below_count, above_count, position}}.
    """
    from models import MarketSnapshot

    latest = db_session.query(MarketSnapshot).filter_by(
        user_id=user_id
    ).order_by(MarketSnapshot.snapshot_date.desc()).first()

    if not latest or not latest.new_comps_json:
        return {}

    try:
        comps = json.loads(latest.new_comps_json)
    except (json.JSONDecodeError, TypeError):
        return {}

    by_property = {}
    for c in comps:
        addr = c.get('property_address', '')
        if addr not in by_property:
            by_property[addr] = {'comps': [], 'below_count': 0, 'above_count': 0}
        by_property[addr]['comps'].append(c)
        if c.get('vs_recommended') == 'below':
            by_property[addr]['below_count'] += 1
        else:
            by_property[addr]['above_count'] += 1

    for data in by_property.values():
        if data['below_count'] > data['above_count']:
            data['position'] = 'improved'
        elif data['above_count'] > data['below_count']:
            data['position'] = 'weakened'
        else:
            data['position'] = 'unchanged'

    return by_property
