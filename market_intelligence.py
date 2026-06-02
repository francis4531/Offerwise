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


# ============================================================================
# MarketIntelligenceEngine — processes RentCast tool_results into a structured
# object consumed by offerwise_intelligence._generate_offer_strategy()
# ============================================================================

class _MarketStats:
    """Holds ZIP-level market stats extracted from MarketStatsTool result."""
    def __init__(self, d: dict):
        self.zip_code                = d.get('zip_code', '')
        self.median_price_per_sqft   = float(d.get('median_price_per_sqft', 0) or 0)
        self.average_days_on_market  = int(d.get('average_days_on_market', 0) or 0)
        self.total_listings          = int(d.get('total_listings', 0) or 0)
        self.new_listings_this_month = int(d.get('new_listings', 0) or 0)
        self.price_trend_pct         = float(d.get('price_trend_pct', 0) or 0)
        self.inventory_trend_pct     = float(d.get('inventory_trend_pct', 0) or 0)
        self.type_match_median       = int(d.get('type_match_median', 0) or 0)
        self.bed_match_median        = int(d.get('bed_match_median', 0) or 0)
        self.history                 = d.get('history', []) or []


class MarketIntelligence:
    """
    Structured market intelligence object returned by MarketIntelligenceEngine.
    All attributes are accessed directly by offerwise_intelligence.py via getattr().
    """
    def __init__(self):
        # Core AVM
        self.avm_price              = 0
        self.value_range_low        = 0
        self.value_range_high       = 0
        self.avm_confidence_range_pct = 0.0

        # Comp statistics
        self.comp_count             = 0
        self.comp_median_price      = 0
        self.comp_avg_price_per_sqft = 0.0
        self.comp_avg_dom           = 0
        self.asking_vs_comps_pct    = 0.0   # positive = asking above comps
        self.price_percentile       = 50

        # Distressed comps
        self.foreclosure_count      = 0
        self.short_sale_count       = 0
        self.new_construction_count = 0
        self.distressed_pct         = 0.0

        # Market temperature
        self.market_temperature     = 'neutral'   # hot / warm / neutral / buyer
        self.data_quality           = 'none'       # none / low / medium / high

        # Price positioning vs type/beds
        self.price_vs_type_median_pct = 0.0
        self.price_vs_bed_median_pct  = 0.0

        # ZIP-level market stats object (accessed as market_intel.market.*)
        self.market = None

    def to_dict(self):
        d = {k: v for k, v in self.__dict__.items() if k != 'market'}
        if self.market:
            d['market'] = self.market.__dict__
        return d


