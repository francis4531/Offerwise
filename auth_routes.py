"""
OfferWise Auth Routes Blueprint
Extracted from app.py v5.74.47 for architecture cleanup.
"""

import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, send_from_directory, redirect, url_for, session, render_template_string, flash
from flask_login import login_required, login_user, logout_user, current_user
from models import db, User, MagicLink, EmailRegistry, Analysis, Property
from blueprint_helpers import DeferredDecorator, make_deferred_limiter
from email_service import send_welcome_email, send_email

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

def _app():
    import app as _a
    return _a

def check_user_needs_onboarding(user):
    return _app().check_user_needs_onboarding(user)

def _get_developer_emails():
    return _app().DEVELOPER_EMAILS

# OAuth provider proxies — resolved lazily to avoid circular import at module load
class _OAuthProxy:
    def __init__(self, name):
        self._name = name
        self._obj = None
    def _resolve(self):
        if self._obj is None:
            self._obj = getattr(_app(), self._name, None)
        return self._obj
    def __bool__(self):
        return self._resolve() is not None
    def __getattr__(self, item):
        obj = self._resolve()
        if obj is None:
            raise RuntimeError(f"OAuth provider '{self._name}' is not configured")
        return getattr(obj, item)

google = _OAuthProxy('google')
apple = _OAuthProxy('apple')
facebook = _OAuthProxy('facebook')

class _LazyList:
    """Proxies a list from app module, resolved on first use."""
    def __init__(self, attr):
        self._attr = attr
    def _resolve(self):
        return getattr(_app(), self._attr)
    def __contains__(self, item): return item in self._resolve()
    def __iter__(self): return iter(self._resolve())
    def __len__(self): return len(self._resolve())

DEVELOPER_EMAILS = _LazyList('DEVELOPER_EMAILS')

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


def init_auth_blueprint(app, admin_required_fn, api_admin_required_fn,
                       api_login_required_fn, dev_only_gate_fn, limiter):
    _admin_required_ref[0] = admin_required_fn
    _api_admin_required_ref[0] = api_admin_required_fn
    _api_login_required_ref[0] = api_login_required_fn
    _dev_only_gate_ref[0] = dev_only_gate_fn
    _limiter_ref[0] = limiter
    app.register_blueprint(auth_bp)
    logger.info("✅ Auth Routes blueprint registered")



@auth_bp.route('/login')
def login_page():
    """Login page - OAuth only"""
    try:
        if current_user.is_authenticated:
            # Check onboarding status and get destination
            needs_onboarding, redirect_url = check_user_needs_onboarding(current_user)
            
            if needs_onboarding:
                return redirect(redirect_url)
            
            # Onboarding complete - go to suggested destination or dashboard
            if redirect_url:
                return redirect(redirect_url)
            
            return redirect(url_for('dashboard'))
    except Exception:
        pass
    
    return send_from_directory('static', 'login.html')



@auth_bp.route('/logout')
@login_required
def logout():
    """User logout - serves page that clears localStorage"""
    logout_user()
    # Serve logout page that clears localStorage before redirecting
    return send_from_directory('static', 'logout.html')



