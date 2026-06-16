"""prospect_research_service.py — v5.87.48 unified discovery + research + drafting.

Orchestrates the full pipeline for adding a B2B prospect:

  1. DISCOVERY  — find email addresses for people at a company
                  Hunter (primary) → Apollo (fallback if Hunter empty/exhausted)
  2. RESEARCH   — pull current focus areas for the company via Anthropic
                  web-search tool. Synthesizes 2-4 sentences on what they
                  shipped/announced/wrote about in the last 60 days.
  3. DRAFT      — generate a personalized first-touch cold email using
                  Claude, conditioned on the prospect's role + the focus
                  areas. Saved as `draft_subject` + `draft_body` on the
                  OutreachContact row, NOT auto-sent.

The founder reviews each draft in the admin UI before sending. The
existing bulk-send path is unchanged — drafts just become the body of
that send when fired.

Design notes:
  - All three steps are independent. If web search fails, drafting still
    happens with a generic template (and the failure is logged on the
    contact). If discovery fails, the founder can paste an email manually.
  - Per-prospect cost: ~1 Hunter credit OR ~1 Apollo credit (discovery)
    + ~$0.01-0.03 of Anthropic web search + ~$0.01 of Claude drafting.
  - This service does not write to the DB. Callers must persist the
    OutreachContact rows. This keeps the service pure-function-shaped
    and easy to test.
"""

from __future__ import annotations
from model_config import SONNET
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Discovery ────────────────────────────────────────────────────────

