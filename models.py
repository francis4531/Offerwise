"""
OfferWise Database Models
SQLite database with user authentication, property storage, and usage tracking
"""

from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

db = SQLAlchemy()

class User(UserMixin, db.Model):
    """User accounts with authentication"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255))
    password_hash = db.Column(db.String(255))  # For email/password auth
    
    # OAuth fields
    google_id = db.Column(db.String(255), unique=True, index=True)
    apple_id = db.Column(db.String(255), unique=True, index=True)
    facebook_id = db.Column(db.String(255), unique=True, index=True)
    github_id = db.Column(db.String(255), unique=True, index=True)
    auth_provider = db.Column(db.String(50))  # 'google', 'apple', 'facebook', 'github'
    
    # Subscription
    tier = db.Column(db.String(50), default='free')  # 'free', 'starter', 'professional', 'enterprise'
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    subscription_status = db.Column(db.String(50), default='active')  # 'active', 'cancelled', 'past_due'
    subscription_end_date = db.Column(db.DateTime)
    analysis_credits = db.Column(db.Integer, default=0)  # Pay-per-use credits
    total_credits_purchased = db.Column(db.Integer, default=0)  # Lifetime credits purchased
    analyses_completed = db.Column(db.Integer, default=0)  # Lifetime analyses run
    
    # Buyer Preferences
    max_budget = db.Column(db.Integer, nullable=True)
    repair_tolerance = db.Column(db.String(50), nullable=True)  # 'Low', 'Moderate', 'High'
    biggest_regret = db.Column(db.Text, nullable=True)
    
    # Onboarding tracking - once completed, never redirect to onboarding again
    onboarding_completed = db.Column(db.Boolean, default=False)
    onboarding_completed_at = db.Column(db.DateTime)
    
    # Referral System
    referral_code = db.Column(db.String(50), unique=True, index=True)  # User's unique referral code
    referred_by_code = db.Column(db.String(50), index=True)  # Code used when they signed up
    referred_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))  # Who referred them
    total_referrals = db.Column(db.Integer, default=0)  # Count of successful referrals
    referral_tier = db.Column(db.Integer, default=0)  # Current tier level (0-4)
    referral_credits_earned = db.Column(db.Integer, default=0)  # Total credits from referrals
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    terms_accepted_at = db.Column(db.DateTime)  # When user accepted Terms of Service
    terms_version = db.Column(db.String(20), default='1.0')  # Track which version they accepted
    
    # Relationships
    properties = db.relationship('Property', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    usage_records = db.relationship('UsageRecord', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    consent_records = db.relationship('ConsentRecord', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    referrals_made = db.relationship('Referral', foreign_keys='Referral.referrer_id', backref='referrer', lazy='dynamic', cascade='all, delete-orphan')
    referred_by_relation = db.relationship('User', remote_side=[id], backref='referees')
    
    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify password"""
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)
    
    def get_tier_limits(self):
        """Get limits for current tier"""
        limits = {
            'free': {'properties_per_month': 3, 'storage_mb': 50},
            'starter': {'properties_per_month': 10, 'storage_mb': 200},
            'professional': {'properties_per_month': 50, 'storage_mb': 1000},
            'enterprise': {'properties_per_month': -1, 'storage_mb': -1}  # -1 = unlimited
        }
        return limits.get(self.tier, limits['free'])
    
    def get_current_usage(self):
        """Get current month's usage"""
        now = datetime.utcnow()
        start_of_month = datetime(now.year, now.month, 1)
        
        usage = UsageRecord.query.filter(
            UsageRecord.user_id == self.id,
            UsageRecord.month_start >= start_of_month
        ).first()
        
        if not usage:
            # Create new usage record for this month
            usage = UsageRecord(
                user_id=self.id,
                month_start=start_of_month,
                properties_analyzed=0
            )
            db.session.add(usage)
            db.session.commit()
        
        return usage
    
    def can_analyze_property(self):
        """Check if user can analyze another property"""
        limits = self.get_tier_limits()
        max_properties = limits['properties_per_month']
        
        if max_properties == -1:  # Unlimited
            return True
        
        usage = self.get_current_usage()
        return usage.properties_analyzed < max_properties
    
    def increment_usage(self):
        """Increment property analysis count"""
        usage = self.get_current_usage()
        usage.properties_analyzed += 1
        usage.last_analysis = datetime.utcnow()
        db.session.commit()
    
    def generate_referral_code(self):
        """Generate unique referral code"""
        if self.referral_code:
            return self.referral_code
        
        # Create code from name + random chars (e.g., "FRANCIS-7X9K")
        if self.name:
            name_part = ''.join(filter(str.isalnum, self.name.upper()))[:8]
        else:
            name_part = ''.join(filter(str.isalnum, self.email.split('@')[0].upper()))[:8]
        
        # Keep generating until unique
        max_attempts = 10
        for _ in range(max_attempts):
            random_part = secrets.token_urlsafe(3).replace('-', '').replace('_', '').upper()[:4]
            code = f"{name_part}-{random_part}"
            
            # Check if unique
            if not User.query.filter_by(referral_code=code).first():
                self.referral_code = code
                db.session.commit()
                return code
        
        # Fallback: fully random if can't generate unique name-based
        for _ in range(max_attempts):
            code = secrets.token_urlsafe(6).replace('-', '').replace('_', '').upper()[:8]
            if not User.query.filter_by(referral_code=code).first():
                self.referral_code = code
                db.session.commit()
                return code
        
        return None
    
    def get_referral_stats(self):
        """Get referral statistics for user"""
        return {
            'code': self.referral_code,
            'total_referrals': self.total_referrals,
            'current_tier': self.referral_tier,
            'credits_earned': self.referral_credits_earned,
            'next_tier': self._get_next_tier_info(),
            'referral_history': self._get_referral_history()
        }
    
    def _get_next_tier_info(self):
        """Calculate progress to next tier"""
        TIER_REQUIREMENTS = {
            0: 1, 1: 5, 2: 10, 3: 25
        }
        
        if self.referral_tier >= 4:
            return None  # Max tier reached
        
        next_tier = self.referral_tier + 1
        required = TIER_REQUIREMENTS.get(self.referral_tier, 1)
        progress = min(100, int((self.total_referrals / required) * 100))
        
        return {
            'tier': next_tier,
            'required': required,
            'current': self.total_referrals,
            'remaining': max(0, required - self.total_referrals),
            'progress_percent': progress
        }
    
    def _get_referral_history(self):
        """Get list of successful referrals"""
        referrals = Referral.query.filter_by(referrer_id=self.id).order_by(Referral.signup_date.desc()).all()
        return [{
            'name': ref.referee.name or ref.referee.email.split('@')[0],
            'email': ref.referee.email,
            'signup_date': ref.signup_date,
            'credits_awarded': 3,  # Standard per-referral credit
            'status': 'Active'
        } for ref in referrals]


class Property(db.Model):
    """Properties analyzed by users"""
    __tablename__ = 'properties'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Property details
    address = db.Column(db.String(500), nullable=False)
    price = db.Column(db.Float)
    
    # Analysis status
    status = db.Column(db.String(50), default='pending')  # 'pending', 'completed', 'failed'
    analyzed_at = db.Column(db.DateTime)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    documents = db.relationship('Document', backref='property', lazy='dynamic', cascade='all, delete-orphan')
    analyses = db.relationship('Analysis', backref='property', lazy='dynamic', cascade='all, delete-orphan')