@auth_bp.route('/login/google')
def login_google():
    """Initiate Google OAuth login"""
    # Store referral code in session if provided
    referral_code = request.args.get('re')
    if referral_code:
        session['referral_code'] = referral_code.strip().upper()
        logging.info(f"🎁 Stored referral code in session: {referral_code}")
    
    redirect_uri = url_for('auth.google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)



@auth_bp.route('/auth/google/callback')
def google_callback():
    """Handle Google OAuth callback"""
    try:
        import time as _cbtime
        _cb_start = _cbtime.time()
        logging.info(f"🔐 CALLBACK START: state={request.args.get('state','')[:12]}...")
        
        # Get the token from Google
        logging.info("🔐 CALLBACK STEP 1: calling authorize_access_token...")
        token = google.authorize_access_token()
        logging.info(f"🔐 CALLBACK STEP 1 DONE in {_cbtime.time()-_cb_start:.2f}s")
        
        # Get user info from Google
        user_info = token.get('userinfo')
        if not user_info:
            resp = google.get('https://www.googleapis.com/oauth2/v3/userinfo')
            user_info = resp.json()
        
        email = user_info.get('email')
        name = user_info.get('name')
        google_id = user_info.get('sub')
        
        if not email:
            flash('Could not get email from Google. Please try again.', 'error')
            return redirect(url_for('auth.login_page'))
        
        logging.info(f"🔐 CALLBACK STEP 2: got email={email}, querying user...")
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        logging.info(f"🔐 CALLBACK STEP 2 DONE: user={'found' if user else 'not found'}")
        
        if user:
            # Existing user - just update Google ID if needed
            if not user.google_id:
                user.google_id = google_id
                user.auth_provider = 'google'
            
            # DEVELOPER ACCOUNT: Ensure unlimited credits on every login
            # Uses global DEVELOPER_EMAILS
            is_developer = email.lower() in DEVELOPER_EMAILS
            
            if is_developer:
                # Boost to 500 if below
                if user.analysis_credits < 500:
                    old_credits = user.analysis_credits
                    user.analysis_credits = 500
                    user.tier = 'enterprise'
                    logging.info(f"👑 DEVELOPER LOGIN: Boosted credits {old_credits} -> 500")
            
            # FREE USER WITH 0 CREDITS: Restore 1 free credit if they never paid
            # This handles stale accounts from failed deletions or edge cases
            if not is_developer and user.analysis_credits <= 0 and not user.stripe_customer_id:
                # Check if they've ever completed an analysis
                analysis_count = db.session.query(Analysis).join(Property).filter(Property.user_id == user.id).count()
                if analysis_count == 0:
                    user.analysis_credits = 1
                    logging.info(f"🔄 Restored 1 free credit for {email} (0 credits, never paid, 0 analyses)")
            
            db.session.commit()
        else:
            # New user signup - check email registry for credit eligibility
            logging.info("")
            logging.info("🆕 NEW USER SIGNUP PROCESS")
            logging.info(f"📧 Email: {email}")
            
            # BLOCK CHECK: Prevent accounts that have been deleted 3+ times
            if EmailRegistry.is_blocked(email):
                logging.warning(f"🚫 BLOCKED: {email} has been deleted 3+ times — account creation denied")
                flash('This email address has been blocked due to repeated account deletions. Please contact support.', 'error')
                return redirect(url_for('auth.login_page'))
            
            
            # Register email and check credit eligibility
            logging.info("🔍 STEP 1: Registering email in EmailRegistry...")
            email_registry, is_new_email = EmailRegistry.register_email(email)
            logging.info(f"   Registry exists: {email_registry is not None}")
            logging.info(f"   Is new email: {is_new_email}")
            if email_registry:
                logging.info(f"   Has received credit before: {email_registry.has_received_free_credit}")
                logging.info(f"   Times deleted: {email_registry.times_deleted}")
                logging.info(f"   Is flagged abuse: {email_registry.is_flagged_abuse}")
            
            logging.info("")
            logging.info("🔍 STEP 2: Checking credit eligibility...")
            can_receive_credit, reason = EmailRegistry.can_receive_free_credit(email)
            logging.info(f"   Can receive credit: {can_receive_credit}")
            logging.info(f"   Reason: {reason}")
            logging.info("")
            
            if can_receive_credit:
                analysis_credits = 1
                logging.info("✅ STEP 3: GIVING FREE CREDIT")
                logging.info(f"   Credits to assign: {analysis_credits}")
                EmailRegistry.give_free_credit(email)
                logging.info("   Marked email as received credit in registry")
            else:
                # No free credit
                analysis_credits = 0
                logging.warning("❌ STEP 3: NO FREE CREDIT")
                logging.warning(f"   Credits to assign: {analysis_credits}")
                logging.warning(f"   Reason: {reason}")
                
                if reason == "abuse_flagged":
                    logging.warning(f"🚨 ABUSE FLAG: {email} is flagged for credit abuse")
                elif reason == "already_received":
                    logging.info(f"ℹ️  {email} already received free credit previously")
            
            logging.info("")
            logging.info("🔍 STEP 4: Creating user account...")
            logging.info(f"   Email: {email}")
            logging.info(f"   Initial Credits: {analysis_credits}")
            logging.info("   Tier: free")
            
            # DEVELOPER/OWNER ACCOUNT: Automatic unlimited credits!
            # Uses global DEVELOPER_EMAILS
            # (defined at top of file)
            
            is_developer = email.lower() in DEVELOPER_EMAILS
            
            if is_developer:
                analysis_credits = 500  # Developer gets unlimited credits
                tier = 'enterprise'  # Give enterprise tier
                logging.info("")
                logging.info("👑 DEVELOPER ACCOUNT DETECTED!")
                logging.info(f"   Email: {email}")
                logging.info(f"   🎁 GRANTING UNLIMITED CREDITS: {analysis_credits}")
                logging.info("   🎁 GRANTING ENTERPRISE TIER")
                logging.info("   This account will auto-refill credits")
            else:
                # Check for saved credits from previous deletion
                if email_registry and email_registry.saved_credits > 0:
                    logging.info("")
                    logging.info("💰 FOUND SAVED CREDITS FROM PREVIOUS ACCOUNT!")
                    logging.info(f"   Saved credits: {email_registry.saved_credits}")
                    logging.info(f"   Saved at: {email_registry.credits_saved_at}")
                    
                    # Restore saved credits
                    analysis_credits = email_registry.saved_credits
                    logging.info(f"   ✅ RESTORING {analysis_credits} credits to new account!")
                    
                    # Clear saved credits (they've been restored)
                    email_registry.saved_credits = 0
                    email_registry.credits_saved_at = None
                    db.session.commit()
                
                tier = 'free'
            
            logging.info("")
            logging.info(f"📊 FINAL CREDIT AMOUNT: {analysis_credits}")
            logging.info(f"📊 TIER: {tier}")
            
            # Create new user account
            user = User(
                email=email,
                name=name,
                google_id=google_id,
                auth_provider='google',
                tier=tier,
                subscription_status='active',
                analysis_credits=analysis_credits
            )
            
            db.session.add(user)
            db.session.flush()  # Get user ID before processing referral
            
            # Generate referral code for new user (backwards compatible)
            try:
                user.generate_referral_code()
            except Exception as e:
                logging.warning(f"Could not generate referral code (migration not run yet?): {e}")
            
            # Process referral if they used a code (backwards compatible)
            referral_code = session.get('referral_code')
            if referral_code:
                try:
                    logging.info(f"🎁 Processing referral with code: {referral_code}")
                    from referral_service import ReferralService
                    result = ReferralService.process_signup_referral(user, referral_code)
                    if result.get('success'):
                        logging.info(f"✅ Referral processed: +{result.get('referee_credits')} credits")
                        session.pop('referral_code', None)  # Clear the code from session
                except Exception as e:
                    logging.warning(f"Could not process referral (migration not run yet?): {e}")
            
            # Inspector report attribution — did they come from a shared report link?
            _share_token = session.pop('inspector_share_token', None)
            if _share_token:
                try:
                    from models import InspectorReport, Inspector
                    _rpt = InspectorReport.query.filter_by(share_token=_share_token).first()
                    if _rpt and not _rpt.buyer_registered:
                        _rpt.buyer_registered = True
                        if hasattr(_rpt, 'buyer_registered_at'):
                            _rpt.buyer_registered_at = datetime.utcnow()
                        _insp = Inspector.query.get(_rpt.inspector_id)
                        if _insp:
                            _insp.total_buyers_converted = (_insp.total_buyers_converted or 0) + 1
                        logging.info(f"📊 Inspector attribution: report→buyer {user.email} via {_share_token[:8]}")
                except Exception as _ae:
                    logging.warning(f"Inspector attribution error: {_ae}")

            db.session.commit()
            session['new_signup'] = True  # triggers pixel fire on next page load
            
            logging.info(f"✅ User account created with ID: {user.id}")
            logging.info(f"✅ Credits assigned: {user.analysis_credits}")
            logging.info(f"🎫 Referral code: {user.referral_code}")
            
            # Track signup funnel event — use UTM source from session if available
            try:
                from funnel_tracker import track
                from flask import session as _sess
                _utm_src = _sess.get('utm_source', 'google')
                _utm_med = _sess.get('utm_medium', 'oauth')
                _utm_cam = _sess.get('utm_campaign')
                track('signup', source=_utm_src, medium=_utm_med,
                      user_id=user.id, metadata={'campaign': _utm_cam, 'auth': 'google'})
            except Exception:
                pass
            
            # 📧 Send welcome email to new user
            try:
                send_welcome_email(user.email, user.name or 'there')
                logging.info(f"📧 Welcome email sent to {user.email}")
            except Exception as e:
                logging.warning(f"📧 Could not send welcome email: {e}")
            
            logging.info("🆕" * 50)
            logging.info("")
        
        # Log the user in
        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        logging.info("")
        logging.info("🔐" * 50)
        logging.info("🔐 GOOGLE OAUTH: USER LOGGED IN")
        logging.info("🔐" * 50)
        logging.info(f"📧 Email: {user.email}")
        logging.info(f"🆔 User ID: {user.id}")
        logging.info(f"⏰ Last Login: {user.last_login}")
        logging.info("")
        logging.info("📊 User State:")
        logging.info(f"   onboarding_completed: {user.onboarding_completed}")
        logging.info(f"   onboarding_completed_at: {user.onboarding_completed_at}")
        logging.info(f"   max_budget: {user.max_budget}")
        logging.info(f"   repair_tolerance: {user.repair_tolerance}")
        logging.info("🔐" * 50)
        logging.info("")
        
        # Check onboarding status and get destination
        needs_onboarding, redirect_url = check_user_needs_onboarding(user)
        
        logging.info("")
        logging.info("🎯 ONBOARDING CHECK RESULT:")
        logging.info(f"   needs_onboarding: {needs_onboarding}")
        logging.info(f"   redirect_url: {redirect_url}")
        logging.info("")
        
        if needs_onboarding:
            # User needs to complete preferences or legal
            logging.info(f"🆕 New Google user {user.id} needs onboarding - redirecting to {redirect_url}")
            logging.info("")
            return redirect(redirect_url)
        
        # Onboarding complete - redirect_url contains final destination
        if redirect_url:
            logging.info(f"✅ Google user {user.id} onboarding complete - sending to {redirect_url}")
            return redirect(redirect_url)
        
        # Fallback to dashboard
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        logging.error(f"Google OAuth error: {e}")
        flash('An error occurred during Google login. Please try again.', 'error')
        return redirect(url_for('auth.login_page'))




# =============================================================================
# EMAIL/PASSWORD AUTHENTICATION
# =============================================================================

@auth_bp.route('/auth/register', methods=['POST'])
@_limiter.limit("5 per minute;20 per hour")  # SECURITY: Prevent mass account creation
def auth_register():
    """Register a new account with email and password"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400
    
    email = (data.get('email') or '').strip().lower()
    password = data.get('password', '')
    name = (data.get('name') or '').strip()
    
    # Validation
    if not email or '@' not in email or '.' not in email:
        return jsonify({'error': 'Please enter a valid email address.'}), 400
    
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters.'}), 400
    
    if len(name) < 1:
        return jsonify({'error': 'Please enter your name.'}), 400
    
    # Check if email already exists
    existing = User.query.filter_by(email=email).first()
    if existing:
        if existing.password_hash:
            return jsonify({'error': 'An account with this email already exists. Please sign in.'}), 409
        else:
            # User exists via OAuth but no password set - let them add one
            existing.set_password(password)
            if not existing.name and name:
                existing.name = name
            db.session.commit()
            login_user(existing)
            existing.last_login = datetime.utcnow()
            db.session.commit()
            
            needs_onboarding, redirect_url = check_user_needs_onboarding(existing)
            return jsonify({
                'success': True,
                'redirect': redirect_url if redirect_url else '/settings?tab=analyses',
                'message': 'Password added to your existing account.'
            })
    
    # --- New user signup (mirrors Google OAuth flow) ---
    logging.info("NEW USER SIGNUP (Email/Password)")
    logging.info(f"Email: {email}")
    
    # BLOCK CHECK: Prevent accounts deleted 3+ times
    if EmailRegistry.is_blocked(email):
        logging.warning(f"🚫 BLOCKED: {email} has been deleted 3+ times — registration denied")
        return jsonify({'error': 'This email address has been blocked due to repeated account deletions. Please contact support.'}), 403
    
    # Store referral code if in session
    referral_code = session.get('referral_code') or data.get('referral_code')
    
    # Register email and check credit eligibility
    email_registry, is_new_email = EmailRegistry.register_email(email)
    can_receive_credit, reason = EmailRegistry.can_receive_free_credit(email)
    
    if can_receive_credit:
        analysis_credits = 1
        EmailRegistry.give_free_credit(email)
        logging.info(f"Free credit granted: {reason}")
    else:
        analysis_credits = 0
        logging.info(f"No free credit: {reason}")
    
    # Check for developer account
    # Uses global DEVELOPER_EMAILS
    # (defined at top of file)
    is_developer = email in DEVELOPER_EMAILS
    
    if is_developer:
        analysis_credits = 500
        tier = 'enterprise'
    else:
        # Restore saved credits from previous deletion
        if email_registry and email_registry.saved_credits > 0:
            analysis_credits = email_registry.saved_credits
            email_registry.saved_credits = 0
            email_registry.credits_saved_at = None
            db.session.commit()
        tier = 'free'
    
    # Create user
    user = User(
        email=email,
        name=name,
        auth_provider='email',
        tier=tier,
        subscription_status='active',
        analysis_credits=analysis_credits
    )
    user.set_password(password)
    
    db.session.add(user)
    db.session.flush()
    
    # Generate referral code
    try:
        user.generate_referral_code()
    except Exception as e:
        logging.warning(f"Could not generate referral code: {e}")
    
    # Process referral
    if referral_code:
        try:
            from referral_service import ReferralService
            result = ReferralService.process_signup_referral(user, referral_code)
            if result.get('success'):
                logging.info(f"Referral processed: +{result.get('referee_credits')} credits")
                session.pop('referral_code', None)
        except Exception as e:
            logging.warning(f"Could not process referral: {e}")
    
    db.session.commit()
    session['new_signup'] = True  # triggers pixel fire on next page load
    logging.info(f"User created with ID: {user.id}, credits: {user.analysis_credits}")
    
    # Track signup funnel event — use UTM source from session if available
    try:
        from funnel_tracker import track
        from flask import session as _sess
        _utm_src = _sess.get('utm_source', 'direct')
        _utm_med = _sess.get('utm_medium', 'email')
        _utm_cam = _sess.get('utm_campaign')
        track('signup', source=_utm_src, medium=_utm_med,
              user_id=user.id, metadata={'campaign': _utm_cam, 'auth': 'email'})
    except Exception:
        pass
    
    # Send welcome email
    try:
        send_welcome_email(user.email, user.name or 'there')
    except Exception as e:
        logging.warning(f"Could not send welcome email: {e}")
    
    # Log in the user
    login_user(user)
    user.last_login = datetime.utcnow()
    db.session.commit()

    # Re-link any ghost PropertyWatches created when this buyer had no account
    try:
        from models import PropertyWatch
        ghost_watches = PropertyWatch.query.filter_by(
            ghost_buyer_email=user.email.lower(), owned_by_professional=True, is_active=True
        ).all()
        for gw in ghost_watches:
            gw.user_id            = user.id
            gw.ghost_buyer_email  = None
            gw.owned_by_professional = False
        if ghost_watches:
            db.session.commit()
            logging.info(f"🔭 Re-linked {len(ghost_watches)} ghost watches to new buyer {user.email}")
    except Exception as _gre:
        logging.warning(f"Ghost watch re-link failed (non-fatal): {_gre}")
    
    # Inspector report attribution — did they come from a shared inspector report?
    _share_token = session.pop('inspector_share_token', None)
    if _share_token:
        try:
            from models import InspectorReport, Inspector
            _rpt = InspectorReport.query.filter_by(share_token=_share_token).first()
            if _rpt and not _rpt.buyer_registered:
                _rpt.buyer_registered = True
                _rpt.user_id = user.id
                if hasattr(_rpt, 'buyer_registered_at'):
                    _rpt.buyer_registered_at = datetime.utcnow()
                _insp = Inspector.query.get(_rpt.inspector_id)
                if _insp:
                    _insp.total_buyers_converted = (_insp.total_buyers_converted or 0) + 1
                db.session.commit()
                logging.info(f"Inspector attribution (email signup): report->buyer {user.email} via {_share_token[:8]}")
        except Exception as _ae:
            logging.warning(f"Inspector attribution error (email signup): {_ae}")

    # If the client requested a specific B2B onboarding destination, honour it.
    # B2B wizard URLs take priority over the buyer onboarding flow.
    B2B_WIZARDS = ('/agent-onboarding', '/inspector-onboarding', '/contractor-onboarding')
    requested_redirect = (request.get_json() or {}).get('next_url', '') or request.args.get('redirect', '')
    if any(requested_redirect.startswith(w) for w in B2B_WIZARDS):
        return jsonify({
            'success': True,
            'redirect': requested_redirect,
            'message': 'Account created successfully!'
        })

    needs_onboarding, redirect_url = check_user_needs_onboarding(user)

    return jsonify({
        'success': True,
        'redirect': redirect_url if redirect_url else '/settings?tab=analyses',
        'message': 'Account created successfully!'
    })




@auth_bp.route('/auth/login-email', methods=['POST'])
@_limiter.limit("10 per minute;30 per hour")  # SECURITY: Brute force protection
def auth_login_email():
    """Sign in with email and password"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400
    
    email = (data.get('email') or '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'error': 'Please enter both email and password.'}), 400
    
    user = User.query.filter_by(email=email).first()
    
    if not user:
        return jsonify({'error': 'No account found with this email. Please sign up.'}), 401
    
    if not user.password_hash:
        # User signed up via OAuth, no password set
        provider = user.auth_provider or 'Google or Facebook'
        return jsonify({
            'error': f'This account was created with {provider.title()}. Please use the "{provider.title()}" button to sign in, or click "Forgot Password" to set a password.'
        }), 401
    
    if not user.check_password(password):
        return jsonify({'error': 'Incorrect password. Please try again.'}), 401
    
    # Developer credit boost
    # Uses global DEVELOPER_EMAILS
    # (defined at top of file)
    if email in DEVELOPER_EMAILS and user.analysis_credits < 500:
        user.analysis_credits = 500
        user.tier = 'enterprise'
    
    # FREE USER WITH 0 CREDITS: Restore 1 free credit if never paid and no analyses
    if email not in DEVELOPER_EMAILS and user.analysis_credits <= 0 and not user.stripe_customer_id:
        analysis_count = db.session.query(Analysis).join(Property).filter(Property.user_id == user.id).count()
        if analysis_count == 0:
            user.analysis_credits = 1
            logging.info(f"🔄 Restored 1 free credit for {email} (0 credits, never paid, 0 analyses)")
    
    login_user(user)
    user.last_login = datetime.utcnow()
    db.session.commit()
    
    needs_onboarding, redirect_url = check_user_needs_onboarding(user)
    
    return jsonify({
        'success': True,
        'redirect': redirect_url if redirect_url else '/settings?tab=analyses'
    })




@auth_bp.route('/auth/forgot-password', methods=['POST'])
def auth_forgot_password():
    """Send password reset email"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400
    
    email = (data.get('email') or '').strip().lower()
    
    if not email or '@' not in email:
        return jsonify({'error': 'Please enter a valid email address.'}), 400
    
    # Always return success to prevent email enumeration
    user = User.query.filter_by(email=email).first()
    
    if user:
        # Create reset token using MagicLink
        link = MagicLink.create_link(email, expires_in_minutes=30)
        
        reset_url = url_for('auth.auth_reset_password_page', token=link.token, _external=True)
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 500px; margin: 0 auto; padding: 32px;">
            <h2 style="color: #1e293b;">Reset Your Password</h2>
            <p style="color: #475569; line-height: 1.6;">
                Hi {user.name or 'there'},<br><br>
                We received a request to reset your OfferWise password. Click the button below to set a new password.
            </p>
            <div style="text-align: center; margin: 32px 0;">
                <a href="{reset_url}" style="background: #3b82f6; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">
                    Reset Password
                </a>
            </div>
            <p style="color: #94a3b8; font-size: 13px;">
                This link expires in 30 minutes. If you did not request this, you can safely ignore this email.
            </p>
        </div>
        """
        
        try:
            send_email(email, "Reset Your OfferWise Password", html_content)
            logging.info(f"Password reset email sent to {email}")
        except Exception as e:
            logging.error(f"Failed to send reset email: {e}")
    
    return jsonify({
        'success': True,
        'message': 'If an account exists with this email, you will receive a password reset link shortly.'
    })




@auth_bp.route('/auth/reset-password/<token>')
def auth_reset_password_page(token):
    """Show password reset form"""
    link = MagicLink.query.filter_by(token=token).first()
    
    if not link or not link.is_valid():
        return send_from_directory('static', 'reset-password.html')
    
    return send_from_directory('static', 'reset-password.html')




@auth_bp.route('/auth/reset-password/<token>', methods=['POST'])
def auth_reset_password(token):
    """Process password reset"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400
    
    password = data.get('password', '')
    
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters.'}), 400
    
    link = MagicLink.query.filter_by(token=token).first()
    
    if not link or not link.is_valid():
        return jsonify({'error': 'This reset link has expired. Please request a new one.'}), 400
    
    user = User.query.filter_by(email=link.email).first()
    if not user:
        return jsonify({'error': 'Account not found.'}), 404
    
    user.set_password(password)
    if not user.auth_provider or user.auth_provider in ('google', 'facebook', 'apple'):
        user.auth_provider = 'email'  # Now they can use email login too
    link.mark_used()
    db.session.commit()
    
    login_user(user)
    user.last_login = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'redirect': '/settings?tab=analyses',
        'message': 'Password reset successfully!'
    })



@auth_bp.route('/login/apple')
def login_apple():
    """Initiate Apple OAuth login"""
    if not apple:
        return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Apple Login - Configuration Required</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                    color: #e2e8f0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 20px;
                }
                .container {
                    max-width: 600px;
                    background: #1e293b;
                    border-radius: 16px;
                    padding: 40px;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
                    border: 1px solid #334155;
                }
                h1 { color: #f1f5f9; margin-top: 0; }
                .message { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 20px; margin: 20px 0; }
                .info { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 8px; padding: 20px; margin: 20px 0; font-size: 14px; line-height: 1.6; }
                .button { display: inline-block; padding: 12px 24px; background: linear-gradient(135deg, #0ea5e9 0%, #06b6d4 100%); color: white; text-decoration: none; border-radius: 8px; font-weight: 600; margin-top: 20px; }
                code { background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🍎 Apple Login Configuration Required</h1>
                <div class="message">
                    <strong>⚠️ Apple OAuth is not yet configured for this application.</strong>
                </div>
                <div class="info">
                    <p><strong>For the application administrator:</strong></p>
                    <p>To enable Apple login, please configure the following environment variables in your Render dashboard:</p>
                    <ul>
                        <li><code>APPLE_CLIENT_ID</code> - Your Apple Service ID</li>
                        <li><code>APPLE_CLIENT_SECRET</code> - Your Apple Private Key</li>
                    </ul>
                    <p><strong>Setup instructions:</strong></p>
                    <ol>
                        <li>Visit <a href="https://developer.apple.com" target="_blank" style="color: #60a5fa;">developer.apple.com</a></li>
                        <li>Create an App ID and Service ID</li>
                        <li>Configure "Sign in with Apple"</li>
                        <li>Generate a private key</li>
                        <li>Add credentials to Render environment variables</li>
                        <li>Redeploy the application</li>
                    </ol>
                </div>
                <a href="/login" class="button">← Back to Login</a>
            </div>
        </body>
        </html>
        ''')
    redirect_uri = url_for('auth.apple_callback', _external=True)
    return apple.authorize_redirect(redirect_uri)



@auth_bp.route('/auth/apple/callback')
def apple_callback():
    """Handle Apple OAuth callback"""
    try:
        # Get the token from Apple
        token = apple.authorize_access_token()
        
        # Get user info from Apple
        user_info = token.get('userinfo')
        if not user_info:
            resp = apple.get('https://appleid.apple.com/auth/userinfo')
            user_info = resp.json()
        
        email = user_info.get('email')
        apple_id = user_info.get('sub')
        
        # Apple may not provide name on subsequent logins
        name = None
        if 'name' in user_info:
            name_obj = user_info.get('name', {})
            if isinstance(name_obj, dict):
                first = name_obj.get('firstName', '')
                last = name_obj.get('lastName', '')
                name = f"{first} {last}".strip()
        
        if not email:
            flash('Could not get email from Apple. Please try again.', 'error')
            return redirect(url_for('auth.login_page'))
        
        # BLOCK CHECK: Prevent accounts that have been deleted 3+ times
        if EmailRegistry.is_blocked(email):
            logging.warning(f"🚫 BLOCKED: {email} has been deleted 3+ times — Apple signup denied")
            flash('This email address has been blocked due to repeated account deletions. Please contact support.', 'error')
            return redirect(url_for('auth.login_page'))
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Existing user - just update Apple ID if needed
            if not user.apple_id:
                user.apple_id = apple_id
                if not user.auth_provider or user.auth_provider == 'email':
                    user.auth_provider = 'apple'

            # FREE USER WITH 0 CREDITS: Restore 1 free credit if never paid and no analyses
            if user.analysis_credits <= 0 and not user.stripe_customer_id:
                apple_analysis_count = db.session.query(Analysis).join(Property).filter(Property.user_id == user.id).count()
                if apple_analysis_count == 0:
                    user.analysis_credits = 1
                    logging.info(f"🔄 Restored 1 free credit for {email} (Apple login, 0 credits, never paid)")

                db.session.commit()
        else:
            # New user signup - check email registry for credit eligibility
            logging.info(f"🆕 New user signup: {email}")
            
            # Register email and check credit eligibility
            email_registry, is_new_email = EmailRegistry.register_email(email)
            can_receive_credit, reason = EmailRegistry.can_receive_free_credit(email)
            
            if can_receive_credit:
                # Give free credit
                analysis_credits = 1
                EmailRegistry.give_free_credit(email)
                logging.info(f"✅ Giving free credit to {email} (reason: {reason})")
            else:
                # No free credit
                analysis_credits = 0
                logging.warning(f"❌ No free credit for {email} (reason: {reason})")
                
                if reason == "abuse_flagged":
                    logging.warning(f"🚨 ABUSE FLAG: {email} is flagged for credit abuse")
                elif reason == "already_received":
                    logging.info(f"ℹ️  {email} already received free credit previously")
            
            # Create new user account
            user = User(
                email=email,
                name=name or email.split('@')[0],  # Fallback to email prefix if no name
                apple_id=apple_id,
                auth_provider='apple',
                tier='free',
                subscription_status='active',
                analysis_credits=analysis_credits
            )
            
            db.session.add(user)
            db.session.commit()
            
            logging.info(f"👤 Created user account with {analysis_credits} credit(s)")
        

            # Inspector report attribution — did they arrive from a shared report?
            _share_token = session.pop('inspector_share_token', None)
            if _share_token:
                try:
                    from models import InspectorReport, Inspector
                    _rpt = InspectorReport.query.filter_by(share_token=_share_token).first()
                    if _rpt and not _rpt.buyer_registered:
                        _rpt.buyer_registered = True
                        _rpt.user_id = user.id
                        if hasattr(_rpt, 'buyer_registered_at'):
                            _rpt.buyer_registered_at = datetime.utcnow()
                        _insp = Inspector.query.get(_rpt.inspector_id)
                        if _insp:
                            _insp.total_buyers_converted = (_insp.total_buyers_converted or 0) + 1
                        db.session.commit()
                        logging.info(f"📊 Inspector attribution: report→buyer {user.email} via {_share_token[:8]}")
                except Exception as _ae:
                    logging.warning(f"Inspector attribution error: {_ae}")
        # Log the user in
        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        # Check onboarding status and get destination
        needs_onboarding, redirect_url = check_user_needs_onboarding(user)
        
        if needs_onboarding:
            # User needs to complete preferences or legal
            logging.info(f"🆕 New Apple user {user.id} needs onboarding - redirecting to {redirect_url}")
            return redirect(redirect_url)
        
        # Onboarding complete - redirect_url contains final destination
        if redirect_url:
            logging.info(f"✅ Apple user {user.id} onboarding complete - sending to {redirect_url}")
            return redirect(redirect_url)
        
        # Fallback to dashboard
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        logging.error(f"Apple OAuth error: {e}")
        flash('An error occurred during Apple login. Please try again.', 'error')
        return redirect(url_for('auth.login_page'))



@auth_bp.route('/login/facebook')
def login_facebook():
    """Initiate Facebook OAuth login"""
    if not facebook:
        return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Facebook Login - Configuration Required</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                    color: #e2e8f0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 20px;
                }
                .container {
                    max-width: 600px;
                    background: #1e293b;
                    border-radius: 16px;
                    padding: 40px;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
                    border: 1px solid #334155;
                }
                h1 { color: #f1f5f9; margin-top: 0; }
                .message { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 20px; margin: 20px 0; }
                .info { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 8px; padding: 20px; margin: 20px 0; font-size: 14px; line-height: 1.6; }
                .button { display: inline-block; padding: 12px 24px; background: linear-gradient(135deg, #0ea5e9 0%, #06b6d4 100%); color: white; text-decoration: none; border-radius: 8px; font-weight: 600; margin-top: 20px; }
                code { background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
                a { color: #60a5fa; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📘 Facebook Login Configuration Required</h1>
                <div class="message">
                    <strong>⚠️ Facebook OAuth is not yet configured for this application.</strong>
                </div>
                <div class="info">
                    <p><strong>For the application administrator:</strong></p>
                    <p>To enable Facebook login, please configure the following environment variables in your Render dashboard:</p>
                    <ul>
                        <li><code>FACEBOOK_CLIENT_ID</code> - Your Facebook App ID</li>
                        <li><code>FACEBOOK_CLIENT_SECRET</code> - Your Facebook App Secret</li>
                    </ul>
                    <p><strong>Setup instructions:</strong></p>
                    <ol>
                        <li>Visit <a href="https://developers.facebook.com/apps" target="_blank">Facebook Developers</a></li>
                        <li>Create a new app or select existing app</li>
                        <li>Add "Facebook Login" product</li>
                        <li>Get App ID and App Secret from Settings -> Basic</li>
                        <li>Configure Valid OAuth Redirect URIs to include: <code>https://your-app.onrender.com/auth/facebook/callback</code></li>
                        <li>Add credentials to Render environment variables</li>
                        <li>Redeploy the application</li>
                    </ol>
                    <p><strong>⚡ Quick Setup:</strong> In Render dashboard -> Environment -> Add:</p>
                    <ul>
                        <li><code>FACEBOOK_CLIENT_ID</code> = your_app_id_here</li>
                        <li><code>FACEBOOK_CLIENT_SECRET</code> = your_app_secret_here</li>
                    </ul>
                </div>
                <a href="/login" class="button">← Back to Login</a>
            </div>
        </body>
        </html>
        ''')
    redirect_uri = url_for('auth.facebook_callback', _external=True)
    return facebook.authorize_redirect(redirect_uri)



@auth_bp.route('/auth/facebook/callback')
def facebook_callback():
    """Handle Facebook OAuth callback"""
    try:
        # Get the token from Facebook
        facebook.authorize_access_token()
        
        # Get user info from Facebook
        resp = facebook.get('me?fields=id,name,email')
        user_info = resp.json()
        
        email = user_info.get('email')
        name = user_info.get('name')
        facebook_id = user_info.get('id')
        
        if not email:
            flash('Could not get email from Facebook. Please try again.', 'error')
            return redirect(url_for('auth.login_page'))
        
        # BLOCK CHECK: Prevent accounts that have been deleted 3+ times
        if EmailRegistry.is_blocked(email):
            logging.warning(f"🚫 BLOCKED: {email} has been deleted 3+ times — Facebook signup denied")
            flash('This email address has been blocked due to repeated account deletions. Please contact support.', 'error')
            return redirect(url_for('auth.login_page'))
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Existing user - just update Facebook ID if needed
            if not user.facebook_id:
                user.facebook_id = facebook_id
                if not user.auth_provider or user.auth_provider == 'email':
                    user.auth_provider = 'facebook'
            
            # DEVELOPER ACCOUNT: Ensure unlimited credits on every login
            # Uses global DEVELOPER_EMAILS
            is_developer = email.lower() in DEVELOPER_EMAILS
            
            if is_developer:
                # Boost to 500 if below
                if user.analysis_credits < 500:
                    old_credits = user.analysis_credits
                    user.analysis_credits = 500
                    user.tier = 'enterprise'
                    logging.info(f"👑 DEVELOPER LOGIN: Boosted credits {old_credits} -> 500")
                    logging.info("👑 DEVELOPER LOGIN: Set tier to enterprise")
            

            # FREE USER WITH 0 CREDITS: Restore 1 free credit if never paid and no analyses
            if not is_developer and user.analysis_credits <= 0 and not user.stripe_customer_id:
                fb_analysis_count = db.session.query(Analysis).join(Property).filter(Property.user_id == user.id).count()
                if fb_analysis_count == 0:
                    user.analysis_credits = 1
                    logging.info(f"🔄 Restored 1 free credit for {email} (Facebook login, 0 credits, never paid)")

            db.session.commit()
        else:
            # New user signup - check email registry for credit eligibility
            logging.info(f"🆕 New user signup: {email}")
            
            # Register email and check credit eligibility
            email_registry, is_new_email = EmailRegistry.register_email(email)
            can_receive_credit, reason = EmailRegistry.can_receive_free_credit(email)
            
            if can_receive_credit:
                # Give free credit
                analysis_credits = 1
                EmailRegistry.give_free_credit(email)
                logging.info(f"✅ Giving free credit to {email} (reason: {reason})")
            else:
                # No free credit
                analysis_credits = 0
                logging.warning(f"❌ No free credit for {email} (reason: {reason})")
                
                if reason == "abuse_flagged":
                    logging.warning(f"🚨 ABUSE FLAG: {email} is flagged for credit abuse")
                elif reason == "already_received":
                    logging.info(f"ℹ️  {email} already received free credit previously")
            
            # Create new user account
            user = User(
                email=email,
                name=name,
                facebook_id=facebook_id,
                auth_provider='facebook',
                tier='free',
                subscription_status='active',
                analysis_credits=analysis_credits
            )
            
            db.session.add(user)
            db.session.commit()
            
            logging.info(f"👤 Created Facebook user account with {analysis_credits} credit(s)")
            
            # 📧 Send welcome email to new user
            try:
                send_welcome_email(user.email, user.name or 'there')
                logging.info(f"📧 Welcome email sent to {user.email}")
            except Exception as e:
                logging.warning(f"📧 Could not send welcome email: {e}")
        

            # Inspector report attribution — did they arrive from a shared report?
            _share_token = session.pop('inspector_share_token', None)
            if _share_token:
                try:
                    from models import InspectorReport, Inspector
                    _rpt = InspectorReport.query.filter_by(share_token=_share_token).first()
                    if _rpt and not _rpt.buyer_registered:
                        _rpt.buyer_registered = True
                        _rpt.user_id = user.id
                        if hasattr(_rpt, 'buyer_registered_at'):
                            _rpt.buyer_registered_at = datetime.utcnow()
                        _insp = Inspector.query.get(_rpt.inspector_id)
                        if _insp:
                            _insp.total_buyers_converted = (_insp.total_buyers_converted or 0) + 1
                        db.session.commit()
                        logging.info(f"📊 Inspector attribution: report→buyer {user.email} via {_share_token[:8]}")
                except Exception as _ae:
                    logging.warning(f"Inspector attribution error: {_ae}")
        # Log the user in
        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        # Check onboarding status and get destination
        needs_onboarding, redirect_url = check_user_needs_onboarding(user)
        
        if needs_onboarding:
            # User needs to complete preferences or legal
            logging.info(f"🆕 New Facebook user {user.id} needs onboarding - redirecting to {redirect_url}")
            return redirect(redirect_url)
        
        # Onboarding complete - redirect_url contains final destination
        if redirect_url:
            logging.info(f"✅ Facebook user {user.id} onboarding complete - sending to {redirect_url}")
            return redirect(redirect_url)
        
        # Fallback to dashboard
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        logging.error(f"Facebook OAuth error: {e}")
        flash('An error occurred during Facebook login. Please try again.', 'error')
        return redirect(url_for('auth.login_page'))


# ── Magic Link Login (passwordless) ─────────────────────────────────────────
# Used by /internachi landing page for new inspector signups.
# Flow: POST /api/auth/magic-link → creates user if needed → sends email with token
#       GET  /auth/magic/<token>  → validates token → logs user in → redirects

@auth_bp.route('/api/auth/magic-link', methods=['POST'])
def send_magic_link():
    """
    Create a passwordless login link and email it to the user.
    Creates a new account if the email doesn't exist yet.

    POST body:
        email    (str, required)
        name     (str, optional) — used for new account creation
        redirect (str, optional) — where to send after login, default /inspector-onboarding
    """
    import secrets as _secrets
    data = request.get_json(silent=True) or {}
    email   = (data.get('email') or '').strip().lower()
    name    = (data.get('name') or '').strip()[:120]
    redirect_to = (data.get('redirect') or '/inspector-onboarding').strip()

    # Only allow safe internal redirects
    if not redirect_to.startswith('/'):
        redirect_to = '/inspector-onboarding'

    if not email or '@' not in email or '.' not in email:
        return jsonify({'error': 'Please enter a valid email address.'}), 400

    try:
        # Find or create the user
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                email=email,
                name=name or email.split('@')[0],
                auth_provider='magic_link',
                analysis_credits=3,
                tier='free',
                subscription_plan='inspector_free',
            )
            db.session.add(user)
            db.session.flush()  # get user.id

            # Track in EmailRegistry so referral/free-credit logic works
            try:
                reg = EmailRegistry.query.filter_by(email=email).first()
                if not reg:
                    reg = EmailRegistry(email=email)
                    db.session.add(reg)
                reg.has_received_free_credit = True
                reg.free_credit_given_at = datetime.utcnow()
            except Exception:
                pass

            try:
                user.generate_referral_code()
            except Exception:
                pass

            db.session.commit()
            logging.info(f"✨ New user created via magic link: {email}")
        else:
            logging.info(f"🔗 Magic link requested for existing user: {email}")

        # Create a magic link token (60 min expiry — longer than password reset)
        token = MagicLink.create_link(email, expires_in_minutes=60)

        # Build the login URL
        host = request.host_url.rstrip('/')
        login_url = f"{host}/auth/magic/{token.token}?redirect={redirect_to}"

        # Send the email
        html = f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;background:#ffffff;">
          <div style="font-size:22px;font-weight:700;margin-bottom:4px;">
            Offer<span style="color:#ea580c;">Wise</span> AI
          </div>
          <div style="font-size:11px;color:#94a3b8;margin-bottom:32px;font-family:monospace;letter-spacing:.05em;">
            EXCLUSIVE INTERNACHI MEMBER ACCESS
          </div>

          <h2 style="font-size:20px;font-weight:700;color:#0f172a;margin-bottom:12px;line-height:1.3;">
            Your Inspector Portal is one click away.
          </h2>
          <p style="font-size:14px;color:#475569;line-height:1.65;margin-bottom:28px;">
            Click the button below to sign in to OfferWise and activate your InterNACHI member plan.
            This link expires in 60 minutes.
          </p>

          <a href="{login_url}"
             style="display:inline-block;padding:14px 32px;background:#ea580c;color:#ffffff;
                    text-decoration:none;border-radius:8px;font-size:15px;font-weight:700;
                    letter-spacing:-.01em;">
            Sign In to OfferWise →
          </a>

          <div style="margin-top:32px;padding-top:20px;border-top:1px solid #e2e8f0;
                      font-size:12px;color:#94a3b8;line-height:1.6;">
            If you didn't request this, you can safely ignore this email.<br>
            This link works once and expires in 60 minutes.<br><br>
            OfferWise AI · <a href="https://www.getofferwise.ai" style="color:#ea580c;">getofferwise.ai</a>
          </div>
        </div>
        """

        send_email(email, "Sign in to OfferWise — Your InterNACHI Portal", html)
        logging.info(f"📧 Magic link email sent to {email}")

        return jsonify({
            'success': True,
            'message': 'Check your email for a sign-in link — it expires in 60 minutes.'
        })

    except Exception as e:
        logging.error(f"Magic link error: {e}")
        return jsonify({'error': 'Could not send sign-in link. Please try again.'}), 500


@auth_bp.route('/auth/magic/<token>')
def consume_magic_link(token):
    """
    Validate a magic link token, log the user in, and redirect.
    Query param ?redirect= controls final destination (default /inspector-onboarding).
    """
    redirect_to = request.args.get('redirect', '/inspector-onboarding').strip()
    if not redirect_to.startswith('/'):
        redirect_to = '/inspector-onboarding'

    link = MagicLink.query.filter_by(token=token).first()

    if not link or not link.is_valid():
        # Expired or already used — send to login with a clear message
        return redirect('/login?error=link_expired')

    # Find the user
    user = User.query.filter_by(email=link.email).first()
    if not user:
        return redirect('/login?error=account_not_found')

    # Mark token used before logging in
    link.mark_used()

    # Log the user in
    login_user(user, remember=True)
    user.last_login = datetime.utcnow()
    db.session.commit()

    logging.info(f"✅ Magic link login: user {user.id} ({user.email}) → {redirect_to}")

    return redirect(redirect_to)

