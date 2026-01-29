"""
Database Migration: Add Onboarding Tracking Columns
====================================================

This migration adds the onboarding_completed and onboarding_completed_at 
columns to the users table.

Run this ONCE on your production database.
"""

import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import User

def migrate_add_onboarding_columns():
    """Add onboarding tracking columns to users table"""
    
    print("\n" + "="*80)
    print("🔧 DATABASE MIGRATION: Add Onboarding Columns")
    print("="*80)
    
    with app.app_context():
        # Get database engine
        engine = db.engine
        connection = engine.connect()
        
        try:
            # Check if columns already exist
            print("\n📋 Step 1: Checking if columns already exist...")
            
            # Try to query the column (will fail if it doesn't exist)
            try:
                result = connection.execute(db.text(
                    "SELECT onboarding_completed FROM users LIMIT 1"
                ))
                result.close()
                print("   ✅ Column 'onboarding_completed' already exists")
                column_exists = True
            except Exception:
                print("   ⚠️  Column 'onboarding_completed' does NOT exist")
                column_exists = False
            
            if not column_exists:
                print("\n📋 Step 2: Adding columns to users table...")
                
                # PostgreSQL
                if 'postgresql' in str(engine.url):
                    print("   🐘 Detected PostgreSQL database")
                    connection.execute(db.text(
                        "ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE"
                    ))
                    connection.execute(db.text(
                        "ALTER TABLE users ADD COLUMN onboarding_completed_at TIMESTAMP"
                    ))
                    connection.commit()
                    print("   ✅ Columns added successfully (PostgreSQL)")
                
                # SQLite
                elif 'sqlite' in str(engine.url):
                    print("   💾 Detected SQLite database")
                    connection.execute(db.text(
                        "ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT 0"
                    ))
                    connection.execute(db.text(
                        "ALTER TABLE users ADD COLUMN onboarding_completed_at DATETIME"
                    ))
                    connection.commit()
                    print("   ✅ Columns added successfully (SQLite)")
                
                else:
                    print(f"   ⚠️  Unknown database type: {engine.url}")
                    print("   You may need to add columns manually")
                    return False
            
            # Verify columns exist
            print("\n📋 Step 3: Verifying columns...")
            result = connection.execute(db.text(
                "SELECT COUNT(*) as total FROM users WHERE onboarding_completed IS NULL"
            ))
            null_count = result.fetchone()[0]
            result.close()
            
            print(f"   ✅ Column verified: {null_count} users have NULL onboarding_completed")
            
            # Set default value for existing users
            print("\n📋 Step 4: Setting defaults for existing users...")
            
            # Count users with consents
            result = connection.execute(db.text("""
                SELECT COUNT(DISTINCT user_id) as count 
                FROM consent_records 
                WHERE consent_type IN ('terms', 'privacy', 'analysis_disclaimer')
            """))
            users_with_consents = result.fetchone()[0]
            result.close()
            
            print(f"   📊 Found {users_with_consents} users who have accepted legal terms")
            
            if users_with_consents > 0:
                print("   🔧 Marking these users as onboarding_completed = TRUE...")
                
                # Mark users who have all 3 consents as onboarding complete
                connection.execute(db.text("""
                    UPDATE users 
                    SET onboarding_completed = TRUE,
                        onboarding_completed_at = NOW()
                    WHERE id IN (
                        SELECT user_id 
                        FROM consent_records 
                        WHERE consent_type IN ('terms', 'privacy', 'analysis_disclaimer')
                        GROUP BY user_id 
                        HAVING COUNT(DISTINCT consent_type) >= 3
                    )
                """))
                connection.commit()
                
                # Verify
                result = connection.execute(db.text(
                    "SELECT COUNT(*) FROM users WHERE onboarding_completed = TRUE"
                ))
                completed_count = result.fetchone()[0]
                result.close()
                
                print(f"   ✅ {completed_count} users marked as onboarding_completed = TRUE")
            
            print("\n" + "="*80)
            print("✅ MIGRATION COMPLETED SUCCESSFULLY")
            print("="*80)
            print("\n📊 Summary:")
            
            # Final stats
            result = connection.execute(db.text("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN onboarding_completed = TRUE THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN onboarding_completed = FALSE OR onboarding_completed IS NULL THEN 1 ELSE 0 END) as not_completed
                FROM users
            """))
            stats = result.fetchone()
            result.close()
            
            print(f"   Total Users: {stats[0]}")
            print(f"   Onboarding Completed: {stats[1]}")
            print(f"   Onboarding Not Completed: {stats[2]}")
            print("\n")
            
            return True
            
        except Exception as e:
            print(f"\n❌ ERROR during migration: {e}")
            print(f"   Error type: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            connection.rollback()
            return False
        
        finally:
            connection.close()

if __name__ == "__main__":
    print("\n🚀 Starting migration...")
    success = migrate_add_onboarding_columns()
    
    if success:
        print("✅ Migration completed successfully!")
        print("\n💡 Next steps:")
        print("   1. Deploy the updated code (v5.02)")
        print("   2. Users should no longer see onboarding repeatedly")
        print("   3. Monitor logs to confirm")
        sys.exit(0)
    else:
        print("❌ Migration failed!")
        print("\n💡 You may need to manually add the columns:")
        print("   ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE;")
        print("   ALTER TABLE users ADD COLUMN onboarding_completed_at TIMESTAMP;")
        sys.exit(1)
