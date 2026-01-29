#!/usr/bin/env python3
"""
Migration script to add AI analysis columns to bugs table
Run this once after deploying v5.54.24
"""

import os
import sys

# Add the app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text

def migrate():
    """Add AI columns to bugs table"""
    
    columns_to_add = [
        ("ai_analysis", "TEXT"),
        ("ai_suggested_fix", "TEXT"),
        ("ai_confidence", "VARCHAR(20)"),
        ("ai_analyzed_at", "TIMESTAMP"),
        ("ai_fix_approved", "BOOLEAN DEFAULT FALSE"),
    ]
    
    with app.app_context():
        # Check if bugs table exists
        result = db.session.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'bugs'
            );
        """))
        table_exists = result.scalar()
        
        if not table_exists:
            print("Creating bugs table from scratch...")
            db.create_all()
            print("✅ Bugs table created!")
            return
        
        # Add columns if they don't exist
        for col_name, col_type in columns_to_add:
            try:
                # Check if column exists
                result = db.session.execute(text(f"""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_name = 'bugs' AND column_name = '{col_name}'
                    );
                """))
                col_exists = result.scalar()
                
                if not col_exists:
                    print(f"Adding column {col_name}...")
                    db.session.execute(text(f"""
                        ALTER TABLE bugs ADD COLUMN {col_name} {col_type};
                    """))
                    db.session.commit()
                    print(f"✅ Added {col_name}")
                else:
                    print(f"⏭️  Column {col_name} already exists")
                    
            except Exception as e:
                print(f"❌ Error adding {col_name}: {e}")
                db.session.rollback()
        
        print("\n✅ Migration complete!")

if __name__ == "__main__":
    migrate()
