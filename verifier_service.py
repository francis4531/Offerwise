"""verifier_service.py — Pluggable email verification (v5.87.49).

Decouples email verification from Hunter, which lets us:
  1. Stop burning Hunter discovery credits on verification (75 free credits/mo
     was being shared between domain-search and email-verifier — switching
     the verifier to a dedicated provider reclaims ~40 credits/month for
     actual prospect discovery)
  2. Use a cheaper specialized verifier when scaling past the free tier.
     MillionVerifier: $3.50 per 1,000 verifications, credits don't expire
     ZeroBounce:      $15.00 per 2,000 verifications, expire after 12 months
     Hunter:          ~$40 per 1,000 verifications (entry paid tier)

Two backends supported via VERIFIER_PROVIDER env var:
  'millionverifier' (default) — MILLIONVERIFIER_API_KEY
  'zerobounce'                — ZEROBOUNCE_API_KEY
  'hunter'                    — falls through to hunter_service.verify_email,
                                 useful as a fallback on existing keys

Public interface mirrors hunter_service exactly so callers can swap without
code changes:
  - account_info()         → {configured, credits_left, plan_name, error}
  - verify_email(email)    → {email, result, score, safe_to_send, ...}
  - verify_emails_batch()  → {results, verified_count, safe_count, ...}

`safe_to_send` derivation is intentionally conservative across both backends:
deliverable AND score ≥ 70. 'risky', 'catch-all', and low-score 'deliverable'
results are filtered out — sending to those hurts your sender reputation.

Env vars:
  VERIFIER_PROVIDER          — 'millionverifier' (default), 'zerobounce', 'hunter'
  MILLIONVERIFIER_API_KEY    — required if provider=millionverifier
  ZEROBOUNCE_API_KEY         — required if provider=zerobounce
  VERIFIER_CREDIT_FLOOR      — default 5
  VERIFIER_TIMEOUT_SECONDS   — default 30 (SMTP probes are slow)
  VERIFIER_BATCH_DELAY_MS    — default 400 (sleep between batch requests)
  VERIFIER_SAFE_SCORE_FLOOR  — default 70 (score ≥ this AND deliverable = safe)
"""
from __future__ import annotations
import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# Provider selector. Single env var so swapping is one Render dashboard edit.
VERIFIER_PROVIDER = os.environ.get('VERIFIER_PROVIDER', 'millionverifier').strip().lower()

# Credentials — only the relevant one needs to be set
MILLIONVERIFIER_API_KEY = os.environ.get('MILLIONVERIFIER_API_KEY', '').strip()
ZEROBOUNCE_API_KEY      = os.environ.get('ZEROBOUNCE_API_KEY', '').strip()

# Tunables
VERIFIER_CREDIT_FLOOR     = int(os.environ.get('VERIFIER_CREDIT_FLOOR', '5'))
VERIFIER_TIMEOUT_SECONDS  = int(os.environ.get('VERIFIER_TIMEOUT_SECONDS', '30'))
VERIFIER_BATCH_DELAY_MS   = int(os.environ.get('VERIFIER_BATCH_DELAY_MS', '400'))
VERIFIER_SAFE_SCORE_FLOOR = int(os.environ.get('VERIFIER_SAFE_SCORE_FLOOR', '70'))

# API constants — protocol literals, stay hardcoded per the v5.87.48 rule.
_MV_BASE = 'https://api.millionverifier.com/api/v3'
_ZB_BASE = 'https://api.zerobounce.net/v2'

# Account-info cache (free across all providers; cache to avoid hammering
# on every UI render of the credit badge)
_account_cache: dict[str, Any] = {'fetched_at': 0, 'data': None}
_ACCOUNT_TTL_SECONDS = 300


# ─── Internal helpers ──────────────────────────────────────────────────

def _safe_log_error(prefix: str, exc: Exception, key: str = '') -> None:
    """Log without ever leaking the API key."""
    msg = str(exc)
    if key and key in msg:
        msg = msg.replace(key, '[redacted]')
    logger.warning('%s: %s', prefix, msg)


