"""
Database Migration Script - Add Terms Acceptance Fields
Run this ONCE after deploying to add terms_accepted_at and terms_version columns
"""

from app import app, db
from sqlalchemy import text

def run_migration():
    """Add terms acceptance fields to users table"""
    with app.app_context():
        try:
            # Check if columns already exist
            with db.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='users' AND column_name IN ('terms_accepted_at', 'terms_version')
                """))
                existing_columns = [row[0] for row in result]
            
            if 'terms_accepted_at' in existing_columns and 'terms_version' in existing_columns:
                print("✅ Columns already exist. No migration needed.")
                return
            
            print("🔄 Adding terms acceptance columns to users table...")
            
            # Add columns if they don't exist
            if 'terms_accepted_at' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN terms_accepted_at TIMESTAMP
                """))
                print("✅ Added terms_accepted_at column")
            
            if 'terms_version' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN terms_version VARCHAR(20) DEFAULT '1.0'
                """))
                print("✅ Added terms_version column")
            
            db.session.commit()
            print("✅ Migration completed successfully!")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    print("=" * 60)
    print("DATABASE MIGRATION: Add Terms Acceptance Fields")
    print("=" * 60)
    run_migration()
    print("=" * 60)