def discover_prospects(
    company_domain: str,
    company_name: str = '',
    titles: Optional[list[str]] = None,
    seniorities: Optional[list[str]] = None,
    limit: int = 10,
    seniority_strict: Optional[bool] = None,
) -> dict[str, Any]:
    """Find prospects at a company via Hunter (primary) or Apollo (fallback).

    Strategy:
      1. Try Hunter Domain Search filtered by seniority. Cheap (1 credit) and
         we already know how to use it.
      2. If Hunter returns nothing OR the credit floor is reached, try Apollo
         people_search with explicit title list.
      3. If both fail, return empty + list of errors.

    Args:
      company_domain: e.g. 'renofi.com'
      company_name: human-readable, used for logging only
      titles: title fragments preferred for the prospect (e.g.
              ['Chief Underwriting Officer', 'VP Risk',
               'Head of Underwriting Innovation'])
      seniorities: Apollo seniority enums (e.g. ['c_suite', 'vp', 'director'])
                   For Hunter, mapped to its seniority filter ('senior,executive')
      limit: max prospects to return

    Returns:
      {
        'prospects': [{email, name, first_name, last_name, title, ...}],
        'source': 'hunter' | 'apollo' | 'none',
        'errors': [str, ...],   # any non-fatal errors encountered
      }
    """
    errors: list[str] = []

    # ── Try Hunter first ──
    try:
        from hunter_service import domain_search as hunter_search
        # Map Apollo seniority enums to Hunter's comma-separated string.
        hunter_seniority = ''
        if seniorities:
            mapping = {
                'c_suite': 'executive,c_level',
                'vp': 'senior,executive',
                'director': 'senior',
                'head': 'senior',
                'manager': 'junior',
                'owner': 'executive,c_level',
                'partner': 'executive,c_level',
            }
            mapped = []
            for s in seniorities:
                if s in mapping:
                    mapped.extend(mapping[s].split(','))
            hunter_seniority = ','.join(sorted(set(mapped))) if mapped else 'senior,executive'

        h = hunter_search(
            domain=company_domain,
            limit=limit,
            seniority=hunter_seniority,
        )

        if h.get('error') and h.get('credit_exhausted'):
            errors.append(f"Hunter: credit floor reached ({h.get('error')})")
        elif h.get('error'):
            errors.append(f"Hunter: {h.get('error')}")
        elif h.get('emails'):
            # Optionally filter by title match — Hunter returns position
            # but we want to honor the caller's title preference.
            prospects = h['emails']
            if titles:
                title_lc = [t.lower() for t in titles]
                filtered = [p for p in prospects
                            if any(t in (p.get('position') or '').lower()
                                   for t in title_lc)]
                # If title filter eliminated everything, fall back to the
                # full set rather than returning empty
                if filtered:
                    prospects = filtered

            # v5.87.74: Band B seniority filter — Hunter's seniority tags
            # let too many sales ICs and middle-managers through. This is
            # a strict post-filter on the title text. Toggle off via
            # seniority_strict=False param or APPLY_SENIORITY_FILTER env.
            try:
                from seniority_filter import filter_prospects as _band_b_filter
                prospects, _stats = _band_b_filter(
                    prospects, apply_filter=seniority_strict, title_key='position'
                )
                if _stats.get('total_rejected', 0) > 0:
                    errors.append(
                        f"Band B filter: kept {_stats['total_kept']}/{_stats['total_in']} "
                        f"(reasons: {_stats['reject_reasons']})"
                    )
            except ImportError:
                pass  # filter optional; if module missing don't break discovery

            return {
                'prospects': [_normalize_hunter(p) for p in prospects],
                'source': 'hunter',
                'errors': errors,
            }
    except ImportError:
        errors.append('hunter_service not importable')
    except Exception as e:
        errors.append(f'Hunter exception: {e.__class__.__name__}: {e}')

    # ── Fall back to Apollo ──
    # v5.87.55: when Apollo isn't configured (the common case for users
    # without a paid Apollo plan), silently skip it rather than adding a
    # noisy "Apollo: APOLLO_API_KEY not set" line to the user-visible
    # error list. Errors should only show real problems, not expected
    # default states.
    try:
        from apollo_service import people_search as apollo_search, _is_configured as apollo_configured
        if apollo_configured():
            a = apollo_search(
                company_domain=company_domain,
                titles=titles or None,
                seniorities=seniorities or None,
                limit=limit,
            )
            if a.get('error'):
                errors.append(f"Apollo: {a.get('error')}")
            elif a.get('people'):
                normalized = [_normalize_apollo(p) for p in a['people']]
                # v5.87.74: Band B post-filter (same as Hunter path)
                try:
                    from seniority_filter import filter_prospects as _band_b_filter
                    normalized, _stats = _band_b_filter(
                        normalized, apply_filter=seniority_strict, title_key='title'
                    )
                    if _stats.get('total_rejected', 0) > 0:
                        errors.append(
                            f"Band B filter (Apollo): kept {_stats['total_kept']}/{_stats['total_in']}"
                        )
                except ImportError:
                    pass
                return {
                    'prospects': normalized,
                    'source': 'apollo',
                    'errors': errors,
                }
        # else: Apollo not configured → silently skip, fall through to Snov
    except ImportError:
        errors.append('apollo_service not importable')
    except Exception as e:
        errors.append(f'Apollo exception: {e.__class__.__name__}: {e}')

    # ── Last resort: Snov.io tertiary fallback ──
    # Reaches Snov only when Hunter and Apollo both came up empty. Useful
    # for niche companies (smaller renovation lenders, regional insurtechs)
    # where Snov's database has coverage the other two miss.
    try:
        from snov_service import domain_search as snov_search, _is_configured as snov_configured
        if snov_configured():
            s = snov_search(domain=company_domain, limit=limit)
            if s.get('error'):
                errors.append(f"Snov: {s.get('error')}")
            elif s.get('emails'):
                # Apply the same title-filter logic Hunter uses
                prospects = s['emails']
                if titles:
                    title_lc = [t.lower() for t in titles]
                    filtered = [p for p in prospects
                                if any(t in (p.get('position') or '').lower()
                                       for t in title_lc)]
                    if filtered:
                        prospects = filtered
                # v5.87.74: Band B post-filter (same as Hunter path)
                try:
                    from seniority_filter import filter_prospects as _band_b_filter
                    prospects, _stats = _band_b_filter(
                        prospects, apply_filter=seniority_strict, title_key='position'
                    )
                    if _stats.get('total_rejected', 0) > 0:
                        errors.append(
                            f"Band B filter (Snov): kept {_stats['total_kept']}/{_stats['total_in']}"
                        )
                except ImportError:
                    pass
                return {
                    'prospects': [_normalize_hunter(p) for p in prospects],
                    'source': 'snov',
                    'errors': errors,
                }
            else:
                # v5.87.58: Snov succeeded but returned zero prospects.
                # Surface this so the user knows Snov was actually tried —
                # otherwise the only visible error is Hunter's credit floor,
                # which is misleading because it suggests no fallback ran.
                errors.append(f'Snov: no emails found at {company_domain}')
        else:
            # Apollo-style suppression — when Snov isn't configured, just
            # silently skip rather than adding to user-visible errors
            pass
    except ImportError:
        errors.append('snov_service not importable')
    except Exception as e:
        errors.append(f'Snov exception: {e.__class__.__name__}: {e}')

    return {'prospects': [], 'source': 'none', 'errors': errors}


