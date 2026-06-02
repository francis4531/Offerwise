"""
Social Content Engine — Facebook Groups + Nextdoor
===================================================
Generates platform-adapted posts for manual posting.

Facebook Groups: narrative, data-backed, conversational tone.
  Target: Bay Area / CA first-time buyer groups (5K–50K members).

Nextdoor: hyperlocal, neighbourhood-aware, personal tone.
  Target: Bay Area neighbourhood feeds where housing is discussed.

No scanning (auth-required platforms). Output is draft posts
you copy-paste into the platform. Same admin review flow as Reddit.

Content adapts the same 7 pillars from content_engine.py but
rewrites for each platform's norms:
  - Facebook: longer, storytelling, data shown as insight
  - Nextdoor:  shorter, local context, neighbour-to-neighbour voice
"""

import json
import logging
import os
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

# ── Facebook Group targets ────────────────────────────────────────────────────
FACEBOOK_GROUPS = [
    {'name': 'Bay Area First Time Home Buyers',     'url': 'https://www.facebook.com/groups/373190116218953',  'size': 'large',  'focus': 'first_time'},
    {'name': 'California First Time Home Buyers',   'url': 'https://www.facebook.com/groups/californiafirsttimehomebuyers', 'size': 'large', 'focus': 'first_time'},
    {'name': 'Bay Area Real Estate',                'url': 'https://www.facebook.com/groups/bayarearealestategroup', 'size': 'large', 'focus': 'general'},
    {'name': 'San Jose Real Estate',                'url': 'https://www.facebook.com/groups/sanjoserealestate', 'size': 'medium', 'focus': 'local'},
    {'name': 'Silicon Valley Real Estate Investors','url': 'https://www.facebook.com/groups/svrei',            'size': 'medium', 'focus': 'investor'},
]

# ── Nextdoor targets ──────────────────────────────────────────────────────────
NEXTDOOR_NEIGHBORHOODS = [
    {'name': 'San Jose - General',         'url': None,                     'city': 'San Jose'},
    {'name': 'Bay Area Housing',           'url': None,                     'city': 'Bay Area'},
    {'name': 'Silicon Valley',             'url': None,                     'city': 'Silicon Valley'},
]

# ── Platform-specific CTAs ────────────────────────────────────────────────────
FB_CTA    = "\n\n🏠 **Try OfferWise free** — upload your inspection report and seller disclosure at getofferwise.ai and get your OfferScore, repair breakdown, and recommended offer price in 60 seconds. First analysis is free, no credit card needed."
ND_CTA    = "\n\nIf you're in the middle of a home purchase and want a second opinion on your inspection report, check out OfferWise (getofferwise.ai) — it's free for your first property."


# ── Facebook post generators ──────────────────────────────────────────────────

def generate_facebook_post(pillar_key: str, stats: dict,
                            target_date: date, group: dict,
                            db_session=None) -> dict:
    """Generate a Facebook Group post for the given pillar."""
    focus = group.get('focus', 'general')

    generators = {
        'what_were_seeing':    _fb_what_were_seeing,
        'first_timer_tuesday': _fb_first_timer,
        'did_you_know':        _fb_did_you_know,
        'real_numbers':        _fb_real_numbers,
        'red_flag_friday':     _fb_red_flag,
        'community_qa':        _fb_community_qa,
        'weekly_digest':       _fb_weekly_digest,
    }
    gen = generators.get(pillar_key, _fb_community_qa)
    title, body, topic_key = gen(stats, target_date, focus, db_session)

    body += FB_CTA

    return {
        'platform':     'facebook',
        'target_group': group['name'],
        'group_url':    group['url'],
        'title':        title,
        'body':         body,
        'pillar':       pillar_key,
        'topic_key':    topic_key,
        'scheduled_date': target_date,
    }


