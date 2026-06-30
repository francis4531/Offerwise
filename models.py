"""
OfferWise Database Models
SQLite database with user authentication, property storage, and usage tracking
"""

from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import json
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
    analysis_credits = db.Column(db.Integer, default=0)  # Reward credits (referrals/promos — NOT purchased)
    total_credits_purchased = db.Column(db.Integer, default=0)  # Legacy field kept for compat
    analyses_completed = db.Column(db.Integer, default=0)  # Lifetime analyses run

    # Subscription model (replaces credit purchases)
    subscription_plan = db.Column(db.String(50), default='free')  # free, buyer_starter, buyer_pro, buyer_unlimited
    analyses_this_month = db.Column(db.Integer, default=0)  # Used this billing period
    analyses_reset_at = db.Column(db.DateTime)  # When the monthly counter resets
    
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

    # v5.88.07: Buyer (User) drip campaign progression tracking.
    # Previously the buyer drip only fired step 1 manually because
    # _UserDripEntry hardcoded drip_step=0 with no persistence. These
    # columns let the auto-firing scheduler track which step each user
    # has received and when, so steps 2-5 auto-progress like waitlist drip.
    drip_step = db.Column(db.Integer, default=0)            # 0=not started, 1-5=email sent
    drip_last_sent_at = db.Column(db.DateTime)
    drip_completed = db.Column(db.Boolean, default=False)
    email_unsubscribed = db.Column(db.Boolean, default=False)
    unsubscribed_at = db.Column(db.DateTime)
    unsubscribe_token = db.Column(db.String(64), index=True)

    # v5.88.50: per-user customer-discovery email draft. Same pattern as
    # outreach_contacts.draft_{subject,body,generated_at} but for buyers.
    # Populated by /api/admin/outreach/buyer-draft/<user_id>/generate
    # (calls Claude with the user's funnel stage + property context).
    # Cleared after successful send so a new draft is generated next time.
    outreach_draft_subject = db.Column(db.String(500), nullable=True)
    outreach_draft_body = db.Column(db.Text, nullable=True)
    outreach_draft_generated_at = db.Column(db.DateTime, nullable=True)

    # v5.89.66: Ad attribution captured at signup time.
    # First-party attribution that doesn't depend on third-party cookies or
    # Google's conversion pixel callback. Populated from URL params + session
    # at OAuth callback. Nullable so existing users aren't affected.
    signup_utm_source = db.Column(db.String(120), nullable=True, index=True)
    signup_utm_medium = db.Column(db.String(120), nullable=True)
    signup_utm_campaign = db.Column(db.String(255), nullable=True)
    signup_utm_term = db.Column(db.String(255), nullable=True)
    signup_utm_content = db.Column(db.String(255), nullable=True)
    signup_referrer = db.Column(db.String(500), nullable=True)
    signup_landing_page = db.Column(db.String(500), nullable=True)
    signup_gclid = db.Column(db.String(255), nullable=True, index=True)  # Google Ads click ID

    # v5.89.66: One-shot conversion pixel flag. Replaces the destructive
    # session['new_signup'] approach. Set True on user creation, flipped
    # False once the conversion pixel has fired successfully on any page.
    # Survives across sessions so users who close the browser during
    # onboarding still fire the pixel when they return.
    signup_pixel_fired = db.Column(db.Boolean, nullable=False, default=False, index=True)

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


# ─────────────────────────────────────────────────────────────────────────────
# Reasoning tiers (Phase 0b): persisted Finding / Claim / Issue.
#
# ADDITIVE — these do not replace the runtime path in cross_reference_engine /
# risk_scoring_model. They are the persistence layer the checklist-driven
# pipeline writes to, keyed on checklist item ids (the missing middle term).
# Relationships are many-to-many per the architecture (one finding can support
# multiple claims; one claim can inform multiple issues), modeled via the two
# association tables below.
# ─────────────────────────────────────────────────────────────────────────────

# Association: which findings support / contradict a claim (role-tagged).
claim_findings = db.Table(
    'claim_findings',
    db.Column('claim_id', db.Integer, db.ForeignKey('reasoning_claims.id', ondelete='CASCADE'), primary_key=True),
    db.Column('finding_id', db.Integer, db.ForeignKey('reasoning_findings.id', ondelete='CASCADE'), primary_key=True),
    db.Column('role', db.String(20), nullable=False, default='supporting'),  # 'supporting' | 'contradicting'
)

# Association: which claims a (decision-axis) issue clusters.
issue_claims = db.Table(
    'issue_claims',
    db.Column('issue_id', db.Integer, db.ForeignKey('reasoning_issues.id', ondelete='CASCADE'), primary_key=True),
    db.Column('claim_id', db.Integer, db.ForeignKey('reasoning_claims.id', ondelete='CASCADE'), primary_key=True),
)


class Finding(db.Model):
    """Tier 1 — a single evidence statement from one document at one location."""
    __tablename__ = 'reasoning_findings'

    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=True, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True, index=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=True, index=True)

    source_document = db.Column(db.String(50))   # 'inspection' | 'disclosure' | 'nhd' | 'title' | ...
    source_page = db.Column(db.Integer, nullable=True)
    source_quote = db.Column(db.Text, nullable=True)
    raw_text = db.Column(db.Text)
    # 'freetext_narrative' | 'structured_form_field' | 'extracted_datapoint' | 'structured_report_field'
    modality = db.Column(db.String(40), nullable=True)
    # bridge to the legacy IssueCategory value during the transition (Phase 4 retires it)
    legacy_category = db.Column(db.String(50), nullable=True)
    severity = db.Column(db.String(20), nullable=True)
    confidence = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Claim(db.Model):
    """Tier 2 — the resolved answer to a checklist item, with dual confidence."""
    __tablename__ = 'reasoning_claims'

    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=True, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True, index=True)

    checklist_item_id = db.Column(db.String(120), nullable=False, index=True)  # e.g. 'plumbing.active_leaks'
    checklist_version = db.Column(db.String(20))                               # e.g. 'v0.5'
    resolved_value = db.Column(db.Text, nullable=True)
    # 'answered' | 'unanswered' | 'contradiction' | 'ambiguous'
    resolution_state = db.Column(db.String(20), default='answered')
    polarity = db.Column(db.String(20), nullable=True)
    # dual confidence (Commitment 2.3): inference vs evidence-quality
    inference_confidence = db.Column(db.Float, nullable=True)
    evidence_quality_confidence = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    findings = db.relationship('Finding', secondary=claim_findings,
                               backref=db.backref('claims', lazy='dynamic'), lazy='dynamic')


class Issue(db.Model):
    """Tier 3 — a decision-axis cluster of claims that affects the offer."""
    __tablename__ = 'reasoning_issues'

    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=True, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True, index=True)

    # 'pre_close_required_action' | 'negotiation_lever' | 'due_diligence_and_reserve' | 'silent_hazard'
    decision_class = db.Column(db.String(40), nullable=False, index=True)
    silent_hazard_flag = db.Column(db.Boolean, default=False)
    severity = db.Column(db.String(20))
    cost_band_low = db.Column(db.Float, nullable=True)
    cost_band_high = db.Column(db.Float, nullable=True)
    # reserve contingency (buyer-held) vs estimable price-adjustment basis
    is_reserve = db.Column(db.Boolean, default=False)
    title = db.Column(db.String(300))
    summary = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    claims = db.relationship('Claim', secondary=issue_claims,
                             backref=db.backref('issues', lazy='dynamic'), lazy='dynamic')


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


class FeatureEvent(db.Model):
    """
    Lightweight feature engagement tracking.
    One row per user interaction with a named product feature.
    Queryable for product decisions without GA4.
    """
    __tablename__ = 'feature_events'

    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    session_id = db.Column(db.String(64), nullable=True, index=True)  # anonymous pre-login

    # What feature was used
    feature    = db.Column(db.String(80),  nullable=False, index=True)  # e.g. 'addendum_draft'
    action     = db.Column(db.String(80),  nullable=True)               # e.g. 'click', 'download', 'copy'

    # Optional context
    property_id   = db.Column(db.Integer, nullable=True)
    analysis_id   = db.Column(db.Integer, nullable=True)
    meta          = db.Column(db.Text,    nullable=True)  # arbitrary JSON payload

    def __repr__(self):
        return f'<FeatureEvent {self.feature}/{self.action} user={self.user_id}>'



