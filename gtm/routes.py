"""
GTM (Go-To-Market) Blueprint
=============================
Extracted from app.py to reduce monolith size.
Contains all /api/gtm/* routes and the /admin/gtm page.
"""

import logging
import time
import functools
import werkzeug.exceptions
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, send_from_directory

gtm_bp = Blueprint('gtm', __name__)


def _db_retry(fn):
    """Retry a DB operation once on OperationalError (stale Render Postgres connection)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            err_str = str(type(e).__name__) + str(e)
            is_conn_err = (
                'OperationalError' in err_str or
                'server closed' in err_str or
                'connection' in err_str.lower()
            )
            if is_conn_err and _db is not None:
                logging.warning(f"DB connection drop in GTM route — rolling back and retrying once: {e}")
                try:
                    _db.session.rollback()
                    _db.session.remove()
                except Exception:
                    pass
                time.sleep(0.3)
                return fn(*args, **kwargs)  # one retry
            raise
    return wrapper


# Module-level flag: only run the "already seeded?" DB check once per process,
# not on every GET /api/gtm/subreddits request.
_subreddits_seeded = False


# ── Auth decorators (imported at registration time) ──────────────
# These get set by init_gtm_blueprint() so the blueprint can use
# the same auth decorators as the main app.
_admin_required = None
_api_admin_required = None
_db = None


_is_admin = None


def init_gtm_blueprint(app, db, admin_required_fn, api_admin_required_fn):
    """
    Register the GTM blueprint with the app and inject dependencies.
    Call this from app.py after defining auth decorators and db.
    """
    global _admin_required, _api_admin_required, _db, _is_admin
    _admin_required = admin_required_fn
    _api_admin_required = api_admin_required_fn
    _db = db
    # Import is_admin from app context
    from app import is_admin
    _is_admin = is_admin
    app.register_blueprint(gtm_bp)
    logging.info("✅ GTM blueprint registered")


# ── Page Route ───────────────────────────────────────────────────

@gtm_bp.route('/admin/gtm')
def admin_gtm_page():
    """GTM Dashboard — Content Engine, Conversion Intel, Ad Performance"""
    if _is_admin and not _is_admin():
        from flask import abort
        abort(404)
    from flask import current_app
    return send_from_directory(current_app.static_folder, 'admin-gtm.html')


# ── GTM Stats (legacy) ───────────────────────────────────────────

@gtm_bp.route('/api/gtm/stats', methods=['GET'])
@_db_retry
def api_gtm_stats():
    """Get GTM aggregate stats (Scout removed in v5.62.85)."""
    _check_admin()
    return jsonify({
        "total_scanned": 0,
        "total_drafts": 0,
        "total_posted": 0,
        "note": "Scout stats deprecated — replaced by Nearby Listings + Preference Learning"
    })


# ── Drafts ───────────────────────────────────────────────────────

@gtm_bp.route('/api/gtm/drafts', methods=['GET'])
@_db_retry
def api_gtm_drafts():
    """Get pending Reddit drafts for review."""
    _check_admin()
    try:
        from models import GTMRedditDraft, GTMScannedThread
        status   = request.args.get('status', 'pending')
        platform = request.args.get('platform')  # reddit | biggerpockets | bp (alias)
        # Normalize 'bp' → 'biggerpockets' (frontend uses shorthand)
        if platform == 'bp':
            platform = 'biggerpockets'
        limit    = min(int(request.args.get('limit', 20)), 100)
        q = GTMRedditDraft.query.filter_by(status=status)
        if platform:
            # Filter by the platform of the parent thread
            q = q.join(GTMScannedThread, GTMRedditDraft.thread_id == GTMScannedThread.id)\
                 .filter(GTMScannedThread.platform == platform)
        drafts = q.order_by(GTMRedditDraft.created_at.desc()).limit(limit).all()
        return jsonify([d.to_dict() for d in drafts])
    except Exception as e:
        logging.error(f"GTM drafts error: {e}")
        return jsonify([])  # Return empty list instead of 500


@gtm_bp.route('/api/gtm/drafts/<int:draft_id>/approve', methods=['POST'])
def api_gtm_approve_draft(draft_id):
    """Approve a draft for posting."""
    _check_admin()
    try:
        from models import GTMRedditDraft
        draft = GTMRedditDraft.query.get(draft_id)
        if not draft:
            return jsonify({"error": "Draft not found"}), 404
        try:
            data = request.get_json(silent=True) or {}
        except Exception:
            data = {}
        if 'edited_text' in data:
            draft.edited_text = data['edited_text']
        draft.status = 'approved'
        draft.reviewed_at = datetime.utcnow()
        _db.session.commit()
        return jsonify({"status": "approved", "draft_id": draft.id})
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM approve error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/drafts/<int:draft_id>/skip', methods=['POST'])
def api_gtm_skip_draft(draft_id):
    """Skip a draft."""
    _check_admin()
    try:
        from models import GTMRedditDraft
        draft = GTMRedditDraft.query.get(draft_id)
        if not draft:
            return jsonify({"error": "Draft not found"}), 404
        try:
            data = request.get_json(silent=True) or {}
        except Exception:
            data = {}
        draft.status = 'skipped'
        draft.skip_reason = data.get('reason', '')
        draft.reviewed_at = datetime.utcnow()
        _db.session.commit()
        return jsonify({"status": "skipped", "draft_id": draft.id})
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM skip error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/drafts/<int:draft_id>/posted', methods=['POST'])
def api_gtm_mark_posted(draft_id):
    """Mark a draft as posted."""
    _check_admin()
    try:
        from models import GTMRedditDraft
        draft = GTMRedditDraft.query.get(draft_id)
        if not draft:
            return jsonify({"error": "Draft not found"}), 404
        try:
            data = request.get_json(silent=True) or {}
        except Exception:
            data = {}
        draft.status = 'posted'
        draft.posted_at = datetime.utcnow()
        draft.posted_url = data.get('posted_url', '')
        _db.session.commit()
        return jsonify({"status": "posted", "draft_id": draft.id})
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM posted error: {e}")
        return jsonify({"error": "Internal error"}), 500


# ── Scanning ─────────────────────────────────────────────────────

@gtm_bp.route('/api/gtm/scan', methods=['POST'])
def api_gtm_scan():
    """Scan Reddit for relevant threads and generate reply drafts."""
    _check_admin()
    try:
        from gtm.forum_scanner import run_scan
        stats = run_scan(_db.session, platform='reddit')
        return jsonify({"status": "ok", "stats": stats})
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM scan error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@gtm_bp.route('/api/gtm/bp-blog/generate', methods=['POST'])
def api_gtm_bp_blog():
    """Generate a marketing-focused blog post for BiggerPockets."""
    _check_admin()
    try:
        import os
        import requests as http_requests
        
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 503
        
        # Collect stats for the post
        from gtm.content_engine import collect_aggregate_stats
        from models import Analysis
        models = {"Analysis": Analysis}
        stats = collect_aggregate_stats(_db.session, models)
        
        # Pick a topic
        import random
        topics = [
            ("How AI Is Changing Home Inspections for First-Time Buyers", "inspection_ai"),
            ("The Hidden Costs Sellers Don't Disclose (And How to Find Them)", "hidden_costs"),
            ("Why Your Inspection Report and Seller Disclosure Don't Match", "contradictions"),
            ("How to Calculate the Right Offer Price Using Data, Not Guesswork", "offer_strategy"),
            ("The Real Cost of Foundation Repairs in California (2026 Data)", "foundation_costs"),
            ("5 Red Flags in Seller Disclosures That Most Buyers Miss", "red_flags"),
            ("How to Negotiate $50K Off Your Home Purchase Using Inspection Data", "negotiation"),
            ("Why 87% of Seller Disclosures Have Gaps (And What to Do About It)", "transparency_gap"),
            ("The Complete Guide to Understanding Your Home Inspection Report", "inspection_guide"),
            ("How ZIP Code Affects Your Repair Costs (California Data)", "zip_costs"),
        ]
        topic_title, topic_key = random.choice(topics)
        
        avg_score = stats.get('avg_offer_score', 62)
        avg_repair = stats.get('avg_repair_cost', 18500)
        avg_transparency = stats.get('avg_transparency_score', 64)
        avg_findings = stats.get('avg_findings_per_property', 8)
        
        prompt = f"""Write a 600-800 word blog post for BiggerPockets about: "{topic_title}"

