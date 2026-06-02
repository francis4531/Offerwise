"""apollo_service.py — Apollo.io API client (v5.87.48).

Apollo's database is materially larger than Hunter's (~270M contacts vs ~107M)
and has stronger title/seniority filtering inside specific organizations. The
free tier is UI-only — API access requires a paid plan ($49+/mo). Until the
key is set in the env, this module silently disables itself the same way
hunter_service does.

Scope (deliberately narrow, mirrors what we need to plug into the unified
discovery pipeline):

  - account_info()        — query plan/credits, free, cached 5 min
  - people_search(...)    — find people at a company by title/seniority

We intentionally do NOT wire:
  - Apollo's enrichment endpoints (we have OutreachContact + per-prospect
    web search already)
  - Sequencing/email-sending (we use Resend through OfferWise's own bulk-send)
  - CRM sync (out of scope)

If/when a paid Apollo plan is active, the discovery service uses Apollo as
fallback when Hunter returns nothing or hits its credit floor.

Env vars:
  APOLLO_API_KEY                — required for any call; absent ⇒ disabled
  APOLLO_CREDIT_FLOOR (default 5)
"""

from __future__ import annotations
import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

APOLLO_API_KEY = os.environ.get('APOLLO_API_KEY', '').strip()
APOLLO_CREDIT_FLOOR = int(os.environ.get('APOLLO_CREDIT_FLOOR', '5'))

# Apollo v1 API base.
_BASE = 'https://api.apollo.io/v1'
_TIMEOUT = 20

# Account-info cache (free; cache to avoid hammering on every UI render)
_account_cache: dict[str, Any] = {'fetched_at': 0, 'data': None}
_ACCOUNT_TTL_SECONDS = 300


def _is_configured() -> bool:
    return bool(APOLLO_API_KEY)


def _safe_log_error(prefix: str, exc: Exception) -> None:
    """Log without ever leaking the API key."""
    msg = str(exc)
    if APOLLO_API_KEY and APOLLO_API_KEY in msg:
        msg = msg.replace(APOLLO_API_KEY, '[redacted]')
    logger.warning('%s: %s', prefix, msg)


def _headers() -> dict[str, str]:
    """Standard Apollo headers. The api_key goes in the header, NOT the URL,
    which keeps it out of access logs."""
    return {
        'Cache-Control': 'no-cache',
        'Content-Type': 'application/json',
        'X-Api-Key': APOLLO_API_KEY,
    }


def account_info(force_refresh: bool = False) -> dict[str, Any]:
    """Return Apollo account state for the credit badge.

    Apollo doesn't have a perfect equivalent to Hunter's /account endpoint,
    but the /auth/health endpoint validates the key and returns plan info.
    Returns:
      {
        'configured': bool,
        'plan_name': str | '',
        'credits_left': int | None,   # may be None if Apollo doesn't expose it
        'error': str | None,
      }
    """
    if not _is_configured():
        return {
            'configured': False,
            'error': 'APOLLO_API_KEY not set in environment',
        }

    now = time.time()
    if (not force_refresh
            and _account_cache['data'] is not None
            and now - _account_cache['fetched_at'] < _ACCOUNT_TTL_SECONDS):
        return _account_cache['data']

    try:
        # The /auth/health endpoint is documented as the lightest way to
        # validate a key. It doesn't return credits, but it confirms the
        # key is active.
        resp = requests.get(f'{_BASE}/auth/health', headers=_headers(), timeout=_TIMEOUT)
        if resp.status_code == 401:
            out = {'configured': True, 'error': 'invalid APOLLO_API_KEY'}
        elif resp.status_code != 200:
            out = {'configured': True, 'error': f'Apollo HTTP {resp.status_code}'}
        else:
            body = resp.json() if resp.content else {}
            out = {
                'configured': True,
                'plan_name': body.get('plan_name') or 'unknown',
                # Apollo doesn't expose remaining credits via auth/health on
                # most plans; we surface None and let the caller handle it.
                'credits_left': None,
                'error': None,
            }
        _account_cache['data'] = out
        _account_cache['fetched_at'] = now
        return out
    except requests.exceptions.RequestException as e:
        _safe_log_error('apollo.account_info', e)
        return {'configured': True, 'error': f'network: {e.__class__.__name__}'}
    except Exception as e:
        _safe_log_error('apollo.account_info', e)
        return {'configured': True, 'error': 'unexpected error'}