def _normalize_hunter(h: dict) -> dict:
    """Coerce Hunter's email shape into the canonical prospect dict."""
    return {
        'email': h.get('email') or '',
        'first_name': h.get('first_name') or '',
        'last_name': h.get('last_name') or '',
        'name': ' '.join(p for p in
                         [h.get('first_name'), h.get('last_name')]
                         if p).strip(),
        'title': h.get('position') or '',
        'seniority': h.get('seniority') or '',
        'linkedin_url': '',  # Hunter doesn't return LinkedIn directly
        'confidence': h.get('confidence', 0),
        'source': 'hunter',
    }


def _normalize_apollo(a: dict) -> dict:
    """Coerce Apollo's people shape into the canonical prospect dict."""
    return {
        'email': a.get('email') or '',
        'first_name': a.get('first_name') or '',
        'last_name': a.get('last_name') or '',
        'name': a.get('name') or '',
        'title': a.get('title') or '',
        'seniority': a.get('seniority') or '',
        'linkedin_url': a.get('linkedin_url') or '',
        'confidence': a.get('confidence', 0),
        'source': 'apollo',
    }


# ─── Research (Anthropic web search) ──────────────────────────────────

# Cost-control cap: we don't want a runaway research loop burning credits
# if the founder accidentally pastes 100 prospects. Each contact costs
# roughly $0.01-0.03 of Anthropic web search.
MAX_RESEARCH_CALLS_PER_BATCH = int(os.environ.get('MAX_RESEARCH_PER_BATCH', '15'))


def research_company_focus(company_name: str, company_domain: str = '') -> dict[str, Any]:
    """Use Anthropic's web_search tool to synthesize a company's current
    focus areas — what they've shipped, announced, hired, or written
    about in the last ~60 days.

    Returns:
      {
        'focus_areas': str,    # 2-4 sentences, plain text, ready to feed
                                #   into the email-drafting prompt
        'sources_count': int,  # how many distinct URLs Claude cited
        'error': str | None,
      }

    On error (key missing, search failed, rate-limited), returns
    focus_areas='' and an error string. Caller should fall back to a
    generic email template that doesn't reference focus areas.
    """
    if not company_name:
        return {'focus_areas': '', 'sources_count': 0, 'error': 'company_name required'}

    anthropic_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not anthropic_key:
        return {'focus_areas': '', 'sources_count': 0,
                'error': 'ANTHROPIC_API_KEY not set'}

    try:
        import anthropic
    except ImportError:
        return {'focus_areas': '', 'sources_count': 0,
                'error': 'anthropic SDK not installed'}

    domain_hint = f' (domain: {company_domain})' if company_domain else ''
    prompt = f"""You are researching the company {company_name}{domain_hint} for a \
cold-outreach email. Use web search to find what this company has been focused on \
in the last 60 days. Look specifically for:

  - Recent product launches, partnerships, or acquisitions
  - Funding rounds or material business announcements
  - Public statements (blog posts, podcast appearances, conference panels) \
from their executives
  - Earnings or quarterly business updates if public

Synthesize what you find into 2-4 sentences of plain prose describing the company's \
CURRENT focus areas. Write it in the way a careful analyst would brief a colleague \
before a meeting — concrete, specific, naming actual products/people/events where \
possible. Do not include preamble, citations, or commentary on your search process. \
Do not speculate beyond what the search results support. If search returns nothing \
recent and substantive, write 'No recent public signal found' — better to say nothing \
than to invent.

Output ONLY the 2-4 sentences. Nothing else."""

    try:
        client = anthropic.Anthropic(api_key=anthropic_key)
        # Anthropic's web_search server tool — cheap, runs on Anthropic's
        # infra, returns synthesized text. The model decides whether to
        # search and how many queries to run.
        resp = client.messages.create(
            model=SONNET,
            max_tokens=600,
            tools=[{
                'type': 'web_search_20250305',
                'name': 'web_search',
                # Cap searches per request to keep cost predictable
                'max_uses': 3,
            }],
            messages=[{'role': 'user', 'content': prompt}],
        )

        # Extract text from the response. With server-tool web_search,
        # the response.content contains a mix of tool_use, tool_result,
        # and text blocks. We want the final text block(s).
        text_parts = []
        sources = set()
        for block in resp.content:
            btype = getattr(block, 'type', '')
            if btype == 'text':
                text_parts.append(getattr(block, 'text', '') or '')
            elif btype == 'web_search_tool_result':
                # Each result block has a list of citations
                content = getattr(block, 'content', []) or []
                for item in content:
                    url = getattr(item, 'url', None) or (item.get('url') if isinstance(item, dict) else None)
                    if url:
                        sources.add(url)

        focus_areas = '\n'.join(p.strip() for p in text_parts if p.strip()).strip()

        return {
            'focus_areas': focus_areas,
            'sources_count': len(sources),
            'error': None,
        }

    except Exception as e:
        logger.warning('research_company_focus(%s) failed: %s',
                       company_name, e.__class__.__name__)
        return {'focus_areas': '', 'sources_count': 0,
                'error': f'{e.__class__.__name__}: {e}'}


