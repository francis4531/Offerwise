"""
OfferWise Drip Campaign Engine v1.0
====================================
5-email nurture sequence for waitlist signups.
Handles scheduling, personalization, unsubscribe, and List-Unsubscribe headers.

Entry points:
  - Waitlist signup after Risk Check / Truth Check
  - Account registration (has 1 free credit)

Schedule:
  Email 1: Immediate (within 5 min of signup)
  Email 2: Day 2
  Email 3: Day 5
  Email 4: Day 9
  Email 5: Day 14

After email 5: Move to monthly newsletter (not implemented here).
"""

import os
import logging
import secrets
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get('BASE_URL', 'https://www.getofferwise.ai')
import os as _os
SUPPORT_EMAIL = _os.environ.get('SUPPORT_EMAIL', 'support@getofferwise.ai')

# Drip schedule: step -> min hours since signup
DRIP_SCHEDULE = {
    1: 0,         # Immediate
    2: 48,        # Day 2
    3: 120,       # Day 5
    4: 216,       # Day 9
    5: 336,       # Day 14
    # After step 5: monthly nearby-listings emails (steps 6, 7, 8, ...)
    # Step 6 = ~Day 44 (30 days after step 5)
    # Each subsequent step adds 30 days
}

# Step 6+ schedule: 30 days (720 hours) between each
MONTHLY_DRIP_INTERVAL_HOURS = 720  # ~30 days
MONTHLY_DRIP_START_HOURS = 336 + MONTHLY_DRIP_INTERVAL_HOURS  # Day 44
MAX_DRIP_STEP = 17  # Stop after ~1 year of monthly emails


def _drip_min_hours(step):
    """Return minimum hours since signup for a given drip step."""
    if step in DRIP_SCHEDULE:
        return DRIP_SCHEDULE[step]
    if step <= MAX_DRIP_STEP:
        # Monthly cadence starting from step 6
        return MONTHLY_DRIP_START_HOURS + (step - 6) * MONTHLY_DRIP_INTERVAL_HOURS
    return 999999  # Beyond max — never send


# =============================================================================
# UNSUBSCRIBE MANAGEMENT
# =============================================================================

def generate_unsubscribe_token():
    """Generate a unique, URL-safe unsubscribe token."""
    return secrets.token_urlsafe(32)


def get_unsubscribe_url(token):
    """Build the full unsubscribe URL for a given token."""
    return f"{BASE_URL}/unsubscribe/{token}"


def get_list_unsubscribe_headers(token):
    """
    RFC 8058 compliant headers for one-click unsubscribe.
    Gmail, Yahoo, Outlook display an 'Unsubscribe' button in the email UI.
    """
    url = get_unsubscribe_url(token)
    return {
        'List-Unsubscribe': f'<{url}>',
        'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
    }


# =============================================================================
# DRIP EMAIL TEMPLATES
# =============================================================================

def _button(text, url, color="#3b82f6"):
    return f'''
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 24px auto;">
        <tr>
            <td style="background-color: {color}; border-radius: 8px;">
                <a href="{url}" target="_blank" style="display: inline-block; padding: 14px 32px; color: #ffffff; text-decoration: none; font-weight: 600; font-size: 16px;">
                    {text}
                </a>
            </td>
        </tr>
    </table>
    '''


def _wrap(content, preview_text, unsubscribe_url):
    """Base email template with unsubscribe footer."""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OfferWise</title>
</head>
<body style="margin:0;padding:0;background-color:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
    <div style="display:none;max-height:0;overflow:hidden;">{preview_text}</div>
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color:#0f172a;">
        <tr><td align="center" style="padding:40px 20px;">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600" style="max-width:600px;background-color:#1e293b;border-radius:16px;overflow:hidden;box-shadow:0 20px 40px rgba(0,0,0,0.3);">
                <tr><td style="background:linear-gradient(135deg,#3b82f6 0%,#8b5cf6 100%);padding:32px 40px;text-align:center;">
                    <h1 style="margin:0;color:#fff;font-size:28px;font-weight:800;letter-spacing:-0.5px;">OfferWise</h1>
                </td></tr>
                <tr><td style="padding:40px;">{content}</td></tr>
                <tr><td style="background-color:#0f172a;padding:24px 40px;text-align:center;border-top:1px solid rgba(255,255,255,0.1);">
                    <p style="margin:0 0 8px 0;color:#64748b;font-size:13px;">&copy; 2026 OfferWise. All rights reserved.</p>
                    <p style="margin:0 0 12px 0;color:#64748b;font-size:13px;">
                        <a href="{BASE_URL}/privacy" style="color:#60a5fa;text-decoration:none;">Privacy</a>
                        &nbsp;&middot;&nbsp;
                        <a href="{BASE_URL}/terms" style="color:#60a5fa;text-decoration:none;">Terms</a>
                        &nbsp;&middot;&nbsp;
                        <a href="mailto:{SUPPORT_EMAIL}" style="color:#60a5fa;text-decoration:none;">Support</a>
                    </p>
                    <p style="margin:0;color:#475569;font-size:12px;">
                        <a href="{unsubscribe_url}" style="color:#475569;text-decoration:underline;">Unsubscribe from these emails</a>
                    </p>
                </td></tr>
            </table>
        </td></tr>
    </table>