class CostPricingProvenance(db.Model):
    """One row per inspection finding that passed through repair-cost pricing.

    Records whether the finding was priced by the ML cost model (confidence at
    or above the live threshold) or fell back to the category baseline (low
    confidence, or no usable model output). This is what lets admin measure the
    baseline-fallback RATE BY CATEGORY — i.e. which defect classes the model is
    blind on and therefore pricing as wide category priors. Append-only
    telemetry; auto-created by db.create_all (no migration). Captured forward
    from when instrumentation ships — provenance can't be reconstructed for
    analyses that ran before it, since findings aren't persisted with a source.

    source values:
      'ml'               — ML priced it, confidence >= threshold (tight estimate)
      'baseline_lowconf' — ML ran but confidence < threshold -> deferred to baseline
      'baseline_noml'    — ML model unavailable / no usable output -> baseline
      'doc'              — cost stated in the document; pricing skipped (not a miss)
      'preset'           — already priced upstream before the ML pass (not a miss)

    The fallback RATE counts (baseline_lowconf + baseline_noml) over
    (ml + baseline_lowconf + baseline_noml). 'doc' and 'preset' are recorded for
    completeness but excluded from the rate — they aren't model decisions.
    """
    __tablename__ = 'cost_pricing_provenance'

    id          = db.Column(db.Integer, primary_key=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    analysis_id = db.Column(db.Integer, nullable=True, index=True)

    category    = db.Column(db.String(50), nullable=True, index=True)  # 'foundation', 'hvac', ...
    severity    = db.Column(db.String(20), nullable=True)              # 'critical'|'major'|'minor'
    source      = db.Column(db.String(24), nullable=False, index=True)
    confidence  = db.Column(db.Float, nullable=True)                   # ML confidence when ML ran
    threshold   = db.Column(db.Float, nullable=True)                   # the bar in effect (e.g. 0.85)

    def __repr__(self):
        return f'<CostPricingProvenance {self.category}/{self.source} conf={self.confidence}>'


class AIParseEvent(db.Model):
    """One row per JSON-returning Claude call routed through ai_json.call_ai_json.

    Records whether the model's structured output parsed, whether it was
    truncated (stop_reason == 'max_tokens'), and whether it only survived via
    repair. This is what lets admin measure the parse-failure / truncation RATE
    BY ENDPOINT — i.e. which AI surfaces are silently degrading on issue-heavy
    real deals. Before this, truncation was invisible: no call site read
    stop_reason, and the worst offender swallowed the parse error into a silent
    fallback to raw rules output.

    Append-only telemetry; auto-created by db.create_all (no migration).
    Forward-looking — there is no honest way to backfill calls that ran before
    instrumentation, since responses aren't persisted.
    """
    __tablename__ = 'ai_parse_event'

    id           = db.Column(db.Integer, primary_key=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    analysis_id  = db.Column(db.Integer, nullable=True, index=True)

    endpoint     = db.Column(db.String(50), nullable=True, index=True)   # 'cross-reference', ...
    model        = db.Column(db.String(50), nullable=True)
    stop_reason  = db.Column(db.String(24), nullable=True, index=True)   # 'end_turn'|'max_tokens'|...
    ok           = db.Column(db.Boolean, nullable=False, default=False, index=True)
    truncated    = db.Column(db.Boolean, nullable=False, default=False, index=True)
    repaired     = db.Column(db.Boolean, nullable=False, default=False)
    output_chars = db.Column(db.Integer, nullable=True)
    attempts     = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f'<AIParseEvent {self.endpoint} ok={self.ok} truncated={self.truncated}>'


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




class IssueConfirmation(db.Model):
    """
    Per-repair-item buyer confirmation — the proprietary training dataset.
    Captures whether each flagged issue was actually found during inspection.
    After ~1000 labeled examples this becomes a fine-tuning dataset no
    competitor can replicate.
    """
    __tablename__ = 'issue_confirmations'

    id           = db.Column(db.Integer, primary_key=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    analysis_id  = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=True, index=True)
    property_id  = db.Column(db.Integer, nullable=True)

    # The flagged issue
    system       = db.Column(db.String(80),  nullable=False)   # e.g. 'foundation', 'roof'
    severity     = db.Column(db.String(20),  nullable=True)    # 'critical', 'major', 'minor'
    description  = db.Column(db.Text,        nullable=True)

    # The verdict
    verdict      = db.Column(db.String(20),  nullable=False)   # 'confirmed' | 'not_found' | 'partial'
    buyer_note   = db.Column(db.Text,        nullable=True)    # optional free text

    def __repr__(self):
        return f'<IssueConfirmation {self.system}/{self.verdict} analysis={self.analysis_id}>'


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


# v5.88.38: SupportShare class removed. The class was kept as legacy in
# v5.88.34 through v5.88.37 for safety, but Ticket + TicketMessage has
# fully replaced it. The 'support_shares' table is dropped at boot
# (see app.py boot sequence) since the founder confirmed zero customers
# meant no data to preserve.


# ============================================================================
# v5.88.34: Ticket + TicketMessage — replaces SupportShare
#
# Design notes
#
# - Ticket holds the WORKFLOW STATE (status, assignee, age, source, subject).
# - TicketMessage holds the CONVERSATION (each user message, each admin reply).
# - The first message of every ticket is automatically the user's initial
#   contact (whether from in-product "Share with support" or, later, an
#   inbound email).
# - Tickets support multiple sources (in_product_share, email, contact_form,
#   etc.) so when we ship inbound email in v5.88.36, the data model already
#   accommodates it without migration.
# - Subject is denormalized (kept on Ticket) because email threading needs
#   it as a constant identifier for the whole conversation; messages don't
#   own their own subject.
# - property_id is OPTIONAL — email tickets won't have a Property attached.
#   In-product shares always will.
# - snapshot_json and findings_json move to the FIRST TicketMessage (since
#   only in-product shares produce them). Cleaner separation of concerns:
#   the Ticket itself is channel-agnostic; the rich payload lives on the
#   message that needed it.
# ============================================================================

class Ticket(db.Model):
    """A unit of support work. May span multiple back-and-forth messages
    between user and admin."""
    __tablename__ = 'tickets'

    id = db.Column(db.Integer, primary_key=True)

    # Who opened it
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    # email_for_anonymous is for inbound emails from people who don't have
    # an account yet — we still need to be able to reply. Set when user_id
    # is null. Either user_id OR email_for_anonymous must be present.
    email_for_anonymous = db.Column(db.String(255), nullable=True, index=True)

    # What it's about
    subject = db.Column(db.String(255), nullable=False)
    # Optional property context — only present for in-product shares.
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)

    # Source of the original contact
    source = db.Column(db.String(50), nullable=False, index=True)
    # Allowed: 'in_product_share', 'inbound_email', 'contact_form', 'manual'

    # Workflow state
    # Allowed transitions:
    #   open -> in_progress -> waiting_on_user -> resolved
    #   open -> resolved (close immediately)
    #   waiting_on_user -> in_progress (user replied, back to work)
    #   resolved -> reopened (user re-engaged after we closed it)
    status = db.Column(db.String(30), nullable=False, default='open', index=True)
    # admin user who currently owns this ticket. NULL = unassigned.
    assigned_admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    # Status timestamps (filled when status enters that state)
    first_admin_reply_at = db.Column(db.DateTime, nullable=True)  # for SLA aging
    last_user_reply_at = db.Column(db.DateTime, nullable=True)
    last_admin_reply_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)

    # Audit / sort
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('tickets', lazy='dynamic'))
    assigned_admin = db.relationship('User', foreign_keys=[assigned_admin_id])
    # v5.88.34: named 'linked_property' (not 'property') to avoid shadowing
    # Python's @property decorator on this class.
    linked_property = db.relationship('Property')
    messages = db.relationship('TicketMessage',
                               backref='ticket',
                               order_by='TicketMessage.created_at',
                               cascade='all, delete-orphan',
                               lazy='dynamic')

    @property
    def message_count(self):
        return self.messages.count()

    @property
    def reply_email(self):
        """Email address to send admin replies to."""
        if self.user_id and self.user:
            return self.user.email
        return self.email_for_anonymous

    @property
    def display_name(self):
        """Best name to show in the admin UI."""
        if self.user:
            return self.user.name or self.user.email
        return self.email_for_anonymous or 'Unknown'

    def to_dict(self, include_messages=False):
        d = {
            'id': self.id,
            'subject': self.subject,
            'status': self.status,
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'first_admin_reply_at': self.first_admin_reply_at.isoformat() if self.first_admin_reply_at else None,
            'last_user_reply_at': self.last_user_reply_at.isoformat() if self.last_user_reply_at else None,
            'last_admin_reply_at': self.last_admin_reply_at.isoformat() if self.last_admin_reply_at else None,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
            'user_id': self.user_id,
            'user_email': self.user.email if self.user else self.email_for_anonymous,
            'user_name': self.display_name,
            'property_id': self.property_id,
            'property_address': self.linked_property.address if self.linked_property else None,
            'assigned_admin_id': self.assigned_admin_id,
            'message_count': self.message_count,
        }
        # v5.88.37: aging metadata. Lazy import to avoid model -> service
        # circular import on first load.
        try:
            from support_service import ticket_age_info
            d['aging'] = ticket_age_info(self)
        except Exception:
            d['aging'] = {'age_hours': 0.0, 'is_stale': False, 'aging_basis': 'n/a'}
        if include_messages:
            d['messages'] = [m.to_dict() for m in self.messages.all()]
        return d


