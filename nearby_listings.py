"""
Nearby Listings Engine — Market Intelligence Without the Onboarding Wall
========================================================================
Fetches active listings near the user and runs mini-analysis with the full
Core moat (public records, market context, risk tier, offer range) — but
requires ZERO profile setup.  Only needs a ZIP code.

Entry points:
  1. Dashboard widget — user already analyzed a property -> we know their ZIP
  2. Drip emails — waitlist user did a Risk Check -> we know their ZIP
  3. API endpoint — /api/nearby-listings?zip=94040

Uses RentCast calls and analysis logic but strips
away profile/filter/persistence/behavioral-learning layers.  Result is a
list of enriched listings ready for frontend display or email rendering.

RentCast API usage: 2 calls per request (1 listings + 1 market).
Market data is cached in-memory for 6 hours to minimize API spend.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

RENTCAST_BASE = 'https://api.rentcast.io/v1'

# In-memory cache: zip -> {data, ts}
_market_cache = {}
_listings_cache = {}
CACHE_TTL = 24 * 3600  # 24 hours — listings don't change hourly, saves RentCast quota


# PUBLIC API

def get_nearby_listings(zip_code, limit=5, buyer_context=None):
    """
    Fetch active listings in zip_code and enrich each with
    intelligence.  No profile, no onboarding, no persistence.

    Args:
        zip_code:      5-digit US ZIP code
        limit:         Max listings to return (default 5)
        buyer_context: Optional dict with keys from User model:
                       {repair_tolerance, max_budget, biggest_regret}

    Returns:
        {listings, market, zip_code, api_calls, cached}
    """
    api_key = os.environ.get('RENTCAST_API_KEY', '')
    if not api_key:
        return _err('RENTCAST_API_KEY not configured.')

    zip_code = str(zip_code).strip()[:5]
    if not zip_code or len(zip_code) != 5 or not zip_code.isdigit():
        return _err('Invalid ZIP code.')

    # Validate ZIP is in a real US range (00501-99950)
    zip_num = int(zip_code)
    if zip_num < 501 or zip_num > 99950:
        return _err('ZIP code out of valid US range.')

    buyer = buyer_context or {}
    api_calls = 0

    # Fetch listings (cached)
    cache_key = '%s:%d' % (zip_code, limit)
    listings_raw, cached = _cached_get(_listings_cache, cache_key)
    if listings_raw is None:
        listings_raw, calls = _fetch_listings(api_key, zip_code, limit)
        api_calls += calls
        _listings_cache[cache_key] = {'data': listings_raw, 'ts': time.time()}
    else:
        logger.info("Nearby: listings cache hit for ZIP %s", zip_code)

    # Fetch market data (cached)
    market, m_cached = _cached_get(_market_cache, zip_code)
    if market is None:
        market, calls = _fetch_market(api_key, zip_code)
        api_calls += calls
        _market_cache[zip_code] = {'data': market, 'ts': time.time()}

    # Enrich each listing
    enriched = []
    for listing in (listings_raw or []):
        try:
            enriched.append(_enrich_listing(listing, market, buyer))
        except Exception as e:
            logger.warning("Nearby: enrichment error: %s", e)

    # Sort by opportunity (highest score first)
    enriched.sort(key=lambda x: x.get('score', 0), reverse=True)

    return {
        'listings': enriched[:limit],
        'market': market,
        'zip_code': zip_code,
        'api_calls': api_calls,
        'cached': api_calls == 0,
    }


# RENTCAST API CALLS

def _fetch_listings(api_key, zip_code, limit):
    headers = {'X-Api-Key': api_key, 'Accept': 'application/json'}
    params = {'zipCode': zip_code, 'status': 'active', 'limit': min(limit * 3, 30)}
    try:
        resp = requests.get('%s/listings/sale' % RENTCAST_BASE,
                            params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        listings = data if isinstance(data, list) else data.get('listings', [])
        logger.info("Nearby: fetched %d listings in ZIP %s", len(listings), zip_code)
        return listings, 1
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else 0
        logger.warning("Nearby: RentCast HTTP %d for ZIP %s", status, zip_code)
        return [], 1
    except Exception as e:
        logger.warning("Nearby: listings fetch failed for ZIP %s: %s", zip_code, e)
        return [], 0


def _fetch_market(api_key, zip_code):
    headers = {'X-Api-Key': api_key, 'Accept': 'application/json'}
    try:
        resp = requests.get('%s/markets' % RENTCAST_BASE,
                            params={'zipCode': zip_code},
                            headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        sale = data.get('saleData', {})
        market = {
            'median_price': sale.get('medianPrice'),
            'avg_price': sale.get('averagePrice'),
            'avg_dom': sale.get('averageDaysOnMarket'),
            'avg_ppsqft': sale.get('averagePricePerSquareFoot'),
            'total_listings': sale.get('totalListings'),
            'price_trend': sale.get('medianPriceTrend'),
        }
        return market, 1
    except Exception as e:
        logger.warning("Nearby: market fetch failed for ZIP %s: %s", zip_code, e)
        return {}, 1


# LISTING ENRICHMENT

def _enrich_listing(listing, market, buyer):
    price = listing.get('price') or listing.get('listPrice') or 0
    sqft = listing.get('squareFootage') or listing.get('sqft') or 0
    beds = listing.get('bedrooms') or 0
    baths = listing.get('bathrooms') or 0
    year_built = listing.get('yearBuilt') or 0
    dom = listing.get('daysOnMarket') or 0
    ptype = listing.get('propertyType', '')
    ppsqft = (price / sqft) if sqft > 0 else 0

    median = market.get('median_price') or 0
    avg_ppsqft = market.get('avg_ppsqft') or 0
    avg_dom = market.get('avg_dom') or 30

    address = (listing.get('formattedAddress')
               or listing.get('addressLine1')
               or listing.get('address', ''))

    # Public records
    last_sale_price = listing.get('lastSalePrice')
    last_sale_date = listing.get('lastSaleDate')
    ownership_yrs = None
    seller_equity_pct = None
    flags = []

    if last_sale_date:
        try:
            sold_dt = datetime.fromisoformat(str(last_sale_date).replace('Z', '+00:00'))
            ownership_yrs = round((datetime.now() - sold_dt.replace(tzinfo=None)).days / 365.25, 1)
            if ownership_yrs < 2:
                flags.append('Owned less than 2 years — possible flip')
            elif ownership_yrs > 20:
                flags.append('Long-term owner (20+ years) — may have deferred maintenance')
        except (ValueError, TypeError):
            pass

    if last_sale_price and price > 0:
        seller_equity_pct = round((price - last_sale_price) / last_sale_price * 100, 1)

    tax_assessment = listing.get('taxAssessment') or listing.get('assessedValue')
    if tax_assessment and price > 0:
        tax_vs_price = round((price / tax_assessment - 1) * 100, 1)
        if tax_vs_price > 30:
            flags.append('Asking %d%% above tax assessment' % int(tax_vs_price))
        elif tax_vs_price < -10:
            flags.append('Asking below tax assessment — possible motivated seller')

    owner_occ = listing.get('ownerOccupied')
    if owner_occ is False:
        flags.append('Non-owner-occupied — may be tenant-occupied')

    hoa = listing.get('hoa') or listing.get('hoaFee')
    hoa_fee = None
    if isinstance(hoa, dict):
        hoa_fee = hoa.get('fee') or hoa.get('amount')
    elif isinstance(hoa, (int, float)):
        hoa_fee = hoa

    # Value estimation
    estimates = []
    if median and median > 0:
        estimates.append(median)
    if avg_ppsqft and sqft > 0:
        estimates.append(avg_ppsqft * sqft)
    if tax_assessment and tax_assessment > 0:
        estimates.append(tax_assessment * 1.15)

    est_mid = round(sum(estimates) / len(estimates)) if estimates else None

    # Price vs. market
    vs_market_pct = None
    if median and median > 0 and price > 0:
        vs_market_pct = round((price / median - 1) * 100, 1)

    # Offer range
    anchor = est_mid or price
    adj = 0.0
    if avg_dom > 0 and dom > avg_dom * 1.5:
        adj -= 0.03
    if dom > 90:
        adj -= 0.05
    elif dom > 60:
        adj -= 0.03
    elif dom > 30:
        adj -= 0.01
    if seller_equity_pct is not None and seller_equity_pct > 50:
        adj -= 0.02

    offer_low = round(anchor * (0.95 + adj))
    offer_high = round(anchor * (1.0 + adj))
    if offer_high > price:
        offer_high = price
    if offer_low > offer_high:
        offer_low = round(offer_high * 0.95)

    # Risk tier
    risk_score = 0
    condition_flags = []
    age = (datetime.now().year - year_built) if year_built else 0

    if age > 50:
        risk_score += 3
        condition_flags.append('Pre-1975 — check for lead paint, asbestos, outdated wiring')
    elif age > 30:
        risk_score += 2
        condition_flags.append('30+ year old systems may need replacement')
    elif age > 15:
        risk_score += 1

    if owner_occ is False:
        risk_score += 1

    repair_tol = (buyer.get('repair_tolerance') or 'moderate').lower()
    if repair_tol == 'low' and age > 20:
        risk_score += 2
        condition_flags.append("Your low repair tolerance may conflict with this home's age")

    if est_mid and price > 0 and price > est_mid * 1.15:
        risk_score += 2
        overpriced_pct = round((price / est_mid - 1) * 100)
        condition_flags.append('Asking %d%% above estimated value' % overpriced_pct)

    if risk_score <= 1:
        risk_tier = 'Low'
    elif risk_score <= 3:
        risk_tier = 'Medium'
    elif risk_score <= 5:
        risk_tier = 'High'
    else:
        risk_tier = 'Critical'

    # Negotiation leverage
    leverage = 50
    leverage_tips = []
    if dom > (avg_dom or 30) * 2:
        leverage += 20
        leverage_tips.append('%d days on market vs. area average of %d — seller likely motivated' % (dom, avg_dom))
    elif dom > (avg_dom or 30) * 1.3:
        leverage += 10
        leverage_tips.append('Above-average days on market gives you room to negotiate')
    elif dom < 7:
        leverage -= 15
        leverage_tips.append('Very new listing — limited leverage')

    if seller_equity_pct is not None and seller_equity_pct > 50:
        leverage += 10
        leverage_tips.append('Seller has %d%% equity — they can afford to negotiate' % int(seller_equity_pct))

    if est_mid and price > est_mid * 1.05:
        leverage += 10
        leverage_tips.append('Asking above estimated value — data supports a lower offer')

    leverage = max(0, min(100, leverage))

    # Monthly cost estimate (PITI + HOA)
    monthly_cost = None
    loan_amount = price * 0.80
    if loan_amount > 0:
        mr = 0.065 / 12
        n = 360
        monthly_pi = loan_amount * (mr * (1 + mr)**n) / ((1 + mr)**n - 1)
        monthly_tax = (listing.get('propertyTaxes') or listing.get('annualTax') or price * 0.0125) / 12
        monthly_ins = price * 0.004 / 12
        monthly_hoa_val = hoa_fee or 0
        monthly_cost = round(monthly_pi + monthly_tax + monthly_ins + monthly_hoa_val)

    # Opportunity score
    score = 50
    if vs_market_pct is not None:
        if vs_market_pct < -8:
            score += 25
        elif vs_market_pct < -3:
            score += 15
        elif vs_market_pct < 3:
            score += 5
        elif vs_market_pct > 10:
            score -= 10
    if dom > (avg_dom or 30) * 1.5:
        score += 15
    elif dom < 7:
        score += 5
    if risk_tier == 'Low':
        score += 10
    elif risk_tier == 'Critical':
        score -= 15
    score = max(0, min(100, score))

    # Briefing (template-based, no AI call)
    briefing = _build_briefing(
        price=price, est_mid=est_mid, median=median, dom=dom,
        avg_dom=avg_dom, beds=beds, baths=baths, sqft=sqft,
        year_built=year_built, vs_market_pct=vs_market_pct,
        risk_tier=risk_tier, leverage=leverage, leverage_tips=leverage_tips,
        seller_equity_pct=seller_equity_pct, ownership_yrs=ownership_yrs,
    )

    return {
        'address': address,
        'city': listing.get('city', ''),
        'state': listing.get('state', ''),
        'zip_code': listing.get('zipCode') or listing.get('zip', ''),
        'price': price,
        'bedrooms': beds,
        'bathrooms': baths,
        'sqft': sqft,
        'year_built': year_built,
        'property_type': ptype,
        'days_on_market': dom,
        'price_per_sqft': round(ppsqft, 2) if ppsqft else None,
        'listing_url': listing.get('listingUrl') or listing.get('url'),
        'offer_range_low': offer_low,
        'offer_range_high': offer_high,
        'avm_estimate': est_mid,
        'vs_market_pct': vs_market_pct,
        'risk_tier': risk_tier,
        'leverage': leverage,
        'leverage_tips': leverage_tips,
        'condition_flags': condition_flags + flags,
        'monthly_cost': monthly_cost,
        'score': round(score, 1),
        'briefing': briefing,
    }


# BRIEFING GENERATOR

def _build_briefing(**kw):
    parts = []
    price = kw.get('price', 0)
    est_mid = kw.get('est_mid')
    vs_mkt = kw.get('vs_market_pct')
    dom = kw.get('dom', 0)
    avg_dom = kw.get('avg_dom', 30)
    risk_tier = kw.get('risk_tier', 'Medium')
    leverage = kw.get('leverage', 50)
    year_built = kw.get('year_built', 0)
    equity = kw.get('seller_equity_pct')
    ownership_yrs = kw.get('ownership_yrs')

    if vs_mkt is not None:
        if vs_mkt < -5:
            parts.append("Priced %d%% below the ZIP median — this looks undervalued relative to the local market." % int(abs(vs_mkt)))
        elif vs_mkt > 8:
            parts.append("Asking %d%% above the ZIP median — the seller is pricing aggressively." % int(vs_mkt))
        else:
            parts.append("Priced in line with the local market.")

    if avg_dom and dom > avg_dom * 2:
        parts.append("It has been sitting for %d days (area average is %d), which suggests the seller may be motivated to negotiate." % (dom, avg_dom))
    elif dom < 7:
        parts.append("Just listed — expect competition if it is priced well.")

    if equity is not None and equity > 60 and dom > (avg_dom or 30):
        parts.append("The seller has significant equity (%d%%) and the listing is aging, so there is room for a data-backed lower offer." % int(equity))
    elif ownership_yrs and ownership_yrs < 2:
        parts.append("Short ownership period may signal a flip or relocation pressure.")

    if risk_tier in ('High', 'Critical') and year_built:
        age = datetime.now().year - year_built
        parts.append("Built in %d (%d years old) — budget for potential system replacements before making an offer." % (year_built, age))

    if leverage >= 70:
        parts.append("Overall, you have strong negotiation leverage here.")
    elif leverage <= 30:
        parts.append("Leverage is limited — a strong initial offer may be needed to compete.")

    return ' '.join(parts) if parts else "Run a full OfferWise analysis with the seller disclosure and inspection report to get a data-backed offer price."


# EMAIL RENDERING

def render_listings_email_html(listings, zip_code, market, cta_base_url):
    """Render enriched listings as email-safe HTML for drip emails."""
    median = market.get('median_price') or 0
    avg_dom = market.get('avg_dom') or 0

    parts = ['<h2 style="margin:0 0 8px 0;color:#f8fafc;font-size:24px;font-weight:700;">New listings near you in %s</h2>' % zip_code]
    parts.append('<p style="margin:0 0 24px 0;color:#94a3b8;font-size:15px;line-height:1.5;">%d properties just listed%s%s. Here is what we would consider before making an offer on each one.</p>' % (
        len(listings),
        (' — ZIP median is $%s' % '{:,}'.format(median)) if median else '',
        (', average days on market is %d' % avg_dom) if avg_dom else '',
    ))

    for l in listings[:5]:
        risk_color = {'Low': '#10b981', 'Medium': '#f59e0b',
                      'High': '#f97316', 'Critical': '#ef4444'}.get(
                          l.get('risk_tier', 'Medium'), '#f59e0b')

        offer_low = l.get('offer_range_low', 0)
        offer_high = l.get('offer_range_high', 0)
        vs_mkt = l.get('vs_market_pct')
        vs_mkt_str = ''
        if vs_mkt is not None:
            if vs_mkt < 0:
                vs_mkt_str = '<span style="color:#10b981;font-weight:600;">%d%% vs. median</span>' % int(vs_mkt)
            else:
                vs_mkt_str = '<span style="color:#f59e0b;font-weight:600;">+%d%% vs. median</span>' % int(vs_mkt)

        sqft_str = (' &middot; %s sqft' % '{:,}'.format(l.get('sqft', 0))) if l.get('sqft') else ''
        yb_str = (' &middot; Built %d' % l.get('year_built')) if l.get('year_built') else ''

        parts.append('''
        <div style="background:rgba(15,23,42,0.6);border:1px solid rgba(96,165,250,0.2);border-radius:12px;padding:20px;margin-bottom:16px;">
            <div style="margin-bottom:8px;">
                <span style="font-weight:700;color:#f8fafc;font-size:16px;">%s</span>
                <span style="float:right;background:rgba(96,165,250,0.15);color:#60a5fa;padding:4px 10px;border-radius:6px;font-size:13px;font-weight:700;">$%s</span>
            </div>
            <div style="color:#94a3b8;font-size:13px;margin-bottom:12px;clear:both;">
                %d bd &middot; %s ba%s%s &middot; %d days on market
            </div>
            <div style="margin-bottom:12px;font-size:13px;">
                <span style="color:#64748b;">Offer range:</span>
                <span style="color:#cbd5e1;font-weight:600;">$%s &ndash; $%s</span>
                &nbsp;&nbsp;
                <span style="color:#64748b;">Risk:</span>
                <span style="color:%s;font-weight:600;">%s</span>
                %s
            </div>
            <p style="margin:0 0 12px 0;color:#cbd5e1;font-size:14px;line-height:1.5;">%s</p>
            <a href="%s/app" style="color:#60a5fa;font-size:14px;font-weight:600;text-decoration:none;">
                Got documents for this property? Get the full analysis &rarr;
            </a>
        </div>
        ''' % (
            l.get('address', ''),
            '{:,}'.format(l.get('price', 0)),
            l.get('bedrooms', 0),
            str(l.get('bathrooms', 0)),
            sqft_str,
            yb_str,
            l.get('days_on_market', 0),
            '{:,}'.format(offer_low),
            '{:,}'.format(offer_high),
            risk_color,
            l.get('risk_tier', 'Medium'),
            ('&nbsp;&nbsp;' + vs_mkt_str) if vs_mkt_str else '',
            l.get('briefing', ''),
            cta_base_url,
        ))

    return '\n'.join(parts)


# HELPERS

def _cached_get(cache, key):
    entry = cache.get(key)
    if entry and time.time() - entry['ts'] < CACHE_TTL:
        return entry['data'], True
    return None, False


def _err(msg):
    return {'listings': [], 'market': {}, 'zip_code': '', 'api_calls': 0,
            'cached': False, 'error': msg}


# =============================================================================
# PREFERENCE LEARNING ENGINE (v5.62.85)
# =============================================================================
# Extracts implicit preferences from save/dismiss history and adjusts
# listing scores. This is the moat — Zillow shows you listings, OfferWise
# learns what YOU actually want and ranks accordingly.

import hashlib
from statistics import median as _median


def listing_hash(address):
    """Deterministic hash for a listing address."""
    return hashlib.sha256((address or '').strip().lower().encode()).hexdigest()[:32]


def extract_preferences(history):
    """Extract preference signals from save/dismiss history.
    
    Args:
        history: list of ListingPreference records (from DB)
    
    Returns:
        dict with preference signals:
          - price_range: (low, high) from saved listings
          - beds_pref: median bedrooms of saved
          - sqft_pref: median sqft of saved
          - age_pref: 'newer' (<20yr median), 'older' (>40yr), 'any'
          - risk_tolerance: 'low' (mostly Low-risk saves), 'high', 'any'
          - dom_pref: 'stale' (high DOM saves), 'fresh' (low DOM), 'any'
    """
    saved = [h for h in history if h.action == 'save']
    dismissed = [h for h in history if h.action == 'dismiss']
    
    if len(saved) < 2:
        return None  # Not enough data to infer preferences
    
    prefs = {}
    
    # Price range from saved listings
    prices = [h.price for h in saved if h.price]
    if prices:
        prefs['price_low'] = min(prices) * 0.85
        prefs['price_high'] = max(prices) * 1.15
        prefs['price_median'] = _median(prices)
    
    # Bedroom preference
    beds = [h.bedrooms for h in saved if h.bedrooms]
    if beds:
        prefs['beds_pref'] = round(_median(beds))
    
    # Sqft preference
    sqfts = [h.sqft for h in saved if h.sqft]
    if sqfts:
        prefs['sqft_pref'] = round(_median(sqfts))
    
    # Age preference
    years = [h.year_built for h in saved if h.year_built]
    if years:
        med_year = _median(years)
        from datetime import date
        age = date.today().year - med_year
        if age < 20:
            prefs['age_pref'] = 'newer'
        elif age > 40:
            prefs['age_pref'] = 'older'
        else:
            prefs['age_pref'] = 'any'
    
    # Risk tolerance from saved vs dismissed
    saved_risks = [h.risk_tier for h in saved if h.risk_tier]
    if saved_risks:
        low_count = sum(1 for r in saved_risks if r == 'Low')
        high_count = sum(1 for r in saved_risks if r in ('High', 'Critical'))
        if low_count > len(saved_risks) * 0.6:
            prefs['risk_tolerance'] = 'low'
        elif high_count > len(saved_risks) * 0.4:
            prefs['risk_tolerance'] = 'high'
        else:
            prefs['risk_tolerance'] = 'any'
    
    # DOM preference
    doms = [h.days_on_market for h in saved if h.days_on_market is not None]
    if doms:
        med_dom = _median(doms)
        if med_dom > 45:
            prefs['dom_pref'] = 'stale'
        elif med_dom < 14:
            prefs['dom_pref'] = 'fresh'
        else:
            prefs['dom_pref'] = 'any'
    
    # Dismissed patterns (negative signals)
    if dismissed:
        dismiss_prices = [h.price for h in dismissed if h.price]
        if dismiss_prices and prices:
            prefs['dismiss_price_median'] = _median(dismiss_prices)
    
    prefs['saved_count'] = len(saved)
    prefs['dismissed_count'] = len(dismissed)
    
    return prefs


def apply_preference_boost(listing, prefs):
    """Apply preference-based score adjustment to an enriched listing.
    
    Args:
        listing: dict from _enrich_listing()
        prefs: dict from extract_preferences()
    
    Returns:
        Adjusted score (float). Original score is in listing['score'].
    """
    if not prefs:
        return listing.get('score', 50)
    
    score = listing.get('score', 50)
    boost = 0
    
    # Price alignment (+/- 15 points)
    price = listing.get('price', 0)
    if price and 'price_low' in prefs and 'price_high' in prefs:
        if prefs['price_low'] <= price <= prefs['price_high']:
            boost += 10
            # Extra boost if close to median
            if 'price_median' in prefs:
                pct_diff = abs(price - prefs['price_median']) / prefs['price_median']
                if pct_diff < 0.1:
                    boost += 5
        elif price > prefs.get('price_high', float('inf')) * 1.3:
            boost -= 10  # Way over budget preference
    
    # Bedroom match (+/- 8 points)
    beds = listing.get('bedrooms')
    if beds and 'beds_pref' in prefs:
        diff = abs(beds - prefs['beds_pref'])
        if diff == 0:
            boost += 8
        elif diff == 1:
            boost += 3
        elif diff >= 3:
            boost -= 5
    
    # Sqft alignment (+/- 5 points)
    sqft = listing.get('sqft')
    if sqft and 'sqft_pref' in prefs:
        ratio = sqft / prefs['sqft_pref']
        if 0.8 <= ratio <= 1.2:
            boost += 5
        elif ratio < 0.5 or ratio > 2.0:
            boost -= 5
    
    # Age preference (+/- 8 points)
    year = listing.get('year_built')
    if year and 'age_pref' in prefs:
        from datetime import date
        age = date.today().year - year
        if prefs['age_pref'] == 'newer' and age < 15:
            boost += 8
        elif prefs['age_pref'] == 'newer' and age > 50:
            boost -= 5
        elif prefs['age_pref'] == 'older' and age > 40:
            boost += 8
        elif prefs['age_pref'] == 'older' and age < 10:
            boost -= 3
    
    # Risk tolerance (+/- 5 points)
    risk = listing.get('risk_tier')
    if risk and 'risk_tolerance' in prefs:
        if prefs['risk_tolerance'] == 'low' and risk == 'Low':
            boost += 5
        elif prefs['risk_tolerance'] == 'low' and risk in ('High', 'Critical'):
            boost -= 5
        elif prefs['risk_tolerance'] == 'high' and risk in ('High', 'Critical'):
            boost += 3  # Bargain hunter
    
    # DOM preference (+/- 5 points)
    dom = listing.get('days_on_market', 0)
    if dom and 'dom_pref' in prefs:
        if prefs['dom_pref'] == 'stale' and dom > 45:
            boost += 5
        elif prefs['dom_pref'] == 'fresh' and dom < 14:
            boost += 5
    
    adjusted = max(0, min(100, score + boost))
    return round(adjusted, 1)
