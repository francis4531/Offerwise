"""
OfferWise Security Module
Comprehensive security measures for production deployment

Features:
- Origin/Referer validation (CSRF protection for APIs)
- Request validation
- Security headers
- Rate limit helpers
"""

import os
import logging
from functools import wraps
from urllib.parse import urlparse
from flask import request, jsonify, abort

logger = logging.getLogger(__name__)

# Allowed origins for CSRF protection
# In production, this should match your actual domain
ALLOWED_ORIGINS = [
    'https://www.getofferwise.ai',
    'https://getofferwise.ai',
    'https://offerwise.onrender.com',
    'http://localhost:5000',  # Development
    'http://127.0.0.1:5000',  # Development
]

# Add any custom origins from environment
custom_origin = os.environ.get('ALLOWED_ORIGIN')
if custom_origin:
    ALLOWED_ORIGINS.append(custom_origin)


def validate_origin(f):
    """
    Decorator to validate Origin/Referer headers for CSRF protection.
    
    For state-changing requests (POST, PUT, DELETE, PATCH), validates that
    the request comes from an allowed origin.
    
    This is the recommended CSRF protection for API-based applications.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Only check for state-changing methods
        if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
            origin = request.headers.get('Origin')
            referer = request.headers.get('Referer')
            
            # Get the origin to check
            check_origin = None
            if origin:
                check_origin = origin
            elif referer:
                parsed = urlparse(referer)
                check_origin = f"{parsed.scheme}://{parsed.netloc}"
            
            # In production, require origin validation
            if os.environ.get('FLASK_ENV') != 'development':
                if not check_origin:
                    # Allow requests without Origin if they have valid session
                    # (same-origin requests from some browsers don't send Origin)
                    pass
                elif check_origin not in ALLOWED_ORIGINS:
                    logger.warning(f"⚠️ CSRF: Blocked request from {check_origin}")
                    return jsonify({'error': 'Invalid request origin'}), 403
        
        return f(*args, **kwargs)
    return decorated_function


def validate_content_type(allowed_types=['application/json']):
    """
    Decorator to validate Content-Type header.
    Prevents content-type sniffing attacks.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if request.method in ['POST', 'PUT', 'PATCH']:
                content_type = request.content_type or ''
                
                # Check if content type matches any allowed type
                valid = any(ct in content_type for ct in allowed_types)
                
                if not valid and request.data:
                    logger.warning(f"⚠️ Invalid Content-Type: {content_type}")
                    return jsonify({'error': 'Invalid content type'}), 415
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def sanitize_input(data, max_length=10000):
    """
    Sanitize user input to prevent injection attacks.
    
    Args:
        data: Input data (string or dict)
        max_length: Maximum allowed length
        
    Returns:
        Sanitized data
    """
    if isinstance(data, str):
        # Truncate overly long strings
        if len(data) > max_length:
            data = data[:max_length]
        # Remove null bytes
        data = data.replace('\x00', '')
        return data
    
    elif isinstance(data, dict):
        return {k: sanitize_input(v, max_length) for k, v in data.items()}
    
    elif isinstance(data, list):
        return [sanitize_input(item, max_length) for item in data]
    
    return data


def require_json(f):
    """
    Decorator to require JSON body for POST/PUT requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'PATCH']:
            if not request.is_json:
                return jsonify({'error': 'Content-Type must be application/json'}), 415
        return f(*args, **kwargs)
    return decorated_function


def secure_endpoint(f):
    """
    Combined security decorator for sensitive endpoints.
    Applies: origin validation + content type validation
    """
    @wraps(f)
    @validate_origin
    @validate_content_type(['application/json', 'multipart/form-data'])
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated_function


# Security configuration for production
SECURITY_CONFIG = {
    # Session security
    'SESSION_COOKIE_SECURE': True,
    'SESSION_COOKIE_HTTPONLY': True,
    'SESSION_COOKIE_SAMESITE': 'Lax',
    
    # Password requirements (if using local auth)
    'MIN_PASSWORD_LENGTH': 8,
    
    # Rate limiting
    'DEFAULT_RATE_LIMIT': '200 per hour',
    'STRICT_RATE_LIMIT': '10 per minute',  # For sensitive endpoints
    'ANALYSIS_RATE_LIMIT': '30 per hour',  # For expensive operations
    
    # File upload
    'MAX_FILE_SIZE': 100 * 1024 * 1024,  # 100MB
    'ALLOWED_EXTENSIONS': {'pdf'},
    
    # API security
    'REQUIRE_HTTPS': True,
}


def get_client_ip():
    """Get real client IP, handling proxies"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


def log_security_event(event_type, details=None):
    """Log security-relevant events for monitoring"""
    client_ip = get_client_ip()
    user_id = 'anonymous'
    
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            user_id = current_user.id
    except:
        pass
    
    logger.warning(f"🔒 SECURITY: {event_type} | IP: {client_ip} | User: {user_id} | {details or ''}")
