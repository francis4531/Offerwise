"""
OfferWise Shared Extensions
============================
Shared Flask extension instances used across all blueprints.
Import from here instead of app.py to avoid circular imports.

Usage:
    from extensions import db, login_manager, limiter
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress

# Database — created in models.py, re-exported here for convenience
# Note: db is the canonical instance from models.py
from models import db

# Auth
login_manager = LoginManager()

# Rate limiting — initialized without app, call limiter.init_app(app) in create_app
limiter = Limiter(key_func=get_remote_address, default_limits=[])

# Response compression
compress = Compress()