def _fb_what_were_seeing(stats, dt, focus, db_session):
    avg_repair = stats.get('avg_repair_cost', 18500)
    avg_score  = stats.get('avg_offer_score', 62)
    deal_pct   = stats.get('deal_breakers_pct', 16)
    cats       = stats.get('top_categories', [{'name': 'Plumbing', 'critical': 5, 'total': 38}])
    top        = cats[0] if cats else {'name': 'plumbing', 'critical': 5, 'total': 38}

    title = f"📊 What we're seeing in Bay Area inspection reports right now"
    body = f"""We've been tracking inspection and disclosure data across Bay Area properties and wanted to share what we're seeing.

The top issue showing up right now is **{top['name']}** — flagged in a significant number of reports, with {top.get('critical', 5)} rated as critical.

A few other numbers worth knowing:
• Average estimated repair costs: **${avg_repair:,}** per property
• Average OfferScore (0–100 condition rating): **{avg_score}/100**
• Properties with at least one deal-breaker finding: **{deal_pct}%**

The thing that consistently surprises buyers isn't the number of findings — it's how often the seller's disclosure doesn't mention what the inspector found. That gap is where negotiation leverage lives.

Has anyone had a big surprise on their inspection report recently? What category was it?"""
    return title, body, 'fb:what_were_seeing'


def _fb_first_timer(stats, dt, focus, db_session):
    avg_findings = stats.get('avg_findings_per_property', 8)
    avg_repair   = stats.get('avg_repair_cost', 18500)
    transparency = stats.get('avg_transparency_score', 64)

    title = "🏠 First-time buyer? Here's what I wish someone had told me about inspection reports"
    body = f"""A lot of first-time buyers get their inspection report and have no idea what to do with it. 47 pages, hundreds of findings, and your agent tells you "don't worry, every house has issues."

Here's what actually matters:

**1. Not all findings are equal.** The average inspection report has around {avg_findings} findings. Most are cosmetic or minor maintenance. What you're looking for is the 2–3 that are safety hazards or structural problems — those are your negotiation leverage.

**2. Compare the report to the seller's disclosure.** This is the step almost no one takes. Sellers are required to disclose known defects. When the inspector finds something the seller said "no known issues" about, that discrepancy gives you real power in negotiations. On average, sellers only disclose about {transparency}% of what inspectors find.

**3. Get dollar amounts.** "Deteriorated roof flashing" means nothing. "$18,000–$28,000 roof repair" means you can ask for a price reduction or seller credit for exactly that amount. Always get contractor estimates for the big items before you negotiate.

Average repair costs we're seeing right now: **${avg_repair:,}** per property. Some of that is a $200 fix, some of it is a $40,000 foundation problem. Knowing which is which is everything.

What questions do you have about navigating your inspection? Happy to help — been through this a few times."""
    return title, body, 'fb:first_timer'


def _fb_did_you_know(stats, dt, focus, db_session):
    transparency = stats.get('avg_transparency_score', 64)
    gap = 100 - transparency

    title = f"🔍 Did you know: sellers only disclose about {transparency}% of what inspectors find?"
    body = f"""This one surprises a lot of people.

We cross-reference seller disclosures against inspection reports across hundreds of Bay Area properties. On average, sellers disclose about **{transparency} out of 100** — meaning roughly **{gap}% of inspection findings** were never mentioned in the disclosure.

That doesn't always mean fraud. Sometimes sellers genuinely don't know — they've lived with a slow roof leak for so long they stopped thinking about it. But sometimes they know exactly what they're not disclosing.

**Why this matters for buyers:**
When an inspector finds something the seller claimed "no known issues" about, that contradiction is powerful. It's not just a repair cost negotiation — it's evidence the seller was aware (or should have been aware) of a defect. That changes the conversation significantly.

The disclosure sections sellers skip most often in California:
• Roof condition and age
• HVAC system service history  
• Water intrusion history
• Permit status for additions

What's your experience been — did your seller's disclosure match what the inspector found?"""
    return title, body, 'fb:did_you_know'


