"""
Database Migration: Add Apple and Facebook OAuth fields

Run this to add apple_id and facebook_id columns to existing User table.

For SQLite:
    python migrate_oauth_fields.py

For PostgreSQL:
    Update the connection string and run
"""

import os
import sys
from sqlalchemy import create_engine, text

# Get database URL from environment or use SQLite default
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///offerwise.db')

print(f"🔄 Migrating database: {DATABASE_URL}")

try:
    engine = create_engine(DATABASE_URL)
    
    with engine.connect() as conn:
        # Check if columns already exist
        try:
            conn.execute(text("SELECT apple_id FROM users LIMIT 1"))
            print("✅ apple_id column already exists")
        except:
            print("➕ Adding apple_id column...")
            conn.execute(text("ALTER TABLE users ADD COLUMN apple_id VARCHAR(255)"))
            conn.execute(text("CREATE UNIQUE INDEX idx_users_apple_id ON users(apple_id) WHERE apple_id IS NOT NULL"))
            conn.commit()
            print("✅ Added apple_id column")
        
        try:
            conn.execute(text("SELECT facebook_id FROM users LIMIT 1"))
            print("✅ facebook_id column already exists")
        except:
            print("➕ Adding facebook_id column...")
            conn.execute(text("ALTER TABLE users ADD COLUMN facebook_id VARCHAR(255)"))
            conn.execute(text("CREATE UNIQUE INDEX idx_users_facebook_id ON users(facebook_id) WHERE facebook_id IS NOT NULL"))
            conn.commit()
            print("✅ Added facebook_id column")
    
    print("🎉 Migration completed successfully!")
    
except Exception as e:
    print(f"❌ Migration failed: {e}")
    print("\nAlternatively, run these SQL commands manually:")
    print("""
    -- SQLite / PostgreSQL
    ALTER TABLE users ADD COLUMN apple_id VARCHAR(255);
    ALTER TABLE users ADD COLUMN facebook_id VARCHAR(255);
    CREATE UNIQUE INDEX idx_users_apple_id ON users(apple_id) WHERE apple_id IS NOT NULL;
    CREATE UNIQUE INDEX idx_users_facebook_id ON users(facebook_id) WHERE facebook_id IS NOT NULL;
    """)
    sys.exit(1)
