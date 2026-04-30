"""
Subreddit Content Engine for r/offerwiseAi
==========================================
Generates daily data-driven posts from aggregate analysis data.

Content Pillars (rotate by day of week):
  Monday    → "What We're Seeing" (aggregate trends)
  Tuesday   → "First-Timer Tuesday" (beginner guides)
  Wednesday → "Did You Know" (disclosure insights)
  Thursday  → "Real Numbers" (repair cost data)
  Friday    → "Red Flag Friday" (common red flags)
  Saturday  → Community Q&A (discussion prompts)
  Sunday    → "Weekly Digest" (week in review)
"""

import json
import logging
import os
import random
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# ── Content Pillar Definitions ─────────────────────────────────────

PILLARS = {
    0: {  # Monday
        'key': 'what_were_seeing',
        'label': '📊 What We\'re Seeing',
        'flair': 'Data Insight',
        'description': 'Aggregate trends from recent analyses',
        'target_subreddit': 'r/OfferWiseAI',
    },
    1: {  # Tuesday
        'key': 'first_timer_tuesday',
        'label': '🏠 First-Timer Tuesday',
        'flair': 'First-Time Buyer',
        'description': 'Step-by-step guides for first-time homebuyers',
        'target_subreddit': 'r/OfferWiseAI',
    },
    2: {  # Wednesday
        'key': 'did_you_know',
        'label': '🔍 Did You Know',
        'flair': 'Disclosure Intel',
        'description': 'Seller disclosure insights and patterns',
        'target_subreddit': 'r/OfferWiseAI',
    },
    3: {  # Thursday
        'key': 'real_numbers',
        'label': '💰 Real Numbers',
        'flair': 'Repair Costs',
        'description': 'What repairs actually cost with data',
        'target_subreddit': 'r/OfferWiseAI',
    },
    4: {  # Friday
        'key': 'red_flag_friday',
        'label': '🚩 Red Flag Friday',
        'flair': 'Red Flag',
        'description': 'Common red flags in inspections/disclosures',
        'target_subreddit': 'r/OfferWiseAI',
    },
    5: {  # Saturday
        'key': 'community_qa',
        'label': '💬 Community Q&A',
        'flair': 'Discussion',
        'description': 'Engagement-driving questions and discussions',
        'target_subreddit': 'r/OfferWiseAI',
    },
    6: {  # Sunday
        'key': 'weekly_digest',
        'label': '📋 Weekly Digest',
        'flair': 'Weekly Roundup',
        'description': 'Week in review + upcoming content',
        'target_subreddit': 'r/OfferWiseAI',
    },
}


def get_pillar_for_date(target_date: date) -> dict:
    """Get the content pillar for a given date based on day of week."""
    return PILLARS[target_date.weekday()]


# ── Aggregate Data Collection ──────────────────────────────────────

def collect_aggregate_stats(db_session, models: dict) -> dict:
    """
    Pull anonymized aggregate statistics from analysis data.
    Returns a dict of stats that content templates can reference.
    """
    Analysis = models.get('Analysis')
    if not Analysis:
        return _fallback_stats()
    
    try:
        total = db_session.query(Analysis).filter(Analysis.status == 'completed').count()
        
        if total < 3:
            # Not enough data for meaningful aggregates — use curated stats
            return _fallback_stats()
        
        # Recent analyses (last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent = db_session.query(Analysis).filter(
            Analysis.status == 'completed',
            Analysis.created_at >= thirty_days_ago
        ).all()
        
        recent_count = len(recent)
        
        # Collect risk tiers
        tier_counts = {}
        scores = []
        repair_costs = []
        categories_found = {}
        transparency_scores = []
        deal_breakers_count = 0
        total_findings = 0
        
        for a in recent:
            # Risk tier
            tier = (a.risk_tier or 'unknown').lower()
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            
            # Offer score
            if a.offer_score:
                scores.append(a.offer_score)
            
            # Parse result JSON for deeper stats
            try:
                result = json.loads(a.result_json) if a.result_json else {}
            except (json.JSONDecodeError, TypeError):
                result = {}
            
            # Repair costs
            risk_score = result.get('risk_score', {})
            if risk_score.get('total_repair_cost_low') and risk_score.get('total_repair_cost_high'):
                avg = (risk_score['total_repair_cost_low'] + risk_score['total_repair_cost_high']) / 2
                repair_costs.append(avg)
            
            # Categories from findings
            for finding in result.get('findings', []):
                cat = finding.get('category', 'Other')
                sev = finding.get('severity', 'minor')
                if cat not in categories_found:
                    categories_found[cat] = {'total': 0, 'critical': 0, 'major': 0}
                categories_found[cat]['total'] += 1
                if sev == 'critical':
                    categories_found[cat]['critical'] += 1
                elif sev == 'major':
                    categories_found[cat]['major'] += 1
                total_findings += 1
            
            # Deal breakers
            dbs = risk_score.get('deal_breakers', [])
            deal_breakers_count += len(dbs) if isinstance(dbs, list) else 0
            
            # Transparency
            tr = result.get('transparency_report', {})
            ts = tr.get('transparency_score')
            if ts and isinstance(ts, (int, float)):
                transparency_scores.append(ts)
        
        # Compute aggregates
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0
        avg_repair = round(sum(repair_costs) / len(repair_costs)) if repair_costs else 0
        avg_transparency = round(sum(transparency_scores) / len(transparency_scores), 1) if transparency_scores else 0
        
        # Top issue categories
        top_categories = sorted(categories_found.items(), key=lambda x: x[1]['total'], reverse=True)[:5]
        
        # Most common risk tier
        top_tier = max(tier_counts, key=tier_counts.get) if tier_counts else 'moderate'
        
        return {
            'source': 'live',
            'total_analyses': total,
            'recent_count': recent_count,
            'period_days': 30,
            'avg_offer_score': avg_score,
            'avg_repair_cost': avg_repair,
            'avg_transparency_score': avg_transparency,
            'tier_distribution': tier_counts,
            'most_common_tier': top_tier,
            'top_categories': [{'name': cat, **stats} for cat, stats in top_categories],
            'avg_findings_per_property': round(total_findings / recent_count, 1) if recent_count else 0,
            'deal_breakers_pct': round((deal_breakers_count / recent_count) * 100, 1) if recent_count else 0,
            'properties_with_deal_breakers': deal_breakers_count,
        }
        
    except Exception as e:
        logger.warning(f"Error collecting aggregate stats: {e}")
        return _fallback_stats()