class Document(db.Model):
    """Uploaded documents (PDFs) for properties"""
    __tablename__ = 'documents'
    
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False, index=True)
    
    # Document details
    document_type = db.Column(db.String(50), nullable=False)  # 'seller_disclosure', 'inspection_report'
    filename = db.Column(db.String(500), nullable=False)
    file_path = db.Column(db.String(1000), nullable=False)
    file_size_bytes = db.Column(db.Integer)
    
    # Extracted content
    extracted_text = db.Column(db.Text)
    
    # Timestamps
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class Analysis(db.Model):
    """Analysis results for properties"""
    __tablename__ = 'analyses'
    
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    status = db.Column(db.String(50), default='completed')
    offer_score = db.Column(db.Float, nullable=True)
    risk_tier = db.Column(db.String(50), nullable=True)
    
    # Analysis results (stored as JSON)
    result_json = db.Column(db.Text, default='{}')
    
    # Buyer profile used
    buyer_profile_json = db.Column(db.Text)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UsageRecord(db.Model):
    """Track monthly usage per user"""
    __tablename__ = 'usage_records'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Usage tracking
    month_start = db.Column(db.DateTime, nullable=False, index=True)
    properties_analyzed = db.Column(db.Integer, default=0)
    last_analysis = db.Column(db.DateTime)
    
    # Unique constraint: one record per user per month
    __table_args__ = (
        db.UniqueConstraint('user_id', 'month_start', name='unique_user_month'),
    )


class MagicLink(db.Model):
    """Passwordless magic link tokens"""
    __tablename__ = 'magic_links'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    token = db.Column(db.String(255), unique=True, nullable=False, index=True)
    
    # Expiration
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @staticmethod
    def create_link(email, expires_in_minutes=15):
        """Create a new magic link"""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(minutes=expires_in_minutes)
        
        link = MagicLink(
            email=email,
            token=token,
            expires_at=expires_at
        )
        db.session.add(link)
        db.session.commit()
        
        return link
    
    def is_valid(self):
        """Check if link is still valid"""
        return not self.used and datetime.utcnow() < self.expires_at
    
    def mark_used(self):
        """Mark link as used"""
        self.used = True
        db.session.commit()


class ConsentRecord(db.Model):
    """
    Track user consent for legal disclaimers and terms.
    
    CRITICAL for legal protection - tracks:
    - What disclaimer/terms they consented to
    - Which version
    - When they consented
    - IP address for audit trail
    """
    __tablename__ = 'consent_records'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Consent details
    consent_type = db.Column(db.String(50), nullable=False)  # 'terms', 'privacy', 'disclaimer', 'analysis_disclaimer'
    consent_version = db.Column(db.String(20), nullable=False)  # Version of the text they consented to
    consent_text_hash = db.Column(db.String(64))  # SHA-256 hash of the exact text shown
    
    # Audit trail
    consented_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ip_address = db.Column(db.String(45))  # IPv4 or IPv6
    user_agent = db.Column(db.String(500))  # Browser/device info
    
    # Context
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'))  # If consent was for specific analysis
    
    # Status
    revoked = db.Column(db.Boolean, default=False)
    revoked_at = db.Column(db.DateTime)
    
    def __repr__(self):
        return f'<ConsentRecord user={self.user_id} type={self.consent_type} version={self.consent_version}>'
    
    @staticmethod
    def has_current_consent(user_id, consent_type, required_version):
        """
        Check if user has valid consent for a specific type and version.
        
        Args:
            user_id: User ID
            consent_type: Type of consent ('disclaimer', 'terms', etc.)
            required_version: Minimum version required
            
        Returns:
            True if user has valid consent, False otherwise
        """
        consent = ConsentRecord.query.filter_by(
            user_id=user_id,
            consent_type=consent_type,
            revoked=False
        ).order_by(ConsentRecord.consented_at.desc()).first()
        
        if not consent:
            return False
        
        # Check if version is current
        return consent.consent_version >= required_version
    
    @staticmethod
    def record_consent(user_id, consent_type, consent_version, consent_text, ip_address=None, user_agent=None, analysis_id=None):
        """
        Record a new consent.
        
        Returns:
            ConsentRecord object
        """
        import hashlib
        
        # Hash the consent text for verification
        text_hash = hashlib.sha256(consent_text.encode('utf-8')).hexdigest()
        
        consent = ConsentRecord(
            user_id=user_id,
            consent_type=consent_type,
            consent_version=consent_version,
            consent_text_hash=text_hash,
            ip_address=ip_address,
            user_agent=user_agent,
            analysis_id=analysis_id
        )
        
        db.session.add(consent)
        db.session.commit()
        
        return consent


class EmailRegistry(db.Model):
    """
    Permanent registry of all emails that have ever signed up.
    Prevents credit abuse by tracking which emails have received free credits,
    even if the user deletes their account.
    
    This table is NEVER deleted - only updated to track deletions.
    """
    __tablename__ = 'email_registry'
    
    email = db.Column(db.String(255), primary_key=True, nullable=False)
    
    # Tracking first signup
    first_signup_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Credit tracking (PERMANENT - survives account deletion)
    has_received_free_credit = db.Column(db.Boolean, default=False, nullable=False)
    free_credit_given_at = db.Column(db.DateTime, nullable=True)
    
    # Account deletion tracking (for abuse detection)
    times_deleted = db.Column(db.Integer, default=0, nullable=False)
    last_deleted_at = db.Column(db.DateTime, nullable=True)
    
    # Credit preservation (for legitimate users testing or re-creating accounts)
    saved_credits = db.Column(db.Integer, default=0, nullable=False)  # Credits at time of deletion
    credits_saved_at = db.Column(db.DateTime, nullable=True)
    
    # Abuse flags
    is_flagged_abuse = db.Column(db.Boolean, default=False, nullable=False)
    abuse_notes = db.Column(db.Text, nullable=True)
    
    # Metadata
    last_updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @staticmethod
    def register_email(email):
        """
        Register an email in the system (called on first signup).
        Returns: (email_registry, is_new)
        """
        existing = EmailRegistry.query.filter_by(email=email).first()
        
        if existing:
            # Email has been used before
            return (existing, False)
        else:
            # Brand new email
            registry = EmailRegistry(
                email=email,
                first_signup_date=datetime.utcnow()
            )
            db.session.add(registry)
            db.session.commit()
            return (registry, True)
    
    @staticmethod
    def can_receive_free_credit(email):
        """
        Check if this email can receive a free credit on signup.
        Rules:
        - Always give 1 free credit on signup
        - UNLESS flagged for abuse (3+ account deletions = blocked entirely)
        """
        registry = EmailRegistry.query.filter_by(email=email).first()
        
        if not registry:
            return (True, "new_email")
        
        if registry.is_flagged_abuse:
            return (False, "account_blocked")
        
        return (True, "eligible")
    
    @staticmethod
    def is_blocked(email):
        """Check if this email is blocked from creating accounts (3+ deletions)."""
        registry = EmailRegistry.query.filter_by(email=email).first()
        if not registry:
            return False
        return registry.is_flagged_abuse
    
    @staticmethod
    def give_free_credit(email):
        """
        Mark that this email has received its free credit.
        This is PERMANENT - survives account deletion.
        """
        registry = EmailRegistry.query.filter_by(email=email).first()
        
        if not registry:
            # Create registry entry
            registry = EmailRegistry(
                email=email,
                first_signup_date=datetime.utcnow()
            )
            db.session.add(registry)
        
        registry.has_received_free_credit = True
        registry.free_credit_given_at = datetime.utcnow()
        db.session.commit()
        
        return registry
    
    @staticmethod
    def track_deletion(email, credits_to_save=0):
        """
        Track that a user with this email deleted their account.
        ALSO saves their credits so they can be restored if they sign up again.
        This helps detect abuse (multiple delete/recreate cycles).
        """
        registry = EmailRegistry.query.filter_by(email=email).first()
        
        if not registry:
            # Email not in registry yet - create it
            registry = EmailRegistry(
                email=email,
                first_signup_date=datetime.utcnow()
            )
            db.session.add(registry)
        
        registry.times_deleted += 1
        registry.last_deleted_at = datetime.utcnow()
        
        # SAVE CREDITS! (for legitimate testing/re-creation)
        if credits_to_save > 0:
            registry.saved_credits = credits_to_save
            registry.credits_saved_at = datetime.utcnow()
            
            # Log credit preservation
            import logging
            logging.info(f"💰 Preserved {credits_to_save} credits for {email}")
        
        # Flag as abuse if deleted 3+ times
        if registry.times_deleted >= 3:
            registry.is_flagged_abuse = True
            registry.abuse_notes = f"Account deleted {registry.times_deleted} times. Possible credit farming abuse."
            
            # Log for monitoring
            import logging
            logging.warning(f"⚠️ ABUSE DETECTED: Email {email} deleted account {registry.times_deleted} times - FLAGGED")
        
        db.session.commit()
        
        return registry