def _fb_real_numbers(stats, dt, focus, db_session):
    avg_repair = stats.get('avg_repair_cost', 18500)
    systems = [
        ('Roof replacement',        '$15,000–$35,000'),
        ('HVAC full replacement',   '$8,000–$18,000'),
        ('Foundation repair',       '$5,000–$25,000+'),
        ('Full re-pipe (plumbing)', '$8,000–$18,000'),
        ('Panel + rewire (elect.)', '$6,000–$15,000'),
        ('Sewer line replacement',  '$3,000–$25,000'),
    ]
    import random
    system, range_str = random.choice(systems)

    title = f"💰 Real numbers: what {system.split()[0].lower()} actually costs in the Bay Area (2026)"
    body = f"""People always ask "is this repair estimate reasonable?" so we pulled real data.

**{system}: {range_str}**

That's the realistic range for a typical single-family home in the Bay Area. The variance is wide because:
— Home size matters (1,200 sqft vs 3,500 sqft)
— Access difficulty (crawl space vs open basement)
— Age of surrounding systems (sometimes one repair requires another)
— Labor costs vary significantly by ZIP code

The number on the inspection report is usually a rough estimate. Before you negotiate, get 2–3 actual contractor quotes. Then present the **mid-range** to the seller — not the worst case, not the best case.

**The negotiation approach that works:**
"The inspection identified [specific finding]. We obtained three contractor estimates ranging from X to Y. We'd like to request a seller credit of [mid-range amount] to address this before closing."

Average total repair costs we're tracking across Bay Area properties right now: **${avg_repair:,}**. Most of that comes from 2–3 big items, not dozens of small ones.

What repair cost has surprised you the most in your home search?"""
    return title, body, 'fb:real_numbers'


def _fb_red_flag(stats, dt, focus, db_session):
    flags = [
        ('Hidden water damage',   'water stains, musty smells, fresh paint over staining'),
        ('Foundation cracks',     'horizontal cracks, stair-step cracks in brick, floor sloping'),
        ('Electrical panels',     'Federal Pacific, Zinsco, or fuse boxes — insurers often refuse to cover'),
        ('Roof age',              'sellers often disclose age as "unknown" when they know exactly how old it is'),
        ('Sewer lateral',         'the underground pipe nobody scopes until it fails — $3K–$25K to replace'),
    ]
    import random
    flag, signs = random.choice(flags)
    deal_pct = stats.get('deal_breakers_pct', 16)

    title = f"🚩 Red flag Friday: {flag}"
    body = f"""Every Friday we highlight one inspection red flag that buyers consistently underestimate.

This week: **{flag}**

What to look for: {signs}.

About **{deal_pct}% of properties** we analyze have at least one finding severe enough to seriously reconsider the purchase. {flag} shows up frequently in that group.

**What to ask your inspector:**
"Can you show me exactly where this is, photograph it, and give me a rough estimate of severity? Is this something I should get a specialist to evaluate before removing my contingency?"

**What to do with the disclosure:**
Check what the seller said about {flag.lower()}. If they said "no known issues" and the inspector found evidence of it, that contradiction is your leverage — not just for a price reduction, but potentially for a stronger legal position if problems emerge after close.

Not every instance of this is a deal-breaker. The question is scope, cost, and whether the seller was honest about it.

Has anyone encountered {flag.lower()} in their home search? What happened?"""
    return title, body, 'fb:red_flag'


def _fb_community_qa(stats, dt, focus, db_session):
    questions = [
        "What was the most surprising thing your home inspector found — and how did it affect your negotiation?",
        "First-time buyers: what do you wish you'd known about seller disclosures before making an offer?",
        "Has anyone successfully negotiated a price reduction based on inspection findings? How much did you get back?",
        "What's the one question you'd ask a home inspector that most buyers forget to ask?",
        "Did your seller's disclosure match what the inspection found? Or were there surprises?",
    ]
    import random
    question = random.choice(questions)

    title = "💬 Question for the group — sharing experiences"
    body = f"""{question}

Sharing real experiences helps everyone in this group make better decisions. Whether you're a first-time buyer or on your fifth property — your story might be exactly what someone else needs to hear right now.

Drop your answer below 👇"""
    return title, body, 'fb:community_qa'


