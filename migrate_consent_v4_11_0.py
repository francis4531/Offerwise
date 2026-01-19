"""
Database Migration: Add Consent Tracking

CRITICAL: Run this migration before deploying v4.11.0

This adds the consent_records table for legal protection.
Tracks what users consent to, when, and from where.
"""

from models import db, ConsentRecord

def upgrade():
    """
    Add consent_records table
    """
    print("🔄 Creating consent_records table...")
    
    # Create the table
    db.create_all()
    
    print("✅ consent_records table created successfully")
    print("")
    print("Table structure:")
    print("  - id: Primary key")
    print("  - user_id: Foreign key to users table")
    print("  - consent_type: 'terms', 'privacy', 'disclaimer', 'analysis_disclaimer'")
    print("  - consent_version: Version they consented to")
    print("  - consent_text_hash: SHA-256 hash of exact text shown")
    print("  - consented_at: Timestamp")
    print("  - ip_address: For audit trail")
    print("  - user_agent: Browser/device info")
    print("  - analysis_id: Optional link to specific analysis")
    print("  - revoked: Boolean")
    print("  - revoked_at: Timestamp if revoked")
    print("")

def downgrade():
    """
    Remove consent_records table (NOT RECOMMENDED - loses legal protection)
    """
    print("⚠️ WARNING: Removing consent_records table")
    print("⚠️ This removes all consent history and legal protection!")
    response = input("Are you sure? Type 'yes' to confirm: ")
    
    if response.lower() == 'yes':
        db.session.execute('DROP TABLE IF EXISTS consent_records')
        db.session.commit()
        print("❌ consent_records table removed")
    else:
        print("✅ Migration cancelled")

if __name__ == '__main__':
    import sys
    from app import app
    
    with app.app_context():
        if len(sys.argv) > 1 and sys.argv[1] == 'downgrade':
            downgrade()
        else:
            upgrade()
