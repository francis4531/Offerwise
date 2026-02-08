"""
OfferWise Database Models - Compatibility Layer
All models are defined in models.py. This file exists for backwards compatibility.
Any code importing from database.py will get the same models as models.py.
"""

# Re-export everything from models.py for backwards compatibility
from models import db, User, CreditTransaction

# Legacy aliases
# database.py used to define 'credits' column (now 'analysis_credits' in models.py)
# database.py used to define 'oauth_provider' column (now 'auth_provider' in models.py)
# All new code should import from models.py directly.
