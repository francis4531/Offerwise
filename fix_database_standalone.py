"""
Standalone Database Fix Script - No Flask Required
Fix Properties with $0 Price using direct SQL
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
import json

def get_db_connection():
    """Get database connection from environment"""
    database_url = os.environ.get('DATABASE_URL')
    
    if not database_url:
        print("\n❌ ERROR: DATABASE_URL environment variable not set")
        print("\nPlease set it first:")
        print("  export DATABASE_URL='postgresql://user:pass@host:5432/dbname'")
        print("\nOr get it from Render:")
        print("  1. Go to Render Dashboard")
        print("  2. Click on your service")
        print("  3. Go to 'Environment' tab")
        print("  4. Copy DATABASE_URL value")
        return None
    
    try:
        # Handle Render's postgres:// vs postgresql://
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        
        conn = psycopg2.connect(database_url)
        print("✅ Connected to database")
        return conn
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None

def show_zero_price_properties(conn):
    """Show all properties with price = 0"""
    
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("""
        SELECT id, user_id, address, price, status, created_at
        FROM properties
        WHERE price = 0 OR price IS NULL
        ORDER BY created_at DESC
    """)
    
    properties = cursor.fetchall()
    
    if not properties:
        print("\n✅ No properties with $0 price found. Database is clean!")
        return
    
    print(f"\n{'='*70}")
    print(f"PROPERTIES WITH $0 PRICE: {len(properties)}")
    print(f"{'='*70}\n")
    
    for prop in properties:
        print(f"ID: {prop['id']}")
        print(f"  Address: {prop['address']}")
        print(f"  Price: ${prop['price'] if prop['price'] else 0}")
        print(f"  User ID: {prop['user_id']}")
        print(f"  Status: {prop['status']}")
        print(f"  Created: {prop['created_at']}")
        print()
    
    print(f"{'='*70}\n")
    cursor.close()

def fix_zero_price_properties(conn):
    """Fix all properties that have price = 0"""
    
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Find all properties with price = 0 or NULL
    cursor.execute("""
        SELECT id, user_id, address, price
        FROM properties
        WHERE price = 0 OR price IS NULL
        ORDER BY id
    """)
    
    properties = cursor.fetchall()
    
    if not properties:
        print("\n✅ No properties with $0 price found. Database is clean!")
        cursor.close()
        return
    
    print(f"\n{'='*70}")
    print(f"FOUND {len(properties)} PROPERTIES WITH $0 PRICE")
    print(f"{'='*70}\n")
    
    fixed_count = 0
    deleted_count = 0
    
    for prop in properties:
        property_id = prop['id']
        print(f"\nProperty ID: {property_id}")
        print(f"Address: {prop['address']}")
        print(f"Current Price: ${prop['price'] if prop['price'] else 0}")
        print(f"User ID: {prop['user_id']}")
        
        # Try to get price from analysis
        cursor.execute("""
            SELECT result_json
            FROM analyses
            WHERE property_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (property_id,))
        
        analysis = cursor.fetchone()
        
        if analysis:
            try:
                result_json = json.loads(analysis['result_json'])
                
                # Check if analysis has property_price
                if 'property_price' in result_json and result_json['property_price'] > 0:
                    # Use price from analysis
                    new_price = result_json['property_price']
                    
                    cursor.execute("""
                        UPDATE properties
                        SET price = %s
                        WHERE id = %s
                    """, (new_price, property_id))
                    
                    conn.commit()
                    print(f"✅ FIXED: Set price to ${new_price:,} from analysis")
                    fixed_count += 1
                else:
                    # Analysis also has no price - delete the property
                    print(f"⚠️ Analysis also has no price. DELETING property...")
                    
                    # Delete analysis first (foreign key)
                    cursor.execute("DELETE FROM analyses WHERE property_id = %s", (property_id,))
                    # Delete documents
                    cursor.execute("DELETE FROM documents WHERE property_id = %s", (property_id,))
                    # Delete property
                    cursor.execute("DELETE FROM properties WHERE id = %s", (property_id,))
                    
                    conn.commit()
                    deleted_count += 1
                    
            except Exception as e:
                print(f"❌ Error processing property {property_id}: {e}")
                conn.rollback()
        else:
            # No analysis exists - delete the property
            print(f"⚠️ No analysis found. DELETING property...")
            
            try:
                # Delete documents first (foreign key)
                cursor.execute("DELETE FROM documents WHERE property_id = %s", (property_id,))
                # Delete property
                cursor.execute("DELETE FROM properties WHERE id = %s", (property_id,))
                
                conn.commit()
                deleted_count += 1
            except Exception as e:
                print(f"❌ Error deleting property {property_id}: {e}")
                conn.rollback()
    
    print(f"\n{'='*70}")
    print(f"FIX COMPLETE")
    print(f"{'='*70}")
    print(f"✅ Fixed: {fixed_count} properties")
    print(f"🗑️ Deleted: {deleted_count} properties (no valid price found)")
    print(f"{'='*70}\n")
    
    cursor.close()

