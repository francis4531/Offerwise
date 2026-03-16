"""
OfferWise Bug Routes Blueprint
Extracted from app.py v5.74.44 for architecture cleanup.
"""

import os
import logging
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from models import db, Bug

logger = logging.getLogger(__name__)

bugs_bp = Blueprint('bugs', __name__)

from blueprint_helpers import DeferredDecorator, make_deferred_limiter

def _get_analyze_bug_with_ai():
    from app import analyze_bug_with_ai
    return analyze_bug_with_ai

def analyze_bug_with_ai(bug):
    return _get_analyze_bug_with_ai()(bug)

_admin_required_ref = [None]
_api_admin_required_ref = [None]
_api_login_required_ref = [None]
_dev_only_gate_ref = [None]
_limiter_ref = [None]

_admin_required = DeferredDecorator(lambda: _admin_required_ref[0])
_api_admin_required = DeferredDecorator(lambda: _api_admin_required_ref[0])
_api_login_required = DeferredDecorator(lambda: _api_login_required_ref[0])
_dev_only_gate = DeferredDecorator(lambda: _dev_only_gate_ref[0])
_limiter = make_deferred_limiter(lambda: _limiter_ref[0])


def init_bugs_blueprint(app, admin_required_fn, api_admin_required_fn,
                       api_login_required_fn, dev_only_gate_fn, limiter):
    _admin_required_ref[0] = admin_required_fn
    _api_admin_required_ref[0] = api_admin_required_fn
    _api_login_required_ref[0] = api_login_required_fn
    _dev_only_gate_ref[0] = dev_only_gate_fn
    _limiter_ref[0] = limiter
    app.register_blueprint(bugs_bp)
    logger.info("✅ Bug Routes blueprint registered")



