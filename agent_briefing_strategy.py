"""Agent Briefing offer-strategy generator (v5.88.77 R3).

Generates a 3-scenario offer strategy + bottom-line summary for an
AgentBriefing, given the repair-cost analysis and the buyer's budget
tiers. Used only by the briefing flow — distinct from the existing
OfferWiseIntelligence._generate_offer_strategy which is buyer-profile-
centric and not budget-tier-aware.

Design choices:

- **Hybrid: deterministic anchors + LLM polish.** Pure formula ignores
  agent commentary (which often contains negotiation-relevant context
  like "seller listed 60 days ago, motivated"). Pure LLM is unstable
  across runs. So we compute baseline numbers deterministically, pass
  them + commentary + repair findings to Claude, ask it to produce
  the final 3 scenarios with rationales, then clamp the numbers back
  to the budget tiers so the LLM can't recommend outside them.

- **Buyer side only at R3.** Seller-side framing (Counter / Hold /
  Concede) ships in R4.

- **Fail-soft.** If Claude is unavailable or the JSON parse fails, we
  return the deterministic baseline with generic rationales rather
  than crashing the whole briefing. The briefing row still persists.

- **Cost tracked.** Every Claude call passes through ai_cost_tracker so
  briefing LLM cost shows up alongside the rest of the system.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Public dataclass-style return: dict with these keys.
#   scenarios: list of {label, price, rationale} dicts (3 items)
#   bottom_line: str (2-3 sentences in agent's voice)
#   ok: bool — whether LLM call succeeded (False → deterministic fallback)


def generate_buyer_offer_strategy(
    *,
    property_price: float,
    repair_low: float,
    repair_high: float,
    budget_qualified: float,
    budget_comfortable: float,
    budget_preferred: float,
    agent_commentary: str,
    property_address: str = '',
    findings_summary: str = '',
) -> Dict[str, Any]:
    """Produce a 3-scenario offer strategy + bottom line for a buyer-side
    briefing.

    Returns a dict like:
        {
          'scenarios': [
            {'label': 'Opening offer', 'price': 1050000,
             'rationale': "Anchors below..."},
            {'label': 'Walk-up', 'price': 1150000,
             'rationale': "..."},
            {'label': 'Walk-away', 'price': 1200000,
             'rationale': "..."},
          ],
          'bottom_line': "Given the $30K repair runway and the seller's...",
          'ok': True,
          'source': 'llm' | 'deterministic_fallback',
        }
    """
    # ── 1. Compute deterministic baseline anchors ─────────────────────
    # These act as the starting numbers Claude is told to "produce
    # rationales for". Claude can adjust them slightly within a band but
    # the final clamp at the bottom forces them into the budget tiers.
    repair_mid = (repair_low + repair_high) / 2.0 if (repair_low or repair_high) else 0.0

    # Opening = lower of (property_price - full repair high) or (preferred - some headroom)
    # The idea: the agent wants to anchor low. Using repair_high (not mid)
    # gives a defensible negotiating position grounded in inspection findings.
    opening_anchor_a = max(0, property_price - repair_high) if property_price else (budget_preferred * 0.92)
    opening_anchor_b = budget_preferred * 0.92 if budget_preferred else opening_anchor_a
    opening_anchor = min(opening_anchor_a, opening_anchor_b)
    # Floor opening at 80% of preferred — going lower is usually not credible
    if budget_preferred:
        opening_anchor = max(opening_anchor, budget_preferred * 0.80)

    # Walk-up = comfortable budget (where the buyer is "comfortable")
    walkup_anchor = budget_comfortable

    # Walk-away = qualified budget OR property_price + small premium,
    # whichever is lower. Above this, buyer can't or shouldn't go.
    if budget_qualified and property_price:
        walkaway_anchor = min(budget_qualified, property_price * 1.05)
    else:
        walkaway_anchor = budget_qualified or (property_price * 1.05) or 0

    # ── 2. Try LLM for rationales + bottom line ────────────────────────
    llm_result = _call_claude_for_strategy(
        property_price=property_price,
        repair_low=repair_low,
        repair_high=repair_high,
        repair_mid=repair_mid,
        budget_qualified=budget_qualified,
        budget_comfortable=budget_comfortable,
        budget_preferred=budget_preferred,
        opening_anchor=opening_anchor,
        walkup_anchor=walkup_anchor,
        walkaway_anchor=walkaway_anchor,
        agent_commentary=agent_commentary,
        property_address=property_address,
        findings_summary=findings_summary,
    )

    if llm_result is not None:
        # Clamp LLM numbers back to budget tiers — defensive against
        # the model going outside what the buyer has indicated.
        scenarios = _clamp_scenarios(
            llm_result.get('scenarios') or [],
            budget_qualified=budget_qualified,
            budget_comfortable=budget_comfortable,
            budget_preferred=budget_preferred,
            opening_anchor=opening_anchor,
            walkup_anchor=walkup_anchor,
            walkaway_anchor=walkaway_anchor,
        )
        return {
            'scenarios': scenarios,
            'bottom_line': (llm_result.get('bottom_line') or '').strip(),
            'ok': True,
            'source': 'llm',
        }

    # ── 3. Deterministic fallback ──────────────────────────────────────
    # Used only when the LLM call fails. Generic rationales — better
    # than nothing but doesn't reflect the agent's commentary.
    logger.warning("Briefing offer strategy: LLM unavailable, using deterministic fallback")
    return {
        'scenarios': [
            {
                'label': 'Opening offer',
                'price': int(round(opening_anchor)),
                'rationale': (
                    f"Anchors below the buyer's preferred budget with the documented "
                    f"${int(repair_low):,}-${int(repair_high):,} in repair findings as "
                    f"the negotiation lever. Leaves room to counter."
                ),
            },
            {
                'label': 'Walk-up',
                'price': int(round(walkup_anchor)),
                'rationale': (
                    f"The buyer's comfortable monthly budget supports this number. "
                    f"Above this, the deal stops being a clear win."
                ),
            },
            {
                'label': 'Walk-away',
                'price': int(round(walkaway_anchor)),
                'rationale': (
                    f"The buyer's qualified ceiling. Going above this either requires "
                    f"new financing or makes the math no longer work given known repairs."
                ),
            },
        ],
        'bottom_line': (
            f"Given the ${int(repair_low):,}-${int(repair_high):,} in known repair "
            f"findings and the buyer's budget tiers, I'd open at "
            f"${int(round(opening_anchor)):,}. We have room to negotiate up to "
            f"${int(round(walkup_anchor)):,}. Above ${int(round(walkaway_anchor)):,} "
            f"the deal doesn't pencil."
        ),
        'ok': False,
        'source': 'deterministic_fallback',
    }


def _call_claude_for_strategy(
    *,
    property_price: float,
    repair_low: float,
    repair_high: float,
    repair_mid: float,
    budget_qualified: float,
    budget_comfortable: float,
    budget_preferred: float,
    opening_anchor: float,
    walkup_anchor: float,
    walkaway_anchor: float,
    agent_commentary: str,
    property_address: str,
    findings_summary: str,
) -> Optional[Dict[str, Any]]:
    """Make the Claude call. Returns parsed JSON dict on success, None on
    any failure (caller falls back to deterministic).
    """
    # Lazy import — heavy.
    try:
        from analysis_ai_helper import AnalysisAIHelper
        helper = AnalysisAIHelper()
        if not (helper and getattr(helper, 'client', None)):
            return None
    except Exception as e:
        logger.warning(f"Briefing offer strategy: helper init failed: {e}")
        return None

    prompt = _build_strategy_prompt(
        property_price=property_price,
        repair_low=repair_low,
        repair_high=repair_high,
        repair_mid=repair_mid,
        budget_qualified=budget_qualified,
        budget_comfortable=budget_comfortable,
        budget_preferred=budget_preferred,
        opening_anchor=opening_anchor,
        walkup_anchor=walkup_anchor,
        walkaway_anchor=walkaway_anchor,
        agent_commentary=agent_commentary,
        property_address=property_address,
        findings_summary=findings_summary,
    )

    try:
        _t0 = time.time()
        response = helper.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        # Cost tracking (best-effort).
        try:
            try:
                from app import app as _ow_app, db as _ow_db
            except Exception:
                _ow_app, _ow_db = None, None
            from ai_cost_tracker import track_ai_call as _track
            _track(response, "briefing-offer-strategy", (time.time() - _t0) * 1000, db=_ow_db, app=_ow_app)
        except Exception:
            pass

        raw = response.content[0].text
    except Exception as e:
        logger.warning(f"Briefing offer strategy: LLM call failed: {e.__class__.__name__}: {e}")
        return None

    # Parse JSON out of the response. Claude sometimes wraps with markdown
    # fences; strip them.
    cleaned = raw.strip()
    if cleaned.startswith('```'):
        # Drop leading ```json or ``` and trailing ```
        lines = cleaned.split('\n')
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        cleaned = '\n'.join(lines).strip()
    # Find JSON object boundaries — Claude may include explanatory text.
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start == -1 or end == -1 or end <= start:
        logger.warning(f"Briefing offer strategy: no JSON in response: {cleaned[:200]!r}")
        return None
    cleaned = cleaned[start:end + 1]

    try:
        parsed = json.loads(cleaned)
    except Exception as e:
        logger.warning(f"Briefing offer strategy: JSON parse failed: {e}; raw[:300]={cleaned[:300]!r}")
        return None

    # Validate shape.
    scenarios = parsed.get('scenarios')
    if not isinstance(scenarios, list) or len(scenarios) < 3:
        logger.warning(f"Briefing offer strategy: malformed scenarios: {scenarios!r}")
        return None
    for s in scenarios[:3]:
        if not isinstance(s, dict):
            return None
        if 'price' not in s or 'label' not in s or 'rationale' not in s:
            return None

    return parsed


def _build_strategy_prompt(
    *,
    property_price: float,
    repair_low: float,
    repair_high: float,
    repair_mid: float,
    budget_qualified: float,
    budget_comfortable: float,
    budget_preferred: float,
    opening_anchor: float,
    walkup_anchor: float,
    walkaway_anchor: float,
    agent_commentary: str,
    property_address: str,
    findings_summary: str,
) -> str:
    """Build the Claude prompt for offer strategy generation."""
    return f"""You are helping a real estate buyer's agent draft an offer strategy for their client.