def delete_all_zero_price_properties(conn):
    """Delete ALL properties with price = 0 (nuclear option)"""
    
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("""
        SELECT id FROM properties
        WHERE price = 0 OR price IS NULL
    """)
    
    properties = cursor.fetchall()
    
    if not properties:
        print("\n✅ No properties with $0 price found.")
        cursor.close()
        return
    
    property_ids = [p['id'] for p in properties]
    
    print(f"\n⚠️ WARNING: This will delete {len(property_ids)} properties!")
    confirm = input("Type 'DELETE' to confirm: ")
    
    if confirm != 'DELETE':
        print("Cancelled.")
        cursor.close()
        return
    
    try:
        # Delete in correct order (foreign keys)
        for property_id in property_ids:
            cursor.execute("DELETE FROM analyses WHERE property_id = %s", (property_id,))
            cursor.execute("DELETE FROM documents WHERE property_id = %s", (property_id,))
            cursor.execute("DELETE FROM properties WHERE id = %s", (property_id,))
        
        conn.commit()
        print(f"✅ Deleted {len(property_ids)} properties with $0 price")
    except Exception as e:
        print(f"❌ Error: {e}")
        conn.rollback()
    
    cursor.close()

def main():
    print("\n" + "="*70)
    print("OFFERWISE DATABASE FIX - Properties with $0 Price")
    print("Standalone Version (No Flask Required)")
    print("="*70)
    
    # Get database connection
    conn = get_db_connection()
    
    if not conn:
        return
    
    try:
        import sys
        
        if len(sys.argv) > 1:
            if sys.argv[1] == '--show':
                show_zero_price_properties(conn)
            elif sys.argv[1] == '--fix':
                fix_zero_price_properties(conn)
            elif sys.argv[1] == '--delete-all':
                delete_all_zero_price_properties(conn)
            else:
                print("\nUsage:")
                print("  python fix_database_standalone.py --show         # Show properties with $0 price")
                print("  python fix_database_standalone.py --fix          # Fix properties (recommended)")
                print("  python fix_database_standalone.py --delete-all   # Delete all $0 properties (nuclear)")
        else:
            print("\nOptions:")
            print("  1. Show properties with $0 price")
            print("  2. Fix properties (recommended)")
            print("  3. Delete all $0 properties (nuclear option)")
            print("  4. Exit")
            print()
            
            choice = input("Enter choice (1-4): ").strip()
            
            if choice == '1':
                show_zero_price_properties(conn)
            elif choice == '2':
                fix_zero_price_properties(conn)
            elif choice == '3':
                delete_all_zero_price_properties(conn)
            elif choice == '4':
                print("Goodbye!")
            else:
                print("Invalid choice.")
    
    finally:
        conn.close()
        print("\n✅ Database connection closed")

if __name__ == '__main__':
    main()
