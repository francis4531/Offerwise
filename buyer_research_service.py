"""buyer_research_service.py — v5.88.50 customer-discovery draft generation.

Per-user draft generator for buyer outreach. Parallel to prospect_research_service
(B2B side), but simpler:

  - No web search (we already know the user; nothing to look up)
  - No wedge mapping (buyers aren't segmented by enterprise wedge)
  - Conditioned on funnel stage + signup date + last-property context

Same shape as the B2B path: returns {subject, body, error?}. Same em-dash strip
and Francis signoff post-processing. Falls back to a static stage-appropriate
template if Claude is unavailable.

Cost: ~$0.005-0.01 per draft (Claude call only, no web search).
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Stage descriptions used in the prompt ──────────────────────────────
#
# These mirror the 3 funnel stages computed in admin_routes.api_funnel_debug
# and outreach_campaign_service._stage_for_user. The 'description' is what
# Claude reads to understand what this user did/didn't do.

STAGE_CONTEXT = {
    'USED_PRODUCT': {
        'description': (
            "This user ran at least one property analysis and then never came back. "
            "This is the most common failure pattern across the user base, so understanding "
            "WHY they didn't return is the highest-priority question."
        ),
        'reply_options': [
            "A) The analysis was useful, but I bought (or didn't buy) the house and don't need it again",
            "B) The analysis wasn't useful enough to come back",
            "C) I forgot OfferWise existed",
            "D) I tried to use it again but ran into a problem",
            "E) The pricing wasn't clear or felt too high",
            "F) Something else (please tell me)",
        ],
    },
    'ONBOARDED': {
        'description': (
            "This user completed signup and onboarding but never actually ran a property "
            "analysis. They got partway through the funnel and stopped. The question is "
            "what stopped them at that specific point."
        ),
        'reply_options': [
            "A) I didn't have a specific property in mind yet",
            "B) I wasn't sure what to upload or how to start",
            "C) I didn't have the documents the tool was asking for",
            "D) I lost interest before I got to it",
            "E) I tried but the analysis tool wasn't clear how to use",
            "F) I'm no longer planning to buy a house",
            "G) Something else (please tell me)",
        ],
    },
    'SIGNED_UP_ONLY': {
        'description': (
            "This user signed up and then did nothing else. No onboarding, no analysis. "
            "Possibly impulsive signup, possibly hit a snag immediately. The question is "
            "what made them stop after just creating an account."
        ),
        'reply_options': [
            "A) I forgot I signed up",
            "B) I signed up out of curiosity, didn't actually need a property analysis",
            "C) The signup process itself was confusing or annoying",
            "D) I tried to use it but got stuck or confused",
            "E) I changed my mind about buying a house",
            "F) Something else (please tell me)",
        ],
    },
}


# ─── Draft generation ───────────────────────────────────────────────────

DRAFT_PROMPT_TEMPLATE = """\
You are writing a short customer-discovery email from Francis, the solo founder
of OfferWise (an AI property disclosure analysis tool), to a real user. The
goal is to learn why this user hasn't come back to the product.

Write in the voice of a thoughtful, slightly tired solo founder. NOT a marketing
person. NOT a salesperson. You are not trying to convert this user, you are
trying to understand them. This means:
- Short. Under 150 words in the body.
- Personal and direct. No corporate phrases like "checking in", "circling back",
  "wanted to reach out", "we've noticed", "your account".
- One specific thing: ask them a question about why they stopped using the
  product. Make it easy to answer.
- Provide multiple-choice reply options (A, B, C, ...) so they can respond
  with one letter and be done. This dramatically lifts reply rates.
- No pitch. No upsell. No "we'd love to have you back." Explicitly tell them
  you are not pitching anything.
- ABSOLUTELY NO EM-DASHES (the character "—"). Use commas, periods, or
  parentheses. Hyphens in compound words like "single-family" are fine.
- No salutation, no greeting line, no signature. Those are added separately.
- No subject line in the body. That's separate too.

Recipient:
  Name: {name}
  Email: {email}
  Signed up {days_since_signup} days ago ({signup_date})
  Funnel stage: {stage}

What that stage means:
  {stage_description}

{property_context}

Multiple-choice reply options to include in the body (one per line, formatted
as shown):
{reply_options}

Output a JSON object with EXACTLY two keys: "subject" and "body". No prose
before or after. No markdown code fences. Just the JSON.

Example structure:
{{"subject": "...", "body": "..."}}

The subject should be 5-9 words, personal, no marketing speak. Hint at the
question without being clickbaity.

The body should:
1. Open with one specific sentence about what this user did or didn't do.
2. Explain why you're emailing (one sentence: "trying to understand why").
3. The multiple-choice list, prefaced with "Hit reply with whichever applies. One letter is enough:"
4. One sentence reassurance that you are not pitching anything.