def _fb_weekly_digest(stats, dt, focus, db_session):
    avg_score  = stats.get('avg_offer_score', 62)
    avg_repair = stats.get('avg_repair_cost', 18500)
    deal_pct   = stats.get('deal_breakers_pct', 16)
    transparency = stats.get('avg_transparency_score', 64)
    week_str = dt.strftime('%B %d, %Y')

    title = f"📋 Weekly digest — Bay Area home inspection data, week of {week_str}"
    body = f"""Here's a quick summary of what we're seeing in Bay Area property analyses this week.

**By the numbers:**
• Average OfferScore: **{avg_score}/100** (higher = better condition)
• Average estimated repair costs: **${avg_repair:,}**
• Properties with deal-breaker findings: **{deal_pct}%**
• Seller transparency (disclosure vs inspection match): **{transparency}/100**

**The pattern we keep seeing:**
The gap between what sellers disclose and what inspectors find is consistent. Buyers who catch this gap before they sign anything are the ones who negotiate from strength.

**If you're in escrow right now:**
→ Read your disclosure before the inspection (not after)
→ Compare the two documents line by line
→ Every mismatch is a data point in your favour

Questions about your specific situation? Drop them below — happy to help."""
    return title, body, 'fb:weekly_digest'


# ── Nextdoor post generators ──────────────────────────────────────────────────

def generate_nextdoor_post(pillar_key: str, stats: dict,
                           target_date: date, neighborhood: dict,
                           db_session=None) -> dict:
    """Generate a Nextdoor post — shorter, hyperlocal, neighbour voice."""
    city = neighborhood.get('city', 'Bay Area')
    generators = {
        'what_were_seeing':    _nd_what_were_seeing,
        'first_timer_tuesday': _nd_first_timer,
        'did_you_know':        _nd_did_you_know,
        'real_numbers':        _nd_real_numbers,
        'red_flag_friday':     _nd_red_flag,
        'community_qa':        _nd_community_qa,
        'weekly_digest':       _nd_weekly_digest,
    }
    gen = generators.get(pillar_key, _nd_community_qa)
    title, body, topic_key = gen(stats, target_date, city, db_session)

    body += ND_CTA

    return {
        'platform':     'nextdoor',
        'target_group': neighborhood['name'],
        'group_url':    neighborhood.get('url'),
        'title':        title,
        'body':         body,
        'pillar':       pillar_key,
        'topic_key':    topic_key,
        'scheduled_date': target_date,
    }


def _nd_what_were_seeing(stats, dt, city, db_session):
    avg_repair = stats.get('avg_repair_cost', 18500)
    deal_pct   = stats.get('deal_breakers_pct', 16)
    title = f"Home inspection data in {city} right now"
    body = f"""For neighbors who are buying or thinking about buying —

We track inspection data across {city} properties. A few things worth knowing:

Average repair costs found in inspections: ~${avg_repair:,}
Properties with at least one serious finding: {deal_pct}%

The biggest thing: always compare the seller's disclosure against the inspection report yourself. The gap between what sellers claim and what inspectors find is where your negotiation leverage lives.

Anyone currently in escrow with questions? Happy to share what we've learned."""
    return title, body, 'nd:what_were_seeing'


def _nd_first_timer(stats, dt, city, db_session):
    transparency = stats.get('avg_transparency_score', 64)
    title = f"Tip for first-time buyers in {city}"
    body = f"""Buying your first home here and feeling overwhelmed by the inspection report?

One thing almost no one does: compare the inspection report against the seller's disclosure BEFORE your contingency deadline. Sellers in {city} are required to disclose known defects. When the inspector finds something the seller said "no issues" about, that's leverage.

On average, sellers only disclose about {transparency}% of what inspectors find. The other {100-transparency}% is your negotiation opening.

Happy to answer questions about navigating the inspection process — just went through it."""
    return title, body, 'nd:first_timer'