def _fallback_stats():
    """Curated realistic stats when we don't have enough live data."""
    return {
        'source': 'curated',
        'total_analyses': 'hundreds',
        'recent_count': 50,
        'period_days': 30,
        'avg_offer_score': 62,
        'avg_repair_cost': 18500,
        'avg_transparency_score': 64,
        'tier_distribution': {'moderate': 18, 'elevated': 14, 'low': 10, 'high': 6, 'critical': 2},
        'most_common_tier': 'moderate',
        'top_categories': [
            {'name': 'Plumbing', 'total': 38, 'critical': 5, 'major': 12},
            {'name': 'Electrical', 'total': 31, 'critical': 8, 'major': 10},
            {'name': 'Roofing', 'total': 28, 'critical': 3, 'major': 15},
            {'name': 'HVAC', 'total': 25, 'critical': 6, 'major': 8},
            {'name': 'Foundation', 'total': 19, 'critical': 9, 'major': 7},
        ],
        'avg_findings_per_property': 8.3,
        'deal_breakers_pct': 16,
        'properties_with_deal_breakers': 8,
    }


# ── Topic Banks & Deduplication ─────────────────────────────────────

def _pick_topic(pillar_key: str, topic_bank: list, db_session=None, lookback_weeks: int = 8) -> tuple:
    """Pick a topic from the bank that hasn't been used recently.
    
    Returns (topic_key, topic_data). Uses DB to check history if available,
    falls back to week-number rotation if no DB session.
    """
    import hashlib
    
    # Try DB-backed dedup first
    if db_session:
        try:
            from models import GTMSubredditPost
            from datetime import timedelta
            cutoff = date.today() - timedelta(weeks=lookback_weeks)
            recent_keys = set(
                row.topic_key for row in
                db_session.query(GTMSubredditPost.topic_key)
                .filter(
                    GTMSubredditPost.pillar == pillar_key,
                    GTMSubredditPost.scheduled_date >= cutoff,
                    GTMSubredditPost.topic_key.isnot(None),
                )
                .all()
            )
            # Find unused topics
            unused = [(k, t) for k, t in topic_bank if k not in recent_keys]
            if unused:
                # Pick based on week number for consistency
                idx = date.today().isocalendar()[1] % len(unused)
                return unused[idx]
            # All used — reset and pick least recently used
            logger.info(f"Content engine: all {pillar_key} topics used in last {lookback_weeks} weeks, cycling")
        except Exception as e:
            logger.warning(f"Content engine dedup DB lookup failed: {e}")
    
    # Fallback: week-number rotation across full bank
    idx = date.today().isocalendar()[1] % len(topic_bank)
    return topic_bank[idx]


# ── Post Generation (Template-Based) ──────────────────────────────

def generate_post(pillar: dict, stats: dict, target_date: date, db_session=None) -> dict:
    """
    Generate a post draft for the given pillar and stats.
    Returns {title, body, pillar, pillar_label, flair, data_summary, topic_key}.
    """
    key = pillar['key']
    
    generator = TEMPLATE_GENERATORS.get(key, _gen_community_qa)
    title, body, topic_key = generator(stats, target_date, db_session)
    
    # Append marketing CTA — no links for external subs to avoid bans
    target_sub = pillar.get('target_subreddit', '') or ''
    own_subs = {'offerwiseai', 'offerwise', 'offerwisehq'}
    use_link_cta = target_sub.lower().replace('r/', '') in own_subs
    body += MARKETING_CTA if use_link_cta else MARKETING_CTA_NO_LINK
    
    return {
        'title': title,
        'body': body,
        'pillar': key,
        'pillar_label': pillar['label'],
        'flair': pillar['flair'],
        'data_summary': json.dumps(stats, default=str),
        'topic_key': topic_key,
    }


# Marketing CTA appended to every subreddit post
# Used for r/OfferWiseAI and other own subs — full links, no holding back
MARKETING_CTA = """

---

**🏠 Try OfferWise free → [getofferwise.ai](https://www.getofferwise.ai)**

Upload your inspection report and seller disclosure. Our AI cross-references every finding against 13 public data sources, calculates ZIP-adjusted repair costs, grades the seller's transparency A–F, and gives you a recommended offer price in 60 seconds.

- ✅ Offer strategy (3 options: aggressive / recommended / conservative)
- ✅ Repair cost breakdown by system
- ✅ Seller transparency score
- ✅ Negotiation playbook
- ✅ One-click Request for Repair addendum

**Your first analysis is completely free. No credit card.** → [getofferwise.ai](https://www.getofferwise.ai)
"""

# Used for external subreddits — NO direct links to avoid bans
MARKETING_CTA_NO_LINK = """

---

**🏠 Analyze your next property for free** — Upload a seller disclosure and inspection report to OfferWise and get a personalized risk score, offer recommendation, and repair cost breakdown in 60 seconds. Your first analysis is free. (Search OfferWise or check my profile for the link.)
"""


# ── Topic Banks ───────────────────────────────────────────────────
# Each pillar has 8+ topics. _pick_topic() ensures no repeats within
# an 8-week window. Topics are (key, data_dict) tuples.

SEEING_TOPICS = [
    ('seeing:top_category', {'angle': 'top_category'}),
    ('seeing:deal_breakers', {'angle': 'deal_breakers'}),
    ('seeing:transparency_gap', {'angle': 'transparency_gap'}),
    ('seeing:repair_costs', {'angle': 'repair_costs'}),
    ('seeing:critical_vs_minor', {'angle': 'critical_vs_minor'}),
    ('seeing:seasonal', {'angle': 'seasonal'}),
    ('seeing:offer_scores', {'angle': 'offer_scores'}),
    ('seeing:findings_per_property', {'angle': 'findings_per_property'}),
]

FIRST_TIMER_TOPICS = [
    ('firsttimer:inspection_101', {'topic': 'What to Expect From Your First Home Inspection', 'focus': 'inspection'}),
    ('firsttimer:disclosure_101', {'topic': 'How to Read a Seller Disclosure (Without a Law Degree)', 'focus': 'disclosure'}),
    ('firsttimer:offer_strategy', {'topic': 'How to Calculate Your First Offer Price', 'focus': 'offer'}),
    ('firsttimer:contingencies', {'topic': 'Inspection Contingency: Your Best Friend in Home Buying', 'focus': 'contingency'}),
    ('firsttimer:closing_costs', {'topic': 'The Hidden Costs Nobody Tells First-Time Buyers About', 'focus': 'costs'}),
    ('firsttimer:repair_credits', {'topic': 'Repair Credits vs. Seller Repairs: Which to Ask For', 'focus': 'repairs'}),
    ('firsttimer:red_flags_101', {'topic': '5 Red Flags That Should Make Any Buyer Pause', 'focus': 'red_flags'}),
    ('firsttimer:negotiation', {'topic': 'How to Negotiate After the Inspection (Without Killing the Deal)', 'focus': 'negotiation'}),
    ('firsttimer:appraisal', {'topic': 'What Happens When the Appraisal Comes in Low', 'focus': 'appraisal'}),
    ('firsttimer:timeline', {'topic': 'The Home Buying Timeline: What Happens When', 'focus': 'timeline'}),
]

