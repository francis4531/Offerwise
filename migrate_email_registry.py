"""
Migration: Add EmailRegistry table
Purpose: Prevent credit abuse by tracking emails that have received free credits
Date: January 21, 2026
Version: v5.12.0

CRITICAL SECURITY FIX: Closes exploit where users could delete accounts and re-signup
for unlimited free credits.
"""

import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import EmailRegistry, User
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate():
    """Create EmailRegistry table and populate with existing users"""
    
    with app.app_context():
        logger.info("=" * 80)
        logger.info("🔐 MIGRATION: Creating EmailRegistry table")
        logger.info("=" * 80)
        
        try:
            # Create the email_registry table
            logger.info("📋 Creating email_registry table...")
            db.create_all()
            logger.info("✅ Table created successfully")
            
            # Populate with existing users
            logger.info("")
            logger.info("👥 Populating registry with existing users...")
            
            users = User.query.all()
            logger.info(f"📊 Found {len(users)} existing users")
            
            added_count = 0
            skipped_count = 0
            
            for user in users:
                # Check if email already in registry
                existing = EmailRegistry.query.filter_by(email=user.email).first()
                
                if existing:
                    logger.info(f"⏭️  Skipping {user.email} (already in registry)")
                    skipped_count += 1
                    continue
                
                # Add to registry
                registry = EmailRegistry(
                    email=user.email,
                    first_signup_date=user.created_at or datetime.utcnow(),
                    has_received_free_credit=True,  # Assume existing users got their credit
                    free_credit_given_at=user.created_at or datetime.utcnow(),
                    times_deleted=0
                )
                
                db.session.add(registry)
                added_count += 1
                logger.info(f"✅ Added {user.email} to registry")
            
            # Commit all changes
            db.session.commit()
            
            logger.info("")
            logger.info("=" * 80)
            logger.info("✅ MIGRATION COMPLETE")
            logger.info("=" * 80)
            logger.info(f"📊 Summary:")
            logger.info(f"   - {added_count} emails added to registry")
            logger.info(f"   - {skipped_count} emails already existed")
            logger.info(f"   - Total in registry: {EmailRegistry.query.count()}")
            logger.info("")
            logger.info("🔒 Credit abuse prevention is now active!")
            logger.info("   - Emails can only receive 1 free credit EVER")
            logger.info("   - Account deletions are tracked")
            logger.info("   - Abuse is auto-detected after 3+ deletions")
            logger.info("=" * 80)
            
            return True
            
        except Exception as e:
            logger.error("")
            logger.error("=" * 80)
            logger.error("❌ MIGRATION FAILED")
            logger.error("=" * 80)
            logger.error(f"Error: {str(e)}")
            logger.error("")
            db.session.rollback()
            raise

if __name__ == '__main__':
    migrate()