class TicketMessage(db.Model):
    """One message in a ticket conversation.

    The first message is always the initial contact (user's "Share with
    support" text, or the body of an inbound email). Subsequent messages
    are admin replies (sent via Resend) or user replies (received via
    inbound email webhook).
    """
    __tablename__ = 'ticket_messages'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('tickets.id'), nullable=False, index=True)

    # Who sent this message
    # 'user'      = the customer
    # 'admin'     = an admin replying
    # 'system'    = automatic message (e.g. "Ticket resolved", status changes)
    # 'note'      = internal admin note, NOT sent to user, only visible in admin UI
    author_kind = db.Column(db.String(20), nullable=False, default='user')
    author_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    # For inbound emails from non-account holders
    author_email = db.Column(db.String(255), nullable=True)

    # Content
    body = db.Column(db.Text, nullable=False)

    # If this message was generated by an in-product "Share with support",
    # we capture the analysis snapshot here. Keeps the rich context with
    # the message that needed it (not at the Ticket level).
    snapshot_json = db.Column(db.Text, nullable=True)
    findings_json = db.Column(db.Text, nullable=True)
    full_result_json = db.Column(db.Text, nullable=True)

    # For outbound admin replies, the Resend send id (for tracking deliveries)
    resend_message_id = db.Column(db.String(100), nullable=True)
    # For inbound emails, the original Message-ID header (for threading verification)
    inbound_message_id = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    author_user = db.relationship('User', foreign_keys=[author_user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'ticket_id': self.ticket_id,
            'author_kind': self.author_kind,
            'author_user_id': self.author_user_id,
            'author_email': self.author_email,
            'author_name': (self.author_user.name or self.author_user.email)
                           if self.author_user else (self.author_email or 'unknown'),
            'body': self.body,
            'has_snapshot': bool(self.snapshot_json),
            'resend_message_id': self.resend_message_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class TicketTemplate(db.Model):
    """v5.88.37: Saved reply templates for the admin Inbox.

    Templates are admin-only artifacts — they have no per-user/per-ticket
    scope. An admin clicks a template, its body fills the composer with
    variables already substituted from the current ticket.

    Variables supported:
      {user_name}         — the customer's display name
      {user_email}        — the customer's email
      {ticket_id}         — current ticket id, useful in references
      {property_address}  — only meaningful for in_product_share tickets
      {offerscore}        — only meaningful when a snapshot is present
      {risk_tier}         — same caveat

    Unsupported variables in a template are left as-is in the body (so
    a typo doesn't silently delete content). The UI flags unresolved
    variables so the admin can fix before sending.

    Templates are seeded once at first boot from a small default set.
    Admins can add/edit/delete via the admin UI (v5.88.37 ships the
    list + use endpoints; full CRUD UI is a stretch goal).
    """
    __tablename__ = 'ticket_templates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    body = db.Column(db.Text, nullable=False)
    # Sort key for display order (lower = earlier). Admins picking the most
    # common reply first matters more than alphabetical.
    sort_order = db.Column(db.Integer, default=100, nullable=False)
    # Mark seeded defaults so we can avoid duplicating them on rebuilds.
    is_seeded = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'body': self.body,
            'sort_order': self.sort_order,
            'is_seeded': self.is_seeded,
        }


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
    # v5.89.60: widened from String(20) to String(100). BP IDs use format
    # bp_<7digit>-<slug-prefix> (~24 chars), and other platforms may use
    # longer identifiers. The 20-char limit caused every BP insert to fail
    # with StringDataRightTruncation, dropping 73/73 fetched threads.
    reddit_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
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
    
    # Platform: reddit | facebook | nextdoor
    platform = db.Column(db.String(20), default='reddit', index=True)
    target_group = db.Column(db.String(200))  # FB group name / Nextdoor neighborhood

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<GTMPost {self.id} [{self.status}] {self.pillar} {self.platform} for {self.scheduled_date}>'
    
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
            'platform': self.platform or 'reddit',
            'target_group': self.target_group,
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
    amount_usd = db.Column(db.Float, nullable=True)  # Revenue for purchase events — enables CAC:LTV by channel
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


class PermitCache(db.Model):
    """v5.87.82: Per-jurisdiction permit-requirement cache.

    Each row caches one LLM-generated permit finding for a unique
    (jurisdiction, repair-system) pair. TTL enforced at read time
    (PERMIT_CACHE_DAYS env, default 90). New lookups upsert.
    """
    __tablename__ = 'permit_cache'

    id = db.Column(db.Integer, primary_key=True)
    jurisdiction_key = db.Column(db.String(120), nullable=False, index=True)
    system_key       = db.Column(db.String(120), nullable=False, index=True)
    payload_json     = db.Column(db.Text, nullable=False)
    created_at       = db.Column(db.DateTime, nullable=False,
                                  default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('jurisdiction_key', 'system_key',
                            name='uq_permit_cache_juris_system'),
    )

    def __repr__(self):
        return f'<PermitCache {self.jurisdiction_key}/{self.system_key}>'


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
    # v5.87.72: auto-ingestion tracking
    source       = db.Column(db.String(20), default='manual')   # 'manual' | 'email_auto'
    parse_confidence = db.Column(db.Float)                       # Claude's self-reported confidence 0.0-1.0
    raw_email_id = db.Column(db.String(100))                     # Resend email_id for audit trail
    needs_review = db.Column(db.Boolean, default=False, index=True)
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
            'source':       self.source or 'manual',
            'parse_confidence': self.parse_confidence,
            'raw_email_id': self.raw_email_id,
            'needs_review': bool(self.needs_review),
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


class ContractorLead(db.Model):
    """Contractor quote requests from results page repair items.
    
    Each row = one user requesting quotes for one repair issue.
    Admin reviews these and forwards to local contractors.
    """
    __tablename__ = 'contractor_leads'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Who
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    user_email      = db.Column(db.String(255), index=True)
    user_name       = db.Column(db.String(255))
    user_phone      = db.Column(db.String(50))

    # What property
    property_address = db.Column(db.String(500))
    property_zip     = db.Column(db.String(10))

    # What repair
    repair_system    = db.Column(db.String(100))   # e.g. "roof", "hvac"
    trade_needed     = db.Column(db.String(100))   # e.g. "Roofing Contractor"
    cost_estimate    = db.Column(db.String(100))   # e.g. "$8K–$14K"
    issue_description = db.Column(db.Text)

    # Preferred contact
    contact_timing   = db.Column(db.String(50))    # 'asap', 'this_week', 'just_exploring'

    # Status workflow: new → available → claimed → expired / closed
    # 'available'  = on marketplace, contractors can claim
    # 'claimed'    = one or more contractors claimed it
    # 'expired'    = unclaimed after 48h, auto-assigned or buyer notified
    # 'closed'     = job done
    status           = db.Column(db.String(30), default='available', index=True)
    notes            = db.Column(db.Text)

    # Marketplace timing
    available_at     = db.Column(db.DateTime)          # when posted to marketplace
    expires_at       = db.Column(db.DateTime)          # 48h after available_at
    claim_count      = db.Column(db.Integer, default=0) # how many contractors claimed

    # Which contractor was assigned (legacy + first claimer)
    assigned_contractor_id = db.Column(db.Integer, db.ForeignKey('contractors.id'), nullable=True, index=True)
    sent_to_contractor_at  = db.Column(db.DateTime)
    contacted_at           = db.Column(db.DateTime)

    # Revenue tracking
    job_closed_at    = db.Column(db.DateTime)
    job_value        = db.Column(db.Float)          # $ value of closed job
    referral_fee_pct = db.Column(db.Float)          # % agreed with contractor
    referral_fee_due = db.Column(db.Float)          # computed: job_value * pct
    referral_paid    = db.Column(db.Boolean, default=False)
    referral_paid_at = db.Column(db.DateTime)

    def fee_due(self):
        if self.job_value and self.referral_fee_pct:
            return round(self.job_value * self.referral_fee_pct / 100, 2)
        return 0.0

    def __repr__(self):
        return f'<ContractorLead {self.repair_system} {self.user_email} {self.created_at}>'


class Inspector(db.Model):
    """Licensed home inspector who uses OfferWise to enhance their reports."""
    __tablename__ = 'inspectors'

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False, index=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    # Profile
    business_name   = db.Column(db.String(255))
    license_number  = db.Column(db.String(100))
    license_state   = db.Column(db.String(2))
    phone           = db.Column(db.String(50))
    website         = db.Column(db.String(255))
    service_areas   = db.Column(db.String(500))   # comma-separated ZIPs or cities

    # Plan
    plan            = db.Column(db.String(20), default='free')  # free, pro
    monthly_quota   = db.Column(db.Integer, default=5)          # analyses/month
    monthly_used    = db.Column(db.Integer, default=0)
    quota_reset_at  = db.Column(db.DateTime)

    # Stats
    total_reports   = db.Column(db.Integer, default=0)
    # v5.88.91: registered = signed up; converted = paid. Earlier code
    # was bumping total_buyers_converted on signup, which conflated the
    # two and made the "your reports drove paid conversions" claim
    # misleading on the portal. Registered is the right counter for
    # the inspector landing page ("you generated 12 signups"); converted
    # is reserved for paid actions which we now bump from the payment
    # flow specifically.
    total_buyers_registered = db.Column(db.Integer, default=0)  # buyers who created an account
    total_buyers_converted  = db.Column(db.Integer, default=0)  # buyers who paid

    # InterNACHI vendor program
    internachi_member_id = db.Column(db.String(50), nullable=True, index=True)  # verified member number
    internachi_verified  = db.Column(db.Boolean, default=False)

    # Status
    is_verified     = db.Column(db.Boolean, default=False)
    is_active       = db.Column(db.Boolean, default=True)
    notes           = db.Column(db.Text)

    def __repr__(self):
        return f'<Inspector {self.business_name} ({self.plan})>'


class InspectorReport(db.Model):
    """An analysis generated by an inspector for a specific buyer."""
    __tablename__ = 'inspector_reports'

    id               = db.Column(db.Integer, primary_key=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    inspector_id     = db.Column(db.Integer, db.ForeignKey('inspectors.id'), nullable=False, index=True)
    inspector_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Property
    property_address = db.Column(db.String(500))
    property_price   = db.Column(db.Float)

    # Buyer info (provided by inspector)
    buyer_name       = db.Column(db.String(255))
    buyer_email      = db.Column(db.String(255))

    # Source document
    inspection_text  = db.Column(db.Text)   # Original inspection report text
    # v5.88.64: Optional pass-through URL to the inspector's own hosted PDF
    # (e.g., Spectora/HomeGauge public report link). NOT a file we host —
    # just a link we render as a button on the buyer report. Inspector
    # controls retention. If they take the link down, the button breaks,
    # which is their call to make. See changelog for rationale.
    inspection_pdf_url = db.Column(db.String(2048), nullable=True)

    # Analysis result
    analysis_json    = db.Column(db.Text)   # Full result JSON
    share_token      = db.Column(db.String(32), unique=True, index=True)

    # Branding
    inspector_name_on_report = db.Column(db.String(255))
    inspector_biz_on_report  = db.Column(db.String(255))

    # Tracking
    buyer_viewed_at  = db.Column(db.DateTime)
    buyer_registered = db.Column(db.Boolean, default=False)
    buyer_converted  = db.Column(db.Boolean, default=False)   # bought credits
    view_count       = db.Column(db.Integer, default=0)

    # v5.88.91: referral attribution. buyer_registered/buyer_converted are
    # boolean flags but the inspector portal needs to show WHEN the signup
    # happened and WHICH buyer (by name + when) so the referral pitch
    # becomes concrete evidence instead of an aggregate counter. The
    # buyer_user_id link also lets us track the full attribution chain
    # (this inspector's report → this user → that user's later purchases).
    # auth_routes already references buyer_registered_at with hasattr()
    # guards — adding the column lets those branches actually persist.
    buyer_registered_at = db.Column(db.DateTime)
    buyer_converted_at  = db.Column(db.DateTime)
    buyer_user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)

    def __repr__(self):
        return f'<InspectorReport {self.property_address} buyer={self.buyer_email}>'


class Contractor(db.Model):
    """A licensed contractor who has signed up to receive leads from OfferWise."""
    __tablename__ = 'contractors'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Identity
    name            = db.Column(db.String(255), nullable=False)
    business_name   = db.Column(db.String(255))
    email           = db.Column(db.String(255), nullable=False, index=True)
    phone           = db.Column(db.String(50))
    website         = db.Column(db.String(255))

    # License
    license_number  = db.Column(db.String(100))
    license_state   = db.Column(db.String(2), default='CA')
    license_verified = db.Column(db.Boolean, default=False)

    # Trades — what they do
    trades          = db.Column(db.String(500))   # comma-separated: "roofing,hvac,electrical"
    trade_notes     = db.Column(db.Text)          # free text about specialties

    # Service area
    service_zips    = db.Column(db.String(1000))  # comma-separated ZIP codes
    service_cities  = db.Column(db.String(500))   # comma-separated city names
    service_radius_miles = db.Column(db.Integer, default=25)

    # Capacity
    accepts_leads   = db.Column(db.Boolean, default=True, index=True)
    max_leads_month = db.Column(db.Integer, default=10)
    leads_sent_month = db.Column(db.Integer, default=0)
    leads_sent_total = db.Column(db.Integer, default=0)
    jobs_closed     = db.Column(db.Integer, default=0)

    # Subscription
    plan            = db.Column(db.String(30), default='free')   # free, starter, pro, enterprise
    stripe_customer_id = db.Column(db.String(100))
    subscription_id = db.Column(db.String(100))
    plan_activated_at = db.Column(db.DateTime)
    monthly_lead_limit = db.Column(db.Integer, default=0)  # 0=no limit on free, set by plan

    # Financials (legacy — no longer used for referral fees)
    avg_job_size    = db.Column(db.Integer)       # estimated avg job value $

    # Availability (contractor self-manages)
    available        = db.Column(db.Boolean, default=True, index=True)  # true = open for leads
    unavailable_until = db.Column(db.DateTime)  # optional: auto-resume date

    # Status (admin-set for fraud/compliance only)
    status          = db.Column(db.String(20), default='active', index=True)  # active, suspended
    notes           = db.Column(db.Text)          # internal admin notes
    source          = db.Column(db.String(50), default='website')  # website, referral, outreach

    def __repr__(self):
        return f'<Contractor {self.business_name or self.name} ({self.status})>'

    def trades_list(self):
        return [t.strip() for t in (self.trades or '').split(',') if t.strip()]

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'business_name': self.business_name,
            'email': self.email,
            'phone': self.phone,
            'trades': self.trades_list(),
            'service_cities': self.service_cities,
            'license_number': self.license_number,
            'license_state': self.license_state,
            'license_verified': self.license_verified,
            'status': self.status,
            'plan': getattr(self, 'plan', 'free') or 'free',
            'monthly_lead_limit': getattr(self, 'monthly_lead_limit', 0),
            'accepts_leads': self.accepts_leads,
            'leads_sent_total': self.leads_sent_total,
            'jobs_closed': self.jobs_closed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class APIKey(db.Model):
    """B2B API keys for external integrations (Spectora, etc.)."""
    __tablename__ = 'api_keys'
    id           = db.Column(db.Integer, primary_key=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    key_hash     = db.Column(db.String(128), unique=True, index=True)  # SHA-256 of the key
    key_prefix   = db.Column(db.String(10))    # first 8 chars for display (ow_live_ab)
    label        = db.Column(db.String(100))   # human name e.g. "Spectora Integration"
    tier         = db.Column(db.String(20), default='standard')  # standard, enterprise
    is_active    = db.Column(db.Boolean, default=True, index=True)
    last_used_at = db.Column(db.DateTime)
    calls_total  = db.Column(db.Integer, default=0)
    calls_month  = db.Column(db.Integer, default=0)
    monthly_limit= db.Column(db.Integer, default=100)  # analyses per month
    revoked_at   = db.Column(db.DateTime)
    # Revenue tracking
    price_per_call   = db.Column(db.Float, default=0.0)   # $ per analysis call (0 = free/bespoke)
    monthly_fee      = db.Column(db.Float, default=0.0)   # flat monthly fee if any
    revenue_month    = db.Column(db.Float, default=0.0)   # accrued this month
    revenue_total    = db.Column(db.Float, default=0.0)   # lifetime revenue from this key
    invoice_day      = db.Column(db.Integer, default=1)   # day of month to invoice
    billing_email    = db.Column(db.String(255))           # separate billing contact if needed

    def accrued_this_call(self):
        return round(self.price_per_call or 0.0, 4)

    def __repr__(self):
        return f'<APIKey {self.key_prefix}... [{self.tier}]>'


class InspectorReferral(db.Model):
    """Inspector-to-inspector referral tracking."""
    __tablename__ = 'inspector_referrals'
    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    referrer_id     = db.Column(db.Integer, db.ForeignKey('inspectors.id'), index=True)
    referred_email  = db.Column(db.String(255))
    referred_name   = db.Column(db.String(255))
    invite_token    = db.Column(db.String(64), unique=True, index=True)
    invite_sent_at  = db.Column(db.DateTime)
    signed_up_at    = db.Column(db.DateTime)
    bonus_granted   = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f'<InspectorReferral {self.referred_email}>'


class ContractorLeadClaim(db.Model):
    """Records when a contractor claims a marketplace lead."""
    __tablename__ = 'contractor_lead_claims'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    lead_id         = db.Column(db.Integer, db.ForeignKey('contractor_leads.id'), nullable=False, index=True)
    contractor_id   = db.Column(db.Integer, db.ForeignKey('contractors.id'), nullable=False, index=True)

    # Status: claimed → contacted → closed / passed
    status          = db.Column(db.String(20), default='claimed', index=True)
    passed_at       = db.Column(db.DateTime)   # if contractor passed on the lead
    contacted_at    = db.Column(db.DateTime)   # when they reached out to buyer
    closed_at       = db.Column(db.DateTime)   # job won
    job_value       = db.Column(db.Float)

    __table_args__ = (
        db.UniqueConstraint('lead_id', 'contractor_id', name='unique_lead_contractor'),
    )

    def __repr__(self):
        return f'<LeadClaim lead={self.lead_id} contractor={self.contractor_id} {self.status}>'


class Agent(db.Model):
    """Licensed real estate agent who shares OfferWise analyses with buyer clients."""
    __tablename__ = 'agents'

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False, index=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    # Profile
    business_name   = db.Column(db.String(255))   # brokerage name
    agent_name      = db.Column(db.String(255))   # individual agent full name
    license_number  = db.Column(db.String(100))   # state RE license #
    license_state   = db.Column(db.String(2))     # 2-letter state code (US nationwide)
    phone           = db.Column(db.String(50))
    website         = db.Column(db.String(255))
    service_areas   = db.Column(db.String(500))   # comma-separated cities/ZIPs

    # Plan
    plan            = db.Column(db.String(20), default='free')   # free, pro
    monthly_quota   = db.Column(db.Integer, default=10)          # shared links/month (free)
    monthly_used    = db.Column(db.Integer, default=0)
    quota_reset_at  = db.Column(db.DateTime)

    # Stats
    total_shares    = db.Column(db.Integer, default=0)   # total analyses shared with clients
    total_buyers_converted = db.Column(db.Integer, default=0)  # buyers who subscribed

    # Vanity link — getofferwise.ai/a/<slug>
    vanity_slug     = db.Column(db.String(60), unique=True, index=True)  # e.g. "sarah-johnson-realty"
    photo_url       = db.Column(db.String(500))   # headshot URL

    # Status
    is_verified     = db.Column(db.Boolean, default=False)
    is_active       = db.Column(db.Boolean, default=True)
    notes           = db.Column(db.Text)

    def __repr__(self):
        return f'<Agent {self.agent_name} @ {self.business_name} ({self.plan})>'


class AgentShare(db.Model):
    """A property analysis link shared by an agent with a buyer client."""
    __tablename__ = 'agent_shares'

    id               = db.Column(db.Integer, primary_key=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    agent_id         = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=False, index=True)
    agent_user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Property
    property_address = db.Column(db.String(500))
    property_price   = db.Column(db.Float)

    # Buyer info (provided by agent)
    buyer_name       = db.Column(db.String(255))
    buyer_email      = db.Column(db.String(255))

    # Analysis — agent can link to an existing buyer analysis OR generate a quick one
    analysis_json    = db.Column(db.Text)    # lightweight analysis result JSON
    share_token      = db.Column(db.String(32), unique=True, index=True)

    # Branding
    agent_name_on_report = db.Column(db.String(255))
    agent_biz_on_report  = db.Column(db.String(255))

    # Tracking
    buyer_viewed_at  = db.Column(db.DateTime)
    buyer_registered = db.Column(db.Boolean, default=False)
    buyer_converted  = db.Column(db.Boolean, default=False)
    view_count       = db.Column(db.Integer, default=0)
    has_text         = db.Column(db.Boolean, default=False)

    # Post-close tracking (v5.87.40 — flywheel completion)
    # Populated when the buyer's PostCloseSurvey reports `did_buy='yes_closed'`,
    # or when an agent self-reports via /api/agent/shares/<id>/close. Triggers
    # send_agent_postclose_email when set.
    deal_closed_at   = db.Column(db.DateTime, nullable=True, index=True)
    final_sale_price = db.Column(db.Float, nullable=True)

    @property
    def share_url(self):
        return f'/agent-report/{self.share_token}'

    def __repr__(self):
        return f'<AgentShare {self.property_address} buyer={self.buyer_email}>'


# ============================================================================
# AGENT BRIEFING (v5.88.75 — agent-as-analyst product)
# ============================================================================
#
# Distinct from AgentShare. AgentShare is the legacy "forward a link to your
# buyer who runs their own analysis" flow. AgentBriefing is the new
# "agent uploads a PDF, gets a one-page briefing with their commentary
# woven through cost + offer-strategy data, then shares it with their
# client" flow.
#
# The two coexist intentionally — AgentShare is kept for existing users
# under a "Legacy" nav label while AgentBriefing is the primary product
# going forward. Once design partners validate the briefing flow,
# AgentShare can be deprecated separately.

class AgentBriefing(db.Model):
    """A one-page property briefing prepared by an agent for their client.

    Agent inputs an inspection or disclosure PDF + buyer's budget tiers
    + their own commentary. OfferWise produces a deliverable styled as
    the agent's work: repair-cost analysis, offer-strategy scenarios,
    and a bottom-line recommendation, with the agent's commentary
    quoted prominently and OfferWise's branding minimized to a footer.

    The agent can either keep the briefing private (use it as their own
    pre-meeting prep) or share the link with their buyer/seller client.
    The shareable URL is /agent-briefing/<share_token>.
    """
    __tablename__ = 'agent_briefings'

    id               = db.Column(db.Integer, primary_key=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent_id         = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=False, index=True)
    agent_user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Property
    property_address = db.Column(db.String(500), nullable=False)
    property_price   = db.Column(db.Float)

    # Which side the agent represents — flips the offer-strategy framing.
    # Must be 'buyer' or 'seller'. Required at creation.
    representing    = db.Column(db.String(10), nullable=False)  # 'buyer' | 'seller'

    # Client (buyer or seller — terminology depends on `representing`)
    client_name      = db.Column(db.String(255))   # rendered in report header per design A
    client_email     = db.Column(db.String(255))   # optional; only used for send-to-client

    # Source document — extracted text from the agent's uploaded PDF.
    # The actual PDF is not retained; only the extracted text is stored,
    # matching the inspector-report pattern (avoids hosting third-party
    # PDFs and the retention/copyright questions that creates).
    inspection_text  = db.Column(db.Text)
    inspection_pdf_filename = db.Column(db.String(500))  # for display only

    # Buyer's budget tiers — only populated when representing='buyer'.
    # Null when representing='seller' (the strategy section there is
    # negotiation-defense oriented, not budget-anchored).
    budget_qualified  = db.Column(db.Float)  # max qualified by lender
    budget_comfortable = db.Column(db.Float)  # comfortable monthly payment range
    budget_preferred  = db.Column(db.Float)  # at or below this is the buyer's preferred

    # The agent's commentary — REQUIRED at creation. This is the input
    # that makes the deliverable feel like the agent's work, not
    # OfferWise's. Min 100 chars enforced at the route level (so the DB
    # constraint stays simple; the route returns a meaningful error
    # message rather than a constraint violation).
    agent_commentary = db.Column(db.Text, nullable=False)

    # Analysis output JSON, populated by the analysis pipeline (R2/R3).
    # Empty at R1 — the row is created, but analysis runs in a follow-up
    # release that wires the pipeline. R1 stores inputs only.
    analysis_json       = db.Column(db.Text)  # repair findings + ZIP cost estimates
    offer_strategy_json = db.Column(db.Text)  # 3 scenarios + rationales
    bottom_line          = db.Column(db.Text)  # 2-3 sentence summary

    # Sharing
    share_token      = db.Column(db.String(32), unique=True, index=True, nullable=False)

    # Branding snapshot — captured at creation so later edits to the
    # Agent profile don't retroactively change historical briefings.
    agent_name_on_report = db.Column(db.String(255))
    agent_biz_on_report  = db.Column(db.String(255))

    # Tracking
    client_viewed_at = db.Column(db.DateTime)
    view_count       = db.Column(db.Integer, default=0)
    sent_to_client_at = db.Column(db.DateTime)   # populated by R4 send-to-client UI

    @property
    def share_url(self):
        return f'/agent-briefing/{self.share_token}'

    def __repr__(self):
        return f'<AgentBriefing {self.property_address} for={self.client_email}>'


# ============================================================================
# AGENTIC MONITORING MODELS (v5.75.92)
# ============================================================================

class PropertyWatch(db.Model):
    """
    Active monitoring record for a property during contingency/escrow period.
    Created when a buyer completes an analysis and opts into monitoring.
    The agentic monitoring jobs scan all active watches daily.
    """
    __tablename__ = 'property_watches'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Owner
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    analysis_id     = db.Column(db.Integer, db.ForeignKey('analyses.id'), nullable=True)

    # Property
    address         = db.Column(db.String(500), nullable=False)
    latitude        = db.Column(db.Float)
    longitude       = db.Column(db.Float)
    asking_price    = db.Column(db.Float)           # price at time of analysis
    avm_at_analysis = db.Column(db.Float)           # RentCast AVM at time of analysis

    # Baselines (set at watch creation, diffed on each run)
    baseline_comps_json  = db.Column(db.Text)       # JSON list of comps at analysis time
    baseline_permits_json= db.Column(db.Text)       # JSON list of permits at analysis time

    # Status
    is_active       = db.Column(db.Boolean, default=True, index=True)
    expires_at      = db.Column(db.DateTime)        # auto-deactivates (default: 45 days)
    deactivated_reason = db.Column(db.String(100))  # 'expired', 'closed', 'cancelled', 'manual'

    # Linked professionals (who gets notified alongside buyer)
    inspector_report_id  = db.Column(db.Integer, db.ForeignKey('inspector_reports.id'), nullable=True)
    agent_share_id       = db.Column(db.Integer, db.ForeignKey('agent_shares.id'), nullable=True)
    # v5.88.85: briefings (the validated v0 agent product) also start watches,
    # linked here so the OfferWatch flywheel covers the briefing path the same
    # way it covers the legacy share path. Either share_id OR briefing_id will
    # be set per watch row, not both (a watch comes from one agent surface).
    agent_briefing_id    = db.Column(db.Integer, db.ForeignKey('agent_briefings.id'), nullable=True)
    contractor_lead_id   = db.Column(db.Integer, db.ForeignKey('contractor_leads.id'), nullable=True)

    # Ghost watch — buyer hasn't created an account yet; watch owned by professional
    # When buyer signs up with matching email, watch is re-linked to buyer's user_id
    ghost_buyer_email    = db.Column(db.String(255), nullable=True, index=True)
    owned_by_professional = db.Column(db.Boolean, default=False)  # True = inspector/agent owns it until buyer signs up

    # Last run tracking
    last_comps_check_at    = db.Column(db.DateTime)
    last_permit_check_at   = db.Column(db.DateTime)
    last_earthquake_check_at = db.Column(db.DateTime)
    last_price_check_at    = db.Column(db.DateTime)
    last_deadline_check_at = db.Column(db.DateTime)

    # v5.88.86: post-close survey tracking. The survey is the data-flywheel
    # input the thesis calls out as the "quality loop" — each response is a
    # row of predicted-vs-actual training data. Triggers when the watch is
    # approaching expiry (day 35-42 of a 45-day watch), buyer has an
    # account, and survey_sent_at is null. Setting this field is what makes
    # the cron job idempotent across all three source types (analysis,
    # inspector report, agent briefing) without needing extra survey-side
    # state tracking.
    survey_sent_at         = db.Column(db.DateTime)

    # ── Escrow deadlines (all optional — buyer sets after accepting offer) ──────
    # When set, the deadline monitor fires Claude reasoning as each date approaches.
    offer_accepted_date         = db.Column(db.Date, nullable=True)  # Day offer was accepted
    inspection_contingency_date = db.Column(db.Date, nullable=True)  # Inspection contingency removal
    loan_contingency_date       = db.Column(db.Date, nullable=True)  # Loan / financing contingency
    appraisal_contingency_date  = db.Column(db.Date, nullable=True)  # Appraisal contingency removal
    seller_response_deadline    = db.Column(db.Date, nullable=True)  # Seller must respond to repair req
    repair_completion_deadline  = db.Column(db.Date, nullable=True)  # Agreed repairs must be done by
    close_of_escrow_date        = db.Column(db.Date, nullable=True)  # Scheduled COE

    def __repr__(self):
        return f'<PropertyWatch {self.address} user={self.user_id} active={self.is_active}>'


class AgentAlert(db.Model):
    """
    Alerts generated by the agentic monitoring system.
    Stored for display in buyer/agent/inspector dashboards and email history.
    """
    __tablename__ = 'agent_alerts'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    watch_id        = db.Column(db.Integer, db.ForeignKey('property_watches.id'), nullable=False, index=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    # Alert content
    alert_type      = db.Column(db.String(50), nullable=False, index=True)
    # Types: 'new_comp', 'price_drop', 'earthquake', 'new_permit', 'buyer_concern'
    severity        = db.Column(db.String(20), default='info')   # info, warning, critical
    title           = db.Column(db.String(300), nullable=False)
    body            = db.Column(db.Text)
    detail_json     = db.Column(db.Text)                         # raw data that triggered the alert

    # Delivery
    email_sent      = db.Column(db.Boolean, default=False)
    email_sent_at   = db.Column(db.DateTime)
    read_at         = db.Column(db.DateTime)

    # v5.88.86: telemetry — captures email + portal engagement so we can
    # measure which alert types actually drive return visits, and tune
    # thresholds based on data instead of guesswork.
    #
    # resend_id links this alert to the EmailSendLog row produced when the
    # alert email went out. The Resend webhook writes EmailEvent rows
    # against the same resend_id when the recipient opens or clicks. To
    # compute open/click rates per alert_type, join AgentAlert→EmailEvent
    # via resend_id and aggregate. No denormalization needed — webhook
    # events stay authoritative.
    #
    # view_count is bumped on every /api/alerts call that returns this
    # alert. It conflates "appeared in the list" with "actually read", but
    # serves as a first-cut portal-engagement proxy. If signal-to-noise is
    # too low, we'll split into list-render vs explicit-click later.
    resend_id       = db.Column(db.String(100), index=True)
    view_count      = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<AgentAlert [{self.alert_type}] watch={self.watch_id}>'


# ============================================================================
# FLYWHEEL MODELS — v5.80.85
# ============================================================================

class ContractorJobCompletion(db.Model):
    """
    Records when a contractor completes a job sourced from OfferWise.
    Completion data feeds back into the repair cost estimate engine.
    """
    __tablename__ = 'contractor_job_completions'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Source
    lead_id         = db.Column(db.Integer, db.ForeignKey('contractor_leads.id'), nullable=True, index=True)
    claim_id        = db.Column(db.Integer, db.ForeignKey('contractor_lead_claims.id'), nullable=True)
    contractor_id   = db.Column(db.Integer, db.ForeignKey('contractors.id'), nullable=False, index=True)

    # Property
    property_address = db.Column(db.String(500))
    zip_code        = db.Column(db.String(10), index=True)

    # Job outcome
    won_job         = db.Column(db.Boolean, nullable=False)   # True=won, False=lost bid
    final_price     = db.Column(db.Float, nullable=True)      # actual price charged (if won)
    work_completed  = db.Column(db.String(500))               # comma-separated system names
    permit_uploaded = db.Column(db.Boolean, default=False)
    permit_number   = db.Column(db.String(100), nullable=True)

    # Estimate accuracy (set by engine after comparing to original estimate)
    original_estimate_low  = db.Column(db.Float, nullable=True)
    original_estimate_high = db.Column(db.Float, nullable=True)
    variance_pct    = db.Column(db.Float, nullable=True)   # (final - midpoint) / midpoint

    def __repr__(self):
        status = 'won' if self.won_job else 'lost'
        return f'<JobCompletion contractor={self.contractor_id} {status} ${self.final_price}>'

# Backward-compat alias
WatchAlert = AgentAlert


# ═══════════════════════════════════════════════════════════════
# ML TRAINING DATA TABLES
# ═══════════════════════════════════════════════════════════════

class MLFindingLabel(db.Model):
    """Gold-standard finding labels for training the finding classifier.
    Sources: inspector validation, Claude parsing (auto-collected), buyer edits.
    """
    __tablename__ = 'ml_finding_labels'

    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # The finding
    finding_text  = db.Column(db.Text, nullable=False)
    category      = db.Column(db.String(100), nullable=False, index=True)
    severity      = db.Column(db.String(50), nullable=False, index=True)

    # Label source and quality
    source        = db.Column(db.String(50), nullable=False, index=True)  # 'ai_parse', 'inspector_confirmed', 'inspector_corrected', 'inspector_rejected'
    confidence    = db.Column(db.Float, default=0.9)
    is_validated  = db.Column(db.Boolean, default=False)  # True if human-reviewed

    # Context
    analysis_id   = db.Column(db.Integer, nullable=True, index=True)
    report_id     = db.Column(db.Integer, nullable=True)   # inspector_report.id
    property_zip  = db.Column(db.String(10))
    property_price = db.Column(db.Float)

    # Inspector correction (if source='inspector_corrected')
    original_category = db.Column(db.String(100))
    original_severity = db.Column(db.String(50))

    # ── v5.86.77: Data quality scaffolding ──────────────────────────────
    # All columns below are nullable. Training code prefers v2 labels when
    # present, falls back to v1 (category/severity above) otherwise.
    #
    # source_version: which extractor or crawler produced this row. Examples:
    # 'ai_parse_v2' (re-extracted user uploads), 'zillow_v1' (first zillow
    # crawler), etc. NULL for legacy rows from before v5.86.77.
    source_version = db.Column(db.String(50), nullable=True)

    # Stream 3 re-labels (Claude-based). These overwrite the semantics of
    # category/severity when populated.
    category_v2 = db.Column(db.String(100), nullable=True)
    severity_v2 = db.Column(db.String(50), nullable=True)

    # Quality flag from Claude: is this actually a home inspection finding,
    # or is it boilerplate / metadata / municipal code that shouldn't train?
    is_real_finding = db.Column(db.Boolean, nullable=True)

    # Diversity tracking. Populated when known; NULL otherwise.
    geographic_region = db.Column(db.String(50), nullable=True)   # northeast, southeast, etc.
    property_age_bucket = db.Column(db.String(30), nullable=True)  # pre_1950, 1950_1980, etc.

    # Labeling metadata (from Stream 3 / Claude relabeling)
    labeling_confidence = db.Column(db.Float, nullable=True)
    labeling_notes = db.Column(db.Text, nullable=True)

    # v5.89.42: label-audit fields. Populated when an operator audit
    # decision touches this row. Snapshots the pre-audit values so the
    # change can be rolled back. excluded_from_training is set when an
    # auditor marks a finding as 'junk' — training fetchers respect it.
    # audit_suggested_category is free-form text from verdict 4 (need
    # new category); the field captures the intent but doesn't change
    # category/category_v2 until the taxonomy formally adopts the new
    # value in a future release.
    audit_modified_at = db.Column(db.DateTime, nullable=True)
    audit_original_category = db.Column(db.String(100), nullable=True)
    audit_original_severity = db.Column(db.String(50), nullable=True)
    audit_suggested_category = db.Column(db.String(100), nullable=True)
    excluded_from_training = db.Column(db.Boolean, default=False, nullable=False, index=True)

    # v5.89.47: bulk-relabel tracking. Distinct from audit_modified_at so
    # we can tell operator-audited rows from bulk-relabeled rows. Both
    # share the audit_original_* columns for rollback — first writer wins
    # on capturing the original, so we never overwrite the true original
    # with a previous bulk-relabeled value.
    last_relabel_at = db.Column(db.DateTime, nullable=True, index=True)
    last_relabel_confidence = db.Column(db.Float, nullable=True)

    # v5.87.22: composite index for the dedup query in
    # SocrataCrawler._add_unlabeled_finding which runs once per scraped row.
    # Without this, a 10K-row Chicago crawl performed 10,000 sequential
    # full-table scans against a ~91K-row table — observed to take 25-30
    # minutes, making "Crawl All" appear stuck on Chicago and never reaching
    # subsequent cities. With the index it should drop to ~3 minutes.
    # finding_text is capped at 500 chars by the inserter, so a plain btree
    # works (well under Postgres's 8KB index entry limit).
    __table_args__ = (
        db.Index(
            'ix_ml_finding_labels_dedup',
            'finding_text',
            'source_version',
        ),
    )


class MLIngestionJob(db.Model):
    """Tracks background data-quality work: re-extraction, crawling, relabeling.

    One row per batch. Status progresses queued → running → succeeded/failed.
    Purpose is to give the Diagnostics panel + admin UI visibility into what
    data work is happening and how it's going.

    Not the same as MLTrainingRun (which tracks model training runs). This
    table is specifically for data INGESTION / LABELING work.
    """
    __tablename__ = 'ml_ingestion_jobs'

    id             = db.Column(db.Integer, primary_key=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at     = db.Column(db.DateTime, nullable=True)
    completed_at   = db.Column(db.DateTime, nullable=True)

    # Classification: what kind of job this is and what produced it
    job_type       = db.Column(db.String(30), nullable=False)   # 'reextract' | 'crawl' | 'relabel' | 'cleanup'
    source         = db.Column(db.String(100), nullable=False)  # e.g. 'ai_parse_v2', 'zillow_v1', 'relabel_batch_1'
    status         = db.Column(db.String(20), nullable=False, default='queued', index=True)

    # Progress / results
    rows_processed = db.Column(db.Integer, nullable=True)
    rows_added     = db.Column(db.Integer, nullable=True)
    rows_rejected  = db.Column(db.Integer, nullable=True)
    elapsed_seconds = db.Column(db.Float, nullable=True)

    # Config + diagnostics
    config_json    = db.Column(db.Text, nullable=True)   # JSON: parameters this job ran with
    log_json       = db.Column(db.Text, nullable=True)   # JSON: progress log entries
    error          = db.Column(db.Text, nullable=True)   # populated if status='failed'

    # v5.87.3: Stale-job timeout (seconds). A 'running' job older than this
    # without completing is assumed dead (SIGKILL, OOM, deploy restart, etc.)
    # and gets auto-marked failed so new jobs can start. Different job types
    # have different runtime expectations — relabel on a huge batch is slow,
    # crawls are normally quick.
    STALE_TIMEOUTS = {
        'crawl':      2 * 3600,    # 2 hours
        'synthesize': 3 * 3600,    # 3 hours
        'relabel':    6 * 3600,    # 6 hours (full corpus pass can be long)
        'reextract':  4 * 3600,    # 4 hours
        'cleanup':    1 * 3600,    # 1 hour
    }
    DEFAULT_STALE_TIMEOUT = 2 * 3600

    @classmethod
    def sweep_stale(cls, job_type: str = None):
        """Mark any 'running' jobs past their type's stale timeout as failed.

        Called before checking for 'already running' conflicts so we don't
        block forever on zombie jobs from killed workers.

        Args:
          job_type: optional filter to only sweep one type. If None, sweeps all.

        Returns:
          list of ids that were swept (for logging).
        """
        import logging
        lg = logging.getLogger(__name__)
        now = datetime.utcnow()

        q = cls.query.filter_by(status='running')
        if job_type:
            q = q.filter_by(job_type=job_type)

        swept = []
        for job in q.all():
            if not job.started_at:
                continue
            elapsed = (now - job.started_at).total_seconds()
            timeout = cls.STALE_TIMEOUTS.get(job.job_type, cls.DEFAULT_STALE_TIMEOUT)
            if elapsed > timeout:
                job.status = 'failed'
                job.completed_at = now
                err_suffix = (f'Auto-marked failed by sweep_stale after {elapsed/3600:.1f}h '
                              f'(timeout={timeout/3600:.1f}h). Process likely killed by OOM, '
                              f'deploy restart, or worker crash without updating status.')
                job.error = (job.error + '\n' + err_suffix) if job.error else err_suffix
                swept.append(job.id)
                lg.warning(f'[sweep_stale] marked {job.job_type} job #{job.id} (source={job.source}) '
                           f'as failed — stuck in running for {elapsed/3600:.1f}h')

        if swept:
            from models import db
            db.session.commit()
        return swept


class MLContradictionPair(db.Model):
    """Training pairs for the contradiction detector.
    Auto-collected from cross-reference engine results.
    """
    __tablename__ = 'ml_contradiction_pairs'

    id               = db.Column(db.Integer, primary_key=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    seller_claim     = db.Column(db.Text, nullable=False)
    inspector_finding = db.Column(db.Text, nullable=False)
    label            = db.Column(db.String(30), nullable=False, index=True)  # 'contradiction', 'consistent', 'omission'
    confidence       = db.Column(db.Float, default=0.8)

    analysis_id      = db.Column(db.Integer, nullable=True, index=True)
    source           = db.Column(db.String(50), default='cross_ref_engine')  # 'cross_ref_engine', 'ai_cross_ref', 'human_review'


class MLCooccurrenceBucket(db.Model):
    """Finding co-occurrence baskets for the predictive model.
    Each row = one analysis's set of findings (as JSON list).
    """
    __tablename__ = 'ml_cooccurrence_buckets'

    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    analysis_id   = db.Column(db.Integer, nullable=True, index=True, unique=True)
    findings_set  = db.Column(db.Text, nullable=False)  # JSON: ["roofing:major", "plumbing:critical", ...]
    n_findings    = db.Column(db.Integer, default=0)
    property_zip  = db.Column(db.String(10))


class PostCloseSurvey(db.Model):
    """Buyer post-close feedback — predicted vs actual repair data.
    Holy grail training data for the repair cost model.
    """
    __tablename__ = 'post_close_surveys'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    token           = db.Column(db.String(64), unique=True, index=True, nullable=False)

    # Link to analysis
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    analysis_id     = db.Column(db.Integer, nullable=True, index=True)
    property_address = db.Column(db.String(500))

    # Survey state
    status          = db.Column(db.String(30), default='sent')  # 'sent', 'opened', 'completed'
    sent_at         = db.Column(db.DateTime)
    completed_at    = db.Column(db.DateTime)

    # Responses
    did_buy         = db.Column(db.String(30))    # 'yes_closed', 'walked_away', 'still_in_escrow'
    final_price     = db.Column(db.Float)
    repairs_needed  = db.Column(db.Text)           # JSON list: ["roofing", "plumbing"]
    repair_cost_range = db.Column(db.String(30))   # 'under_1k', '1k_5k', '5k_15k', '15k_30k', '30k_plus'
    surprises_text  = db.Column(db.Text)
    accuracy_rating = db.Column(db.Integer)        # 1-5 stars

    # Our predictions for comparison
    predicted_offer_low  = db.Column(db.Float)
    predicted_offer_high = db.Column(db.Float)
    predicted_repair_total = db.Column(db.Float)
    predicted_findings_count = db.Column(db.Integer)


class MLTrainingRun(db.Model):
    """Records every training run with accuracy metrics for trend tracking."""
    __tablename__ = 'ml_training_runs'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    trigger         = db.Column(db.String(30))  # 'manual', 'scheduled', 'auto'
    elapsed_seconds = db.Column(db.Float)

    # Finding Classifier
    fc_status       = db.Column(db.String(20))  # 'READY', 'MARGINAL', 'FAILED', 'SKIPPED'
    fc_category_acc = db.Column(db.Float)
    fc_severity_acc = db.Column(db.Float)
    fc_data_points  = db.Column(db.Integer)
    fc_augmented    = db.Column(db.Integer, default=0)
    fc_error        = db.Column(db.Text)  # Exception message if fc_status='FAILED'

    # Contradiction Detector
    cd_status       = db.Column(db.String(20))
    cd_accuracy     = db.Column(db.Float)
    cd_data_points  = db.Column(db.Integer)
    cd_error        = db.Column(db.Text)

    # Repair Cost
    rc_status       = db.Column(db.String(20))
    rc_r2           = db.Column(db.Float)
    rc_mae          = db.Column(db.Float)
    rc_median_pct   = db.Column(db.Float)
    rc_data_points  = db.Column(db.Integer)
    rc_error        = db.Column(db.Text)

    # Inference test results (run after training)
    inference_tested = db.Column(db.Boolean, default=False)
    inference_passed = db.Column(db.Integer, default=0)
    inference_failed = db.Column(db.Integer, default=0)
    inference_details = db.Column(db.Text)  # JSON

    def __repr__(self):
        return f'<MLTrainingRun {self.id} {self.created_at} fc={self.fc_category_acc} cd={self.cd_accuracy}>'


class MLMetricsSnapshot(db.Model):
    """Captures corpus + model state at a point in time so before/after
    comparisons across crawl/relabel/train cycles are explicit and verifiable.

    Added v5.87.20 because users were running the full pipeline blind —
    no way to compare "what did this expansion buy us." This snapshot table
    fills that gap for corpus-level metrics. Production ML override rate is
    NOT captured here because the underlying telemetry doesn't exist yet
    in the analyses table.
    """
    __tablename__ = 'ml_metrics_snapshots'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    label           = db.Column(db.String(100))  # e.g. "before-multicity-train" or "after"
    notes           = db.Column(db.Text)

    # Corpus stats (from ml_finding_labels)
    total_rows               = db.Column(db.Integer)
    rows_labeled             = db.Column(db.Integer)  # has category_v2 / severity_v2 set
    rows_unlabeled           = db.Column(db.Integer)
    rows_high_confidence     = db.Column(db.Integer)  # confidence >= 0.85
    sources_breakdown_json   = db.Column(db.Text)  # {source_version: count, ...}
    states_breakdown_json    = db.Column(db.Text)  # {state: count, ...}

    # Most recent training run (snapshot of metrics at this moment)
    last_training_run_id     = db.Column(db.Integer)
    last_training_at         = db.Column(db.DateTime)
    fc_category_acc          = db.Column(db.Float)
    fc_severity_acc          = db.Column(db.Float)
    cd_accuracy              = db.Column(db.Float)
    rc_r2                    = db.Column(db.Float)
    rc_mae                   = db.Column(db.Float)

    # Crawler registry state
    active_crawlers_json     = db.Column(db.Text)  # ["chicago", "seattle", ...]
    active_crawler_count     = db.Column(db.Integer)

    def __repr__(self):
        return f'<MLMetricsSnapshot {self.id} {self.label!r} @ {self.created_at}>'


class MLCostData(db.Model):
    """External repair cost data collected from public sources.
    Feeds the Repair Cost Predictor training alongside baseline + analysis data.
    """
    __tablename__ = 'ml_cost_data'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    finding_text    = db.Column(db.Text, nullable=False)
    category        = db.Column(db.String(50), index=True)
    severity        = db.Column(db.String(20), index=True)
    cost_low        = db.Column(db.Float)
    cost_high       = db.Column(db.Float)
    cost_mid        = db.Column(db.Float, nullable=False, index=True)
    zip_code        = db.Column(db.String(10), index=True)

    # Source attribution
    source          = db.Column(db.String(50), nullable=False, index=True)  # 'permit_la', 'homeadvisor', 'fema', 'inspector_label'
    source_meta     = db.Column(db.Text)  # JSON metadata

    # Deduplication
    content_hash    = db.Column(db.String(64), unique=True, index=True)

    def __repr__(self):
        return f'<MLCostData {self.source} ${self.cost_mid:,.0f} {self.category}>'


class MLAgentRun(db.Model):
    """Log of autonomous ML Agent pipeline runs."""
    __tablename__ = 'ml_agent_runs'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    elapsed_seconds = db.Column(db.Float)
    trigger         = db.Column(db.String(30), default='scheduled')  # 'scheduled', 'manual'

    # Phase results
    crawl_added     = db.Column(db.Integer, default=0)
    crawl_scanned   = db.Column(db.Integer, default=0)
    data_findings   = db.Column(db.Integer)
    data_pairs      = db.Column(db.Integer)
    data_costs      = db.Column(db.Integer)
    skipped_reason  = db.Column(db.String(200))  # null if training ran

    # Training result (if ran)
    trained         = db.Column(db.Boolean, default=False)
    fc_acc          = db.Column(db.Float)
    cd_acc          = db.Column(db.Float)
    rc_r2           = db.Column(db.Float)
    rolled_back     = db.Column(db.Boolean, default=False)
    rollback_reason = db.Column(db.String(200))

    # Full log
    agent_log       = db.Column(db.Text)  # JSON array

    def __repr__(self):
        return f'<MLAgentRun {self.id} trained={self.trained} rolled_back={self.rolled_back}>'


# =============================================================================
# OUTREACH (v5.87.29) — Founder customer-discovery + B2B prospect tracking
# =============================================================================
# Two cohorts share the same plumbing:
#   1. Buyer signups (existing User rows). We do NOT duplicate them — we look
#      them up live by user_id when rendering the Buyers tab. OutreachContact
#      rows here only exist for B2B prospects that aren't in the users table.
#   2. B2B prospects (manually added via admin UI). These have no User row.
#
# OutreachLog stores every send with linkage back to EmailSendLog.resend_id so
# opens/clicks from EmailEvent can be joined to the specific outreach attempt.
# =============================================================================


class OutreachContact(db.Model):
    """B2B prospects added manually for cold outreach.

    For buyer signups, we render directly from the User table — no row here.
    This table exists ONLY for prospects who aren't OfferWise users (enterprise
    targets like renovation lenders, insurtechs, brokerage tech).
    """
    __tablename__ = 'outreach_contacts'

    id = db.Column(db.Integer, primary_key=True)
    cohort = db.Column(db.String(20), nullable=False, default='b2b', index=True)
    # cohort values:
    #   'b2b' — manually-added enterprise prospect (this table is the source of truth)

    # Identity
    email = db.Column(db.String(255), nullable=False, index=True)
    name = db.Column(db.String(255))                # Contact's full name
    title = db.Column(db.String(255))               # Job title
    company = db.Column(db.String(255), index=True) # Company name
    linkedin_url = db.Column(db.String(500))

    # Segmentation
    wedge = db.Column(db.String(50), index=True)    # 'renovation_lenders', 'insurtechs', 'brokerage_tech', 'other'
    notes = db.Column(db.Text)                      # Free-form notes on the prospect

    # Status — manual flags the founder updates as they work the list
    status = db.Column(db.String(30), default='not_contacted', index=True)
    # status values:
    #   'not_contacted' — never emailed
    #   'contacted'     — at least one email sent, no reply yet
    #   'replied'       — they responded (manually flagged after seeing reply in Gmail)
    #   'meeting_set'   — a call is scheduled
    #   'design_partner'— signed as design partner (the win condition)
    #   'passed'        — declined or non-fit, archive

    last_contacted_at = db.Column(db.DateTime, index=True)
    replied_at = db.Column(db.DateTime)
    last_reply_summary = db.Column(db.Text)         # What they said (founder's note)

    # v5.87.48 — research + draft pipeline
    # focus_areas: 2-4 sentences synthesized from web search describing
    #   what the company is currently focused on. Used to personalize
    #   the draft. Empty string when research failed or was skipped.
    # draft_subject / draft_body: a personalized first-touch cold email
    #   ready for review. Generated by Claude conditioned on focus_areas
    #   and the prospect's role. NOT auto-sent — the founder reviews each
    #   one in the admin UI before clicking send.
    # draft_generated_at: when the draft was last regenerated (so the UI
    #   can show "stale draft, regenerate" if it's been a while).
    focus_areas = db.Column(db.Text)
    draft_subject = db.Column(db.String(500))
    draft_body = db.Column(db.Text)
    draft_generated_at = db.Column(db.DateTime)

    # Bookkeeping
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<OutreachContact {self.id} {self.email} {self.status}>'


class ProspectBlocklist(db.Model):
    """v5.88.00: Permanently blocked B2B prospect emails.

    A prospect goes here when the founder decides "never contact this
    person, ever, regardless of how many times the crawler re-finds
    them." Different from soft-delete or status='passed' because:
      - It survives crawler re-discovery (the discovery_crawler checks
        this table before inserting from Hunter/Snov results)
      - It survives manual re-add attempts (the POST /b2b endpoint
        rejects emails that are blocklisted)
      - It records WHY blocked, so the founder can review later

    Common reasons:
      - 'wrong_role' — wrong decision-maker level (e.g. junior PM at
        a Fortune 500, not the underwriting head we actually want)
      - 'departed' — known to have left the company we were targeting
      - 'do_not_contact' — explicit DNC request from the prospect
      - 'wrong_company' — company isn't a real fit despite domain match
      - 'manual' — founder gut-call, no specific reason
    """
    __tablename__ = 'prospect_blocklist'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)

    # Optional metadata at time of block — useful when reviewing later
    name_at_block = db.Column(db.String(255))
    title_at_block = db.Column(db.String(255))
    company_at_block = db.Column(db.String(255))

    reason = db.Column(db.String(50), default='manual', index=True)
    notes = db.Column(db.Text)  # Free-form note from the founder

    blocked_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    def __repr__(self):
        return f'<ProspectBlocklist {self.email} ({self.reason})>'


class OutreachLog(db.Model):
    """Every outreach email sent. One row per send.

    Joins to EmailSendLog.resend_id to pick up open/click events from
    EmailEvent. Joins to either users.id (for buyer cohort) or
    outreach_contacts.id (for b2b cohort) — exactly one of those will be set.
    """
    __tablename__ = 'outreach_log'

    id = db.Column(db.Integer, primary_key=True)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Cohort + foreign key — exactly one of (user_id, contact_id) is non-null
    cohort = db.Column(db.String(20), nullable=False, index=True)  # 'buyer' or 'b2b'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True, nullable=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('outreach_contacts.id'), index=True, nullable=True)

    # Snapshot of what was sent (so the audit trail survives even if the
    # prospect is later edited)
    to_email = db.Column(db.String(255), nullable=False, index=True)
    subject = db.Column(db.String(500))
    body = db.Column(db.Text)               # Full body text
    reply_to = db.Column(db.String(255))    # The from-which-replies-route address

    # Linkage to engagement tracking
    resend_id = db.Column(db.String(100), index=True)  # Matches EmailSendLog.resend_id
    success = db.Column(db.Boolean, default=True, nullable=False)
    error = db.Column(db.String(500))

    # v5.88.42: optional grouping for buyer cohort bulk sends.
    # Null for B2B single-prospect sends (kept compatible).
    campaign_id = db.Column(db.Integer, db.ForeignKey('outreach_campaigns.id'),
                            nullable=True, index=True)

    # v5.88.47: per-send reply tracking. B2B replies also still update
    # OutreachContact.status/replied_at/last_reply_summary (1-to-1 with the
    # contact). Buyer replies have no comparable home — same user may be
    # emailed across many campaigns, so reply state belongs to the send,
    # not the user. Populated by /api/admin/outreach/reply/<log_id>.
    replied_at = db.Column(db.DateTime, nullable=True)
    reply_summary = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<OutreachLog {self.id} {self.cohort} {self.to_email} at {self.sent_at}>'


# =============================================================================
# v5.88.42 — Prospect Outreach: templates, campaigns, unsubscribes
# =============================================================================
# Adds bulk-send to existing users (Buyer Users tab) on top of the existing
# B2B single-prospect flow. Three new tables work together:
#
#   OutreachTemplate    — reusable subject+body with {variable} placeholders
#   OutreachCampaign    — groups OutreachLog rows sent as one batch
#   OutreachUnsubscribe — global do-not-contact list, keyed by email
#
# Why a separate template table from TicketTemplate (v5.88.37)?
#   - Different concerns: ticket replies are conversational; outreach is
#     campaign-style
#   - Different variable sets: outreach uses {first_name}, {month_joined},
#     {days_since_signup}, {last_property_address}, {stage}, etc.
#   - Different audiences: admins editing them care about different things


class OutreachTemplate(db.Model):
    """Reusable outreach template with variable substitution.

    Variables supported in subject + body:
      {first_name}              from User.name (first token) or 'there'
      {email}                   recipient email
      {month_joined}            'February 2026' style
      {days_since_signup}       integer string
      {last_property_address}   from most recent Property, or '(no property)'
      {stage}                   SIGNED_UP_ONLY | ONBOARDED | etc.
      {unsubscribe_link}        auto-appended to footer if not present

    Unresolved variables are LEFT IN PLACE (same pattern as v5.88.37).
    The UI flags them in an amber warning so the admin sees the problem
    before sending.

    Templates are admin-only artifacts — no per-user/per-cohort scoping.
    """
    __tablename__ = 'outreach_templates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    cohort = db.Column(db.String(20), nullable=False, default='buyer')  # 'buyer' or 'b2b'
    subject_template = db.Column(db.String(500), nullable=False)
    body_template = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=100, nullable=False)
    is_seeded = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'cohort': self.cohort,
            'subject_template': self.subject_template,
            'body_template': self.body_template,
            'sort_order': self.sort_order,
            'is_seeded': self.is_seeded,
        }


class OutreachCampaign(db.Model):
    """Groups multiple OutreachLog sends as one logical batch.

    Created at send time. Stores a snapshot of the cohort criteria so the
    founder can later see 'who exactly did I email in this batch?'.

    Not used for B2B single-prospect sends — only for the cohort-style
    Buyer Users tab that batches.
    """
    __tablename__ = 'outreach_campaigns'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)  # admin-facing label
    cohort = db.Column(db.String(20), nullable=False, default='buyer')

    # The template at send time (for audit / reuse)
    template_id = db.Column(db.Integer, db.ForeignKey('outreach_templates.id'),
                            nullable=True)
    subject_template = db.Column(db.String(500), nullable=False)
    body_template = db.Column(db.Text, nullable=False)

    # Snapshot of cohort filter criteria at send time (so an audit later
    # answers 'who exactly was in the May 2026 USED_PRODUCT batch?')
    cohort_filter_json = db.Column(db.Text)  # JSON dump of filter params

    # Send addressing
    from_email = db.Column(db.String(255), nullable=False)
    reply_to_email = db.Column(db.String(255), nullable=False)

    # Counters (set as send progresses)
    recipient_count = db.Column(db.Integer, default=0, nullable=False)
    sent_count = db.Column(db.Integer, default=0, nullable=False)
    failed_count = db.Column(db.Integer, default=0, nullable=False)
    skipped_count = db.Column(db.Integer, default=0, nullable=False)  # unsub'd, bounced

    # Lifecycle
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    send_started_at = db.Column(db.DateTime, nullable=True)
    send_completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), default='draft', nullable=False)
    # status: 'draft' | 'sending' | 'completed' | 'partial' | 'failed'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'cohort': self.cohort,
            'template_id': self.template_id,
            'subject_template': self.subject_template,
            'body_template': self.body_template,
            'cohort_filter': json.loads(self.cohort_filter_json) if self.cohort_filter_json else None,
            'from_email': self.from_email,
            'reply_to_email': self.reply_to_email,
            'recipient_count': self.recipient_count,
            'sent_count': self.sent_count,
            'failed_count': self.failed_count,
            'skipped_count': self.skipped_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'send_started_at': self.send_started_at.isoformat() if self.send_started_at else None,
            'send_completed_at': self.send_completed_at.isoformat() if self.send_completed_at else None,
            'status': self.status,
        }


