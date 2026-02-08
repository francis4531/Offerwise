"""
Cache Clear Utility - Run this to clear old cached analyses
Use this if you're still seeing $N/A after deploying v2.3.5
"""

import os
import sqlite3
from datetime import datetime

def clear_analysis_cache():
    """Clear all cached analysis results"""
    
    cache_db_path = 'analysis_cache.db'
    
    if not os.path.exists(cache_db_path):
        print("❌ Cache database not found. Nothing to clear.")
        return
    
    try:
        conn = sqlite3.connect(cache_db_path)
        cursor = conn.cursor()
        
        # Get count before deletion
        cursor.execute('SELECT COUNT(*) FROM analysis_cache')
        count_before = cursor.fetchone()[0]
        
        # Delete all entries
        cursor.execute('DELETE FROM analysis_cache')
        conn.commit()
        
        # Get count after deletion
        cursor.execute('SELECT COUNT(*) FROM analysis_cache')
        count_after = cursor.fetchone()[0]
        
        conn.close()
        
        print(f"\n{'='*60}")
        print(f"CACHE CLEARED")
        print(f"{'='*60}")
        print(f"Entries before: {count_before}")
        print(f"Entries after: {count_after}")
        print(f"Deleted: {count_before - count_after} entries")
        print(f"{'='*60}\n")
        print("✅ Cache cleared successfully!")
        print("\nNext analysis will be fresh (not from cache).")
        
    except Exception as e:
        print(f"❌ Error clearing cache: {e}")

def clear_entries_without_property_price():
    """Clear only cache entries that don't have property_price"""
    
    cache_db_path = 'analysis_cache.db'
    
    if not os.path.exists(cache_db_path):
        print("❌ Cache database not found.")
        return
    
    try:
        import json
        
        conn = sqlite3.connect(cache_db_path)
        cursor = conn.cursor()
        
        # Get all entries
        cursor.execute('SELECT cache_key, result FROM analysis_cache')
        entries = cursor.fetchall()
        
        invalid_keys = []
        
        # Find entries without property_price
        for cache_key, result_json in entries:
            try:
                result = json.loads(result_json)
                if 'property_price' not in result or result.get('property_price', 0) == 0:
                    invalid_keys.append(cache_key)
            except:
                invalid_keys.append(cache_key)  # Corrupted entry
        
        print(f"\nFound {len(invalid_keys)} invalid cache entries out of {len(entries)} total")
        
        if invalid_keys:
            # Delete invalid entries
            placeholders = ','.join('?' * len(invalid_keys))
            cursor.execute(f'DELETE FROM analysis_cache WHERE cache_key IN ({placeholders})', invalid_keys)
            conn.commit()
            print(f"✅ Deleted {len(invalid_keys)} invalid cache entries")
        else:
            print("✅ All cache entries are valid")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")

def show_cache_stats():
    """Show cache statistics"""
    
    cache_db_path = 'analysis_cache.db'
    
    if not os.path.exists(cache_db_path):
        print("❌ Cache database not found.")
        return
    
    try:
        import json
        
        conn = sqlite3.connect(cache_db_path)
        cursor = conn.cursor()
        
        # Total entries
        cursor.execute('SELECT COUNT(*) FROM analysis_cache')
        total = cursor.fetchone()[0]
        
        # Entries with property_price
        cursor.execute('SELECT result FROM analysis_cache')
        results = cursor.fetchall()
        
        valid_count = 0
        invalid_count = 0
        
        for (result_json,) in results:
            try:
                result = json.loads(result_json)
                if 'property_price' in result and result.get('property_price', 0) > 0:
                    valid_count += 1
                else:
                    invalid_count += 1
            except:
                invalid_count += 1
        
        conn.close()
        
        print(f"\n{'='*60}")
        print(f"CACHE STATISTICS")
        print(f"{'='*60}")
        print(f"Total entries: {total}")
        print(f"Valid entries (with property_price): {valid_count}")
        print(f"Invalid entries (missing property_price): {invalid_count}")
        print(f"{'='*60}\n")
        
        if invalid_count > 0:
            print(f"⚠️ You have {invalid_count} old cache entries")
            print(f"   These will cause $N/A to display")
            print(f"   Run: python clear_cache.py --clear-invalid")
        else:
            print("✅ All cache entries are valid!")
        
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == '--clear-all':
            clear_analysis_cache()
        elif sys.argv[1] == '--clear-invalid':
            clear_entries_without_property_price()
        elif sys.argv[1] == '--stats':
            show_cache_stats()
        else:
            print("Usage:")
            print("  python clear_cache.py --stats          # Show cache statistics")
            print("  python clear_cache.py --clear-invalid  # Clear only invalid entries")
            print("  python clear_cache.py --clear-all      # Clear ALL cache (nuclear option)")
    else:
        print("\nOfferWise Cache Management")
        print("="*60)
        print("\nOptions:")
        print("  1. Show cache statistics")
        print("  2. Clear invalid cache entries (recommended)")
        print("  3. Clear ALL cache (nuclear option)")
        print("  4. Exit")
        print()
        
        choice = input("Enter choice (1-4): ").strip()
        
        if choice == '1':
            show_cache_stats()
        elif choice == '2':
            clear_entries_without_property_price()
        elif choice == '3':
            confirm = input("This will delete ALL cached analyses. Continue? (yes/no): ")
            if confirm.lower() == 'yes':
                clear_analysis_cache()
            else:
                print("Cancelled.")
        elif choice == '4':
            print("Goodbye!")
        else:
            print("Invalid choice.")
