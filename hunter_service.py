"""hunter_service.py — Hunter.io API client (v5.87.46).

Scope is deliberately narrow for free-tier safety:
  - domain_search(domain)   — find prospects at a company (1 credit per call,
                              returns up to 100 emails)
  - verify_email(email)     — pre-send gate to reduce bounces (1 credit each)
  - account_info()          — query remaining credits before each call

Endpoints we DELIBERATELY do NOT wire (would consume credits without
proportionate value at free-tier volume):
  - Email Finder            — 1 credit returns 1 email; LinkedIn lookup +
                              guess pattern is usually free and good enough
  - Person Enrichment       — 1 credit per email; rarely needed before the
                              first conversation
  - Company Enrichment      — 1 credit per domain; we already have company
                              metadata from the keyword classifier
  - Combined Enrichment     — superset of the above; same reasoning

If a future paid tier makes sense, add those endpoints in a v5.87.4x release.

Credit floor: configurable via HUNTER_CREDIT_FLOOR env var, defaults to 5.
When account remaining credits ≤ floor, the service refuses new calls and
returns a structured "credit_exhausted" error so the UI can show a banner.

API key: HUNTER_API_KEY env var. NEVER returned to the frontend in any
response shape. NEVER logged.
"""

from __future__ import annotations
import logging
import os
import time
from typing import Any, Optional

import requests


logger = logging.getLogger(__name__)

# Module-level env-var read, matching the pattern in app.py for stripe_secret.
HUNTER_API_KEY = os.environ.get('HUNTER_API_KEY', '').strip()
HUNTER_CREDIT_FLOOR = int(os.environ.get('HUNTER_CREDIT_FLOOR', '5'))

# Hunter v2 API base. Versioned in case a future v3 ships.
_BASE = 'https://api.hunter.io/v2'

# Network timeout. Hunter's docs claim sub-second response times for
# domain-search; we set 15s as a generous ceiling. Verify is slower
# (Hunter does live SMTP probes) so we use 30s for that one specifically.
_TIMEOUT_DEFAULT = 15
_TIMEOUT_VERIFY = 30

# Cache for account info — Hunter charges nothing for the /account endpoint
# but we still cache for 5 minutes to avoid hammering it from the UI.
_account_cache: dict[str, Any] = {'fetched_at': 0, 'data': None}
_ACCOUNT_TTL_SECONDS = 300


def _is_configured() -> bool:
    return bool(HUNTER_API_KEY)


def _safe_log_error(prefix: str, exc: Exception) -> None:
    """Log without ever including the API key."""
    msg = str(exc)
    # Belt-and-suspenders: scrub the key from any error string before logging
    if HUNTER_API_KEY and HUNTER_API_KEY in msg:
        msg = msg.replace(HUNTER_API_KEY, '[redacted]')
    logger.warning('%s: %s', prefix, msg)


def account_info(force_refresh: bool = False) -> dict[str, Any]:
    """Return account state including remaining credits.

    Free, doesn't consume credits. Cached 5 min unless force_refresh.
    Shape:
      {
        'configured': bool,
        'plan_name': str,
        'searches_used': int,    # used this billing period
        'searches_max': int,     # max this billing period
        'searches_left': int,    # what we actually care about for the floor
        'verifications_used': int,
        'verifications_max': int,
        'verifications_left': int,
        'reset_date': str,       # next reset (ISO date)
        'error': str | None,
      }
    """
    if not _is_configured():
        return {
            'configured': False,
            'error': 'HUNTER_API_KEY not set in environment',
        }

    now = time.time()
    if (not force_refresh
            and _account_cache['data'] is not None
            and now - _account_cache['fetched_at'] < _ACCOUNT_TTL_SECONDS):
        return _account_cache['data']

    try:
        resp = requests.get(
            f'{_BASE}/account',
            params={'api_key': HUNTER_API_KEY},
            timeout=_TIMEOUT_DEFAULT,
        )
        if resp.status_code != 200:
            return {
                'configured': True,
                'error': f'Hunter /account returned HTTP {resp.status_code}',
            }
        body = resp.json().get('data', {}) or {}
        # Hunter's response shape (current as of v2):
        #   plan_name, calls.used, calls.available
        # Newer responses split searches and verifications, older ones don't.
        # Handle both shapes defensively.
        calls = body.get('calls', {}) or {}
        searches_used = int(calls.get('used', 0) or 0)
        searches_max = int(calls.get('available', 0) or 0)

        # Some plans expose a separate 'verifications' counter; older free-tier
        # responses count verifications under the same 'calls' meter.
        verifications = body.get('verifications', {}) or {}
        if verifications:
            v_used = int(verifications.get('used', 0) or 0)
            v_max = int(verifications.get('available', 0) or 0)
        else:
            v_used = searches_used
            v_max = searches_max

        out = {
            'configured': True,
            'plan_name': body.get('plan_name') or 'unknown',
            'searches_used': searches_used,
            'searches_max': searches_max,
            'searches_left': max(0, searches_max - searches_used),
            'verifications_used': v_used,
            'verifications_max': v_max,
            'verifications_left': max(0, v_max - v_used),
            'reset_date': body.get('reset_date') or '',
            'error': None,
        }
        _account_cache['data'] = out
        _account_cache['fetched_at'] = now
        return out
    except requests.exceptions.RequestException as e:
        _safe_log_error('hunter.account_info', e)
        return {'configured': True, 'error': f'network: {e.__class__.__name__}'}
    except Exception as e:
        _safe_log_error('hunter.account_info', e)
        return {'configured': True, 'error': 'unexpected error'}