# ─── Drafting (Claude email generation) ───────────────────────────────

# Loaded from a constant rather than a config file so the founder can read
# and tweak it directly. The shape was chosen to mirror the proven Roc360
# email shape: opening hook tied to their world, sharp diagnosis, concrete
# proof, well-framed ask.
DRAFT_PROMPT_TEMPLATE = """You are helping Francis Anthony, founder of OfferWise, \
write a cold outreach email to a prospect. OfferWise is an AI tool that reads \
property seller disclosures and inspection reports the way a careful buyer's \
agent would, prices each repair against ZIP-level cost data, and surfaces \
contradictions between what sellers claim and what inspections find.

OfferWise has:
  - 170,000+ labeled inspection findings
  - 111,000+ regionally-calibrated repair cost records
  - Won 5 of 7 criteria in a head-to-head benchmark vs GPT-5 and Claude Opus
  - Live in production at getofferwise.ai

The prospect:
  - Name: {name}
  - Title: {title}
  - Company: {company}
  - Wedge angle: {wedge_pain}

The company's current focus areas (from web research, may be empty):
{focus_areas}

YOUR TASK: Write a cold email of 4-6 short paragraphs (under 200 words total) in \
the proven Francis Anthony shape:

  1. Opening sentence that ties to something specific the company is doing right \
now (use the focus areas above; if empty, fall back to the wedge angle).
  2. Sharp diagnosis: name the specific problem THIS person/role would care about, \
in their vocabulary.
  3. Concrete proof: cite specific OfferWise numbers (170K labeled findings, \
111K cost records, 5/7 head-to-head) and link relevant ones.
  4. Well-framed ask: not "want to chat?" but reframe to "the right \
conversation isn't X, it's whether Y" pattern. Suggest 20 minutes.

Voice: peer-to-peer, not vendor-to-prospect. Direct, specific, no marketing \
language. The recipient should feel respected by the diligence shown.

LINK INCLUSION (mandatory): Include this URL naturally somewhere in the body, \
preferably in the proof paragraph or as a P.S. line: {landing_url}
The URL takes the prospect to a page written specifically for their persona. \
Do not introduce it with marketing language. A natural phrasing is: \
"There's a one-pager for {company_role} folks at {landing_url}" or \
"P.S. Page written for {company_role}: {landing_url}". Pick whichever flows \
better with your draft, but DO include the URL exactly as given.

STYLE RULE (mandatory): Do NOT use em-dashes (—) or en-dashes (–) anywhere \
in the subject or body. Rewrite with commas, periods, or parentheses instead. \
Hyphens in compound words like "well-suited" or "follow-up" are fine. This \
rule applies to the entire output and to any examples you might generate.

Output ONLY the email body. Do NOT include 'Subject:' line, salutation \
formatting, or signature. Those are added separately. The body will be \
prepended with 'Greetings <FirstName>,' before send, so start your output \
with the FIRST SENTENCE of the email content (NOT a salutation).

Also generate a subject line that would make this prospect's eyes go to it \
first in their inbox. The subject should be 4-9 words, specific to their \
company or world, and never include 'OfferWise' or 'AI' or 'cold outreach'.

Return your output as a JSON object exactly in this shape:

{{"subject": "<subject line>", "body": "<email body>"}}

Output ONLY the JSON object, nothing else.
"""


