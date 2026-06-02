"""
Blueprint Helpers — deferred decorators for extracted route blueprints.

Problem: Blueprint modules are imported before init_*_blueprint() sets the
auth decorator references. Decorators applied at module level try to call
None and crash.

Solution: Wrap each decorator in a lazy proxy that captures the function
at definition time but defers the actual decorator call to request time.
"""

from functools import wraps
from flask import request


class DeferredDecorator:
    """A decorator proxy that defers to the real decorator at call time."""
    
    def __init__(self, getter):
        """
        Args:
            getter: callable that returns the real decorator function.
                    Called on each request, so it picks up the value
                    set by init_*_blueprint().
        """
        self._getter = getter
    
    def __call__(self, fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            real_decorator = self._getter()
            if real_decorator is None:
                # Decorator not set yet — block access instead of silently passing
                from flask import jsonify, request
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Authentication required.'}), 401
                from flask import redirect, url_for
                return redirect('/login')
            # Apply the real decorator and call its result
            decorated = real_decorator(fn)
            return decorated(*args, **kwargs)
        return wrapper


def make_deferred_limiter(getter):
    """Create a deferred limiter that mimics limiter.limit('...')."""
    class _DeferredLimiter:
        def limit(self, rule):
            def decorator(fn):
                @wraps(fn)
                def wrapper(*args, **kwargs):
                    real_limiter = getter()
                    if real_limiter is None:
                        return fn(*args, **kwargs)
                    try:
                        return real_limiter.limit(rule)(fn)(*args, **kwargs)
                    except Exception as e:
                        # v5.88.17 (Path B Release 8c): RateLimitExceeded
                        # is an HTTPException — must let it bubble so Flask
                        # can convert it to a 429 response. Previous
                        # `except Exception` swallowed it and called fn()
                        # anyway, silently disabling brute-force protection
                        # across every blueprint that used _limiter.
                        from flask_limiter.errors import RateLimitExceeded
                        if isinstance(e, RateLimitExceeded):
                            raise
                        # Any other failure (Redis down, config errors,
                        # transient I/O) — log and let the request through.
                        # Better to serve than to 500 because of broken
                        # rate limiting.
                        return fn(*args, **kwargs)
                return wrapper
            return decorator
    return _DeferredLimiter()


# v5.88.16: Centralized email validator.
# Replaces the loose `'@' in email and '.' in email` checks scattered
# across route files (5 instances found in v5.88.15 audit).
# This one rejects: empty, '@nodomain.com', 'user@nodomain', '@.', etc.
def is_valid_email(email):
    """Return True only for plausibly-valid email addresses.

    Real RFC 5321 validation is overkill (and Resend will reject bad
    addresses at send-time anyway). This catches the obvious garbage:
    empty, missing @, missing local-part, missing domain, missing TLD.

    Used by auth, agent invite, inspector invite, waitlist signup,
    outreach add — anywhere we accept user-typed email input.
    """
    if not email or not isinstance(email, str):
        return False
    email = email.strip().lower()
    at = email.find('@')
    if at <= 0 or at == len(email) - 1:
        return False
    local, domain = email[:at], email[at+1:]
    if not local:
        return False
    if '.' not in domain or domain.startswith('.') or domain.endswith('.'):
        return False
    return True
