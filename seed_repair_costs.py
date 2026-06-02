"""
Seed repair cost data into the database.
Run once on deploy to populate RepairCostZone and RepairCostBaseline tables.
Subsequent updates can be done through the admin API.
"""
import logging

logger = logging.getLogger(__name__)


def seed_repair_cost_data(app=None):
    """
    Seed RepairCostZone and RepairCostBaseline tables from hardcoded data.
    Safe to run multiple times — uses upsert logic.
    """
    from models import db, RepairCostZone, RepairCostBaseline
    from zip_cost_data import ZIP_COST_DATA
    from repair_cost_estimator import BASELINE_COSTS

    # Determine state from ZIP prefix
    def zip_to_state(prefix):
        p = int(prefix)
        if 10 <= p <= 14: return 'NY', 'Northeast'
        if 100 <= p <= 149: return 'NY', 'Northeast'
        if 70 <= p <= 89: return 'NJ', 'Northeast'
        if 60 <= p <= 69: return 'CT', 'Northeast'
        if 10 <= p <= 27: return 'MA', 'Northeast'
        if 28 <= p <= 29: return 'RI', 'Northeast'
        if 30 <= p <= 38: return 'NH', 'Northeast'
        if 39 <= p <= 49: return 'ME', 'Northeast'
        if 50 <= p <= 59: return 'VT', 'Northeast'
        if 150 <= p <= 196: return 'PA', 'Northeast'
        if 197 <= p <= 199: return 'DE', 'Mid-Atlantic'
        if 200 <= p <= 205: return 'DC', 'Mid-Atlantic'
        if 206 <= p <= 219: return 'MD', 'Mid-Atlantic'
        if 220 <= p <= 246: return 'VA', 'Southeast'
        if 247 <= p <= 268: return 'WV', 'Southeast'
        if 270 <= p <= 289: return 'NC', 'Southeast'
        if 290 <= p <= 299: return 'SC', 'Southeast'
        if 300 <= p <= 319: return 'GA', 'Southeast'
        if 320 <= p <= 349: return 'FL', 'Southeast'
        if 350 <= p <= 369: return 'AL', 'Southeast'
        if 370 <= p <= 385: return 'TN', 'Southeast'
        if 386 <= p <= 397: return 'MS', 'Southeast'
        if 400 <= p <= 427: return 'KY', 'Southeast'
        if 430 <= p <= 458: return 'OH', 'Midwest'
        if 460 <= p <= 479: return 'IN', 'Midwest'
        if 480 <= p <= 499: return 'MI', 'Midwest'
        if 500 <= p <= 528: return 'IA', 'Midwest'
        if 530 <= p <= 549: return 'WI', 'Midwest'
        if 550 <= p <= 567: return 'MN', 'Midwest'
        if 570 <= p <= 577: return 'SD', 'Plains'
        if 580 <= p <= 588: return 'ND', 'Plains'
        if 590 <= p <= 599: return 'MT', 'Mountain'
        if 600 <= p <= 629: return 'IL', 'Midwest'
        if 630 <= p <= 658: return 'MO', 'Central'
        if 660 <= p <= 679: return 'KS', 'Central'
        if 680 <= p <= 693: return 'NE', 'Central'
        if 700 <= p <= 714: return 'LA', 'South'
        if 716 <= p <= 729: return 'AR', 'South'
        if 730 <= p <= 749: return 'OK', 'South'
        if 750 <= p <= 799: return 'TX', 'South'
        if 800 <= p <= 816: return 'CO', 'Mountain'
        if 820 <= p <= 831: return 'WY', 'Mountain'
        if 832 <= p <= 838: return 'ID', 'Mountain'
        if 840 <= p <= 847: return 'UT', 'Mountain'
        if 850 <= p <= 865: return 'AZ', 'Mountain'
        if 870 <= p <= 884: return 'NM', 'Mountain'
        if 889 <= p <= 898: return 'NV', 'Pacific'
        if 900 <= p <= 961: return 'CA', 'Pacific'
        if 967 <= p <= 968: return 'HI', 'Pacific'
        if 970 <= p <= 979: return 'OR', 'Pacific'
        if 980 <= p <= 994: return 'WA', 'Pacific'
        if 995 <= p <= 999: return 'AK', 'Pacific'
        return None, 'Other'

    ctx = app.app_context() if app else None
    if ctx:
        ctx.__enter__()

    try:
        # Seed zones
        zone_count = 0
        for prefix, (mult, metro) in ZIP_COST_DATA.items():
            state, region = zip_to_state(prefix)
            existing = RepairCostZone.query.filter_by(zip_prefix=prefix).first()
            if existing:
                existing.metro_name = metro
                existing.cost_multiplier = mult
                existing.state = state
                existing.region = region
            else:
                db.session.add(RepairCostZone(
                    zip_prefix=prefix, metro_name=metro,
                    cost_multiplier=mult, state=state, region=region,
                ))
            zone_count += 1

        # Seed baselines
        baseline_count = 0
        for category, severities in BASELINE_COSTS.items():
            for severity, (low, high) in severities.items():
                existing = RepairCostBaseline.query.filter_by(
                    category=category, severity=severity
                ).first()
                if existing:
                    existing.cost_low = low
                    existing.cost_high = high
                else:
                    db.session.add(RepairCostBaseline(
                        category=category, severity=severity,
                        cost_low=low, cost_high=high,
                    ))
                baseline_count += 1

        db.session.commit()
        logger.info(f"💰 Seeded repair cost DB: {zone_count} zones, {baseline_count} baselines")
        return zone_count, baseline_count

    except Exception as e:
        db.session.rollback()
        logger.error(f"💰 Failed to seed repair cost DB: {e}")
        raise
    finally:
        if ctx:
            ctx.__exit__(None, None, None)