The deliverable will be read by the agent's buyer client. The agent's voice — direct,
professional, no fluff — is what should come through.

PROPERTY: {property_address or 'subject property'}
LISTING PRICE: ${int(property_price):,} {"(listed)" if property_price else "(unknown)"}

REPAIR COST ANALYSIS:
Total estimated repair budget: ${int(repair_low):,} – ${int(repair_high):,}
Mid-point: ${int(repair_mid):,}

{findings_summary if findings_summary else ''}

BUYER'S BUDGET (in their own words):
- Qualified up to: ${int(budget_qualified):,} (lender max)
- Comfortable up to: ${int(budget_comfortable):,} (monthly payment they want)
- Prefers at or below: ${int(budget_preferred):,} (where they'd like to land)

AGENT'S COMMENTARY (paste from the agent):
\"\"\"
{agent_commentary}
\"\"\"

ANCHORS (computed from the math; you may adjust ±5% if the commentary strongly suggests it):
- Opening offer: ${int(opening_anchor):,}
- Walk-up: ${int(walkup_anchor):,}
- Walk-away: ${int(walkaway_anchor):,}

TASK: Produce a JSON object with exactly two fields:

1. "scenarios": an array of exactly 3 scenarios in this order:
   - {{"label": "Opening offer", "price": <int>, "rationale": "<1 short sentence>"}}
   - {{"label": "Walk-up", "price": <int>, "rationale": "<1 short sentence>"}}
   - {{"label": "Walk-away", "price": <int>, "rationale": "<1 short sentence>"}}

2. "bottom_line": a 2-3 sentence summary in the AGENT'S voice. The agent's
   client will read this. It should sound like a confident professional
   speaking, NOT like AI marketing copy. Reference specific numbers
   (repair total, opening offer, walk-away). If the agent's commentary
   mentions context (seller motivation, market conditions, timing), weave
   one piece of it into the bottom line. NO emojis, NO buzzwords, NO
   "leveraging insights" type phrases. Talk like a real agent.

RULES:
- Prices must be whole integers (no decimals).
- Opening < Walk-up < Walk-away.
- Walk-away ≤ ${int(budget_qualified):,} (the buyer cannot afford more than this).
- Rationales: ≤25 words each, concrete, grounded in the specific numbers above.
- The bottom line should be the agent talking to their buyer, not OfferWise talking.

Return ONLY the JSON object. No preamble, no markdown, no commentary."""


def _clamp_scenarios(
    scenarios_raw,
    *,
    budget_qualified: float,
    budget_comfortable: float,
    budget_preferred: float,
    opening_anchor: float,
    walkup_anchor: float,
    walkaway_anchor: float,
):
    """Clamp LLM-returned scenario prices to defensible bounds.

    Even with explicit constraints in the prompt, LLMs occasionally
    return numbers outside the budget tiers. This applies hard limits
    so we never recommend something the buyer can't afford or
    something so low the agent looks unprofessional.

    Order is also enforced (opening < walk-up < walk-away).
    """
    out = []
    for i, s in enumerate(scenarios_raw[:3]):
        try:
            price = int(round(float(s.get('price', 0))))
        except (TypeError, ValueError):
            price = 0
        label = str(s.get('label') or ['Opening offer', 'Walk-up', 'Walk-away'][i])
        rationale = str(s.get('rationale') or '').strip()
        out.append({'label': label, 'price': price, 'rationale': rationale})

    # Clamp walk-away ≤ qualified
    if budget_qualified and out[2]['price'] > budget_qualified:
        out[2]['price'] = int(round(budget_qualified))
    # Clamp opening ≥ floor (80% of preferred)
    if budget_preferred:
        floor = int(round(budget_preferred * 0.80))
        if out[0]['price'] < floor:
            out[0]['price'] = floor
    # Enforce strict ordering
    if out[1]['price'] <= out[0]['price']:
        out[1]['price'] = out[0]['price'] + int(round((out[2]['price'] - out[0]['price']) / 2))
    if out[2]['price'] <= out[1]['price']:
        out[2]['price'] = out[1]['price'] + 1
    return out


def generate_seller_offer_strategy(
    *,
    property_price: float,
    repair_low: float,
    repair_high: float,
    agent_commentary: str,
    property_address: str = '',
    findings_summary: str = '',
) -> Dict[str, Any]:
    """Produce a 3-scenario negotiation-defense strategy + bottom line for
    a seller-side briefing.

    Unlike the buyer-side flow, seller-side has no budget tiers. The
    scenarios are anchored to the asking price minus a portion of the
    documented repair burden — small at the Counter end, full at the
    Concede end.

    Returns a dict like:
        {
          'scenarios': [
            {'label': 'Counter', 'price': 1230000,
             'rationale': "Acknowledges the HVAC..."},
            {'label': 'Hold', 'price': 1210000,
             'rationale': "..."},
            {'label': 'Concede', 'price': 1175000,
             'rationale': "..."},
          ],
          'bottom_line': "Given the documented $X-$Y in...",
          'ok': True,
          'source': 'llm' | 'deterministic_fallback',
        }
    """
    if not property_price or property_price <= 0:
        # Without a list price, the seller-side strategy doesn't have an
        # anchor. Return a friendly empty result that the caller can
        # detect and the UI can render as "list price required for
        # strategy" — better than fabricating numbers.
        return {
            'scenarios': [],
            'bottom_line': '',
            'ok': False,
            'source': 'no_list_price',
        }

    # If there are no repair findings, there's nothing to negotiate over
    # — every scenario would collapse to the list price. Return empty
    # rather than fabricate three identical numbers.
    if not (repair_low or repair_high):
        return {
            'scenarios': [],
            'bottom_line': '',
            'ok': False,
            'source': 'no_findings',
        }

    # ── 1. Deterministic anchors ──────────────────────────────────────
    repair_mid = (repair_low + repair_high) / 2.0 if (repair_low or repair_high) else 0.0

    # Counter: list price minus a small acknowledgment (half of low-end repair).
    # Sends the signal "we hear you, but not at your number."
    counter_anchor = property_price - (repair_low / 2.0 if repair_low else 0.0)

    # Hold: list price minus the repair mid-point. The seller is willing
    # to credit the realistic middle of the inspection range.
    hold_anchor = property_price - repair_mid

    # Concede: list price minus the full high-end repair total. This is
    # the floor — full credit for everything documented. Going lower
    # would imply the inspection findings underestimate the issue.
    concede_anchor = property_price - repair_high

    # Defensive: never let the concede anchor go negative or below 70%
    # of list. A 30% credit from inspection items is extreme and
    # suggests the property shouldn't be selling at this price.
    concede_floor = property_price * 0.70
    if concede_anchor < concede_floor:
        concede_anchor = concede_floor

    # ── 2. Try LLM for rationales + bottom line ────────────────────────
    llm_result = _call_claude_for_seller_strategy(
        property_price=property_price,
        repair_low=repair_low,
        repair_high=repair_high,
        repair_mid=repair_mid,
        counter_anchor=counter_anchor,
        hold_anchor=hold_anchor,
        concede_anchor=concede_anchor,
        agent_commentary=agent_commentary,
        property_address=property_address,
        findings_summary=findings_summary,
    )

    if llm_result is not None:
        scenarios = _clamp_seller_scenarios(
            llm_result.get('scenarios') or [],
            property_price=property_price,
            counter_anchor=counter_anchor,
            hold_anchor=hold_anchor,
            concede_anchor=concede_anchor,
        )
        return {
            'scenarios': scenarios,
            'bottom_line': (llm_result.get('bottom_line') or '').strip(),
            'ok': True,
            'source': 'llm',
        }

    # ── 3. Deterministic fallback ──────────────────────────────────────
    logger.warning("Seller-side offer strategy: LLM unavailable, using deterministic fallback")
    # Build the raw scenarios, then run through the SAME clamp logic
    # the LLM output goes through. This ensures ordering and bounds are
    # enforced consistently regardless of source.
    raw_scenarios = [
        {
            'label': 'Counter',
            'price': int(round(counter_anchor)),
            'rationale': (
                f"Acknowledges the ${int(repair_low):,}-${int(repair_high):,} "
                f"in documented findings with a partial credit, while signaling "
                f"the list price is defensible."
            ),
        },
        {
            'label': 'Hold',
            'price': int(round(hold_anchor)),
            'rationale': (
                f"A fair-middle response to the inspection: full credit at the "
                f"mid-point of the repair range, which matches what a reasonable "
                f"buyer should expect."
            ),
        },
        {
            'label': 'Concede',
            'price': int(round(concede_anchor)),
            'rationale': (
                f"The floor — full credit at the high end of repair findings. "
                f"Below this, you should relist or wait for the next buyer."
            ),
        },
    ]
    clamped = _clamp_seller_scenarios(
        raw_scenarios,
        property_price=property_price,
        counter_anchor=counter_anchor,
        hold_anchor=hold_anchor,
        concede_anchor=concede_anchor,
    )
    return {
        'scenarios': clamped,
        'bottom_line': (
            f"With ${int(repair_low):,}-${int(repair_high):,} in documented findings "
            f"against a ${int(property_price):,} list price, I'd counter at "
            f"${clamped[0]['price']:,}. We can hold at "
            f"${clamped[1]['price']:,}. Below ${clamped[2]['price']:,} "
            f"the math says relist."
        ),
        'ok': False,
        'source': 'deterministic_fallback',
    }


def _call_claude_for_seller_strategy(
    *,
    property_price: float,
    repair_low: float,
    repair_high: float,
    repair_mid: float,
    counter_anchor: float,
    hold_anchor: float,
    concede_anchor: float,
    agent_commentary: str,
    property_address: str,
    findings_summary: str,
) -> Optional[Dict[str, Any]]:
    """Make the Claude call for seller-side strategy. Same pattern as
    `_call_claude_for_strategy` but with a seller-framed prompt.
    """
    try:
        from analysis_ai_helper import AnalysisAIHelper
        helper = AnalysisAIHelper()
        if not (helper and getattr(helper, 'client', None)):
            return None
    except Exception as e:
        logger.warning(f"Seller-side strategy: helper init failed: {e}")
        return None

    prompt = f"""You are helping a real estate seller's agent draft a negotiation-defense
strategy for their client. The deliverable will be read by the agent's seller client.
The agent's voice — direct, professional, no fluff — is what should come through.

PROPERTY: {property_address or 'subject property'}
LIST PRICE: ${int(property_price):,}

REPAIR FINDINGS (from buyer's inspection):
Total estimated cost range: ${int(repair_low):,} – ${int(repair_high):,}
Mid-point: ${int(repair_mid):,}

{findings_summary if findings_summary else ''}

AGENT'S COMMENTARY (paste from the agent):
\"\"\"
{agent_commentary}
\"\"\"

ANCHORS (computed from the math; you may adjust ±3% if commentary strongly suggests it):
- Counter: ${int(counter_anchor):,} (first counter to buyer's lowball)
- Hold: ${int(hold_anchor):,} (price you'll defend without going below)
- Concede: ${int(concede_anchor):,} (the floor — below this, relist)

TASK: Produce a JSON object with exactly two fields:

1. "scenarios": an array of exactly 3 scenarios in this order:
   - {{"label": "Counter", "price": <int>, "rationale": "<1 short sentence>"}}
   - {{"label": "Hold", "price": <int>, "rationale": "<1 short sentence>"}}
   - {{"label": "Concede", "price": <int>, "rationale": "<1 short sentence>"}}

2. "bottom_line": a 2-3 sentence summary in the AGENT'S voice. The agent's
   seller client will read this. Sound like a confident professional
   speaking, NOT like AI marketing copy. Reference specific numbers
   (repair range, counter, concede). If the commentary mentions
   leverage points (multiple offers, market temperature, days on
   market), weave one in. NO emojis, NO buzzwords.

RULES:
- Prices must be whole integers.
- Counter > Hold > Concede (counter is the highest, concede the lowest).
- Counter ≤ ${int(property_price):,} (cannot exceed list price).
- Concede ≥ ${int(property_price * 0.70):,} (defensive floor).
- Rationales: ≤25 words each, grounded in the specific numbers above.
- The bottom line is the agent talking to their seller, not OfferWise talking.

Return ONLY the JSON object. No preamble, no markdown, no commentary."""

    try:
        _t0 = time.time()
        response = helper.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            try:
                from app import app as _ow_app, db as _ow_db
            except Exception:
                _ow_app, _ow_db = None, None
            from ai_cost_tracker import track_ai_call as _track
            _track(response, "briefing-seller-strategy", (time.time() - _t0) * 1000, db=_ow_db, app=_ow_app)
        except Exception:
            pass
        raw = response.content[0].text
    except Exception as e:
        logger.warning(f"Seller-side strategy LLM call failed: {e.__class__.__name__}: {e}")
        return None

    cleaned = raw.strip()
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        cleaned = '\n'.join(lines).strip()
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start == -1 or end == -1 or end <= start:
        logger.warning(f"Seller-side strategy: no JSON in response: {cleaned[:200]!r}")
        return None
    cleaned = cleaned[start:end + 1]

    try:
        parsed = json.loads(cleaned)
    except Exception as e:
        logger.warning(f"Seller-side strategy: JSON parse failed: {e}")
        return None

    scenarios = parsed.get('scenarios')
    if not isinstance(scenarios, list) or len(scenarios) < 3:
        return None
    for s in scenarios[:3]:
        if not isinstance(s, dict):
            return None
        if 'price' not in s or 'label' not in s or 'rationale' not in s:
            return None

    return parsed


def _clamp_seller_scenarios(
    scenarios_raw,
    *,
    property_price: float,
    counter_anchor: float,
    hold_anchor: float,
    concede_anchor: float,
):
    """Clamp LLM-returned seller scenario prices.

    Counter must not exceed list price. Concede must not drop below 70%
    of list. Order is enforced (Counter > Hold > Concede — descending,
    the inverse of buyer-side).

    Edge case: when documented repair burden is so high that concede
    would naturally fall below 70% of list, concede gets floored UP to
    70%. That can put concede above hold or even counter, breaking the
    ordering. In that case, we push hold and counter up proportionally
    so all three stay in descending order. The semantic outcome is "the
    documented repairs exceed what a routine sale can absorb — these
    numbers reflect a defensive floor across the board."
    """
    out = []
    for i, s in enumerate(scenarios_raw[:3]):
        try:
            price = int(round(float(s.get('price', 0))))
        except (TypeError, ValueError):
            price = 0
        label = str(s.get('label') or ['Counter', 'Hold', 'Concede'][i])
        rationale = str(s.get('rationale') or '').strip()
        out.append({'label': label, 'price': price, 'rationale': rationale})

    # Counter ≤ list price
    if property_price and out[0]['price'] > property_price:
        out[0]['price'] = int(round(property_price))
    # Concede ≥ 70% of list
    floor = int(round(property_price * 0.70))
    if out[2]['price'] < floor:
        out[2]['price'] = floor

    # If concede got floored up past hold, push hold above it. Same for
    # counter. We use a minimum $1k gap so the three numbers are visibly
    # distinct in the rendered output.
    GAP = 1000
    if out[1]['price'] <= out[2]['price']:
        out[1]['price'] = out[2]['price'] + GAP
    if out[0]['price'] <= out[1]['price']:
        out[0]['price'] = out[1]['price'] + GAP

    # If pushing counter above hold pushed counter past list price, cap
    # counter at list price and squeeze hold/concede down to fit. In
    # this rare case (extreme repair burden on a low-priced home), the
    # ordering survives but the spread is compressed.
    if property_price and out[0]['price'] > property_price:
        out[0]['price'] = int(round(property_price))
        if out[1]['price'] >= out[0]['price']:
            out[1]['price'] = out[0]['price'] - GAP
        if out[2]['price'] >= out[1]['price']:
            out[2]['price'] = out[1]['price'] - GAP

    return out


def build_findings_summary(analysis_dict: Dict[str, Any], max_categories: int = 5) -> str:
    """Build a compact text summary of the repair findings for the LLM
    prompt. Avoids dumping the full analysis_json (too big and noisy).
    """
    if not isinstance(analysis_dict, dict):
        return ''
    risk = analysis_dict.get('risk_score') or {}
    cats = risk.get('category_scores') or []
    if not cats:
        return ''
    # Sort by high-end cost, take top N.
    cats_with_costs = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        low = c.get('estimated_cost_low') or 0
        high = c.get('estimated_cost_high') or 0
        if low <= 0 and high <= 0:
            continue
        cat = c.get('category')
        if isinstance(cat, dict):
            cat = cat.get('value', '')
        cats_with_costs.append((str(cat or 'other'), low, high))
    cats_with_costs.sort(key=lambda x: -x[2])

    lines = ['Top repair categories by cost:']
    for cat, low, high in cats_with_costs[:max_categories]:
        lines.append(f"  - {cat.replace('_', ' ')}: ${int(low):,} – ${int(high):,}")
    return '\n'.join(lines)