class OutreachUnsubscribe(db.Model):
    """Global do-not-contact list, keyed by email.

    A user (or any prospect) can land here three ways:
      1. They click an unsubscribe link in an outreach email (reason='manual')
      2. Their email bounces (reason='bounced') — set by future Resend webhook
      3. They mark our email as spam (reason='complained') — same future webhook

    Honored across BOTH cohorts (buyer + b2b). The send loop filters by
    lower(email) IN (this table) and skips them.

    Email-keyed (not user-keyed) so it survives email changes / deletions /
    anonymous prospects.
    """
    __tablename__ = 'outreach_unsubscribes'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    reason = db.Column(db.String(30), nullable=False, default='manual')
    # reason: 'manual' | 'bounced' | 'complained' | 'admin'
    campaign_id = db.Column(db.Integer, db.ForeignKey('outreach_campaigns.id'),
                            nullable=True)  # which campaign triggered the unsub
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    notes = db.Column(db.String(500))


# =============================================================================
# DISCOVERY QUEUE (v5.87.59) — nightly crawler queue for prospect discovery
# =============================================================================
# The 3:30am discovery job reads pending items from this table, runs
# discovery (Hunter → Snov fallback) on each domain, and creates
# OutreachContact rows for any prospects found. After discovery, the job
# also auto-runs research+draft on the new contacts (Level 2 automation)
# so the founder wakes up to drafts ready for review.
#
# Status state machine:
#   pending → running → completed (with prospects_found_count)
#   pending → running → failed (with error, retries up to 3 times)
#   pending → deferred (provider credit floor reached, retry next night)
#
# Items get queued from two sources:
#   1. Manual: founder clicks "Add to queue" in the admin UI
#   2. Autopilot: if pending count < N at 3:30am, top up from
#      _WEDGE_TOP_PLAYERS on a rotation
class DiscoveryQueueItem(db.Model):
    """Queued domain for the nightly discovery crawler."""
    __tablename__ = 'discovery_queue'

    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), nullable=False, index=True)
    wedge = db.Column(db.String(50))  # renovation_lenders, insurtechs, etc.

    # Provenance: 'manual' or 'autopilot' — useful for analytics on whether
    # the autopilot is finding companies the founder wouldn't have queued
    queued_by = db.Column(db.String(20), nullable=False, default='manual')
    queued_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # State machine
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_attempt_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    # Results
    prospects_found_count = db.Column(db.Integer, default=0)
    drafts_generated_count = db.Column(db.Integer, default=0)  # Level 2 auto-research
    source_used = db.Column(db.String(20))  # 'hunter', 'snov', or 'none'
    error = db.Column(db.String(1000))

    # Optional title/seniority filters captured at queue time
    title_filter = db.Column(db.String(500))      # comma-separated titles
    seniority_filter = db.Column(db.String(100))  # 'senior,executive,c_level'

    def __repr__(self):
        return f'<DiscoveryQueueItem {self.id} {self.domain} status={self.status}>'


