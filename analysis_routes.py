"""
OfferWise Analysis Routes Blueprint
Extracted from app.py v5.74.47 for architecture cleanup.
"""

import os
import json
import logging
import base64
from datetime import datetime
from flask import Blueprint, request, jsonify, url_for
from flask_login import current_user
from models import db, User, Property, Document, Analysis, ConsentRecord
from blueprint_helpers import DeferredDecorator, make_deferred_limiter
from security import validate_origin
from risk_scoring_model import BuyerProfile
from analysis_cache import AnalysisCache
from confidence_scorer import ConfidenceScorer
from property_research_agent import PropertyResearchAgent
from validation import validate_analysis_output, ValidationError
from email_service import send_analysis_complete

logger = logging.getLogger(__name__)

analysis_bp = Blueprint('analysis', __name__)

def detect_and_flag_special_properties(result_dict, disclosure_text, inspection_text):
    from app import detect_and_flag_special_properties as _fn
    return _fn(result_dict, disclosure_text, inspection_text)

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


# App-level singletons injected by init
_app_refs = {}

def init_analysis_blueprint(app, admin_required_fn, api_admin_required_fn,
                       api_login_required_fn, dev_only_gate_fn, limiter, **extras):
    _admin_required_ref[0] = admin_required_fn
    _api_admin_required_ref[0] = api_admin_required_fn
    _api_login_required_ref[0] = api_login_required_fn
    _dev_only_gate_ref[0] = dev_only_gate_fn
    _limiter_ref[0] = limiter
    # Store app-level singletons for use by route handlers
    _app_refs.update(extras)
    app.register_blueprint(analysis_bp)
    logger.info("✅ Analysis Routes blueprint registered")


def _get(key):
    """Get an app-level singleton injected at init time."""
    return _app_refs.get(key)




# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT EVIDENCE API - REMOVED (v5.51.2)
# ═══════════════════════════════════════════════════════════════════════════════
# These server-side endpoints were removed to maintain our privacy promise:
# "PDFs are parsed directly in your browser - we never receive, store, or 
#  have access to your PDF files."
#
# Screenshots are now rendered CLIENT-SIDE using PDF.js in the browser.
# The PDF never leaves the user's device.
# ═══════════════════════════════════════════════════════════════════════════════

@analysis_bp.route('/api/upload-pdf', methods=['POST', 'OPTIONS'])
@_api_login_required  # Use API-friendly decorator
@_limiter.limit("30 per hour")  # SECURITY: Max 30 uploads per hour per user
def upload_pdf():
    """Upload PDF and queue for async processing"""
    if request.method == 'OPTIONS':
        return jsonify({'success': True})
    
    try:
        logger.info("📤 PDF upload started (async mode)")
        data = request.get_json()
        pdf_base64 = data.get('pdf_base64', '')
        filename = data.get('filename', 'document.pdf')
        
        # Remove data URL prefix if present
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        # SECURITY: Validate size BEFORE decoding
        if len(pdf_base64) > 20_971_520:  # 20MB base64 = ~15MB actual
            return jsonify({'error': 'File too large (max 15MB)'}), 413
        
        # Decode PDF
        logger.info(f"Decoding PDF (base64 length: {len(pdf_base64)})")
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
        except Exception as e:
            logger.error(f"Base64 decode failed: {e}")
            return jsonify({'error': 'Invalid file encoding'}), 400
        
        logger.info(f"PDF decoded: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024/1024:.2f} MB)")
        
        # SECURITY: Validate it's actually a PDF (check magic bytes)
        if not pdf_bytes.startswith(b'%PDF-'):
            logger.error("File is not a valid PDF (wrong magic bytes)")
            return jsonify({'error': 'Invalid PDF file'}), 400
        
        # SECURITY: Validate size after decoding too
        if len(pdf_bytes) > 15_728_640:  # 15MB
            return jsonify({'error': 'File too large (max 15MB)'}), 413
        
        # Create job
        job_id = _get('job_manager').create_job(
            user_id=current_user.id,
            filename=filename,
            pdf_bytes=pdf_bytes
        )
        
        logger.info(f"✅ Job {job_id} created for user {current_user.id}: {filename}")
        
        # Queue for async processing
        _get('pdf_worker').process_pdf_async(job_id)
        
        # Return immediately!
        # CRITICAL: Don't include page_count at all until processing completes
        return jsonify({
            'success': True,
            'job_id': job_id,
            'status': 'processing',
            'message': 'Upload complete! Processing document...',
            'poll_url': f'/api/jobs/{job_id}',
            'async': True,
            'processing': True
            # NO page_count field at all!
        })
        
    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        return jsonify({'error': 'Upload failed', 'message': 'An internal error occurred. Please try again.'}), 500



@analysis_bp.route('/api/jobs/<job_id>', methods=['GET'])
@_api_login_required  # Use API-friendly decorator
@_limiter.limit("100 per minute")  # Allow frequent polling
def get_job_status(job_id):
    """Get status of PDF processing job"""
    try:
        job = _get('job_manager').get_job(job_id)
        
        if not job:
            return jsonify({'error': 'Job not found', 'status': 'error'}), 404
        
        # SECURITY: Only owner can check job status
        if job.user_id != current_user.id:
            logger.warning(f"🚫 User {current_user.id} tried to access job {job_id} owned by user {job.user_id}")
            return jsonify({'error': 'Unauthorized', 'status': 'error'}), 403
        
        # Check if job is taking too long (> 10 minutes = 600 seconds)
        if job.status == 'processing':
            from datetime import datetime
            elapsed = (datetime.now() - job.created_at).total_seconds()
            if elapsed > 600:
                logger.error(f"⏰ Job {job_id} has been processing for {elapsed:.0f}s - marking as failed")
                job.status = 'failed'
                job.error = 'Processing timeout - job took longer than 10 minutes'
                _get('job_manager').update_job(job_id, status='failed', error=job.error)
        
        # Return job status as dict
        return jsonify(job.to_dict())
        
    except Exception as e:
        logger.error(f"Error getting job status for {job_id}: {e}", exc_info=True)
        # Always return JSON, even on error
        return jsonify({
            'error': 'Failed to get job status',
            'status': 'error',
            'message': 'An internal error occurred. Please try again.'
        }), 500