def _credit_floor_check(kind: str = 'searches') -> Optional[dict[str, Any]]:
    """Returns an error dict if credits are below the floor, None if OK.

    kind: 'searches' or 'verifications' — counted against different caps
    on plans that separate them.
    """
    info = account_info()
    if info.get('error') and not info.get('configured'):
        return {'error': info['error'], 'credit_exhausted': False}
    if info.get('error'):
        # Couldn't determine credits — fail closed. Better to block than
        # accidentally consume credits during an outage.
        return {'error': f'cannot determine credits: {info["error"]}', 'credit_exhausted': False}

    left = info.get('searches_left' if kind == 'searches' else 'verifications_left', 0)
    if left <= HUNTER_CREDIT_FLOOR:
        return {
            'error': f'Hunter credit floor reached ({left} ≤ {HUNTER_CREDIT_FLOOR}). '
                     f'Resets {info.get("reset_date") or "next billing cycle"}.',
            'credit_exhausted': True,
            'searches_left': info.get('searches_left'),
            'verifications_left': info.get('verifications_left'),
            'plan_name': info.get('plan_name'),
        }
    return None


def domain_search(domain: str, limit: int = 25, seniority: str = '',
                  department: str = '') -> dict[str, Any]:
    """Look up emails associated with a company domain.

    1 credit per call regardless of how many results come back.

    Args:
      domain: e.g. 'renofi.com'
      limit: max emails to return (Hunter caps at 100, free tier may cap lower)
      seniority: optional comma-separated filter — 'senior,executive,c_level'
      department: optional filter — 'executive,it,finance,management,sales,
                  legal,support,hr,marketing,communication,education,design,
                  health,operations,consulting,assistant,research'

    Returns:
      {
        'domain': str,
        'organization': str | None,
        'emails': [
          {'email', 'first_name', 'last_name', 'position', 'seniority',
           'department', 'confidence', 'sources_count'},
          ...
        ],
        'credits_left_after': int,
        'error': str | None,
      }
    """
    if not _is_configured():
        return {'domain': domain, 'emails': [], 'error': 'HUNTER_API_KEY not configured'}

    floor = _credit_floor_check('searches')
    if floor:
        return {'domain': domain, 'emails': [], **floor}

    domain = (domain or '').strip().lower()
    if not domain:
        return {'domain': '', 'emails': [], 'error': 'domain is required'}

    params: dict[str, Any] = {
        'api_key': HUNTER_API_KEY,
        'domain': domain,
        'limit': max(1, min(100, int(limit))),
    }
    if seniority:
        params['seniority'] = seniority
    if department:
        params['department'] = department

    try:
        resp = requests.get(f'{_BASE}/domain-search', params=params, timeout=_TIMEOUT_DEFAULT)
        if resp.status_code == 401:
            return {'domain': domain, 'emails': [], 'error': 'invalid HUNTER_API_KEY'}
        if resp.status_code == 429:
            return {'domain': domain, 'emails': [], 'error': 'Hunter rate-limited (HTTP 429)'}
        if resp.status_code != 200:
            return {'domain': domain, 'emails': [], 'error': f'Hunter HTTP {resp.status_code}'}
        body = resp.json().get('data', {}) or {}
        raw_emails = body.get('emails', []) or []

        # Normalize to a stable schema. Hunter's response includes a confidence
        # int 0-100 and per-email source URLs; we surface the count, not the
        # URLs, to keep the response compact.
        emails = [
            {
                'email': e.get('value') or '',
                'first_name': e.get('first_name') or '',
                'last_name': e.get('last_name') or '',
                'position': e.get('position') or '',
                'seniority': e.get('seniority') or '',
                'department': e.get('department') or '',
                'confidence': int(e.get('confidence') or 0),
                'sources_count': len(e.get('sources') or []),
            }
            for e in raw_emails
            if e.get('value')
        ]

        # Force account-info refresh on next read so the UI badge reflects
        # the credit decrement immediately.
        _account_cache['fetched_at'] = 0

        return {
            'domain': domain,
            'organization': body.get('organization') or '',
            'emails': emails,
            'pattern': body.get('pattern') or '',
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        _safe_log_error(f'hunter.domain_search({domain})', e)
        return {'domain': domain, 'emails': [], 'error': f'network: {e.__class__.__name__}'}
    except Exception as e:
        _safe_log_error(f'hunter.domain_search({domain})', e)
        return {'domain': domain, 'emails': [], 'error': 'unexpected error'}


def verify_email(email: str) -> dict[str, Any]:
    """Verify an email address before sending.

    1 credit per call. Hunter performs live SMTP probes so this is slow
    (1-3 seconds) and rate-sensitive — don't call in tight loops.

    Returns:
      {
        'email': str,
        'result': 'deliverable' | 'undeliverable' | 'risky' | 'unknown',
        'status': str,                 # raw Hunter status
        'score': int,                  # 0-100
        'regexp': bool,                # passes basic syntax
        'gibberish': bool,             # likely garbage
        'disposable': bool,            # disposable mail provider
        'webmail': bool,               # gmail/yahoo etc
        'mx_records': bool,            # domain has MX
        'smtp_server': bool,           # SMTP responded
        'smtp_check': bool,            # mailbox exists
        'accept_all': bool,            # catch-all (verification weak)
        'block': bool,                 # provider blocks verification
        'safe_to_send': bool,          # convenience: result in (deliverable,)
        'error': str | None,
      }
    """
    if not _is_configured():
        return {'email': email, 'result': 'unknown', 'error': 'HUNTER_API_KEY not configured'}

    floor = _credit_floor_check('verifications')
    if floor:
        return {'email': email, 'result': 'unknown', **floor}

    email = (email or '').strip().lower()
    if not email or '@' not in email:
        return {'email': email, 'result': 'unknown', 'error': 'email is required'}

    try:
        resp = requests.get(
            f'{_BASE}/email-verifier',
            params={'api_key': HUNTER_API_KEY, 'email': email},
            timeout=_TIMEOUT_VERIFY,
        )
        if resp.status_code == 401:
            return {'email': email, 'result': 'unknown', 'error': 'invalid HUNTER_API_KEY'}
        if resp.status_code == 429:
            return {'email': email, 'result': 'unknown', 'error': 'rate-limited (HTTP 429)'}
        if resp.status_code != 200:
            return {'email': email, 'result': 'unknown', 'error': f'Hunter HTTP {resp.status_code}'}

        body = resp.json().get('data', {}) or {}
        # Hunter's verifier returns a `result` field with one of:
        #   'deliverable', 'undeliverable', 'risky', 'unknown'
        # `safe_to_send` is our derived convenience boolean — we treat
        # 'risky' as not-safe even though Hunter sometimes accepts it.
        result = body.get('result') or 'unknown'
        score = int(body.get('score') or 0)
        safe = result == 'deliverable' and score >= 70

        # Decrement-aware: invalidate the account cache
        _account_cache['fetched_at'] = 0

        return {
            'email': email,
            'result': result,
            'status': body.get('status') or '',
            'score': score,
            'regexp': bool(body.get('regexp')),
            'gibberish': bool(body.get('gibberish')),
            'disposable': bool(body.get('disposable')),
            'webmail': bool(body.get('webmail')),
            'mx_records': bool(body.get('mx_records')),
            'smtp_server': bool(body.get('smtp_server')),
            'smtp_check': bool(body.get('smtp_check')),
            'accept_all': bool(body.get('accept_all')),
            'block': bool(body.get('block')),
            'safe_to_send': safe,
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        _safe_log_error(f'hunter.verify_email({email})', e)
        return {'email': email, 'result': 'unknown', 'error': f'network: {e.__class__.__name__}'}
    except Exception as e:
        _safe_log_error(f'hunter.verify_email({email})', e)
        return {'email': email, 'result': 'unknown', 'error': 'unexpected error'}


def verify_emails_batch(emails: list[str]) -> dict[str, Any]:
    """Verify multiple emails sequentially with credit-floor protection.

    Stops at the floor — partial results are still returned with a clear
    'credit_exhausted' marker so the caller can decide whether to send
    the verified subset and abort the rest.

    Returns:
      {
        'results': [verify_email(...) shape],
        'verified_count': int,
        'safe_count': int,
        'credit_exhausted': bool,
        'credits_left': int | None,
      }
    """
    results = []
    safe = 0
    exhausted = False
    for email in emails:
        # Per-call floor check happens inside verify_email; we re-check
        # here to short-circuit the loop without firing each call's
        # account_info check (which is cached but still costs a code path).
        floor = _credit_floor_check('verifications')
        if floor:
            exhausted = True
            results.append({
                'email': email,
                'result': 'unknown',
                'safe_to_send': False,
                **floor,
            })
            # Once the floor hits, everything else gets the same skip
            for remaining in emails[len(results):]:
                results.append({
                    'email': remaining,
                    'result': 'unknown',
                    'safe_to_send': False,
                    'error': 'skipped: credit floor',
                    'credit_exhausted': True,
                })
            break
        r = verify_email(email)
        results.append(r)
        if r.get('safe_to_send'):
            safe += 1
        # Brief pause between SMTP probes — Hunter rate-limits aggressive
        # callers and the SMTP servers being probed sometimes do too.
        time.sleep(0.4)

    info = account_info(force_refresh=False)
    return {
        'results': results,
        'verified_count': len(results),
        'safe_count': safe,
        'credit_exhausted': exhausted,
        'credits_left': info.get('verifications_left'),
    }
