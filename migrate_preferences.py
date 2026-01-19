"""
Database Migration: Add User Preferences Fields
Version: v4.30.0
Date: January 17, 2026

Adds buyer preference fields to User model:
- max_budget (Integer, default 2000000)
- repair_tolerance (String, default 'Moderate')
- biggest_regret (Text, default 'Overpaying')
"""

# For SQLite (if you're using SQLite in development)
SQLITE_MIGRATION = """
ALTER TABLE users ADD COLUMN max_budget INTEGER DEFAULT 2000000;
ALTER TABLE users ADD COLUMN repair_tolerance VARCHAR(50) DEFAULT 'Moderate';
ALTER TABLE users ADD COLUMN biggest_regret TEXT DEFAULT 'Overpaying';
"""

# For PostgreSQL (if you're using PostgreSQL in production)
POSTGRESQL_MIGRATION = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS max_budget INTEGER DEFAULT 2000000;
ALTER TABLE users ADD COLUMN IF NOT EXISTS repair_tolerance VARCHAR(50) DEFAULT 'Moderate';
ALTER TABLE users ADD COLUMN IF NOT EXISTS biggest_regret TEXT DEFAULT 'Overpaying';
"""

# Python migration using SQLAlchemy (safest approach)
PYTHON_MIGRATION = """
from sqlalchemy import text
from app_with_auth import app, db

with app.app_context():
    # Check if columns already exist
    inspector = db.inspect(db.engine)
    existing_columns = [col['name'] for col in inspector.get_columns('users')]
    
    # Add max_budget if it doesn't exist
    if 'max_budget' not in existing_columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN max_budget INTEGER DEFAULT 2000000'))
        print('✅ Added max_budget column')
    
    # Add repair_tolerance if it doesn't exist  
    if 'repair_tolerance' not in existing_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN repair_tolerance VARCHAR(50) DEFAULT 'Moderate'"))
        print('✅ Added repair_tolerance column')
    
    # Add biggest_regret if it doesn't exist
    if 'biggest_regret' not in existing_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN biggest_regret TEXT DEFAULT 'Overpaying'"))
        print('✅ Added biggest_regret column')
    
    db.session.commit()
    print('✅ Migration complete!')
"""

# Flask-Migrate version (if you're using Flask-Migrate)
FLASK_MIGRATE_INSTRUCTIONS = """
# If you're using Flask-Migrate, run:
flask db migrate -m "Add user preferences fields"
flask db upgrade
"""

if __name__ == '__main__':
    print("=" * 70)
    print("DATABASE MIGRATION: Add User Preferences")
    print("=" * 70)
    print("\nChoose your migration method:\n")
    print("1. Run Python migration (recommended)")
    print("2. Manually run SQL (SQLite)")
    print("3. Manually run SQL (PostgreSQL)")
    print("4. Use Flask-Migrate")
    print("\nOR: Let Render auto-migrate on deployment (if using Render)")
    print("=" * 70)
