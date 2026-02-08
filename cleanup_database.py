"""
DATABASE CLEANUP SCRIPT
Fixes duplicate properties, orphaned analyses, and data integrity issues
"""

import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import User, Property, Analysis, Document

def cleanup_database(dry_run=True):
    """
    Clean up database issues
    
    Args:
        dry_run: If True, only report what would be done. If False, actually do it.
    """
    
    mode = "DRY RUN" if dry_run else "LIVE MODE"
    print(f"\n{'='*80}")
    print(f"🧹 DATABASE CLEANUP - {mode}")
    print(f"{'='*80}")
    
    if not dry_run:
        print(f"\n⚠️  WARNING: This will PERMANENTLY modify the database!")
        response = input("Type 'YES' to continue: ")
        if response != 'YES':
            print("Cancelled.")
            return
    
    with app.app_context():
        try:
            stats = {
                'duplicates_removed': 0,
                'orphaned_analyses_removed': 0,
                'orphaned_documents_removed': 0
            }
            
            for user in User.query.all():
                print(f"\n{'-'*80}")
                print(f"USER: {user.email}")
                print(f"{'-'*80}")
                
                # FIX #1: Remove duplicate properties (keep most recent)
                properties = Property.query.filter_by(user_id=user.id).all()
                address_groups = {}
                
                for prop in properties:
                    addr = prop.address or "Unknown"
                    if addr not in address_groups:
                        address_groups[addr] = []
                    address_groups[addr].append(prop)
                
                for addr, props in address_groups.items():
                    if len(props) > 1:
                        # Sort by analyzed_at (most recent first)
                        props.sort(key=lambda p: p.analyzed_at or datetime.min, reverse=True)
                        
                        keep = props[0]
                        remove = props[1:]
                        
                        print(f"\n🗑️  DUPLICATE: '{addr}' has {len(props)} entries")
                        print(f"   ✅ KEEPING: Property ID {keep.id} (analyzed: {keep.analyzed_at})")
                        
                        for prop in remove:
                            print(f"   ❌ REMOVING: Property ID {prop.id} (analyzed: {prop.analyzed_at})")
                            
                            if not dry_run:
                                # Delete analyses for this property
                                analyses = Analysis.query.filter_by(property_id=prop.id).all()
                                for analysis in analyses:
                                    db.session.delete(analysis)
                                
                                # Delete documents for this property
                                docs = Document.query.filter_by(property_id=prop.id).all()
                                for doc in docs:
                                    db.session.delete(doc)
                                
                                # Delete the property
                                db.session.delete(prop)
                                stats['duplicates_removed'] += 1
                
                # FIX #2: Remove orphaned analyses
                all_analyses = Analysis.query.filter_by(user_id=user.id).all()
                orphaned_analyses = []
                
                for analysis in all_analyses:
                    if not Property.query.get(analysis.property_id):
                        orphaned_analyses.append(analysis)
                
                if orphaned_analyses:
                    print(f"\n🗑️  ORPHANED ANALYSES: {len(orphaned_analyses)} found")
                    for oa in orphaned_analyses:
                        print(f"   ❌ REMOVING: Analysis ID {oa.id} (property {oa.property_id} missing)")
                        
                        if not dry_run:
                            db.session.delete(oa)
                            stats['orphaned_analyses_removed'] += 1
                
                # FIX #3: Remove orphaned documents
                all_docs = Document.query.filter_by(user_id=user.id).all()
                orphaned_docs = []
                
                for doc in all_docs:
                    if not Property.query.get(doc.property_id):
                        orphaned_docs.append(doc)
                
                if orphaned_docs:
                    print(f"\n🗑️  ORPHANED DOCUMENTS: {len(orphaned_docs)} found")
                    for od in orphaned_docs:
                        print(f"   ❌ REMOVING: Document ID {od.id} (property {od.property_id} missing)")
                        
                        if not dry_run:
                            db.session.delete(od)
                            stats['orphaned_documents_removed'] += 1
            
            if not dry_run:
                db.session.commit()
                print(f"\n{'='*80}")
                print(f"✅ CLEANUP COMPLETE")
                print(f"{'='*80}")
                print(f"   Duplicate properties removed: {stats['duplicates_removed']}")
                print(f"   Orphaned analyses removed: {stats['orphaned_analyses_removed']}")
                print(f"   Orphaned documents removed: {stats['orphaned_documents_removed']}")
                print(f"\n💡 Users should clear their browser localStorage to see changes:")
                print(f"   localStorage.clear(); location.reload();")
            else:
                print(f"\n{'='*80}")
                print(f"🔍 DRY RUN COMPLETE - No changes made")
                print(f"{'='*80}")
                print(f"   Would remove {stats['duplicates_removed']} duplicate properties")
                print(f"   Would remove {stats['orphaned_analyses_removed']} orphaned analyses")
                print(f"   Would remove {stats['orphaned_documents_removed']} orphaned documents")
                print(f"\n💡 Run with dry_run=False to actually clean up:")
                print(f"   python cleanup_database.py --live")
            
        except Exception as e:
            if not dry_run:
                db.session.rollback()
            print(f"\n❌ ERROR during cleanup: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    import sys
    dry_run = '--live' not in sys.argv
    cleanup_database(dry_run=dry_run)
