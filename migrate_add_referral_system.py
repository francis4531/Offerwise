"""
Migration: Add Referral System
Adds referral tracking, rewards, and user referral fields

Run with: python migrate_add_referral_system.py
"""

import os
from app import app, db
from models import User, Referral, ReferralReward
from sqlalchemy import text

def migrate():
    """Add referral system tables and fields"""
    
    with app.app_context():
        print("🔄 Starting referral system migration...")
        
        try:
            # Create new tables
            print("📊 Creating referral tables...")
            db.create_all()
            print("✅ Tables created successfully")
            
            # Add referral fields to existing users table (PostgreSQL)
            print("🔧 Adding referral columns to users table...")
            
            with db.engine.connect() as conn:
                # Check if columns exist before adding
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='referral_code'
                """))
                
                if result.fetchone() is None:
                    print("  Adding referral_code column...")
                    conn.execute(text("ALTER TABLE users ADD COLUMN referral_code VARCHAR(50)"))
                    conn.execute(text("CREATE UNIQUE INDEX idx_users_referral_code ON users(referral_code)"))
                    conn.commit()
                
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='referred_by_code'
                """))
                
                if result.fetchone() is None:
                    print("  Adding referred_by_code column...")
                    conn.execute(text("ALTER TABLE users ADD COLUMN referred_by_code VARCHAR(50)"))
                    conn.execute(text("CREATE INDEX idx_users_referred_by_code ON users(referred_by_code)"))
                    conn.commit()
                
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='referred_by_user_id'
                """))
                
                if result.fetchone() is None:
                    print("  Adding referred_by_user_id column...")
                    conn.execute(text("ALTER TABLE users ADD COLUMN referred_by_user_id INTEGER REFERENCES users(id)"))
                    conn.commit()
                
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='total_referrals'
                """))
                
                if result.fetchone() is None:
                    print("  Adding total_referrals column...")
                    conn.execute(text("ALTER TABLE users ADD COLUMN total_referrals INTEGER DEFAULT 0"))
                    conn.commit()
                
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='referral_tier'
                """))
                
                if result.fetchone() is None:
                    print("  Adding referral_tier column...")
                    conn.execute(text("ALTER TABLE users ADD COLUMN referral_tier INTEGER DEFAULT 0"))
                    conn.commit()
                
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='referral_credits_earned'
                """))
                
                if result.fetchone() is None:
                    print("  Adding referral_credits_earned column...")
                    conn.execute(text("ALTER TABLE users ADD COLUMN referral_credits_earned INTEGER DEFAULT 0"))
                    conn.commit()
            
            print("✅ User table columns added successfully")
            
            # Generate referral codes for existing users
            print("🎫 Generating referral codes for existing users...")
            users = User.query.filter_by(referral_code=None).all()
            count = 0
            
            for user in users:
                user.generate_referral_code()
                count += 1
                if count % 100 == 0:
                    print(f"  Generated {count} codes...")
            
            db.session.commit()
            print(f"✅ Generated {count} referral codes")
            
            print("\n🎉 Migration complete!")
            print("\n📊 Referral System Summary:")
            print(f"  Total users: {User.query.count()}")
            print(f"  Users with referral codes: {User.query.filter(User.referral_code != None).count()}")
            print(f"  Total referrals: {Referral.query.count()}")
            print(f"  Total rewards distributed: {ReferralReward.query.count()}")
            
            return True
            
        except Exception as e:
            print(f"\n❌ Migration failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == '__main__':
    migrate()
