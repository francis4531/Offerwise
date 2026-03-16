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
                # Decorator not set yet — just call the function directly
                return fn(*args, **kwargs)
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
                    return real_limiter.limit(rule)(fn)(*args, **kwargs)
                return wrapper
            return decorator
    return _DeferredLimiter()
