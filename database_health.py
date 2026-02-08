"""
Database Health Check - Automatically fixes common issues on app startup
"""

import logging
import json
from sqlalchemy import text

logger = logging.getLogger(__name__)

class DatabaseHealth:
    """Database health checks and automatic fixes"""
    
    @staticmethod
    def fix_zero_price_properties(db):
        """
        Automatically fix properties with price=$0
        
        This runs on app startup to ensure database integrity.
        
        CRITICAL: We NEVER delete user analyses - only fix them!
        If price can't be recovered, we leave it as-is and user sees error when loading.
        """
        try:
            from models import Property, Analysis
            
            # Find properties with price = 0 or NULL
            zero_price_properties = Property.query.filter(
                (Property.price == 0) | (Property.price == None)
            ).all()
            
            if not zero_price_properties:
                logger.info("✅ Database health check: No properties with $0 price")
                return {
                    'status': 'healthy',
                    'fixed': 0,
                    'unfixable': 0
                }
            
            logger.warning(f"⚠️ Found {len(zero_price_properties)} properties with $0 price - attempting auto-fix...")
            
            fixed_count = 0
            unfixable_count = 0
            
            for property in zero_price_properties:
                try:
                    # Try to get price from analysis
                    analysis = Analysis.query.filter_by(
                        property_id=property.id
                    ).order_by(Analysis.created_at.desc()).first()
                    
                    if analysis:
                        result_json = json.loads(analysis.result_json)
                        
                        if 'property_price' in result_json and result_json['property_price'] > 0:
                            # Fix it - recover price from analysis
                            old_price = property.price or 0
                            property.price = result_json['property_price']
                            db.session.commit()
                            
                            logger.info(
                                f"✅ Fixed property {property.id} ({property.address}): "
                                f"${old_price} → ${property.price:,}"
                            )
                            fixed_count += 1
                        else:
                            # Can't fix - leave it, user will see error when loading
                            logger.warning(
                                f"⚠️ Cannot fix property {property.id} ({property.address}): "
                                f"No valid price in analysis. Keeping property - user will see error when loading."
                            )
                            unfixable_count += 1
                    else:
                        # No analysis - can't fix, but KEEP the property
                        logger.warning(
                            f"⚠️ Cannot fix property {property.id} ({property.address}): "
                            f"No analysis found. Keeping property - user will see error when loading."
                        )
                        unfixable_count += 1
                        
                except Exception as e:
                    logger.error(f"❌ Error processing property {property.id}: {e}")
                    db.session.rollback()
                    unfixable_count += 1
                    continue
            
            logger.info(
                f"✅ Database health check complete: "
                f"Fixed {fixed_count} properties, {unfixable_count} need manual attention"
            )
            
            return {
                'status': 'fixed' if fixed_count > 0 else 'needs_attention',
                'fixed': fixed_count,
                'unfixable': unfixable_count
            }
            
        except Exception as e:
            logger.error(f"❌ Database health check failed: {e}")
            db.session.rollback()
            return {
                'status': 'error',
                'error': str(e)
            }
    
    @staticmethod
    def check_and_fix_all(db):
        """
        Run all database health checks
        
        This is called on app startup to ensure database integrity.
        Non-blocking - won't prevent app from starting.
        
        IMPORTANT: We NEVER delete user data automatically!
        """
        logger.info("🔍 Running database health checks...")
        
        results = {
            'zero_price_properties': DatabaseHealth.fix_zero_price_properties(db)
        }
        
        # Add more health checks here in the future:
        # - Check for orphaned documents
        # - Check for corrupted analysis JSON
        # - Clean old cache entries (safe - not user data)
        # etc.
        
        return results
