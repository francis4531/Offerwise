"""
ONBOARDING DIAGNOSTIC TOOL
Checks database schema and onboarding status for all users
"""

import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import User
from sqlalchemy import inspect

def diagnose_onboarding():
    """Comprehensive onboarding diagnostics"""
    
    print("\n" + "="*100)
    print("🔍 ONBOARDING DIAGNOSTIC TOOL")
    print("="*100)
    
    with app.app_context():
        try:
            # Check 1: Database schema
            print("\n📊 STEP 1: Checking database schema...")
            print("-"*100)
            
            inspector = inspect(db.engine)
            columns = inspector.get_columns('users')
            column_names = [col['name'] for col in columns]
            
            print(f"\n📋 Users table has {len(column_names)} columns:")
            for col in columns:
                print(f"   - {col['name']}: {col['type']}")
            
            has_onboarding_completed = 'onboarding_completed' in column_names
            has_onboarding_completed_at = 'onboarding_completed_at' in column_names
            
            print("\n🔍 ONBOARDING COLUMNS:")
            if has_onboarding_completed:
                print(f"   ✅ onboarding_completed: EXISTS")
            else:
                print(f"   ❌ onboarding_completed: MISSING!")
            
            if has_onboarding_completed_at:
                print(f"   ✅ onboarding_completed_at: EXISTS")
            else:
                print(f"   ❌ onboarding_completed_at: MISSING!")
            
            # Check 2: User data
            print("\n📊 STEP 2: Checking user onboarding status...")
            print("-"*100)
            
            users = User.query.all()
            print(f"\n👥 Total users: {len(users)}")
            
            if not users:
                print("   ⚠️  No users in database")
                return
            
            for i, user in enumerate(users, 1):
                print(f"\n👤 User #{i}:")
                print(f"   Email: {user.email}")
                print(f"   ID: {user.id}")
                print(f"   Created: {user.created_at}")
                
                if has_onboarding_completed and has_onboarding_completed_at:
                    # Columns exist, check values
                    try:
                        completed = user.onboarding_completed
                        completed_at = user.onboarding_completed_at
                        
                        print(f"   Onboarding completed: {completed}")
                        print(f"   Completed at: {completed_at}")
                        
                        if completed:
                            print(f"   Status: ✅ Onboarding complete")
                        else:
                            print(f"   Status: ❌ Onboarding pending")
                            
                            # Check consent records
                            from models import ConsentRecord
                            consents = ConsentRecord.query.filter_by(user_id=user.id).all()
                            consent_types = set(c.consent_type for c in consents)
                            
                            print(f"   Consents given: {len(consents)} ({', '.join(consent_types)})")
                            
                            if len(consent_types) >= 3:
                                print(f"   ⚠️  User has given all consents but onboarding_completed=False!")
                                print(f"   💡 This indicates the flag was never set properly")
                    
                    except Exception as e:
                        print(f"   ❌ Error reading onboarding status: {e}")
                else:
                    print(f"   Status: ⚠️  Columns missing - cannot check status")
            
            # Check 3: Recommendations
            print("\n📊 STEP 3: Recommendations...")
            print("-"*100)
            
            if not has_onboarding_completed or not has_onboarding_completed_at:
                print("\n🚨 CRITICAL ISSUE DETECTED:")
                print("   ❌ Onboarding columns are MISSING from database")
                print("   ❌ Users will be forced to repeat onboarding every login")
                print("")
                print("🔧 TO FIX:")
                print("   Option 1 (Recommended):")
                print("      python migrate_add_onboarding.py")
                print("")
                print("   Option 2 (Manual SQL):")
                print("      ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE;")
                print("      ALTER TABLE users ADD COLUMN onboarding_completed_at TIMESTAMP;")
                print("")
                print("      UPDATE users SET onboarding_completed = TRUE, onboarding_completed_at = NOW()")
                print("      WHERE id IN (")
                print("          SELECT user_id FROM consent_records")
                print("          WHERE consent_type IN ('terms', 'privacy', 'analysis_disclaimer')")
                print("          GROUP BY user_id HAVING COUNT(DISTINCT consent_type) >= 3")
                print("      );")
            else:
                print("\n✅ Database schema is correct!")
                
                # Check if any users need their flag updated
                incomplete_users = []
                for user in users:
                    if not user.onboarding_completed:
                        from models import ConsentRecord
                        consents = ConsentRecord.query.filter_by(user_id=user.id).all()
                        consent_types = set(c.consent_type for c in consents)
                        if len(consent_types) >= 3:
                            incomplete_users.append(user)
                
                if incomplete_users:
                    print(f"\n⚠️  Found {len(incomplete_users)} users with consents but onboarding_completed=False")
                    print("   These users should have onboarding_completed=True but don't")
                    print("")
                    print("🔧 TO FIX:")
                    print(f"   Run this SQL:")
                    user_ids = [str(u.id) for u in incomplete_users]
                    print(f"      UPDATE users SET onboarding_completed = TRUE, onboarding_completed_at = NOW()")
                    print(f"      WHERE id IN ({', '.join(user_ids)});")
                else:
                    print("✅ All users have correct onboarding status!")
            
            print("\n" + "="*100)
            print("✅ DIAGNOSTIC COMPLETE")
            print("="*100 + "\n")
            
        except Exception as e:
            print(f"\n❌ ERROR during diagnostic: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    diagnose_onboarding()
