"""
Migration: Mark existing users with preferences as onboarding_completed
Run this ONCE after deploying v4.44
"""

from app import app, db
from models import User
from datetime import datetime

def migrate_existing_users():
    """Mark all users who have preferences as onboarding_completed"""
    
    with app.app_context():
        # Find all users who have preferences but onboarding_completed is not set
        users_with_prefs = User.query.filter(
            (User.max_budget.isnot(None)) | 
            (User.repair_tolerance.isnot(None)) | 
            (User.biggest_regret.isnot(None))
        ).filter(
            User.onboarding_completed == False
        ).all()
        
        print(f"Found {len(users_with_prefs)} users with preferences to migrate")
        
        for user in users_with_prefs:
            user.onboarding_completed = True
            user.onboarding_completed_at = datetime.utcnow()
            print(f"  ✅ Marked {user.email} as onboarding complete")
        
        db.session.commit()
        print(f"\n✅ Migration complete! {len(users_with_prefs)} users updated")

if __name__ == '__main__':
    migrate_existing_users()
