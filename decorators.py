"""
OfferWise Shared Decorators
=============================
Auth, admin, and API decorators used across blueprints and routes.
Extracted from app.py to enable clean blueprint imports.

Usage:
    from decorators import api_login_required, admin_required, api_admin_required
"""

import functools
import logging
from flask import request, jsonify
from flask_login import current_user

logger = logging.getLogger(__name__)


def api_login_required(f):
    """Like @login_required but returns JSON 401 instead of redirect for API routes."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required', 'login_required': True}), 401
        return f(*args, **kwargs)
    return decorated_function


def is_admin():
    """Check if current user is an admin."""
    if not current_user.is_authenticated:
        return False
    import os as _dos
    admin_emails = [e.strip() for e in _dos.environ.get('ADMIN_EMAILS', 'hello@getofferwise.ai,francis@getofferwise.ai').split(',') if e.strip()]
    return current_user.email in admin_emails


def admin_required(f):
    """Decorator: require admin access for page routes (redirect on failure)."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin():
            return "Access denied", 403
        return f(*args, **kwargs)
    return decorated_function


def api_admin_required(f):
    """Decorator: require admin access for API routes (JSON 403 on failure)."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin():
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function


def dev_only(f):
    """Gate endpoint to non-production environments only.
    Returns 404 in production so these routes are invisible to attackers."""
    import os
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if os.environ.get('FLASK_ENV') != 'development' and not is_admin():
            return jsonify({'error': 'Not found'}), 404
        return f(*args, **kwargs)
    return decorated_function