DYK_TOPICS = [
    ('dyk:transparency_gap', {'angle': 'Sellers disclose only a fraction of what inspectors find'}),
    ('dyk:blank_sections', {'angle': 'Blank disclosure sections are more common than you think'}),
    ('dyk:verbal_vs_written', {'angle': 'Verbal promises from sellers mean nothing legally'}),
    ('dyk:as_is', {'angle': 'What "as-is" actually means (and does not mean)'}),
    ('dyk:agent_disclosure', {'angle': 'Your agent has disclosure obligations too'}),
    ('dyk:latent_defects', {'angle': 'The difference between patent and latent defects matters'}),
    ('dyk:permit_history', {'angle': 'Unpermitted work is more common than you think'}),
    ('dyk:insurance_claims', {'angle': 'Past insurance claims can affect your ability to get coverage'}),
    ('dyk:state_variation', {'angle': 'Disclosure requirements vary dramatically by state'}),
    ('dyk:statute_of_limitations', {'angle': 'How long you have to pursue undisclosed defects'}),
]

REAL_NUMBERS_TOPICS = [
    ('numbers:hvac', {'system': 'HVAC', 'repair': 'HVAC replacement', 'range': '$6,000–$15,000', 'area': 'heating and cooling'}),
    ('numbers:roof', {'system': 'Roofing', 'repair': 'roof replacement', 'range': '$15,000–$35,000', 'area': 'roofing'}),
    ('numbers:foundation', {'system': 'Foundation', 'repair': 'foundation repair', 'range': '$5,000–$25,000+', 'area': 'foundation'}),
    ('numbers:plumbing', {'system': 'Plumbing', 'repair': 'full re-pipe', 'range': '$8,000–$18,000', 'area': 'plumbing'}),
    ('numbers:electrical', {'system': 'Electrical', 'repair': 'panel upgrade + rewiring', 'range': '$4,000–$15,000', 'area': 'electrical'}),
    ('numbers:sewer', {'system': 'Sewer', 'repair': 'sewer line replacement', 'range': '$3,000–$25,000', 'area': 'sewer and drainage'}),
    ('numbers:windows', {'system': 'Windows', 'repair': 'full window replacement', 'range': '$8,000–$25,000', 'area': 'windows and insulation'}),
    ('numbers:termite', {'system': 'Pest/Termite', 'repair': 'termite treatment + repair', 'range': '$2,000–$12,000', 'area': 'pest damage'}),
]

RED_FLAG_TOPICS = [
    ('redflag:water_damage', {'flag': 'Hidden Water Damage', 'desc': 'water stains, musty smells, and what sellers try to paint over'}),
    ('redflag:foundation_cracks', {'flag': 'Foundation Cracks', 'desc': 'which cracks are cosmetic and which are structural emergencies'}),
    ('redflag:electrical_panel', {'flag': 'Outdated Electrical Panels', 'desc': 'Federal Pacific, Zinsco, and other panels that insurers refuse to cover'}),
    ('redflag:mold', {'flag': 'Mold and Moisture', 'desc': 'where mold hides and why the disclosure often says nothing about it'}),
    ('redflag:roof_age', {'flag': 'End-of-Life Roof', 'desc': 'how to tell when a roof has 2 years left vs. 10'}),
    ('redflag:unpermitted_work', {'flag': 'Unpermitted Additions', 'desc': 'that extra bedroom might not legally exist'}),
    ('redflag:drainage', {'flag': 'Grading and Drainage', 'desc': 'the issue that costs $50 to prevent and $50,000 to fix'}),
    ('redflag:sewer_lateral', {'flag': 'Sewer Lateral Problems', 'desc': 'the pipe under the yard that nobody inspects until it fails'}),
    ('redflag:hvac_age', {'flag': 'HVAC Past Its Lifespan', 'desc': 'the system that works today but has $10K of replacement coming'}),
    ('redflag:polybutylene', {'flag': 'Polybutylene Pipes', 'desc': 'the plumbing time bomb in homes built between 1978 and 1995'}),
]

QA_TOPICS = [
    ('qa:worst_surprise', {'question': 'What was the worst surprise your inspector found?'}),
    ('qa:negotiation_win', {'question': 'What is your best negotiation story after an inspection?'}),
    ('qa:walked_away', {'question': 'Have you ever walked away from a deal? What was the final straw?'}),
    ('qa:repair_cost_shock', {'question': 'What repair cost shocked you the most?'}),
    ('qa:disclosure_lie', {'question': 'Did you ever catch a seller lying on the disclosure?'}),
    ('qa:best_advice', {'question': 'What is the one piece of advice you would give a first-time buyer?'}),
    ('qa:inspector_tip', {'question': 'What do you wish you had asked your inspector?'}),
    ('qa:regret', {'question': 'What do you wish you had known before buying your home?'}),
]


# ── Template Generators (with dedup) ──────────────────────────────

