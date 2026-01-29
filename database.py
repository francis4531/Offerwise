"""
OfferWise Database Models
User authentication and credit management
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User model with credit balance"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255))
    
    # OAuth fields
    oauth_provider = db.Column(db.String(50))  # 'google', 'github', etc.
    oauth_id = db.Column(db.String(255))
    
    # Credit balance
    credits = db.Column(db.Integer, default=1)  # Start with 1 free credit
    total_credits_purchased = db.Column(db.Integer, default=0)
    analyses_completed = db.Column(db.Integer, default=0)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    
    # Relationships
    transactions = db.relationship('CreditTransaction', backref='user', lazy='dynamic')
    
    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify password"""
        return check_password_hash(self.password_hash, password)
    
    def has_credits(self, required=1):
        """Check if user has enough credits"""
        return self.credits and self.credits >= required
    
    def __repr__(self):
        return f'<User {self.email}>'


class CreditTransaction(db.Model):
    """Transaction history for credits"""
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


class PropertyAnalysis(db.Model):
    """Store analysis results for user dashboard"""
    __tablename__ = 'property_analyses'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Property details
    address = db.Column(db.String(500))
    price = db.Column(db.Float)
    
    # Analysis results (stored as JSON)
    risk_tier = db.Column(db.String(20))  # CRITICAL, HIGH, MODERATE, LOW
    overall_risk_score = db.Column(db.Integer)
    recommended_offer = db.Column(db.Float)
    
    # Analysis data (full JSON)
    analysis_data = db.Column(db.JSON)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('analyses', lazy='dynamic'))
    
    def __repr__(self):
        return f'<Analysis {self.id}: {self.address}>'
