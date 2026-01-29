"""
Quick Fix: Give francis@piotnetworks testing credits
Purpose: Allow continued testing without migration hassle
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import User
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def give_testing_credits():
    """Give francis@piotnetworks 10 credits for testing"""
    
    with app.app_context():
        logger.info("=" * 80)
        logger.info("🧪 GIVING TESTING CREDITS")
        logger.info("=" * 80)
        
        try:
            # Find francis@piotnetworks
            user = User.query.filter_by(email='francis@piotnetworks').first()
            
            if not user:
                logger.error("❌ User francis@piotnetworks not found!")
                logger.info("Available users:")
                all_users = User.query.all()
                for u in all_users:
                    logger.info(f"  - {u.email}")
                return False
            
            # Give 10 testing credits
            old_credits = user.analysis_credits
            user.analysis_credits = 10
            
            db.session.commit()
            
            logger.info("")
            logger.info("✅ SUCCESS!")
            logger.info(f"📧 User: {user.email}")
            logger.info(f"💳 Credits before: {old_credits}")
            logger.info(f"💳 Credits now: {user.analysis_credits}")
            logger.info("")
            logger.info("🧪 You can now continue testing!")
            logger.info("=" * 80)
            
            return True
            
        except Exception as e:
            logger.error("")
            logger.error("❌ FAILED")
            logger.error(f"Error: {str(e)}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    give_testing_credits()