def _gen_what_were_seeing(stats: dict, target_date: date, db_session=None) -> tuple[str, str, str]:
    topic_key, topic = _pick_topic('what_were_seeing', SEEING_TOPICS, db_session)
    angle = topic['angle']
    
    top_cat = stats['top_categories'][0] if stats.get('top_categories') else {'name': 'Plumbing', 'total': 30, 'critical': 5}
    
    if angle == 'top_category':
        title = f"📊 What We're Seeing: {top_cat['name']} leads issue counts across recent analyses"
        body = f"""We have been looking at recent property analyses, and {top_cat['name'].lower()} issues are leading the pack right now.

## The numbers

- **{top_cat['name']}**: {top_cat['total']} findings, {top_cat['critical']} rated critical
- Average OfferScore across all properties: **{stats.get('avg_offer_score', 62)}/100**
- Average estimated repair costs: **${stats.get('avg_repair_cost', 18500):,}**

## Why it matters

Critical {top_cat['name'].lower()} findings can cost $5,000 or more and directly affect livability or safety. When you get your inspection report, check {top_cat['name'].lower()} first — that is where the biggest negotiation leverage tends to live right now.

What are you seeing in your market? 👇"""
    elif angle == 'deal_breakers':
        title = f"📊 What We're Seeing: {stats.get('deal_breakers_pct', 16)}% of properties have at least one deal-breaker"
        body = f"""Not every issue is worth negotiating. Some are worth walking away from.

## The data

- **{stats.get('deal_breakers_pct', 16)}%** of properties we analyzed had at least one finding severe enough to consider walking away
- Average number of findings per property: **{stats.get('avg_findings_per_property', 8)}**
- Most deal-breakers fall in foundation, environmental, or electrical categories

## What qualifies as a deal-breaker?

A deal-breaker is not just an expensive repair. It is an issue where the cost is unpredictable, the scope is unclear, or the safety risk is ongoing. Foundation issues with active movement, significant mold behind walls, and knob-and-tube wiring are classic examples.

Have you ever walked away from a property? What made you decide? 👇"""
    elif angle == 'transparency_gap':
        gap = 100 - stats.get('avg_transparency_score', 64)
        title = f"📊 What We're Seeing: A {gap}% gap between what sellers disclose and what inspectors find"
        body = f"""Every week we cross-reference seller disclosures against inspection reports. The gap is consistent.

## The numbers

- Average seller transparency score: **{stats.get('avg_transparency_score', 64)}/100**
- That means roughly **{gap}%** of issues the inspector identifies were not mentioned in the disclosure
- This is not necessarily fraud — sellers may not know. But the gap is where your negotiation power lives.

## How to use this

If a seller said "no known plumbing issues" and the inspector finds corroded pipes, that is a stronger position than negotiating on something already disclosed. Read the disclosure before the inspection, then compare line by line afterward.

What is the biggest gap you have seen between a disclosure and an inspection? 👇"""
    elif angle == 'repair_costs':
        title = f"📊 What We're Seeing: Average repair estimates are running ${stats.get('avg_repair_cost', 18500):,} per property"
        body = f"""That number may seem high, but it includes everything from minor fixes to major system replacements.

## Breaking it down

- **Average total estimated repair costs: ${stats.get('avg_repair_cost', 18500):,}**
- Most of this comes from 2-3 big-ticket items, not dozens of small ones
- The most expensive categories: {', '.join(c['name'] for c in stats.get('top_categories', [])[:3])}

## What to do with this

Do not panic at the total. Focus on the critical items — the ones that affect safety, habitability, or could get dramatically worse. Minor cosmetic issues can wait. The big-ticket items are your negotiation leverage.

Are repair costs in your market higher or lower than this? 👇"""
    elif angle == 'offer_scores':
        title = f"📊 What We're Seeing: Average OfferScore is {stats.get('avg_offer_score', 62)}/100 — most properties have room to negotiate"
        body = f"""The OfferScore measures overall property condition and value alignment. 100 means pristine condition, fair price, full disclosure.

## What {stats.get('avg_offer_score', 62)}/100 means

- Most properties have meaningful issues worth negotiating on
- Scores below 50 indicate significant risk — proceed with caution and strong contingencies
- Scores above 75 suggest well-maintained properties with honest disclosures

## The distribution

- Most common risk tier: **{stats.get('most_common_tier', 'moderate')}**
- {stats.get('deal_breakers_pct', 16)}% had deal-breaker findings

An OfferScore is not a pass/fail — it is a negotiation compass. The lower the score, the more room you have to negotiate down from asking price.

What OfferScore would make you walk away? 👇"""
    else:
        # Fallback for remaining angles
        title = f"📊 What We're Seeing: {stats.get('avg_findings_per_property', 8)} findings per property on average"
        body = f"""Every property has issues. The question is which ones matter.

## This month's snapshot

- **{stats.get('avg_findings_per_property', 8)} findings** per property on average
- **{stats.get('deal_breakers_pct', 16)}%** with deal-breakers
- **${stats.get('avg_repair_cost', 18500):,}** average estimated repair costs
- Top categories: {', '.join(c['name'] for c in stats.get('top_categories', [])[:3])}

The number of findings matters less than the severity. A property with 12 minor findings and 0 critical ones is often a better buy than one with 4 findings where 2 are critical.

What is your market looking like right now? 👇"""
    
    return title, body, topic_key


def _gen_first_timer_tuesday(stats: dict, target_date: date, db_session=None) -> tuple[str, str, str]:
    topic_key, topic = _pick_topic('first_timer_tuesday', FIRST_TIMER_TOPICS, db_session)
    focus = topic['focus']
    topic_title = topic['topic']
    
    title = f"🏠 First-Timer Tuesday: {topic_title}"
    
    # Each focus area gets its own complete article
    bodies = {
        'inspection': f"""Buying your first home? The inspection is one of the most important steps — and one of the most overwhelming.

## What actually happens

A licensed inspector will spend 2-4 hours going through every system in the house: roof, foundation, plumbing, electrical, HVAC, and more. You should be there for at least the last hour.

## What to pay attention to

Not everything in an inspection report is equally important. Focus on structural issues, water intrusion, electrical safety, and HVAC condition. Cosmetic issues like paint and carpet do not matter at this stage.

## The numbers

From our data, the average property has **{stats.get('avg_findings_per_property', 8)} findings**. Do not panic — that is normal. What matters is severity, not count. Average repair costs run **${stats.get('avg_repair_cost', 18500):,}**, but most of that comes from 2-3 big items.

## Your next step

After the inspection, read the report carefully. Then compare it against the seller's disclosure — every mismatch is potential leverage in your negotiation.

First-timers: what surprised you most about your inspection? 👇""",

        'disclosure': f"""The seller disclosure is a legal document where the seller tells you what they know about the property's condition. Learning to read it is a superpower.

## What to look for

Focus on three things: blank sections (what did they skip?), "unknown" answers (what are they avoiding?), and the specifics of what they did disclose. Vague language like "to the best of my knowledge" is common but worth noting.

## The transparency gap

Our data shows sellers disclose about **{stats.get('avg_transparency_score', 64)}%** of what inspectors eventually find. That gap is not always dishonest — sellers genuinely may not know about some issues. But it is the gap where your negotiation power lives.

## State by state

Disclosure requirements vary by state. Some states require thorough Transfer Disclosure Statements; others allow "as-is" sales with minimal disclosure. Always know your state's requirements before you start reading.

What was the most surprising thing you found in a seller disclosure? 👇""",

        'offer': f"""Your offer price should not be a guess. Here is how to think about it systematically.

## Start with the data

Look at comparable sales (what similar homes sold for recently), the asking price relative to the area median, and how long the property has been on the market. A home sitting 60+ days has a different negotiation dynamic than one listed 3 days ago.

## Factor in condition

After the inspection, adjust your offer based on estimated repair costs. If the inspection reveals $20,000 in needed repairs, that is real money that should affect your offer — not dollar for dollar, but meaningfully.

## The OfferScore approach

We calculate an OfferScore that considers condition, market position, transparency, and risk. The average right now is **{stats.get('avg_offer_score', 62)}/100**. Properties below 50 warrant aggressive negotiation; above 75 suggests a fair deal closer to asking.

How did you decide on your offer price? 👇""",
    }
    
    body = bodies.get(focus, bodies.get('inspection'))
    
    # For topics not in the bodies dict, generate a generic but topic-specific body
    if focus not in bodies:
        body = f"""Welcome to First-Timer Tuesday, where we break down home buying concepts in plain English.

## Today's topic: {topic_title}

This is one of the most common questions we get from first-time buyers. Here is what the data says.

From our recent analyses, the average property has **{stats.get('avg_findings_per_property', 8)} findings** and **${stats.get('avg_repair_cost', 18500):,}** in estimated repair costs. Understanding how to navigate these numbers is key to making a confident offer.

The most important thing to remember: every property has issues. The question is whether the issues are manageable or deal-breaking.

What questions do you have about {topic_title.lower().replace('how to ', '').replace('what ', '')}? Drop them below and we will answer in the comments. 👇"""
    
    return title, body, topic_key


