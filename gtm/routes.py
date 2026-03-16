"""
GTM (Go-To-Market) Blueprint
=============================
Extracted from app.py to reduce monolith size.
Contains all /api/gtm/* routes and the /admin/gtm page.
"""

import logging
import werkzeug.exceptions
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, send_from_directory

gtm_bp = Blueprint('gtm', __name__)


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
def api_gtm_drafts():
    """Get pending Reddit drafts for review."""
    _check_admin()
    try:
        from models import GTMRedditDraft
        status = request.args.get('status', 'pending')
        limit = min(int(request.args.get('limit', 20)), 100)
        drafts = GTMRedditDraft.query.filter_by(status=status)\
            .order_by(GTMRedditDraft.created_at.desc()).limit(limit).all()
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
                'model': 'claude-sonnet-4-20250514',
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
    """Generate a daily post for the subreddit."""
    _check_admin()
    try:
        from gtm.content_engine import generate_daily_post
        from models import GTMSubredditPost, Analysis
        from datetime import date as date_type

        data = request.get_json() or {}
        target_str = data.get('date')
        target_date = date_type.fromisoformat(target_str) if target_str else date_type.today()

        existing = GTMSubredditPost.query.filter_by(scheduled_date=target_date).first()
        if existing:
            return jsonify({"status": "exists", "post": existing.to_dict(),
                            "message": f"Post already exists for {target_date}"})

        models = {"Analysis": Analysis}
        post_data = generate_daily_post(_db.session, models, target_date)

        post = GTMSubredditPost(
            title=post_data['title'], body=post_data['body'],
            pillar=post_data['pillar'], pillar_label=post_data['pillar_label'],
            flair=post_data['flair'], data_summary=post_data.get('data_summary', ''),
            scheduled_date=target_date, status='draft',
            topic_key=post_data.get('topic_key', ''),
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
def api_gtm_content_list():
    """List subreddit content posts."""
    _check_admin()
    try:
        from models import GTMSubredditPost
        status = request.args.get('status')
        limit = min(int(request.args.get('limit', 30)), 100)
        q = GTMSubredditPost.query
        if status:
            q = q.filter_by(status=status)
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
def api_gtm_subreddits():
    """Get all target subreddits."""
    _check_admin()
    try:
        from models import GTMTargetSubreddit
        _seed_default_subreddits()
        subs = GTMTargetSubreddit.query.order_by(
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
    """Seed the default community list if table is empty."""
    from models import GTMTargetSubreddit
    if GTMTargetSubreddit.query.first():
        _seed_bp_forums()
        return
    defaults = [
        ("offerwiseAi", "reddit", 1, "Our own subreddit — community building", None),
        ("RealEstate", "reddit", 3, "High volume, general real estate", None),
        ("FirstTimeHomeBuyer", "reddit", 1, "Highest intent — our core audience", None),
        ("HomeInspections", "reddit", 1, "Direct match — inspection discussion", None),
        ("RealEstateAdvice", "reddit", 2, "Advice seekers, high intent", None),
        ("homebuying", "reddit", 2, "Active buying process discussions", None),
        ("RealEstateAgent", "reddit", 4, "Agent perspective, indirect value", None),
        ("bayarea", "reddit", 5, "Local — Bay Area housing discussions", None),
        ("SanJose", "reddit", 5, "Local — San Jose housing threads", None),
        ("Home Inspections", "biggerpockets", 1, "Direct match — inspection discussions", "https://www.biggerpockets.com/forums/311"),
        ("First-Time Home Buyer", "biggerpockets", 1, "Direct match — first-time buyers", "https://www.biggerpockets.com/forums/903"),
        ("Starting Out", "biggerpockets", 2, "New investors, high intent", "https://www.biggerpockets.com/forums/12"),
        ("Deal Analysis", "biggerpockets", 2, "Analyzing deals — inspection/disclosure relevant", "https://www.biggerpockets.com/forums/88"),
        ("California RE Q&A", "biggerpockets", 2, "Local — CA-specific TDS and disclosure threads", "https://www.biggerpockets.com/forums/548"),
    ]
    for name, platform, priority, notes, url in defaults:
        _db.session.add(GTMTargetSubreddit(
            name=name, platform=platform, priority=priority, notes=notes, url=url
        ))
    _db.session.commit()
    logging.info(f"Seeded {len(defaults)} default target communities (Reddit + BiggerPockets)")


def _seed_bp_forums():
    """Seed BiggerPockets forums if none exist yet."""
    from models import GTMTargetSubreddit
    if GTMTargetSubreddit.query.filter_by(platform='biggerpockets').first():
        return
    bp_forums = [
        ("Home Inspections", 1, "Direct match — inspection discussions", "https://www.biggerpockets.com/forums/311"),
        ("First-Time Home Buyer", 1, "Direct match — first-time buyers", "https://www.biggerpockets.com/forums/903"),
        ("Starting Out", 2, "New investors, high intent", "https://www.biggerpockets.com/forums/12"),
        ("Deal Analysis", 2, "Analyzing deals — inspection/disclosure relevant", "https://www.biggerpockets.com/forums/88"),
        ("California RE Q&A", 2, "Local — CA-specific TDS and disclosure threads", "https://www.biggerpockets.com/forums/548"),
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
    if _is_admin and not _is_admin():
        from flask import abort
        abort(403)