class MarketIntelligenceEngine:
    """
    Converts RentCast tool_results (already fetched by PropertyResearchAgent)
    into a MarketIntelligence object. Zero additional API calls.
    """

    def from_research_data(self, research_data: dict, asking_price: int,
                           address: str = '') -> MarketIntelligence:
        """
        Primary path: consume tool_results already fetched during analysis.
        Extracts rentcast + market_stats tool data and builds MarketIntelligence.
        """
        mi = MarketIntelligence()

        tool_results = research_data.get('tool_results', []) or []
        rentcast_data = {}
        market_stats_data = {}

        for tr in tool_results:
            if not isinstance(tr, dict):
                continue
            name   = tr.get('tool_name', '')
            status = tr.get('status', '')
            data   = tr.get('data') or {}
            if name == 'rentcast' and status == 'success':
                rentcast_data = data
            elif name == 'market_stats' and status == 'success':
                market_stats_data = data

        # Also try profile-level data
        profile = research_data.get('profile', {}) or {}

        self._populate_from_rentcast(mi, rentcast_data, asking_price)
        self._populate_from_market_stats(mi, market_stats_data)

        # Determine data quality
        if mi.avm_price > 0 and mi.comp_count >= 3:
            mi.data_quality = 'high'
        elif mi.avm_price > 0 or mi.comp_count > 0:
            mi.data_quality = 'medium'
        elif market_stats_data:
            mi.data_quality = 'low'
        else:
            mi.data_quality = 'none'

        return mi

    def _populate_from_rentcast(self, mi: MarketIntelligence, data: dict,
                                 asking_price: int):
        if not data:
            return

        avm = int(data.get('avm_price', 0) or data.get('estimated_value', 0) or 0)
        mi.avm_price      = avm
        mi.value_range_low  = int(data.get('avm_price_low', 0)  or 0)
        mi.value_range_high = int(data.get('avm_price_high', 0) or 0)

        if mi.value_range_low > 0 and mi.value_range_high > 0 and avm > 0:
            mi.avm_confidence_range_pct = round(
                (mi.value_range_high - mi.value_range_low) / avm * 100, 1)

        # Comparables
        comps = data.get('comparables', []) or []
        sold_comps = [c for c in comps if isinstance(c, dict) and
                      c.get('price', 0) > 0 and
                      c.get('status', '').lower() not in ('active', 'for sale')]
        mi.comp_count = len(sold_comps)

        if sold_comps:
            prices = [c['price'] for c in sold_comps]
            prices.sort()
            n = len(prices)
            mi.comp_median_price = int(prices[n // 2])

            ppsqft_vals = [c['price_per_sqft'] for c in sold_comps
                           if c.get('price_per_sqft', 0) > 0]
            if ppsqft_vals:
                mi.comp_avg_price_per_sqft = round(
                    sum(ppsqft_vals) / len(ppsqft_vals), 0)

            dom_vals = [c['days_on_market'] for c in sold_comps
                        if c.get('days_on_market', 0) > 0]
            if dom_vals:
                mi.comp_avg_dom = int(sum(dom_vals) / len(dom_vals))

            # Price percentile
            below = sum(1 for p in prices if p < asking_price)
            mi.price_percentile = round(below / n * 100)

            # Asking vs comp median
            if mi.comp_median_price > 0:
                mi.asking_vs_comps_pct = round(
                    (asking_price - mi.comp_median_price) / mi.comp_median_price * 100, 1)

            # Distressed
            mi.foreclosure_count      = data.get('foreclosure_count', 0) or 0
            mi.short_sale_count       = data.get('short_sale_count', 0)  or 0
            mi.new_construction_count = data.get('new_construction_count', 0) or 0
            distressed = mi.foreclosure_count + mi.short_sale_count
            mi.distressed_pct = round(distressed / n * 100, 1) if n > 0 else 0

        # AVM vs asking
        if avm > 0 and asking_price > 0:
            asking_vs_avm = (asking_price - avm) / avm * 100
            # Determine market temperature from AVM gap + DOM
            if asking_vs_avm > 5:
                mi.market_temperature = 'hot'
            elif asking_vs_avm > 1:
                mi.market_temperature = 'warm'
            elif asking_vs_avm < -5:
                mi.market_temperature = 'buyer'
            else:
                mi.market_temperature = 'neutral'

    def _populate_from_market_stats(self, mi: MarketIntelligence, data: dict):
        if not data:
            return

        ms = _MarketStats(data)
        mi.market = ms

        # Refine temperature using DOM trend
        dom = ms.average_days_on_market
        inv_trend = ms.inventory_trend_pct
        if dom > 0 and dom < 15 and inv_trend < 0:
            mi.market_temperature = 'hot'
        elif dom > 45 or inv_trend > 15:
            mi.market_temperature = 'buyer'

        # Price positioning vs type + beds
        if ms.type_match_median > 0:
            mi.price_vs_type_median_pct = round(
                (mi.avm_price - ms.type_match_median) / ms.type_match_median * 100, 1)
        if ms.bed_match_median > 0:
            mi.price_vs_bed_median_pct = round(
                (mi.avm_price - ms.bed_match_median) / ms.bed_match_median * 100, 1)


def apply_market_adjustment(recommended_offer: float, asking_price: float,
                             market_intel: 'MarketIntelligence') -> dict:
    """
    Adjusts the recommended offer based on market intelligence.
    Returns a dict consumed by offerwise_intelligence._generate_offer_strategy().
    """
    if not market_intel or market_intel.data_quality == 'none':
        return {'market_applied': False}

    avm   = market_intel.avm_price
    temp  = market_intel.market_temperature
    comps = market_intel.comp_median_price
    comp_count = market_intel.comp_count

    if avm <= 0 and comps <= 0:
        return {'market_applied': False}

    # Reference price: prefer AVM, fall back to comp median
    ref_price = avm if avm > 0 else comps

    # Asking vs AVM
    asking_vs_avm_pct = round((asking_price - ref_price) / ref_price * 100, 1) \
        if ref_price > 0 else 0.0

    # Market adjustment: nudge offer toward fair value
    # Hot market  → loosen discount slightly (max -1%)
    # Buyer market → increase discount (max -4%)
    # Neutral/warm → small adjustment based on AVM gap
    adj_pct = 0.0
    if temp == 'hot':
        # Competitive — reduce discount by up to 1%
        adj_pct = min(0.01, max(0.0, asking_vs_avm_pct / 100 * 0.5))
        buyer_leverage = 'weak'
    elif temp == 'buyer':
        # Buyer's market — increase discount by 2-4%
        adj_pct = -0.03
        buyer_leverage = 'strong'
    elif temp == 'warm':
        adj_pct = -0.005
        buyer_leverage = 'moderate'
    else:
        # Neutral — adjust by half the AVM gap
        adj_pct = max(-0.02, min(0.01, -asking_vs_avm_pct / 100 * 0.3))
        buyer_leverage = 'moderate'

    market_adjustment_amount = round(asking_price * adj_pct)
    adjusted_offer = max(0, round(recommended_offer + market_adjustment_amount))

    # Rationale text
    parts = []
    if avm > 0:
        direction = 'above' if asking_vs_avm_pct > 0 else 'below'
        parts.append(f"Asking price is {abs(asking_vs_avm_pct):.1f}% {direction} the AVM of ${avm:,}.")
    if comp_count > 0 and comps > 0:
        comp_dir = 'above' if market_intel.asking_vs_comps_pct > 0 else 'below'
        parts.append(f"{comp_count} comparable sales averaged ${comps:,} "
                     f"({abs(market_intel.asking_vs_comps_pct):.1f}% {comp_dir} asking).")
    if temp == 'hot':
        parts.append("This is a competitive market — offer strength matters.")
    elif temp == 'buyer':
        parts.append("Inventory is rising and homes are sitting longer — you have negotiating room.")
    elif temp == 'warm':
        parts.append("Market is active but not overheated — balanced negotiating position.")
    rationale = ' '.join(parts)

    return {
        'market_applied':          True,
        'market_temperature':      temp,
        'buyer_leverage':          buyer_leverage,
        'estimated_value':         avm or comps,
        'comp_median_price':       comps,
        'comp_count':              comp_count,
        'asking_vs_avm_pct':       asking_vs_avm_pct,
        'market_adjustment_amount': market_adjustment_amount,
        'market_adjustment_pct':   adj_pct,
        'adjusted_offer':          adjusted_offer,
        'rationale':               rationale,
    }