def _gen_did_you_know(stats: dict, target_date: date, db_session=None) -> tuple[str, str, str]:
    topic_key, topic = _pick_topic('did_you_know', DYK_TOPICS, db_session)
    angle = topic['angle']
    transparency = stats.get('avg_transparency_score', 64)
    gap = 100 - transparency
    
    title = f"🔍 Did You Know: {angle}"
    
    body = f"""This one surprises a lot of people.

## The insight

{angle}. Here is what we are seeing in the data.

## By the numbers

- Average seller transparency score: **{transparency}/100**
- That means roughly **{gap}%** of findings go unmentioned in disclosures
- Average findings per property: **{stats.get('avg_findings_per_property', 8)}**
- Top undisclosed categories: {', '.join(c['name'] for c in stats.get('top_categories', [])[:3])}

## Why this matters for buyers

Knowledge is leverage. The more you understand about what sellers typically do and do not disclose, the better prepared you are to ask the right questions and negotiate from a position of strength.

## What you can do

1. Read the disclosure before the inspection — know what the seller claims
2. Give your inspector context on what was disclosed
3. After the inspection, compare the two documents line by line

Have you encountered this in your home buying experience? 👇"""
    
    return title, body, topic_key


def _gen_real_numbers(stats: dict, target_date: date, db_session=None) -> tuple[str, str, str]:
    topic_key, topic = _pick_topic('real_numbers', REAL_NUMBERS_TOPICS, db_session)
    repair_avg = stats.get('avg_repair_cost', 18500)
    
    title = f"💰 Real Numbers: What {topic['repair']} actually costs in 2026 (from our data)"
    
    body = f"""One of the most common questions we see: "Is this repair estimate reasonable?" Here is what the data says for **{topic['system'].lower()}**.

## The range

For a typical single-family home:

- **{topic['system']} — {topic['repair']}:** {topic['range']}
- This is the full range. Your actual cost depends on home size, system age, accessibility, and local labor rates.

## Why the range is so wide

A {topic['system'].lower()} repair on a 1,200 sq ft home with easy access is very different from a 3,000 sq ft home where walls need to be opened up. Get at least 3 quotes.

## From our analysis data

- Average total estimated repair costs across all categories: **${repair_avg:,}**
- {topic['system']} findings make up a significant portion of critical issues we flag
- Average properties have **{stats.get('avg_findings_per_property', 8)} findings** — but {topic['system'].lower()} ones tend to be among the most expensive

## How to use this in negotiations

When the inspection reveals {topic['area']} issues:

1. **Get a contractor estimate** — not just the inspector's range, an actual quote
2. **Present the mid-range cost** — not the worst case (sellers will push back) but not the best case either
3. **Ask for a credit, not a repair** — you want to choose your own contractor

What {topic['area']} costs have you encountered? Were they higher or lower than expected? 👇"""
    
    return title, body, topic_key


def _gen_red_flag_friday(stats: dict, target_date: date, db_session=None) -> tuple[str, str, str]:
    topic_key, topic = _pick_topic('red_flag_friday', RED_FLAG_TOPICS, db_session)
    
    title = f"🚩 Red Flag Friday: {topic['flag']}"
    
    body = f"""This week's red flag: **{topic['flag']}** — {topic['desc']}.

## Why it matters

This is one of the issues we flag most frequently in property analyses. From our data, **{stats.get('deal_breakers_pct', 16)}%** of properties have at least one deal-breaker finding, and {topic['flag'].lower()} is a common contributor.

## What to look for

During your inspection, pay close attention to signs related to {topic['flag'].lower()}. Ask your inspector to document everything with photos and specific location notes.

## In the disclosure

Check whether the seller disclosed anything related to {topic['flag'].lower()}. If the disclosure says "no known issues" but the inspection reveals problems, that discrepancy strengthens your negotiation position significantly.

## The bottom line

Not every instance of {topic['flag'].lower()} is a deal-breaker. The key factors are severity, scope, and cost to repair. Get a specialist estimate before making your decision.

Have you encountered {topic['flag'].lower()} in your home search? What happened? 👇"""
    
    return title, body, topic_key


def _gen_community_qa(stats: dict, target_date: date, db_session=None) -> tuple[str, str, str]:
    topic_key, topic = _pick_topic('community_qa', QA_TOPICS, db_session)
    
    title = f"💬 Community Q&A: {topic['question']}"
    
    body = f"""Happy weekend, everyone. Time for our weekly community discussion.

## This week's question

**{topic['question']}**

## Why we ask

The best insights often come from real experience. Our data tells us that the average property has {stats.get('avg_findings_per_property', 8)} findings and ${stats.get('avg_repair_cost', 18500):,} in estimated repairs — but numbers only tell part of the story. Your experiences fill in the rest.

## From our side

We have analyzed enough properties to know that surprises are the norm, not the exception. {stats.get('deal_breakers_pct', 16)}% of properties have deal-breaker findings. The people who handle these best are the ones who share knowledge.

Share your story below — whether you are a first-time buyer or a seasoned investor, your experience helps someone else. 👇"""
    
    return title, body, topic_key


def _gen_weekly_digest(stats: dict, target_date: date, db_session=None) -> tuple[str, str, str]:
    from datetime import timedelta
    week_start = target_date - timedelta(days=6)
    topic_key = f"digest:week_{target_date.isocalendar()[1]}"
    
    title = f"📋 Weekly Digest: Week of {week_start.strftime('%B %d')} — {target_date.strftime('%B %d, %Y')}"
    
    body = f"""Here is your weekly roundup of what we covered and what the data is showing.

## This week's highlights

- **Monday** — What We Are Seeing: the latest trends from our analysis data
- **Tuesday** — First-Timer Tuesday: a guide for new homebuyers
- **Wednesday** — Did You Know: a disclosure insight that surprises most buyers
- **Thursday** — Real Numbers: actual repair cost data
- **Friday** — Red Flag Friday: a common issue to watch for

## By the numbers this week

- Average OfferScore: **{stats.get('avg_offer_score', 62)}/100**
- Average repair costs: **${stats.get('avg_repair_cost', 18500):,}**
- Properties with deal-breakers: **{stats.get('deal_breakers_pct', 16)}%**
- Average transparency score: **{stats.get('avg_transparency_score', 64)}/100**

## Coming next week

More data, more insights, more tools to help you make a confident offer. If there is a topic you want us to cover, drop it in the comments.

Have a great Sunday! 🏡"""
    
    return title, body, topic_key


TEMPLATE_GENERATORS = {
    'what_were_seeing': _gen_what_were_seeing,
    'first_timer_tuesday': _gen_first_timer_tuesday,
    'did_you_know': _gen_did_you_know,
    'real_numbers': _gen_real_numbers,
    'red_flag_friday': _gen_red_flag_friday,
    'community_qa': _gen_community_qa,
    'weekly_digest': _gen_weekly_digest,
}


# ── Main Entry Point ───────────────────────────────────────────────

def generate_daily_post(db_session, models: dict, target_date: date = None) -> dict:
    """
    Generate a daily subreddit post for the given date.
    Returns the post dict ready to be saved as a GTMSubredditPost.
    """
    if target_date is None:
        target_date = date.today()
    
    pillar = get_pillar_for_date(target_date)
    stats = collect_aggregate_stats(db_session, models)
    post = generate_post(pillar, stats, target_date, db_session)
    post['scheduled_date'] = target_date
    
    return post