WEDGE_PAIN_LOOKUP = {
    'renovation_lenders':
        'repair-cost risk on the properties you finance',
    'insurtechs':
        'hidden risk in disclosure documents during underwriting',
    'brokerage_tech':
        'agent-side tools that surface findings buyers actually care about',
    'title_closing':
        'contradiction-detection in the closing-doc package',
    'buyer_fintech':
        'helping first-time buyers avoid the financially worst houses',
    'ibuyer':
        'condition risk in your acquisition pipeline',
    'other': 'this space',
    '': 'this space',
}


# v5.87.98: Map each wedge to the persona-specific landing page URL +
# a human-readable role name used in the prompt's link-inclusion sentence.
# When sending B2B outreach, the prospect deserves to land on a page
# written for their specific persona, not the generic homepage.
#
# Mapping rationale:
#   renovation_lenders → /for-lenders (collateral condition intelligence)
#   insurtechs         → /for-insurance (pre-bind risk profiles)
#   brokerage_tech     → /for-agents (branded buyer analysis)
#   title_closing      → /for-title-companies (closing-doc analysis)
#   buyer_fintech      → /enterprise (consumer fintech is treated as API
#                       partnership rather than a /for-* page; closest
#                       match in current site map)
#   ibuyer             → /enterprise (acquisition-pipeline integrations
#                       are sales-led, same as buyer_fintech)
#   other / ''         → /personas (the persona map page; lets the prospect
#                       self-identify which surface they belong to)
WEDGE_URL_LOOKUP = {
    'renovation_lenders': (
        'https://www.getofferwise.ai/for-lenders',
        'lender / underwriter',
    ),
    'insurtechs': (
        'https://www.getofferwise.ai/for-insurance',
        'underwriter',
    ),
    'brokerage_tech': (
        'https://www.getofferwise.ai/for-agents',
        'brokerage / agent-tech',
    ),
    'title_closing': (
        'https://www.getofferwise.ai/for-title-companies',
        'title / closing',
    ),
    'buyer_fintech': (
        'https://www.getofferwise.ai/enterprise',
        'consumer-fintech',
    ),
    'ibuyer': (
        'https://www.getofferwise.ai/enterprise',
        'acquisitions / iBuyer',
    ),
    'other': (
        'https://www.getofferwise.ai/personas',
        'partner',
    ),
    '': (
        'https://www.getofferwise.ai/personas',
        'partner',
    ),
}


def get_landing_url_for_wedge(wedge: str) -> tuple[str, str]:
    """Return (url, company_role_label) for a given wedge.

    Falls back to (/personas, 'partner') for unknown wedges. The role
    label is used in the LLM prompt to phrase the link inclusion
    naturally ('one-pager for lender / underwriter folks at ...').
    """
    return WEDGE_URL_LOOKUP.get(wedge or '', WEDGE_URL_LOOKUP[''])


def _extract_first_name(name: str) -> str:
    """Return a first name suitable for a "Greetings <Name>," opener.

    Handles real-world prospect data:
      'Jane Doe'         → 'Jane'
      'Smith, John'      → 'John'   (LinkedIn export format)
      'Sridhar'          → 'Sridhar'
      'jane'             → 'Jane'   (title-case the lowercase)
      'JANE DOE'         → 'Jane'   (title-case the all-caps)
      'Mr. John Smith'   → 'John'   (skip honorifics)
      'Jane M. Doe'      → 'Jane'
      ''  or None        → ''       (caller decides fallback)
    """
    if not name:
        return ''
    cleaned = name.strip()
    if not cleaned:
        return ''

    # LinkedIn-export format: "Smith, John" → "John"
    if ',' in cleaned:
        # The part AFTER the comma is the first name
        after_comma = cleaned.split(',', 1)[1].strip()
        if after_comma:
            cleaned = after_comma

    # Skip honorifics (Mr./Mrs./Ms./Dr.) at the start
    parts = cleaned.split()
    while parts and parts[0].rstrip('.').lower() in {
        'mr', 'mrs', 'ms', 'mx', 'dr', 'prof'
    }:
        parts.pop(0)
    if not parts:
        return ''

    first = parts[0]
    # Title-case if the input was all upper or all lower (preserve mixed-
    # case names like "McKinsey" or "DeAndre" that the prospect entered
    # deliberately).
    if first.isupper() or first.islower():
        first = first.capitalize()
    return first


