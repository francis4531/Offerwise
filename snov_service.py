"""snov_service.py — Snov.io discovery API client (v5.87.49).

Tertiary discovery fallback after Hunter (primary) and Apollo (secondary).
Reaches for Snov when both Hunter and Apollo come up empty for niche
companies — the database overlap is incomplete and Snov sometimes has
contacts the other two miss for smaller renovation lenders, regional
insurtechs, and sub-100-person brokerages.

Free tier: 50 credits/month for both domain search AND email finder
(separate quotas, both reset monthly). Domain search consumes 1 credit
per call regardless of how many emails come back.

Auth model: OAuth2 client_credentials. Different from Hunter/Apollo's
simple API-key headers — Snov requires:
  1. POST /v1/oauth/access_token with client_id + client_secret → access_token
  2. Use access_token as Bearer in subsequent calls

We cache the access_token for ~50 minutes (Snov tokens are valid for ~1hr).

Env vars:
  SNOV_CLIENT_ID         — required for any call
  SNOV_CLIENT_SECRET     — required
  SNOV_CREDIT_FLOOR      — default 5
  SNOV_TIMEOUT_SECONDS   — default 20
  SNOV_TOKEN_TTL_SECONDS — default 3000 (50 min, leaves margin)
"""
from __future__ import annotations
import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

SNOV_CLIENT_ID         = os.environ.get('SNOV_CLIENT_ID', '').strip()
SNOV_CLIENT_SECRET     = os.environ.get('SNOV_CLIENT_SECRET', '').strip()
SNOV_CREDIT_FLOOR      = int(os.environ.get('SNOV_CREDIT_FLOOR', '5'))
SNOV_TIMEOUT_SECONDS   = int(os.environ.get('SNOV_TIMEOUT_SECONDS', '20'))
SNOV_TOKEN_TTL_SECONDS = int(os.environ.get('SNOV_TOKEN_TTL_SECONDS', '3000'))

_BASE = 'https://api.snov.io'
_token_cache: dict[str, Any] = {'token': None, 'fetched_at': 0}
_account_cache: dict[str, Any] = {'fetched_at': 0, 'data': None}
_ACCOUNT_TTL_SECONDS = 300


def _is_configured() -> bool:
    return bool(SNOV_CLIENT_ID and SNOV_CLIENT_SECRET)


def _safe_log_error(prefix: str, exc: Exception) -> None:
    """Log without leaking client_secret."""
    msg = str(exc)
    if SNOV_CLIENT_SECRET and SNOV_CLIENT_SECRET in msg:
        msg = msg.replace(SNOV_CLIENT_SECRET, '[redacted]')
    logger.warning('%s: %s', prefix, msg)


