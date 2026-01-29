"""
Quick Diagnostic: Check if onboarding columns exist
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import inspect

def check_onboarding_columns():
    """Check if onboarding columns exist in users table"""
    
    print("\n" + "="*80)
    print("🔍 DIAGNOSTIC: Checking Onboarding Columns")
    print("="*80)
    
    with app.app_context():
        try:
            # Get inspector
            inspector = inspect(db.engine)
            
            # Get columns from users table
            columns = inspector.get_columns('users')
            column_names = [col['name'] for col in columns]
            
            print(f"\n📋 Found {len(column_names)} columns in 'users' table")
            
            # Check for onboarding columns
            has_onboarding_completed = 'onboarding_completed' in column_names
            has_onboarding_completed_at = 'onboarding_completed_at' in column_names
            
            print("\n🔍 Onboarding Columns Check:")
            print(f"   onboarding_completed: {'✅ EXISTS' if has_onboarding_completed else '❌ MISSING'}")
            print(f"   onboarding_completed_at: {'✅ EXISTS' if has_onboarding_completed_at else '❌ MISSING'}")
            
            if has_onboarding_completed and has_onboarding_completed_at:
                print("\n✅ GOOD NEWS: Both columns exist!")
                print("\n🔍 Checking user data...")
                
                # Check user data
                result = db.session.execute(db.text(
                    "SELECT COUNT(*) as total FROM users"
                ))
                total_users = result.fetchone()[0]
                
                result = db.session.execute(db.text(
                    "SELECT COUNT(*) as completed FROM users WHERE onboarding_completed = TRUE"
                ))
                completed_users = result.fetchone()[0]
                
                print(f"   Total users: {total_users}")
                print(f"   Completed onboarding: {completed_users}")
                print(f"   Not completed: {total_users - completed_users}")
                
                if completed_users > 0:
                    print("\n✅ COLUMNS EXIST AND HAVE DATA")
                    print("\n💡 If onboarding still repeating, the issue is in the code logic.")
                    print("   Check app.py check_onboarding_required() function.")
                else:
                    print("\n⚠️  COLUMNS EXIST BUT NO USERS MARKED AS COMPLETED")
                    print("\n💡 Run this to mark existing users as completed:")
                    print("   python migrate_add_onboarding.py")
                
            else:
                print("\n❌ PROBLEM FOUND: Columns are missing!")
                print("\n🔧 SOLUTION: Run the migration script:")
                print("   python migrate_add_onboarding.py")
                print("\nOR manually add columns with SQL:")
                print("   ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE;")
                print("   ALTER TABLE users ADD COLUMN onboarding_completed_at TIMESTAMP;")
            
            print("\n" + "="*80)
            
            # Show all columns for reference
            print("\n📋 All columns in users table:")
            for col in sorted(column_names):
                print(f"   - {col}")
            
            print("\n" + "="*80)
            
            return has_onboarding_completed and has_onboarding_completed_at
            
        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            print(f"   Type: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    print("\n🚀 Running diagnostic...")
    success = check_onboarding_columns()
    
    if success:
        print("\n✅ Diagnostic complete - columns exist")
        sys.exit(0)
    else:
        print("\n❌ Diagnostic complete - migration needed")
        sys.exit(1)
