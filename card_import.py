"""card_import.py — classify credit-card activity into infra-cost vendors.

Reusable engine behind the admin "Import card activity" feature. Pure and
side-effect-free (no app/DB imports) so it unit-tests cleanly.

Flow: a card-activity CSV (columns Date, Description, Amount) is classified row
by row. Matched vendor charges are grouped by (vendor, charge-month) and summed
into one monthly invoice each. Ad channels that are already synced on the API
Costs page (Google Ads, Reddit Ads) are deliberately skipped so they are never
double-counted; payments/credits and unmatched (personal) charges are skipped too.
"""

import csv
import io
from calendar import monthrange
from datetime import date, datetime

# Card-descriptor substring (UPPER) -> canonical InfraVendor name.
# Order matters only in that the first containing-match wins.
VENDOR_ALIASES = {
    'RENDER': 'Render',
    'CLAUDE.AI': 'Anthropic', 'CLAUDE AI': 'Anthropic', 'ANTHROPIC': 'Anthropic',
    'RENTCAST': 'RentCast',
    'HUNTER.IO': 'Hunter', 'HUNTER': 'Hunter',
    'GOOGLE*CLOUD': 'Google Cloud', 'GOOGLE *CLOUD': 'Google Cloud',
    'PORKBUN': 'Porkbun',
    'INTER NACHI': 'InterNACHI', 'INTERNACHI': 'InterNACHI',
    'ZILLOW': 'Zillow',
    'DELAWARE CORP': 'Delaware franchise tax',
    'RESEND': 'Resend', 'SENTRY': 'Sentry', 'GITHUB': 'GitHub', 'MAILGUN': 'Mailgun',
    'WALKSCORE': 'WalkScore', 'GREATSCHOOLS': 'GreatSchools', 'PERMITDATA': 'PermitData',
    'APOLLO': 'Apollo', 'SNOV': 'Snov', 'MILLIONVERIFIER': 'MillionVerifier',
    'ZEROBOUNCE': 'ZeroBounce', 'STRIPE': 'Stripe',
}

# Vendors auto-created on import (not in the default seed) -> (category, emoji).
AUTO_CREATE_CATEGORY = {
    'Zillow': ('ads', '\U0001F4E3'),
    'Delaware franchise tax': ('corporate', '\U0001F3DB'),
}


def classify_charge(description, amount):
    """Return (vendor_name | None, skip_reason | None).

    skip_reason is one of: 'payment_or_credit', 'ad_synced', 'unmatched'.
    """
    if amount is None or amount <= 0:
        return None, 'payment_or_credit'
    u = (description or '').upper()
    # Ad channels are already tracked live on the API Costs page — never import.
    if 'GOOGLE *ADS' in u or 'GOOGLE*ADS' in u:
        return None, 'ad_synced'
    if 'REDDIT' in u and 'ADS' in u:
        return None, 'ad_synced'
    for needle, vendor in VENDOR_ALIASES.items():
        if needle in u:
            return vendor, None
    return None, 'unmatched'


def _parse_amount(s):
    try:
        return float(str(s).replace(',', '').replace('$', '').strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _parse_date(s):
    s = (s or '').strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_card_csv(csv_text):
    """Parse card-activity CSV text → grouped monthly vendor invoices + skip summary.

    Columns are matched case-insensitively; only Date, Description, Amount are used.
    Returns: {
        'invoices': [ {vendor, period_start, period_end, amount, charge_count}, ... ],
        'skipped':  { 'ad_synced': {count, amount}, 'unmatched': {count, amount},
                      'payment_or_credit': {count}, 'no_date': int },
        'matched_total': float,
    }
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    groups = {}  # (vendor, year, month) -> {amount, count}
    skipped = {
        'ad_synced': {'count': 0, 'amount': 0.0},
        'unmatched': {'count': 0, 'amount': 0.0},
        'payment_or_credit': {'count': 0},
        'no_date': 0,
    }
    for row in reader:
        r = {(k or '').strip().lower(): v for k, v in row.items()}
        amt = _parse_amount(r.get('amount'))
        vendor, reason = classify_charge(r.get('description', ''), amt)
        if reason == 'payment_or_credit':
            skipped['payment_or_credit']['count'] += 1
            continue
        if reason in ('ad_synced', 'unmatched'):
            skipped[reason]['count'] += 1
            skipped[reason]['amount'] = round(skipped[reason]['amount'] + (amt or 0.0), 2)
            continue
        d = _parse_date(r.get('date'))
        if not d:
            skipped['no_date'] += 1
            continue
        key = (vendor, d.year, d.month)
        g = groups.setdefault(key, {'amount': 0.0, 'count': 0})
        g['amount'] += amt
        g['count'] += 1

    invoices = []
    for (vendor, year, month), g in sorted(groups.items()):
        ps = date(year, month, 1)
        pe = date(year, month, monthrange(year, month)[1])
        invoices.append({
            'vendor': vendor,
            'period_start': ps.isoformat(),
            'period_end': pe.isoformat(),
            'amount': round(g['amount'], 2),
            'charge_count': g['count'],
        })
    return {
        'invoices': invoices,
        'skipped': skipped,
        'matched_total': round(sum(i['amount'] for i in invoices), 2),
    }