# =============================================================================
# AD CAMPAIGN CONFIG (v5.87.38) — prepaid budget tracking per channel
# =============================================================================
# Daily spend rows live in GTMAdPerformance and are summed against this
# config's budget + date range to compute remaining budget. No FK between
# the two — the join is by (channel, date BETWEEN start_date AND end_date).
#
# Primary use case: Zillow Group ads, which are prepaid (load $501 for 30
# days, burn down). The same shape supports any channel that wants budget
# tracking — leave prepaid_budget NULL for postpay/PPC channels and the
# UI degrades to date-window-only display.
# =============================================================================


class AdCampaignConfig(db.Model):
    """Active campaign metadata, one row per channel.

    Endpoints + UI consuming this model:
      - admin_routes.py: _campaign_status helper, /api/admin/ad-campaign-config*,
        /api/admin/ad-campaigns*, /api/admin/zillow-ads-status
      - static/admin.html: Campaign Config panel in the GTM tab,
        prepaid budget context above Zillow Ads card metrics on Costs view
    """
    __tablename__ = 'ad_campaign_config'

    # Channel is the primary key — exactly one active campaign per channel
    # Values: 'zillow_ads', 'google_ads', 'reddit_ads', 'nextdoor', 'facebook_ads'
    channel = db.Column(db.String(50), primary_key=True)

    # Human-readable label, e.g. "Zillow Group Q2 2026"
    campaign_name = db.Column(db.String(200))

    # Prepaid budget — NULL means "not prepaid, treat as PPC postpay"
    prepaid_budget = db.Column(db.Float)

    # Campaign window. NULL end_date means open-ended (until manually closed).
    # start_date is required because it anchors the spend-window calculation.
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)

    # Free-form notes — billing terms, contact info, anything to remember
    notes = db.Column(db.Text)

    # Bookkeeping
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<AdCampaignConfig {self.channel} budget={self.prepaid_budget} window={self.start_date}..{self.end_date}>'


