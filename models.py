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
        Check if this email can receive a free credit.
        Returns: (can_receive: bool, reason: str)
        """
        registry = EmailRegistry.query.filter_by(email=email).first()
        
        if not registry:
            # New email - can receive credit
            return (True, "new_email")
        
        if registry.is_flagged_abuse:
            # Flagged for abuse - no credit
            return (False, "abuse_flagged")
        
        if registry.has_received_free_credit:
            # Already received credit before (even if account was deleted)
            return (False, "already_received")
        
        # Email exists but hasn't received credit yet (shouldn't happen normally)
        return (True, "eligible")
    
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
        'signup_credits': 3,
        'bonus_credits': 0,
    },
    1: {
        'name': 'Active Referrer',
        'icon': '⭐',
        'referrals_required': 1,
        'signup_credits': 3,
        'bonus_credits': 0,
    },
    2: {
        'name': 'Pro Analyzer',
        'icon': '🏆',
        'referrals_required': 5,
        'signup_credits': 3,
        'bonus_credits': 20,
    },
    3: {
        'name': 'Expert',
        'icon': '💎',
        'referrals_required': 10,
        'signup_credits': 3,
        'bonus_credits': 50,
    },
    4: {
        'name': 'Ambassador',
        'icon': '👑',
        'referrals_required': 25,
        'signup_credits': 3,
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
        except:
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