def draft_email(
    name: str,
    title: str,
    company: str,
    wedge: str = '',
    focus_areas: str = '',
) -> dict[str, Any]:
    """Generate a personalized cold-email draft via Claude.

    Returns:
      {
        'subject': str,
        'body': str,
        'error': str | None,
      }

    On error, subject and body fall back to a static template using the
    wedge-pain phrase, so the founder always has something to start from.
    """
    wedge_pain = WEDGE_PAIN_LOOKUP.get(wedge or '', WEDGE_PAIN_LOOKUP[''])
    landing_url, company_role = get_landing_url_for_wedge(wedge)

    prompt = DRAFT_PROMPT_TEMPLATE.format(
        name=name or '(unknown)',
        title=title or '(unknown role)',
        company=company or '(unknown company)',
        wedge_pain=wedge_pain,
        focus_areas=focus_areas or '(no recent public signal found)',
        landing_url=landing_url,
        company_role=company_role,
    )

    try:
        from ai_client import get_ai_response
        raw = get_ai_response(prompt, max_tokens=900, temperature=0.4)
    except Exception as e:
        logger.warning('draft_email Claude call failed: %s', e.__class__.__name__)
        return _fallback_draft(name, company, wedge_pain, landing_url, company_role,
                               error=f'{e.__class__.__name__}: {e}')

    # Parse JSON. Claude sometimes wraps in markdown fences; strip them.
    cleaned = raw.strip()
    if cleaned.startswith('```'):
        # Strip ```json ... ``` wrapper if present
        lines = cleaned.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        cleaned = '\n'.join(lines).strip()

    try:
        parsed = json.loads(cleaned)
        subject = (parsed.get('subject') or '').strip()
        body = (parsed.get('body') or '').strip()
        if not subject or not body:
            return _fallback_draft(name, company, wedge_pain, landing_url, company_role,
                                   error='Claude returned empty subject or body')
        # v5.87.78: defensive em-dash strip. Founder doesn't want em-dashes
        # in any outreach drafts. The system prompt already forbids them, but
        # if Claude slips, replace em-dash and en-dash with a comma + space.
        # Hyphens (compound words) are preserved.
        subject = subject.replace('—', ', ').replace('–', ', ')
        body = body.replace('—', ', ').replace('–', ', ')

        # v5.88.03: append "-Francis" signoff. Goes BEFORE the URL P.S.
        # append below so the email reads in correct order:
        #   greeting → body → signoff → P.S.
        # which matches email convention (P.S. follows the signature, not
        # the other way around). Defensive check: if the LLM already wrote
        # a Francis signoff (it shouldn't, the prompt forbids signatures,
        # but LLMs slip), don't double-add it.
        body_lower = body.lower().rstrip()
        already_signed = (
            body_lower.endswith('-francis')
            or body_lower.endswith('— francis')
            or body_lower.endswith('francis anthony')
            or body_lower.endswith(', francis')
            or body_lower.endswith('best, francis')
            or body_lower.endswith('thanks, francis')
        )
        if not already_signed:
            body = body.rstrip() + '\n\n-Francis'

        # v5.87.98: defensive URL append. The prompt instructs the LLM to
        # include the landing_url in the body, but LLM output is non-
        # deterministic. If the URL didn't make it into the body, append
        # it as a P.S. line so the link inclusion is guaranteed for the
        # B2B outreach use case.
        if landing_url and landing_url not in body:
            body = body.rstrip() + (
                f'\n\nP.S. Wrote a one-pager for {company_role} folks: {landing_url}'
            )
            logger.info(
                'draft_email: appended missing landing_url for wedge=%s', wedge
            )

        # v5.88.02: prepend "Greetings <FirstName>," opener. The prompt
        # already instructs the LLM not to include a salutation (because
        # we add it here), so prepending is the right place. If we have
        # no name, fall back to a generic "Greetings," — better than
        # "Greetings ," with stray space-comma.
        first_name = _extract_first_name(name)
        if first_name:
            body = f'Greetings {first_name},\n\n' + body
        else:
            body = 'Greetings,\n\n' + body

        return {'subject': subject, 'body': body, 'error': None}
    except json.JSONDecodeError as e:
        logger.warning('draft_email JSON parse failed: %s. Raw[:200]: %r',
                       e, raw[:200])
        return _fallback_draft(name, company, wedge_pain, landing_url, company_role,
                               error=f'JSON parse failed: {e}')