class PostgresTestRun(db.Model):
    """Records each run of the Postgres-portable test suite.

    Added v5.89.29 because the previous implementation stored results only
    in in-process memory (`_admin_jobs` dict in admin_routes.py), meaning
    every web service restart erased the history. The question "have these
    tests ever passed?" had no answer.

    This table persists one row per run. The summary JSON column carries
    the per-file pass/fail breakdown (small, fast to render in the
    history table). The log_excerpt text column carries up to 50KB of the
    pytest output for debugging failures — capped to prevent unbounded DB
    growth if many runs accumulate.

    Fields are intentionally close to MLTrainingRun's shape so the admin
    UI history pattern can be reused.
    """
    __tablename__ = 'postgres_test_runs'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    trigger         = db.Column(db.String(30), default='manual')  # 'manual' or 'scheduled' (future)
    elapsed_seconds = db.Column(db.Float)

    # Overall status: 'success' (all files passed) or 'failed' (any test failed)
    # or 'error' (couldn't run — e.g. URL invalid). Matches the value passed
    # to _job_finalize in the existing job runner.
    status          = db.Column(db.String(20))

    # Aggregate counts
    total_passed    = db.Column(db.Integer, default=0)
    total_failed    = db.Column(db.Integer, default=0)
    total_skipped   = db.Column(db.Integer, default=0)

    # Which DB the run hit (host + db name only; password redacted by caller)
    test_db_host    = db.Column(db.String(200))
    test_db_name    = db.Column(db.String(100))

    # Structured per-file breakdown as JSON:
    #   [{file: 'test_e2e_auth_signup.py', passed: 12, failed: 0, skipped: 1}, ...]
    # Plus failed_files: ['test_e2e_signup.py', ...]
    summary         = db.Column(db.Text)  # JSON

    # Log excerpt, capped at 50KB. Long pytest output is truncated head+tail
    # so both the summary line at the bottom AND the first failure trace are
    # preserved. See _truncate_log_excerpt in admin_routes.py.
    log_excerpt     = db.Column(db.Text)

    def __repr__(self):
        return f'<PostgresTestRun {self.id} {self.created_at} {self.status} {self.total_passed}p/{self.total_failed}f>'