Write it now.
"""


def _build_property_context(last_property_address: Optional[str],
                            last_analysis_at: Optional[datetime]) -> str:
    """Construct the property context paragraph for the prompt.

    If the user analyzed a property, include the address and when. This
    lets Claude write a more specific opening line ("you analyzed 1234
    Oak St back in February"). Otherwise note its absence.
    """
    if last_property_address:
        when = ''
        if last_analysis_at:
            when = f' in {last_analysis_at.strftime("%B %Y")}'
        return (
            f"Most recently analyzed property: {last_property_address}{when}. "
            f"Feel free to reference this specifically in the opening line, "
            f"e.g. 'you ran an analysis on {last_property_address} back{when}'."
        )
    return (
        "This user never analyzed a property. Do not reference any specific address. "
        "Speak in general terms about their signup."
    )


def _fallback_subject_and_body(name: str, stage: str, days_since_signup: int,
                                month_joined: str) -> dict[str, Any]:
    """Static stage-specific draft used when Claude is unavailable.

    Mirrors the three v5.88.42 default templates (USED_PRODUCT, ONBOARDED,
    SIGNED_UP_ONLY) almost word-for-word. Customer-tested copy, em-dash free,
    Francis signoff appended downstream.
    """
    first = (name or '').split()[0] if name else 'there'

    if stage == 'USED_PRODUCT':
        subject = "You used OfferWise once. What happened? (Francis, founder)"
        body = (
            f"Hi {first},\n\n"
            f"You ran a property analysis on OfferWise back in {month_joined} "
            f"and didn't come back. That's the pattern I'm seeing across nearly "
            f"every user, and I'd love to understand why.\n\n"
            f"Hit reply with whichever applies. One letter is enough:\n\n"
            f"  A) The analysis was useful, but I bought (or didn't buy) the house and don't need it again\n"
            f"  B) The analysis wasn't useful enough to come back\n"
            f"  C) I forgot OfferWise existed\n"
            f"  D) I tried to use it again but ran into a problem\n"
            f"  E) The pricing wasn't clear or felt too high\n"
            f"  F) Something else (please tell me)\n\n"
            f"No pitch coming. I'm a solo founder trying to figure out if this "
            f"product solves a real problem, and your honest answer means more than "
            f"another 100 signups."
        )
    elif stage == 'ONBOARDED':
        subject = "You got close but didn't finish (Francis at OfferWise)"
        body = (
            f"Hi {first},\n\n"
            f"You signed up for OfferWise in {month_joined} and completed onboarding, "
            f"but never actually ran a property analysis. I'm trying to understand why, "
            f"because you got further than most people and then stopped.\n\n"
            f"Hit reply with whichever applies. One letter is enough:\n\n"
            f"  A) I didn't have a specific property in mind yet\n"
            f"  B) I wasn't sure what to upload or how to start\n"
            f"  C) I didn't have the documents the tool was asking for\n"
            f"  D) I lost interest before I got to it\n"
            f"  E) I tried but the analysis tool wasn't clear how to use\n"
            f"  F) I'm no longer planning to buy a house\n"
            f"  G) Something else (please tell me)\n\n"
            f"No pitch coming. I'm a solo founder and I need to understand why "
            f"people sign up but never analyze a property."
        )
    else:  # SIGNED_UP_ONLY (default)
        subject = "OfferWise question, one letter reply (Francis, founder)"
        body = (
            f"Hi {first},\n\n"
            f"You signed up for OfferWise back in {month_joined} and didn't end up using it. "
            f"That's fine, but I'm a solo founder trying to figure out what's not working, "
            f"and your honest answer is more useful to me than any other data I have.\n\n"
            f"Hit reply with whichever applies. One letter is enough:\n\n"
            f"  A) I forgot I signed up\n"
            f"  B) I signed up out of curiosity, didn't actually need a property analysis\n"
            f"  C) The signup process itself was confusing or annoying\n"
            f"  D) I tried to use it but got stuck or confused\n"
            f"  E) I changed my mind about buying a house\n"
            f"  F) Something else (please tell me)\n\n"
            f"No pitch coming. Not going to ask you to upgrade. Not going to add you to a "
            f"newsletter. Just trying to understand what made you stop."
        )

    return {'subject': subject, 'body': body, 'error': 'fallback_used'}


def draft_buyer_email(user) -> dict[str, Any]:
    """Generate a personalized customer-discovery email draft for one user.

    Args:
        user: A User instance from models.User. Must have at minimum
              email, name (optional), created_at.

    Returns:
        {
            'subject': str,    # 5-9 word subject line
            'body': str,       # personal body, no signoff, no salutation
            'error': str|None, # 'fallback_used' or specific failure
            'stage': str,      # which funnel stage was used
        }

    The returned subject and body are POST-PROCESSED for em-dashes (stripped
    to commas) and signoff (', Francis' prepended at signature line). Caller
    should persist as-is.
    """
    # ─── Compute context ─────────────────────────────────────────────
    name = (user.name or '').strip() or ''
    email = (user.email or '').strip()
    created_at = user.created_at
    if not created_at:
        days_since_signup = 0
        signup_date = '(unknown date)'
        month_joined = '(unknown month)'
    else:
        days_since_signup = max(0, (datetime.utcnow() - created_at).days)
        signup_date = created_at.strftime('%B %d, %Y')
        month_joined = created_at.strftime('%B %Y')

    # Funnel stage classification (mirrors admin_routes.api_funnel_debug)
    stage = _classify_funnel_stage(user)

    # Most recent analyzed property (for opener personalization)
    last_property_address, last_analysis_at = _get_last_property_context(user)

    # ─── Build the prompt ──────────────────────────────────────────
    stage_meta = STAGE_CONTEXT.get(stage, STAGE_CONTEXT['SIGNED_UP_ONLY'])
    property_context = _build_property_context(last_property_address, last_analysis_at)
    reply_options_formatted = '\n'.join(f'  {opt}' for opt in stage_meta['reply_options'])

    prompt = DRAFT_PROMPT_TEMPLATE.format(
        name=name or '(unknown)',
        email=email,
        days_since_signup=days_since_signup,
        signup_date=signup_date,
        stage=stage,
        stage_description=stage_meta['description'],
        property_context=property_context,
        reply_options=reply_options_formatted,
    )

    # ─── Try Claude ──────────────────────────────────────────────────
    try:
        from ai_client import get_ai_response
        raw = get_ai_response(prompt, max_tokens=900, temperature=0.4)
    except Exception as e:
        logger.warning('draft_buyer_email Claude call failed: %s', e.__class__.__name__)
        out = _fallback_subject_and_body(name, stage, days_since_signup, month_joined)
        out['stage'] = stage
        return out

    # ─── Parse JSON ──────────────────────────────────────────────────
    cleaned = (raw or '').strip()
    if cleaned.startswith('```'):
        # Strip ```json ... ``` wrapper
        lines = cleaned.split('\n')
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        cleaned = '\n'.join(lines).strip()

    try:
        parsed = json.loads(cleaned)
        subject = (parsed.get('subject') or '').strip()
        body = (parsed.get('body') or '').strip()
        if not subject or not body:
            logger.warning('draft_buyer_email: Claude returned empty subject/body')
            out = _fallback_subject_and_body(name, stage, days_since_signup, month_joined)
            out['stage'] = stage
            return out
    except json.JSONDecodeError as e:
        logger.warning('draft_buyer_email JSON parse failed: %s. Raw[:200]: %r', e, (raw or '')[:200])
        out = _fallback_subject_and_body(name, stage, days_since_signup, month_joined)
        out['stage'] = stage
        return out

    # ─── Post-process ────────────────────────────────────────────────
    # 1. Defensive em-dash strip (founder directive — no em-dashes in
    #    outreach). Prompt forbids them but LLMs slip.
    subject = subject.replace('—', ', ').replace('–', ', ')
    body = body.replace('—', ', ').replace('–', ', ')

    # 2. Append Francis signoff if not already present.
    body_lower = body.lower().rstrip()
    already_signed = (
        body_lower.endswith('-francis')
        or body_lower.endswith(', francis')
        or body_lower.endswith('francis anthony')
        or body_lower.endswith('francis at offerwise')
        or 'francis\nofferwise' in body_lower
        or 'francis, offerwise' in body_lower
    )
    if not already_signed:
        body = body.rstrip() + '\n\nFrancis\nOfferWise'

    return {
        'subject': subject,
        'body': body,
        'error': None,
        'stage': stage,
    }


# ─── Funnel stage classification ────────────────────────────────────────

def _classify_funnel_stage(user) -> str:
    """Same logic as admin_routes.api_funnel_debug. Replicated here so
    this module doesn't pull admin_routes (which would be a circular
    import — admin_routes imports this module).
    """
    try:
        from models import Property
        analyses = Property.query.filter_by(user_id=user.id).count()
    except Exception:
        analyses = 0

    onboarded = bool(getattr(user, 'onboarding_completed', False))
    has_stripe = bool(getattr(user, 'stripe_customer_id', None))

    # CONVERTED/CHECKOUT_STARTED users shouldn't normally reach the
    # customer-discovery flow, but if they do, return the best-fit
    # stage (treat them as USED_PRODUCT since presumably they used it
    # before they paid).
    if has_stripe or analyses > 0:
        return 'USED_PRODUCT'
    if onboarded:
        return 'ONBOARDED'
    return 'SIGNED_UP_ONLY'


def _get_last_property_context(user) -> tuple[Optional[str], Optional[datetime]]:
    """Return (address, analyzed_at) for the user's most recent property.

    Returns (None, None) if the user has no properties or the lookup
    fails. Never raises.
    """
    try:
        from models import Property
        p = (Property.query
             .filter_by(user_id=user.id)
             .order_by(Property.id.desc())
             .first())
        if not p:
            return None, None
        return p.address, p.analyzed_at
    except Exception as e:
        logger.warning('_get_last_property_context failed for user %s: %s',
                       getattr(user, 'id', '?'), e)
        return None, None