def _get_access_token() -> Optional[str]:
    """Fetch a Snov OAuth access token, with caching.

    Returns None on failure — callers must check.
    """
    if not _is_configured():
        return None

    now = time.time()
    if (_token_cache['token']
            and now - _token_cache['fetched_at'] < SNOV_TOKEN_TTL_SECONDS):
        return _token_cache['token']

    try:
        resp = requests.post(
            f'{_BASE}/v1/oauth/access_token',
            data={
                'grant_type': 'client_credentials',
                'client_id': SNOV_CLIENT_ID,
                'client_secret': SNOV_CLIENT_SECRET,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning('snov OAuth failed: HTTP %s', resp.status_code)
            return None
        token = (resp.json() or {}).get('access_token')
        if not token:
            return None
        _token_cache['token'] = token
        _token_cache['fetched_at'] = now
        return token
    except requests.exceptions.RequestException as e:
        _safe_log_error('snov.oauth', e)
        return None


def account_info(force_refresh: bool = False) -> dict[str, Any]:
    """Query Snov user balance. Free, doesn't consume credits."""
    if not _is_configured():
        return {'configured': False,
                'error': 'SNOV_CLIENT_ID/SECRET not set in environment'}

    now = time.time()
    if (not force_refresh
            and _account_cache['data'] is not None
            and now - _account_cache['fetched_at'] < _ACCOUNT_TTL_SECONDS):
        return _account_cache['data']

    token = _get_access_token()
    if not token:
        return {'configured': True, 'error': 'OAuth token fetch failed'}

    try:
        # v5.87.54: corrected back to /v1/get-balance after v5.87.51 mis-fix.
        # The official Snov docs (2026) confirm:
        #   - Endpoint is /v1/get-balance (NOT /v1/get-user-balance)
        #   - Response shape: {success: bool, data: {balance: "25000.00",
        #     teamwork: bool, unique_recipients_used: int, limit_resets_in: int,
        #     expires_in: int}}
        #   - `balance` comes back as a STRING (e.g. "50.00"), not an int
        # Source: https://snov.io/api
        resp = requests.get(
            f'{_BASE}/v1/get-balance',
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )
        if resp.status_code != 200:
            return {'configured': True, 'error': f'Snov HTTP {resp.status_code}'}
        body = resp.json() or {}
        if not body.get('success', True):
            return {'configured': True,
                    'error': body.get('message') or 'Snov returned success=false'}

        # Defensive: try a couple of plausible field paths in case Snov
        # changes shape on us. Snov returns balance as a STRING ("50.00")
        # so we float()→int() to handle decimals safely.
        data = body.get('data') or {}
        credits_raw = (
            data.get('balance')                # current shape (Snov v1 docs)
            or data.get('user_balance')        # paranoid fallback
            or body.get('balance')             # paranoid fallback
            or 0
        )
        try:
            credits = int(float(credits_raw))  # handle "50.00" → 50
        except (TypeError, ValueError):
            credits = 0

        out = {
            'configured': True,
            'provider': 'snov',
            'plan_name': 'free' if credits <= 50 else 'paid',
            'credits_left': credits,
            'error': None,
        }
        _account_cache['data'] = out
        _account_cache['fetched_at'] = now
        return out
    except requests.exceptions.RequestException as e:
        _safe_log_error('snov.account', e)
        return {'configured': True, 'error': f'network: {e.__class__.__name__}'}


def _credit_floor_check() -> Optional[dict[str, Any]]:
    info = account_info()
    if info.get('error') and not info.get('configured'):
        return {'error': info['error'], 'credit_exhausted': False}
    if info.get('error'):
        return {'error': f'cannot determine credits: {info["error"]}',
                'credit_exhausted': False}
    left = info.get('credits_left')
    if left is None:
        return None
    if left <= SNOV_CREDIT_FLOOR:
        return {
            'error': f'Snov credit floor reached ({left} ≤ {SNOV_CREDIT_FLOOR})',
            'credit_exhausted': True,
            'credits_left': left,
        }
    return None


def domain_search(domain: str, limit: int = 25) -> dict[str, Any]:
    """Find emails associated with a company domain via Snov.

    1 credit per call. Returns same shape as hunter_service.domain_search
    so the unified discovery service can drop Snov in as fallback without
    branching on provider type.

    Returns:
      {
        'domain': str,
        'organization': str | None,
        'emails': [{email, first_name, last_name, position,
                    seniority, department, confidence, sources_count}],
        'error': str | None,
      }
    """
    if not _is_configured():
        return {'domain': domain, 'emails': [],
                'error': 'SNOV_CLIENT_ID/SECRET not configured'}

    floor = _credit_floor_check()
    if floor:
        return {'domain': domain, 'emails': [], **floor}

    domain = (domain or '').strip().lower()
    if not domain:
        return {'domain': '', 'emails': [], 'error': 'domain is required'}

    token = _get_access_token()
    if not token:
        return {'domain': domain, 'emails': [], 'error': 'OAuth token fetch failed'}

    try:
        # v5.87.54: Snov's domain search is now an async two-step flow
        # (the old /v2/domain-emails-with-info single-GET endpoint is dead,
        # which is why v5.87.49-53 produced 404s).
        #
        # Step 1: POST /v2/domain-search/start → returns task_hash
        # Step 2: poll GET /v2/domain-search/result/{task_hash} until
        #         status is 'completed' (or timeout)
        # Source: https://snov.io/api · v2 docs · 2026
        start_resp = requests.post(
            f'{_BASE}/v2/domain-search/start',
            headers={'Authorization': f'Bearer {token}'},
            data={
                'domain': domain,
                'type': 'all',  # 'personal' | 'generic' | 'all'
                'limit': max(1, min(100, int(limit))),
            },
            timeout=SNOV_TIMEOUT_SECONDS,
        )
        if start_resp.status_code == 401:
            return {'domain': domain, 'emails': [], 'error': 'invalid Snov credentials'}
        if start_resp.status_code == 429:
            return {'domain': domain, 'emails': [], 'error': 'Snov rate-limited (429)'}
        if start_resp.status_code == 404:
            return {'domain': domain, 'emails': [],
                    'error': 'Snov v2 endpoint not found (API may have changed again)'}
        # v5.87.55: accept 200 AND 202 as success. Snov's async /start
        # endpoint returns HTTP 202 Accepted when the task is queued,
        # which is the normal happy path — the work happens in the
        # subsequent /result/{task_hash} polling. v5.87.54 incorrectly
        # treated 202 as an error.
        if start_resp.status_code not in (200, 202):
            return {'domain': domain, 'emails': [],
                    'error': f'Snov start HTTP {start_resp.status_code}'}

        start_body = start_resp.json() or {}
        # Response shape (current Snov v2, confirmed by real response):
        #   {data: [], meta: {domain, task_hash, ...}, links: {result: "..."}}
        # The task_hash lives at meta.task_hash. Older docs sometimes show
        # data.task_hash or top-level task_hash; we try all three plus parse
        # from links.result URL as a last resort, for forward/backward compat.
        task_hash = None

        # Path 1: meta.task_hash (current shape - 2026)
        meta = start_body.get('meta') or {}
        task_hash = meta.get('task_hash')

        # Path 2: data.task_hash (older shape, when data is a dict)
        if not task_hash:
            data = start_body.get('data')
            if isinstance(data, dict):
                task_hash = data.get('task_hash')

        # Path 3: top-level task_hash (some account types)
        if not task_hash:
            task_hash = start_body.get('task_hash')

        # Path 4: parse from links.result URL ("..../result/{hash}")
        if not task_hash:
            links = start_body.get('links') or {}
            result_url = links.get('result') or ''
            if '/result/' in result_url:
                task_hash = result_url.rsplit('/result/', 1)[-1].strip('/')

        if not task_hash:
            return {'domain': domain, 'emails': [],
                    'error': f'Snov start did not return task_hash: {start_body}'}

        # Step 2: poll for result. Snov typically completes in 2-5 sec for
        # small domains, but can take 10-15 sec for larger ones. We poll
        # every 1.5 sec up to SNOV_TIMEOUT_SECONDS.
        result_url = f'{_BASE}/v2/domain-search/result/{task_hash}'
        poll_interval = 1.5
        max_polls = max(3, int(SNOV_TIMEOUT_SECONDS / poll_interval))

        result_body = None
        for _ in range(max_polls):
            time.sleep(poll_interval)
            poll_resp = requests.get(
                result_url,
                headers={'Authorization': f'Bearer {token}'},
                timeout=10,
            )
            if poll_resp.status_code != 200:
                continue
            poll_body = poll_resp.json() or {}
            status = (poll_body.get('status') or
                      (poll_body.get('meta') or {}).get('status') or '')
            if status == 'completed':
                result_body = poll_body
                break
            # Other statuses: 'in_progress', 'pending' — keep polling

        if not result_body:
            return {'domain': domain, 'emails': [],
                    'error': f'Snov result polling timed out after {SNOV_TIMEOUT_SECONDS}s'}

        # Result body shape (current Snov v2 docs):
        #   {data: [{email, first_name, last_name, position, ...}],
        #    meta: {domain, total_count, ...},
        #    status: "completed"}
        # raw_emails should be a list per Snov v2 docs. Be defensive in case
        # Snov returns a different shape (dict-of-emails, error string, etc).
        raw_emails = result_body.get('data')
        if not isinstance(raw_emails, list):
            raw_emails = []
        meta = result_body.get('meta') or {}
        company_name = meta.get('company_name') or meta.get('domain') or ''

        emails = []
        for e in raw_emails:
            # Defensive: each entry should be a dict but Snov has been known
            # to return string emails for "generic" type results. Skip
            # anything that isn't a dict rather than crashing.
            if not isinstance(e, dict):
                continue
            email = (e.get('email') or '').strip().lower()
            if not email:
                continue
            # Snov v2 result shape uses snake_case (first_name) per current
            # docs, but defensive: also try camelCase in case of drift.
            try:
                sources_count = int(e.get('sources_count') or e.get('sourcesCount') or 0)
            except (TypeError, ValueError):
                sources_count = 0
            emails.append({
                'email': email,
                'first_name': e.get('first_name') or e.get('firstName') or '',
                'last_name': e.get('last_name') or e.get('lastName') or '',
                'position': e.get('position') or '',
                'seniority': '',
                'department': '',
                'confidence': 50 if e.get('type') == 'personal' else 30,
                'sources_count': sources_count,
            })

        # Bust account cache so the credit badge refreshes
        _account_cache['fetched_at'] = 0

        return {
            'domain': domain,
            'organization': company_name,
            'emails': emails,
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        _safe_log_error(f'snov.domain_search({domain})', e)
        return {'domain': domain, 'emails': [],
                'error': f'network: {e.__class__.__name__}: {str(e)[:200]}'}
    except Exception as e:
        # v5.87.57: surface the actual exception class + message + line number
        # so we can debug from the user-facing error rather than guessing.
        # Previously this returned 'unexpected error' which was useless.
        import traceback as _tb
        tb_lines = _tb.format_exc().splitlines()
        # Find the last frame inside snov_service.py for an actionable line ref
        last_frame = ''
        for ln in tb_lines:
            if 'snov_service.py' in ln:
                last_frame = ln.strip()
        _safe_log_error(f'snov.domain_search({domain})', e)
        logger.warning('snov.domain_search traceback: %s', _tb.format_exc())
        return {
            'domain': domain,
            'emails': [],
            'error': f'{e.__class__.__name__}: {str(e)[:200]}'
                     + (f' [{last_frame}]' if last_frame else ''),
        }