This is for our company member blog on BiggerPockets. We are OfferWise (getofferwise.ai) — an AI tool that analyzes seller disclosures and inspection reports to give homebuyers data-backed offer recommendations, risk scores, and repair cost breakdowns.

REQUIREMENTS:
- Write as the OfferWise team, first person plural ("we", "our data shows")
- Include real data points from our platform: average {avg_findings} findings per property, average repair costs of ${avg_repair:,}, average transparency score of {avg_transparency}%, average OfferScore of {avg_score}/100
- Include 2-3 clear product mentions with HTML links: <a href="https://www.getofferwise.ai">OfferWise</a>
- End with a strong CTA: 'Try OfferWise free — <a href="https://www.getofferwise.ai">upload your seller disclosure and inspection report</a> and get a full analysis in 60 seconds.'
- Use specific examples and numbers, not generic advice
- Professional but conversational tone — this is BiggerPockets, not a corporate whitepaper
- Do NOT use bullet points excessively — write in flowing paragraphs

FORMAT — Output clean HTML suitable for pasting into a WYSIWYG rich text editor:
- Use <h2> for main section headers and <h3> for sub-headers
- Use <p> for paragraphs
- Use <strong> for bold and <em> for italic
- Use <a href="URL">text</a> for links
- Do NOT include <html>, <head>, <body>, or any wrapper tags — just the content HTML
- Do NOT use markdown syntax (no ## or ** or [text](url))

Return ONLY the HTML content, no preamble or code fences."""

        resp = http_requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'content-type': 'application/json',
                'anthropic-version': '2023-06-01',
            },
            json={
                'model': 'claude-sonnet-4-6',
                'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=60,
        )
        
        if resp.status_code != 200:
            return jsonify({"error": f"Claude API error: {resp.status_code}"}), 500
        
        result = resp.json()
        body = result['content'][0]['text']
        word_count = len(body.split())
        
        return jsonify({
            "title": topic_title,
            "topic": topic_key,
            "body": body,
            "word_count": word_count,
        })
    except Exception as e:
        import traceback
        logging.error(f"BP blog generation error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@gtm_bp.route('/api/gtm/scan/bp', methods=['POST'])
def api_gtm_scan_bp():
    """Scan BiggerPockets for relevant threads and generate reply drafts."""
    _check_admin()
    try:
        data = request.get_json(silent=True) or {}
        
        # Nuclear reset: clear all BP threads that don't have approved/posted drafts
        if data.get('reset'):
            from models import GTMScannedThread, GTMRedditDraft
            from sqlalchemy import or_
            
            protected_ids = set(
                row[0] for row in
                _db.session.query(GTMScannedThread.id)
                .join(GTMRedditDraft, GTMScannedThread.id == GTMRedditDraft.thread_id)
                .filter(GTMRedditDraft.status.in_(['approved', 'posted']))
                .all()
            )
            
            q = GTMScannedThread.query.filter(
                or_(
                    GTMScannedThread.platform == 'biggerpockets',
                    GTMScannedThread.reddit_id.like('bp_%'),
                )
            )
            if protected_ids:
                q = q.filter(~GTMScannedThread.id.in_(protected_ids))
            deleted = q.delete(synchronize_session=False)
            _db.session.commit()
            logging.info(f"Reset {deleted} BP threads for fresh rescan")
        
        from gtm.forum_scanner import run_scan
        stats = run_scan(_db.session, platform='biggerpockets')
        return jsonify({"status": "ok", "stats": stats})
    except Exception as e:
        _db.session.rollback()
        import traceback
        tb = traceback.format_exc()
        logging.error(f"GTM BP scan error: {e}\n{tb}")
        return jsonify({"error": str(e), "traceback_line": tb.strip().split('\n')[-2] if tb else ''}), 500


# ── Reddit Auto-Post API ─────────────────────────────────────────

@gtm_bp.route('/api/reddit/next-post', methods=['GET'])
def api_reddit_next_post():
    """Serve the next approved post for external publishers (Devvit or cron).

    Secured by REDDIT_POST_API_KEY query param or X-Api-Key header.
    Returns JSON with title, body, flair, post_id — or 204 if nothing queued.
    """
    import os
    api_key = os.environ.get('REDDIT_POST_API_KEY', '')
    if not api_key:
        return jsonify({"error": "REDDIT_POST_API_KEY not configured"}), 503

    provided = request.args.get('key', '') or request.headers.get('X-Api-Key', '')
    if provided != api_key:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from reddit_poster import get_next_approved_post
        post = get_next_approved_post(_db.session)
        if not post:
            return '', 204  # Nothing to post

        return jsonify({
            "post_id": post.id,
            "title": post.title,
            "body": post.edited_body or post.body,
            "flair": post.flair,
            "pillar": post.pillar,
            "pillar_label": post.pillar_label,
            "scheduled_date": post.scheduled_date.isoformat() if post.scheduled_date else None,
        })
    except Exception as e:
        logging.error(f"Reddit next-post error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/reddit/post-confirm', methods=['POST'])
def api_reddit_post_confirm():
    """Callback after a post is published to Reddit (by Devvit or Data API).

    Expects JSON: {"post_id": 123, "reddit_url": "https://reddit.com/r/..."}
    """
    import os
    api_key = os.environ.get('REDDIT_POST_API_KEY', '')
    if not api_key:
        return jsonify({"error": "REDDIT_POST_API_KEY not configured"}), 503

    provided = request.args.get('key', '') or request.headers.get('X-Api-Key', '')
    if provided != api_key:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json(silent=True) or {}
        post_id = data.get('post_id')
        reddit_url = data.get('reddit_url', '')
        if not post_id:
            return jsonify({"error": "post_id required"}), 400

        from reddit_poster import confirm_posted
        ok = confirm_posted(_db.session, post_id, reddit_url)
        if not ok:
            return jsonify({"error": "Post not found"}), 404
        return jsonify({"status": "ok", "post_id": post_id})
    except Exception as e:
        logging.error(f"Reddit post-confirm error: {e}")
        return jsonify({"error": "Internal error"}), 500


# ── Funnel & Ads ─────────────────────────────────────────────────

@gtm_bp.route('/api/gtm/funnel', methods=['GET'])
@_db_retry
def api_gtm_funnel():
    """Get conversion funnel snapshot."""
    _check_admin()
    try:
        from gtm.conversion_intel import get_funnel_snapshot, get_ga4_status
        from models import GTMFunnelEvent
        days = int(request.args.get('days', 30))
        models = {"FunnelEvent": GTMFunnelEvent}
        snapshot = get_funnel_snapshot(_db.session, models, days=days)
        snapshot["ga4"] = get_ga4_status()
        return jsonify(snapshot)
    except Exception as e:
        logging.error(f"GTM funnel error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/ads', methods=['GET'])
@_db_retry
def api_gtm_ads():
    """Get ad performance summary."""
    _check_admin()
    try:
        from gtm.conversion_intel import get_ad_performance_summary
        from models import GTMAdPerformance
        days = int(request.args.get('days', 30))
        models = {"AdPerformance": GTMAdPerformance}
        summary = get_ad_performance_summary(_db.session, models, days=days)
        return jsonify(summary)
    except Exception as e:
        logging.error(f"GTM ads error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/ads/entry', methods=['POST'])
def api_gtm_ads_entry():
    """Add a manual ad spend entry."""
    _check_admin()
    try:
        from gtm.conversion_intel import add_ad_entry
        from models import GTMAdPerformance
        data = request.get_json() or {}
        models = {"AdPerformance": GTMAdPerformance}
        result = add_ad_entry(_db.session, models, data)
        return jsonify(result)
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM ad entry error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/funnel/event', methods=['POST'])
def api_gtm_funnel_event():
    """Record a funnel event."""
    _check_admin()
    try:
        from gtm.conversion_intel import record_funnel_event
        from models import GTMFunnelEvent
        data = request.get_json() or {}
        models = {"FunnelEvent": GTMFunnelEvent}
        result = record_funnel_event(_db.session, models, data)
        return jsonify(result)
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM funnel event error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/funnel/track', methods=['POST'])
def api_public_funnel_track():
    """Public funnel event endpoint for client-side tracking. No admin auth needed."""
    try:
        from funnel_tracker import track
        data = request.get_json(silent=True) or {}
        stage = data.get('stage', '')
        if not stage:
            return jsonify({"error": "stage required"}), 400

        # Get user_id from session if logged in
        uid = None
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                uid = current_user.id
        except Exception:
            pass

        source = data.get('source', 'direct')
        metadata = data.get('metadata')
        track(stage=stage, source=source, user_id=uid, metadata=metadata)
        return jsonify({"ok": True})
    except Exception as e:
        logging.debug(f"Public funnel track error: {e}")
        return jsonify({"ok": True})  # Never fail client-side tracking


@gtm_bp.route('/api/gtm/threads', methods=['GET'])
@_db_retry
def api_gtm_threads():
    """Get scanned threads."""
    _check_admin()
    try:
        from models import GTMScannedThread
        limit = min(int(request.args.get('limit', 50)), 200)
        threads = GTMScannedThread.query.order_by(
            GTMScannedThread.created_at.desc()
        ).limit(limit).all()
        return jsonify([t.to_dict() for t in threads])
    except Exception as e:
        logging.error(f"GTM threads error: {e}")
        return jsonify({"error": "Internal error"}), 500


# ── Subreddit Content Engine ────────────────────────────────────

@gtm_bp.route('/api/gtm/content/generate', methods=['POST'])
def api_gtm_generate_content():
    """Generate a daily post for a specific platform (reddit|facebook|nextdoor)."""
    _check_admin()
    try:
        from gtm.content_engine import generate_post_for_platform
        from models import GTMSubredditPost, Analysis
        from datetime import date as date_type

        data = request.get_json() or {}
        target_str   = data.get('date')
        platform     = data.get('platform', 'reddit')
        target_group = data.get('target_group', '')
        target_date  = date_type.fromisoformat(target_str) if target_str else date_type.today()

        # Dedup: one post per platform per day
        existing = GTMSubredditPost.query.filter_by(
            scheduled_date=target_date, platform=platform
        ).first()
        if existing:
            return jsonify({"status": "exists", "post": existing.to_dict(),
                            "message": f"Post already exists for {platform} on {target_date}"})

        models_map = {"Analysis": Analysis}
        post_data = generate_post_for_platform(
            _db.session, models_map, platform, target_group, target_date
        )

        post = GTMSubredditPost(
            title        = post_data['title'],
            body         = post_data['body'],
            pillar       = post_data['pillar'],
            pillar_label = post_data['pillar_label'],
            flair        = post_data.get('flair', ''),
            data_summary = post_data.get('data_summary', ''),
            scheduled_date = target_date,
            status       = 'draft',
            topic_key    = post_data.get('topic_key', ''),
            platform     = platform,
            target_group = target_group,
        )
        _db.session.add(post)
        _db.session.commit()
        return jsonify({"status": "generated", "post": post.to_dict()})
    except werkzeug.exceptions.HTTPException:
        raise  # Let 403/401 propagate
    except Exception as e:
        _db.session.rollback()
        import traceback
        tb = traceback.format_exc()
        logging.error(f"GTM content generate error: {e}\n{tb}")
        return jsonify({"error": str(e), "traceback": tb.split('\n')[-3] if tb else ''}), 500


@gtm_bp.route('/api/gtm/content/batch', methods=['POST'])
def api_gtm_generate_batch():
    """Generate posts for the next N days."""
    _check_admin()
    try:
        from gtm.content_engine import generate_daily_post
        from models import GTMSubredditPost, Analysis
        from datetime import date as date_type

        data = request.get_json() or {}
        days = min(int(data.get('days', 7)), 14)
        models = {"Analysis": Analysis}
        generated, skipped = [], []

        for i in range(days):
            target_date = date_type.today() + timedelta(days=i)
            if GTMSubredditPost.query.filter_by(scheduled_date=target_date).first():
                skipped.append(target_date.isoformat())
                continue
            post_data = generate_daily_post(_db.session, models, target_date)
            post = GTMSubredditPost(
                title=post_data['title'], body=post_data['body'],
                pillar=post_data['pillar'], pillar_label=post_data['pillar_label'],
                flair=post_data['flair'], data_summary=post_data.get('data_summary', ''),
                scheduled_date=target_date, status='draft',
                topic_key=post_data.get('topic_key', ''),
            )
            _db.session.add(post)
            generated.append(target_date.isoformat())

        _db.session.commit()
        return jsonify({"status": "ok", "generated": generated, "skipped": skipped})
    except werkzeug.exceptions.HTTPException:
        raise
    except Exception as e:
        _db.session.rollback()
        import traceback
        tb = traceback.format_exc()
        logging.error(f"GTM content batch error: {e}\n{tb}")
        return jsonify({"error": str(e), "traceback": tb.split('\n')[-3] if tb else ''}), 500


@gtm_bp.route('/api/gtm/content', methods=['GET'])
@_db_retry
def api_gtm_content_list():
    """List subreddit content posts. Auto-purges unposted drafts older than 3 days."""
    _check_admin()
    try:
        from models import GTMSubredditPost
        from datetime import date as _date, timedelta as _td

        # ── Retention: delete draft/skipped posts older than 3 days ──────────
        # Posted/approved posts are kept permanently for the record.
        cutoff = _date.today() - _td(days=3)
        old_drafts = GTMSubredditPost.query.filter(
            GTMSubredditPost.scheduled_date < cutoff,
            GTMSubredditPost.status.in_(['draft', 'skipped'])
        ).all()
        if old_drafts:
            for p in old_drafts:
                _db.session.delete(p)
            _db.session.commit()
            logging.info(f"GTM retention: purged {len(old_drafts)} old draft/skipped posts")

        status   = request.args.get('status')
        platform = request.args.get('platform')  # reddit | biggerpockets | facebook | nextdoor
        limit    = min(int(request.args.get('limit', 30)), 100)
        q = GTMSubredditPost.query
        if status:
            q = q.filter_by(status=status)
        if platform:
            q = q.filter_by(platform=platform)
        posts = q.order_by(GTMSubredditPost.scheduled_date.desc()).limit(limit).all()
        return jsonify([p.to_dict() for p in posts])
    except Exception as e:
        logging.error(f"GTM content list error: {e}")
        return jsonify([])  # Return empty list instead of 500


@gtm_bp.route('/api/gtm/content/<int:post_id>', methods=['PUT'])
def api_gtm_content_update(post_id):
    """Update a content post."""
    _check_admin()
    try:
        from models import GTMSubredditPost
        post = GTMSubredditPost.query.get(post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        data = request.get_json() or {}
        if 'edited_body' in data:
            post.edited_body = data['edited_body']
        if 'title' in data:
            post.title = data['title']
        if 'status' in data:
            new_status = data['status']
            if new_status in ('draft', 'approved', 'posted', 'skipped'):
                post.status = new_status
                if new_status == 'posted':
                    post.posted_at = datetime.utcnow()
                    if 'posted_url' in data:
                        post.posted_url = data['posted_url']
                elif new_status == 'skipped':
                    post.skip_reason = data.get('skip_reason', '')
        _db.session.commit()
        return jsonify({"status": "updated", "post": post.to_dict()})
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM content update error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/content/<int:post_id>', methods=['DELETE'])
def api_gtm_content_delete(post_id):
    """Delete a content post."""
    _check_admin()
    try:
        from models import GTMSubredditPost
        post = GTMSubredditPost.query.get(post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        _db.session.delete(post)
        _db.session.commit()
        return jsonify({"status": "deleted"})
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM content delete error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/content/calendar', methods=['GET'])
def api_gtm_content_calendar():
    """Get content calendar for the next 7 days."""
    _check_admin()
    try:
        from gtm.content_engine import get_pillar_for_date
        from datetime import date as date_type

        days = []
        for i in range(7):
            d = date_type.today() + timedelta(days=i)
            pillar = get_pillar_for_date(d)
            post = None
            try:
                from models import GTMSubredditPost
                post = GTMSubredditPost.query.filter_by(scheduled_date=d).first()
            except Exception:
                pass  # Table may not exist yet
            days.append({
                'date': d.isoformat(), 'day_name': d.strftime('%A'),
                'pillar': pillar['key'], 'pillar_label': pillar['label'],
                'flair': pillar['flair'], 'has_post': post is not None,
                'post': post.to_dict() if post else None,
            })
        return jsonify(days)
    except Exception as e:
        logging.error(f"GTM content calendar error: {e}")
        return jsonify({"error": str(e)}), 500


# ── Target Communities ───────────────────────────────────────────

@gtm_bp.route('/api/gtm/subreddits', methods=['GET'])
@_db_retry
def api_gtm_subreddits():
    """Get all target subreddits."""
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        from flask import request as _req
        _seed_default_subreddits()
        platform = _req.args.get('platform')
        q = GTMTargetSubreddit.query
        if platform:
            q = q.filter_by(platform=platform)
        subs = q.order_by(
            GTMTargetSubreddit.priority.asc(), GTMTargetSubreddit.name.asc()
        ).all()
        return jsonify([s.to_dict() for s in subs])
    except Exception as e:
        logging.error(f"GTM subreddits error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/subreddits', methods=['POST'])
def api_gtm_add_subreddit():
    """Add a new target subreddit."""
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        if not name:
            return jsonify({"error": "Name required"}), 400
        platform = data.get('platform', 'reddit')
        existing = GTMTargetSubreddit.query.filter_by(name=name, platform=platform).first()
        if existing:
            return jsonify({"error": f"'{name}' already exists"}), 409
        sub = GTMTargetSubreddit(
            name=name, platform=platform,
            priority=int(data.get('priority', 5)),
            notes=data.get('notes', ''),
            url=data.get('url', ''),
        )
        _db.session.add(sub)
        _db.session.commit()
        return jsonify({"status": "created", "subreddit": sub.to_dict()})
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM add subreddit error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/subreddits/<int:sub_id>', methods=['PUT'])
def api_gtm_update_subreddit(sub_id):
    """Update a target subreddit."""
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        sub = GTMTargetSubreddit.query.get(sub_id)
        if not sub:
            return jsonify({"error": "Not found"}), 404
        data = request.get_json() or {}
        if 'priority' in data:
            sub.priority = int(data['priority'])
        if 'notes' in data:
            sub.notes = data['notes']
        if 'active' in data:
            sub.active = bool(data['active'])
        _db.session.commit()
        return jsonify({"status": "updated", "subreddit": sub.to_dict()})
    except Exception as e:
        _db.session.rollback()
        logging.error(f"GTM update subreddit error: {e}")
        return jsonify({"error": "Internal error"}), 500


@gtm_bp.route('/api/gtm/subreddits/<int:sub_id>', methods=['DELETE'])
def api_gtm_delete_subreddit(sub_id):
    """Delete a target subreddit."""
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        sub = GTMTargetSubreddit.query.get(sub_id)
        if not sub:
            return jsonify({"error": "Not found"}), 404
        name = sub.name
        _db.session.delete(sub)
        _db.session.commit()
        return jsonify({"status": "deleted", "name": name})
    except Exception as e:
        logging.error(f"GTM delete subreddit error: {e}")
        return jsonify({"error": "Internal error"}), 500


# ── Seed Helpers ─────────────────────────────────────────────────

def _seed_default_subreddits():
    """Seed the default community list if table is empty.
    Uses a module-level flag to avoid a DB round-trip on every request.
    """
    global _subreddits_seeded
    if _subreddits_seeded:
        return
    from models import GTMTargetSubreddit

    # Migration: wipe any record that is stored as 'reddit' but has a BP/Facebook/Nextdoor URL
    # This fixes the polluted DB where all platforms got mixed into 'reddit'
    try:
        from sqlalchemy import or_ as _or_
        # Valid reddit entries: short names without spaces/commas, no external URLs
        # Remove anything that looks like a city, FB group, or BP forum
        bad = GTMTargetSubreddit.query.filter(
            GTMTargetSubreddit.platform == 'reddit'
        ).filter(
            _or_(
                GTMTargetSubreddit.url.like('%biggerpockets%'),
                GTMTargetSubreddit.url.like('%facebook.com%'),
                GTMTargetSubreddit.url.like('%nextdoor.com%'),
                GTMTargetSubreddit.name.like('%, CA%'),
                GTMTargetSubreddit.name.like('%Home Buyers%'),
                GTMTargetSubreddit.name.like('%Home Buyer%'),
                GTMTargetSubreddit.name.like('%Bay Area Real Estate%'),
                GTMTargetSubreddit.name.like('%Home Buying%'),
                GTMTargetSubreddit.name.like('%Los Angeles%'),
                GTMTargetSubreddit.name.like('%San Diego%'),
                GTMTargetSubreddit.name.like('%Investors%'),
                GTMTargetSubreddit.name.like('%Tips & Advice%'),
            )
        ).all()
        if bad:
            for b in bad:
                _db.session.delete(b)
            _db.session.commit()
            logging.info(f"Community migration: removed {len(bad)} misclassified reddit records")
    except Exception as e:
        _db.session.rollback()
        logging.warning(f"Community migration skipped: {e}")

    if GTMTargetSubreddit.query.first():
        _seed_bp_forums()
        _subreddits_seeded = True
        return
    defaults = [
        # ── Reddit — verified real subreddits only ─────────────────────
        ("OfferWiseAI",      "reddit", 1, "Our own subreddit — original posts",                        "https://www.reddit.com/r/OfferWiseAI"),
        ("HomeInspections",  "reddit", 1, "Real sub, 45K members — inspection discussion, replies only", "https://www.reddit.com/r/HomeInspections"),
        ("homebuying",       "reddit", 2, "Active buying process discussions, replies only",             "https://www.reddit.com/r/homebuying"),
        ("RealEstateAdvice", "reddit", 2, "37K members, high intent advice seekers, replies only",       "https://www.reddit.com/r/RealEstateAdvice"),
        ("RealEstate",       "reddit", 3, "304K members, broad RE discussion, replies only",             "https://www.reddit.com/r/RealEstate"),
        ("bayarea",          "reddit", 4, "Local Bay Area housing discussions, replies only",            "https://www.reddit.com/r/bayarea"),
        ("SanJose",          "reddit", 4, "Local San Jose housing threads, replies only",               "https://www.reddit.com/r/SanJose"),
        # ── BiggerPockets — verified real forum URLs ───────────────────
        ("First-Time Home Buyer", "biggerpockets", 1, "Direct match — first-time buyers",                   "https://www.biggerpockets.com/forums/903"),
        ("Home Inspections",      "biggerpockets", 1, "Direct match — inspection cost and repair questions", "https://www.biggerpockets.com/forums/88"),
        ("California RE Q&A",     "biggerpockets", 2, "CA-specific TDS, disclosure, inspection threads",    "https://www.biggerpockets.com/forums/548"),
        ("Deal Analysis",         "biggerpockets", 2, "Repair cost and negotiation leverage threads",        "https://www.biggerpockets.com/forums/88"),
        ("Starting Out",          "biggerpockets", 2, "New buyers, high intent — inspection questions",      "https://www.biggerpockets.com/forums/12"),
    ]
    for name, platform, priority, notes, url in defaults:
        _db.session.add(GTMTargetSubreddit(
            name=name, platform=platform, priority=priority, notes=notes, url=url
        ))
    _db.session.commit()
    _subreddits_seeded = True
    logging.info(f"Seeded {len(defaults)} default target communities (Reddit + BiggerPockets)")


def _seed_bp_forums():
    """Seed BiggerPockets forums if none exist yet."""
    from models import GTMTargetSubreddit
    if GTMTargetSubreddit.query.filter_by(platform='biggerpockets').first():
        return
    bp_forums = [
        ("First-Time Home Buyer", 1, "Direct match — first-time buyers asking about inspections and offers", "https://www.biggerpockets.com/forums/903"),
        ("Home Inspections",      1, "Direct match — inspection cost and repair questions", "https://www.biggerpockets.com/forums/88"),
        ("California RE Q&A",     2, "CA-specific — TDS, disclosure, and inspection threads", "https://www.biggerpockets.com/forums/548"),
        ("Deal Analysis",         2, "Analyzing deals — repair cost and negotiation leverage threads", "https://www.biggerpockets.com/forums/88"),
        ("Starting Out",          2, "New buyers high intent — inspection and offer questions", "https://www.biggerpockets.com/forums/12"),
    ]
    for name, priority, notes, url in bp_forums:
        _db.session.add(GTMTargetSubreddit(
            name=name, platform='biggerpockets', priority=priority, notes=notes, url=url
        ))
    _db.session.commit()
    logging.info(f"Seeded {len(bp_forums)} BiggerPockets target forums")


# ── Internal Helpers ─────────────────────────────────────────────

def _check_admin():
    """Check admin access for API routes. Raises 403 if not authorized."""
    from flask import abort, request, jsonify
    from flask_login import current_user
    from werkzeug.exceptions import Forbidden
    
    # Fail closed: if _is_admin isn't injected yet, reject
    if not _is_admin:
        raise Forbidden()
    
    result = _is_admin()
    if not result:
        raise Forbidden()


# ═══════════════════════════════════════════════════════════════
# FACEBOOK GROUPS — target group management + post generation
# ═══════════════════════════════════════════════════════════════

@gtm_bp.route('/api/gtm/facebook/groups', methods=['GET'])
def api_fb_groups_list():
    """List target Facebook groups."""
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        _seed_fb_groups()
        groups = GTMTargetSubreddit.query.filter_by(platform='facebook') \
            .order_by(GTMTargetSubreddit.priority.asc()).all()
        return jsonify([g.to_dict() for g in groups])
    except Exception as e:
        logging.error(f'FB groups list error: {e}')
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/facebook/groups', methods=['POST'])
def api_fb_groups_add():
    """Add a target Facebook group."""
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Name required'}), 400
        existing = GTMTargetSubreddit.query.filter_by(name=name, platform='facebook').first()
        if existing:
            return jsonify({'error': f"'{name}' already exists"}), 409
        g = GTMTargetSubreddit(
            name=name, platform='facebook',
            priority=int(data.get('priority', 5)),
            notes=data.get('notes', ''),
            url=data.get('url', ''),
        )
        _db.session.add(g)
        _db.session.commit()
        return jsonify({'status': 'created', 'group': g.to_dict()})
    except Exception as e:
        _db.session.rollback()
        logging.error(f'FB group add error: {e}')
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/facebook/groups/<int:gid>', methods=['PUT'])
def api_fb_groups_update(gid):
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        g = GTMTargetSubreddit.query.get(gid)
        if not g or g.platform != 'facebook':
            return jsonify({'error': 'Not found'}), 404
        data = request.get_json() or {}
        if 'enabled' in data: g.enabled = bool(data['enabled'])
        if 'notes'   in data: g.notes   = data['notes']
        if 'priority'in data: g.priority = int(data['priority'])
        _db.session.commit()
        return jsonify({'status': 'updated', 'group': g.to_dict()})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/facebook/groups/<int:gid>', methods=['DELETE'])
def api_fb_groups_delete(gid):
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        g = GTMTargetSubreddit.query.get(gid)
        if not g or g.platform != 'facebook':
            return jsonify({'error': 'Not found'}), 404
        _db.session.delete(g)
        _db.session.commit()
        return jsonify({'status': 'deleted'})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/facebook/posts', methods=['GET'])
@_db_retry
def api_fb_posts_list():
    """List Facebook post drafts."""
    _check_admin()
    try:
        from models import GTMSubredditPost
        status = request.args.get('status')
        limit  = min(int(request.args.get('limit', 20)), 100)
        q = GTMSubredditPost.query.filter_by(platform='facebook')
        if status:
            q = q.filter_by(status=status)
        posts = q.order_by(GTMSubredditPost.scheduled_date.desc()).limit(limit).all()
        return jsonify([p.to_dict() for p in posts])
    except Exception as e:
        logging.error(f'FB posts list error: {e}')
        return jsonify([])


@gtm_bp.route('/api/gtm/facebook/posts/<int:pid>', methods=['PUT'])
def api_fb_posts_update(pid):
    """Update a Facebook post draft (edit body / change status)."""
    _check_admin()
    try:
        from models import GTMSubredditPost
        post = GTMSubredditPost.query.get(pid)
        if not post or post.platform != 'facebook':
            return jsonify({'error': 'Not found'}), 404
        data = request.get_json() or {}
        if 'edited_body' in data: post.edited_body = data['edited_body']
        if 'title'       in data: post.title       = data['title']
        if 'target_group'in data: post.target_group = data['target_group']
        if 'status'      in data:
            s = data['status']
            if s in ('draft', 'approved', 'posted', 'skipped'):
                post.status = s
                if s == 'posted':
                    post.posted_at  = datetime.utcnow()
                    post.posted_url = data.get('posted_url', '')
        _db.session.commit()
        return jsonify({'status': 'updated', 'post': post.to_dict()})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/facebook/posts/<int:pid>', methods=['DELETE'])
def api_fb_posts_delete(pid):
    _check_admin()
    try:
        from models import GTMSubredditPost
        post = GTMSubredditPost.query.get(pid)
        if not post or post.platform != 'facebook':
            return jsonify({'error': 'Not found'}), 404
        _db.session.delete(post)
        _db.session.commit()
        return jsonify({'status': 'deleted'})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/facebook/generate', methods=['POST'])
def api_fb_generate():
    """Generate a Facebook group post using Claude."""
    _check_admin()
    try:
        import os, requests as http_req
        from gtm.content_engine import collect_aggregate_stats, get_pillar_for_date
        from models import GTMSubredditPost, Analysis, GTMTargetSubreddit
        from datetime import date as _date

        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY not set'}), 503

        data    = request.get_json() or {}
        today   = _date.today()
        pillar  = get_pillar_for_date(today)
        models  = {'Analysis': Analysis}
        stats   = collect_aggregate_stats(_db.session, models)
        group   = data.get('target_group', 'First Time Home Buyers - Bay Area CA')

        avg_repair  = stats.get('avg_repair_cost', 18500)
        avg_score   = stats.get('avg_offer_score', 62)
        avg_transp  = stats.get('avg_transparency_score', 64)
        avg_finds   = stats.get('avg_findings_per_property', 8)
        deal_pct    = stats.get('deal_breakers_pct', 16)

        pillar_prompts = {
            'what_were_seeing':    f'Share data-driven insight: average repair costs are ${avg_repair:,}, average OfferScore is {avg_score}/100, {deal_pct}% of properties have deal-breakers. Make it feel like insider knowledge from analyzing hundreds of Bay Area properties.',
            'first_timer_tuesday': 'Write a first-timer tip about reading inspection reports or seller disclosures. Use a specific, surprising example.',
            'did_you_know':        f'Share a surprising disclosure fact. Sellers disclose only about {avg_transp}% of what inspectors find. Use a concrete story format.',
            'real_numbers':        f'Share what repairs actually cost. Average ${avg_repair:,} per property, {avg_finds} findings each. Give specific numbers for one repair type.',
            'red_flag_friday':     'Share one specific red flag buyers miss in inspections or disclosures. Be specific and practical.',
            'community_qa':        'Ask an engaging question that homebuyers and agents will want to answer from their own experience.',
            'weekly_digest':       f'Weekly roundup: avg repairs ${avg_repair:,}, avg score {avg_score}/100. Make it feel like a useful weekly data brief.',
        }
        pillar_hint = pillar_prompts.get(pillar['key'], pillar_prompts['community_qa'])

        prompt = f"""You are writing a Facebook group post for OfferWise (getofferwise.ai) to be posted in the group "{group}".

PILLAR: {pillar['label']} — {pillar_hint}

FACEBOOK FORMAT RULES (critical):
- NO markdown. No **bold**, no ## headers, no bullet hyphens. Facebook renders these as literal characters.
- Use ALL CAPS sparingly for emphasis (one or two words max).
- Use line breaks between paragraphs. Keep paragraphs to 2-3 sentences.
- 150-250 words total — Facebook posts that are too long get cut off and lose engagement.
- Conversational, human tone. Write like a knowledgeable friend, not a company.
- End with a question to drive comments.
- ONE natural mention of OfferWise near the end: "I ran it through OfferWise (getofferwise.ai) and..." or similar.
- Do NOT start with "Hey everyone" or "Hi friends" — start with the hook immediately.

Return JSON only:
{{"title": "short internal label", "body": "the full post text", "pillar": "{pillar['key']}"}}"""

        import json, re as _re
        from ai_client import get_ai_response
        raw = get_ai_response(prompt, max_tokens=600)
        raw = raw.strip()
        raw = _re.sub(r'^```\w*\n?', '', raw); raw = _re.sub(r'\n?```$', '', raw)
        result = json.loads(raw)

        post = GTMSubredditPost(
            title          = result.get('title', f'Facebook — {pillar["label"]}'),
            body           = result.get('body', ''),
            pillar         = pillar['key'],
            pillar_label   = pillar['label'],
            flair          = '',
            platform       = 'facebook',
            target_group   = group,
            scheduled_date = today,
            status         = 'draft',
            topic_key      = f'fb:{pillar["key"]}:{today.isoformat()}',
        )
        _db.session.add(post)
        _db.session.commit()
        return jsonify({'status': 'generated', 'post': post.to_dict()})

    except Exception as e:
        _db.session.rollback()
        import traceback
        logging.error(f'FB generate error: {e}\n{traceback.format_exc()}')
        return jsonify({'error': str(e)}), 500



# ═══════════════════════════════════════════════════════════════
# SOCIAL REPLY GENERATOR — value-add replies for FB + Nextdoor
# ═══════════════════════════════════════════════════════════════

@gtm_bp.route('/api/gtm/social-reply/generate', methods=['POST'])
def api_social_reply_generate():
    """
    Generate a value-add reply to an existing Facebook or Nextdoor post.
    No direct product promotion — subtle, helpful reply with bio link context.
    """
    _check_admin()
    try:
        import os
        from ai_client import get_ai_response
        from gtm.content_engine import collect_aggregate_stats, get_pillar_for_date
        from models import Analysis
        from datetime import date as _date

        data      = request.get_json() or {}
        platform  = data.get('platform', 'facebook')
        post_url  = data.get('post_url', '').strip()
        post_text = data.get('post_text', '').strip()  # optional — pasted post content

        today   = _date.today()
        models  = {'Analysis': Analysis}
        stats   = collect_aggregate_stats(_db.session, models)
        avg_repair = stats.get('avg_repair_cost', 18500)
        avg_score  = stats.get('avg_offer_score', 62)

        platform_label = 'Facebook group' if platform == 'facebook' else 'Nextdoor neighborhood'
        post_context = f'\n\nPost being replied to:\n{post_text}' if post_text else ''

        prompt = f"""You are writing a reply for Francis, a real estate analytics expert, to post in a {platform_label}.

CONTEXT: OfferWise (getofferwise.ai) analyzes inspection reports and seller disclosures. Average repair cost in Bay Area: ${avg_repair:,}. Average OfferScore: {avg_score}/100.{post_context}

REPLY RULES (critical):
- Be genuinely helpful. Answer the question or add real value first.
- NO direct promotion. Do not say "check out OfferWise" or mention the product by name in the reply itself.
- One subtle signal is allowed: reference your expertise naturally, e.g. "In my work analyzing hundreds of Bay Area inspection reports, I've seen..."
- Keep it 2-4 sentences. Conversational, warm, specific.
- If the post is about repairs, disclosures, or offer strategy — lead with a concrete data point.
- End naturally — no CTA, no link, no hashtags.

Return JSON only: {{"reply_text": "the reply"}}"""

        raw    = get_ai_response(prompt, max_tokens=300)
        import json, re as _re
        raw    = raw.strip()
        raw    = _re.sub(r'^```\w*\n?', '', raw)
        raw    = _re.sub(r'\n?```$', '', raw)
        result = json.loads(raw)

        return jsonify({
            'status':     'generated',
            'reply_text': result.get('reply_text', ''),
            'platform':   platform,
            'post_url':   post_url,
        })

    except Exception as e:
        _db.session.rollback()
        import traceback
        logging.error(f'Social reply generate error: {e}\n{traceback.format_exc()}')
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# NEXTDOOR — neighborhood management + post generation
# ═══════════════════════════════════════════════════════════════

@gtm_bp.route('/api/gtm/nextdoor/neighborhoods', methods=['GET'])
def api_nd_neighborhoods_list():
    """List target Nextdoor neighborhoods."""
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        _seed_nd_neighborhoods()
        hoods = GTMTargetSubreddit.query.filter_by(platform='nextdoor') \
            .order_by(GTMTargetSubreddit.priority.asc()).all()
        return jsonify([h.to_dict() for h in hoods])
    except Exception as e:
        logging.error(f'ND neighborhoods list error: {e}')
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/nextdoor/neighborhoods', methods=['POST'])
def api_nd_neighborhoods_add():
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Name required'}), 400
        existing = GTMTargetSubreddit.query.filter_by(name=name, platform='nextdoor').first()
        if existing:
            return jsonify({'error': f"'{name}' already exists"}), 409
        h = GTMTargetSubreddit(
            name=name, platform='nextdoor',
            priority=int(data.get('priority', 5)),
            notes=data.get('notes', ''),
            url=data.get('url', ''),
        )
        _db.session.add(h)
        _db.session.commit()
        return jsonify({'status': 'created', 'neighborhood': h.to_dict()})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/nextdoor/neighborhoods/<int:hid>', methods=['PUT'])
def api_nd_neighborhoods_update(hid):
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        h = GTMTargetSubreddit.query.get(hid)
        if not h or h.platform != 'nextdoor':
            return jsonify({'error': 'Not found'}), 404
        data = request.get_json() or {}
        if 'enabled'  in data: h.enabled  = bool(data['enabled'])
        if 'notes'    in data: h.notes    = data['notes']
        if 'priority' in data: h.priority = int(data['priority'])
        _db.session.commit()
        return jsonify({'status': 'updated', 'neighborhood': h.to_dict()})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/nextdoor/neighborhoods/<int:hid>', methods=['DELETE'])
def api_nd_neighborhoods_delete(hid):
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        h = GTMTargetSubreddit.query.get(hid)
        if not h or h.platform != 'nextdoor':
            return jsonify({'error': 'Not found'}), 404
        _db.session.delete(h)
        _db.session.commit()
        return jsonify({'status': 'deleted'})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/nextdoor/posts', methods=['GET'])
@_db_retry
def api_nd_posts_list():
    """List Nextdoor post drafts."""
    _check_admin()
    try:
        from models import GTMSubredditPost
        status = request.args.get('status')
        limit  = min(int(request.args.get('limit', 20)), 100)
        q = GTMSubredditPost.query.filter_by(platform='nextdoor')
        if status:
            q = q.filter_by(status=status)
        posts = q.order_by(GTMSubredditPost.scheduled_date.desc()).limit(limit).all()
        return jsonify([p.to_dict() for p in posts])
    except Exception as e:
        logging.error(f'ND posts list error: {e}')
        return jsonify([])


@gtm_bp.route('/api/gtm/nextdoor/posts/<int:pid>', methods=['PUT'])
def api_nd_posts_update(pid):
    """Update a Nextdoor post draft."""
    _check_admin()
    try:
        from models import GTMSubredditPost
        post = GTMSubredditPost.query.get(pid)
        if not post or post.platform != 'nextdoor':
            return jsonify({'error': 'Not found'}), 404
        data = request.get_json() or {}
        if 'edited_body'  in data: post.edited_body  = data['edited_body']
        if 'title'        in data: post.title        = data['title']
        if 'target_group' in data: post.target_group = data['target_group']
        if 'status'       in data:
            s = data['status']
            if s in ('draft', 'approved', 'posted', 'skipped'):
                post.status = s
                if s == 'posted':
                    post.posted_at  = datetime.utcnow()
                    post.posted_url = data.get('posted_url', '')
        _db.session.commit()
        return jsonify({'status': 'updated', 'post': post.to_dict()})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/nextdoor/posts/<int:pid>', methods=['DELETE'])
def api_nd_posts_delete(pid):
    _check_admin()
    try:
        from models import GTMSubredditPost
        post = GTMSubredditPost.query.get(pid)
        if not post or post.platform != 'nextdoor':
            return jsonify({'error': 'Not found'}), 404
        _db.session.delete(post)
        _db.session.commit()
        return jsonify({'status': 'deleted'})
    except Exception as e:
        _db.session.rollback()
        return jsonify({'error': 'Internal error'}), 500


@gtm_bp.route('/api/gtm/nextdoor/generate', methods=['POST'])
def api_nd_generate():
    """Generate a Nextdoor neighborhood post using Claude."""
    _check_admin()
    try:
        import os, requests as http_req
        from gtm.content_engine import collect_aggregate_stats, get_pillar_for_date
        from models import GTMSubredditPost, Analysis
        from datetime import date as _date

        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return jsonify({'error': 'ANTHROPIC_API_KEY not set'}), 503

        data         = request.get_json() or {}
        today        = _date.today()
        pillar       = get_pillar_for_date(today)
        models       = {'Analysis': Analysis}
        stats        = collect_aggregate_stats(_db.session, models)
        neighborhood = data.get('target_neighborhood', 'San Jose, CA')

        avg_repair = stats.get('avg_repair_cost', 18500)
        avg_score  = stats.get('avg_offer_score', 62)
        avg_transp = stats.get('avg_transparency_score', 64)
        deal_pct   = stats.get('deal_breakers_pct', 16)

        pillar_prompts = {
            'what_were_seeing':    f'Share what you\'re seeing in the local market: repair costs averaging ${avg_repair:,}, {deal_pct}% of properties have serious issues buyers miss.',
            'first_timer_tuesday': 'Share one thing first-time buyers in this neighborhood should know about home inspections.',
            'did_you_know':        f'Share that sellers only disclose about {avg_transp}% of what inspectors find. Give a local, relatable example.',
            'real_numbers':        f'Share real repair cost data for the area. Average ${avg_repair:,} per property.',
            'red_flag_friday':     'Share one red flag specific to California homes (foundation, old electrical, disclosure gaps).',
            'community_qa':        'Ask neighbors a question about their home buying or selling experience.',
            'weekly_digest':       'Share a brief local market update — what buyers should know this week.',
        }
        pillar_hint = pillar_prompts.get(pillar['key'], pillar_prompts['community_qa'])

        prompt = f"""You are writing a Nextdoor post for the neighborhood "{neighborhood}" for OfferWise (getofferwise.ai).

PILLAR: {pillar['label']} — {pillar_hint}

NEXTDOOR FORMAT RULES (critical):
- Write as a LOCAL RESIDENT and business owner, not as a company.
- Hyper-local, neighbor-to-neighbor tone. Reference "{neighborhood}" or "our area" specifically.
- NO links in the body text — Nextdoor often strips or flags them. Instead say "search OfferWise" or "getofferwise.ai".
- Short: 80-120 words maximum. Nextdoor readers scroll fast.
- NO markdown formatting. Plain text only.
- End with a genuine local question to drive comments.
- Mention OfferWise naturally once, as a local resource: "I run OfferWise, a free tool for buyers here in {neighborhood}..."
- Warm, helpful, not promotional. Nextdoor penalises overt advertising.

Return JSON only:
{{"title": "short internal label", "body": "the full post text", "pillar": "{pillar['key']}"}}"""

        import json, re as _re
        from ai_client import get_ai_response
        raw = get_ai_response(prompt, max_tokens=400)
        raw = raw.strip()
        raw = _re.sub(r'^```\w*\n?', '', raw); raw = _re.sub(r'\n?```$', '', raw)
        result = json.loads(raw)

        post = GTMSubredditPost(
            title          = result.get('title', f'Nextdoor — {pillar["label"]}'),
            body           = result.get('body', ''),
            pillar         = pillar['key'],
            pillar_label   = pillar['label'],
            flair          = '',
            platform       = 'nextdoor',
            target_group   = neighborhood,
            scheduled_date = today,
            status         = 'draft',
            topic_key      = f'nd:{pillar["key"]}:{today.isoformat()}',
        )
        _db.session.add(post)
        _db.session.commit()
        return jsonify({'status': 'generated', 'post': post.to_dict()})

    except Exception as e:
        _db.session.rollback()
        import traceback
        logging.error(f'ND generate error: {e}\n{traceback.format_exc()}')
        return jsonify({'error': str(e)}), 500


# ── Platform seed helpers ────────────────────────────────────────

def _seed_fb_groups():
    """Sync Facebook target groups — upserts on every call so URLs stay current."""
    from models import GTMTargetSubreddit
    # (name, priority, notes, url)
    desired = [
        # Only verified real groups — add more via the admin UI as you confirm them
        ('Bay Area Real Estate', 1, 'Verified active group. High volume Bay Area RE discussion.', 'https://www.facebook.com/groups/bayarearealestategroup'),
    ]
    try:
        existing = {r.name: r for r in GTMTargetSubreddit.query.filter_by(platform='facebook').all()}
        desired_names = {d[0] for d in desired}
        for name, record in existing.items():
            if name not in desired_names:
                _db.session.delete(record)
        for name, priority, notes, url in desired:
            rec = existing.get(name)
            if rec:
                rec.url = url; rec.priority = priority; rec.notes = notes
            else:
                _db.session.add(GTMTargetSubreddit(
                    name=name, platform='facebook', priority=priority, notes=notes, url=url))
        _db.session.commit()
        logging.info(f'Synced {len(desired)} Facebook target groups')
    except Exception as e:
        _db.session.rollback()
        logging.warning(f'FB group sync error: {e}')


def _seed_nd_neighborhoods():
    """Sync Nextdoor neighborhoods — upserts on every call so URLs stay current."""
    from models import GTMTargetSubreddit
    # (name, priority, notes, url)
    desired = [
        ('San Jose, CA',       1, 'Broad SJ reach. Use neighborhood posts + nearby share.',              'https://nextdoor.com/city/san--jose--ca/'),
        ('Willow Glen, CA',    1, 'Affluent SJ neighborhood. High homeownership, active RE discussion.', 'https://nextdoor.com/neighborhood/willow-glen--san-jose--ca/'),
        ('Almaden Valley, CA', 2, 'High-value SJ market. Strong buyer intent, inspection scrutiny.',     'https://nextdoor.com/neighborhood/almaden-valley--san-jose--ca/'),
        ('Los Gatos, CA',      2, 'Luxury market. Buyers ask lots of repair/negotiation questions.',     'https://nextdoor.com/city/los--gatos--ca/'),
        ('Cupertino, CA',      2, 'Tech-heavy, high prices. Active homebuyer discussions.',              'https://nextdoor.com/city/cupertino--ca/'),
        ('Sunnyvale, CA',      3, 'Core Silicon Valley. High buyer intent.',                             'https://nextdoor.com/city/sunnyvale--ca/'),
        ('Mountain View, CA',  3, 'Tech hub, competitive market. Good fit for OfferWise.',              'https://nextdoor.com/city/mountain--view--ca/'),
        ('Fremont, CA',        3, 'East Bay. More affordable entry, growing buyer market.',              'https://nextdoor.com/city/fremont--ca/'),
        ('Oakland, CA',        4, 'East Bay urban market. Active buyer community.',                     'https://nextdoor.com/city/oakland--ca/'),
        ('Berkeley, CA',       4, 'High-income market. Analytically minded buyers — great OfferWise fit.', 'https://nextdoor.com/city/berkeley--ca/'),
    ]
    try:
        existing = {r.name: r for r in GTMTargetSubreddit.query.filter_by(platform='nextdoor').all()}
        desired_names = {d[0] for d in desired}
        for name, record in existing.items():
            if name not in desired_names:
                _db.session.delete(record)
        for name, priority, notes, url in desired:
            rec = existing.get(name)
            if rec:
                rec.url = url; rec.priority = priority; rec.notes = notes
            else:
                _db.session.add(GTMTargetSubreddit(
                    name=name, platform='nextdoor', priority=priority, notes=notes, url=url))
        _db.session.commit()
        logging.info(f'Synced {len(desired)} Nextdoor neighborhoods')
    except Exception as e:
        _db.session.rollback()
        logging.warning(f'ND neighborhood sync error: {e}')