def _nd_did_you_know(stats, dt, city, db_session):
    transparency = stats.get('avg_transparency_score', 64)
    title = f"Did you know: the disclosure gap in {city} homes"
    body = f"""Quick one for neighbors considering buying:

Sellers in California are legally required to disclose known defects. But on average, inspection reports find issues that weren't disclosed in about {100-transparency}% of cases.

This isn't always bad faith — sometimes sellers genuinely don't know. But knowing this going in helps you read the disclosure more carefully and ask better questions before you're in contract.

The sections sellers skip most often: roof condition, HVAC service history, water intrusion, and permit status for additions.

Anyone want to share their experience with {city} disclosure practices?"""
    return title, body, 'nd:did_you_know'


def _nd_real_numbers(stats, dt, city, db_session):
    avg_repair = stats.get('avg_repair_cost', 18500)
    title = f"What repairs actually cost in {city} (real data)"
    body = f"""For neighbors weighing a home purchase —

People always ask if repair estimates are reasonable. Average total repair costs we're tracking in {city} properties: **${avg_repair:,}**.

That's usually dominated by 2-3 big items: roof, HVAC, foundation, or plumbing. The rest are minor.

The key: get contractor estimates for the big items before your inspection contingency expires. Then ask for a seller credit — not a seller repair. You want to choose your own contractor.

Anyone have recent repair cost data from local contractors to share?"""
    return title, body, 'nd:real_numbers'


def _nd_red_flag(stats, dt, city, db_session):
    title = f"Home buying red flag to watch for in {city}"
    body = f"""For neighbors currently house hunting:

One pattern we see consistently in {city} inspection reports: sellers disclosing "no known roof issues" while the inspector finds active water intrusion staining in the attic.

This isn't rare. It shows up in a meaningful percentage of inspections.

If you're in escrow: ask your inspector specifically about the attic, crawl space, and any visible staining on ceilings or walls. Get photos. Then check what the seller said in their disclosure about water intrusion history.

Any neighbors recently gone through this? What was your experience?"""
    return title, body, 'nd:red_flag'


def _nd_community_qa(stats, dt, city, db_session):
    title = f"Question for {city} neighbors — home buying experiences"
    body = f"""Curious about neighbors' experiences here:

For those who've bought a home in {city} in the last few years — what surprised you most about the inspection or disclosure process?

Asking because a lot of first-time buyers come to me with questions, and real local experience is more valuable than anything generic I could share.

Any tips, warnings, or things you'd do differently?"""
    return title, body, 'nd:community_qa'


def _nd_weekly_digest(stats, dt, city, db_session):
    avg_repair = stats.get('avg_repair_cost', 18500)
    deal_pct   = stats.get('deal_breakers_pct', 16)
    title = f"Weekly {city} real estate note"
    body = f"""Quick update for neighbors following the {city} market:

This week's inspection data snapshot:
• Average repair costs found: ${avg_repair:,}
• Properties with serious findings: {deal_pct}%

If you're actively searching: the market is moving fast but that doesn't mean skipping your contingency. The inspection is the one moment you have real leverage — use it.

Questions about the process? Drop them here."""
    return title, body, 'nd:weekly_digest'


# ── Batch generator ───────────────────────────────────────────────────────────

def generate_social_posts_for_date(db_session, models_map: dict,
                                   target_date: date = None,
                                   platforms: list = None) -> list:
    """
    Generate Facebook + Nextdoor posts for a given date.
    Returns list of post dicts ready to save as GTMSubredditPost rows.
    """
    if target_date is None:
        target_date = date.today()
    if platforms is None:
        platforms = ['facebook', 'nextdoor']

    from gtm.content_engine import get_pillar_for_date, collect_aggregate_stats
    Analysis = models_map.get('Analysis')
    stats    = collect_aggregate_stats(db_session, {'Analysis': Analysis})
    pillar   = get_pillar_for_date(target_date)
    pillar_key = pillar['key']

    posts = []

    if 'facebook' in platforms:
        for group in FACEBOOK_GROUPS:
            posts.append(generate_facebook_post(pillar_key, stats, target_date, group, db_session))

    if 'nextdoor' in platforms:
        for hood in NEXTDOOR_NEIGHBORHOODS:
            posts.append(generate_nextdoor_post(pillar_key, stats, target_date, hood, db_session))

    return posts