@analysis_bp.route('/api/analyze', methods=['POST'])
@_api_login_required  # Use API-friendly decorator
@validate_origin  # SECURITY: CSRF protection
@_limiter.limit("20 per hour")  # SECURITY: Max 20 analyses per hour per user
def analyze_property():
    """Analyze a property (protected endpoint)"""
    
    # Track analysis start
    try:
        from funnel_tracker import track_from_request
        track_from_request('analysis_started', request, user_id=current_user.id)
    except Exception:
        pass
    
    logging.info(f"🎯 Analysis request from {current_user.email} (credits: {current_user.analysis_credits})")
    logging.info("")
    
    # Check credits (pay-per-use system) with FREE TIER bypass (v5.59.34)
    logging.info("🔍 CHECKING CREDITS...")
    logging.info(f"   Current credits: {current_user.analysis_credits}")
    
    # Determine if this is a free-tier user (never paid)
    # Uses global _get('DEVELOPER_EMAILS')
    dev_emails = _get('DEVELOPER_EMAILS')
    is_developer = current_user.email.lower() in dev_emails
    has_paid = bool(current_user.stripe_customer_id) or is_developer or (current_user.analysis_credits >= 100)
    is_free_tier_user = not has_paid
    
    if current_user.analysis_credits <= 0:
        if is_free_tier_user:
            # FREE TIER with no credits: Block — must purchase
            logging.warning(f"🚫 FREE TIER BLOCKED: {current_user.email} has 0 credits, never paid")
            return jsonify({
                'error': 'No analysis credits',
                'message': 'Your free analysis has been used. Purchase credits to analyze more properties.',
                'credits_remaining': 0,
                'upgrade_url': url_for('pricing')
            }), 403
        else:
            # PAID USER with no credits: Block and redirect to pricing
            logging.warning("❌ CREDIT CHECK FAILED - Paid user with no credits remaining")
            logging.warning(f"   User {current_user.email} has {current_user.analysis_credits} credits")
            return jsonify({
                'error': 'No analysis credits',
                'message': 'You have no analysis credits remaining. Please purchase more credits to continue.',
                'credits_remaining': 0,
                'upgrade_url': url_for('pricing')
            }), 403
    
    logging.info(f"✅ CREDIT CHECK PASSED - User has {current_user.analysis_credits} credits (free_tier={is_free_tier_user})")
    logging.info("")
    
    # 🛡️ LEGAL PROTECTION: Verify user has consented to analysis disclaimer
    # NOTE: Consent is now collected in Settings/Onboarding, not here
    has_consent = ConsentRecord.has_current_consent(
        user_id=current_user.id,
        consent_type='analysis_disclaimer',
        required_version=_get('ANALYSIS_DISCLAIMER_VERSION')
    )
    
    if not has_consent:
        logging.warning(f"⚖️ User {current_user.id} analyzing without explicit consent - will prompt in settings")
        # Don't block - user will be prompted to consent in settings
        # We record the analysis but flag that consent should be obtained
    else:
        logging.info(f"✅ User {current_user.id} has valid consent for analysis")
    
    try:
        data = request.get_json()
        
        # Log incoming analysis request
        logging.info(f"📊 Analysis request - Address: {data.get('property_address', 'N/A')[:50]}")
        
        # NEW: Check if job_id provided (async upload)
        job_id = data.get('job_id')
        if job_id:
            logging.info(f"📋 Analyze called with job_id: {job_id}")
        
        # Extract data - accept both text and PDF formats
        property_address = data.get('property_address', '')
        
        # Robust price handling - accept both string and number
        raw_price = data.get('property_price', 0)
        try:
            if raw_price:
                property_price = int(float(raw_price))  # Handle both string "925000" and number 925000
                if property_price <= 0 or property_price > 100000000:
                    logging.warning(f"Invalid property price: {property_price}")
                    return jsonify({'error': 'Property price must be between $1 and $100M'}), 400
                logging.info(f"Property price parsed: ${property_price:,}")
            else:
                property_price = 0
                logging.warning("No property price provided")
                return jsonify({'error': 'Property price is required. Please provide a valid asking price.'}), 400
        except (ValueError, TypeError) as e:
            logging.error(f"Price parsing error: {e}, raw_price={raw_price}")
            return jsonify({'error': 'Invalid property price format'}), 400
        
        # Accept text format (from upload endpoint)
        seller_disclosure_text = (data.get('seller_disclosure_text', '') or '').replace('\x00', '')
        inspection_report_text = (data.get('inspection_report_text', '') or '').replace('\x00', '')
        
        # NEW: If job_id provided, get text from completed job
        if job_id and (not seller_disclosure_text or not inspection_report_text):
            job = _get('job_manager').get_job(job_id)
            
            if not job:
                return jsonify({'error': 'Job not found', 'message': 'Upload job has expired or does not exist'}), 404
            
            # SECURITY: Verify job ownership
            if job.user_id != current_user.id:
                logging.warning(f"🚫 User {current_user.id} tried to analyze job {job_id} owned by {job.user_id}")
                return jsonify({'error': 'Unauthorized'}), 403
            
            # Check job status
            if job.status == 'failed':
                return jsonify({
                    'error': 'Document processing failed',
                    'message': job.error or 'Failed to process uploaded document'
                }), 400
            
            if job.status in ['queued', 'processing']:
                # Job still processing - return special status
                return jsonify({
                    'error': 'Document still processing',
                    'message': f'Please wait... {job.message}',
                    'status': job.status,
                    'progress': job.progress,
                    'total': job.total,
                    'job_id': job_id,
                    'retry_after': 2  # Seconds to wait before retrying
                }), 202  # 202 Accepted (processing)
            
            if job.status == 'complete' and job.result:
                # Use text from completed job
                document_text = job.result.get('text', '')
                
                # Determine which document type this is based on request
                doc_type = data.get('document_type', 'inspection')  # Default to inspection
                
                if doc_type == 'disclosure':
                    seller_disclosure_text = document_text
                    logging.info(f"✅ Using disclosure text from job {job_id} ({len(document_text)} chars)")
                else:
                    inspection_report_text = document_text
                    logging.info(f"✅ Using inspection text from job {job_id} ({len(document_text)} chars)")
            else:
                return jsonify({
                    'error': 'Job incomplete',
                    'message': 'Document processing has not completed successfully'
                }), 400
        
        # Also accept PDF format (legacy)
        disclosure_pdf = data.get('disclosure_pdf', '')
        inspection_pdf = data.get('inspection_pdf', '')
        
        buyer_profile_data = data.get('buyer_profile', {})
        
        # If PDFs provided, extract text (with vision fallback for scanned/DocuSign PDFs)
        from pdf_handler import is_meaningful_extraction, extract_text_via_vision
        
        if disclosure_pdf and not seller_disclosure_text:
            if ',' in disclosure_pdf:
                disclosure_pdf = disclosure_pdf.split(',')[1]
            pdf_bytes = base64.b64decode(disclosure_pdf)
            
            # For seller disclosures: ALWAYS use vision extraction
            # Reason: TDS forms have handwritten answers, checked boxes, and annotations
            # that are the MOST IMPORTANT content. Text extractors get printed form labels
            # but miss handwritten entries. Vision reads the actual page images.
            logging.info("📄 Disclosure PDF: Using vision extraction for handwriting accuracy")
            vision_result = extract_text_via_vision(disclosure_pdf, document_type='seller_disclosure')
            if vision_result and vision_result.get('text'):
                seller_disclosure_text = vision_result['text']
                logging.info(f"✅ Vision extraction for disclosure: {len(seller_disclosure_text)} chars")
            else:
                # Fallback to text extraction if vision fails
                logging.warning("⚠️ Vision extraction failed for disclosure, falling back to text extraction")
                result = _get('pdf_handler').extract_text_from_bytes(pdf_bytes)
                seller_disclosure_text = result.get('text', '') if isinstance(result, dict) else result
        
        if inspection_pdf and not inspection_report_text:
            if ',' in inspection_pdf:
                inspection_pdf = inspection_pdf.split(',')[1]
            pdf_bytes = base64.b64decode(inspection_pdf)
            
            # For inspection reports: try text extraction first (usually typed/digital)
            # Fall back to vision if quality is poor
            result = _get('pdf_handler').extract_text_from_bytes(pdf_bytes)
            inspection_report_text = result.get('text', '') if isinstance(result, dict) else result
            
            # Quality check - fall back to vision if extraction is garbage
            is_good, reason = is_meaningful_extraction(inspection_report_text, result.get('page_count', 1) if isinstance(result, dict) else 1)
            if not is_good:
                logging.warning(f"Inspection text extraction poor ({reason}), trying vision fallback")
                vision_result = extract_text_via_vision(inspection_pdf, document_type='inspection_report')
                if vision_result and vision_result.get('text'):
                    inspection_report_text = vision_result['text']
                    logging.info(f"Vision fallback succeeded for inspection: {len(inspection_report_text)} chars")
        
        # Documents are optional — analysis depth scales with what is provided.
        # State 1: address only → market + environmental + permit analysis
        # State 2: address + disclosure → adds Transparency Report + inspector checklist
        # State 3: address + both documents → full offer strategy (existing behaviour)
        has_disclosure = bool(seller_disclosure_text and seller_disclosure_text.strip())
        has_inspection = bool(inspection_report_text and inspection_report_text.strip())
        analysis_depth = 'full' if (has_disclosure and has_inspection) else 'disclosure_only' if has_disclosure else 'address_only'
        logging.info(f"📊 Analysis depth: {analysis_depth} (disclosure={has_disclosure}, inspection={has_inspection})")

        # Create property record
        property = Property(
            user_id=current_user.id,
            address=property_address,
            price=property_price or buyer_profile_data.get('max_budget'),
            status='pending'
        )
        db.session.add(property)
        db.session.flush()  # Get property ID
        
        # ═══════════════════════════════════════════════════════════════
        # PRIVACY-FIRST ARCHITECTURE:
        # PDFs are parsed client-side in user's browser
        # Only extracted text is sent to server (NOT the PDF files!)
        # We NEVER save document files to disk
        # ═══════════════════════════════════════════════════════════════
        
        logging.info("🔒 PRIVACY MODE: Text received from client-side parsing")
        logging.info(f"📄 Disclosure text: {len(seller_disclosure_text)} characters")
        logging.info(f"📄 Inspection text: {len(inspection_report_text)} characters")
        logging.info("✅ NO FILES SAVED - True privacy architecture!")
        
        # Create document records for metadata only
        # NOTE: file_path is required by DB but file doesn't exist - using placeholder
        disclosure_doc = Document(
            property_id=property.id,
            document_type='seller_disclosure',
            filename='parsed_in_browser.txt',
            file_path='CLIENT_SIDE_PARSED',  # Placeholder - file was parsed in browser, never uploaded
            file_size_bytes=len(seller_disclosure_text.encode('utf-8'))
            # NO extracted_text - not stored in DB for privacy!
        )
        db.session.add(disclosure_doc)
        
        inspection_doc = Document(
            property_id=property.id,
            document_type='inspection_report',
            filename='parsed_in_browser.txt',
            file_path='CLIENT_SIDE_PARSED',  # Placeholder - file was parsed in browser, never uploaded
            file_size_bytes=len(inspection_report_text.encode('utf-8'))
            # NO extracted_text - not stored in DB for privacy!
        )
        db.session.add(inspection_doc)
        
        # Run analysis
        buyer_profile = BuyerProfile(
            max_budget=buyer_profile_data.get('max_budget', 0),
            repair_tolerance=buyer_profile_data.get('repair_tolerance', 'moderate'),
            ownership_duration=buyer_profile_data.get('ownership_duration', '3-7'),
            biggest_regret=buyer_profile_data.get('biggest_regret', ''),
            replaceability=buyer_profile_data.get('replaceability', 'somewhat_unique'),
            deal_breakers=buyer_profile_data.get('deal_breakers', [])
        )
        
        # CRITICAL: Initialize caching and confidence systems
        cache = AnalysisCache()
        confidence_scorer = ConfidenceScorer()
        
        # Generate cache key
        buyer_profile_dict = {
            'max_budget': buyer_profile_data.get('max_budget', 0),
            'repair_tolerance': buyer_profile_data.get('repair_tolerance', 'moderate'),
            'ownership_duration': buyer_profile_data.get('ownership_duration', '3-7'),
            'biggest_regret': buyer_profile_data.get('biggest_regret', ''),
            'replaceability': buyer_profile_data.get('replaceability', 'somewhat_unique'),
            'deal_breakers': buyer_profile_data.get('deal_breakers', [])
        }
        
        cache_key = cache.generate_cache_key(
            inspection_text=inspection_report_text,
            disclosure_text=seller_disclosure_text,
            asking_price=property_price or buyer_profile_data.get('max_budget', 0),
            buyer_profile=buyer_profile_dict
        )
        
        # Try to get cached result
        cached_result = cache.get(cache_key)
        
        if cached_result:
            # Cache hit - instant response
            logging.info(f"✅ Cache HIT - returning cached analysis for {property_address}")
            result_dict = cached_result
            
            # CRITICAL: Validate cached result has property_price (Bug #27 - old cache entries)
            if 'property_price' not in result_dict or result_dict.get('property_price', 0) == 0:
                logging.warning("⚠️ Cached result missing property_price - invalidating cache entry")
                # Invalidate this cache entry and re-run analysis
                cached_result = None
                result_dict = None
            else:
                logging.info(f"✅ Cached result validated with property_price: ${result_dict['property_price']:,}")
        
        if not cached_result:
            # Cache miss OR invalid cache - run full analysis
            logging.info(f"🔄 Cache MISS or invalid - running full analysis for {property_address}")
            
            # Determine price for analysis
            price_to_use = property_price or buyer_profile_data.get('max_budget', 0)
            logging.info(f"💰 Analysis price: ${price_to_use:,}")
            
            # 🤖 MARKET DATA: Fetch RentCast synchronously first (2-3s), then run full research in parallel
            # This guarantees AVM + comps are always available for the offer strategy
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
            research_data = None
            # === MARKET DATA DEBUG ===
            rentcast_key = os.environ.get('RENTCAST_API_KEY', '')
            logging.warning(f"[MARKET] 🔑 RENTCAST_API_KEY: {'SET (' + str(len(rentcast_key)) + ' chars)' if rentcast_key else 'NOT SET'}")
            logging.warning(f"[MARKET] 📍 Address length: {len(property_address)}")

            # ── FAST MARKET FETCH: RentCast only, synchronous, ~2s ──────────────
            _fast_market_data = None
            if property_address and len(property_address) >= 15 and rentcast_key:
                try:
                    import requests as _rreq
                    _zip = None
                    import re as _re2
                    _zm = _re2.search(r'\b(\d{5})\b', property_address)
                    if _zm: _zip = _zm.group(1)
                    
                    # AVM call
                    _avm_resp = _rreq.get(
                        'https://api.rentcast.io/v1/avm/value',
                        params={'address': property_address, 'compCount': 10, 'maxRadius': 5, 'daysOld': 180},
                        headers={'X-Api-Key': rentcast_key, 'Accept': 'application/json'},
                        timeout=8
                    )
                    logging.warning(f"[MARKET] ⚡ Fast RentCast AVM: HTTP {_avm_resp.status_code}")
                    if _avm_resp.status_code == 200:
                        _avm = _avm_resp.json()
                        _comps_raw = _avm.get('comparables', []) or []
                        _comps = []
                        for _comp in _comps_raw:
                            _cprice = int(_comp.get('price', 0) or 0)
                            if _cprice <= 0: continue
                            _csq = int(_comp.get('squareFootage', 0) or 0)
                            _comps.append({
                                'price': _cprice,
                                'sqft': _csq,
                                'price_per_sqft': round(_cprice/_csq, 2) if _csq > 0 else 0,
                                'bedrooms': int(_comp.get('bedrooms', 0) or 0),
                                'bathrooms': float(_comp.get('bathrooms', 0) or 0),
                                'days_on_market': int(_comp.get('daysOnMarket', 0) or 0),
                                'status': _comp.get('status', '') or '',
                                'distance_miles': round(float(_comp.get('distance', 0) or 0), 2),
                            })
                        
                        _ms_data = {}
                        if _zip:
                            try:
                                _ms_resp = _rreq.get(
                                    f'https://api.rentcast.io/v1/markets',
                                    params={'zipCode': _zip, 'historyRange': 1},
                                    headers={'X-Api-Key': rentcast_key, 'Accept': 'application/json'},
                                    timeout=6
                                )
                                if _ms_resp.status_code == 200:
                                    _ms_json = _ms_resp.json()
                                    _sale = _ms_json.get('saleData', _ms_json)
                                    _ms_data = {
                                        'zip_code': _zip,
                                        'average_days_on_market': int(_sale.get('averageDaysOnMarket', 0) or 0),
                                        'median_days_on_market': int(_sale.get('medianDaysOnMarket', 0) or 0),
                                        'total_listings': int(_sale.get('totalListings', 0) or 0),
                                        'new_listings': int(_sale.get('newListings', 0) or 0),
                                        'median_price_per_sqft': float(_sale.get('medianPricePerSquareFoot', 0) or 0),
                                        'average_sale_price': int(_sale.get('averagePrice', 0) or 0),
                                    }
                                    logging.warning(f"[MARKET] ⚡ Fast MarketStats: DOM={_ms_data.get('average_days_on_market')} listings={_ms_data.get('total_listings')}")
                            except Exception as _mse:
                                logging.warning(f"[MARKET] MarketStats fast fetch failed: {_mse}")
                        
                        _fast_market_data = {
                            'tool_results': [
                                {'tool_name': 'rentcast', 'status': 'success', 'data': {
                                    'found': True,
                                    'avm_price': int(_avm.get('price', 0) or 0),
                                    'avm_price_low': int(_avm.get('priceRangeLow', 0) or 0),
                                    'avm_price_high': int(_avm.get('priceRangeHigh', 0) or 0),
                                    'comparables': _comps,
                                }},
                                {'tool_name': 'market_stats', 'status': 'success' if _ms_data else 'skipped', 'data': _ms_data},
                            ],
                            'tools_succeeded': 1 + (1 if _ms_data else 0),
                        }
                        logging.warning(f"[MARKET] ⚡ Fast market fetch: AVM=${_avm.get('price',0):,} comps={len(_comps)}")
                except Exception as _fme:
                    logging.warning(f"[MARKET] Fast market fetch failed: {_fme}")
            if property_address and len(property_address) >= 15:
                def _run_research(addr):
                    try:
                        _ai_client = None
                        try:
                            import anthropic as _anthropic
                            _api_key = os.environ.get('ANTHROPIC_API_KEY')
                            if _api_key:
                                _ai_client = _anthropic.Anthropic(api_key=_api_key)
                        except Exception:
                            pass
                        logging.warning(f"[MARKET] 🤖 Research thread starting: {addr[:50]}")
                        _rc_key = os.environ.get('RENTCAST_API_KEY', '')
                        logging.warning(f"[MARKET] 🔑 Thread RENTCAST: {'SET' if _rc_key else 'MISSING'}")
                        agent = PropertyResearchAgent(ai_client=_ai_client)
                        result = agent.research(addr)
                        logging.warning(f"[MARKET] 🤖 Research done: {result.get('tools_succeeded',0)} ok, {result.get('tools_failed',0)} fail")
                        for tr in (result.get('tool_results') or []):
                            logging.warning(f"[MARKET]   tool={tr.get('tool_name')} status={tr.get('status')} ms={tr.get('duration_ms',0)} err={tr.get('error','')[:60]}")
                        return result
                    except Exception as e:
                        logging.warning(f"🤖 Background research failed: {e}", exc_info=True)
                        return None
                
                _research_executor = ThreadPoolExecutor(max_workers=1)
                research_future = _research_executor.submit(_run_research, property_address)
                logging.info(f"🤖 Research agent started for: {property_address[:50]}")
                
                # Wait for research to complete (needed for AI cross-referencing)
                try:
                    research_data = research_future.result(timeout=25)
                    if research_data:
                        logging.warning(f"[MARKET] 🤖 Research complete: {research_data.get('tools_succeeded', 0)} tools in {research_data.get('research_time_ms', 0)}ms")
                        tool_results = research_data.get('tool_results', [])
                        rc = next((t for t in tool_results if t.get('tool_name') == 'rentcast'), None)
                        ms = next((t for t in tool_results if t.get('tool_name') == 'market_stats'), None)
                        logging.warning(f"[MARKET] 🏠 RentCast: {rc.get('status') if rc else 'not run'} AVM={rc.get('data', {}).get('avm_price', 0) if rc and rc.get('data') else 0}")
                        logging.warning(f"[MARKET] 📊 MarketStats: {ms.get('status') if ms else 'not run'}")
                except FuturesTimeoutError:
                    logging.warning("🤖 Research timed out after 25s — proceeding without")
                except Exception as e:
                    logging.warning(f"🤖 Research failed: {e}")
                finally:
                    _research_executor.shutdown(wait=False)
            
            # ── FEATURE 6: SSE progress — emit real phase updates ──
            import uuid as _uuid
            sse_job_id = _uuid.uuid4().hex
            try:
                from app import set_analysis_progress
                _sp = set_analysis_progress
            except Exception:
                _sp = lambda jid, phase, msg, pct: None

            # Use fast market data if full research timed out or failed
            _effective_research = research_data or _fast_market_data
            if _fast_market_data and research_data:
                _full_tools = {t.get('tool_name') for t in (research_data.get('tool_results') or [])}
                for _ft in (_fast_market_data.get('tool_results') or []):
                    if _ft.get('tool_name') not in _full_tools:
                        research_data['tool_results'].append(_ft)
                _effective_research = research_data

            # ── BRANCH ON ANALYSIS DEPTH ──────────────────────────────────────
            # address_only:     skip intelligence engine entirely — return research data only
            # disclosure_only:  run intelligence with empty inspection string (disclosure analysis)
            # full:             existing behaviour — both documents through full pipeline
            # -----------------------------------------------------------------

            if analysis_depth == 'address_only':
                # No documents — build result dict from research data only.
                # No Anthropic API calls, no document parsing, no offer strategy.
                logging.info("📍 Address-only analysis — skipping intelligence engine")
                _sp(sse_job_id, 'research', 'Querying public records...', 40)
                _sp(sse_job_id, 'complete', 'Analysis complete!', 100)

                # Extract market data from research results
                _rc = None
                _ms = None
                _flood = None
                _env = None
                _walk = None
                _schools = None
                _permits = None
                _eq = None
                _air = None
                if _effective_research:
                    for _t in (_effective_research.get('tool_results') or []):
                        _tn = _t.get('tool_name', '')
                        _td = _t.get('data') or {}
                        if _tn == 'rentcast': _rc = _td
                        elif _tn == 'market_stats': _ms = _td
                        elif _tn == 'flood_zone': _flood = _td
                        elif _tn in ('california_hazards', 'environmental'): _env = _td
                        elif _tn == 'walk_score': _walk = _td
                        elif _tn == 'school_ratings': _schools = _td
                        elif _tn == 'permit_history': _permits = _td
                        elif _tn == 'earthquake_history': _eq = _td
                        elif _tn == 'air_quality': _air = _td

                _avm = int((_rc or {}).get('avm_price', 0))
                _avm_low = int((_rc or {}).get('avm_price_low', 0))
                _avm_high = int((_rc or {}).get('avm_price_high', 0))
                _price_gap_pct = round(((price_to_use - _avm) / _avm * 100), 1) if _avm else 0
                _comps = (_rc or {}).get('comparables', []) or []
                _dom = int((_ms or {}).get('average_days_on_market', 0))
                _listings = int((_ms or {}).get('total_listings', 0))

                result_dict = {
                    'analysis_depth': 'address_only',
                    'property_address': property_address,
                    'property_price': price_to_use,
                    'offer_score': None,
                    'risk_level': None,
                    'risk_score': None,
                    'repair_costs': None,
                    'offer_strategy': None,
                    'negotiation_strategy': None,
                    'deal_breakers': [],
                    'transparency_score': None,
                    'seller_grade': None,
                    'cross_reference': None,
                    'strategic_options': [],
                    'inspection_priorities': [],
                    'market_context': {
                        'avm_price': _avm,
                        'avm_price_low': _avm_low,
                        'avm_price_high': _avm_high,
                        'price_vs_avm_pct': _price_gap_pct,
                        'comparables_count': len(_comps),
                        'average_days_on_market': _dom,
                        'total_listings': _listings,
                        'median_price_per_sqft': float((_ms or {}).get('median_price_per_sqft', 0)),
                    },
                    'environmental': {
                        'flood_zone': (_flood or {}).get('flood_zone', 'Unknown'),
                        'flood_risk': (_flood or {}).get('flood_risk', 'Unknown'),
                        'wildfire_risk': (_env or {}).get('wildfire_risk', 'Unknown'),
                        'seismic_zone': (_env or {}).get('seismic_zone', 'Unknown'),
                        'air_quality_index': int((_air or {}).get('aqi', 0)),
                    },
                    'neighborhood': {
                        'walk_score': int((_walk or {}).get('walk_score', 0)),
                        'transit_score': int((_walk or {}).get('transit_score', 0)),
                        'bike_score': int((_walk or {}).get('bike_score', 0)),
                        'school_rating': float((_schools or {}).get('average_rating', 0)),
                        'school_name': (_schools or {}).get('elementary_school', ''),
                    },
                    'permit_flags': (_permits or {}).get('flags', []),
                    'permit_count': int((_permits or {}).get('permit_count', 0)),
                    'recent_earthquakes': (_eq or {}).get('recent_earthquakes', [])[:3],
                    'research_data': _effective_research,
                }

            else:
                # disclosure_only or full — run intelligence engine
                if analysis_depth == 'disclosure_only':
                    _sp(sse_job_id, 'parsing',     'Parsing disclosure...', 15)
                    _sp(sse_job_id, 'research',    'Querying public records...', 30)
                    _sp(sse_job_id, 'crossref',    'Analysing seller disclosure...', 55)
                    _sp(sse_job_id, 'offer_strat', 'Building inspector checklist...', 80)
                else:
                    _sp(sse_job_id, 'parsing',     'Parsing documents...', 15)
                    _sp(sse_job_id, 'research',    'Querying public records...', 30)
                    _sp(sse_job_id, 'crossref',    'Cross-referencing disclosure vs inspection...', 50)
                    _sp(sse_job_id, 'repair_costs','Calculating repair costs...', 65)
                    _sp(sse_job_id, 'risk_dna',    'Building Property Risk DNA™...', 78)
                    _sp(sse_job_id, 'offer_strat', 'Generating offer strategy...', 88)

                result = _get('intelligence').analyze_property(
                    seller_disclosure_text=seller_disclosure_text,
                    inspection_report_text=inspection_report_text,
                    property_price=price_to_use,
                    buyer_profile=buyer_profile,
                    property_address=property_address,
                    research_data=_effective_research
                )

                _sp(sse_job_id, 'complete', 'Analysis complete!', 100)
                logging.info("✅ Intelligence analysis complete")
            
            # Convert PropertyAnalysis to JSON-serializable dict
            # Only needed when intelligence engine ran (disclosure_only or full)
            if analysis_depth != 'address_only':
                from dataclasses import asdict
                import datetime as dt
                from enum import Enum
                import numpy as np
                
                def convert_value(obj):
                    """Convert a single value to JSON-serializable format"""
                    if isinstance(obj, (dt.datetime, dt.date)):
                        return obj.isoformat()
                    elif isinstance(obj, Enum):
                        return obj.value
                    elif isinstance(obj, np.ndarray):
                        return obj.tolist()
                    elif hasattr(obj, 'to_dict') and callable(obj.to_dict):
                        return obj.to_dict()
                    elif hasattr(obj, '__dataclass_fields__'):
                        return asdict(obj, dict_factory=dict_factory)
                    else:
                        return obj
                
                def dict_factory(fields):
                    return {k: convert_value(v) for k, v in fields}
                
                result_dict = asdict(result, dict_factory=dict_factory)
                
                def clean_dict(obj):
                    import numpy as np
                    if isinstance(obj, np.ndarray):
                        return obj.tolist()
                    elif isinstance(obj, dict):
                        return {k: clean_dict(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [clean_dict(item) for item in obj]
                    elif isinstance(obj, Enum):
                        return obj.value
                    elif isinstance(obj, (dt.datetime, dt.date)):
                        return obj.isoformat()
                    else:
                        return obj
                
                result_dict = clean_dict(result_dict)
                result_dict['analysis_depth'] = analysis_depth
        
        # ── State Disclosure Intelligence (v5.62.85) ────────────────────
        # Detect state from property address or document text, inject
        # state-specific disclosure context into the result.
        try:
            from state_disclosures import detect_state_from_zip, detect_state_from_text, get_state_context
            _state = None
            # Try ZIP first
            _addr = result_dict.get('property_address', '') or ''
            import re as _re
            _zip_match = _re.search(r'(\d{5})', _addr)
            if _zip_match:
                _state = detect_state_from_zip(_zip_match.group(1))
            # Fallback to text detection
            if not _state:
                _text = seller_disclosure_text[:5000] if 'seller_disclosure_text' in dir() else ''
                if _text:
                    _state = detect_state_from_text(_text)
            # Build context
            if _state:
                _ctx = get_state_context(_state)
                result_dict['state_context'] = {
                    'state_code': _ctx.state_code,
                    'state_name': _ctx.state_name,
                    'disclosure_level': _ctx.disclosure_level,
                    'primary_form': _ctx.primary_form,
                    'disclosure_notes': _ctx.disclosure_notes,
                    'buyer_protections': _ctx.buyer_protections,
                    'common_hazards': _ctx.common_hazards,
                    'legal_disclaimer': _ctx.legal_disclaimer,
                }
        except Exception as _e:
            logging.warning(f"State context detection failed: {_e}")
        # ── End State Disclosure Intelligence ────────────────────────────
        
        # Clean up category names (remove underscores, title case)
        _rs = result_dict.get('risk_score') or {}
        if _rs and 'category_scores' in _rs:
            for cat in _rs['category_scores']:
                if 'category' in cat and isinstance(cat['category'], str):
                    # Replace underscores with spaces and title case
                    cat['category'] = cat['category'].replace('_', ' & ').title()
        
        # Professional cleanup for detailed expert output
        import re
        _rs_db = result_dict.get('risk_score') or {}
        if _rs_db and 'deal_breakers' in _rs_db:
            cleaned_breakers = []
            seen_issues = set()
            
            for breaker in _rs_db['deal_breakers']:
                clean_text = breaker
                
                # STEP 1: Remove programmer/system artifacts
                
                # Remove severity prefixes at start
                clean_text = re.sub(r'^(CRITICAL|MAJOR|MODERATE|MINOR)\s*[:\-]?\s*', '', clean_text, flags=re.IGNORECASE)
                
                # Remove programmer variable names (words with underscores)
                clean_text = re.sub(r'\b[a-z]+_[a-z_]+\b', '', clean_text, flags=re.IGNORECASE)
                
                # Remove internal system data references
                clean_text = re.sub(r'(?:with\s+)?(?:risk\s+)?score\s+\d+/\d+', '', clean_text, flags=re.IGNORECASE)
                clean_text = re.sub(r'severity\s*:\s*\d+', '', clean_text, flags=re.IGNORECASE)
                
                # Remove ALL CAPS segments (even in middle of sentence)
                clean_text = re.sub(r'\b[A-Z][A-Z\s\-]{2,}[A-Z]\b\s*[\-:]?\s*', '', clean_text)
                
                # Remove separator artifacts
                clean_text = re.sub(r'[=\-]{3,}', '', clean_text)
                clean_text = re.sub(r'^[-•*]\s*', '', clean_text, flags=re.MULTILINE)
                
                # CRITICAL: Remove leading colons (often left after prefix removal)
                clean_text = re.sub(r'^\s*:\s*', '', clean_text)
                
                # STEP 2: Fix grammar issues
                
                # Fix common grammar errors
                clean_text = re.sub(r'\bdisclose\b(?!\w)', 'disclosed', clean_text, flags=re.IGNORECASE)
                clean_text = re.sub(r'\bobserve\b(?!\w)', 'observed', clean_text, flags=re.IGNORECASE)
                
                # NOTE: DO NOT add periods between lowercase and uppercase automatically
                # This breaks proper nouns like "Federal Pacific", "Foundation Structure", etc.
                
                # STEP 3: Clean up formatting (keep detailed content)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                clean_text = re.sub(r'\s+([.,;:!?])', r'\1', clean_text)
                
                # Remove trailing incomplete fragments
                clean_text = re.sub(r',?\s+OR[.,]?\s*$', '', clean_text, flags=re.IGNORECASE)
                
                # Fix incomplete last words (but keep detailed explanations)
                words = clean_text.split()
                if words and len(words[-1].rstrip('.,;:!?')) <= 2:
                    last_word = words[-1].rstrip('.,;:!?').lower()
                    if last_word in ['ye', 't', 'or', 'in', 'on', 'at', 'to']:
                        words = words[:-1]
                        clean_text = ' '.join(words)
                
                # STEP 4: Quality filters (but keep detailed content)
                
                # Must be at least 50 characters (detailed enough)
                if len(clean_text) < 50:
                    continue
                
                # Must not be all caps
                if clean_text.isupper():
                    continue
                
                # Must not end with colon
                if clean_text.endswith(':'):
                    continue
                
                # Filter recommendations/advice (not actual issues)
                advice_patterns = [
                    r'^consider\s+',
                    r'^completion\s+',
                    r'^recommend',
                    r'^suggest',
                    r'^should\s+consider',
                    r'^advise',
                    r'^buyer\s+should'
                ]
                if any(re.search(pattern, clean_text, re.IGNORECASE) for pattern in advice_patterns):
                    continue
                
                # Filter vague/generic statements (but keep detailed ones)
                if len(clean_text) < 100:  # Only check if relatively short
                    vague_patterns = [
                        r'^issues?\s+(?:with|in|noted)',
                        r'^concerns?\s+(?:with|in|about)',
                        r'^problems?\s+(?:with|in|found)',
                        r'^defects?\s+(?:were|noted)',
                        r'the following',
                        r'items? (?:were )?found',
                        r'repairs? (?:are )?needed'
                    ]
                    if any(re.search(pattern, clean_text, re.IGNORECASE) for pattern in vague_patterns):
                        continue
                
                # Must mention specific components (not just meta-commentary)
                specific_components = [
                    'panel', 'breaker', 'wiring', 'electrical', 'circuit',
                    'roof', 'shingle', 'flashing', 'gutter', 'soffit',
                    'foundation', 'basement', 'crawl', 'slab', 'footing',
                    'plumbing', 'pipe', 'drain', 'sewer', 'water', 'leak',
                    'hvac', 'furnace', 'ac', 'heating', 'cooling', 'duct',
                    'window', 'door', 'wall', 'floor', 'ceiling',
                    'insulation', 'vapor', 'ventilation',
                    'structural', 'beam', 'joist', 'framing',
                    'crack', 'damage', 'corrosion', 'rust', 'mold', 'rot'
                ]
                has_component = any(comp in clean_text.lower() for comp in specific_components)
                if not has_component:
                    continue
                
                # STEP 4: Deduplicate
                # Extract key terms for comparison
                key_terms = re.sub(r'[^a-z0-9\s]', '', clean_text.lower())
                key_terms = ' '.join(sorted(set(key_terms.split())))[:80]
                
                # Check if similar to existing items
                is_duplicate = False
                for existing in seen_issues:
                    # Count common words
                    existing_words = set(existing.split())
                    new_words = set(key_terms.split())
                    common = existing_words & new_words
                    # If more than 60% overlap, it's a duplicate
                    if len(common) > 0.6 * min(len(existing_words), len(new_words)):
                        is_duplicate = True
                        break
                
                if is_duplicate:
                    continue
                
                seen_issues.add(key_terms)
                
                # STEP 5: Ensure professional formatting
                if clean_text and clean_text[0].islower():
                    clean_text = clean_text[0].upper() + clean_text[1:]
                
                if clean_text and not clean_text[-1] in '.!?':
                    if len(clean_text.split()) >= 5:
                        clean_text += '.'
                
                # STEP 6: Final validation - must be detailed enough
                word_count = len(clean_text.split())
                if word_count < 6:  # Too short to be informative
                    continue
                
                cleaned_breakers.append(clean_text)
                
                # Stop at 6 items
                if len(cleaned_breakers) >= 6:
                    break
            
            if result_dict.get('risk_score'):
                result_dict['risk_score']['deal_breakers'] = cleaned_breakers
        
        # Custom JSON encoder for any remaining datetime/enum objects
        import datetime as dt
        from enum import Enum
        class DateTimeEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, (dt.datetime, dt.date)):
                    return obj.isoformat()
                if isinstance(obj, Enum):
                    return obj.value
                return super().default(obj)
        
        # Save analysis — offer_score/risk_tier only available for full/disclosure analyses
        _risk_dna = result_dict.get('risk_dna', {}) or {}
        _composite = float(_risk_dna.get('composite_score', 0) or 0) if isinstance(_risk_dna, dict) else 0
        _offer_score = round(100 - _composite) if _composite else None
        _risk_tier = (_risk_dna.get('risk_tier', 'UNKNOWN') if isinstance(_risk_dna, dict) else None) or ('address_only' if analysis_depth == 'address_only' else 'UNKNOWN')
        
        # 💰 Generate detailed repair cost breakdown — only when inspection data exists
        if analysis_depth != 'address_only' and result_dict.get('risk_score'):
            try:
                from repair_cost_estimator import estimate_repair_costs
                zip_match = None
                addr = result_dict.get('property_address', '')
                import re as _re
                zip_m = _re.search(r'\b(\d{5})\b', addr)
                if zip_m:
                    zip_match = zip_m.group(1)
                
                risk_score_data = result_dict.get('risk_score', {}) or {}
                repair_estimate = estimate_repair_costs(
                    zip_code=zip_match or '',
                    findings=result_dict.get('findings', []),
                    category_scores=risk_score_data.get('category_scores', []),
                    total_repair_low=risk_score_data.get('total_repair_cost_low', 0),
                    total_repair_high=risk_score_data.get('total_repair_cost_high', 0),
                    property_year_built=result_dict.get('year_built'),
                )
                result_dict['repair_estimate'] = repair_estimate
                logging.info(f"💰 Repair estimate attached: ${repair_estimate['total_low']:,.0f}–${repair_estimate['total_high']:,.0f} ({len(repair_estimate['breakdown'])} items)")
            except Exception as repair_err:
                logging.warning(f"💰 Could not generate repair estimate: {repair_err}")
        
        analysis = Analysis(
            property_id=property.id,
            user_id=current_user.id,
            status='completed',
            offer_score=_offer_score,
            risk_tier=_risk_tier,
            result_json=json.dumps(result_dict, cls=DateTimeEncoder),
            buyer_profile_json=json.dumps(buyer_profile_data)
        )
        db.session.add(analysis)
        
        # Update property
        property.status = 'completed'
        property.analyzed_at = datetime.utcnow()
        
        # Increment usage
        current_user.increment_usage()
        
        # Decrement analysis credits — ATOMIC to prevent race conditions
        # CRITICAL: flush ORM changes first, then do raw SQL update to avoid session conflicts
        if current_user.analysis_credits > 0:
            db.session.flush()  # Flush analysis + property changes first
            
            rows_updated = User.query.filter(
                User.id == current_user.id,
                User.analysis_credits > 0
            ).update(
                {User.analysis_credits: User.analysis_credits - 1},
                synchronize_session=False  # Don't conflict with ORM session
            )
            
            if rows_updated == 0:
                db.session.rollback()
                return jsonify({'error': 'No analysis credits remaining'}), 402
            
            # AUTO-REFILL for developer accounts
            # Uses global _get('DEVELOPER_EMAILS')
            if current_user.email.lower() in _get('DEVELOPER_EMAILS'):
                User.query.filter(
                    User.id == current_user.id,
                    User.analysis_credits < 50
                ).update(
                    {User.analysis_credits: 500},
                    synchronize_session=False
                )
                logging.info("👑 DEVELOPER ACCOUNT: Auto-refilled credits to 500")
        
        db.session.commit()
        
        # ── ML Training Data Collection (fire-and-forget) ──
        try:
            from ml_data_collector import collect_training_data
            collect_training_data(
                analysis_id=analysis.id if analysis else 0,
                result_dict=result_dict,
                property_address=property_address or '',
                property_price=property_price or 0,
            )
        except Exception as ml_err:
            logging.warning(f"ML data collection skipped: {ml_err}")
        
        # CRITICAL: Add property_price to result_dict BEFORE validation (Bug #40 - $N/A fix)
        # Validation needs this to correctly validate the recommended_offer
        result_dict['property_price'] = property_price or buyer_profile_data.get('max_budget', 0)
        result_dict['property_address'] = property_address
        # Add offer_score to payload (100 - risk = quality score)
        # For address_only, offer_score stays None (set in result_dict already)
        if analysis_depth != 'address_only':
            _risk_dna = result_dict.get('risk_dna') or {}
            _composite = float(_risk_dna.get('composite_score', 0) or 0) if isinstance(_risk_dna, dict) else 0
            result_dict['offer_score'] = round(100 - _composite)
        logging.info(f"Added property_price to result_dict BEFORE validation: ${result_dict['property_price']:,}")
        
        # CRITICAL: Validate all output before sending to user
        # Validation expects full analysis structure — skip for address_only
        if analysis_depth != 'address_only':
            try:
                result_dict = validate_analysis_output(result_dict)
                logging.info("Analysis output validated successfully")
            except ValidationError as e:
                logging.warning(f"Validation warning: {e}")
                # Continue even if validation has warnings
        
        # CRITICAL: Detect and flag special property types (Bug #34, #38, #39)
        # Only run when documents are present
        if analysis_depth != 'address_only':
            result_dict = detect_and_flag_special_properties(
                result_dict,
                seller_disclosure_text,
                inspection_report_text
            )
        
        # CRITICAL: Calculate confidence score (transparency for users)
        # Skip for address_only — no documents to score confidence against
        if not cached_result and analysis_depth != 'address_only':
            confidence = confidence_scorer.calculate(
                analysis=result_dict,
                input_data={
                    'inspection': inspection_report_text,
                    'disclosure': seller_disclosure_text
                }
            )
            result_dict['confidence'] = confidence
            logging.info(f"Confidence score: {confidence['score']:.1f}% ({confidence['level']})")
            
            # Cache write happens AFTER market patching below — see deferred_cache_write flag
            _deferred_cache = True
        
        # CRITICAL: Ensure property metadata is in result (Bug #27 - $N/A display fix)
        # This applies to BOTH cached and non-cached results
        result_dict['property_id'] = property.id
        result_dict['analysis_id'] = analysis.id  # Needed for addendum, objection, calendar agentic actions
        
        # Ensure property price is present
        if 'property_price' not in result_dict or result_dict['property_price'] <= 0:
            result_dict['property_price'] = property_price or buyer_profile_data.get('max_budget', 0)
            result_dict['property_address'] = property_address
        
        logging.info(f"✅ Analysis complete - Price: ${result_dict.get('property_price', 0):,}")
        
        # 📄 ADD DOCUMENT EXTRACTS (v5.55.8 - Credibility feature)
        # Show users exactly what we found in their uploaded documents
        try:
            document_extracts = {
                'inspection_extracts': [],
                'disclosure_extracts': []
            }
            
            # v5.59.10: Filter out parsing artifacts from document extracts
            import re as _re
            _extract_garbage_patterns = _re.compile(
                r'(?i)'
                r'^FINDINGS\s*:\s*None|'           # Section header "FINDINGS: None"
                r'MAJOR CONCERNS\s*:\s*\d|'        # Section header "MAJOR CONCERNS: 1"
                r'MINOR CONCERNS\s*:\s*\d|'        # Section header "MINOR CONCERNS: 3"
                r'^SECTION\s*\d|'                   # "SECTION 4"
                r'^PAGE\s*\d|'                      # "PAGE 12"
                r'^N/?A$|'                          # "N/A" or "NA" as sole content
                r'^None$|'                          # "None" as sole content
                r'^not applicable$|'               # "not applicable"
                r'^\d+\s*$|'                        # Just a number
                r'TRANSPARENCY CONCERN|'           # AI commentary leak
                r'DISCLOSURE (ISSUE|CONCERN)|'     # AI commentary leak
                r'^(CRITICAL|MAJOR|MODERATE|MINOR)\s*[-:]?\s*$'  # Bare severity labels
            )
            # Separate case-sensitive check for ALL-CAPS headers
            _allcaps_pattern = _re.compile(r'^[A-Z\s:,\-]{10,}$')
            
            def _is_valid_extract(text):
                """Return True if this looks like a real finding, not a parsing artifact"""
                if not text or not isinstance(text, str):
                    return False
                text = text.strip()
                if len(text) < 5:
                    return False
                if _extract_garbage_patterns.search(text):
                    return False
                if _allcaps_pattern.match(text):
                    return False
                return True
            
            # Extract key inspection findings with source quotes
            _rs2 = result_dict.get('risk_score') or {}
            if _rs2 and 'category_scores' in _rs2:
                for cat in _rs2['category_scores']:
                    if cat.get('key_issues'):
                        for issue in cat.get('key_issues', [])[:3]:  # Top 3 per category
                            if _is_valid_extract(issue):
                                document_extracts['inspection_extracts'].append({
                                    'category': cat.get('category', 'Unknown'),
                                    'finding': issue,
                                    'cost_from_document': not cat.get('costs_are_estimates', True)
                                })
            
            # Extract disclosed items from transparency report
            if 'transparency_report' in result_dict:
                tr = result_dict['transparency_report']
                if tr.get('red_flags'):
                    for flag in tr['red_flags'][:5]:  # Top 5 red flags
                        if flag.get('evidence'):
                            evidence = flag['evidence']
                            if isinstance(evidence, list):
                                evidence = '; '.join(evidence[:2])
                            document_extracts['disclosure_extracts'].append({
                                'flag': flag.get('description', ''),
                                'evidence': evidence[:200] if evidence else '',
                                'source_page': flag.get('disclosure_page') or flag.get('inspection_page')
                            })
            
            result_dict['document_extracts'] = document_extracts
            logging.info(f"📄 Added {len(document_extracts['inspection_extracts'])} inspection + {len(document_extracts['disclosure_extracts'])} disclosure extracts")
        except Exception as extract_error:
            logging.warning(f"Could not add document extracts: {extract_error}")
        
        # 📧 Send analysis complete email (async-friendly, non-blocking)
        try:
            offer_strategy = result_dict.get('offer_strategy') or {}
            recommended_offer = offer_strategy.get('recommended_offer') if offer_strategy else None
            
            # OfferScore = 100 - risk_dna.composite_score (same formula as main analysis display)
            risk_dna = result_dict.get('risk_dna') or {}
            composite_score = float(risk_dna.get('composite_score', 0) or 0) if isinstance(risk_dna, dict) else 0
            offer_score = round(100 - composite_score) if composite_score else None
            
            # Only send if we have meaningful data (skip for address_only)
            if recommended_offer and property_price and analysis_depth != 'address_only':
                send_analysis_complete(
                    current_user.email,
                    current_user.name or 'there',
                    property_address,
                    offer_score,
                    recommended_offer,
                    property_price,
                    property_id=property.id
                )
                logging.info(f"📧 Analysis complete email sent to {current_user.email}")
        except Exception as email_error:
            # Don't fail the analysis if email fails
            logging.warning(f"📧 Could not send analysis complete email: {email_error}")
        

        # 📋 FLYWHEEL: Inspector loop-back notification
        # If this analysis was sourced from an inspector's report, notify the inspector
        try:
            _insp_report_id = (
                data.get('inspector_report_id') or
                request.args.get('inspector_report_id')
            )
            if _insp_report_id:
                from models import InspectorReport, Inspector, User as _User
                from flywheel_notifications import _send_inspector_loop_email
                _ir = InspectorReport.query.get(int(_insp_report_id))
                if _ir:
                    # Store the link on the analysis
                    analysis.inspector_report_id = _ir.id
                    db.session.commit()
                    # Queue the inspector notification email
                    _send_inspector_loop_email(
                        inspector_report=_ir,
                        result_dict=result_dict,
                        buyer_email=current_user.email,
                        savings=int(property_price - ((result_dict.get('offer_strategy') or {}).get('recommended_offer') or property_price)),
                    )
                    logging.info(f"📋 Inspector loop-back email queued for report {_ir.id}")
        except Exception as _insp_loop_err:
            logging.warning(f"📋 Inspector loop-back skipped: {_insp_loop_err}")

        # 🤖 INJECT RESEARCH DATA into response
        # This was collected from the parallel background thread (or run now for cached results)
        # Best available research data for fresh analyses
        _best_research = research_data or _fast_market_data
        if not cached_result and _best_research:
            result_dict['research_data'] = _best_research
            logging.info(f"🤖 Research data included ({_best_research.get('tools_succeeded', 0)} tools)")

            # ── FRESH ANALYSIS: patch market_context if intelligence didn't get it ──
            # analyze_property sets market_context only if market_intel succeeded internally.
            # If it's missing from the result, recompute it now from research_data.
            _mc_existing = (result_dict.get('offer_strategy') or {}).get('market_context') or {}
            if not _mc_existing.get('market_applied') and _best_research:
                _patch_source = _best_research
                try:
                    from market_intelligence import MarketIntelligenceEngine, apply_market_adjustment
                    _mi_engine = MarketIntelligenceEngine()
                    _prop_price = result_dict.get('property_price', 0) or 0
                    if _prop_price > 0 and (_patch_source or {}).get('tool_results'):
                        _market_intel = _mi_engine.from_research_data(
                            research_data=_patch_source,
                            asking_price=int(_prop_price),
                            address=property_address or '',
                        )
                        if _market_intel and _market_intel.data_quality != 'none':
                            result_dict['market_intelligence'] = _market_intel.to_dict()
                            _market_result = apply_market_adjustment(
                                (result_dict.get('offer_strategy') or {}).get('recommended_offer', _prop_price),
                                _prop_price,
                                _market_intel
                            )
                            if _market_result.get('market_applied'):
                                if 'offer_strategy' not in result_dict or result_dict['offer_strategy'] is None:
                                    result_dict['offer_strategy'] = {}
                                result_dict['offer_strategy']['market_context'] = {
                                    'market_applied': True,
                                    'market_temperature': _market_result.get('market_temperature', ''),
                                    'buyer_leverage': _market_result.get('buyer_leverage', ''),
                                    'estimated_value': _market_result.get('estimated_value', 0),
                                    'avg_dom': getattr(_market_intel.market, 'average_days_on_market', 0) if _market_intel.market else 0,
                                    'asking_vs_avm_pct': _market_result.get('asking_vs_avm_pct', 0),
                                    'comp_count': _market_result.get('comp_count', 0),
                                    'comp_median_price': _market_result.get('comp_median_price', 0),
                                    'comp_avg_dom': getattr(_market_intel, 'comp_avg_dom', 0),
                                    'comp_avg_price_per_sqft': getattr(_market_intel, 'comp_avg_price_per_sqft', 0),
                                    'asking_vs_comps_pct': getattr(_market_intel, 'asking_vs_comps_pct', 0),
                                    'market_adjustment_amount': _market_result.get('market_adjustment_amount', 0),
                                    'market_adjustment_pct': _market_result.get('market_adjustment_pct', 0),
                                    'market_rationale': _market_result.get('rationale', ''),
                                    'avm_range_low': getattr(_market_intel, 'value_range_low', 0),
                                    'avm_range_high': getattr(_market_intel, 'value_range_high', 0),
                                    'zip_code': getattr(_market_intel.market, 'zip_code', '') if _market_intel.market else '',
                                    'total_listings': getattr(_market_intel.market, 'total_listings', 0) if _market_intel.market else 0,
                                    'median_price_per_sqft': getattr(_market_intel.market, 'median_price_per_sqft', 0) if _market_intel.market else 0,
                                    'price_trend_pct': getattr(_market_intel.market, 'price_trend_pct', 0) if _market_intel.market else 0,
                                    'inventory_trend_pct': getattr(_market_intel.market, 'inventory_trend_pct', 0) if _market_intel.market else 0,
                                }
                                logging.warning(f"[MARKET] ✅ Fresh result patched post-analysis: temp={_market_result.get('market_temperature')} avm={_market_result.get('estimated_value')}")
                            else:
                                logging.warning(f"[MARKET] ⚠️ Fresh result: apply_market_adjustment returned market_applied=False avm={_market_intel.avm_price} comps={_market_intel.comp_count}")
                        else:
                            logging.warning(f"[MARKET] ⚠️ Fresh result: MarketIntelligence quality={getattr(_market_intel,'data_quality','none') if _market_intel else 'None'}")
                except Exception as _mi_fresh_err:
                    logging.warning(f"[MARKET] ⚠️ Fresh market patch failed: {_mi_fresh_err}")
        elif cached_result and property_address and len(property_address) >= 15:
            # Cached result — run research now and recompute market intelligence
            try:
                _ai_client = None
                try:
                    import anthropic as _anthropic
                    _api_key = os.environ.get('ANTHROPIC_API_KEY')
                    if _api_key:
                        _ai_client = _anthropic.Anthropic(api_key=_api_key)
                except Exception:
                    pass
                agent = PropertyResearchAgent(ai_client=_ai_client)
                research_data = agent.research(property_address)
                if research_data:
                    result_dict['research_data'] = research_data
                    logging.info(f"🤖 Research data (cached path) included: {research_data.get('tools_succeeded', 0)} tools")

                    # Recompute market_intelligence from fresh research data
                    try:
                        from market_intelligence import MarketIntelligenceEngine, apply_market_adjustment
                        _mi_engine = MarketIntelligenceEngine()
                        _prop_price = result_dict.get('property_price', 0) or 0
                        if research_data.get('tool_results') and _prop_price > 0:
                            _market_intel = _mi_engine.from_research_data(
                                research_data=research_data,
                                asking_price=int(_prop_price),
                                address=property_address,
                            )
                            if _market_intel and _market_intel.data_quality != 'none':
                                result_dict['market_intelligence'] = _market_intel.to_dict()
                                # Also patch market_context into offer_strategy
                                _market_result = apply_market_adjustment(
                                    (result_dict.get('offer_strategy') or {}).get('recommended_offer', _prop_price),
                                    _prop_price,
                                    _market_intel
                                )
                                if _market_result.get('market_applied'):
                                    if 'offer_strategy' not in result_dict:
                                        result_dict['offer_strategy'] = {}
                                    result_dict['offer_strategy']['market_context'] = {
                                        'market_applied': True,
                                        'market_temperature': _market_result.get('market_temperature', ''),
                                        'buyer_leverage': _market_result.get('buyer_leverage', ''),
                                        'estimated_value': _market_result.get('estimated_value', 0),
                                        'avg_dom': getattr(_market_intel.market, 'average_days_on_market', 0) if _market_intel.market else 0,
                                        'asking_vs_avm_pct': _market_result.get('asking_vs_avm_pct', 0),
                                        'comp_count': _market_result.get('comp_count', 0),
                                        'comp_median_price': _market_result.get('comp_median_price', 0),
                                        'market_rationale': _market_result.get('rationale', ''),
                                    }
                                    logging.warning(f"[MARKET] ✅ Cached result patched: temp={_market_result.get('market_temperature')} avm={_market_result.get('estimated_value')}")
                    except Exception as _mi_err:
                        logging.warning(f"[MARKET] Market recompute for cached result failed: {_mi_err}")
            except Exception as re:
                logging.warning(f"🤖 Research for cached result failed: {re}")

        # ── Deferred cache write — now that market_context is patched in ──
        if not cached_result and 'cache_key' in dir() and cache_key:
            try:
                cache.set(
                    cache_key=cache_key,
                    analysis=result_dict,
                    property_address=property_address,
                    asking_price=property_price or buyer_profile_data.get('max_budget', 0)
                )
                logging.info(f"💾 Cached analysis (with market data) property_price: ${result_dict.get('property_price',0):,}")
            except Exception as _cache_err:
                logging.warning(f"💾 Cache write failed (non-fatal): {_cache_err}")

        # 🆓 FREE TIER GATING (v5.59.33)
        # If user has never paid (no stripe_customer_id) and is not a developer, gate premium sections
        # Uses global _get('DEVELOPER_EMAILS')
        dev_emails = _get('DEVELOPER_EMAILS')
        is_developer = current_user.email.lower() in dev_emails
        is_free_tier = not bool(current_user.stripe_customer_id) and not is_developer
        result_dict['is_free_tier'] = is_free_tier
        if is_free_tier:
            logging.info(f"🆓 Free tier analysis for {current_user.email} — premium sections will be gated in frontend")
        
        # Track analysis completion (skip test accounts)
        try:
            from funnel_tracker import track, TEST_EMAIL_DOMAINS
            if not any(current_user.email.endswith(d) for d in TEST_EMAIL_DOMAINS):
                track('analysis_complete', user_id=current_user.id, metadata={
                    'address': result_dict.get('property_address', '')[:100],
                    'risk_score': result_dict.get('risk_score'),
                    'is_free_tier': is_free_tier,
                })
        except Exception:
            pass
        
        resp = jsonify(result_dict)
        # Emit job ID so frontend can connect SSE for progress replay
        try:
            resp.headers['X-Job-Id'] = sse_job_id
        except Exception:
            pass
        return resp
        
    except Exception as e:
        db.session.rollback()
        import traceback
        tb = traceback.format_exc()
        logging.error(f"❌ Analysis error: {e}")
        logging.error(f'Internal error traceback:\n{tb}')
        return jsonify({'error': 'An internal error occurred. Please try again.', 'detail': str(e), 'traceback': tb}), 500