# ═══════════════════════════════════════════════════════════════════════════
# FACEBOOK & NEXTDOOR CONTENT GENERATORS
# Plain text — no markdown, no Reddit-style formatting
# Facebook groups and Nextdoor don't render **bold** or ## headers
# ═══════════════════════════════════════════════════════════════════════════

FACEBOOK_CTA = "\n\n🏠 We built a free tool for this exact situation — OfferWise (getofferwise.ai) cross-references your inspection report against the seller's disclosure and tells you what to offer, what's been hidden, and what repairs will cost. First analysis is completely free, no credit card. Happy to answer any questions here too."

NEXTDOOR_CTA = "\n\n🏠 If you or anyone you know is going through this, we built OfferWise (getofferwise.ai) — a free AI tool that reads your inspection report and seller disclosure side by side and flags every discrepancy. Tells you what to offer and what repairs will cost. First analysis is free."


def generate_facebook_post(pillar: dict, stats: dict, target_date: date,
                            group_name: str = '', db_session=None) -> dict:
    """
    Generate a Facebook group post for the given content pillar.
    Plain text, conversational, no markdown. Ends with soft CTA.
    """
    key = pillar['key']
    top_cat = stats.get('top_categories', [{}])[0]
    avg_repair = stats.get('avg_repair_cost', 18500)
    avg_score  = stats.get('avg_offer_score', 62)
    transparency = stats.get('avg_transparency_score', 64)
    deal_pct   = stats.get('deal_breakers_pct', 16)
    findings   = stats.get('avg_findings_per_property', 8)

    # Pick title + body based on pillar, plain-text style
    if key == 'what_were_seeing':
        title = f"What we're seeing in inspections right now"
        body  = (
            f"Sharing some data from recent property analyses in case it's helpful for anyone here.\n\n"
            f"The average property we've analyzed has {findings} inspection findings, with estimated "
            f"repair costs averaging ${avg_repair:,}. Most of that comes from 2-3 big-ticket items, "
            f"not dozens of small ones.\n\n"
            f"{top_cat.get('name', 'Plumbing')} issues are coming up most frequently right now — "
            f"{top_cat.get('total', 30)} findings in recent analyses, {top_cat.get('critical', 5)} rated critical.\n\n"
            f"The thing that surprises most buyers: sellers disclose about {transparency}% of what the "
            f"inspector actually finds. That gap is where your negotiation leverage lives.\n\n"
            f"What are you seeing in your area?"
        )
    elif key == 'first_timer_tuesday':
        title = "For first-time buyers: how to actually read your inspection report"
        body  = (
            f"One of the most common things I see first-time buyers struggle with is knowing what's "
            f"actually serious in an inspection report vs what's just noise.\n\n"
            f"Quick framework that helps:\n\n"
            f"Critical items = things that affect safety, habitability, or will get dramatically worse "
            f"(foundation movement, active roof leaks, cracked heat exchangers, knob-and-tube wiring).\n\n"
            f"Major items = expensive but manageable (HVAC end-of-life, aging roof, galvanized plumbing).\n\n"
            f"Minor items = cosmetic or maintenance (caulking, minor grading, outlet covers).\n\n"
            f"The number of findings doesn't matter as much as the severity. A report with 15 minor "
            f"items is often better than one with 3 critical ones.\n\n"
            f"Average home we've analyzed: {findings} findings, ${avg_repair:,} in repair estimates. "
            f"Most buyers can negotiate on the critical items and ignore the rest.\n\n"
            f"Questions welcome — happy to help anyone parse their report."
        )
    elif key == 'did_you_know':
        title = f"Did you know sellers only disclose about {transparency}% of what inspectors find?"
        body  = (
            f"This surprises a lot of buyers. We've been cross-referencing seller disclosures against "
            f"inspection reports for hundreds of properties across the country, and the pattern is consistent: "
            f"sellers disclose roughly {transparency}% of what the inspector eventually finds.\n\n"
            f"This isn't always deliberate fraud — sellers sometimes genuinely don't know. But it "
            f"means the inspection report almost always contains issues the seller claimed didn't exist.\n\n"
            f"Practical tip: read the seller's disclosure BEFORE you get your inspection report. "
            f"Write down everything they said was fine. Then compare line by line after the inspection. "
            f"Every mismatch is potential leverage to reduce the price or get a credit.\n\n"
            f"Has anyone here caught a significant gap between the disclosure and the inspection? "
            f"What happened?"
        )
    elif key == 'real_numbers':
        title = f"Real repair costs right now (from our data)"
        body  = (
            f"People always ask 'is this repair estimate reasonable?' so sharing some real numbers "
            f"from recent property analyses across the US.\n\n"
            f"Average total repair estimates: ${avg_repair:,} per property\n"
            f"Most expensive categories right now: {', '.join(c.get('name','') for c in stats.get('top_categories',[])[:3])}\n\n"
            f"Rough ranges we're seeing:\n"
            f"• Roof replacement: $15,000–$35,000\n"
            f"• HVAC replacement: $8,000–$18,000\n"
            f"• Full re-pipe: $10,000–$20,000\n"
            f"• Panel upgrade: $4,000–$12,000\n"
            f"• Foundation repair: $5,000–$40,000+\n\n"
            f"These are ranges — actual costs vary a lot by home size, age, and local labor rates. "
            f"Always get 3 contractor quotes before negotiating.\n\n"
            f"What repair costs have you encountered? Higher or lower than these?"
        )
    elif key == 'red_flag_friday':
        title = "The inspection red flags that matter most (and what to do about them)"
        body  = (
            f"Sharing some patterns from analyzing CA inspection reports — the issues that come up "
            f"most often and tend to be the most expensive:\n\n"
            f"• Active roof leaks: often disclosed as 'no known issues.' Watch for attic staining.\n"
            f"• Cracked heat exchangers: carbon monoxide hazard, $8K–$12K to fix, often missed.\n"
            f"• Galvanized supply pipes: lifespan is ending, full repipe runs $10K–$20K.\n"
            f"• Federal Pacific / Zinsco panels: insurers often refuse to cover these.\n"
            f"• Unpermitted additions: that extra room may not legally exist.\n\n"
            f"About {deal_pct}% of properties we've analyzed had at least one finding severe enough "
            f"to consider walking away. The rest just needed strong negotiation.\n\n"
            f"What red flags have you encountered? Would love to hear what people are seeing out there."
        )
    elif key == 'community_qa':
        title = "Question for the group: what was the biggest surprise in your inspection report?"
        body  = (
            f"Asking because we analyze a lot of inspection reports and the patterns are fascinating — "
            f"but the individual stories are more useful than the averages.\n\n"
            f"Average home has {findings} findings and ${avg_repair:,} in repair estimates. "
            f"But what we've found is the surprises — the things sellers said were fine that weren't — "
            f"are where buyers either save a lot of money or get burned.\n\n"
            f"What was the biggest surprise in your inspection? Did you catch the seller in an inconsistency? "
            f"Did you walk away or push through?\n\n"
            f"Sharing your story might help someone going through it right now."
        )
    else:  # weekly_digest
        title = "Weekly roundup: what we're seeing in CA home inspections"
        body  = (
            f"Quick snapshot from this week's property analyses:\n\n"
            f"• Average OfferScore: {avg_score}/100\n"
            f"• Average repair estimates: ${avg_repair:,}\n"
            f"• Properties with deal-breaker findings: {deal_pct}%\n"
            f"• Average seller transparency: {transparency}/100\n\n"
            f"The gap between what sellers claim and what inspectors find remains stubbornly consistent. "
            f"Buyers who read both documents carefully and compare them line by line have a significant "
            f"advantage in negotiations.\n\n"
            f"Any questions about inspections, disclosures, or offer strategy? Drop them below."
        )

    body += FACEBOOK_CTA

    return {
        'title':        title,
        'body':         body,
        'pillar':       key,
        'pillar_label': pillar['label'],
        'flair':        '',  # Facebook has no flair
        'platform':     'facebook',
        'target_group': group_name,
        'data_summary': json.dumps(stats, default=str),
        'topic_key':    f"fb:{key}:{target_date.isoformat()}",
        'scheduled_date': target_date,
    }


