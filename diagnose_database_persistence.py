"""
EMERGENCY DATABASE DIAGNOSTIC
Check why database persistence is failing
"""

import os
import sys

print("=" * 100)
print("🚨 EMERGENCY DATABASE DIAGNOSTIC")
print("=" * 100)

# Check DATABASE_URL
database_url = os.environ.get('DATABASE_URL')
print(f"\n1. DATABASE_URL environment variable:")
if database_url:
    print(f"   ✅ SET")
    # Mask password
    if '@' in database_url:
        parts = database_url.split('@')
        masked = parts[0].split(':')[0] + ':****@' + '@'.join(parts[1:])
        print(f"   Value: {masked}")
    else:
        print(f"   Value: {database_url}")
else:
    print(f"   ❌ NOT SET - Will use SQLite (data will be lost on each deploy!)")

# Check what the app will use
from app import app
actual_db = app.config.get('SQLALCHEMY_DATABASE_URI')
print(f"\n2. Actual database being used:")
print(f"   {actual_db}")

if 'sqlite' in actual_db.lower():
    print(f"   ⚠️  WARNING: Using SQLite - data will NOT persist across deploys!")
    print(f"   ⚠️  Each deploy creates a NEW empty database!")
elif 'postgres' in actual_db.lower():
    print(f"   ✅ Using PostgreSQL - data should persist across deploys")
else:
    print(f"   ⚠️  Unknown database type")

# Check if database file exists (SQLite)
if 'sqlite' in actual_db.lower():
    import re
    db_path = re.search(r'sqlite:///(.+)', actual_db)
    if db_path:
        db_file = db_path.group(1)
        import os.path
        if os.path.exists(db_file):
            file_size = os.path.getsize(db_file)
            print(f"\n3. SQLite file status:")
            print(f"   File: {db_file}")
            print(f"   Exists: YES")
            print(f"   Size: {file_size:,} bytes")
        else:
            print(f"\n3. SQLite file status:")
            print(f"   File: {db_file}")
            print(f"   Exists: NO (will be created on first use)")

# Check user count
with app.app_context():
    from models import User
    try:
        user_count = User.query.count()
        print(f"\n4. Users in database:")
        print(f"   Count: {user_count}")
        
        if user_count > 0:
            users = User.query.all()
            for u in users:
                print(f"   - {u.email} (ID: {u.id}, Onboarding: {getattr(u, 'onboarding_completed', 'N/A')})")
        else:
            print(f"   ⚠️  NO USERS - Database is empty!")
    except Exception as e:
        print(f"   ❌ Error querying users: {e}")

print("\n" + "=" * 100)
print("🔍 DIAGNOSIS:")
print("=" * 100)

if not database_url:
    print("""
❌ PROBLEM FOUND: DATABASE_URL is not set!

This means:
- App is using SQLite (file-based database)
- SQLite files are NOT persistent on Render
- Each deploy creates a fresh empty database
- All user data, consents, preferences are LOST on every deploy!

🔧 TO FIX:
1. Go to Render Dashboard
2. Click on your service (Offerwise-docker)
3. Go to "Environment" tab
4. Add DATABASE_URL variable with your PostgreSQL connection string
   Format: postgresql://user:password@host:port/database
5. If you don't have a PostgreSQL database:
   - Go to Render Dashboard
   - Create a new PostgreSQL database
   - Copy the "Internal Database URL"
   - Set it as DATABASE_URL in your web service

After setting DATABASE_URL:
- Redeploy (or wait for auto-deploy)
- Data will persist across all future deploys!
""")
elif 'sqlite' in actual_db.lower():
    print("""
⚠️  WARNING: Still using SQLite even though DATABASE_URL might be set

Possible causes:
- DATABASE_URL is set but app is ignoring it
- There's a bug in database configuration
- Need to restart app for changes to take effect

Check Render logs for database connection messages.
""")
else:
    print("""
✅ GOOD: Using PostgreSQL

If you're still losing data, check:
1. Are you connecting to the right database?
2. Is the database itself being dropped/recreated?
3. Check Render database dashboard for the database state
""")

print("=" * 100)