class Referral(db.Model):
    """Track referrals between users"""
    __tablename__ = 'referrals'
    
    id = db.Column(db.Integer, primary_key=True)
    referrer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)  # Who referred
    referee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)   # Who was referred
    referral_code = db.Column(db.String(50), nullable=False, index=True)
    
    # Tracking
    signup_date = db.Column(db.DateTime, default=datetime.utcnow)
    credits_awarded = db.Column(db.Boolean, default=False)  # Whether credits have been given
    first_analysis_date = db.Column(db.DateTime, nullable=True)  # When referee did first analysis
    
    # Relationships
    referee = db.relationship('User', foreign_keys=[referee_id], backref='referral_source')
    
    def __repr__(self):
        return f'<Referral {self.referrer_id} → {self.referee_id}>'


class ReferralReward(db.Model):
    """Track referral credit rewards"""
    __tablename__ = 'referral_rewards'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    referral_id = db.Column(db.Integer, db.ForeignKey('referrals.id'), nullable=True)
    
    # Reward details
    reward_type = db.Column(db.String(50), nullable=False)  # 'signup', 'tier_bonus', 'milestone'
    credits_awarded = db.Column(db.Integer, nullable=False)
    tier = db.Column(db.Integer, nullable=True)  # Which tier unlocked (1-4) if tier_bonus
    description = db.Column(db.String(255))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='referral_rewards')
    referral = db.relationship('Referral', backref='rewards')
    
    def __repr__(self):
        return f'<ReferralReward user={self.user_id} type={self.reward_type} credits={self.credits_awarded}>'


# Referral tier configuration
REFERRAL_TIERS = {
    0: {
        'name': 'Starter',
        'icon': '🌱',
        'referrals_required': 0,
        'signup_credits': 1,
        'bonus_credits': 0,
    },
    1: {
        'name': 'Active Referrer',
        'icon': '⭐',
        'referrals_required': 1,
        'signup_credits': 1,
        'bonus_credits': 0,
    },
    2: {
        'name': 'Pro Analyzer',
        'icon': '🏆',
        'referrals_required': 5,
        'signup_credits': 1,
        'bonus_credits': 20,
    },
    3: {
        'name': 'Expert',
        'icon': '💎',
        'referrals_required': 10,
        'signup_credits': 1,
        'bonus_credits': 50,
    },
    4: {
        'name': 'Ambassador',
        'icon': '👑',
        'referrals_required': 25,
        'signup_credits': 1,
        'bonus_credits': 100,
    }
}


class Comparison(db.Model):
    """Track property comparisons - compare 3 properties side-by-side"""
    __tablename__ = 'comparisons'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Properties being compared (up to 3)
    property1_address = db.Column(db.String(500))
    property1_listing_url = db.Column(db.String(1000))
    property1_price = db.Column(db.Integer)
    property1_analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=True)
    
    property2_address = db.Column(db.String(500))
    property2_listing_url = db.Column(db.String(1000))
    property2_price = db.Column(db.Integer)
    property2_analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=True)
    
    property3_address = db.Column(db.String(500), nullable=True)
    property3_listing_url = db.Column(db.String(1000), nullable=True)
    property3_price = db.Column(db.Integer, nullable=True)
    property3_analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=True)
    
    # Comparison results
    comparison_data = db.Column(db.JSON)  # Stores quick scan results for all 3
    winner_property = db.Column(db.Integer)  # 1, 2, or 3
    rankings = db.Column(db.JSON)  # [{rank: 1, property: 1, score: 87}, ...]
    
    # Status
    status = db.Column(db.String(50), default='pending')  # 'pending', 'processing', 'completed', 'failed'
    error_message = db.Column(db.Text)
    
    # Credits
    credits_used = db.Column(db.Integer, default=1)  # 1 credit for comparison
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    
    # Relationships
    user = db.relationship('User', backref='comparisons')
    
    def __repr__(self):
        return f'<Comparison {self.id} by user {self.user_id}>'


class TurkSession(db.Model):
    """Track Mechanical Turk / crowdsourced testing sessions (v5.54.2)"""
    __tablename__ = 'turk_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Turk identifiers
    turk_id = db.Column(db.String(100), index=True)  # Worker ID from MTurk
    task_id = db.Column(db.String(100), index=True)  # HIT/Task ID
    
    # Session tracking
    session_token = db.Column(db.String(50), unique=True, index=True)  # Our internal token
    completion_code = db.Column(db.String(20))  # Code shown to user for payment verification
    
    # Timing
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    time_spent_seconds = db.Column(db.Integer, default=0)
    
    # Progress tracking
    actions = db.Column(db.JSON, default=list)  # List of actions: [{action, timestamp}, ...]
    current_step = db.Column(db.String(50))  # upload, analyzing, results
    
    # Completion milestones
    uploaded_disclosure = db.Column(db.Boolean, default=False)
    uploaded_inspection = db.Column(db.Boolean, default=False)
    started_analysis = db.Column(db.Boolean, default=False)
    viewed_results = db.Column(db.Boolean, default=False)
    viewed_risk_dna = db.Column(db.Boolean, default=False)
    viewed_transparency = db.Column(db.Boolean, default=False)
    viewed_decision_path = db.Column(db.Boolean, default=False)
    is_complete = db.Column(db.Boolean, default=False)
    
    # Feedback (optional survey)
    rating = db.Column(db.Integer)  # 1-5 stars
    feedback = db.Column(db.Text)
    would_pay = db.Column(db.Boolean)
    confusion_points = db.Column(db.Text)
    
    # Browser/device info
    user_agent = db.Column(db.String(500))
    screen_width = db.Column(db.Integer)
    screen_height = db.Column(db.Integer)
    
    def __repr__(self):
        return f'<TurkSession {self.turk_id}:{self.task_id} - {"✅" if self.is_complete else "⏳"}>'