class AccessRequest(db.Model):
    """v5.89.39: requests for access to gated investor materials
    (currently /architecture and /thesis).

    Workflow:
      1. Visitor submits the request form → row created with status='pending'
      2. Operator gets notified by email, clicks approve/deny in email or
         in the admin UI → status flips to 'approved' or 'denied'
      3. On approval, a magic_token is generated and emailed to the visitor
      4. Visitor clicks the magic link → token validated, single-use
         (consumed_at set), cookie set with cookie_token (90-day TTL)
      5. Subsequent visits check the cookie against approved_cookies table
         (we keep the cookie_token on this row for revocation simplicity)

    Rate limiting is enforced at the route level (the form endpoint uses
    a strict @limiter.limit). Honeypot field also enforced at route level.
    """
    __tablename__ = 'access_requests'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # What they submitted
    name            = db.Column(db.String(150), nullable=False)
    email           = db.Column(db.String(255), nullable=False, index=True)
    company         = db.Column(db.String(200))  # optional
    role            = db.Column(db.String(100))  # optional: "Investor", "Partner", etc.
    reason          = db.Column(db.Text)         # what they want it for
    page_requested  = db.Column(db.String(50))   # which page they hit ('/architecture' or '/thesis')

    # Bot detection / forensics
    ip_address      = db.Column(db.String(45))   # ipv6-capable length
    user_agent      = db.Column(db.String(500))

    # Workflow state
    status          = db.Column(db.String(20), default='pending', index=True)
    # 'pending', 'approved', 'denied', 'auto_approved'

    # Operator-side actions
    reviewed_at     = db.Column(db.DateTime)
    reviewed_by     = db.Column(db.String(200))  # admin email or 'auto' for trusted domains
    review_note     = db.Column(db.Text)         # optional internal note

    # Magic link (single-use, sent in approval email)
    magic_token     = db.Column(db.String(64), unique=True, index=True)
    magic_sent_at   = db.Column(db.DateTime)
    magic_consumed_at = db.Column(db.DateTime)   # set when they click the link

    # Browser cookie token (long-lived after magic link consumption)
    # Storing both lets us revoke a cookie without deleting the request row.
    cookie_token    = db.Column(db.String(64), unique=True, index=True)
    cookie_issued_at = db.Column(db.DateTime)
    cookie_expires_at = db.Column(db.DateTime)
    cookie_revoked  = db.Column(db.Boolean, default=False, nullable=False)

    # Last actual page view via this access (for audit)
    last_accessed_at = db.Column(db.DateTime)
    access_count    = db.Column(db.Integer, default=0, nullable=False)

    def __repr__(self):
        return f'<AccessRequest {self.id} {self.email} {self.status} for {self.page_requested}>'