@bugs_bp.route('/api/bugs', methods=['GET'])
@_api_admin_required
def get_bugs():
    """Get all bugs with optional filters"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        # Check if Bug table exists
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        if 'bugs' not in [t.lower() for t in inspector.get_table_names()]:
            return jsonify({
                "bugs": [],
                "stats": {"total": 0, "open": 0, "in_progress": 0, "fixed": 0, "critical": 0, "fix_queue": 0, "needs_analysis": 0},
                "warning": "Bug table not found. Run db.create_all() to create it."
            })
        
        # Filters
        status = request.args.get('status')
        severity = request.args.get('severity')
        category = request.args.get('category')
        
        query = Bug.query
        
        if status and status != 'all':
            if status == 'fix_queue':
                # Bugs with approved AI fixes, ready for implementation
                try:
                    query = query.filter(Bug.ai_fix_approved == True, Bug.status != 'fixed')
                except Exception:
                    query = query.filter(Bug.status == 'in_progress')  # Fallback
            elif status == 'needs_analysis':
                # Open bugs without AI analysis
                try:
                    query = query.filter(Bug.status.in_(['open', 'in_progress']), Bug.ai_analysis.is_(None))
                except Exception:
                    query = query.filter(Bug.status == 'open')  # Fallback
            else:
                query = query.filter_by(status=status)
        if severity and severity != 'all':
            query = query.filter_by(severity=severity)
        if category and category != 'all':
            query = query.filter_by(category=category)
        
        bugs = query.order_by(Bug.created_at.desc()).all()
        
        # Build stats with fallbacks for missing columns
        stats = {
            "total": Bug.query.count(),
            "open": Bug.query.filter_by(status='open').count(),
            "in_progress": Bug.query.filter_by(status='in_progress').count(),
            "fixed": Bug.query.filter_by(status='fixed').count(),
            "critical": Bug.query.filter(Bug.status != 'fixed', Bug.severity == 'critical').count(),
        }
        
        # Try to add AI-related stats (may fail if columns don't exist)
        try:
            stats["fix_queue"] = Bug.query.filter(Bug.ai_fix_approved == True, Bug.status != 'fixed').count()
        except Exception:
            stats["fix_queue"] = 0
        
        try:
            stats["needs_analysis"] = Bug.query.filter(Bug.status.in_(['open', 'in_progress']), Bug.ai_analysis.is_(None)).count()
        except Exception:
            stats["needs_analysis"] = stats["open"]  # Assume all open bugs need analysis
        
        return jsonify({
            "bugs": [b.to_dict() for b in bugs],
            "stats": stats
        })
        
    except Exception:
        return jsonify({
            "error": "An internal error occurred. Please try again.",
            "trace": "See server logs",
            "bugs": [],
            "stats": {"total": 0, "open": 0, "in_progress": 0, "fixed": 0, "critical": 0, "fix_queue": 0, "needs_analysis": 0}
        }), 500


@bugs_bp.route('/api/bugs', methods=['POST'])
@_api_admin_required
def create_bug():
    """Create a new bug report"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        table_names = [t.lower() for t in inspector.get_table_names()]
        
        # Create Bug table if it doesn't exist
        if 'bugs' not in table_names:
            logging.info("🔧 Bug table not found, running db.create_all()...")
            db.create_all()
            logging.info("✅ Created missing tables including Bug")
        else:
            # Bug table exists - ensure all required columns exist
            bug_columns = [col['name'] for col in inspector.get_columns('bugs')]
            required_columns = [
                ("stack_trace", "TEXT"),
                ("ai_analysis", "TEXT"),
                ("ai_suggested_fix", "TEXT"),
                ("ai_confidence", "VARCHAR(20)"),
                ("ai_analyzed_at", "TIMESTAMP"),
                ("ai_fix_approved", "BOOLEAN DEFAULT FALSE"),
            ]
            
            for col_name, col_type in required_columns:
                if col_name not in bug_columns:
                    try:
                        db.session.execute(text(f"ALTER TABLE bugs ADD COLUMN {col_name} {col_type};"))
                        db.session.commit()
                        logging.info(f"✅ Added missing column to bug: {col_name}")
                    except Exception as col_err:
                        db.session.rollback()
                        if 'already exists' not in str(col_err).lower():
                            logging.warning(f"⚠️ Could not add {col_name}: {col_err}")
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Get current version
        try:
            with open('VERSION', 'r') as f:
                current_version = f.read().strip()
        except Exception:
            current_version = 'unknown'
        
        # Create bug
        bug = Bug(
            title=data.get('title', 'Untitled Bug'),
            description=data.get('description'),
            steps_to_reproduce=data.get('steps_to_reproduce'),
            expected_behavior=data.get('expected_behavior'),
            actual_behavior=data.get('actual_behavior'),
            severity=data.get('severity', 'medium'),
            category=data.get('category'),
            status='open',
            version_reported=current_version,
            reported_by=data.get('reported_by', 'manual'),
            error_message=data.get('error_message'),
            stack_trace=data.get('stack_trace')
        )
        
        db.session.add(bug)
        db.session.commit()
        
        logging.info(f"✅ Created bug #{bug.id}: {bug.title}")
        
        return jsonify({"success": True, "bug": bug.to_dict()})
    
    except Exception as e:
        import traceback
        db.session.rollback()
        logging.error(f"❌ Bug creation failed: {e}")
        logging.error(traceback.format_exc())
        return jsonify({"error": "An internal error occurred. Please try again.", "trace": "See server logs"}), 500