class Bug(db.Model):
    """Track bugs and issues (v5.54.20)"""
    __tablename__ = 'bugs'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Bug details
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    steps_to_reproduce = db.Column(db.Text)
    expected_behavior = db.Column(db.Text)
    actual_behavior = db.Column(db.Text)
    
    # Classification
    severity = db.Column(db.String(20), default='medium')  # critical, high, medium, low
    category = db.Column(db.String(50))  # ui, api, analysis, auth, upload, etc.
    
    # Status tracking
    status = db.Column(db.String(20), default='open')  # open, in_progress, fixed, wont_fix, duplicate
    
    # Version info
    version_reported = db.Column(db.String(20))
    version_fixed = db.Column(db.String(20))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    fixed_at = db.Column(db.DateTime)
    
    # Source
    reported_by = db.Column(db.String(100))  # 'auto', 'user', 'claude', etc.
    error_message = db.Column(db.Text)
    stack_trace = db.Column(db.Text)
    
    # Resolution
    fix_notes = db.Column(db.Text)
    
    # AI Analysis (v5.54.24)
    ai_analysis = db.Column(db.Text)  # AI's analysis of the bug
    ai_suggested_fix = db.Column(db.Text)  # AI's suggested code fix
    ai_confidence = db.Column(db.String(20))  # high, medium, low
    ai_analyzed_at = db.Column(db.DateTime)
    ai_fix_approved = db.Column(db.Boolean, default=False)  # Human approved the fix
    
    def __repr__(self):
        return f'<Bug #{self.id}: {self.title} [{self.status}]>'
    
    def to_dict(self):
        result = {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'steps_to_reproduce': self.steps_to_reproduce,
            'expected_behavior': self.expected_behavior,
            'actual_behavior': self.actual_behavior,
            'severity': self.severity,
            'category': self.category,
            'status': self.status,
            'version_reported': self.version_reported,
            'version_fixed': self.version_fixed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'fixed_at': self.fixed_at.isoformat() if self.fixed_at else None,
            'reported_by': self.reported_by,
            'error_message': self.error_message,
            'fix_notes': self.fix_notes,
        }
        # AI columns may not exist if migration hasn't run
        try:
            result['ai_analysis'] = self.ai_analysis
            result['ai_suggested_fix'] = self.ai_suggested_fix
            result['ai_confidence'] = self.ai_confidence
            result['ai_analyzed_at'] = self.ai_analyzed_at.isoformat() if self.ai_analyzed_at else None
            result['ai_fix_approved'] = self.ai_fix_approved
        except Exception:
            result['ai_analysis'] = None
            result['ai_suggested_fix'] = None
            result['ai_confidence'] = None
            result['ai_analyzed_at'] = None
            result['ai_fix_approved'] = False
        return result


class PMFSurvey(db.Model):
    """Sean Ellis PMF survey responses"""
    __tablename__ = 'pmf_surveys'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    email = db.Column(db.String(255), index=True)  # Backup if no user_id
    
    # Sean Ellis question: "How would you feel if you could no longer use OfferWise?"
    disappointment = db.Column(db.String(50))  # 'very', 'somewhat', 'not'
    
    # Follow-up questions
    main_benefit = db.Column(db.Text)  # "What is the main benefit you get from OfferWise?"
    improvement = db.Column(db.Text)  # "How can we improve OfferWise for you?"
    use_case = db.Column(db.Text)  # "What do you primarily use OfferWise for?"
    would_recommend = db.Column(db.Boolean)  # "Would you recommend OfferWise?"
    recommend_to = db.Column(db.Text)  # "Who would you recommend it to?"
    
    # Context
    analyses_at_survey = db.Column(db.Integer)  # How many analyses when surveyed
    trigger = db.Column(db.String(50))  # 'first_analysis', 'third_analysis', 'manual'
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'email': self.email,
            'disappointment': self.disappointment,
            'main_benefit': self.main_benefit,
            'improvement': self.improvement,
            'use_case': self.use_case,
            'would_recommend': self.would_recommend,
            'recommend_to': self.recommend_to,
            'analyses_at_survey': self.analyses_at_survey,
            'trigger': self.trigger,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class QuickFeedback(db.Model):
    """Lightweight in-app feedback from the persistent feedback tab"""
    __tablename__ = 'quick_feedback'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    email = db.Column(db.String(255))
    
    reaction = db.Column(db.String(20))  # 'love', 'like', 'meh', 'dislike'
    message = db.Column(db.Text)  # Optional free-text
    page = db.Column(db.String(100))  # Which page they were on
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'reaction': self.reaction,
            'message': self.message,
            'page': self.page,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class ExitSurvey(db.Model):
    """Exit survey for users who don't complete analysis"""
    __tablename__ = 'exit_surveys'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    session_id = db.Column(db.String(255), index=True)  # For anonymous users
    
    # Exit reason (multiple choice)
    exit_reason = db.Column(db.String(100))  # 'no_documents', 'too_expensive', 'confusing', 'found_alternative', 'just_browsing', 'other'
    exit_reason_other = db.Column(db.Text)  # If 'other' selected
    
    # Where they dropped off
    exit_page = db.Column(db.String(100))  # 'upload', 'pricing', 'onboarding', 'analysis'
    
    # Would they come back?
    would_return = db.Column(db.Boolean)
    what_would_help = db.Column(db.Text)  # "What would make you come back?"
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'exit_reason': self.exit_reason,
            'exit_reason_other': self.exit_reason_other,
            'exit_page': self.exit_page,
            'would_return': self.would_return,
            'what_would_help': self.what_would_help,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Subscriber(db.Model):
    """
    Email subscribers for lead capture (non-users who want updates/guides).
    """
    __tablename__ = 'subscribers'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    source = db.Column(db.String(100), nullable=True)  # Where they signed up (landing_page, pricing, etc.)
    subscribed_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'source': self.source,
            'subscribed_at': self.subscribed_at.isoformat() if self.subscribed_at else None,
            'is_active': self.is_active
        }