def _fallback_draft(name: str, company: str, wedge_pain: str,
                    landing_url: str = '',
                    company_role: str = 'partner',
                    error: str = '') -> dict[str, Any]:
    """Static draft used when Claude fails. Better than blank, gives the
    founder something to edit rather than starting from scratch."""
    # v5.88.02: use the proper first-name helper instead of the old naive
    # split(' ')[0]. Handles 'Smith, John' and similar correctly.
    first_name = _extract_first_name(name)
    subject = f'Quick question on {company} + AI disclosure analysis' if company \
              else 'Quick question on AI disclosure analysis'

    # v5.87.98: Use the persona-specific landing URL when provided, falling
    # back to the architecture/comparison pages for the generic case.
    if landing_url:
        link_line = (
            f'I wrote a one-pager for {company_role} folks: {landing_url}. '
            f'Architecture and a head-to-head benchmark are at '
            f'https://www.getofferwise.ai/architecture and '
            f'https://www.getofferwise.ai/comparison.'
        )
    else:
        link_line = (
            'Architecture and a head-to-head benchmark are at '
            'https://www.getofferwise.ai/architecture and '
            'https://www.getofferwise.ai/comparison.'
        )

    # v5.88.02: prepend greeting so fallback drafts match the LLM-path shape
    greeting = f'Greetings {first_name},' if first_name else 'Greetings,'

    # v5.88.03: include "-Francis" signoff so fallback drafts also have it
    body = f"""{greeting}

I'm building OfferWise, an AI tool that reads property seller \
disclosures and inspection reports the way a careful buyer's agent would, priced \
against ZIP-level cost data. We have 170K labeled inspection findings, 111K \
regionally-calibrated cost records, and won 5 of 7 criteria in a head-to-head \
against GPT-5 and Claude Opus.

I'm reaching out specifically because of {wedge_pain}, that's the overlap I'd \
love to compare notes on.

Worth 20 minutes this week or next? No pitch. I'm trying to understand how teams \
like yours think about this problem and whether OfferWise is in your strike zone.

{link_line}

-Francis"""
    return {'subject': subject, 'body': body, 'error': error or 'fallback used'}


# ─── Combined entry point ─────────────────────────────────────────────

def research_and_draft(
    name: str,
    email: str,
    title: str,
    company: str,
    company_domain: str = '',
    wedge: str = '',
    skip_research: bool = False,
) -> dict[str, Any]:
    """The full pipeline for a single prospect: research focus areas,
    then draft a personalized email.

    skip_research=True bypasses the web-search step (useful when the
    founder pastes prospects who all share a known company they've
    already researched manually).

    Returns:
      {
        'subject': str,
        'body': str,
        'focus_areas': str,
        'sources_count': int,
        'errors': [str, ...],
      }
    """
    errors: list[str] = []
    focus_areas = ''
    sources_count = 0

    if not skip_research and company:
        r = research_company_focus(company, company_domain)
        focus_areas = r.get('focus_areas', '')
        sources_count = r.get('sources_count', 0)
        if r.get('error'):
            errors.append(f'research: {r["error"]}')

    d = draft_email(
        name=name,
        title=title,
        company=company,
        wedge=wedge,
        focus_areas=focus_areas,
    )
    if d.get('error'):
        errors.append(f'draft: {d["error"]}')

    return {
        'subject': d.get('subject', ''),
        'body': d.get('body', ''),
        'focus_areas': focus_areas,
        'sources_count': sources_count,
        'errors': errors,
    }
