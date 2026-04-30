"""
OfferWise Inspector Routes Blueprint
=====================================
Extracted from app.py to reduce monolith size.
Contains all /api/inspector/* and /inspector/* page routes.
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from flask import Blueprint, jsonify, request, redirect, send_from_directory, current_app
from flask_login import login_required, current_user
from blueprint_helpers import DeferredDecorator

from models import (
    db, User, Inspector, Contractor, ContractorLead, Analysis, Property,
    Agent, AgentShare, InspectorReport, PropertyWatch,
)

import os as _os
ADMIN_EMAIL = _os.environ.get('ADMIN_EMAIL', 'hello@getofferwise.ai')

def send_email(*a, **kw):
    from app import send_email as _se; return _se(*a, **kw)

def _is_admin():
    from app import is_admin; return is_admin()

class _PdfHandlerProxy:
    def __getattr__(self, name):
        from app import pdf_handler as _ph
        return getattr(_ph, name)
pdf_handler = _PdfHandlerProxy()


inspector_bp = Blueprint('inspector', __name__)
logger = logging.getLogger(__name__)

# ── Injected dependencies ──────────────────────────────────────────
_login_required     = None
_admin_required     = None
_limiter            = None

_login_req_dec = DeferredDecorator(lambda: _login_required)
_admin_req_dec = DeferredDecorator(lambda: _admin_required)


def init_inspector_blueprint(app, db_ref, login_required_fn, admin_required_fn):
    global _login_required, _admin_required, _limiter
    _login_required = login_required_fn
    _admin_required = admin_required_fn
    app.register_blueprint(inspector_bp)
    logging.info("✅ inspector blueprint registered")


@inspector_bp.route('/api/inspector/invite', methods=['POST'])
@_login_req_dec
def inspector_send_invite():
    from models import InspectorReferral
    import secrets
    data = request.get_json() or {}
    referred_email = (data.get('email') or '').strip().lower()
    referred_name = (data.get('name') or '').strip()
    if not referred_email:
        return jsonify({'error': 'Email required'}), 400

    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Inspector account not found'}), 403

    # Check not already invited
    existing = InspectorReferral.query.filter_by(
        referrer_id=insp.id, referred_email=referred_email
    ).first()
    if existing:
        return jsonify({'error': 'Already invited this email'}), 409

    token = secrets.token_urlsafe(32)
    ref = InspectorReferral(
        referrer_id=insp.id,
        referred_email=referred_email,
        referred_name=referred_name,
        invite_token=token,
        invite_sent_at=datetime.utcnow(),
    )
    db.session.add(ref)
    db.session.commit()

    invite_url = f"https://www.getofferwise.ai/for-inspectors?invite={token}"
    referrer_name = current_user.name or insp.business_name or 'A colleague'
    try:
        send_email(
            to_email=referred_email,
            subject=f"{referrer_name} invited you to OfferWise Inspector Portal",
            html_content=f"""<div style="font-family:sans-serif;background:#0f172a;color:#f1f5f9;padding:32px;border-radius:16px;max-width:600px;">
            <div style="font-size:22px;font-weight:800;margin-bottom:8px;">You've been invited to OfferWise</div>
            <div style="font-size:14px;color:#94a3b8;margin-bottom:24px;">{referrer_name} thinks you'd find this useful.</div>
            <div style="padding:16px;background:rgba(249,115,22,.08);border:1px solid rgba(249,115,22,.2);border-radius:10px;margin-bottom:24px;">
              <div style="font-weight:700;color:#f97316;margin-bottom:8px;">What OfferWise does for inspectors:</div>
              <div style="font-size:13px;color:#94a3b8;line-height:1.8;">
                ✓ Your inspection report generates a full AI buyer analysis automatically<br>
                ✓ Buyers get a shareable PDF with your name and branding on it<br>
                ✓ 5 free analyses per month — no credit card required<br>
                ✓ Upgrade to Pro ($49/mo) for unlimited
              </div>
            </div>
            <a href="{invite_url}" style="display:inline-block;padding:14px 28px;background:linear-gradient(90deg,#f97316,#f59e0b);color:white;border-radius:10px;font-weight:700;text-decoration:none;font-size:15px;">Join OfferWise Free →</a>
            <div style="margin-top:20px;font-size:12px;color:#475569;">Both you and {referrer_name} get 2 bonus analyses when you sign up.</div>
            </div>"""
        )
    except Exception as e:
        logging.warning(f"Inspector invite email failed: {e}")
        return jsonify({'error': 'Email failed to send'}), 500

    logging.info(f"Inspector invite sent: {insp.id} → {referred_email}")
    return jsonify({'success': True, 'token': token})



@inspector_bp.route('/api/inspector/invite/redeem', methods=['POST'])
def inspector_redeem_invite():
    """Called when a new inspector signs up via an invite link."""
    from models import InspectorReferral
    data = request.get_json() or {}
    token = (data.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'Token required'}), 400

    ref = InspectorReferral.query.filter_by(invite_token=token).first()
    if not ref or ref.signed_up_at:
        return jsonify({'error': 'Invalid or already used token'}), 400

    ref.signed_up_at = datetime.utcnow()
    # Give referrer 2 bonus analyses
    referrer_insp = Inspector.query.get(ref.referrer_id)
    if referrer_insp and referrer_insp.monthly_quota != -1:
        referrer_insp.monthly_quota = (referrer_insp.monthly_quota or 5) + 2
    ref.bonus_granted = True
    db.session.commit()

    logging.info(f"Inspector invite redeemed: token={token[:8]}... referrer={ref.referrer_id}")
    return jsonify({'success': True, 'bonus_granted': True})



@inspector_bp.route('/api/inspector/invites', methods=['GET'])
@_login_req_dec
def inspector_get_invites():
    from models import InspectorReferral
    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Not an inspector'}), 403
    refs = InspectorReferral.query.filter_by(referrer_id=insp.id)\
        .order_by(InspectorReferral.created_at.desc()).all()
    return jsonify({'invites': [{
        'email': r.referred_email,
        'name': r.referred_name,
        'sent_at': r.invite_sent_at.isoformat() if r.invite_sent_at else None,
        'signed_up': bool(r.signed_up_at),
        'bonus_granted': r.bonus_granted,
    } for r in refs]})


# ============================================================
# FEATURE 5: B2B PUBLIC API + KEY MANAGEMENT (v5.74.95)
# ============================================================


@inspector_bp.route('/api/inspector/register', methods=['POST'])
@_login_req_dec
def inspector_register():
    """Register current user as an inspector."""
    data = request.get_json() or {}
    existing = Inspector.query.filter_by(user_id=current_user.id).first()
    if existing:
        return jsonify({'success': True, 'inspector_id': existing.id, 'already_exists': True})

    from datetime import timedelta
    # If registering from InterNACHI landing page, apply plan immediately
    nachi_id = (data.get('internachi_member_id') or '').strip()
    nachi_already_used = Inspector.query.filter_by(internachi_member_id=nachi_id).first() if nachi_id else None
    apply_nachi = bool(nachi_id and not nachi_already_used)

    insp = Inspector(
        user_id              = current_user.id,
        business_name        = data.get('business_name', ''),
        license_number       = data.get('license_number', ''),
        license_state        = data.get('license_state', 'CA'),
        phone                = data.get('phone', ''),
        website              = data.get('website', ''),
        service_areas        = data.get('service_areas', ''),
        plan                 = 'internachi' if apply_nachi else 'free',
        monthly_quota        = 3 if apply_nachi else 5,
        monthly_used         = 0,
        quota_reset_at       = datetime.utcnow() + timedelta(days=30),
        internachi_member_id = nachi_id if apply_nachi else None,
        internachi_verified  = apply_nachi,
    )
    db.session.add(insp)
    db.session.commit()
    logging.info(f"🔍 New inspector registered: {current_user.email} — {insp.business_name}")
    # Notify admin
    try:
        send_email(
            to_email=ADMIN_EMAIL,
            subject=f"🔍 New Inspector Signup: {insp.business_name or current_user.email}",
            html_content=f"""<div style="font-family:sans-serif;padding:20px;">
            <h2 style="color:#f97316;">New Inspector Registered</h2>
            <p><b>Name:</b> {current_user.name}</p>
            <p><b>Email:</b> {current_user.email}</p>
            <p><b>Business:</b> {insp.business_name or '—'}</p>
            <p><b>License:</b> {insp.license_number or '—'} ({insp.license_state})</p>
            <p><b>Phone:</b> {insp.phone or '—'}</p>
            <p><b>Areas:</b> {insp.service_areas or '—'}</p>
            {'<p style="color:#f97316;font-weight:700;">✅ InterNACHI Member: ' + nachi_id + '</p>' if apply_nachi else ''}
            </div>"""
        )
    except Exception as e:
        logging.error(f"Inspector signup email failed: {e}")
    return jsonify({'success': True, 'inspector_id': insp.id})


@inspector_bp.route('/api/inspector/profile', methods=['GET', 'POST'])
@_login_req_dec
def inspector_profile_get():
    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if request.method == 'POST':
        data = request.get_json() or {}
        if not insp:
            return jsonify({'error': 'Not registered'}), 403
        insp.business_name  = data.get('business_name', insp.business_name)
        insp.license_number = data.get('license_number', insp.license_number)
        insp.license_state  = data.get('license_state', insp.license_state)
        insp.phone          = data.get('phone', insp.phone)
        insp.website        = data.get('website', insp.website)
        insp.service_areas  = data.get('service_areas', insp.service_areas)
        db.session.commit()
        return jsonify({'success': True})
    if not insp:
        return jsonify({'registered': False})
    return jsonify({
        'registered': True,
        'id': insp.id,
        'email': current_user.email,
        'name': current_user.name or '',
        'business_name': insp.business_name,
        'license_number': insp.license_number,
        'license_state': insp.license_state,
        'phone': insp.phone,
        'website': insp.website,
        'service_areas': insp.service_areas,
        'plan': insp.plan,
        'monthly_quota': insp.monthly_quota,
        'monthly_used': insp.monthly_used,
        'total_reports': insp.total_reports,
        'total_buyers_converted': insp.total_buyers_converted,
        'quota_remaining': max(0, insp.monthly_quota - insp.monthly_used) if insp.monthly_quota > 0 else 999,
    })


@inspector_bp.route('/api/inspector/extract-pdf', methods=['POST'])
@_login_req_dec
def inspector_extract_pdf():
    """Extract text from an uploaded inspection report PDF."""
    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Not registered as an inspector.'}), 403

    f = request.files.get('pdf')
    if not f:
        return jsonify({'error': 'No PDF file uploaded.'}), 400

    try:
        pdf_bytes = f.read()
        if len(pdf_bytes) > 50 * 1024 * 1024:
            return jsonify({'error': 'PDF too large (max 50MB).'}), 400

        result = pdf_handler.extract_text_from_bytes(pdf_bytes)
        text = result.get('text', '').strip()

        if not text or len(text) < 100:
            return jsonify({'error': 'Could not extract text from this PDF. Please paste the report text manually.'}), 422

        return jsonify({
            'success': True,
            'text': text,
            'page_count': result.get('page_count', 0),
            'method': result.get('method', 'unknown'),
            'char_count': len(text),
        })
    except Exception as e:
        logging.error(f"Inspector PDF extraction failed: {e}", exc_info=True)
        return jsonify({'error': f'PDF extraction failed: {type(e).__name__}. Please paste the text manually.'}), 500



@inspector_bp.route('/api/inspector/analyze', methods=['POST'])
@_login_req_dec
def inspector_analyze():
    """Inspector uploads a report PDF and generates a buyer-facing analysis."""
    import secrets, json as _json
    from app import pdf_handler as _pdf_mod; PDFHandler = _pdf_mod.PDFHandler if hasattr(_pdf_mod, "PDFHandler") else None

    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Not registered as an inspector. Please register first.'}), 403

    # Quota check
    if insp.monthly_quota > 0 and insp.monthly_used >= insp.monthly_quota:
        return jsonify({
            'error': 'Monthly quota reached',
            'message': f'You have used all {insp.monthly_quota} analyses this month. Upgrade to Inspector Pro for unlimited analyses.',
            'upgrade_url': '/for-inspectors#pricing'
        }), 403

    data = request.get_json() or {}
    inspection_text = (data.get('inspection_text', '') or '').replace('\x00', '')
    disclosure_text = (data.get('disclosure_text', '') or '').replace('\x00', '')
    property_address = data.get('property_address', '')
    property_price   = data.get('property_price', 0)
    buyer_name       = (data.get('buyer_name') or '').strip()
    buyer_email      = (data.get('buyer_email') or '').strip()

    if not inspection_text or len(inspection_text) < 100:
        return jsonify({'error': 'Inspection report text is required (minimum 100 characters).'}), 400
    if not property_address:
        return jsonify({'error': 'Property address is required.'}), 400
    if not buyer_name:
        return jsonify({'error': 'Client name is required.'}), 400
    if not buyer_email or '@' not in buyer_email:
        return jsonify({'error': 'Client email is required — they need it to receive their report.'}), 400
    if not property_price or float(str(property_price).replace(',','') or 0) < 10000:
        return jsonify({'error': 'Property list price is required for offer calculations.'}), 400

    # Truncate very large PDFs — keep first 80K chars (sufficient for analysis, avoids token overflow)
    MAX_TEXT = 80_000
    if len(inspection_text) > MAX_TEXT:
        inspection_text = inspection_text[:MAX_TEXT]
        logging.info(f"Inspector analysis: truncated inspection_text to {MAX_TEXT} chars")

    # Run the analysis using the same AI engine
    try:
        from offerwise_intelligence import OfferWiseIntelligence
        from risk_scoring_model import BuyerProfile
        intel = OfferWiseIntelligence()

        buyer_profile_obj = BuyerProfile(
            max_budget=int(float(property_price)) if property_price else 1000000,
            repair_tolerance='moderate',
            ownership_duration='5-10',
            biggest_regret='hidden_issues',
            replaceability='somewhat_unique',
            deal_breakers=['foundation', 'mold', 'electrical'],
        )

        result = intel.analyze_property(
            seller_disclosure_text=disclosure_text or '',
            inspection_report_text=inspection_text,
            property_price=int(float(property_price)) if property_price else 0,
            property_address=property_address,
            buyer_profile=buyer_profile_obj,
        )
    except Exception as e:
        logging.error(f"Inspector analysis engine failed: {e}", exc_info=True)
        return jsonify({'error': f'Analysis engine error: {type(e).__name__}. Please try again.'}), 500

    # Convert PropertyAnalysis dataclass → JSON-serializable dict
    # Uses the exact same pattern as analysis_routes.py
    try:
        from dataclasses import asdict
        import datetime as _dt
        from enum import Enum
        import numpy as _np

        def convert_value(obj):
            if isinstance(obj, (_dt.datetime, _dt.date)):
                return obj.isoformat()
            elif isinstance(obj, Enum):
                return obj.value
            elif isinstance(obj, _np.ndarray):
                return obj.tolist()
            elif hasattr(obj, 'to_dict') and callable(obj.to_dict):
                return obj.to_dict()
            elif hasattr(obj, '__dataclass_fields__'):
                return asdict(obj, dict_factory=dict_factory)
            return obj

        def dict_factory(fields):
            return {k: convert_value(v) for k, v in fields}

        result_dict = asdict(result, dict_factory=dict_factory)

        # Second pass: clean any remaining non-serializable objects
        def clean_dict(obj):
            if isinstance(obj, _np.ndarray):
                return obj.tolist()
            elif isinstance(obj, dict):
                return {k: clean_dict(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean_dict(item) for item in obj]
            elif isinstance(obj, Enum):
                return obj.value
            elif isinstance(obj, (_dt.datetime, _dt.date)):
                return obj.isoformat()
            elif hasattr(obj, '__dataclass_fields__'):
                return {k: clean_dict(v) for k, v in vars(obj).items()}
            elif hasattr(obj, 'to_dict') and callable(obj.to_dict):
                return clean_dict(obj.to_dict())
            return obj

        result_dict = clean_dict(result_dict)
        result_json = _json.dumps(result_dict)

    except Exception as e:
        logging.error(f"Inspector analysis serialization failed: {e}", exc_info=True)
        return jsonify({'error': f'Serialization error: {type(e).__name__}. Please try again.'}), 500

    # Create InspectorReport with share token
    token = secrets.token_urlsafe(16)
    report = InspectorReport(
        inspector_id             = insp.id,
        inspector_user_id        = current_user.id,
        property_address         = property_address,
        property_price           = float(property_price) if property_price else 0,
        buyer_name               = buyer_name,
        buyer_email              = buyer_email,
        inspection_text          = inspection_text,
        analysis_json            = result_json,
        share_token              = token,
        inspector_name_on_report = current_user.name or insp.business_name,
        inspector_biz_on_report  = insp.business_name,
    )
    db.session.add(report)

    # Update inspector stats
    insp.monthly_used  += 1
    insp.total_reports += 1
    db.session.commit()

    # Auto-watch: create PropertyWatch for the BUYER, linked to this inspector report
    if buyer_email:
        try:
            from models import PropertyWatch, User
            buyer_user = User.query.filter_by(email=buyer_email.strip().lower()).first()
            if buyer_user:
                _rj2 = json.loads(result_json) if result_json else {}
                _rp2 = _rj2.get('research_profile', {}) or {}
                _existing_w = PropertyWatch.query.filter_by(
                    user_id=buyer_user.id, address=property_address, is_active=True
                ).first()
                if not _existing_w:
                    _w2 = PropertyWatch(
                        user_id             = buyer_user.id,
                        address             = property_address,
                        asking_price        = float(property_price) if property_price else None,
                        latitude            = _rp2.get('latitude'),
                        longitude           = _rp2.get('longitude'),
                        avm_at_analysis     = _rp2.get('avm_price'),
                        inspector_report_id = report.id,
                        expires_at          = datetime.utcnow() + timedelta(days=45),
                    )
                    db.session.add(_w2)
                    db.session.commit()
                    logging.info(f"🔭 Buyer watch created from inspector report: {property_address}")
            else:
                # Buyer has no account yet — create ghost watch owned by inspector
                # so inspector still gets market alerts; watch re-links when buyer signs up
                _ghost_existing = PropertyWatch.query.filter_by(
                    inspector_report_id=report.id, is_active=True
                ).first()
                if not _ghost_existing and property_address:
                    _ghost = PropertyWatch(
                        user_id               = current_user.id,  # inspector's user_id
                        address               = property_address,
                        asking_price          = float(property_price) if property_price else None,
                        inspector_report_id   = report.id,
                        ghost_buyer_email     = buyer_email.strip().lower() if buyer_email else None,
                        owned_by_professional = True,
                        expires_at            = datetime.utcnow() + timedelta(days=45),
                    )
                    db.session.add(_ghost)
                    db.session.commit()
                    logging.info(f"🔭 Ghost watch created for inspector (buyer not yet registered): {property_address}")
        except Exception as _iw:
            logging.warning(f"Inspector buyer-watch creation failed (non-fatal): {_iw}")

    share_url = f"https://{request.host}/inspector-report/{token}"

    # ── ML Training Data Collection (fire-and-forget) ──
    try:
        from ml_data_collector import collect_training_data
        collect_training_data(
            analysis_id=report.id,
            result_dict=result_dict,
            property_address=property_address,
            property_price=float(property_price) if property_price else 0,
        )
    except Exception as ml_err:
        logging.warning(f"ML data collection skipped: {ml_err}")

    # Extract findings for inspector validation UI
    parsed_findings = []
    for f in result_dict.get('findings', []):
        text = (f.get('description') or f.get('text') or '').strip()
        cat = (f.get('category') or '').strip()
        sev = (f.get('severity') or '').strip()
        if text and cat and sev and sev.lower() not in ('none', 'informational'):
            parsed_findings.append({
                'text': text[:300],
                'category': cat,
                'severity': sev,
            })

    logging.info(f"✅ Inspector report created: {token} for {property_address}")
    return jsonify({
        'success': True,
        'report_id': report.id,
        'share_token': token,
        'share_url': share_url,
        'property_address': property_address,
        'findings_for_review': parsed_findings[:20],
    })


@inspector_bp.route('/api/inspector/validate-findings', methods=['POST'])
@_login_req_dec
def inspector_validate_findings():
    """Inspector confirms/corrects AI-parsed findings. Gold-standard ML training data."""
    from models import MLFindingLabel

    data = request.get_json(silent=True) or {}
    report_id = data.get('report_id')
    validations = data.get('validations', [])

    if not report_id or not validations:
        return jsonify({'error': 'report_id and validations required'}), 400

    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Not registered'}), 403

    report = InspectorReport.query.filter_by(id=report_id, inspector_id=insp.id).first()
    if not report:
        return jsonify({'error': 'Report not found'}), 404

    import re
    zip_code = ''
    m = re.search(r'\b(\d{5})\b', report.property_address or '')
    if m:
        zip_code = m.group(1)

    saved = 0
    for v in validations:
        text = (v.get('text') or '').strip()
        verdict = v.get('verdict', '')  # 'confirmed', 'corrected', 'rejected'
        if not text or not verdict:
            continue

        if verdict == 'confirmed':
            label = MLFindingLabel(
                finding_text=text[:2000],
                category=v.get('category', ''),
                severity=v.get('severity', ''),
                source='inspector_confirmed',
                confidence=0.98,
                is_validated=True,
                report_id=report_id,
                property_zip=zip_code,
                property_price=report.property_price,
            )
        elif verdict == 'corrected':
            label = MLFindingLabel(
                finding_text=text[:2000],
                category=v.get('new_category', v.get('category', '')),
                severity=v.get('new_severity', v.get('severity', '')),
                source='inspector_corrected',
                confidence=0.99,
                is_validated=True,
                report_id=report_id,
                property_zip=zip_code,
                property_price=report.property_price,
                original_category=v.get('category', ''),
                original_severity=v.get('severity', ''),
            )
        elif verdict == 'rejected':
            label = MLFindingLabel(
                finding_text=text[:2000],
                category=v.get('category', ''),
                severity='none',
                source='inspector_rejected',
                confidence=0.99,
                is_validated=True,
                report_id=report_id,
                property_zip=zip_code,
                property_price=report.property_price,
                original_category=v.get('category', ''),
                original_severity=v.get('severity', ''),
            )
        else:
            continue

        db.session.add(label)
        saved += 1

    if saved:
        db.session.commit()
        logging.info(f"ML: inspector {insp.id} validated {saved} findings for report {report_id}")

    return jsonify({'saved': saved})


@inspector_bp.route('/api/inspector/reports', methods=['GET'])
@_login_req_dec
def inspector_reports_list():
    """List all reports for this inspector."""
    import json as _json
    insp = Inspector.query.filter_by(user_id=current_user.id).first()
    if not insp:
        return jsonify({'error': 'Not registered'}), 403
    reports = InspectorReport.query.filter_by(inspector_id=insp.id)\
                .order_by(InspectorReport.created_at.desc()).limit(50).all()
    out = []
    for r in reports:
        result = {}
        try:
            result = _json.loads(r.analysis_json) if r.analysis_json else {}
        except Exception:
            pass
        risk_dna = result.get('risk_dna', {})
        composite = risk_dna.get('composite_score', 0)
        offer_score = round(100 - composite)
        out.append({
            'id': r.id,
            'created_at': r.created_at.isoformat(),
            'property_address': r.property_address,
            'property_price': r.property_price,
            'buyer_name': r.buyer_name,
            'buyer_email': r.buyer_email,
            'share_token': r.share_token,
            'share_url': f"https://{request.host}/inspector-report/{r.share_token}",
            'share_token': r.share_token,
            'has_text': bool(r.inspection_text),
            'offer_score': offer_score,
            'view_count': r.view_count,
            'buyer_viewed': bool(r.buyer_viewed_at),
            'buyer_registered': r.buyer_registered,
            'buyer_converted': r.buyer_converted,
        })
    return jsonify({'reports': out, 'total': len(out)})


@inspector_bp.route('/inspector-report/<token>')
def inspector_report_page(token):
    """Serve the buyer-facing report viewer page."""
    from flask import send_from_directory
    return send_from_directory('static', 'inspector-report.html')


@inspector_bp.route('/api/inspector-report/<token>', methods=['GET'])
def inspector_report_data(token):
    """Return report JSON for the buyer-facing page."""
    import json as _json
    report = InspectorReport.query.filter_by(share_token=token).first_or_404()
    result = {}
    try:
        result = _json.loads(report.analysis_json) if report.analysis_json else {}
    except Exception:
        pass
    return jsonify({
        'property_address': report.property_address,
        'property_price': report.property_price,
        'buyer_name': report.buyer_name,
        'inspector_name': report.inspector_name_on_report or None,
        'inspector_biz': report.inspector_biz_on_report or None,
        'created_at': report.created_at.isoformat(),
        'inspection_text': report.inspection_text or '',
        'result': result,
    })



@inspector_bp.route('/api/inspector-report/<token>/update', methods=['PATCH'])
@_login_req_dec
def inspector_report_update(token):
    """Allow the inspector who owns this report to update inspection_text and branding."""
    report = InspectorReport.query.filter_by(share_token=token).first_or_404()
    # Only the inspector who created it can update it
    if report.inspector_user_id != current_user.id and not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    if 'inspection_text' in data:
        report.inspection_text = data['inspection_text']
    if 'inspector_name' in data:
        report.inspector_name_on_report = data['inspector_name']
    if 'inspector_biz' in data:
        report.inspector_biz_on_report = data['inspector_biz']
    db.session.commit()
    return jsonify({'success': True})



@inspector_bp.route('/api/inspector/forward-to-realtor', methods=['POST'])
@_login_req_dec
def inspector_forward_to_realtor():
    """
    P2.6 — Inspector sends a completed report to a realtor with one click.
    Creates an AgentShare, emails the realtor with analysis summary + OfferWatch
    pre-wired, and returns the share URL for inspector confirmation.
    """
    data = request.get_json() or {}
    report_token = data.get('report_token', '').strip()
    realtor_email = data.get('realtor_email', '').strip().lower()
    realtor_name  = data.get('realtor_name', '').strip()
    message       = data.get('message', '').strip()

    if not report_token:
        return jsonify({'success': False, 'error': 'report_token is required.'}), 400
    if not realtor_email or '@' not in realtor_email:
        return jsonify({'success': False, 'error': 'A valid realtor email is required.'}), 400

    report = InspectorReport.query.filter_by(share_token=report_token).first()
    if not report:
        return jsonify({'success': False, 'error': 'Report not found.'}), 404
    if report.inspector_user_id != current_user.id and not _is_admin():
        return jsonify({'success': False, 'error': 'Unauthorized.'}), 403

    try:
        from agentic_monitor import forward_report_to_realtor
        result = forward_report_to_realtor(
            inspector_report_id=report.id,
            realtor_email=realtor_email,
            realtor_name=realtor_name or None,
            message=message or None,
        )
        if result and result.get('success'):
            logging.info(f"✅ [P2.6] Inspector {current_user.id} → realtor {realtor_email}, token {result.get('share_token')}")
            return jsonify({'success': True, 'share_url': result.get('share_url'), 'share_token': result.get('share_token')})
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Send failed.')}), 500
    except Exception as e:
        logging.error(f"[P2.6] forward_to_realtor error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal error. Please try again.'}), 500



@inspector_bp.route('/api/inspector/internachi-verify', methods=['POST'])
@_login_req_dec
def inspector_internachi_verify():
    """Verify an InterNACHI member number and upgrade inspector to internachi plan."""
    from models import Inspector
    data = request.get_json() or {}
    member_id = (data.get('member_id') or '').strip()
    if not member_id or len(member_id) < 3:
        return jsonify({'error': 'Please enter your InterNACHI member number.'}), 400

    inspector = Inspector.query.filter_by(user_id=current_user.id).first()
    if not inspector:
        return jsonify({'error': 'Inspector profile not found.'}), 404

    # Check member_id not already claimed by another inspector
    existing = Inspector.query.filter_by(internachi_member_id=member_id).first()
    if existing and existing.id != inspector.id:
        return jsonify({'error': 'That member number is already linked to another account.'}), 400

    inspector.internachi_member_id = member_id
    inspector.internachi_verified = True
    # Set plan to internachi — 3 free analyses/month forever
    inspector.plan = 'internachi'
    inspector.monthly_quota = 3
    db.session.commit()

    logging.info(f"✅ Inspector {inspector.id} verified as InterNACHI member {member_id}")
    return jsonify({'success': True, 'plan': 'internachi', 'monthly_quota': 3})


@inspector_bp.route('/inspector-onboarding')
@_login_req_dec
def inspector_onboarding():
    """4-step inspector setup wizard — shown on first visit to inspector portal."""
    return send_from_directory('static', 'inspector-onboarding.html')


@inspector_bp.route('/inspector-portal')
@_login_req_dec
def inspector_portal():
    return send_from_directory('static', 'inspector-portal.html')


@inspector_bp.route('/internachi')
def internachi_landing():
    """InterNACHI vendor member landing page."""
    from flask import send_from_directory
    return send_from_directory('static', 'internachi.html')


@inspector_bp.route('/for-inspectors')
def for_inspectors_landing():
    return send_from_directory('static', 'for-inspectors.html')


# ── Inspector impact stats — flywheel v5.80.86 ───────────────────────────────

@inspector_bp.route('/api/inspector/impact', methods=['GET'])
@_login_req_dec
def inspector_impact_stats():
    """
    Returns monthly impact stats for the inspector portal dashboard:
    findings used, buyer savings, analyses this month, and per-report breakdown.
    """
    from models import Inspector, InspectorReport, Analysis
    import json as _json

    inspector = Inspector.query.filter_by(user_id=current_user.id).first()
    if not inspector:
        return jsonify({'error': 'Not an inspector'}), 403

    from flywheel_notifications import get_inspector_impact_stats
    stats = get_inspector_impact_stats(inspector.id)

    # Per-report breakdown for the portal list
    from datetime import timedelta
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    reports = InspectorReport.query.filter(
        InspectorReport.inspector_id == inspector.id
    ).order_by(InspectorReport.created_at.desc()).limit(20).all()

    report_rows = []
    for r in reports:
        linked = Analysis.query.filter_by(inspector_report_id=r.id).all()
        savings = 0
        findings_used = 0
        for a in linked:
            try:
                rd = _json.loads(a.result_json or '{}')
                asking = rd.get('property_price', 0) or 0
                rec = rd.get('offer_strategy', {}).get('recommended_offer', asking) or asking
                savings += max(0, asking - rec)
                findings_used += len(rd.get('risk_score', {}).get('deal_breakers', []))
                findings_used += len(rd.get('cross_reference', {}).get('contradictions', []))
            except Exception:
                pass
        report_rows.append({
            'id': r.id,
            'property_address': r.property_address or '',
            'created_at': r.created_at.isoformat() if r.created_at else '',
            'analyses_run': len(linked),
            'findings_used': findings_used,
            'savings': savings,
            'share_token': r.share_token or '',
            'buyer_name': r.buyer_name or '',
        })

    return jsonify({
        **stats,
        'reports': report_rows,
    })


@inspector_bp.route('/api/inspector/label-cost', methods=['POST'])
@_login_req_dec
def inspector_label_cost():
    """Inspector submits a real-world cost they've seen for a repair type.
    Feeds the Repair Cost Predictor training.
    """
    import hashlib, json as _json
    from models import MLCostData

    data = request.get_json(silent=True) or {}
    finding_text = (data.get('finding_text') or '').strip()
    category = (data.get('category') or '').strip().lower()
    severity = (data.get('severity') or '').strip().lower()
    cost_low = data.get('cost_low')
    cost_high = data.get('cost_high')
    zip_code = (data.get('zip_code') or '').strip()[:10]

    if not finding_text or len(finding_text) < 10:
        return jsonify({'error': 'finding_text required (10+ chars)'}), 400

    try:
        cost_low = float(cost_low) if cost_low else 0
        cost_high = float(cost_high) if cost_high else 0
    except (ValueError, TypeError):
        return jsonify({'error': 'cost_low/cost_high must be numbers'}), 400

    if cost_low <= 0 or cost_high <= 0 or cost_high < cost_low:
        return jsonify({'error': 'invalid cost range'}), 400

    cost_mid = (cost_low + cost_high) / 2

    # Inspector ID for attribution (anonymized)
    inspector_id = current_user.id if hasattr(current_user, 'id') else 0

    h = hashlib.sha256(
        (finding_text[:200] + '|inspector_' + str(inspector_id) + '|' + str(int(cost_mid))).encode()
    ).hexdigest()

    existing = MLCostData.query.filter_by(content_hash=h).first()
    if existing:
        return jsonify({'status': 'duplicate', 'id': existing.id})

    entry = MLCostData(
        finding_text=finding_text[:500],
        category=category or 'general',
        severity=severity or 'moderate',
        cost_low=cost_low,
        cost_high=cost_high,
        cost_mid=cost_mid,
        zip_code=zip_code,
        source='inspector_label',
        source_meta=_json.dumps({'inspector_id': inspector_id}),
        content_hash=h,
    )
    db.session.add(entry)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'DB error: {e}'}), 500

    return jsonify({'status': 'success', 'id': entry.id, 'cost_mid': cost_mid})