class ShareLink(db.Model):
    """Shareable analysis summary links for 'Get a Second Opinion' feature"""
    __tablename__ = 'share_links'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # URL-safe token (unique identifier in the share URL)
    token = db.Column(db.String(32), unique=True, nullable=False, index=True)
    
    # Who shared it
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)
    
    # Personalization
    sharer_name = db.Column(db.String(100))
    recipient_name = db.Column(db.String(100))
    personal_note = db.Column(db.String(280))
    
    # Snapshot of visible data at time of sharing (frozen, won't change if re-analyzed)
    snapshot_json = db.Column(db.Text, nullable=False)
    
    # Analytics
    view_count = db.Column(db.Integer, default=0)
    first_viewed_at = db.Column(db.DateTime)
    
    # Reactions: JSON array of {reaction, timestamp, ip_hash}
    reactions_json = db.Column(db.Text)
    
    # Status
    is_active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('share_links', lazy='dynamic'))
    property = db.relationship('Property')
    
    @staticmethod
    def create_link(user_id, property_id, snapshot, sharer_name=None, recipient_name=None, personal_note=None):
        """Create a new share link with a unique token"""
        token = secrets.token_urlsafe(16)  # 22 chars, URL-safe
        expires_at = datetime.utcnow() + timedelta(days=90)
        
        link = ShareLink(
            token=token,
            user_id=user_id,
            property_id=property_id,
            sharer_name=sharer_name,
            personal_note=personal_note,
            recipient_name=recipient_name,
            snapshot_json=snapshot,
            expires_at=expires_at
        )
        db.session.add(link)
        db.session.commit()
        
        return link
    
    def is_valid(self):
        """Check if link is still active and not expired"""
        return self.is_active and (self.expires_at is None or datetime.utcnow() < self.expires_at)
    
    def record_view(self):
        """Increment view counter"""
        self.view_count = (self.view_count or 0) + 1
        if not self.first_viewed_at:
            self.first_viewed_at = datetime.utcnow()
        db.session.commit()
    
    def add_reaction(self, reaction, ip_hash):
        """Add a reaction from a viewer"""
        import json
        reactions = json.loads(self.reactions_json) if self.reactions_json else []
        reactions.append({
            'reaction': reaction,
            'timestamp': datetime.utcnow().isoformat(),
            'ip_hash': ip_hash
        })
        self.reactions_json = json.dumps(reactions)
        db.session.commit()


class SupportShare(db.Model):
    """User-initiated 'Share with OfferWise support' records.

    Created when a user explicitly requests help from the OfferWise team.
    Stores a frozen snapshot of their analysis at share time plus their message.
    Admin-only access — never exposed to other users.
    """
    __tablename__ = 'support_shares'

    id = db.Column(db.Integer, primary_key=True)

    # Who shared
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)

    # User's message / context for their request
    user_message = db.Column(db.String(1000))

    # Frozen snapshot JSON (same structure as ShareLink.snapshot_json + full result)
    snapshot_json = db.Column(db.Text, nullable=False)   # summary fields
    full_result_json = db.Column(db.Text, nullable=False)  # complete analysis result
    findings_json = db.Column(db.Text)                   # parsed inspection findings

    # Admin workflow
    status = db.Column(db.String(50), default='open')   # 'open', 'reviewed', 'resolved'
    admin_notes = db.Column(db.Text)
    reviewed_at = db.Column(db.DateTime)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = db.relationship('User', backref=db.backref('support_shares', lazy='dynamic'))
    property = db.relationship('Property')


class AICallLog(db.Model):
    """Persistent log of every Anthropic API call — survives server restarts.

    Written immediately after each messages.create() response so cost data
    is never lost when Render redeploys or restarts the instance.
    """
    __tablename__ = 'ai_call_logs'

    id            = db.Column(db.Integer, primary_key=True)
    ts            = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    endpoint      = db.Column(db.String(100), nullable=False, index=True)
    model         = db.Column(db.String(100), nullable=False)
    input_tokens  = db.Column(db.Integer, default=0)
    output_tokens = db.Column(db.Integer, default=0)
    latency_ms    = db.Column(db.Float, default=0)
    cost_usd      = db.Column(db.Float, default=0.0)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    context_note  = db.Column(db.String(200))


class EmailSendLog(db.Model):
    """Append-only log of every email sent via Resend. One row per send.
    Written by email_service.send_email() so the cost dashboard has real counts.
    """
    __tablename__ = 'email_send_log'

    id         = db.Column(db.Integer, primary_key=True)
    ts         = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    to_email   = db.Column(db.String(255), nullable=False, index=True)
    email_type = db.Column(db.String(50),  nullable=False, index=True)  # welcome, drip_1..5, receipt, analysis_complete, market_intel
    subject    = db.Column(db.String(255))
    resend_id  = db.Column(db.String(100))   # ID returned by Resend API
    success    = db.Column(db.Boolean, default=True, nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)


class CreditTransaction(db.Model):
    """Transaction history for credits (purchases and usage)"""
    __tablename__ = 'credit_transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Stripe fields
    payment_intent_id = db.Column(db.String(255), unique=True, index=True)
    
    # Transaction details
    plan_id = db.Column(db.String(50))  # 'single', 'bundle_5', 'bundle_10', 'usage'
    amount = db.Column(db.Float)  # Dollar amount
    credits = db.Column(db.Integer)  # Credits added (positive) or used (negative)
    
    # Status
    status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    failure_reason = db.Column(db.Text)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    completed_at = db.Column(db.DateTime)
    
    def __repr__(self):
        return f'<Transaction {self.id}: {self.credits} credits - {self.status}>'


class Waitlist(db.Model):
    """Community waitlist signups — tracks interest before building."""
    __tablename__ = 'waitlist'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    feature = db.Column(db.String(50), nullable=False, default='community')  # community, etc
    source = db.Column(db.String(100))  # truth-check-results, homepage, etc
    referrer = db.Column(db.String(500))
    
    # Context from their session
    had_result = db.Column(db.Boolean, default=False)
    result_score = db.Column(db.Integer)
    result_address = db.Column(db.String(500))  # Address they checked (for personalization)
    result_grade = db.Column(db.String(5))       # Grade they received
    result_exposure = db.Column(db.Integer)       # Dollar exposure from Risk Check
    result_zip = db.Column(db.String(10), index=True)  # ZIP code (for nearby listings drip)
    result_city = db.Column(db.String(100))
    result_state = db.Column(db.String(2))
    
    # Drip campaign tracking
    drip_step = db.Column(db.Integer, default=0)           # 0=not started, 1-5=email sent
    drip_last_sent_at = db.Column(db.DateTime)
    drip_completed = db.Column(db.Boolean, default=False)  # True after email 5
    
    # Unsubscribe
    unsubscribe_token = db.Column(db.String(64), unique=True, index=True)
    email_unsubscribed = db.Column(db.Boolean, default=False)
    unsubscribed_at = db.Column(db.DateTime)
    
    # Status
    notified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        return f'<Waitlist {self.id}: {self.email} for {self.feature}>'


# ============================================================
# GTM (Go-To-Market) Models — Reddit Scout + Conversion Intel
# ============================================================

class GTMScannedThread(db.Model):
    """Threads scanned by the GTM Scout from Reddit, BiggerPockets, etc."""
    __tablename__ = 'gtm_scanned_threads'
    
    id = db.Column(db.Integer, primary_key=True)
    reddit_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    subreddit = db.Column(db.String(100), nullable=False, index=True)
    platform = db.Column(db.String(30), default='reddit', index=True)  # reddit, biggerpockets
    title = db.Column(db.Text, nullable=False)
    selftext = db.Column(db.Text)
    author = db.Column(db.String(100))
    reddit_score = db.Column(db.Integer, default=0)
    num_comments = db.Column(db.Integer, default=0)
    url = db.Column(db.String(500))
    created_utc = db.Column(db.DateTime)
    
    # Scoring
    keyword_score = db.Column(db.Integer, default=0)
    ai_score = db.Column(db.Integer, default=0)
    ai_reasoning = db.Column(db.Text)
    ai_topics = db.Column(db.Text)  # JSON array
    
    # Status: scanned, low_intent, below_threshold, qualified
    status = db.Column(db.String(30), default='scanned', index=True)
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    drafts = db.relationship('GTMRedditDraft', backref='thread', lazy='dynamic',
                             cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<GTMThread {self.reddit_id} [{self.status}] score={self.ai_score}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'reddit_id': self.reddit_id,
            'subreddit': self.subreddit,
            'platform': self.platform or 'reddit',
            'title': self.title,
            'selftext': (self.selftext or '')[:500],
            'author': self.author,
            'reddit_score': self.reddit_score,
            'num_comments': self.num_comments,
            'url': self.url,
            'keyword_score': self.keyword_score,
            'ai_score': self.ai_score,
            'ai_reasoning': self.ai_reasoning,
            'ai_topics': self.ai_topics,
            'status': self.status,
            'scanned_at': self.scanned_at.isoformat() if self.scanned_at else None,
        }