def people_search(
    company_domain: str,
    titles: Optional[list[str]] = None,
    seniorities: Optional[list[str]] = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Find people at a company filtered by title and seniority.

    Apollo's people-search is the right primitive for "find the Chief
    Underwriting Officer at Renofi" — it scopes to the company domain and
    filters by role, returning higher-quality matches than a Hunter pure
    domain dump.

    Args:
      company_domain: e.g. 'renofi.com'
      titles: list of title fragments to match, e.g.
              ['Chief Underwriting Officer', 'VP Risk', 'Head of Underwriting']
      seniorities: list from Apollo's enum:
              ['c_suite', 'vp', 'director', 'manager', 'owner', 'partner', ...]
      limit: max results (Apollo caps individual queries at 100)

    Returns:
      {
        'company_domain': str,
        'people': [
          {'email', 'name', 'first_name', 'last_name',
           'title', 'seniority', 'linkedin_url', 'confidence'},
        ],
        'total_returned': int,
        'error': str | None,
      }

    Note: Apollo's people-search endpoint may return contacts WITHOUT verified
    email addresses (the email field is empty). The unified discovery service
    upstream of this should fall back to Hunter Email-Finder or skip those
    prospects.
    """
    if not _is_configured():
        return {
            'company_domain': company_domain,
            'people': [],
            'error': 'APOLLO_API_KEY not configured',
        }

    company_domain = (company_domain or '').strip().lower()
    if not company_domain:
        return {'company_domain': '', 'people': [], 'error': 'company_domain is required'}

    payload: dict[str, Any] = {
        'q_organization_domains': company_domain,
        'page': 1,
        'per_page': max(1, min(100, int(limit))),
    }
    if titles:
        # Apollo accepts an array under person_titles
        payload['person_titles'] = list(titles)
    if seniorities:
        payload['person_seniorities'] = list(seniorities)

    try:
        resp = requests.post(
            f'{_BASE}/mixed_people/search',
            headers=_headers(),
            json=payload,
            timeout=_TIMEOUT,
        )
        if resp.status_code == 401:
            return {'company_domain': company_domain, 'people': [], 'error': 'invalid APOLLO_API_KEY'}
        if resp.status_code == 429:
            return {'company_domain': company_domain, 'people': [], 'error': 'Apollo rate-limited (429)'}
        if resp.status_code == 402:
            # Apollo returns 402 when the plan doesn't include this endpoint
            return {'company_domain': company_domain, 'people': [],
                    'error': 'Apollo plan does not include people search (paid plan required)'}
        if resp.status_code != 200:
            return {'company_domain': company_domain, 'people': [],
                    'error': f'Apollo HTTP {resp.status_code}'}

        body = resp.json() or {}
        raw_people = body.get('people', []) or []

        people = []
        for p in raw_people:
            email = (p.get('email') or '').strip()
            # Skip records without emails — Apollo returns these when the
            # plan doesn't include "verified email" credits, and we'd rather
            # the upstream service fall back to Hunter than send blank.
            if not email or email == 'email_not_unlocked@domain.com':
                continue
            people.append({
                'email': email.lower(),
                'name': p.get('name') or '',
                'first_name': p.get('first_name') or '',
                'last_name': p.get('last_name') or '',
                'title': p.get('title') or '',
                'seniority': p.get('seniority') or '',
                'linkedin_url': p.get('linkedin_url') or '',
                # Apollo doesn't return a Hunter-style confidence score;
                # we fill 0 to keep the schema parallel for downstream code.
                'confidence': 0,
            })

        # Bust the account-info cache so the credit badge refreshes
        _account_cache['fetched_at'] = 0

        return {
            'company_domain': company_domain,
            'people': people,
            'total_returned': len(people),
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        _safe_log_error(f'apollo.people_search({company_domain})', e)
        return {'company_domain': company_domain, 'people': [],
                'error': f'network: {e.__class__.__name__}'}
    except Exception as e:
        _safe_log_error(f'apollo.people_search({company_domain})', e)
        return {'company_domain': company_domain, 'people': [],
                'error': 'unexpected error'}
