"""
COMPREHENSIVE DATABASE DIAGNOSTIC
Checks for duplicate properties, orphaned analyses, and data integrity issues
"""

import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import User, Property, Analysis, Document
from sqlalchemy import func

def run_comprehensive_diagnostic():
    """Run full database diagnostic"""
    
    print("\n" + "="*80)
    print("🔍 COMPREHENSIVE DATABASE DIAGNOSTIC")
    print("="*80)
    
    with app.app_context():
        try:
            # Get user count
            user_count = User.query.count()
            print(f"\n👥 USERS: {user_count} total")
            
            if user_count == 0:
                print("   ⚠️  No users in database")
                return
            
            # Check each user
            for user in User.query.all():
                print(f"\n{'='*80}")
                print(f"USER: {user.email} (ID: {user.id})")
                print(f"{'='*80}")
                
                # Get properties for this user
                properties = Property.query.filter_by(user_id=user.id).all()
                print(f"\n📦 PROPERTIES: {len(properties)}")
                
                if len(properties) == 0:
                    print("   ℹ️  No properties")
                    continue
                
                # Check for duplicates by address
                address_counts = {}
                for prop in properties:
                    addr = prop.address or "Unknown"
                    address_counts[addr] = address_counts.get(addr, 0) + 1
                
                # Report duplicates
                duplicates = {addr: count for addr, count in address_counts.items() if count > 1}
                if duplicates:
                    print(f"\n🚨 DUPLICATE ADDRESSES FOUND:")
                    for addr, count in duplicates.items():
                        print(f"   ❌ '{addr}' appears {count} times")
                        
                        # Show details of duplicates
                        dup_props = Property.query.filter_by(user_id=user.id, address=addr).all()
                        for i, dp in enumerate(dup_props, 1):
                            analyses_count = Analysis.query.filter_by(property_id=dp.id).count()
                            docs_count = Document.query.filter_by(property_id=dp.id).count()
                            print(f"      Property #{i}:")
                            print(f"         ID: {dp.id}")
                            print(f"         Price: ${dp.price:,.0f}" if dp.price else "         Price: None")
                            print(f"         Created: {dp.created_at}")
                            print(f"         Analyzed: {dp.analyzed_at}" if dp.analyzed_at else "         Analyzed: Never")
                            print(f"         Analyses: {analyses_count}")
                            print(f"         Documents: {docs_count}")
                
                # Check all properties
                for prop in properties:
                    analyses = Analysis.query.filter_by(property_id=prop.id).all()
                    docs = Document.query.filter_by(property_id=prop.id).all()
                    
                    print(f"\n   Property: {prop.address or 'Unknown'}")
                    print(f"      ID: {prop.id}")
                    print(f"      Price: ${prop.price:,.0f}" if prop.price else "      Price: None")
                    print(f"      Analyses: {len(analyses)}")
                    print(f"      Documents: {len(docs)}")
                    
                    # Check for multiple analyses
                    if len(analyses) > 1:
                        print(f"      ⚠️  Multiple analyses found:")
                        for i, analysis in enumerate(analyses, 1):
                            result_data = {}
                            if analysis.result_json:
                                import json
                                try:
                                    result_data = json.loads(analysis.result_json)
                                except:
                                    pass
                            
                            risk_dna = result_data.get('risk_dna', {})
                            composite_score = risk_dna.get('composite_score', 'N/A')
                            
                            print(f"         Analysis #{i}:")
                            print(f"            ID: {analysis.id}")
                            print(f"            Created: {analysis.created_at}")
                            print(f"            Risk Score: {composite_score}")
                
                # Check for orphaned analyses
                print(f"\n🔍 CHECKING FOR ORPHANED DATA:")
                
                all_analyses = Analysis.query.filter_by(user_id=user.id).all()
                orphaned_analyses = []
                for analysis in all_analyses:
                    if not Property.query.get(analysis.property_id):
                        orphaned_analyses.append(analysis)
                
                if orphaned_analyses:
                    print(f"   ❌ Found {len(orphaned_analyses)} orphaned analyses (property deleted but analysis remains)")
                    for oa in orphaned_analyses:
                        print(f"      Analysis ID {oa.id} → Property ID {oa.property_id} (MISSING)")
                else:
                    print(f"   ✅ No orphaned analyses")
                
                # Check for orphaned documents
                all_docs = Document.query.filter_by(user_id=user.id).all()
                orphaned_docs = []
                for doc in all_docs:
                    if not Property.query.get(doc.property_id):
                        orphaned_docs.append(doc)
                
                if orphaned_docs:
                    print(f"   ❌ Found {len(orphaned_docs)} orphaned documents (property deleted but document remains)")
                    for od in orphaned_docs:
                        print(f"      Document ID {od.id} → Property ID {od.property_id} (MISSING)")
                else:
                    print(f"   ✅ No orphaned documents")
            
            print(f"\n{'='*80}")
            print(f"✅ DIAGNOSTIC COMPLETE")
            print(f"{'='*80}\n")
            
        except Exception as e:
            print(f"\n❌ ERROR during diagnostic: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    run_comprehensive_diagnostic()