class GTMRedditDraft(db.Model):
    """Reply drafts generated by the Reddit Scout for human review."""
    __tablename__ = 'gtm_reddit_drafts'
    
    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey('gtm_scanned_threads.id'), nullable=False, index=True)
    
    # Generated content
    draft_text = db.Column(db.Text, nullable=False)
    strategy = db.Column(db.String(50))  # helpful_with_mention, helpful_only
    tone = db.Column(db.String(50))  # empathetic, experienced, technical
    mention_type = db.Column(db.String(30))  # natural, none
    
    # Review
    edited_text = db.Column(db.Text)  # Admin-edited version
    status = db.Column(db.String(20), default='pending', index=True)  # pending, approved, skipped, posted
    skip_reason = db.Column(db.String(200))
    reviewed_at = db.Column(db.DateTime)
    posted_at = db.Column(db.DateTime)
    posted_url = db.Column(db.String(500))  # URL where the comment was actually posted
    reddit_comment_id = db.Column(db.String(20))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        return f'<GTMDraft {self.id} [{self.status}] for thread={self.thread_id}>'
    
    def to_dict(self):
        thread = self.thread
        return {
            'id': self.id,
            'thread_id': self.thread_id,
            'draft_text': self.draft_text,
            'edited_text': self.edited_text,
            'strategy': self.strategy,
            'tone': self.tone,
            'mention_type': self.mention_type,
            'status': self.status,
            'skip_reason': self.skip_reason,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'posted_at': self.posted_at.isoformat() if self.posted_at else None,
            'posted_url': self.posted_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'thread': thread.to_dict() if thread else None,
        }


class GTMScanRun(db.Model):
    """Track each Reddit Scout scan cycle."""
    __tablename__ = 'gtm_scan_runs'
    
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, nullable=False, index=True)
    finished_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='running')  # running, completed, failed
    
    posts_scanned = db.Column(db.Integer, default=0)
    posts_filtered = db.Column(db.Integer, default=0)
    posts_scored = db.Column(db.Integer, default=0)
    drafts_created = db.Column(db.Integer, default=0)
    errors = db.Column(db.Integer, default=0)
    error_detail = db.Column(db.Text)
    
    def __repr__(self):
        return f'<GTMScanRun {self.id} [{self.status}] at {self.started_at}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'status': self.status,
            'posts_scanned': self.posts_scanned,
            'posts_filtered': self.posts_filtered,
            'posts_scored': self.posts_scored,
            'drafts_created': self.drafts_created,
            'errors': self.errors,
        }


class GTMSubredditPost(db.Model):
    """Daily content posts for r/offerwiseAi subreddit."""
    __tablename__ = 'gtm_subreddit_posts'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Content
    title = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, nullable=False)
    edited_body = db.Column(db.Text)  # Admin-edited version
    
    # Classification
    pillar = db.Column(db.String(50), nullable=False)  # what_were_seeing, did_you_know, real_numbers, red_flag_friday, first_timer_tuesday, community_qa
    pillar_label = db.Column(db.String(100))  # Human-readable label
    flair = db.Column(db.String(50))  # Reddit flair suggestion
    
    # Data backing
    data_summary = db.Column(db.Text)  # JSON: the aggregate stats used to generate this post
    
    # Schedule & status
    scheduled_date = db.Column(db.Date, nullable=False, index=True)  # Which day this is for
    status = db.Column(db.String(20), default='draft', index=True)  # draft, approved, posted, skipped
    skip_reason = db.Column(db.String(200))
    
    # Posting tracking
    posted_at = db.Column(db.DateTime)
    posted_url = db.Column(db.String(500))
    topic_key = db.Column(db.String(100), index=True)  # Dedup: e.g. "real_numbers:hvac" or "red_flag:water_damage"
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<GTMPost {self.id} [{self.status}] {self.pillar} for {self.scheduled_date}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'body': self.body,
            'edited_body': self.edited_body,
            'pillar': self.pillar,
            'pillar_label': self.pillar_label,
            'flair': self.flair,
            'data_summary': self.data_summary,
            'scheduled_date': self.scheduled_date.isoformat() if self.scheduled_date else None,
            'status': self.status,
            'skip_reason': self.skip_reason,
            'posted_at': self.posted_at.isoformat() if self.posted_at else None,
            'posted_url': self.posted_url,
            'topic_key': self.topic_key,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class GTMFunnelEvent(db.Model):
    """Local funnel event tracking (always-on, independent of GA4)."""
    __tablename__ = 'gtm_funnel_events'
    
    id = db.Column(db.Integer, primary_key=True)
    stage = db.Column(db.String(30), nullable=False, index=True)
    source = db.Column(db.String(100), default='direct', index=True)
    medium = db.Column(db.String(100), default='none')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    session_id = db.Column(db.String(100))
    metadata_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        return f'<GTMFunnel {self.stage} from {self.source} at {self.created_at}>'


class GTMAdPerformance(db.Model):
    """Daily ad performance metrics per channel."""
    __tablename__ = 'gtm_ad_performance'
    
    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(50), nullable=False, index=True)  # google_ads, reddit_ads
    date = db.Column(db.Date, nullable=False, index=True)
    
    impressions = db.Column(db.Integer, default=0)
    clicks = db.Column(db.Integer, default=0)
    spend = db.Column(db.Numeric(10, 2), default=0)
    conversions = db.Column(db.Integer, default=0)
    revenue = db.Column(db.Numeric(10, 2), default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)
    
    __table_args__ = (
        db.UniqueConstraint('channel', 'date', name='uq_channel_date'),
    )
    
    def __repr__(self):
        return f'<GTMAd {self.channel} {self.date} spend=${self.spend}>'


class GTMTargetSubreddit(db.Model):
    """Admin-managed list of target communities for Community Scout."""
    __tablename__ = 'gtm_target_subreddits'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  # e.g. "FirstTimeHomeBuyer" or "First-Time Home Buyer"
    platform = db.Column(db.String(30), default='reddit', index=True)  # reddit, biggerpockets
    enabled = db.Column(db.Boolean, default=True, index=True)
    priority = db.Column(db.Integer, default=5)  # 1=highest, 10=lowest (for sort order)
    notes = db.Column(db.String(300))  # Admin notes: "High intent, CA focused"
    url = db.Column(db.String(500))  # For BP: direct forum URL
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('name', 'platform', name='uq_target_name_platform'),
    )
    
    def __repr__(self):
        prefix = 'r/' if (self.platform or 'reddit') == 'reddit' else 'BP:'
        return f'<GTMTarget {prefix}{self.name} enabled={self.enabled}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'platform': self.platform or 'reddit',
            'enabled': self.enabled,
            'priority': self.priority,
            'notes': self.notes,
            'url': self.url,
            'added_at': self.added_at.isoformat() if self.added_at else None,
        }