</body>
</html>'''


def drip_email_1(entry):
    """Immediate: 'Here's what you missed' — personalized to their free tool session."""
    source = entry.source or ''
    address = entry.result_address or 'that property'
    grade = entry.result_grade or ''
    exposure = entry.result_exposure or 0
    unsub_url = get_unsubscribe_url(entry.unsubscribe_token)

    if 'risk' in source.lower() and exposure:
        hook = f'''
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                You just ran a Risk Check on <strong style="color:#f8fafc;">{address}</strong> and found
                <strong style="color:#f59e0b;">${exposure:,} in hidden risk exposure</strong>{f" (Grade {grade})" if grade else ""}.
            </p>
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                Government databases flagged real hazards at this address. But they only tell half the story.
                The seller's disclosure is where the <em>real</em> red flags hide — what did they know, and what did they leave out?
            </p>'''
    elif 'truth' in source.lower() and entry.result_score is not None:
        score = entry.result_score
        hook = f'''
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                You just ran a Truth Check and got a trust score of <strong style="color:{"#10b981" if score >= 70 else "#f59e0b" if score >= 40 else "#ef4444"};">{score}/100</strong>.
            </p>
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                The Truth Check analyzes one document. But the real power is cross-referencing the seller's disclosure against the inspection report —
                that's where contradictions surface that cost buyers tens of thousands of dollars.
            </p>'''
    else:
        hook = '''
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                You signed up for OfferWise — the AI that tells homebuyers exactly what to offer.
            </p>
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                Our free tools give you a snapshot. The full analysis cross-references seller disclosures, inspection reports,
                and 11 government databases to find what the seller didn't tell you — and calculates exactly what to offer.
            </p>'''

    content = f'''
        <h2 style="margin:0 0 16px 0;color:#f8fafc;font-size:24px;font-weight:700;">Here's what you found — and what's still hidden</h2>
        {hook}
        <div style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.3);border-radius:12px;padding:20px;margin:24px 0;">
            <p style="margin:0 0 12px 0;color:#f8fafc;font-size:15px;font-weight:600;">What the full analysis adds:</p>
            <p style="margin:0 0 8px 0;color:#cbd5e1;font-size:14px;line-height:1.6;">
                &#x1F50D; <strong>Seller Contradiction Detection</strong> — what they said vs. what inspectors found<br>
                &#x1F4B0; <strong>Data-Backed Offer Price</strong> — not a guess, a calculation<br>
                &#x1F4CB; <strong>Negotiation Talking Points</strong> — specific leverage you can use
            </p>
        </div>
        <p style="margin:0 0 8px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">Your first analysis is free. No credit card required.</p>
        {_button("Get Your Free Analysis &rarr;", f"{BASE_URL}/login?signup&utm_source=drip1&utm_medium=email")}
    '''

    subject = "Here's what you found — and what's still hidden"
    return subject, _wrap(content, "You ran a free check. Here's the next step.", unsub_url)


def drip_email_2(entry):
    """Day 2: Education — what sellers skip on the disclosure form."""
    unsub_url = get_unsubscribe_url(entry.unsubscribe_token)

    content = f'''
        <h2 style="margin:0 0 16px 0;color:#f8fafc;font-size:24px;font-weight:700;">
            What sellers are legally required to disclose (and what they skip)
        </h2>
        <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            Most states require sellers to complete a disclosure statement with dozens of required questions.
            Sellers are legally required to answer every one. In practice, three sections get "creative" treatment more than any others.
        </p>

        <div style="background:rgba(245,158,11,0.1);border-left:3px solid #f59e0b;padding:16px 20px;margin:20px 0;border-radius:0 8px 8px 0;">
            <p style="margin:0 0 8px 0;color:#f59e0b;font-size:14px;font-weight:600;">SECTION II-C: STRUCTURAL</p>
            <p style="margin:0;color:#cbd5e1;font-size:14px;line-height:1.5;">
                "Are you aware of any settling, slipping, sliding, or soil problems?" — Sellers who've lived in the home for years
                often mark "No" even when the inspection finds foundation cracks. This is the #1 contradiction our AI catches.
            </p>
        </div>

        <div style="background:rgba(245,158,11,0.1);border-left:3px solid #f59e0b;padding:16px 20px;margin:20px 0;border-radius:0 8px 8px 0;">
            <p style="margin:0 0 8px 0;color:#f59e0b;font-size:14px;font-weight:600;">SECTION II-A: WATER</p>
            <p style="margin:0;color:#cbd5e1;font-size:14px;line-height:1.5;">
                "Are you aware of any flooding, drainage, or grading problems?" — FEMA says the property is in a flood zone,
                the county has 47 disaster declarations, but the seller checked "No." Government data doesn't lie.
            </p>
        </div>

        <div style="background:rgba(245,158,11,0.1);border-left:3px solid #f59e0b;padding:16px 20px;margin:20px 0;border-radius:0 8px 8px 0;">
            <p style="margin:0 0 8px 0;color:#f59e0b;font-size:14px;font-weight:600;">SECTION IV: ADDITIONAL DISCLOSURES</p>
            <p style="margin:0;color:#cbd5e1;font-size:14px;line-height:1.5;">
                "Are you aware of any other material facts?" — The most commonly left blank.
                This is where permit issues, neighbor disputes, past insurance claims, and known defects should go.
                When it's blank, it's worth asking why.
            </p>
        </div>

        <p style="margin:20px 0 0 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            Have a disclosure in hand? Our Truth Check analyzes it in 15 seconds — free, no signup required.
        </p>
        {_button("Check a Disclosure &rarr;", f"{BASE_URL}/truth-check?utm_source=drip2&utm_medium=email")}
    '''

    content += '\n        <p style="margin:20px 0 0;color:#94a3b8;font-size:13px;text-align:center;line-height:1.6;">\n          Know someone buying a home?\n          <a href="{BASE_URL}/settings?tab=referrals&amp;utm_source=drip2&amp;utm_medium=email" style="color:#f97316;font-weight:700;">Refer them &mdash; you both get a free analysis.</a>\n        </p>'
    subject = "The 3 disclosure sections sellers skip most often"
    return subject, _wrap(content, "Sellers skip these 3 disclosure sections more than any others.", unsub_url)


def drip_email_3(entry):
    """Day 5: Case study — the $23K question inspectors don't answer."""
    unsub_url = get_unsubscribe_url(entry.unsubscribe_token)

    content = f'''
        <h2 style="margin:0 0 16px 0;color:#f8fafc;font-size:24px;font-weight:700;">
            The $23,000 question your inspector won't answer
        </h2>
        <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            Inspectors find problems. That's their job, and most do it well. But here's what they don't do:
            they don't tell you how those problems should change your offer.
        </p>
        <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            A recent OfferWise analysis found a property where the inspection flagged foundation movement,
            aging HVAC, and galvanized plumbing — roughly $23,000 in estimated repairs. The seller's disclosure
            said "No" to structural awareness and "No" to plumbing problems.
        </p>

        <div style="background:rgba(15,23,42,0.6);border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:24px;margin:24px 0;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                <tr>
                    <td width="50%" style="padding-right:12px;text-align:center;">
                        <div style="font-size:13px;color:#94a3b8;margin-bottom:4px;">Inspector found</div>
                        <div style="font-size:28px;font-weight:800;color:#ef4444;">$23,400</div>
                        <div style="font-size:12px;color:#94a3b8;">in repair costs</div>
                    </td>
                    <td width="50%" style="padding-left:12px;text-align:center;">
                        <div style="font-size:13px;color:#94a3b8;margin-bottom:4px;">Seller disclosed</div>
                        <div style="font-size:28px;font-weight:800;color:#f59e0b;">$0</div>
                        <div style="font-size:12px;color:#94a3b8;">of those issues</div>
                    </td>
                </tr>
            </table>
        </div>

        <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            That gap between what the inspector found and what the seller admitted? That's your negotiation leverage.
            OfferWise calculates it automatically and gives you a data-backed offer price — not a gut feeling.
        </p>
        <p style="margin:0 0 8px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            See exactly what a full report looks like:
        </p>
        {_button("View Sample Report &rarr;", f"{BASE_URL}/sample-analysis?utm_source=drip3&utm_medium=email")}
    '''

    content += '\n        <p style="margin:20px 0 0;color:#94a3b8;font-size:13px;text-align:center;line-height:1.6;">\n          Know someone buying a home?\n          <a href="{BASE_URL}/settings?tab=referrals&amp;utm_source=drip3&amp;utm_medium=email" style="color:#f97316;font-weight:700;">Refer them &mdash; you both get a free analysis.</a>\n        </p>'
    subject = "The $23,000 question your inspector won't answer"
    return subject, _wrap(content, "Inspectors find problems. They don't tell you how to change your offer.", unsub_url)


def drip_email_4(entry):
    """Day 9: Value reminder — your free credit is still waiting."""
    address = entry.result_address or ''
    unsub_url = get_unsubscribe_url(entry.unsubscribe_token)

    address_ref = f' You checked <strong style="color:#f8fafc;">{address}</strong> — still considering it?' if address else ''

    content = f'''
        <h2 style="margin:0 0 16px 0;color:#f8fafc;font-size:24px;font-weight:700;">
            Your free analysis credit is still waiting
        </h2>
        <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            When you signed up, you received one free analysis credit.
            It&rsquo;s still there, ready whenever you are.{address_ref}
        </p>
        <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            Upload a seller&rsquo;s disclosure and inspection report, and you&rsquo;ll get:
        </p>

        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:20px 0;">
            <tr>
                <td style="padding:8px 0;color:#cbd5e1;font-size:15px;">
                    &#x2705; <strong style="color:#f8fafc;">OfferScore&trade;</strong> &mdash; property quality rating (0&ndash;100)
                </td>
            </tr>
            <tr>
                <td style="padding:8px 0;color:#cbd5e1;font-size:15px;">
                    &#x2705; <strong style="color:#f8fafc;">Property Risk DNA&trade;</strong> &mdash; 64 risk dimensions analyzed
                </td>
            </tr>
            <tr>
                <td style="padding:8px 0;color:#cbd5e1;font-size:15px;">
                    &#x2705; <strong style="color:#f8fafc;">Seller Transparency Report&trade;</strong> &mdash; contradictions exposed
                </td>
            </tr>
            <tr>
                <td style="padding:8px 0;color:#cbd5e1;font-size:15px;">
                    &#x2705; <strong style="color:#f8fafc;">Recommended Offer Price</strong> &mdash; data-backed, not a guess
                </td>
            </tr>
            <tr>
                <td style="padding:8px 0;color:#cbd5e1;font-size:15px;">
                    &#x2705; <strong style="color:#f8fafc;">Negotiation Toolkit</strong> &mdash; talking points for your agent
                </td>
            </tr>
        </table>

        {_button("Use Your Free Credit &rarr;", f"{BASE_URL}/app?utm_source=drip4&utm_medium=email", "#10b981")}

        <p style="margin:20px 0 0 0;color:#94a3b8;font-size:14px;text-align:center;">
            No credit card required. Your free credit never expires.
        </p>
    '''

    subject = "Your free analysis credit is still waiting"
    return subject, _wrap(content, "Your free OfferWise credit is ready whenever you are.", unsub_url)


def drip_email_5(entry):
    """Day 14: Real stories — what buyers found with their free credit."""
    unsub_url = get_unsubscribe_url(entry.unsubscribe_token)

    content = f'''
        <h2 style="margin:0 0 16px 0;color:#f8fafc;font-size:24px;font-weight:700;">
            What other buyers found with their free analysis
        </h2>
        <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            Your free credit is still ready to use. Here&rsquo;s what buyers like you discovered
            when they finally ran their analysis:
        </p>

        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 24px 0;">
            <tr>
                <td style="padding:16px;background:rgba(16,185,129,0.08);border-left:3px solid #10b981;border-radius:0 8px 8px 0;margin-bottom:12px;">
                    <p style="margin:0 0 6px 0;color:#f8fafc;font-size:15px;font-weight:600;">&ldquo;Seller said no known issues. OfferWise flagged 3 disclosure gaps.&rdquo;</p>
                    <p style="margin:0;color:#94a3b8;font-size:13px;">Beta tester &mdash; negotiated $35K off the price</p>
                </td>
            </tr>
        </table>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 24px 0;">
            <tr>
                <td style="padding:16px;background:rgba(16,185,129,0.08);border-left:3px solid #10b981;border-radius:0 8px 8px 0;">
                    <p style="margin:0 0 6px 0;color:#f8fafc;font-size:15px;font-weight:600;">&ldquo;The $19 analysis saved me from a $47K crawl space problem.&rdquo;</p>
                    <p style="margin:0;color:#94a3b8;font-size:13px;">Beta tester &mdash; walked away before closing</p>
                </td>
            </tr>
        </table>

        <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
            If you&rsquo;re actively looking at a property, now&rsquo;s the time. Upload the seller&rsquo;s disclosure
            and inspection report &mdash; you&rsquo;ll have your recommended offer price in under 60 seconds.
        </p>

        {_button("Use Your Free Credit &rarr;", f"{BASE_URL}/app?utm_source=drip4&utm_medium=email", "#10b981")}

        <p style="margin:24px 0 0 0;color:#94a3b8;font-size:14px;text-align:center;">
            Not actively house hunting right now? No worries &mdash; your free credit will be here when you need it.
            You can always <a href="{unsub_url}" style="color:#60a5fa;">unsubscribe</a>.
        </p>
    '''

    content += '\n        <p style="margin:20px 0 0;color:#94a3b8;font-size:13px;text-align:center;line-height:1.6;">\n          Know someone buying a home?\n          <a href="{BASE_URL}/settings?tab=referrals&amp;utm_source=drip5&amp;utm_medium=email" style="color:#f97316;font-weight:700;">Refer them &mdash; you both get a free analysis.</a>\n        </p>'
    subject = "What buyers found with their free OfferWise analysis"
    return subject, _wrap(content, "Real stories from buyers who used their free credit.", unsub_url)


def drip_email_nearby(entry):
    """Monthly: nearby listings with market intelligence."""
    unsub_url = get_unsubscribe_url(entry.unsubscribe_token)
    zip_code = getattr(entry, 'result_zip', None) or ''
    address = entry.result_address or ''

    # Try to extract ZIP from address if not stored
    if not zip_code and address:
        import re
        m = re.search(r'(\d{5})', address)
        if m:
            zip_code = m.group(1)

    if not zip_code:
        # Fallback: generic nurture email, not listings
        content = """
            <h2 style="margin:0 0 16px 0;color:#f8fafc;font-size:24px;font-weight:700;">
                Still house hunting?
            </h2>
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                When you find a property you are serious about, upload the seller disclosure
                and inspection report to get a data-backed offer price in under 60 seconds.
            </p>
            %s
        """ % _button("Analyze a Property &rarr;", "%s/app?utm_source=drip5&utm_medium=email" % BASE_URL)
        subject = "Ready to make a confident offer?"
        return subject, _wrap(content, "OfferWise is here when you need it.", unsub_url)

    # Fetch nearby listings
    try:
        from nearby_listings import get_nearby_listings, render_listings_email_html
        result = get_nearby_listings(zip_code=zip_code, limit=3)
        listings = result.get('listings', [])
        market = result.get('market', {})
    except Exception as e:
        import logging
        logging.warning('Drip nearby-listings fetch failed: %s', e)
        listings = []
        market = {}

    if not listings:
        content = """
            <h2 style="margin:0 0 16px 0;color:#f8fafc;font-size:24px;font-weight:700;">
                Market update for %s
            </h2>
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                We checked for new listings near your area but did not find active matches right now.
                We will keep looking and notify you when something interesting pops up.
            </p>
            <p style="margin:0 0 20px 0;color:#cbd5e1;font-size:16px;line-height:1.6;">
                In the meantime, if you are already looking at a property, upload the seller disclosure
                and inspection report — you will have your recommended offer price in under 60 seconds.
            </p>
            %s
        """ % (zip_code, _button("Analyze a Property &rarr;", "%s/app" % BASE_URL))
        subject = "Market update for %s" % zip_code
        return subject, _wrap(content, "No new matches — we will keep looking.", unsub_url)

    # Render listings
    listings_html = render_listings_email_html(listings, zip_code, market, BASE_URL)

    content = """
        %s
        <div style="margin-top:24px;text-align:center;">
            <p style="margin:0 0 16px 0;color:#94a3b8;font-size:14px;">
                Interested in any of these? Upload the disclosure and inspection report
                for a full data-backed analysis.
            </p>
            %s
        </div>
    """ % (listings_html, _button("Analyze a Property &rarr;", "%s/app" % BASE_URL, "#10b981"))

    subject = "%d new listings in %s — here is what we would offer" % (len(listings), zip_code)
    return subject, _wrap(content, "New listings near you with offer intelligence.", unsub_url)


# Map step number to template function
DRIP_TEMPLATES = {
    1: drip_email_1,
    2: drip_email_2,
    3: drip_email_3,
    4: drip_email_4,
    5: drip_email_5,
}


def send_drip_email(entry, step):
    """
    Send a specific drip email to a waitlist entry.
    Steps 1-5: Fixed nurture templates.
    Steps 6+: Monthly nearby-listings emails (dynamic, personalized to ZIP).
    Returns True if sent, False if skipped/failed.
    """
    from email_service import send_email, EMAIL_ENABLED

    if not EMAIL_ENABLED:
        logger.info(f"Drip email {step} skipped (email disabled): {entry.email}")
        return False

    if entry.email_unsubscribed:
        logger.info(f"Drip email {step} skipped (unsubscribed): {entry.email}")
        return False

    # Ensure unsubscribe token exists
    if not entry.unsubscribe_token:
        entry.unsubscribe_token = generate_unsubscribe_token()

    # Determine which template to use
    if step in DRIP_TEMPLATES:
        template_fn = DRIP_TEMPLATES[step]
        subject, html = template_fn(entry)
    elif step <= MAX_DRIP_STEP:
        # Monthly nearby-listings email
        subject, html = drip_email_nearby(entry)
        if subject is None:
            # No listings available or no ZIP — skip this month silently
            logger.info(f"Nearby drip {step} skipped for {entry.email} (no data)")
            return False
    else:
        logger.warning(f"Drip step {step} exceeds max ({MAX_DRIP_STEP}) for {entry.email}")
        return False

    # Get List-Unsubscribe headers for one-click unsubscribe in Gmail/Yahoo
    headers = get_list_unsubscribe_headers(entry.unsubscribe_token)

    success = send_email(
        to_email=entry.email,
        subject=subject,
        html_content=html,
        headers=headers,
        email_type=f'drip_{step}' if step <= 5 else 'drip_monthly',
    )

    if success:
        entry.drip_step = step
        entry.drip_last_sent_at = datetime.now(timezone.utc)
        if step >= MAX_DRIP_STEP:
            entry.drip_completed = True
        logger.info(f"Drip email {step} sent to {entry.email}" +
                     (" [Monthly listings]" if step > 5 else f"/{5}"))
    else:
        logger.error(f"Drip email {step} FAILED for {entry.email}")

    return success


# =============================================================================
# SCHEDULER (called by cron endpoint)
# =============================================================================

def run_drip_scheduler(db_session, batch_size=50):
    """
    Process pending drip emails. Call this from a cron endpoint every 15-30 minutes.

    Logic per entry:
      - If unsubscribed or completed: skip
      - Steps 1-5: fixed nurture sequence on accelerating schedule
      - Steps 6+: monthly listing emails personalized to their ZIP

    Returns: dict with counts (sent, skipped, errors)
    """
    from models import Waitlist

    now = datetime.now(timezone.utc)
    stats = {'sent': 0, 'skipped': 0, 'errors': 0, 'checked': 0}

    # Get candidates: not completed, not unsubscribed, ordered by created_at
    candidates = Waitlist.query.filter(
        Waitlist.drip_completed == False,
        Waitlist.email_unsubscribed == False,
    ).order_by(Waitlist.created_at.asc()).limit(batch_size).all()

    stats['checked'] = len(candidates)

    for entry in candidates:
        try:
            # Make created_at timezone-aware if it isn't
            created = entry.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            hours_since_signup = (now - created).total_seconds() / 3600
            current_step = entry.drip_step or 0
            next_step = current_step + 1

            if next_step > MAX_DRIP_STEP:
                entry.drip_completed = True
                stats['skipped'] += 1
                continue

            # Check if enough time has passed for next step
            min_hours = _drip_min_hours(next_step)
            if hours_since_signup < min_hours:
                stats['skipped'] += 1
                continue

            # For step 1, also require at least 5 minutes (avoid sending during their session)
            if next_step == 1 and hours_since_signup < 0.083:
                stats['skipped'] += 1
                continue

            # Don't double-send: check last sent time
            if entry.drip_last_sent_at:
                last_sent = entry.drip_last_sent_at
                if last_sent.tzinfo is None:
                    last_sent = last_sent.replace(tzinfo=timezone.utc)
                hours_since_last = (now - last_sent).total_seconds() / 3600
                # Min 7 days between monthly listing emails, min 12h between nurture emails
                min_gap = 168 if next_step > 5 else 12
                if hours_since_last < min_gap:
                    stats['skipped'] += 1
                    continue

            # Send it (send_drip_email handles both nurture and monthly listing steps)
            success = send_drip_email(entry, next_step)
            if success:
                stats['sent'] += 1
            else:
                stats['skipped'] += 1

        except Exception as e:
            logger.error(f"Drip scheduler error for {entry.email}: {e}")
            stats['errors'] += 1

    # Commit all updates
    try:
        db_session.commit()
    except Exception as e:
        logger.error(f"Drip scheduler commit failed: {e}")
        db_session.rollback()

    logger.info(f"Drip scheduler: checked={stats['checked']} sent={stats['sent']} "
                f"skipped={stats['skipped']} errors={stats['errors']}")
    return stats


# =============================================================================
# MARKET INTELLIGENCE EMAIL (v5.62.92)
# =============================================================================

def send_market_intelligence_email(db_session, user, snapshot):
    """Send a market intelligence briefing email based on a MarketSnapshot.
    
    Only sends if the snapshot has alerts worth sending (high-match listing,
    significant market shift, or new comparable sales).
    
    Returns True if email was sent, False if skipped.
    """
    import json

    if not snapshot or snapshot.alerts_generated == 0:
        return False

    if snapshot.alert_email_sent:
        return False

    # Check unsubscribe
    if hasattr(user, 'email_unsubscribed') and user.email_unsubscribed:
        return False

    # Build email sections
    sections_html = ''

    # Section 1: Matched listings
    matched = []
    try:
        matched = json.loads(snapshot.matched_listings_json) if snapshot.matched_listings_json else []
    except (json.JSONDecodeError, TypeError):
        pass

    high_matches = [m for m in matched if (m.get('score') or 0) >= 75]
    if high_matches:
        listings_html = ''
        for m in high_matches[:3]:
            addr = m.get('address', '').split(',')[0]
            price = f"${m.get('price', 0):,}" if m.get('price') else ''
            risk = m.get('risk_tier', '')
            score = m.get('score', 0)
            offer_low = f"${m.get('offer_range_low', 0):,}" if m.get('offer_range_low') else ''
            offer_high = f"${m.get('offer_range_high', 0):,}" if m.get('offer_range_high') else ''
            listings_html += f'''
                <div style="padding:12px 14px;background:white;border-radius:8px;border:1px solid #e2e8f0;margin-bottom:8px;">
                    <div style="font-weight:700;font-size:13px;color:#0f172a;">{addr}</div>
                    <div style="font-size:12px;color:#64748b;margin-top:2px;">{price} · Risk: {risk} · Offer range: {offer_low} – {offer_high}</div>
                    <div style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;background:#dcfce7;color:#166534;margin-top:4px;">Match Score: {score}</div>
                </div>'''

        sections_html += f'''
            <div style="margin-bottom:24px;padding:18px;border-radius:10px;border:1px solid #bbf7d0;background:#f0fdf4;">
                <div style="font-size:13px;font-weight:700;color:#166534;margin-bottom:12px;">🎯 New Listings That Match Your Preferences</div>
                {listings_html}
            </div>'''

    # Section 2: Market stats
    if snapshot.median_price:
        median_str = f"${snapshot.median_price:,}"
        delta_str = ''
        if snapshot.median_price_delta_pct:
            arrow = '↓' if snapshot.median_price_delta_pct < 0 else '↑'
            color = '#166534' if snapshot.median_price_delta_pct < 0 else '#dc2626'
            delta_str = f'<span style="font-weight:700;color:{color};">{median_str} {arrow} {abs(snapshot.median_price_delta_pct):.1f}%</span>'
        else:
            delta_str = f'<span style="font-weight:700;color:#0f172a;">{median_str}</span>'

        dom_str = ''
        if snapshot.avg_dom:
            dom_str = f'''<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#475569;">
                <span>Avg. days on market</span>
                <span style="font-weight:700;color:#0f172a;">{snapshot.avg_dom} days</span>
            </div>'''

        inv_str = ''
        if snapshot.active_inventory:
            inv_str = f'''<div style="display:flex;justify-content:space-between;padding:8px 0;font-size:13px;color:#475569;">
                <span>Active inventory</span>
                <span style="font-weight:700;color:#0f172a;">{snapshot.active_inventory} homes</span>
            </div>'''

        sections_html += f'''
            <div style="margin-bottom:24px;padding:18px;border-radius:10px;border:1px solid #bfdbfe;background:#eff6ff;">
                <div style="font-size:13px;font-weight:700;color:#1e40af;margin-bottom:12px;">📊 {snapshot.zip_code} This Week</div>
                <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#475569;">
                    <span>Median price</span>
                    {delta_str}
                </div>
                <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#475569;">
                    <span>New listings</span>
                    <span style="font-weight:700;color:#0f172a;">{snapshot.new_listings_count or 0}</span>
                </div>
                {dom_str}
                {inv_str}
            </div>'''

    # Section 3: Comp updates
    comps = []
    try:
        comps = json.loads(snapshot.new_comps_json) if snapshot.new_comps_json else []
    except (json.JSONDecodeError, TypeError):
        pass

    if comps:
        # Group by property
        by_prop = {}
        for c in comps:
            addr = c.get('property_address', '')
            if addr not in by_prop:
                by_prop[addr] = {'below': 0, 'above': 0, 'total': 0}
            by_prop[addr]['total'] += 1
            if c.get('vs_recommended') == 'below':
                by_prop[addr]['below'] += 1
            else:
                by_prop[addr]['above'] += 1

        comp_html = ''
        for addr, data in list(by_prop.items())[:3]:
            short_addr = addr.split(',')[0] if addr else 'Your property'
            if data['below'] > data['above']:
                comp_html += f'<div style="font-size:13px;color:#475569;line-height:1.6;">Since your analysis on <strong>{short_addr}</strong>, <strong style="color:#166534;">{data["below"]} comparable sale(s) closed below your recommended offer</strong>. Your negotiating position has strengthened.</div>'
            else:
                comp_html += f'<div style="font-size:13px;color:#475569;line-height:1.6;">Since your analysis on <strong>{short_addr}</strong>, <strong style="color:#dc2626;">{data["above"]} comparable sale(s) closed above your recommended offer</strong>. Consider revising your offer upward to stay competitive.</div>'

        sections_html += f'''
            <div style="margin-bottom:24px;padding:18px;border-radius:10px;border:1px solid #fde68a;background:#fefce8;">
                <div style="font-size:13px;font-weight:700;color:#92400e;margin-bottom:12px;">🏠 Updates on Your Properties</div>
                {comp_html}
            </div>'''

    if not sections_html:
        return False

    # Build full email
    user_name = getattr(user, 'name', '') or user.email.split('@')[0]
    
    html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;margin-top:20px;margin-bottom:20px;">
    <div style="background:linear-gradient(135deg,#0f172a,#1e293b);padding:32px 36px 28px;text-align:center;">
        <div style="color:#60a5fa;font-weight:700;font-size:20px;"><span style="color:white;">Offer</span>Wise</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:6px;">Your Weekly Market Intelligence · {snapshot.zip_code}</div>
    </div>
    <div style="padding:32px 36px;color:#1e293b;">
        <div style="font-size:16px;font-weight:700;color:#0f172a;margin-bottom:4px;">Hi {user_name},</div>
        <div style="font-size:13.5px;color:#475569;margin-bottom:24px;line-height:1.6;">Your market moved this week. Here is what our engine found for you.</div>
        {sections_html}
        <a href="https://www.getofferwise.ai/dashboard?utm_source=market_intel&utm_medium=email" style="display:block;text-align:center;padding:14px 24px;background:linear-gradient(135deg,#3b82f6,#2563eb);color:white;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;margin:24px auto 0;max-width:280px;">View Your Dashboard →</a>
        <p style="margin:16px 0 0;color:#94a3b8;font-size:12px;text-align:center;">
          Know a buyer? <a href="https://www.getofferwise.ai/settings?tab=referrals" style="color:#3b82f6;">Share your referral link</a> — you both get a free analysis.
        </p>
    </div>
    <div style="text-align:center;padding:20px 36px 28px;font-size:11px;color:#94a3b8;border-top:1px solid #f1f5f9;">
        You are receiving this because you have active preferences on OfferWise.<br>
        <a href="https://www.getofferwise.ai/unsubscribe?token={getattr(user, 'referral_code', '')}" style="color:#94a3b8;text-decoration:underline;">Unsubscribe</a>
    </div>
</div>
</body></html>'''

    # Send via existing email infrastructure
    try:
        _send_intel_email(user.email, f"📊 Your {snapshot.zip_code} Market Update", html)
        snapshot.alert_email_sent = True
        snapshot.alert_email_sent_at = datetime.utcnow()
        db_session.flush()
        logger.info(f"Market intel email sent to {user.email} (ZIP {snapshot.zip_code}, alerts={snapshot.alerts_generated})")
        return True
    except Exception as e:
        logger.error(f"Market intel email failed for {user.email}: {e}")
        return False


