"""
Database Fix Script - Fix Properties with $0 Price
Run this ONCE after deploying v2.3.6 to fix all old properties
"""

from app import app, db
from models import Property, Analysis
import json

def fix_zero_price_properties():
    """Fix all properties that have price = 0"""
    
    with app.app_context():
        # Find all properties with price = 0 or NULL
        zero_price_properties = Property.query.filter(
            (Property.price == 0) | (Property.price == None)
        ).all()
        
        if not zero_price_properties:
            print("✅ No properties with $0 price found. Database is clean!")
            return
        
        print(f"\n{'='*70}")
        print(f"FOUND {len(zero_price_properties)} PROPERTIES WITH $0 PRICE")
        print(f"{'='*70}\n")
        
        fixed_count = 0
        deleted_count = 0
        
        for property in zero_price_properties:
            print(f"\nProperty ID: {property.id}")
            print(f"Address: {property.address}")
            print(f"Current Price: ${property.price if property.price else 0}")
            print(f"User ID: {property.user_id}")
            
            # Try to get price from analysis
            analysis = Analysis.query.filter_by(property_id=property.id).first()
            
            if analysis:
                try:
                    result_json = json.loads(analysis.result_json)
                    
                    # Check if analysis has property_price
                    if 'property_price' in result_json and result_json['property_price'] > 0:
                        # Use price from analysis
                        property.price = result_json['property_price']
                        db.session.commit()
                        print(f"✅ FIXED: Set price to ${property.price:,} from analysis")
                        fixed_count += 1
                    else:
                        # Analysis also has no price - delete the property
                        print(f"⚠️ Analysis also has no price. DELETING property...")
                        db.session.delete(property)
                        db.session.commit()
                        deleted_count += 1
                        
                except Exception as e:
                    print(f"❌ Error processing property {property.id}: {e}")
            else:
                # No analysis exists - delete the property
                print(f"⚠️ No analysis found. DELETING property...")
                db.session.delete(property)
                db.session.commit()
                deleted_count += 1
        
        print(f"\n{'='*70}")
        print(f"FIX COMPLETE")
        print(f"{'='*70}")
        print(f"✅ Fixed: {fixed_count} properties")
        print(f"🗑️ Deleted: {deleted_count} properties (no valid price found)")
        print(f"{'='*70}\n")

def show_zero_price_properties():
    """Show all properties with price = 0"""
    
    with app.app_context():
        zero_price_properties = Property.query.filter(
            (Property.price == 0) | (Property.price == None)
        ).all()
        
        if not zero_price_properties:
            print("✅ No properties with $0 price found.")
            return
        
        print(f"\n{'='*70}")
        print(f"PROPERTIES WITH $0 PRICE: {len(zero_price_properties)}")
        print(f"{'='*70}\n")
        
        for property in zero_price_properties:
            print(f"ID: {property.id} | Address: {property.address} | Price: ${property.price if property.price else 0} | User: {property.user_id}")
        
        print(f"\n{'='*70}\n")

def delete_all_zero_price_properties():
    """Delete ALL properties with price = 0 (nuclear option)"""
    
    with app.app_context():
        zero_price_properties = Property.query.filter(
            (Property.price == 0) | (Property.price == None)
        ).all()
        
        if not zero_price_properties:
            print("✅ No properties with $0 price found.")
            return
        
        print(f"⚠️ WARNING: This will delete {len(zero_price_properties)} properties!")
        confirm = input("Type 'DELETE' to confirm: ")
        
        if confirm != 'DELETE':
            print("Cancelled.")
            return
        
        for property in zero_price_properties:
            db.session.delete(property)
        
        db.session.commit()
        
        print(f"✅ Deleted {len(zero_price_properties)} properties with $0 price")

if __name__ == '__main__':
    import sys
    
    print("\n" + "="*70)
    print("OFFERWISE DATABASE FIX - Properties with $0 Price")
    print("="*70)
    
    if len(sys.argv) > 1:
        if sys.argv[1] == '--show':
            show_zero_price_properties()
        elif sys.argv[1] == '--fix':
            fix_zero_price_properties()
        elif sys.argv[1] == '--delete-all':
            delete_all_zero_price_properties()
        else:
            print("\nUsage:")
            print("  python fix_database.py --show         # Show properties with $0 price")
            print("  python fix_database.py --fix          # Fix properties (recommended)")
            print("  python fix_database.py --delete-all   # Delete all $0 properties (nuclear)")
    else:
        print("\nOptions:")
        print("  1. Show properties with $0 price")
        print("  2. Fix properties (recommended)")
        print("  3. Delete all $0 properties (nuclear option)")
        print("  4. Exit")
        print()
        
        choice = input("Enter choice (1-4): ").strip()
        
        if choice == '1':
            show_zero_price_properties()
        elif choice == '2':
            fix_zero_price_properties()
        elif choice == '3':
            delete_all_zero_price_properties()
        elif choice == '4':
            print("Goodbye!")
        else:
            print("Invalid choice.")