# =============================================================================
# =============================================================================
# LISTING PREFERENCES (v5.62.85) — Preference learning from save/dismiss
# =============================================================================

class ListingPreference(db.Model):
    """Tracks user save/dismiss actions on nearby listings for preference learning."""
    __tablename__ = 'listing_preferences'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    listing_hash = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(10), nullable=False)  # 'save' or 'dismiss'
    
    # Listing snapshot at time of action
    zip_code = db.Column(db.String(10))
    price = db.Column(db.Integer)
    bedrooms = db.Column(db.Integer)
    bathrooms = db.Column(db.Float)
    sqft = db.Column(db.Integer)
    year_built = db.Column(db.Integer)
    days_on_market = db.Column(db.Integer)
    risk_tier = db.Column(db.String(20))
    opportunity_score = db.Column(db.Integer)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'listing_hash', name='uq_user_listing'),
    )


# =============================================================================
# MARKET SNAPSHOT (v5.62.92) — Nightly market intelligence accumulation
# =============================================================================

class MarketSnapshot(db.Model):
    """Nightly snapshot of market conditions for a user's ZIP code.
    
    One row per user per day. Stores listings seen, market stats,
    deltas from previous snapshot, and alerts generated.
    This is the memory that powers the Market Pulse, Living Analysis,
    and proactive email alerts.
    """
    __tablename__ = 'market_snapshots'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    zip_code = db.Column(db.String(10), nullable=False, index=True)
    snapshot_date = db.Column(db.Date, nullable=False, index=True)
    
    # Market stats at time of snapshot
    median_price = db.Column(db.Integer)
    active_inventory = db.Column(db.Integer)
    avg_dom = db.Column(db.Integer)
    new_listings_count = db.Column(db.Integer)
    
    # Deltas from previous snapshot
    median_price_delta_pct = db.Column(db.Float)       # e.g. -1.8
    inventory_delta = db.Column(db.Integer)             # e.g. +6
    avg_dom_delta = db.Column(db.Integer)               # e.g. +4
    
    # Matched listings (JSON array of {address, price, score, risk_tier})
    matched_listings_json = db.Column(db.Text)
    top_match_score = db.Column(db.Integer)
    top_match_address = db.Column(db.String(300))
    
    # Comparable sales for user's analysed properties (JSON)
    # [{property_address, comp_address, comp_price, comp_date, vs_recommended}]
    new_comps_json = db.Column(db.Text)
    new_comps_count = db.Column(db.Integer, default=0)
    
    # Alert tracking
    alerts_generated = db.Column(db.Integer, default=0)
    alert_email_sent = db.Column(db.Boolean, default=False)
    alert_email_sent_at = db.Column(db.DateTime)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'zip_code', 'snapshot_date', name='uq_user_zip_date'),
    )
    
    def __repr__(self):
        return f'<Snapshot {self.user_id} {self.zip_code} {self.snapshot_date}>'


# PROPERTY SCOUT AGENT (v5.62.75)
# =============================================================================

class ScoutProfile(db.Model):
    """User's property search criteria for the Scout Agent"""
    __tablename__ = 'scout_profiles'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Search criteria
    name = db.Column(db.String(100), default='My Search')
    zip_codes = db.Column(db.Text, nullable=False)  # JSON array: ["95124","95125"]
    price_min = db.Column(db.Integer)
    price_max = db.Column(db.Integer, nullable=False)
    bedrooms_min = db.Column(db.Integer, default=1)
    bathrooms_min = db.Column(db.Integer, default=1)
    sqft_min = db.Column(db.Integer)
    property_types = db.Column(db.Text, default='["Single Family"]')  # JSON array
    
    # Agent preferences
    alert_frequency = db.Column(db.String(20), default='daily')  # 'realtime', 'daily', 'weekly'
    auto_analyze = db.Column(db.Boolean, default=False)  # Auto-run full analysis on top matches
    active = db.Column(db.Boolean, default=True)
    
    # Stats
    total_matches = db.Column(db.Integer, default=0)
    last_scan_at = db.Column(db.DateTime)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    matches = db.relationship('ScoutMatch', backref='profile', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self):
        import json
        return {
            'id': self.id,
            'name': self.name,
            'zip_codes': json.loads(self.zip_codes) if self.zip_codes else [],
            'price_min': self.price_min,
            'price_max': self.price_max,
            'bedrooms_min': self.bedrooms_min,
            'bathrooms_min': self.bathrooms_min,
            'sqft_min': self.sqft_min,
            'property_types': json.loads(self.property_types) if self.property_types else [],
            'alert_frequency': self.alert_frequency,
            'auto_analyze': self.auto_analyze,
            'active': self.active,
            'total_matches': self.total_matches,
            'last_scan_at': self.last_scan_at.isoformat() if self.last_scan_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ScoutMatch(db.Model):
    """A property listing matched by the Scout Agent"""
    __tablename__ = 'scout_matches'
    
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey('scout_profiles.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Listing data (from RentCast or other sources)
    address = db.Column(db.String(500), nullable=False)
    city = db.Column(db.String(100))
    state = db.Column(db.String(10))
    zip_code = db.Column(db.String(10), index=True)
    price = db.Column(db.Integer, nullable=False)
    bedrooms = db.Column(db.Integer)
    bathrooms = db.Column(db.Float)
    sqft = db.Column(db.Integer)
    lot_size = db.Column(db.Integer)
    year_built = db.Column(db.Integer)
    property_type = db.Column(db.String(50))
    
    # Listing metadata
    listing_url = db.Column(db.String(1000))
    listed_date = db.Column(db.DateTime)
    days_on_market = db.Column(db.Integer)
    price_per_sqft = db.Column(db.Float)
    listing_status = db.Column(db.String(50))  # 'active', 'pending', 'sold'
    listing_source = db.Column(db.String(50), default='rentcast')
    
    # AI Scout scoring
    scout_score = db.Column(db.Float)  # 0-100 overall match score
    value_score = db.Column(db.Float)  # Price vs market value
    location_score = db.Column(db.Float)  # How well ZIP matches criteria
    condition_score = db.Column(db.Float)  # Age, sqft, etc.
    opportunity_signal = db.Column(db.String(100))  # 'price_drop', 'below_market', 'high_dom', 'new_listing'
    ai_summary = db.Column(db.Text)  # AI-generated one-liner why this is interesting
    
    # Market context (from our existing market intel)
    avm_estimate = db.Column(db.Integer)
    avm_vs_price_pct = db.Column(db.Float)  # positive = asking below AVM (good for buyer)
    zip_median_price = db.Column(db.Integer)
    zip_avg_dom = db.Column(db.Integer)
    
    # User interaction
    status = db.Column(db.String(20), default='new')  # 'new', 'viewed', 'saved', 'dismissed', 'analyzed'
    viewed_at = db.Column(db.DateTime)
    saved_at = db.Column(db.DateTime)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'))  # Link if user runs full analysis
    
    # Timestamps
    found_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Dedup
    listing_hash = db.Column(db.String(64), index=True)  # Hash of address+price for dedup
    
    def to_dict(self):
        return {
            'id': self.id,
            'address': self.address,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'price': self.price,
            'bedrooms': self.bedrooms,
            'bathrooms': self.bathrooms,
            'sqft': self.sqft,
            'lot_size': self.lot_size,
            'year_built': self.year_built,
            'property_type': self.property_type,
            'listing_url': self.listing_url,
            'listed_date': self.listed_date.isoformat() if self.listed_date else None,
            'days_on_market': self.days_on_market,
            'price_per_sqft': self.price_per_sqft,
            'listing_status': self.listing_status,
            'scout_score': self.scout_score,
            'value_score': self.value_score,
            'location_score': self.location_score,
            'condition_score': self.condition_score,
            'opportunity_signal': self.opportunity_signal,
            'ai_summary': self.ai_summary,
            'avm_estimate': self.avm_estimate,
            'avm_vs_price_pct': self.avm_vs_price_pct,
            'zip_median_price': self.zip_median_price,
            'zip_avg_dom': self.zip_avg_dom,
            'status': self.status,
            'found_at': self.found_at.isoformat() if self.found_at else None,
        }

# ═══════════════════════════════════════════════════════════════════
# REPAIR COST DATABASE — ZIP-level contractor rates (v5.68.2)
# ═══════════════════════════════════════════════════════════════════

class RepairCostZone(db.Model):
    """Cost multiplier zones by ZIP prefix (3-digit)."""
    __tablename__ = 'repair_cost_zones'

    id = db.Column(db.Integer, primary_key=True)
    zip_prefix = db.Column(db.String(3), unique=True, nullable=False, index=True)
    metro_name = db.Column(db.String(100), nullable=False)
    cost_multiplier = db.Column(db.Float, nullable=False, default=1.0)
    state = db.Column(db.String(2))
    region = db.Column(db.String(50))  # e.g. 'Northeast', 'Pacific'
    source = db.Column(db.String(100), default='RSMeans CCI 2026')
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    def to_dict(self):
        return {
            'zip_prefix': self.zip_prefix,
            'metro_name': self.metro_name,
            'cost_multiplier': self.cost_multiplier,
            'state': self.state,
            'region': self.region,
            'source': self.source,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class RepairCostBaseline(db.Model):
    """National baseline repair costs by category and severity."""
    __tablename__ = 'repair_cost_baselines'

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)  # e.g. 'foundation', 'hvac'
    severity = db.Column(db.String(20), nullable=False)   # minor/moderate/major/critical
    cost_low = db.Column(db.Integer, nullable=False)
    cost_high = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(200))
    source = db.Column(db.String(100), default='RSMeans 2026 + HomeAdvisor')
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('category', 'severity', name='uq_category_severity'),
    )

    def to_dict(self):
        return {
            'category': self.category,
            'severity': self.severity,
            'cost_low': self.cost_low,
            'cost_high': self.cost_high,
            'description': self.description,
            'source': self.source,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class RepairCostLog(db.Model):
    """Log of repair estimates generated — for learning and accuracy tracking."""
    __tablename__ = 'repair_cost_logs'

    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)
    zip_code = db.Column(db.String(5))
    metro_name = db.Column(db.String(100))
    cost_multiplier = db.Column(db.Float)
    total_low = db.Column(db.Integer)
    total_high = db.Column(db.Integer)
    breakdown_json = db.Column(db.Text)  # JSON of full breakdown
    property_year_built = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=db.func.now())