class MLLabelAuditQueue(db.Model):
    """v5.89.42: queued misclassifications waiting for operator audit.

    Each row is one (finding, model_prediction) pair the operator needs
    to judge. Populated during training by ml_training_pipeline.py
    after the confusion-matrix step. Each training run produces one
    batch (linked via training_job_id — the per-run UUID).

    Why a string `training_job_id` rather than FK to MLTrainingRun.id:
    the MLTrainingRun row isn't created until the post_training stage,
    AFTER the finding_classifier stage where the audit queue is
    populated. The job_id UUID is the same value across all stages of
    one training cycle and is available from the start.

    The queue stores snapshots of the model's prediction at queue time
    rather than referencing live model state — so the audit decision
    is reproducible even if the model later changes.
    """
    __tablename__ = 'ml_label_audit_queue'

    id              = db.Column(db.Integer, primary_key=True)
    training_job_id = db.Column(db.String(64), nullable=False, index=True)
    finding_id      = db.Column(db.Integer, db.ForeignKey('ml_finding_labels.id'), nullable=False, index=True)
    queued_at       = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # What kind of error this represents — used by the sampler to ensure
    # balanced coverage across error types
    queue_type      = db.Column(db.String(20), nullable=False)
    # Values: 'category' | 'severity' | 'high_conf'

    # Useful for the report's confusion-pair breakdown
    confusion_pair  = db.Column(db.String(100))
    # Format: "roof_exterior→foundation_structure" (category) or
    #         "major→moderate" (severity)

    # Snapshot of the labels at queue time (so audit decision is
    # reproducible even if the corpus changes)
    original_category   = db.Column(db.String(100), nullable=False)
    original_severity   = db.Column(db.String(50),  nullable=False)
    predicted_category  = db.Column(db.String(100), nullable=False)
    predicted_severity  = db.Column(db.String(50),  nullable=False)

    # Model's confidence in its prediction (0.0–1.0). High confidence
    # AND wrong is a strong signal of either label noise or a systematic
    # model failure mode worth investigating.
    confidence_category = db.Column(db.Float, nullable=True)
    confidence_severity = db.Column(db.Float, nullable=True)

    # Text snippet preserved here so the audit can render even if the
    # original ml_finding_labels row is later deleted/excluded.
    text_snippet    = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f'<MLLabelAuditQueue {self.id} run={self.training_run_id} finding={self.finding_id} {self.queue_type}>'


class MLLabelAuditDecision(db.Model):
    """v5.89.42: operator's verdict on a queued misclassification.

    One row per audited queue entry. Captures the verdict and any
    side-effects on the corpus (e.g. category change for verdict 2,
    exclusion for verdict 5).

    Each decision is reversible: undo_decision_id points at a later
    decision that supersedes this one. For most decisions this is null.
    """
    __tablename__ = 'ml_label_audit_decisions'

    id              = db.Column(db.Integer, primary_key=True)
    queue_id        = db.Column(db.Integer, db.ForeignKey('ml_label_audit_queue.id'), nullable=False, index=True)
    decided_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    decided_by      = db.Column(db.String(200))  # admin email if available, else 'admin'

    # The five canonical verdicts:
    verdict         = db.Column(db.String(30), nullable=False, index=True)
    # 'original_correct'  : original label right, model wrong
    # 'predicted_correct' : model right, label was wrong (relabel pipeline bug)
    # 'both_defensible'   : taxonomy ambiguity
    # 'neither_correct'   : need a new category (suggested_category captures intent)
    # 'junk'              : should not be in the corpus (excluded_from_training=True)

    # Optional fields
    suggested_category = db.Column(db.String(100), nullable=True)  # for 'neither_correct'
    notes              = db.Column(db.Text, nullable=True)         # free-form
    needs_review       = db.Column(db.Boolean, default=False, nullable=False)  # operator unsure flag

    # Corpus mutation tracking. Populated when the verdict triggers a
    # mutation to ml_finding_labels:
    #   - verdict 'predicted_correct' → corpus_changed=True, sets audit_original_*
    #   - verdict 'neither_correct'   → corpus_changed=True (only suggested_category)
    #   - verdict 'junk'              → corpus_changed=True (excluded_from_training)
    #   - other verdicts              → corpus_changed=False
    corpus_changed     = db.Column(db.Boolean, default=False, nullable=False)
    rollback_payload   = db.Column(db.Text, nullable=True)  # JSON: { 'category': '...', 'severity': '...', 'excluded': bool }

    # If this decision was later reversed (e.g. operator hit Back), the
    # superseding decision points back at this one's id.
    superseded_by_id   = db.Column(db.Integer, db.ForeignKey('ml_label_audit_decisions.id'), nullable=True)
    is_active          = db.Column(db.Boolean, default=True, nullable=False, index=True)

    # v5.89.43: enforce "at most one ACTIVE decision per queue row" via
    # a partial unique index. Inactive (undone) rows are NOT subject to
    # the constraint, so the same queue_id can have many superseded
    # decisions across multiple undo/redo cycles. The original
    # `unique=True` on queue_id (shipped in v5.89.42) was wrong — it
    # blocked any second decision on the same queue row, even after
    # undo. See migration in app.py for the drop+create.
    __table_args__ = (
        db.Index(
            'uq_ml_label_audit_decisions_active_per_queue',
            'queue_id',
            unique=True,
            postgresql_where=db.text('is_active = true'),
        ),
    )

    def __repr__(self):
        return f'<MLLabelAuditDecision {self.id} queue={self.queue_id} verdict={self.verdict}>'


class MLRelabelRun(db.Model):
    """v5.89.47: bulk relabel job state.

    One row per relabel run (a single end-to-end pass over the corpus).
    Tracks progress, dry-run vs commit mode, threshold, results, and
    error state. Cooperative cancellation via the `cancel_requested`
    flag (same pattern as v5.89.34 crawl_abort_registry).

    Lifecycle:
      'queued' → 'running' → 'completed' | 'failed' | 'cancelled'

    On commit-mode completion, automatically schedules a follow-up
    training run (see relabel_pipeline.py).
    """
    __tablename__ = 'ml_relabel_runs'

    id              = db.Column(db.Integer, primary_key=True)
    job_id          = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at      = db.Column(db.DateTime, nullable=True)
    completed_at    = db.Column(db.DateTime, nullable=True)

    # Job parameters
    mode            = db.Column(db.String(20), nullable=False)  # 'dry_run' | 'commit'
    confidence_threshold = db.Column(db.Float, nullable=False)  # e.g. 0.90
    triggered_by    = db.Column(db.String(200))                 # 'admin' or email

    # Progress (updated as the job runs)
    status          = db.Column(db.String(20), default='queued', nullable=False, index=True)
    rows_total      = db.Column(db.Integer, default=0, nullable=False)
    rows_processed  = db.Column(db.Integer, default=0, nullable=False)
    rows_changed_category = db.Column(db.Integer, default=0, nullable=False)
    rows_changed_severity = db.Column(db.Integer, default=0, nullable=False)
    rows_low_confidence   = db.Column(db.Integer, default=0, nullable=False)
    rows_agreement        = db.Column(db.Integer, default=0, nullable=False)
    rows_failed     = db.Column(db.Integer, default=0, nullable=False)

    # Detailed stats (JSON blob)
    # For dry-run: distribution of changes by from-category/to-category
    # For commit: same, plus per-row mutation timestamps
    stats_json      = db.Column(db.Text, nullable=True)

    # Cancellation + error
    cancel_requested = db.Column(db.Boolean, default=False, nullable=False)
    error_message   = db.Column(db.Text, nullable=True)

    # v5.89.52: when did rows_processed last advance? Used by the status
    # endpoint to auto-detect zombie runs (subprocess died abruptly leaving
    # status='running' but no progress). Distinct from started_at (the
    # subprocess began) and completed_at (the subprocess finished cleanly).
    last_progress_at = db.Column(db.DateTime, nullable=True, index=True)

    # On commit-mode runs that triggered a follow-up training:
    triggered_training_job_id = db.Column(db.String(64), nullable=True)

    def __repr__(self):
        return f'<MLRelabelRun {self.id} {self.mode} {self.status} {self.rows_processed}/{self.rows_total}>'


class SystemSetting(db.Model):
    """Tiny generic key/value store for admin-controlled runtime settings.

    Created automatically by db.create_all() on startup — no migration needed.
    Used for feature flags an admin can flip from the dashboard without a deploy
    (e.g. reasoning_in_report). Values are stored as strings; helpers coerce.
    """
    __tablename__ = 'system_setting'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(500), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(200), nullable=True)  # admin email, for audit

    def __repr__(self):
        return f'<SystemSetting {self.key}={self.value}>'

    @staticmethod
    def get(key, default=None):
        """Return the stored string value for key, or default. Never raises."""
        try:
            row = SystemSetting.query.get(key)
            return row.value if row is not None else default
        except Exception:
            return default

    @staticmethod
    def set(key, value, updated_by=None):
        """Upsert a setting. Returns True on success, False on failure."""
        try:
            row = SystemSetting.query.get(key)
            if row is None:
                row = SystemSetting(key=key)
                db.session.add(row)
            row.value = None if value is None else str(value)
            if updated_by:
                row.updated_by = updated_by
            db.session.commit()
            return True
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            return False


class SharedRiskCheck(db.Model):
    """A persisted risk-check result with a shareable URL + OG preview card.

    Powers the viral loop: a completed scan becomes /r/<token>, which unfurls
    into a provocative preview image when shared, sending new visitors back
    into the scanner.
    """
    __tablename__ = 'shared_risk_checks'

    id            = db.Column(db.Integer, primary_key=True)
    token         = db.Column(db.String(16), unique=True, index=True, nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    address       = db.Column(db.String(300))
    city          = db.Column(db.String(120))
    state         = db.Column(db.String(40))
    risk_grade    = db.Column(db.String(4))
    risk_exposure = db.Column(db.Integer, default=0)
    risk_count    = db.Column(db.Integer, default=0)
    headline      = db.Column(db.String(300))
    result_json   = db.Column(db.Text)
    view_count    = db.Column(db.Integer, default=0)

    @staticmethod
    def new_token():
        return secrets.token_urlsafe(8).replace('-', '').replace('_', '')[:10]

    def __repr__(self):
        return f'<SharedRiskCheck {self.token} {self.risk_grade}>'