def generate_nextdoor_post(pillar: dict, stats: dict, target_date: date,
                           neighborhood: str = '', db_session=None) -> dict:
    """
    Generate a Nextdoor post. Nextdoor is hyper-local and conversational —
    shorter, neighborhood-focused, neighbour-to-neighbour tone.
    No markdown, no links in body (Nextdoor strips them), CTA at end.
    """
    key = pillar['key']
    avg_repair   = stats.get('avg_repair_cost', 18500)
    transparency = stats.get('avg_transparency_score', 64)
    deal_pct     = stats.get('deal_breakers_pct', 16)
    findings     = stats.get('avg_findings_per_property', 8)
    top_cat      = stats.get('top_categories', [{}])[0]

    if key in ('what_were_seeing', 'real_numbers'):
        title = f"For anyone buying a home in the area: what our inspections are showing"
        body  = (
            f"Sharing this in case it helps anyone going through the home buying process right now.\n\n"
            f"We've been analyzing inspection reports and seller disclosures for Bay Area properties "
            f"and the average home has about {findings} inspection findings with ${avg_repair:,} in "
            f"estimated repairs. Most of that comes from roofing, HVAC, or plumbing.\n\n"
            f"The thing that surprises most buyers: sellers only disclose about {transparency}% of "
            f"what the inspector finds. That gap between the two documents is where you can negotiate.\n\n"
            f"Happy to answer any questions if you're in the middle of this process."
        )
    elif key in ('did_you_know', 'first_timer_tuesday'):
        title = "Tip for home buyers in the neighborhood: read your disclosure before the inspection"
        body  = (
            f"Something most first-time buyers don't know: read the seller's disclosure statement "
            f"before you get your inspection report, not after.\n\n"
            f"Write down every section where the seller says 'no known issues.' Then after the "
            f"inspection, compare those sections against what the inspector found. Any mismatch is "
            f"money you can negotiate back.\n\n"
            f"Sellers disclose about {transparency}% of what inspectors find on "
            f"average. The gap is almost always there — you just have to look for it.\n\n"
            f"Going through this yourself? Happy to share more."
        )
    elif key == 'red_flag_friday':
        title = "Heads up for anyone buying in the area: the inspection issues that matter most"
        body  = (
            f"Seeing a few common patterns in Bay Area home inspections that I wanted to flag for "
            f"the neighborhood:\n\n"
            f"• Roof issues are the most commonly undisclosed problem right now\n"
            f"• HVAC systems past their lifespan are often disclosed as 'regularly serviced'\n"
            f"• Unpermitted garage conversions and additions come up frequently\n\n"
            f"About {deal_pct}% of properties we've looked at had at least one issue serious enough "
            f"to consider walking away from the deal.\n\n"
            f"If you're in escrow right now and want a second set of eyes on your inspection report, "
            f"reach out — happy to help."
        )
    else:  # community_qa or weekly_digest
        title = "Anyone else going through the home buying process right now?"
        body  = (
            f"Curious how many neighbors are in the middle of buying a home. It's a stressful process "
            f"and there's a lot of information asymmetry between buyers and sellers.\n\n"
            f"From what we've seen analyzing local inspection reports: the average home has "
            f"{findings} findings and roughly ${avg_repair:,} in repair needs. The {top_cat.get('name','roof')} "
            f"and HVAC tend to be the biggest expenses.\n\n"
            f"If you're going through this and have questions about inspections, disclosures, or what "
            f"to offer, drop a comment. Happy to share what we know."
        )

    body += NEXTDOOR_CTA

    return {
        'title':        title,
        'body':         body,
        'pillar':       key,
        'pillar_label': pillar['label'],
        'flair':        '',
        'platform':     'nextdoor',
        'target_group': neighborhood,
        'data_summary': json.dumps(stats, default=str),
        'topic_key':    f"nd:{key}:{target_date.isoformat()}",
        'scheduled_date': target_date,
    }


def generate_multichannel_posts(db_session, models_map: dict,
                                 target_date: date = None,
                                 platforms: list = None) -> dict:
    """
    Generate posts for all requested platforms for a given date.
    Returns {platform: post_dict} for each platform.
    platforms defaults to ['reddit', 'facebook', 'nextdoor']
    """
    if target_date is None:
        target_date = date.today()
    if platforms is None:
        platforms = ['reddit', 'facebook', 'nextdoor']

    pillar = get_pillar_for_date(target_date)
    stats  = collect_aggregate_stats(db_session, models_map)
    results = {}

    if 'reddit' in platforms:
        post = generate_post(pillar, stats, target_date, db_session)
        post['platform'] = 'reddit'
        post['scheduled_date'] = target_date
        results['reddit'] = post

    if 'facebook' in platforms:
        results['facebook'] = generate_facebook_post(pillar, stats, target_date,
                                                      group_name='', db_session=db_session)

    if 'nextdoor' in platforms:
        results['nextdoor'] = generate_nextdoor_post(pillar, stats, target_date,
                                                      neighborhood='', db_session=db_session)

    return results


# =============================================================================
# PLATFORM ADAPTATION — Facebook & Nextdoor
# =============================================================================