def _send_intel_email(to_email, subject, html_body):
    """Send market intelligence email using Resend (same as all other OfferWise emails)."""
    try:
        from email_service import send_email, EMAIL_ENABLED
        if not EMAIL_ENABLED:
            logger.warning("Resend not configured — skipping market intel email to %s", to_email)
            return
        send_email(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            email_type='market_intel',
        )
    except Exception as e:
        logger.error("Market intel email failed for %s: %s", to_email, e)


# =============================================================================
# USER ACCOUNT DRIP — for signed-up accounts (not waitlist entries)
# =============================================================================

class _UserDripEntry:
    """Shim that makes a User object look like a Waitlist entry for drip templates."""
    def __init__(self, user):
        import secrets as _sec
        self.email              = user.email
        self.source             = getattr(user, 'auth_provider', '') or ''
        self.result_address     = None
        self.result_grade       = None
        self.result_exposure    = None
        self.result_score       = None
        self.result_zip         = None
        self.result_address     = None
        self.drip_step          = 0
        self.drip_last_sent_at  = None
        self.drip_completed     = False
        self.email_unsubscribed = False
        # Generate a stable unsubscribe token from the user email
        self.unsubscribe_token  = _sec.token_urlsafe(32)

    # Make it look writable (no-op — we track state separately)
    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


def send_user_drip_step(user, step: int) -> bool:
    """
    Send a single drip step email to a signed-up User account.
    Uses the same templates as the waitlist drip.
    reply_to is always support@getofferwise.ai.

    Returns True if sent successfully.
    """
    from email_service import send_email, EMAIL_ENABLED
    import os as _os

    if not EMAIL_ENABLED:
        logger.info(f"User drip step {step} skipped (email disabled): {user.email}")
        return False

    entry = _UserDripEntry(user)
    reply_to = _os.environ.get('SUPPORT_EMAIL', 'support@getofferwise.ai')

    if step in DRIP_TEMPLATES:
        template_fn = DRIP_TEMPLATES[step]
        subject, html = template_fn(entry)
    else:
        logger.warning(f"Invalid drip step {step} for user {user.email}")
        return False

    headers = get_list_unsubscribe_headers(entry.unsubscribe_token)

    success = send_email(
        to_email=user.email,
        subject=subject,
        html_content=html,
        reply_to=reply_to,
        headers=headers,
        email_type=f'user_drip_{step}',
    )
    logger.info(f"User drip step {step} {'sent' if success else 'FAILED'}: {user.email}")
    return success