def _is_configured() -> bool:
    """Whether the currently-selected provider has its API key set."""
    if VERIFIER_PROVIDER == 'millionverifier':
        return bool(MILLIONVERIFIER_API_KEY)
    if VERIFIER_PROVIDER == 'zerobounce':
        return bool(ZEROBOUNCE_API_KEY)
    if VERIFIER_PROVIDER == 'hunter':
        # Falls through to hunter_service which has its own configured-check
        try:
            from hunter_service import _is_configured as h_configured
            return h_configured()
        except ImportError:
            return False
    return False


# ─── MillionVerifier backend ───────────────────────────────────────────

def _mv_account_info() -> dict[str, Any]:
    """Query MillionVerifier credit balance. Free, doesn't consume credits."""
    try:
        resp = requests.get(
            f'{_MV_BASE}/credits',
            params={'api': MILLIONVERIFIER_API_KEY},
            timeout=10,
        )
        if resp.status_code == 401:
            return {'configured': True, 'error': 'invalid MILLIONVERIFIER_API_KEY'}
        if resp.status_code != 200:
            return {'configured': True, 'error': f'MV HTTP {resp.status_code}'}
        body = resp.json() or {}
        # MillionVerifier returns {credits: int}
        credits = int(body.get('credits') or 0)
        return {
            'configured': True,
            'provider': 'millionverifier',
            'plan_name': 'free' if credits <= 100 else 'paid',
            'credits_left': credits,
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        _safe_log_error('millionverifier.account', e, MILLIONVERIFIER_API_KEY)
        return {'configured': True, 'error': f'network: {e.__class__.__name__}'}


def _mv_verify_email(email: str) -> dict[str, Any]:
    """MillionVerifier single-email verification. 1 credit per call."""
    try:
        resp = requests.get(
            f'{_MV_BASE}/api',
            params={
                'api': MILLIONVERIFIER_API_KEY,
                'email': email,
                'timeout': min(VERIFIER_TIMEOUT_SECONDS, 60),
            },
            timeout=VERIFIER_TIMEOUT_SECONDS,
        )
        if resp.status_code == 401:
            return {'email': email, 'result': 'unknown', 'error': 'invalid MILLIONVERIFIER_API_KEY'}
        if resp.status_code == 429:
            return {'email': email, 'result': 'unknown', 'error': 'rate-limited (429)'}
        if resp.status_code != 200:
            return {'email': email, 'result': 'unknown', 'error': f'MV HTTP {resp.status_code}'}

        body = resp.json() or {}
        # MillionVerifier returns:
        #   resultcode: 1=ok, 2=catch_all, 3=unknown, 4=error, 5=disposable, 6=invalid
        #   quality: 'good' | 'risky' | 'bad'
        #   role/free/disposable: bool flags
        # We map their resultcode to Hunter-shape semantics so callers can
        # use the same `result` and `safe_to_send` fields.
        rc = int(body.get('resultcode') or 0)
        quality = (body.get('quality') or '').lower()
        result = {
            1: 'deliverable',
            2: 'risky',         # catch-all
            3: 'unknown',
            4: 'unknown',       # service error during probe
            5: 'undeliverable', # disposable
            6: 'undeliverable', # invalid syntax / domain
        }.get(rc, 'unknown')
        # MV's "quality" is their summary score. Map to 0-100 for parity.
        score = {'good': 90, 'risky': 50, 'bad': 10}.get(quality, 0)
        safe = (result == 'deliverable') and (score >= VERIFIER_SAFE_SCORE_FLOOR)

        # Bust account cache so credit-badge refreshes
        _account_cache['fetched_at'] = 0

        return {
            'email': email,
            'result': result,
            'status': body.get('result') or quality,
            'score': score,
            'regexp': bool(body.get('regex')),
            'gibberish': False,
            'disposable': bool(body.get('disposable')),
            'webmail': bool(body.get('free')),
            'mx_records': True,
            'smtp_server': True,
            'smtp_check': result == 'deliverable',
            'accept_all': rc == 2,
            'block': False,
            'safe_to_send': safe,
            'provider': 'millionverifier',
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        _safe_log_error(f'millionverifier.verify({email})', e, MILLIONVERIFIER_API_KEY)
        return {'email': email, 'result': 'unknown', 'error': f'network: {e.__class__.__name__}'}


# ─── ZeroBounce backend ────────────────────────────────────────────────

def _zb_account_info() -> dict[str, Any]:
    """Query ZeroBounce credit balance. Free, doesn't consume credits."""
    try:
        resp = requests.get(
            f'{_ZB_BASE}/getcredits',
            params={'api_key': ZEROBOUNCE_API_KEY},
            timeout=10,
        )
        if resp.status_code != 200:
            return {'configured': True, 'error': f'ZB HTTP {resp.status_code}'}
        body = resp.json() or {}
        # ZB returns {Credits: int} (capital C)
        credits = int(body.get('Credits') or 0)
        return {
            'configured': True,
            'provider': 'zerobounce',
            'plan_name': 'free' if credits <= 100 else 'paid',
            'credits_left': credits,
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        _safe_log_error('zerobounce.account', e, ZEROBOUNCE_API_KEY)
        return {'configured': True, 'error': f'network: {e.__class__.__name__}'}


def _zb_verify_email(email: str) -> dict[str, Any]:
    """ZeroBounce single-email verification. 1 credit per call."""
    try:
        resp = requests.get(
            f'{_ZB_BASE}/validate',
            params={'api_key': ZEROBOUNCE_API_KEY, 'email': email},
            timeout=VERIFIER_TIMEOUT_SECONDS,
        )
        if resp.status_code == 401:
            return {'email': email, 'result': 'unknown', 'error': 'invalid ZEROBOUNCE_API_KEY'}
        if resp.status_code == 429:
            return {'email': email, 'result': 'unknown', 'error': 'rate-limited (429)'}
        if resp.status_code != 200:
            return {'email': email, 'result': 'unknown', 'error': f'ZB HTTP {resp.status_code}'}

        body = resp.json() or {}
        # ZeroBounce status: 'valid' | 'invalid' | 'catch-all' | 'unknown' | 'spamtrap' | 'abuse'
        zb_status = (body.get('status') or '').lower()
        result = {
            'valid': 'deliverable',
            'invalid': 'undeliverable',
            'catch-all': 'risky',
            'unknown': 'unknown',
            'spamtrap': 'undeliverable',
            'abuse': 'undeliverable',
            'do_not_mail': 'undeliverable',
        }.get(zb_status, 'unknown')

        # ZB doesn't return a numeric score directly — their "sub_status"
        # gives reason codes. Map status to a consistent 0-100 score.
        score = {
            'deliverable': 90,
            'risky': 50,
            'undeliverable': 5,
            'unknown': 0,
        }.get(result, 0)
        safe = (result == 'deliverable') and (score >= VERIFIER_SAFE_SCORE_FLOOR)

        _account_cache['fetched_at'] = 0

        return {
            'email': email,
            'result': result,
            'status': zb_status,
            'score': score,
            'regexp': True,
            'gibberish': False,
            'disposable': bool(body.get('disposable')),
            'webmail': bool(body.get('free_email')),
            'mx_records': bool(body.get('mx_found') == 'true'),
            'smtp_server': result != 'undeliverable',
            'smtp_check': result == 'deliverable',
            'accept_all': zb_status == 'catch-all',
            'block': False,
            'safe_to_send': safe,
            'provider': 'zerobounce',
            'error': None,
        }
    except requests.exceptions.RequestException as e:
        _safe_log_error(f'zerobounce.verify({email})', e, ZEROBOUNCE_API_KEY)
        return {'email': email, 'result': 'unknown', 'error': f'network: {e.__class__.__name__}'}


# ─── Hunter pass-through (legacy fallback) ─────────────────────────────

def _hunter_account_info() -> dict[str, Any]:
    """Pass through to hunter_service so VERIFIER_PROVIDER=hunter works."""
    try:
        from hunter_service import account_info as h_account
        info = h_account()
        if info.get('error') or not info.get('configured'):
            return info
        return {
            'configured': True,
            'provider': 'hunter',
            'plan_name': info.get('plan_name'),
            'credits_left': info.get('verifications_left'),
            'error': None,
        }
    except ImportError:
        return {'configured': False, 'error': 'hunter_service not available'}


def _hunter_verify_email(email: str) -> dict[str, Any]:
    """Pass through to hunter_service.verify_email."""
    try:
        from hunter_service import verify_email as h_verify
        result = h_verify(email)
        result['provider'] = 'hunter'
        return result
    except ImportError:
        return {'email': email, 'result': 'unknown', 'error': 'hunter_service not available'}


# ─── Public dispatch ───────────────────────────────────────────────────

def account_info(force_refresh: bool = False) -> dict[str, Any]:
    """Return current verifier provider's account state."""
    if not _is_configured():
        return {
            'configured': False,
            'provider': VERIFIER_PROVIDER,
            'error': f'{VERIFIER_PROVIDER.upper()}_API_KEY not set',
        }

    now = time.time()
    if (not force_refresh
            and _account_cache['data'] is not None
            and now - _account_cache['fetched_at'] < _ACCOUNT_TTL_SECONDS):
        return _account_cache['data']

    if VERIFIER_PROVIDER == 'millionverifier':
        out = _mv_account_info()
    elif VERIFIER_PROVIDER == 'zerobounce':
        out = _zb_account_info()
    elif VERIFIER_PROVIDER == 'hunter':
        out = _hunter_account_info()
    else:
        out = {'configured': False, 'error': f'unknown provider: {VERIFIER_PROVIDER}'}

    out['credit_floor'] = VERIFIER_CREDIT_FLOOR
    _account_cache['data'] = out
    _account_cache['fetched_at'] = now
    return out


# v5.87.68: per-provider cache so /api/admin/verifier/status-all isn't
# slow when all three are configured. Each entry: {'fetched_at': ts, 'data': dict}
_all_account_cache: dict[str, dict[str, Any]] = {}

def account_info_all(force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Return account state for every configured verifier provider.

    Unlike account_info() which only queries the active provider (the
    one named by VERIFIER_PROVIDER), this queries each configured
    provider independently so the UI can surface all balances at once.

    A provider is "configured" if its API key env var is set. Providers
    without keys return {'configured': False} without making any API call.

    Returns:
        {
            'millionverifier': {configured, provider, credits_left, ...},
            'zerobounce':      {configured, provider, credits_left, ...},
            'hunter':          {configured, provider, ...},
            'active': str  # which one is currently selected via VERIFIER_PROVIDER
        }
    """
    now = time.time()
    out: dict[str, dict[str, Any]] = {}

    # MillionVerifier
    if MILLIONVERIFIER_API_KEY:
        cached = _all_account_cache.get('millionverifier')
        if (not force_refresh and cached
                and now - cached['fetched_at'] < _ACCOUNT_TTL_SECONDS):
            out['millionverifier'] = cached['data']
        else:
            data = _mv_account_info()
            data['credit_floor'] = VERIFIER_CREDIT_FLOOR
            _all_account_cache['millionverifier'] = {'fetched_at': now, 'data': data}
            out['millionverifier'] = data
    else:
        out['millionverifier'] = {
            'configured': False,
            'provider': 'millionverifier',
            'error': 'MILLIONVERIFIER_API_KEY not set',
        }

    # ZeroBounce
    if ZEROBOUNCE_API_KEY:
        cached = _all_account_cache.get('zerobounce')
        if (not force_refresh and cached
                and now - cached['fetched_at'] < _ACCOUNT_TTL_SECONDS):
            out['zerobounce'] = cached['data']
        else:
            data = _zb_account_info()
            data['credit_floor'] = VERIFIER_CREDIT_FLOOR
            _all_account_cache['zerobounce'] = {'fetched_at': now, 'data': data}
            out['zerobounce'] = data
    else:
        out['zerobounce'] = {
            'configured': False,
            'provider': 'zerobounce',
            'error': 'ZEROBOUNCE_API_KEY not set',
        }

    # Hunter (legacy verifier — re-uses the existing HUNTER_API_KEY)
    try:
        from hunter_service import HUNTER_API_KEY
        if HUNTER_API_KEY:
            cached = _all_account_cache.get('hunter')
            if (not force_refresh and cached
                    and now - cached['fetched_at'] < _ACCOUNT_TTL_SECONDS):
                out['hunter'] = cached['data']
            else:
                data = _hunter_account_info()
                data['credit_floor'] = VERIFIER_CREDIT_FLOOR
                _all_account_cache['hunter'] = {'fetched_at': now, 'data': data}
                out['hunter'] = data
        else:
            out['hunter'] = {
                'configured': False,
                'provider': 'hunter',
                'error': 'HUNTER_API_KEY not set',
            }
    except ImportError:
        out['hunter'] = {
            'configured': False,
            'provider': 'hunter',
            'error': 'hunter_service not loaded',
        }

    out['active'] = VERIFIER_PROVIDER  # type: ignore[assignment]
    return out  # type: ignore[return-value]


def _credit_floor_check() -> Optional[dict[str, Any]]:
    """Returns an error dict if credits are below the floor, None if OK."""
    info = account_info()
    if info.get('error') and not info.get('configured'):
        return {'error': info['error'], 'credit_exhausted': False}
    if info.get('error'):
        # Couldn't determine credits — fail closed.
        return {'error': f'cannot determine credits: {info["error"]}',
                'credit_exhausted': False}
    left = info.get('credits_left')
    if left is None:
        return None  # Some providers don't expose credits; let the call fail naturally
    if left <= VERIFIER_CREDIT_FLOOR:
        return {
            'error': f'{VERIFIER_PROVIDER} credit floor reached '
                     f'({left} ≤ {VERIFIER_CREDIT_FLOOR})',
            'credit_exhausted': True,
            'credits_left': left,
            'provider': VERIFIER_PROVIDER,
        }
    return None


def verify_email(email: str) -> dict[str, Any]:
    """Verify a single email through the configured provider.

    1 credit per call. Slow (1-3 sec) due to live SMTP probes.
    Returns Hunter-shaped dict so callers can swap providers transparently.
    """
    if not _is_configured():
        return {'email': email, 'result': 'unknown',
                'error': f'{VERIFIER_PROVIDER}_API_KEY not configured'}

    floor = _credit_floor_check()
    if floor:
        return {'email': email, 'result': 'unknown', **floor}

    email = (email or '').strip().lower()
    if not email or '@' not in email:
        return {'email': email, 'result': 'unknown', 'error': 'email is required'}

    if VERIFIER_PROVIDER == 'millionverifier':
        return _mv_verify_email(email)
    if VERIFIER_PROVIDER == 'zerobounce':
        return _zb_verify_email(email)
    if VERIFIER_PROVIDER == 'hunter':
        return _hunter_verify_email(email)
    return {'email': email, 'result': 'unknown',
            'error': f'unknown provider: {VERIFIER_PROVIDER}'}


def verify_emails_batch(emails: list[str]) -> dict[str, Any]:
    """Verify multiple emails sequentially with credit-floor short-circuit.

    If credits hit the floor mid-batch, returns partial results so the
    caller can send the verified subset.
    """
    delay_seconds = max(0.0, VERIFIER_BATCH_DELAY_MS / 1000.0)
    results = []
    safe = 0
    exhausted = False

    for email in emails:
        floor = _credit_floor_check()
        if floor:
            exhausted = True
            results.append({
                'email': email, 'result': 'unknown', 'safe_to_send': False,
                **floor,
            })
            for remaining in emails[len(results):]:
                results.append({
                    'email': remaining, 'result': 'unknown', 'safe_to_send': False,
                    'error': 'skipped: credit floor', 'credit_exhausted': True,
                })
            break

        r = verify_email(email)
        results.append(r)
        if r.get('safe_to_send'):
            safe += 1
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    info = account_info(force_refresh=False)
    return {
        'results': results,
        'verified_count': len(results),
        'safe_count': safe,
        'credit_exhausted': exhausted,
        'credits_left': info.get('credits_left'),
        'provider': VERIFIER_PROVIDER,
    }