@bugs_bp.route('/api/bugs/<int:bug_id>', methods=['PUT'])
@_api_admin_required
def update_bug(bug_id):
    """Update a bug"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        bug = Bug.query.get(bug_id)
        if not bug:
            return jsonify({"error": f"Bug #{bug_id} not found"}), 404
        
        data = request.get_json()
        
        # Update fields
        if 'title' in data:
            bug.title = data['title']
        if 'description' in data:
            bug.description = data['description']
        if 'severity' in data:
            bug.severity = data['severity']
        if 'category' in data:
            bug.category = data['category']
        if 'status' in data:
            old_status = bug.status
            bug.status = data['status']
            # Track when fixed
            if data['status'] == 'fixed' and old_status != 'fixed':
                bug.fixed_at = datetime.now()
                try:
                    with open('VERSION', 'r') as f:
                        bug.version_fixed = f.read().strip()
                except Exception:
                    pass
        if 'fix_notes' in data:
            bug.fix_notes = data['fix_notes']
        
        db.session.commit()
        
        return jsonify({"success": True, "bug": bug.to_dict()})
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@bugs_bp.route('/api/bugs/<int:bug_id>', methods=['DELETE'])
@_api_admin_required
def delete_bug(bug_id):
    """Delete a bug"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        bug = Bug.query.get(bug_id)
        if not bug:
            return jsonify({"error": f"Bug #{bug_id} not found"}), 404
        db.session.delete(bug)
        db.session.commit()
        return jsonify({"success": True})
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@bugs_bp.route('/api/bugs/bulk-close', methods=['POST'])
@_api_admin_required
def bulk_close_bugs():
    """Bulk close bugs by version or IDs (v5.54.57)"""
    try:
        data = request.get_json() or {}
        version = data.get('version')
        bug_ids = data.get('bug_ids', [])
        fix_notes = data.get('fix_notes', 'Bulk closed - superseded by newer version')
        
        closed_count = 0
        
        if version:
            # Close all open bugs from specific version
            bugs = Bug.query.filter(
                Bug.version_reported == version,
                Bug.status.in_(['open', 'in_progress'])
            ).all()
            
            for bug in bugs:
                bug.status = 'fixed'
                bug.fix_notes = fix_notes
                bug.fixed_at = datetime.now()
                closed_count += 1
        
        if bug_ids:
            # Close specific bug IDs
            for bug_id in bug_ids:
                bug = Bug.query.get(bug_id)
                if bug and bug.status in ['open', 'in_progress']:
                    bug.status = 'fixed'
                    bug.fix_notes = fix_notes
                    bug.fixed_at = datetime.now()
                    closed_count += 1
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "closed_count": closed_count,
            "version": version,
            "bug_ids": bug_ids
        })
        
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@bugs_bp.route('/api/bugs/analyze', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def analyze_bugs_api():
    """Analyze open bugs with AI and suggest fixes"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        data = request.get_json() or {}
        bug_id = data.get('bug_id')  # Optional: analyze specific bug
        
        if bug_id:
            bug = Bug.query.get(bug_id)
            if not bug:
                return jsonify({"error": f"Bug #{bug_id} not found"}), 404
            bugs = [bug]
        else:
            # Get all open bugs that haven't been analyzed recently (within 24 hours)
            try:
                cutoff = datetime.now() - timedelta(hours=24)
                bugs = Bug.query.filter(
                    Bug.status.in_(['open', 'in_progress']),
                    db.or_(
                        Bug.ai_analyzed_at.is_(None),
                        Bug.ai_analyzed_at < cutoff
                    )
                ).order_by(
                    db.case(
                        (Bug.severity == 'critical', 1),
                        (Bug.severity == 'high', 2),
                        (Bug.severity == 'medium', 3),
                        else_=4
                    )
                ).limit(10).all()  # Limit to 10 per run to control API costs
            except Exception:
                # Fallback if AI columns don't exist - just get open bugs
                bugs = Bug.query.filter(
                    Bug.status.in_(['open', 'in_progress'])
                ).limit(10).all()
        
        results = []
        
        for bug in bugs:
            result = analyze_bug_with_ai(bug)
            
            if result.get('success'):
                try:
                    bug.ai_analysis = result['analysis']
                    bug.ai_suggested_fix = result['fix']
                    bug.ai_confidence = result['confidence']
                    bug.ai_analyzed_at = datetime.now()
                    db.session.commit()
                except Exception:
                    # AI columns might not exist yet
                    db.session.rollback()
                    results.append({
                        "bug_id": bug.id,
                        "title": bug.title,
                        "status": "error",
                        "error": "Cannot save AI analysis - check server logs for details."
                    })
                    continue
                
                results.append({
                    "bug_id": bug.id,
                    "title": bug.title,
                    "status": "analyzed",
                    "confidence": result['confidence']
                })
            else:
                results.append({
                    "bug_id": bug.id,
                    "title": bug.title,
                    "status": "error",
                    "error": result.get('error')
                })
        
        return jsonify({
            "analyzed": len([r for r in results if r['status'] == 'analyzed']),
            "errors": len([r for r in results if r['status'] == 'error']),
            "results": results
        })
    
    except Exception:
        return jsonify({
            "error": "An internal error occurred. Please try again.",
            "trace": "See server logs",
            "analyzed": 0,
            "errors": 1,
            "results": []
        }), 500


@bugs_bp.route('/api/bugs/analyze/<int:bug_id>', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def analyze_single_bug(bug_id):
    """Analyze a single bug with AI"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        # Check if Bug table exists
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        if 'bugs' not in [t.lower() for t in inspector.get_table_names()]:
            return jsonify({"error": "Bug table not found. Create a bug first to auto-create the table."}), 500
        
        bug = Bug.query.get(bug_id)
        if not bug:
            return jsonify({"error": f"Bug #{bug_id} not found"}), 404
        
        result = analyze_bug_with_ai(bug)
        
        if result.get('success'):
            try:
                bug.ai_analysis = result['analysis']
                bug.ai_suggested_fix = result['fix']
                bug.ai_confidence = result['confidence']
                bug.ai_analyzed_at = datetime.now()
                db.session.commit()
            except Exception:
                db.session.rollback()
                # Return the analysis even if we can't save it
                return jsonify({
                    "success": True,
                    "bug_id": bug.id,
                    "analysis": result['analysis'],
                    "fix": result['fix'],
                    "confidence": result['confidence'],
                    "warning": "Analysis complete but could not save to database. Please contact support."
                })
            
            return jsonify({
                "success": True,
                "bug_id": bug.id,
                "analysis": result['analysis'],
                "fix": result['fix'],
                "confidence": result['confidence']
            })
        else:
            return jsonify({"error": result.get('error', 'Unknown error')}), 500
    
    except Exception:
        return jsonify({"error": "An internal error occurred. Please try again.", "trace": "See server logs"}), 500


@bugs_bp.route('/api/bugs/approve-fix/<int:bug_id>', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def approve_bug_fix(bug_id):
    """Approve an AI-suggested fix (marks it for implementation)"""
    # Admin check handled by @api_admin_required decorator
    
    try:
        bug = Bug.query.get(bug_id)
        if not bug:
            return jsonify({"error": f"Bug #{bug_id} not found"}), 404
        
        if not bug.ai_suggested_fix:
            return jsonify({"error": "No AI fix to approve"}), 400
        
        bug.ai_fix_approved = True
        bug.status = 'in_progress'
        bug.fix_notes = f"[AI-APPROVED] {bug.ai_suggested_fix[:500]}..."
        db.session.commit()
        
        return jsonify({"success": True, "bug_id": bug.id})
    
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred. Please try again.", "trace": "See server logs"}), 500


@bugs_bp.route('/cron/analyze-bugs')
def cron_analyze_bugs():
    """Cron endpoint for daily bug analysis - call this from Render Cron Jobs"""
    # Use a secret token for cron authentication
    cron_token = request.args.get('token')
    expected_token = os.environ.get('CRON_SECRET')
    
    if not expected_token or cron_token != expected_token:
        return jsonify({"error": "Invalid cron token"}), 401
    
    # Get open bugs that need analysis
    cutoff = datetime.now() - timedelta(hours=24)
    bugs = Bug.query.filter(
        Bug.status.in_(['open', 'in_progress']),
        db.or_(
            Bug.ai_analyzed_at.is_(None),
            Bug.ai_analyzed_at < cutoff
        )
    ).order_by(
        db.case(
            (Bug.severity == 'critical', 1),
            (Bug.severity == 'high', 2),
            else_=3
        )
    ).limit(5).all()  # Limit to 5 per cron run
    
    results = []
    for bug in bugs:
        result = analyze_bug_with_ai(bug)
        if result.get('success'):
            bug.ai_analysis = result['analysis']
            bug.ai_suggested_fix = result['fix']
            bug.ai_confidence = result['confidence']
            bug.ai_analyzed_at = datetime.now()
            db.session.commit()
            results.append({"bug_id": bug.id, "status": "analyzed"})
        else:
            results.append({"bug_id": bug.id, "status": "error", "error": result.get('error')})
    
    return jsonify({
        "timestamp": datetime.now().isoformat(),
        "bugs_analyzed": len([r for r in results if r['status'] == 'analyzed']),
        "results": results
    })