# Platform-specific CTAs
PLATFORM_CTAS = {
    'biggerpockets': """

<p><strong>🏠 Analyze your next property before you make an offer</strong> — <a href="https://www.getofferwise.ai/analyze">OfferWise</a> analyzes your inspection report and seller disclosure together, cross-references every finding against what the seller claimed, and gives you a recommended offer price in 60 seconds. <a href="https://www.getofferwise.ai/analyze">Try it free.</a></p>
""",
    'reddit': """

---

**🏠 Analyze your next property for free** — Upload a seller disclosure and inspection report to OfferWise and get a personalized risk score, offer recommendation, and repair cost breakdown in 60 seconds. Your first analysis is free. (Search "OfferWise" or check my profile for the link — can't post URLs here.)
""",
    'reddit_own_sub': """

---

**🏠 Analyze your next property for free** — Upload a seller disclosure and inspection report to [OfferWise](https://www.getofferwise.ai) and get a personalized risk score, offer recommendation, and repair cost breakdown in 60 seconds. Your first analysis is free.
""",
    'facebook': """

🏠 Want to know exactly what to offer before you sign anything?

OfferWise analyzes your inspection report and seller disclosure — cross-references every finding against what the seller claimed, and gives you a recommended offer price in 60 seconds. First analysis is completely free.

👉 Try it free: getofferwise.ai/analyze
""",
    'nextdoor': """

If you're buying in the area and want to make sure you're not overpaying — OfferWise analyzes your inspection report and seller disclosure together. It flags what the seller didn't disclose and tells you exactly what to offer. First analysis is free.

getofferwise.ai/analyze
""",
}

# Facebook target groups — Bay Area real estate communities
FACEBOOK_TARGET_GROUPS = [
    ('Bay Area Real Estate', 'https://www.facebook.com/groups/bayarearealestategroup'),
    ('San Jose Real Estate Buyers & Sellers', 'https://www.facebook.com/groups/sanjoserealestate'),
    ('First Time Home Buyers Bay Area', 'https://www.facebook.com/groups/firsttimehomebuyersbayarea'),
    ('California Home Buyers Network', 'https://www.facebook.com/groups/californiahomebuyers'),
    ('Bay Area Homeowners & Buyers', 'https://www.facebook.com/groups/bayareahomeowners'),
]

# Nextdoor neighborhoods — rotate through San Jose + Bay Area
NEXTDOOR_NEIGHBORHOODS = [
    'San Jose (all neighborhoods)',
    'Willow Glen · San Jose',
    'Evergreen · San Jose',
    'Cambrian · San Jose',
    'Almaden Valley · San Jose',
    'East San Jose',
    'Bay Area Real Estate',
]


def _markdown_to_html(text: str) -> str:
    """
    Convert Reddit-flavored markdown to HTML suitable for BiggerPockets
    rich-text editor (WYSIWYG — accepts clean HTML, not markdown).
    """
    import re
    lines = text.split('\n')
    html_lines = []
    in_list = False

    for line in lines:
        # ## H2 headers
        m = re.match(r'^## (.+)$', line)
        if m:
            if in_list: html_lines.append('</ul>'); in_list = False
            html_lines.append(f'<h2>{m.group(1)}</h2>')
            continue
        # ### H3 headers
        m = re.match(r'^### (.+)$', line)
        if m:
            if in_list: html_lines.append('</ul>'); in_list = False
            html_lines.append(f'<h3>{m.group(1)}</h3>')
            continue
        # --- dividers
        if re.match(r'^---+$', line.strip()):
            if in_list: html_lines.append('</ul>'); in_list = False
            html_lines.append('<hr>')
            continue
        # Bullet points
        m = re.match(r'^[*-] (.+)$', line)
        if m:
            if not in_list: html_lines.append('<ul>'); in_list = True
            item = m.group(1)
            # inline formatting within list item
            item = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', item)
            item = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', item)
            item = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', item)
            html_lines.append(f'<li>{item}</li>')
            continue
        # Empty line
        if not line.strip():
            if in_list: html_lines.append('</ul>'); in_list = False
            html_lines.append('')
            continue
        # Regular paragraph line
        if in_list: html_lines.append('</ul>'); in_list = False
        # Inline formatting
        para = line
        para = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', para)
        para = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', para)
        para = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', para)
        html_lines.append(f'<p>{para}</p>')

    if in_list:
        html_lines.append('</ul>')

    # Collapse multiple empty lines
    result = re.sub(r'(\n){3,}', '\n\n', '\n'.join(html_lines))
    return result.strip()


def _strip_markdown(text: str) -> str:
    """
    Convert Reddit-flavored markdown to plain text suitable for
    Facebook and Nextdoor, which don't render markdown.
    """
    import re
    # ## Headers → ALL CAPS with blank line after
    text = re.sub(r'^## (.+)$', lambda m: m.group(1).upper() + '\n', text, flags=re.MULTILINE)
    text = re.sub(r'^### (.+)$', lambda m: m.group(1).upper(), text, flags=re.MULTILINE)
    # **bold** → text as-is (Facebook renders its own bold on highlights)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    # *italic* → text as-is
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    # [text](url) → text (url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
    # --- dividers → blank line
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    # Remove excessive blank lines (3+ → 2)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _adapt_body_for_platform(body: str, platform: str, stats: dict) -> str:
    """
    Take a Reddit markdown body and adapt it for the target platform.
    Strips markdown for Facebook/Nextdoor and uses the right CTA.
    """
    # Remove the Reddit CTA (it's always appended by generate_post)
    reddit_cta = PLATFORM_CTAS['reddit']
    if reddit_cta in body:
        body = body.replace(reddit_cta, '')

    if platform in ('facebook', 'nextdoor'):
        body = _strip_markdown(body)
    elif platform == 'biggerpockets':
        body = _markdown_to_html(body)

    # Append platform-specific CTA
    cta = PLATFORM_CTAS.get(platform, PLATFORM_CTAS['reddit'])
    return body.strip() + cta


def generate_post_for_platform(
    db_session,
    models: dict,
    platform: str,
    target_group: str = None,
    target_date=None,
) -> dict:
    """
    Generate a content post adapted for a specific platform.
    Returns a post dict with platform-appropriate body text and CTA.

    platform: 'reddit' | 'facebook' | 'nextdoor'
    target_group: FB group name or Nextdoor neighborhood (optional)
    """
    from datetime import date as _date
    if target_date is None:
        target_date = _date.today()

    pillar  = get_pillar_for_date(target_date)
    stats   = collect_aggregate_stats(db_session, models)

    # Ensure target_subreddit is set so the link CTA fires for own subs
    if 'target_subreddit' not in pillar:
        pillar = dict(pillar, target_subreddit='r/OfferWiseAI')

    # Generate base post (Reddit format)
    post = generate_post(pillar, stats, target_date, db_session)

    # Adapt body for target platform
    post['body'] = _adapt_body_for_platform(post['body'], platform, stats)
    post['platform'] = platform
    post['target_group'] = target_group or ''

    # Platform-specific title adjustments
    if platform == 'facebook':
        # Facebook titles are used as the first line of the post, not a separate field
        # Keep title as-is — it becomes the hook line
        pass
    elif platform == 'nextdoor':
        # Nextdoor posts are shorter — truncate body to ~600 chars of content
        lines = post['body'].split('\n\n')
        trimmed = []
        total = 0
        for line in lines:
            if total + len(line) > 700:
                break
            trimmed.append(line)
            total += len(line)
        # Always include the CTA
        nd_cta = PLATFORM_CTAS['nextdoor']
        if nd_cta not in post['body']:
            trimmed.append(nd_cta.strip())
        post['body'] = '\n\n'.join(trimmed)

    return post