# ---------------------------------------------------------------------------
# INFRASTRUCTURE & DEV COSTS
# ---------------------------------------------------------------------------

class InfraVendor(db.Model):
    """Known vendors/providers for infrastructure & dev cost tracking."""
    __tablename__ = 'infra_vendors'

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False, unique=True)  # e.g. "Render", "Anthropic"
    category   = db.Column(db.String(50))   # 'hosting', 'ai', 'email', 'ads', 'domain', 'tooling', 'other'
    logo_emoji = db.Column(db.String(10))   # e.g. "🚀"
    notes      = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    invoices   = db.relationship('InfraInvoice', back_populates='vendor', lazy='dynamic',
                                  order_by='InfraInvoice.period_start.desc()')

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'category': self.category,
            'logo_emoji': self.logo_emoji, 'notes': self.notes,
        }


class InfraInvoice(db.Model):
    """Monthly invoice records for infrastructure & dev costs."""
    __tablename__ = 'infra_invoices'

    id           = db.Column(db.Integer, primary_key=True)
    vendor_id    = db.Column(db.Integer, db.ForeignKey('infra_vendors.id'), nullable=False, index=True)
    period_start = db.Column(db.Date, nullable=False)   # e.g. 2026-03-01
    period_end   = db.Column(db.Date, nullable=False)   # e.g. 2026-03-31
    amount_usd   = db.Column(db.Float, nullable=False)
    description  = db.Column(db.String(500))            # e.g. "Pro plan + 2M extra tokens"
    invoice_ref  = db.Column(db.String(100))            # invoice number / receipt ID
    # PDF stored as base64 in DB (admin-only, small files)
    pdf_data     = db.Column(db.Text)                   # base64-encoded PDF or image
    pdf_filename = db.Column(db.String(255))
    pdf_mime     = db.Column(db.String(50))             # 'application/pdf', 'image/png', etc.
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = db.relationship('InfraVendor', back_populates='invoices')

    __table_args__ = (
        db.UniqueConstraint('vendor_id', 'period_start', name='uq_vendor_period'),
    )

    def to_dict(self, include_pdf=False):
        d = {
            'id': self.id,
            'vendor_id': self.vendor_id,
            'vendor_name': self.vendor.name if self.vendor else None,
            'vendor_emoji': self.vendor.logo_emoji if self.vendor else None,
            'vendor_category': self.vendor.category if self.vendor else None,
            'period_start': self.period_start.isoformat() if self.period_start else None,
            'period_end':   self.period_end.isoformat()   if self.period_end   else None,
            'amount_usd':   self.amount_usd,
            'description':  self.description,
            'invoice_ref':  self.invoice_ref,
            'pdf_filename': self.pdf_filename,
            'pdf_mime':     self.pdf_mime,
            'has_pdf':      bool(self.pdf_data),
            'created_at':   self.created_at.isoformat() if self.created_at else None,
        }
        if include_pdf:
            d['pdf_data'] = self.pdf_data
        return d


class EmailEvent(db.Model):
    """Tracks email engagement events from Resend webhooks (opens, clicks, bounces).
    
    One row per event. An email can have multiple opens and clicks.
    Resend webhook docs: https://resend.com/docs/dashboard/webhooks/introduction
    """
    __tablename__ = 'email_events'

    id         = db.Column(db.Integer, primary_key=True)
    ts         = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    resend_id  = db.Column(db.String(100), index=True)   # Matches EmailSendLog.resend_id
    to_email   = db.Column(db.String(255), index=True)
    event_type = db.Column(db.String(30), nullable=False, index=True)  # delivered, opened, clicked, bounced, complained
    link_url   = db.Column(db.String(1000))  # For click events — which link was clicked
    user_agent = db.Column(db.String(500))
    raw_json   = db.Column(db.Text)          # Full webhook payload for debugging

    def __repr__(self):
        return f'<EmailEvent {self.event_type} {self.resend_id} at {self.ts}>'
