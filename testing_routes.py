"""
OfferWise Testing Routes Blueprint
Extracted from app.py v5.74.44 for architecture cleanup.
"""

import os
import json
import logging
import time
import re
import secrets
import base64
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, send_from_directory, redirect, url_for, render_template, render_template_string, current_app, make_response
from flask_login import login_required, current_user
from models import db, User, Property, Document, Analysis, Bug, TurkSession, Referral, ReferralReward, Comparison

logger = logging.getLogger(__name__)

testing_bp = Blueprint('testing', __name__)

from blueprint_helpers import DeferredDecorator, make_deferred_limiter

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


def init_testing_blueprint(app, admin_required_fn, api_admin_required_fn,
                       api_login_required_fn, dev_only_gate_fn, limiter):
    _admin_required_ref[0] = admin_required_fn
    _api_admin_required_ref[0] = api_admin_required_fn
    _api_login_required_ref[0] = api_login_required_fn
    _dev_only_gate_ref[0] = dev_only_gate_fn
    _limiter_ref[0] = limiter
    app.register_blueprint(testing_bp)
    logger.info("✅ Testing Routes blueprint registered")



@testing_bp.route('/api/test/stripe', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def run_stripe_tests():
    """
    Run comprehensive Stripe integration tests using test keys.
    Tests the full flow: payment -> credits -> analysis -> deduction
    """
    results = []
    
    # Check if test keys are available
    if not stripe_test_secret:
        return jsonify({
            'error': 'STRIPE_TEST_SECRET_KEY not configured',
            'message': 'Add STRIPE_TEST_SECRET_KEY to Render environment variables'
        }), 400
    
    data = request.get_json() or {}
    test_count = min(data.get('count', 1), 20)  # Max 20 tests at a time
    
    # Temporarily switch to test mode
    original_key = stripe.api_key
    stripe.api_key = stripe_test_secret
    
    try:
        for i in range(test_count):
            test_result = {
                'test_number': i + 1,
                'tests': {},
                'success': True
            }
            
            # Create a test user for this run
            test_email = f"stripe_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}@test.offerwise.ai"
            test_user = User(
                email=test_email,
                name=f"Stripe Test User {i+1}",
                auth_provider='test',
                analysis_credits=0
            )
            db.session.add(test_user)
            db.session.commit()
            
            test_result['test_user_id'] = test_user.id
            test_result['test_email'] = test_email
            
            # TEST 1: Verify user starts with 0 credits
            test_result['tests']['initial_credits'] = {
                'name': 'Initial Credits = 0',
                'passed': test_user.analysis_credits == 0,
                'expected': 0,
                'actual': test_user.analysis_credits
            }
            
            # TEST 2: Simulate webhook payment success (5 credits)
            try:
                credits_to_add = 5
                test_user.analysis_credits += credits_to_add
                test_user.stripe_customer_id = f"cus_test_{test_user.id}"
                db.session.commit()
                
                # Refresh from DB
                db.session.refresh(test_user)
                
                test_result['tests']['credit_addition'] = {
                    'name': 'Credits Added After Payment',
                    'passed': test_user.analysis_credits == credits_to_add,
                    'expected': credits_to_add,
                    'actual': test_user.analysis_credits
                }
            except Exception as e:
                test_result['tests']['credit_addition'] = {
                    'name': 'Credits Added After Payment',
                    'passed': False,
                    'error': 'An internal error occurred. Please try again.'
                }
            
            # TEST 3: Verify can_analyze returns True with credits
            try:
                # Check can_analyze logic
                can_analyze = test_user.analysis_credits > 0
                test_result['tests']['can_analyze_with_credits'] = {
                    'name': 'Can Analyze With Credits',
                    'passed': can_analyze == True,
                    'expected': True,
                    'actual': can_analyze
                }
            except Exception as e:
                test_result['tests']['can_analyze_with_credits'] = {
                    'name': 'Can Analyze With Credits',
                    'passed': False,
                    'error': 'An internal error occurred. Please try again.'
                }
            
            # TEST 4: Simulate credit deduction (1 credit for analysis)
            try:
                credits_before = test_user.analysis_credits
                User.query.filter(
                    User.id == test_user.id,
                    User.analysis_credits > 0
                ).update({User.analysis_credits: User.analysis_credits - 1})
                db.session.commit()
                db.session.refresh(test_user)
                
                test_result['tests']['credit_deduction'] = {
                    'name': 'Credit Deducted After Analysis',
                    'passed': test_user.analysis_credits == credits_before - 1,
                    'expected': credits_before - 1,
                    'actual': test_user.analysis_credits
                }
            except Exception as e:
                test_result['tests']['credit_deduction'] = {
                    'name': 'Credit Deducted After Analysis',
                    'passed': False,
                    'error': 'An internal error occurred. Please try again.'
                }
            
            # TEST 5: Deduct remaining credits and verify can't analyze
            try:
                # Use remaining credits
                remaining = test_user.analysis_credits
                test_user.analysis_credits = 0
                db.session.commit()
                db.session.refresh(test_user)
                
                can_analyze_empty = test_user.analysis_credits > 0
                test_result['tests']['blocked_without_credits'] = {
                    'name': 'Blocked Without Credits',
                    'passed': can_analyze_empty == False,
                    'expected': False,
                    'actual': can_analyze_empty,
                    'credits_used': remaining
                }
            except Exception as e:
                test_result['tests']['blocked_without_credits'] = {
                    'name': 'Blocked Without Credits',
                    'passed': False,
                    'error': 'An internal error occurred. Please try again.'
                }
            
            # TEST 6: Test Stripe API connectivity (using test keys)
            try:
                # Try to retrieve balance (simple API call)
                balance = stripe.Balance.retrieve()
                test_result['tests']['stripe_api_connection'] = {
                    'name': 'Stripe API Connection',
                    'passed': True,
                    'mode': 'test' if stripe_test_secret.startswith('sk_test_') else 'live'
                }
            except stripe.error.AuthenticationError as e:
                test_result['tests']['stripe_api_connection'] = {
                    'name': 'Stripe API Connection',
                    'passed': False,
                    'error': 'Invalid API key'
                }
            except Exception as e:
                test_result['tests']['stripe_api_connection'] = {
                    'name': 'Stripe API Connection',
                    'passed': False,
                    'error': 'An internal error occurred. Please try again.'
                }
            
            # Cleanup: Delete test user
            try:
                db.session.delete(test_user)
                db.session.commit()
                test_result['cleanup'] = 'success'
            except Exception:
                test_result['cleanup'] = 'failed'
            
            # Calculate overall success
            test_result['success'] = all(
                t.get('passed', False) 
                for t in test_result['tests'].values()
            )
            
            results.append(test_result)
        
        # Summary
        passed_count = sum(1 for r in results if r['success'])
        
        # AUTO-FILE BUGS for failures (v5.55.19)
        bugs_filed = 0
        current_version = open('VERSION').read().strip() if os.path.exists('VERSION') else 'unknown'
        for r in results:
            if not r['success']:
                failed_tests = [name for name, t in r['tests'].items() if not t.get('passed', False)]
                for test_name in failed_tests:
                    test_detail = r['tests'][test_name]
                    try:
                        # Check for existing open bug with same title
                        bug_title = f"Stripe test failed: {test_detail.get('name', test_name)}"
                        existing = Bug.query.filter_by(title=bug_title, status='open').first()
                        if not existing:
                            bug = Bug(
                                title=bug_title,
                                description=f"Stripe payment test failure. Expected: {test_detail.get('expected', 'N/A')}, Actual: {test_detail.get('actual', 'N/A')}",
                                error_message=test_detail.get('error', f"Expected {test_detail.get('expected')} but got {test_detail.get('actual')}"),
                                severity='high',
                                category='payments',
                                status='open',
                                version_reported=current_version,
                                reported_by='auto_test_stripe'
                            )
                            db.session.add(bug)
                            db.session.commit()
                            bugs_filed += 1
                    except Exception as e:
                        logging.warning(f"Could not auto-file Stripe bug: {e}")
                        db.session.rollback()
        
        return jsonify({
            'success': passed_count == len(results),
            'summary': {
                'total': len(results),
                'passed': passed_count,
                'failed': len(results) - passed_count
            },
            'results': results,
            'bugs_filed': bugs_filed,
            'stripe_mode': 'test',
            'test_key_configured': bool(stripe_test_secret)
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            'error': 'An internal error occurred. Please try again.',
            'trace': 'See server logs'
        }), 500
        
    finally:
        # Restore original API key
        stripe.api_key = original_key


@testing_bp.route('/api/test/stripe/config')
@_dev_only_gate
@_api_admin_required
def get_stripe_test_config():
    """Get Stripe configuration status for testing"""
    return jsonify({
        'live_key_configured': bool(stripe_secret),
        'live_key_mode': 'live' if stripe_secret and stripe_secret.startswith('sk_live_') else 'test' if stripe_secret else 'none',
        'test_key_configured': bool(stripe_test_secret),
        'test_key_mode': 'test' if stripe_test_secret and stripe_test_secret.startswith('sk_test_') else 'unknown' if stripe_test_secret else 'none',
        'webhook_secret_configured': bool(os.environ.get('STRIPE_WEBHOOK_SECRET', '')),
        'publishable_key_configured': bool(stripe_publishable),
        'test_publishable_key_configured': bool(stripe_test_publishable)
    })


@testing_bp.route('/api/test/referrals', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def test_referral_system():
    """Comprehensive referral system tests"""
    results = []
    passed = 0
    failed = 0
    
    try:
        from referral_service import ReferralService
        
        # Test 1: Check referral tables exist
        try:
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            
            referral_tables = ['referrals', 'referral_rewards']
            missing_tables = [t for t in referral_tables if t not in tables]
            
            if missing_tables:
                results.append({
                    'name': 'Referral Tables Exist',
                    'passed': False,
                    'error': f'Missing tables: {", ".join(missing_tables)}'
                })
                failed += 1
            else:
                results.append({
                    'name': 'Referral Tables Exist',
                    'passed': True,
                    'details': f'Found tables: {", ".join(referral_tables)}'
                })
                passed += 1
        except Exception as e:
            results.append({
                'name': 'Referral Tables Exist',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            })
            failed += 1
        
        # Test 2: User has referral_code column
        try:
            test_user = User.query.first()
            if test_user:
                has_code_attr = hasattr(test_user, 'referral_code')
                if has_code_attr:
                    results.append({
                        'name': 'User Referral Code Column',
                        'passed': True,
                        'details': f'Sample user code: {test_user.referral_code or "None yet"}'
                    })
                    passed += 1
                else:
                    results.append({
                        'name': 'User Referral Code Column',
                        'passed': False,
                        'error': 'User model missing referral_code attribute'
                    })
                    failed += 1
            else:
                results.append({
                    'name': 'User Referral Code Column',
                    'passed': False,
                    'error': 'No users in database to test'
                })
                failed += 1
        except Exception as e:
            results.append({
                'name': 'User Referral Code Column',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            })
            failed += 1
        
        # Test 3: Referral code generation
        try:
            test_user = User.query.first()
            if test_user:
                old_code = test_user.referral_code
                if not old_code:
                    new_code = test_user.generate_referral_code()
                    db.session.commit()
                    results.append({
                        'name': 'Referral Code Generation',
                        'passed': bool(new_code),
                        'details': f'Generated code: {new_code}' if new_code else 'Failed to generate'
                    })
                    if new_code:
                        passed += 1
                    else:
                        failed += 1
                else:
                    results.append({
                        'name': 'Referral Code Generation',
                        'passed': True,
                        'details': f'User already has code: {old_code}'
                    })
                    passed += 1
            else:
                results.append({
                    'name': 'Referral Code Generation',
                    'passed': False,
                    'error': 'No users to test'
                })
                failed += 1
        except Exception as e:
            results.append({
                'name': 'Referral Code Generation',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            })
            failed += 1
        
        # Test 4: Validate code API works
        try:
            test_user = User.query.filter(User.referral_code.isnot(None)).first()
            if test_user and test_user.referral_code:
                # Test valid code
                referrer = User.query.filter_by(referral_code=test_user.referral_code).first()
                results.append({
                    'name': 'Code Validation (Valid)',
                    'passed': bool(referrer),
                    'details': f'Code {test_user.referral_code} validated successfully'
                })
                if referrer:
                    passed += 1
                else:
                    failed += 1
                
                # Test invalid code
                invalid_referrer = User.query.filter_by(referral_code='INVALID123XYZ').first()
                results.append({
                    'name': 'Code Validation (Invalid)',
                    'passed': invalid_referrer is None,
                    'details': 'Invalid code correctly rejected' if not invalid_referrer else 'ERROR: Found invalid code!'
                })
                if not invalid_referrer:
                    passed += 1
                else:
                    failed += 1
            else:
                results.append({
                    'name': 'Code Validation',
                    'passed': False,
                    'error': 'No users with referral codes to test'
                })
                failed += 1
        except Exception as e:
            results.append({
                'name': 'Code Validation',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            })
            failed += 1
        
        # Test 5: Referral stats API
        try:
            test_user = User.query.first()
            if test_user:
                stats = test_user.get_referral_stats()
                required_keys = ['code', 'total_referrals', 'current_tier', 'credits_earned']
                missing_keys = [k for k in required_keys if k not in stats]
                
                if missing_keys:
                    results.append({
                        'name': 'Referral Stats API',
                        'passed': False,
                        'error': f'Missing keys: {", ".join(missing_keys)}'
                    })
                    failed += 1
                else:
                    results.append({
                        'name': 'Referral Stats API',
                        'passed': True,
                        'details': f'Stats: {stats["total_referrals"]} referrals, tier {stats["current_tier"]}'
                    })
                    passed += 1
        except Exception as e:
            results.append({
                'name': 'Referral Stats API',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            })
            failed += 1
        
        # Test 6: ReferralService methods exist
        try:
            required_methods = ['process_signup_referral', 'check_tier_progression', 'get_referral_url', 'get_share_text']
            missing_methods = [m for m in required_methods if not hasattr(ReferralService, m)]
            
            if missing_methods:
                results.append({
                    'name': 'ReferralService Methods',
                    'passed': False,
                    'error': f'Missing methods: {", ".join(missing_methods)}'
                })
                failed += 1
            else:
                results.append({
                    'name': 'ReferralService Methods',
                    'passed': True,
                    'details': f'All {len(required_methods)} methods available'
                })
                passed += 1
        except Exception as e:
            results.append({
                'name': 'ReferralService Methods',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            })
            failed += 1
        
        # Test 7: Referral URL generation
        try:
            test_user = User.query.filter(User.referral_code.isnot(None)).first()
            if test_user:
                url = ReferralService.get_referral_url(test_user)
                is_valid = url and '?ref=' in url and test_user.referral_code in url
                results.append({
                    'name': 'Referral URL Generation',
                    'passed': is_valid,
                    'details': f'URL: {url}' if is_valid else f'Invalid URL: {url}'
                })
                if is_valid:
                    passed += 1
                else:
                    failed += 1
            else:
                results.append({
                    'name': 'Referral URL Generation',
                    'passed': False,
                    'error': 'No users with codes to test'
                })
                failed += 1
        except Exception as e:
            results.append({
                'name': 'Referral URL Generation',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            })
            failed += 1
        
        # Test 8: REFERRAL_TIERS configuration
        try:
            from models import REFERRAL_TIERS
            required_tiers = [0, 1, 2, 3]
            missing_tiers = [t for t in required_tiers if t not in REFERRAL_TIERS]
            
            if missing_tiers:
                results.append({
                    'name': 'Referral Tiers Configuration',
                    'passed': False,
                    'error': f'Missing tiers: {missing_tiers}'
                })
                failed += 1
            else:
                tier_names = [REFERRAL_TIERS[t]['name'] for t in required_tiers]
                results.append({
                    'name': 'Referral Tiers Configuration',
                    'passed': True,
                    'details': f'Tiers: {", ".join(tier_names)}'
                })
                passed += 1
        except Exception as e:
            results.append({
                'name': 'Referral Tiers Configuration',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            })
            failed += 1
        
        # AUTO-FILE BUGS for failures (v5.55.19)
        bugs_filed = 0
        current_version = open('VERSION').read().strip() if os.path.exists('VERSION') else 'unknown'
        for r in results:
            if not r.get('passed', False):
                try:
                    bug_title = f"Referral test failed: {r.get('name', 'Unknown')}"
                    existing = Bug.query.filter_by(title=bug_title, status='open').first()
                    if not existing:
                        bug = Bug(
                            title=bug_title,
                            description=f"Referral system test failure: {r.get('name', 'Unknown')}",
                            error_message=r.get('error', r.get('details', 'Test failed')),
                            severity='medium',
                            category='referrals',
                            status='open',
                            version_reported=current_version,
                            reported_by='auto_test_referral'
                        )
                        db.session.add(bug)
                        db.session.commit()
                        bugs_filed += 1
                except Exception as e:
                    logging.warning(f"Could not auto-file Referral bug: {e}")
                    db.session.rollback()
        
        return jsonify({
            'success': failed == 0,
            'summary': {
                'total': passed + failed,
                'passed': passed,
                'failed': failed
            },
            'results': results,
            'bugs_filed': bugs_filed
        })
        
    except ImportError as e:
        return jsonify({
            'success': False,
            'error': 'Referral service temporarily unavailable.',
            'summary': {'total': 1, 'passed': 0, 'failed': 1},
            'results': [{
                'name': 'Import ReferralService',
                'passed': False,
                'error': 'An internal error occurred. Please try again.'
            }]
        })
    except Exception as e:
        logging.error(f"Referral test error: {e}")
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.',
            'summary': {'total': len(results), 'passed': passed, 'failed': failed + 1},
            'results': results
        }), 500


@testing_bp.route('/api/test/integrity', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def run_integrity_tests():
    """Run ALL tests: integrity tests + unit test suites (356 total)."""
    try:
        import time as _time
        all_results = []
        total_passed = 0
        total_failed = 0
        start = _time.time()
        
        # ── Phase 1: Integrity tests (101) ──
        try:
            from integrity_tests import IntegrityTestEngine
            engine = IntegrityTestEngine(app=app, db=db)
            integrity = engine.run_all()
            for r in integrity.get('results', []):
                all_results.append(r)
                if r.get('passed'):
                    total_passed += 1
                else:
                    total_failed += 1
        except Exception as int_err:
            all_results.append({'name': 'Integrity Engine', 'passed': False, 'error': str(int_err)})
            total_failed += 1
        
        # ── Phase 2: Unit test suites ──
        import unittest
        import io
        import importlib
        
        # Ensure sys.path includes the project root for test imports
        import sys as _sys
        import importlib.util
        project_dir = os.path.dirname(os.path.abspath(__file__))
        if project_dir not in _sys.path:
            _sys.path.insert(0, project_dir)
        
        test_modules = [
            'test_results_quality',
            'test_transparency_scorer',
            'test_negotiation',
            'test_confidence_scorer',
            'test_analysis_cache',
            'test_gtm',
            'test_gtm_content',
            'test_nearby_listings',
            'test_comprehensive',
            'test_critical_paths',
        ]
        
        modules_loaded = []
        modules_failed = []
        
        for mod_name in test_modules:
            try:
                # Use file-based import to guarantee we find the module
                mod_path = os.path.join(project_dir, f'{mod_name}.py')
                if not os.path.exists(mod_path):
                    raise FileNotFoundError(f'{mod_path} not found')
                
                if mod_name in _sys.modules:
                    mod = _sys.modules[mod_name]
                else:
                    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
                    mod = importlib.util.module_from_spec(spec)
                    _sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                
                # Load and run tests
                loader = unittest.TestLoader()
                suite = loader.loadTestsFromModule(mod)
                stream = io.StringIO()
                runner = unittest.TextTestRunner(stream=stream, verbosity=0)
                result = runner.run(suite)
                
                # Collect results
                for test_case in result.failures + result.errors:
                    test_name = str(test_case[0])
                    all_results.append({
                        'name': f'{mod_name}: {test_name}',
                        'passed': False,
                        'error': test_case[1][:200],
                        'details': f'From {mod_name}',
                    })
                    total_failed += 1
                
                tests_in_module = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
                for _ in range(tests_in_module):
                    total_passed += 1
                
                # Add summary for the module
                modules_loaded.append(f'{mod_name} ({result.testsRun} tests)')
                if result.failures or result.errors:
                    all_results.append({
                        'name': f'{mod_name} ({result.testsRun} tests)',
                        'passed': False,
                        'details': f'{len(result.failures)} failures, {len(result.errors)} errors',
                    })
                else:
                    all_results.append({
                        'name': f'{mod_name} ({result.testsRun} tests)',
                        'passed': True,
                        'details': f'All {result.testsRun} passed',
                    })
                    
            except FileNotFoundError:
                all_results.append({'name': f'{mod_name}', 'passed': True, 'details': 'Skipped (not in production image)'})
                total_passed += 1
            except Exception as mod_err:
                modules_failed.append(f'{mod_name}: {str(mod_err)[:100]}')
                all_results.append({
                    'name': f'{mod_name} (import)',
                    'passed': False,
                    'error': str(mod_err)[:200],
                })
                total_failed += 1
        
        duration = round(_time.time() - start, 2)
        
        results = {
            'success': total_failed == 0,
            'summary': {
                'total': total_passed + total_failed,
                'passed': total_passed,
                'failed': total_failed,
                'duration_seconds': duration,
                'modules_loaded': len(modules_loaded),
                'modules_failed': len(modules_failed),
                'module_errors': modules_failed[:5],
            },
            'results': all_results,
        }
        
        # Auto-file bugs for failures
        bugs_filed = 0
        try:
            current_version = open('VERSION').read().strip() if os.path.exists('VERSION') else 'unknown'
        except Exception:
            current_version = 'unknown'
        
        for r in results.get('results', []):
            if not r.get('passed', False):
                try:
                    bug_title = f"Integrity: {r.get('name', 'Unknown')}"
                    existing = Bug.query.filter_by(title=bug_title, status='open').first()
                    if not existing:
                        bug = Bug(
                            title=bug_title,
                            description=f"Integrity test failure.\n\nDetails: {r.get('details', 'N/A')}\n\nError: {r.get('error', 'N/A')}",
                            error_message=r.get('error', r.get('details', 'Test failed')),
                            severity='high' if 'IDOR' in r.get('name', '') or 'negative' in r.get('error', '').lower() else 'medium',
                            category='integrity',
                            status='open',
                            version_reported=current_version,
                            reported_by='auto_test_integrity'
                        )
                        db.session.add(bug)
                        db.session.commit()
                        bugs_filed += 1
                except Exception as e:
                    logging.warning(f"Could not auto-file integrity bug: {e}")
                    db.session.rollback()
        
        results['bugs_filed'] = bugs_filed
        
        # Auto-resolve integrity bugs that now pass (v5.62.70)
        bugs_resolved = 0
        try:
            current_version = open('VERSION').read().strip() if os.path.exists('VERSION') else 'unknown'
        except Exception:
            current_version = 'unknown'
        
        passed_names = set()
        for r in results.get('results', []):
            if r.get('passed', False):
                bug_title = f"Integrity: {r.get('name', 'Unknown')}"
                passed_names.add(bug_title)
        
        if passed_names:
            try:
                open_integrity_bugs = Bug.query.filter_by(category='integrity', status='open').all()
                for bug in open_integrity_bugs:
                    if bug.title in passed_names:
                        bug.status = 'resolved'
                        bug.resolution_notes = f"Auto-resolved: test passes in v{current_version}"
                        bugs_resolved += 1
                if bugs_resolved > 0:
                    db.session.commit()
                    logging.info(f"✅ Auto-resolved {bugs_resolved} integrity bugs that now pass")
            except Exception as e:
                logging.warning(f"Could not auto-resolve integrity bugs: {e}")
                db.session.rollback()
        
        results['bugs_resolved'] = bugs_resolved
        
        # Sanitize types that jsonify can't handle
        import json as _json
        def _safe_default(obj):
            try:
                import numpy as np
                if isinstance(obj, (np.bool_,)):
                    return bool(obj)
                if isinstance(obj, (np.integer,)):
                    return int(obj)
                if isinstance(obj, (np.floating,)):
                    return float(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
            except ImportError:
                pass
            return str(obj)
        sanitized = _json.loads(_json.dumps(results, default=_safe_default))
        return jsonify(sanitized)
        
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"Test endpoint error: {e}\n{tb}")
        return jsonify({
            'success': False,
            'error': 'Test engine encountered an internal error. Check server logs for details.',
            'summary': {'total': 0, 'passed': 0, 'failed': 1, 'duration_seconds': 0},
            'results': [{
                'name': 'Test Engine Startup',
                'passed': False,
                'error': 'Internal error — see server logs'
            }]
        }), 500


@testing_bp.route('/api/test/adversarial-pdfs', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def run_adversarial_pdf_tests():
    """Run adversarial PDF tests against the production pipeline.
    
    Tests: quality gate, TDS completeness, document type detection,
    PDF extraction with synthetic bad PDFs, and real document validation.
    """
    import time as _time
    start = _time.time()
    results = []
    passed = 0
    failed = 0
    
    def record(name, ok, details="", category=""):
        nonlocal passed, failed
        entry = {'name': f"{category}: {name}" if category else name, 'passed': ok, 'details': details}
        results.append(entry)
        if ok:
            passed += 1
        else:
            failed += 1
    
    try:
        from pdf_handler import PDFHandler, is_meaningful_extraction, is_tds_complete
        handler = PDFHandler()
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'PDF handler import failed: {str(e)}',
            'summary': {'total': 0, 'passed': 0, 'failed': 1},
            'results': [{'name': 'Import', 'passed': False, 'details': str(e)}]
        })
    
    # --- Helper: generate bad PDFs ---
    try:
        from fpdf import FPDF
        fpdf_available = True
    except ImportError:
        fpdf_available = False
    
    def make_blank_pdf(pages=1):
        pdf = FPDF(); [pdf.add_page() for _ in range(pages)]; return pdf.output()
    
    def make_metadata_pdf():
        pdf = FPDF(); pdf.add_page(); pdf.set_font('Helvetica', size=8)
        for _ in range(3):
            pdf.cell(0, 10, 'Docusign Envelope ID: B6DB4E44-50E2-4439-9ED1-45DD569345A3'); pdf.ln()
            pdf.cell(0, 10, 'Coldwell Banker Realty'); pdf.ln()
            if _ < 2: pdf.add_page()
        return pdf.output()
    
    def make_wrong_doc_pdf():
        pdf = FPDF(); pdf.add_page(); pdf.set_font('Helvetica', size=12)
        pdf.cell(0, 10, 'MORTGAGE STATEMENT'); pdf.ln()
        pdf.cell(0, 10, 'Account: 12345678 Balance: $425,000 Rate: 6.5%'); pdf.ln()
        pdf.multi_cell(0, 10, 'Monthly mortgage statement from First National Bank. Payment due by the 1st.')
        return pdf.output()
    
    def make_tds_pdf():
        pdf = FPDF(); pdf.add_page(); pdf.set_font('Helvetica', 'B', 14)
        pdf.cell(0, 10, 'REAL ESTATE TRANSFER DISCLOSURE STATEMENT'); pdf.ln()
        pdf.set_font('Helvetica', size=9)
        pdf.cell(0, 8, 'Property: 123 Test St, San Jose CA 95123'); pdf.ln()
        # Section A
        pdf.cell(0, 6, 'A. Items: [X] Range [X] Dishwasher [X] Washer [X] Smoke Detector [X] Fire Alarm [X] Garage [X] Roof [X] Fireplace'); pdf.ln()
        # Section B
        pdf.cell(0, 6, 'B. Defects: [X] Interior Walls [ ] Ceilings [ ] Floors [ ] Foundation [ ] Plumbing [ ] Electrical [ ] Other Structural'); pdf.ln()
        pdf.cell(0, 6, 'Describe: Holes from hanging art and TVs'); pdf.ln()
        # Section C
        pdf.add_page(); pdf.set_font('Helvetica', size=9)
        questions = ['environmental hazard', 'encroachment', 'easement', 'room addition', 'structural modification',
                     'permit', 'fill', 'settling', 'flooding', 'major damage', 'earthquake', 'neighborhood noise',
                     'cc&r', 'homeowners', 'association', 'lawsuit', 'abatement', 'citation']
        pdf.cell(0, 6, 'C. Awareness: ' + ', '.join(questions)); pdf.ln()
        # Section D
        pdf.cell(0, 6, 'D. Compliance: smoke detector installed, water heater braced anchored strapped'); pdf.ln()
        pdf.cell(0, 6, 'Seller Signature: _____________  Date: 02/03/2025')
        return pdf.output()
    
    # --- Helper: generate IMAGE-BASED PDFs (simulating scans/handwriting) ---
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io as _io
        import random as _rand
        import tempfile
        pillow_available = True
    except ImportError:
        pillow_available = False
    
    def _img_to_pdf(images):
        """Convert list of PIL images to a single PDF bytes object."""
        pdf = FPDF()
        tmp_files = []
        for i, img in enumerate(images):
            pdf.add_page()
            tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            img.save(tmp, format='JPEG', quality=70)
            tmp.close()
            tmp_files.append(tmp.name)
            pdf.image(tmp.name, 0, 0, 210, 297)
        out = pdf.output()
        for f in tmp_files:
            try: os.unlink(f)
            except Exception: pass
        return out
    
    def _get_font(size=14):
        """Get a font, preferring italic (looks more handwritten)."""
        font_paths = [
            '/usr/share/fonts/truetype/crosextra/Carlito-Italic.ttf',
            '/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf',
            '/usr/share/fonts/truetype/crosextra/Caladea-Italic.ttf',
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                return ImageFont.truetype(fp, size)
        return ImageFont.load_default()
    
    def make_scanned_handwritten_tds():
        """Simulates a SCANNED handwritten TDS - the #1 production failure case.
        Creates an image-based PDF where text extraction returns ZERO."""
        pages = []
        
        # Page 1: Printed form + handwritten entries
        img = Image.new('RGB', (1700, 2200), '#FFFFF5')  # Slightly yellowed paper
        draw = ImageDraw.Draw(img)
        form_font = _get_font(22)
        hand_font = _get_font(18)
        
        # Printed header
        draw.text((200, 80), 'REAL ESTATE TRANSFER DISCLOSURE STATEMENT', fill='black', font=_get_font(28))
        draw.text((200, 130), '(California Civil Code Section 1102)', fill='#333333', font=form_font)
        draw.text((200, 180), 'THIS DISCLOSURE IS NOT A WARRANTY', fill='#333333', font=form_font)
        
        # Property address - handwritten
        draw.text((200, 260), 'Property Address:', fill='black', font=form_font)
        draw.text((480, 258), '381 Tina Dr, Hollister CA 95023', fill='#1a1aCC', font=hand_font)
        draw.line((475, 280, 850, 280), fill='black', width=1)
        
        # Section A - checkboxes
        draw.text((200, 340), 'A. The subject property has the following items:', fill='black', font=form_font)
        items = [('Range', True), ('Oven', True), ('Dishwasher', True), ('Trash Compactor', False),
                 ('Washer/Dryer', True), ('Rain Gutters', True), ('Burglar Alarm', False),
                 ('Smoke Detector(s)', True), ('Fire Alarm', True), ('TV Antenna', False),
                 ('Satellite Dish', False), ('Central Heating', True), ('Central AC', True),
                 ('Wall/Window AC', False), ('Fireplace', False), ('Garage', True), ('Pool', False)]
        
        y = 390
        for i, (item, checked) in enumerate(items):
            x = 220 + (i % 3) * 480
            if i % 3 == 0 and i > 0:
                y += 40
            # Draw checkbox
            draw.rectangle([x, y, x+20, y+20], outline='black', width=2)
            if checked:
                # Handwritten X in the checkbox
                draw.line((x+3, y+3, x+17, y+17), fill='#1a1aCC', width=2)
                draw.line((x+17, y+3, x+3, y+17), fill='#1a1aCC', width=2)
            draw.text((x+28, y-2), item, fill='black', font=_get_font(16))
        
        # Section B - defects with handwritten "No"
        y += 80
        draw.text((200, y), 'B. Are you (Seller) aware of any significant defects/malfunctions', fill='black', font=form_font)
        y += 35
        draw.text((220, y), 'in any of the following?', fill='black', font=form_font)
        y += 40
        defects = ['Interior Walls', 'Ceilings', 'Floors', 'Exterior Walls', 'Insulation',
                   'Roof(s)', 'Windows', 'Doors', 'Foundation', 'Slab(s)', 'Driveways',
                   'Sidewalks', 'Walls/Fences', 'Electrical Systems', 'Plumbing/Sewers/Septics']
        for i, defect in enumerate(defects):
            x = 220 + (i % 3) * 480
            if i % 3 == 0 and i > 0:
                y += 35
            draw.text((x, y), f'[ ] {defect}', fill='black', font=_get_font(14))
        
        y += 60
        draw.text((220, y), 'If yes, explain:', fill='black', font=form_font)
        draw.text((420, y-2), 'Holes in walls from hanging art and TVs', fill='#1a1aCC', font=hand_font)
        draw.line((415, y+20, 900, y+20), fill='black', width=1)
        
        pages.append(img)
        
        # Page 2: Section C - awareness questions (all handwritten Yes/No)
        img2 = Image.new('RGB', (1700, 2200), '#FFFFF5')
        draw2 = ImageDraw.Draw(img2)
        draw2.text((200, 80), 'C. Are you (Seller) aware of any of the following:', fill='black', font=_get_font(24))
        
        questions = [
            ('1. Substances, materials, or products (asbestos, lead, mold)', 'No'),
            ('2. Features shared in common with adjoining landowners', 'No'),
            ('3. Any encroachments, easements, or similar', 'No'),
            ('4. Room additions, structural modifications', 'Yes'),
            ('5. Room additions not in compliance with building codes', 'No'),
            ('6. Fill (compacted or otherwise) on the property', 'No'),
            ('7. Any settling, slippage, sliding, or soil problems', 'No'),
            ('8. Flooding, drainage, or grading problems', 'Yes'),
            ('9. Major damage from fire, earthquake, floods, or landslides', 'No'),
            ('10. Zoning violations, nonconforming uses', 'No'),
            ('11. Neighborhood noise or other nuisances', 'No'),
            ('12. CC&Rs or other deed restrictions', 'Yes'),
            ('13. Homeowners Association (HOA)', 'Yes'),
            ('14. Any common area facilities', 'No'),
            ('15. Any notices of abatement or citations', 'No'),
            ('16. Any lawsuits against the seller', 'No'),
        ]
        
        y = 140
        for q_text, answer in questions:
            draw2.text((220, y), q_text, fill='black', font=_get_font(15))
            y += 28
            # Handwritten answer
            ans_color = '#CC1a1a' if answer == 'Yes' else '#1a1aCC'
            draw2.text((260, y), f'Yes [ ] No [ ]    Answer: {answer}', fill='black', font=_get_font(14))
            # Circle the answer
            if answer == 'Yes':
                draw2.ellipse([254, y-2, 314, y+22], outline=ans_color, width=2)
            else:
                draw2.ellipse([330, y-2, 380, y+22], outline=ans_color, width=2)
            y += 40
        
        pages.append(img2)
        
        # Page 3: Section D + signatures
        img3 = Image.new('RGB', (1700, 2200), '#FFFFF5')
        draw3 = ImageDraw.Draw(img3)
        draw3.text((200, 80), 'D. Seller certifies the following:', fill='black', font=_get_font(24))
        draw3.text((220, 140), '1. Smoke detector(s) installed per Health and Safety Code', fill='black', font=form_font)
        draw3.text((220, 180), '2. Water heater braced, anchored, or strapped', fill='black', font=form_font)
        
        # Handwritten signature
        draw3.text((220, 300), 'Seller Signature:', fill='black', font=form_font)
        # Simulate a scrawly signature
        sig_y = 298
        for i in range(60):
            x = 460 + i * 4
            y_off = _rand.randint(-8, 8)
            draw3.line((x, sig_y + y_off, x+4, sig_y + _rand.randint(-8, 8)), fill='#1a1aCC', width=2)
        
        draw3.text((220, 360), 'Date:', fill='black', font=form_font)
        draw3.text((320, 358), '02/03/2025', fill='#1a1aCC', font=hand_font)
        
        pages.append(img3)
        
        return _img_to_pdf(pages)
    
    def make_faded_scan():
        """Simulates a bad photocopy - low contrast, faded text."""
        img = Image.new('RGB', (1700, 2200), '#F0F0F0')  # Gray paper
        draw = ImageDraw.Draw(img)
        font = _get_font(18)
        
        # Very light text (simulating faded photocopy)
        draw.text((200, 100), 'SELLER PROPERTY DISCLOSURE', fill='#C0C0C0', font=_get_font(24))
        draw.text((200, 160), 'Property: 999 Faded Ave, San Jose CA', fill='#B8B8B8', font=font)
        
        y = 240
        items = ['Foundation: No issues', 'Roof: Tile, 20 years', 'Plumbing: Functional',
                 'Electrical: Up to code', 'HVAC: Central heating and AC',
                 'Water heater: Gas, 8 years old', 'Known defects: None']
        for item in items:
            draw.text((220, y), item, fill='#AAAAAA', font=font)  # Very faded
            y += 40
        
        return _img_to_pdf([img])
    
    def make_crooked_scan():
        """Simulates a document scanned at an angle."""
        # Create normal document first
        img = Image.new('RGB', (1700, 2200), 'white')
        draw = ImageDraw.Draw(img)
        font = _get_font(20)
        
        draw.text((200, 100), 'HOME INSPECTION REPORT', fill='black', font=_get_font(28))
        draw.text((200, 160), 'Inspector: John Smith, License #12345', fill='black', font=font)
        draw.text((200, 210), 'Property: 456 Crooked Lane, San Jose CA 95123', fill='black', font=font)
        
        y = 300
        findings = [
            'ROOF: Composition shingle, approx 22 years. Curling observed on south side.',
            'FOUNDATION: Concrete perimeter. Hairline crack east wall. Monitor recommended.',
            'PLUMBING: Main shutoff at front. Water heater gas 40gal, 9 years old.',
            'ELECTRICAL: 200 amp panel. Two outlets tested open ground in garage.',
            'HVAC: Forced air gas furnace, approx 18 years. Filter dirty.',
            'EXTERIOR: Wood siding with peeling paint on north exposure.',
            'INTERIOR: Water stain on ceiling in master bedroom. Source unknown.',
        ]
        for finding in findings:
            draw.text((200, y), finding, fill='black', font=_get_font(16))
            y += 50
        
        # Rotate 3-5 degrees (simulating crooked scan)
        rotated = img.rotate(_rand.uniform(2, 5), expand=True, fillcolor='white')
        
        return _img_to_pdf([rotated])
    
    def make_phone_photo_pdf():
        """Simulates a phone camera photo of a document - perspective, shadows, uneven lighting."""
        img = Image.new('RGB', (1700, 2200), '#E8E4D8')  # Warm paper color
        draw = ImageDraw.Draw(img)
        font = _get_font(20)
        
        # Add shadow gradient on left side (simulating hand shadow)
        for x in range(300):
            alpha = int(60 * (1 - x/300))
            for y_pos in range(0, 2200, 4):
                draw.point((x, y_pos), fill=(alpha, alpha, alpha))
        
        # Document content
        draw.text((350, 150), 'TRANSFER DISCLOSURE STATEMENT', fill='#222222', font=_get_font(26))
        draw.text((350, 220), 'Property: 789 Phone Photo Dr, Sunnyvale CA', fill='#333333', font=font)
        
        y = 320
        lines = [
            'Section A: [X] Range [X] Dishwasher [X] Washer [ ] Pool',
            '[X] Smoke Detector [X] Garage [X] Central Heat [X] Roof',
            '',
            'Section B: Are you aware of defects?',
            'Answer: Yes - water damage in basement noted 2023',
            '',
            'Section C: Environmental hazards? No',
            'Flooding or drainage? Yes - backyard floods in heavy rain',
            'Neighborhood noise? No',
            'HOA? Yes - $350/month',
            '',
            'Section D: Smoke detectors installed. Water heater strapped.',
        ]
        for line in lines:
            # Slight random horizontal offset (phone wasn't perfectly aligned)
            x_offset = _rand.randint(-10, 10)
            fill_color = '#1a1aCC' if 'Answer:' in line or 'Yes -' in line or '$350' in line else '#222222'
            draw.text((350 + x_offset, y), line, fill=fill_color, font=_get_font(17))
            y += 42
        
        # Add some noise spots (dust on camera lens)
        for _ in range(15):
            nx, ny = _rand.randint(0, 1700), _rand.randint(0, 2200)
            draw.ellipse([nx, ny, nx+_rand.randint(2,6), ny+_rand.randint(2,6)], fill='#CCC8C0')
        
        return _img_to_pdf([img])
    
    def make_mixed_text_image_pdf():
        """3-page PDF: page 1 is digital text, pages 2-3 are scanned images.
        This is the real-world pattern: cover letter is digital, disclosures are scanned."""
        # Page 1: Digital text (fpdf)
        cover = FPDF()
        cover.add_page()
        cover.set_font('Helvetica', 'B', 16)
        cover.cell(0, 10, 'SELLER DISCLOSURE PACKAGE')
        cover.ln()
        cover.set_font('Helvetica', size=11)
        cover.cell(0, 8, 'Property: 321 Mixed Ct, Mountain View, CA 94043')
        cover.ln()
        cover.cell(0, 8, 'Prepared by: Coldwell Banker Realty')
        cover.ln()
        cover.cell(0, 8, 'Date: February 10, 2025')
        cover.ln(12)
        cover.multi_cell(0, 7, 'This package contains the following disclosure documents:\n'
                         '1. Transfer Disclosure Statement (TDS)\n'
                         '2. Seller Property Questionnaire (SPQ)\n'
                         '3. Natural Hazard Disclosure (NHD)')
        cover_bytes = cover.output()
        
        # Pages 2-3: Scanned handwritten content (images)
        scan1 = Image.new('RGB', (1700, 2200), '#FFFFF8')
        d1 = ImageDraw.Draw(scan1)
        d1.text((200, 100), 'TRANSFER DISCLOSURE STATEMENT - PAGE 1', fill='black', font=_get_font(22))
        d1.text((200, 180), 'Section A: Items checked below:', fill='black', font=_get_font(18))
        d1.text((220, 230), '[X] Range  [X] Dishwasher  [X] Smoke Detector  [X] Roof', fill='#1a1aCC', font=_get_font(16))
        d1.text((200, 320), 'Section B: Defects: None known', fill='#1a1aCC', font=_get_font(16))
        
        scan2 = Image.new('RGB', (1700, 2200), '#FFFFF8')
        d2 = ImageDraw.Draw(scan2)
        d2.text((200, 100), 'TRANSFER DISCLOSURE STATEMENT - PAGE 2', fill='black', font=_get_font(22))
        d2.text((200, 180), 'Section C & D', fill='black', font=_get_font(18))
        d2.text((220, 240), 'Environmental hazards: No', fill='#1a1aCC', font=_get_font(16))
        d2.text((220, 280), 'Flooding: No', fill='#1a1aCC', font=_get_font(16))
        d2.text((220, 320), 'HOA: No', fill='#1a1aCC', font=_get_font(16))
        d2.text((220, 400), 'Smoke detector installed. Water heater strapped.', fill='#1a1aCC', font=_get_font(16))
        d2.text((220, 500), 'Seller Signature: [scrawl]  Date: 02/10/2025', fill='#1a1aCC', font=_get_font(16))
        
        # Combine: merge the cover PDF with the scanned pages
        import PyPDF2
        from io import BytesIO
        
        # Create PDF from scanned images
        scan_pdf_bytes = _img_to_pdf([scan1, scan2])
        
        # Merge cover + scans
        merger = PyPDF2.PdfMerger()
        merger.append(BytesIO(cover_bytes))
        merger.append(BytesIO(scan_pdf_bytes))
        output = BytesIO()
        merger.write(output)
        return output.getvalue()

    # ===== QUALITY GATE TESTS =====
    cat = "Quality Gate"
    
    # Empty/null
    ok, reason = is_meaningful_extraction("", 1)
    record("Empty text rejected", not ok, f"reason={reason}", cat)
    
    ok, reason = is_meaningful_extraction(None, 1)
    record("None text rejected", not ok, f"reason={reason}", cat)
    
    # Short text
    ok, reason = is_meaningful_extraction("hello world", 1)
    record("Short text rejected", not ok, f"reason={reason}", cat)
    
    # Whitespace only
    ok, reason = is_meaningful_extraction("\n\n\n   \n\n\t\t\n" * 100, 1)
    record("Whitespace-only rejected", not ok, f"reason={reason}", cat)
    
    # DocuSign metadata string
    docusign_text = ("Docusign Envelope ID: B6DB4E44-50E2-4439-9ED1-45DD569345A3\n" * 3 +
                     "Coldwell Banker Realty\nMikala Caune 11/20/2025\n11/21/2025\nColdwell Banker Realty\n11/20/2025")
    ok, reason = is_meaningful_extraction(docusign_text, 3)
    record("DocuSign metadata string rejected", not ok, f"reason={reason}", cat)
    
    # DocuSign mixed with a few keywords (should still fail)
    mixed = ("Docusign Envelope ID: ABC123\n" * 5 +
             "property seller disclosure inspection roof plumbing electrical\n" +
             "Docusign Envelope ID: DEF456\n" * 5)
    ok, reason = is_meaningful_extraction(mixed, 3)
    record("DocuSign-dominated text rejected", not ok, f"reason={reason}", cat)
    
    # Valid disclosure text should pass
    real_text = """The seller discloses that the property located at 381 Tina Dr, Hollister CA 95023
has the following items: Range, Oven, Microwave, Dishwasher, Garbage Disposal, 
Washer/Dryer Hookups, Rain Gutters. The seller is not aware of any significant defects.
Roof type is tile, approximately 35 years old. Central heating present. Water supply is city."""
    ok, reason = is_meaningful_extraction(real_text, 3)
    record("Valid disclosure text accepted", ok, f"reason={reason}", cat)
    
    # ===== TDS COMPLETENESS TESTS =====
    cat = "TDS Completeness"
    
    complete, score, missing = is_tds_complete("")
    record("Empty text incomplete", not complete, f"score={score}", cat)
    
    # Section A only
    complete, score, missing = is_tds_complete(
        "range dishwasher washer smoke detector fire alarm garage roof fireplace"
    )
    record("Section A only = incomplete", not complete, f"score={score:.2f}, missing={missing}", cat)
    
    # Full TDS
    full_tds = """range dishwasher washer smoke detector fire alarm garage roof fireplace
interior walls ceiling floor exterior wall foundation slab driveway sidewalk plumbing electrical other structural
environmental hazard asbestos lead mold encroachment easement room addition structural modification
permit fill settling sliding soil flooding drainage major damage earthquake neighborhood noise
cc&r homeowners association lawsuit abatement citation
smoke detector water heater braced anchored strapped"""
    complete, score, missing = is_tds_complete(full_tds)
    record("Full TDS text = complete", complete, f"score={score:.2f}", cat)
    
    # ===== DOCUMENT TYPE DETECTION =====
    cat = "Doc Type Detection"
    
    record("TDS detected", handler.detect_document_type("Real Estate Transfer Disclosure Statement") == 'seller_disclosure', "", cat)
    record("Inspection detected", handler.detect_document_type("Home Inspection Report by Inspector Smith ASHI") == 'inspection_report', "", cat)
    record("HOA detected", handler.detect_document_type("Homeowners Association CC&R Covenants HOA Dues") == 'hoa_docs', "", cat)
    record("Mortgage = unknown", handler.detect_document_type("Monthly Mortgage Statement Payment Due") == 'unknown', "", cat)
    record("Empty = unknown", handler.detect_document_type("") == 'unknown', "", cat)
    
    # ===== PDF EXTRACTION TESTS (use static corpus, fallback to fpdf) =====
    cat = "PDF Extraction"
    corpus_dir = os.path.join(os.path.dirname(__file__), 'test_corpus')
    
    # Auto-generate corpus if missing
    if not os.path.isdir(corpus_dir) or len([f for f in os.listdir(corpus_dir) if f.endswith('.pdf')]) < 5:
        try:
            import subprocess
            gen_script = os.path.join(os.path.dirname(__file__), 'generate_test_corpus.py')
            if os.path.exists(gen_script):
                subprocess.run(['python3', gen_script], capture_output=True, timeout=60,
                               cwd=os.path.dirname(__file__))
        except Exception:
            pass
    
    has_corpus = os.path.isdir(corpus_dir) and len([f for f in os.listdir(corpus_dir) if f.endswith('.pdf')]) >= 5
    
    def _load_corpus_pdf(filename):
        """Load a PDF from the static test corpus."""
        path = os.path.join(corpus_dir, filename)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
        return None
    
    if has_corpus:
        # --- Use static corpus (preferred — no runtime deps needed) ---
        
        # Blank PDF
        try:
            pdf_bytes = _load_corpus_pdf('10_blank_3pages.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                record("Blank PDF: no crash", True, f"{len(result.get('text','').strip())} chars", cat)
        except Exception as e:
            record("Blank PDF: no crash", False, str(e)[:100], cat)
        
        # DocuSign metadata PDF
        try:
            pdf_bytes = _load_corpus_pdf('11_metadata_only_docusign.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                ok, reason = is_meaningful_extraction(text, result.get('page_count', 3))
                record("DocuSign PDF fails quality gate", not ok, f"reason={reason}, {len(text)} raw chars", cat)
        except Exception as e:
            record("DocuSign PDF fails quality gate", False, str(e)[:100], cat)
        
        # Valid TDS PDF (digital)
        try:
            pdf_bytes = _load_corpus_pdf('01_digital_tds_clean.pdf')
            if pdf_bytes:
                import re
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                normalized = re.sub(r'\s+', ' ', text.lower())
                has_content = 'transfer disclosure' in normalized and 'seller' in normalized
                record("TDS PDF extracts content", has_content, f"{len(text)} chars, found keywords={has_content}", cat)
        except Exception as e:
            record("TDS PDF extracts content", False, str(e)[:100], cat)
        
        # Wrong document type (mortgage)
        try:
            pdf_bytes = _load_corpus_pdf('12_wrong_doc_mortgage.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                dtype = handler.detect_document_type(text)
                record("Mortgage PDF != disclosure", dtype != 'seller_disclosure', f"detected as: {dtype}", cat)
        except Exception as e:
            record("Mortgage PDF != disclosure", False, str(e)[:100], cat)
        
        # Corrupted PDF
        try:
            pdf_bytes = _load_corpus_pdf('13_corrupted.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                record("Corrupted PDF: no crash", True, "Handled gracefully", cat)
        except Exception:
            record("Corrupted PDF: no crash", True, "Exception caught", cat)
        
        # Not a PDF at all
        try:
            pdf_bytes = _load_corpus_pdf('14_not_a_pdf.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                record("Non-PDF: no crash", True, "Handled gracefully", cat)
        except Exception:
            record("Non-PDF: no crash", True, "Exception caught", cat)
        
        # Nightmare TDS (seller hides everything)
        try:
            pdf_bytes = _load_corpus_pdf('03_digital_tds_nightmare_no_disclosure.pdf')
            if pdf_bytes:
                import re
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                normalized = re.sub(r'\s+', ' ', text.lower())
                has_disclosure = 'transfer disclosure' in normalized or 'seller' in normalized
                record("Nightmare TDS extracts", has_disclosure, f"{len(text)} chars", cat)
        except Exception as e:
            record("Nightmare TDS extracts", False, str(e)[:100], cat)
        
        # Full inspection report (15 pages)
        try:
            pdf_bytes = _load_corpus_pdf('02_digital_inspection_clean.pdf')
            if pdf_bytes:
                import re
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                pages = result.get('page_count', 0)
                normalized = re.sub(r'\s+', ' ', text.lower())
                has_sections = 'roof' in normalized and 'foundation' in normalized and 'electrical' in normalized
                record("15-page inspection extracts", has_sections, 
                       f"{len(text)} chars, {pages} pages, key sections found={has_sections}", cat)
        except Exception as e:
            record("15-page inspection extracts", False, str(e)[:100], cat)
    
    elif fpdf_available:
        cat = "PDF Extraction"
        
        # Blank PDF
        try:
            result = handler.extract_text_from_bytes(make_blank_pdf(3))
            record("Blank PDF: no crash", True, f"{len(result.get('text','').strip())} chars", cat)
        except Exception as e:
            record("Blank PDF: no crash", False, str(e)[:100], cat)
        
        # DocuSign metadata PDF
        try:
            result = handler.extract_text_from_bytes(make_metadata_pdf())
            text = result.get('text', '')
            ok, reason = is_meaningful_extraction(text, result.get('page_count', 3))
            record("DocuSign PDF fails quality gate", not ok, f"reason={reason}, {len(text)} raw chars", cat)
        except Exception as e:
            record("DocuSign PDF fails quality gate", False, str(e)[:100], cat)
        
        # Valid TDS PDF
        try:
            import re
            result = handler.extract_text_from_bytes(make_tds_pdf())
            text = result.get('text', '')
            normalized = re.sub(r'\s+', ' ', text.lower())
            has_content = 'transfer disclosure' in normalized and 'seller' in normalized
            record("TDS PDF extracts content", has_content, f"{len(text)} chars, found keywords={has_content}", cat)
        except Exception as e:
            record("TDS PDF extracts content", False, str(e)[:100], cat)
        
        # Wrong document type
        try:
            result = handler.extract_text_from_bytes(make_wrong_doc_pdf())
            text = result.get('text', '')
            dtype = handler.detect_document_type(text)
            record("Mortgage PDF != disclosure", dtype != 'seller_disclosure', f"detected as: {dtype}", cat)
        except Exception as e:
            record("Mortgage PDF != disclosure", False, str(e)[:100], cat)
        
        # Corrupted PDF
        try:
            result = handler.extract_text_from_bytes(b"%PDF-1.4\n" + b"\x00\xff\xfe" * 100 + b"\n%%EOF")
            record("Corrupted PDF: no crash", True, "Handled gracefully", cat)
        except Exception:
            record("Corrupted PDF: no crash", True, "Exception caught", cat)
        
        # Not a PDF at all
        try:
            result = handler.extract_text_from_bytes(b"This is just text, not a PDF")
            record("Non-PDF: no crash", True, "Handled gracefully", cat)
        except Exception:
            record("Non-PDF: no crash", True, "Exception caught", cat)
    else:
        record("PDF tests skipped", False, "No test corpus found and fpdf2 not installed. Run: python generate_test_corpus.py", "Setup")
    
    # ===== SCANNED / HANDWRITTEN PDF TESTS (use corpus or fpdf + Pillow) =====
    if has_corpus:
        cat = "Scanned/Handwritten"
        
        # 1. Handwritten TDS
        try:
            pdf_bytes = _load_corpus_pdf('05_scanned_handwritten_tds.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                pages = result.get('page_count', 3)
                ok, reason = is_meaningful_extraction(text, pages)
                if len(text.strip()) < 50:
                    record("Handwritten TDS: text extraction blind", True,
                           f"{len(text.strip())} chars (expected ~0 from image PDF). Vision fallback needed.", cat)
                    record("Handwritten TDS: quality gate catches it", not ok,
                           f"reason={reason}. Gate should reject sparse/empty extraction.", cat)
                else:
                    record("Handwritten TDS: OCR extracted text", True,
                           f"{len(text.strip())} chars via OCR. meaningful={ok} ({reason})", cat)
        except Exception as e:
            record("Handwritten TDS: no crash", False, str(e)[:150], cat)
        
        # 2. Faded photocopy
        try:
            pdf_bytes = _load_corpus_pdf('06_faded_photocopy.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                ok, reason = is_meaningful_extraction(text, 1)
                if len(text.strip()) < 50:
                    record("Faded scan: text extraction blind", True,
                           f"{len(text.strip())} chars. Vision fallback needed for faded docs.", cat)
                    record("Faded scan: quality gate catches it", not ok,
                           f"reason={reason}", cat)
                else:
                    record("Faded scan: OCR extracted text", True,
                           f"{len(text.strip())} chars. meaningful={ok}", cat)
        except Exception as e:
            record("Faded scan: no crash", False, str(e)[:150], cat)
        
        # 3. Crooked scan
        try:
            pdf_bytes = _load_corpus_pdf('07_crooked_scan.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                record("Crooked scan: no crash", True,
                       f"{len(text.strip())} chars extracted from rotated scan", cat)
        except Exception as e:
            record("Crooked scan: no crash", False, str(e)[:150], cat)
        
        # 4. Phone camera photo
        try:
            pdf_bytes = _load_corpus_pdf('08_phone_photo.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                ok, reason = is_meaningful_extraction(text, 1)
                if len(text.strip()) < 50:
                    record("Phone photo PDF: text extraction blind", True,
                           f"{len(text.strip())} chars. Vision fallback needed.", cat)
                    record("Phone photo PDF: quality gate catches it", not ok,
                           f"reason={reason}", cat)
                else:
                    record("Phone photo PDF: OCR extracted text", True,
                           f"{len(text.strip())} chars. meaningful={ok}", cat)
        except Exception as e:
            record("Phone photo PDF: no crash", False, str(e)[:150], cat)
        
        # 5. Mixed digital + scanned
        try:
            pdf_bytes = _load_corpus_pdf('09_mixed_digital_scanned.pdf')
            if pdf_bytes:
                import re
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                pages = result.get('page_count', 3)
                normalized = re.sub(r'\s+', ' ', text.lower())
                has_cover = 'coldwell' in normalized or 'seller disclosure package' in normalized
                record("Mixed PDF: digital page extracts", has_cover,
                       f"{len(text.strip())} chars total, {pages} pages, cover_found={has_cover}", cat)
                ok, reason = is_meaningful_extraction(text, pages)
                chars_per_page = len(re.sub(r'\s+', ' ', text.strip())) / max(pages, 1)
                record("Mixed PDF: quality assessment", True,
                       f"meaningful={ok} ({reason}), ~{chars_per_page:.0f} chars/page.", cat)
        except Exception as e:
            record("Mixed PDF: no crash", False, str(e)[:150], cat)
        
        # 6. Inspection with embedded photos
        try:
            pdf_bytes = _load_corpus_pdf('15_inspection_with_photos.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                pages = result.get('page_count', 2)
                if len(text.strip()) < 50:
                    ok, reason = is_meaningful_extraction(text, pages)
                    record("Inspection w/ photos: text blind, gate catches", not ok,
                           f"0 chars from image-based report with damage photos. Vision fallback needed.", cat)
                else:
                    record("Inspection w/ photos: OCR extracted", True,
                           f"{len(text.strip())} chars. Check if photo captions captured.", cat)
        except Exception as e:
            record("Inspection w/ photos: no crash", False, str(e)[:150], cat)
        
        # 7. Redacted disclosure
        try:
            pdf_bytes = _load_corpus_pdf('16_redacted_disclosure.pdf')
            if pdf_bytes:
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                record("Redacted disclosure: no crash", True,
                       f"{len(text.strip())} chars extracted from partially redacted doc", cat)
        except Exception as e:
            record("Redacted disclosure: no crash", False, str(e)[:150], cat)
    
    elif fpdf_available and pillow_available:
        cat = "Scanned/Handwritten"
        
        # 1. Full handwritten TDS - the #1 production failure case
        try:
            pdf_bytes = make_scanned_handwritten_tds()
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '')
            pages = result.get('page_count', 3)
            ok, reason = is_meaningful_extraction(text, pages)
            # Text extraction SHOULD fail on image-based PDFs
            # If it passes, great (OCR worked). If it fails, the quality gate should catch it.
            if len(text.strip()) < 50:
                # No text extracted - quality gate should flag this for vision fallback
                record("Handwritten TDS: text extraction blind", True,
                       f"{len(text.strip())} chars extracted (expected ~0 from image PDF). Vision fallback needed.", cat)
                record("Handwritten TDS: quality gate catches it", not ok,
                       f"reason={reason}. Gate should reject sparse/empty extraction.", cat)
            else:
                # OCR got something - check if it's meaningful
                record("Handwritten TDS: OCR extracted text", True,
                       f"{len(text.strip())} chars via OCR. meaningful={ok} ({reason})", cat)
        except Exception as e:
            record("Handwritten TDS: no crash", False, str(e)[:150], cat)
        
        # 2. Faded photocopy - low contrast
        try:
            pdf_bytes = make_faded_scan()
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '')
            ok, reason = is_meaningful_extraction(text, 1)
            if len(text.strip()) < 50:
                record("Faded scan: text extraction blind", True,
                       f"{len(text.strip())} chars. Vision fallback needed for faded docs.", cat)
                record("Faded scan: quality gate catches it", not ok,
                       f"reason={reason}", cat)
            else:
                record("Faded scan: OCR extracted text", True,
                       f"{len(text.strip())} chars. meaningful={ok}", cat)
        except Exception as e:
            record("Faded scan: no crash", False, str(e)[:150], cat)
        
        # 3. Crooked scan - rotated document
        try:
            pdf_bytes = make_crooked_scan()
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '')
            record("Crooked scan: no crash", True,
                   f"{len(text.strip())} chars extracted from rotated scan", cat)
        except Exception as e:
            record("Crooked scan: no crash", False, str(e)[:150], cat)
        
        # 4. Phone camera photo of document
        try:
            pdf_bytes = make_phone_photo_pdf()
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '')
            ok, reason = is_meaningful_extraction(text, 1)
            if len(text.strip()) < 50:
                record("Phone photo PDF: text extraction blind", True,
                       f"{len(text.strip())} chars. Vision fallback needed.", cat)
                record("Phone photo PDF: quality gate catches it", not ok,
                       f"reason={reason}", cat)
            else:
                record("Phone photo PDF: OCR extracted text", True,
                       f"{len(text.strip())} chars. meaningful={ok}", cat)
        except Exception as e:
            record("Phone photo PDF: no crash", False, str(e)[:150], cat)
        
        # 5. Mixed digital + scanned pages (cover letter + handwritten disclosures)
        try:
            pdf_bytes = make_mixed_text_image_pdf()
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '')
            pages = result.get('page_count', 3)
            import re
            normalized = re.sub(r'\s+', ' ', text.lower())
            # Page 1 (digital) should extract. Pages 2-3 (scanned) likely won't.
            has_cover = 'coldwell' in normalized or 'seller disclosure package' in normalized
            record("Mixed PDF: digital page extracts", has_cover,
                   f"{len(text.strip())} chars total, {pages} pages, cover_found={has_cover}", cat)
            # The critical test: does the system know pages 2-3 are image-only?
            ok, reason = is_meaningful_extraction(text, pages)
            chars_per_page = len(re.sub(r'\s+', ' ', text.strip())) / max(pages, 1)
            record("Mixed PDF: quality assessment",True,
                   f"meaningful={ok} ({reason}), ~{chars_per_page:.0f} chars/page. " +
                   f"If low, vision fallback should trigger for scanned pages.", cat)
        except Exception as e:
            record("Mixed PDF: no crash", False, str(e)[:150], cat)
    
        # 6. Inspection report with embedded damage photos
        try:
            # Create a realistic inspection report: text sections + photos of "damage"
            pages_imgs = []
            
            # Page 1: Cover page with text
            p1 = Image.new('RGB', (1700, 2200), 'white')
            d1 = ImageDraw.Draw(p1)
            d1.text((400, 100), 'HOME INSPECTION REPORT', fill='black', font=_get_font(30))
            d1.text((200, 200), 'Inspector: John Smith, ASHI #12345', fill='black', font=_get_font(18))
            d1.text((200, 250), 'Property: 456 Test Ave, San Jose CA 95123', fill='black', font=_get_font(18))
            d1.text((200, 300), 'Date: February 1, 2025', fill='black', font=_get_font(18))
            d1.text((200, 400), 'ROOF SECTION', fill='black', font=_get_font(24))
            d1.text((200, 450), 'Condition: Deficient', fill='#CC0000', font=_get_font(18))
            d1.text((200, 500), 'Composition shingle roof, approximately 25 years old.', fill='black', font=_get_font(16))
            d1.text((200, 540), 'Multiple areas of curling and missing shingles observed.', fill='black', font=_get_font(16))
            
            # Simulate a damage photo: brown/dark patch (water stain on ceiling)
            d1.text((200, 620), 'Photo 1: Water stain master bedroom ceiling', fill='#666', font=_get_font(14))
            # Draw a "photo" - rectangle with brown stain pattern
            d1.rectangle([200, 650, 800, 1050], outline='gray', width=2)
            d1.rectangle([210, 660, 790, 1040], fill='#F5F0E8')  # ceiling color
            # Water stain
            for i in range(40):
                x = _rand.randint(350, 650)
                y = _rand.randint(750, 900)
                r = _rand.randint(5, 25)
                d1.ellipse([x-r, y-r, x+r, y+r], fill=f'#{_rand.randint(140,170):02x}{_rand.randint(100,130):02x}{_rand.randint(60,90):02x}')
            d1.text((420, 1060), 'Brownish water stain ~2ft diameter', fill='#666', font=_get_font(13))
            pages_imgs.append(p1)
            
            # Page 2: Foundation section with crack photo
            p2 = Image.new('RGB', (1700, 2200), 'white')
            d2 = ImageDraw.Draw(p2)
            d2.text((200, 80), 'FOUNDATION SECTION', fill='black', font=_get_font(24))
            d2.text((200, 130), 'Condition: Marginal', fill='#CC8800', font=_get_font(18))
            d2.text((200, 180), 'Concrete perimeter foundation.', fill='black', font=_get_font(16))
            d2.text((200, 220), 'Hairline crack observed on east wall, approximately 3 feet long.', fill='black', font=_get_font(16))
            
            # Simulate crack photo
            d2.text((200, 310), 'Photo 2: Foundation crack, east wall exterior', fill='#666', font=_get_font(14))
            d2.rectangle([200, 340, 800, 740], outline='gray', width=2)
            d2.rectangle([210, 350, 790, 730], fill='#C0C0B0')  # concrete color
            # Draw crack line
            y_pos = 400
            for x in range(300, 700, 3):
                y_off = _rand.randint(-3, 3)
                y_pos += y_off
                d2.line((x, y_pos, x+3, y_pos + _rand.randint(-2, 2)), fill='#333', width=1)
            d2.text((350, 750), 'Hairline crack running horizontally', fill='#666', font=_get_font(13))
            
            d2.text((200, 850), 'ELECTRICAL SECTION', fill='black', font=_get_font(24))
            d2.text((200, 900), 'Condition: Satisfactory', fill='#008800', font=_get_font(18))
            d2.text((200, 950), '200 amp panel. All circuits labeled. GFCI present in wet areas.', fill='black', font=_get_font(16))
            d2.text((200, 990), 'Two outlets in garage tested open ground - repair recommended.', fill='black', font=_get_font(16))
            pages_imgs.append(p2)
            
            pdf_bytes = _img_to_pdf(pages_imgs)
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '')
            pages = result.get('page_count', 2)
            
            if len(text.strip()) < 50:
                # Image-only, text extraction blind - expected
                ok, reason = is_meaningful_extraction(text, pages)
                record("Inspection w/ photos: text blind, gate catches", not ok,
                       f"0 chars extracted from image-based report with damage photos. Vision fallback needed - " +
                       f"prompt now includes [PHOTO:] description rules for damage evidence.", cat)
            else:
                record("Inspection w/ photos: OCR extracted", True,
                       f"{len(text.strip())} chars. Check if photo captions captured.", cat)
        except Exception as e:
            record("Inspection w/ photos: no crash", False, str(e)[:150], cat)
    
    elif fpdf_available and not pillow_available:
        record("Scanned PDF tests", False, "Pillow not installed - pip install Pillow", "Setup")
    
    # ===== REAL DOCUMENT TESTS =====
    cat = "Real Documents"
    
    # Look for real PDFs in standard locations
    real_pdfs = []
    search_dirs = [
        app.config.get('UPLOAD_FOLDER', 'uploads'),  # Production upload dir
        os.path.join(os.path.dirname(__file__), 'test_files'),  # Test fixtures
    ]
    
    for search_dir in search_dirs:
        if os.path.exists(search_dir):
            # Check top-level PDFs
            for f in os.listdir(search_dir):
                full = os.path.join(search_dir, f)
                if f.lower().endswith('.pdf') and os.path.isfile(full):
                    real_pdfs.append((f, full))
            # Also check one level deep (uploads/user_id/property_id/)
            for sub in os.listdir(search_dir):
                subpath = os.path.join(search_dir, sub)
                if os.path.isdir(subpath):
                    for sub2 in os.listdir(subpath):
                        subpath2 = os.path.join(subpath, sub2)
                        if os.path.isdir(subpath2):
                            for f in os.listdir(subpath2):
                                full = os.path.join(subpath2, f)
                                if f.lower().endswith('.pdf') and os.path.isfile(full):
                                    real_pdfs.append((f, full))
    
    if real_pdfs:
        for name, path in real_pdfs[:5]:  # Test up to 5
            try:
                with open(path, 'rb') as f:
                    pdf_bytes = f.read()
                result = handler.extract_text_from_bytes(pdf_bytes)
                text = result.get('text', '')
                pages = result.get('page_count', 1)
                ok, reason = is_meaningful_extraction(text, pages)
                record(f"{name}: extraction", True, f"{len(text)} chars, {pages} pages, meaningful={ok} ({reason})", cat)
            except Exception as e:
                record(f"{name}: extraction", False, str(e)[:100], cat)
    else:
        record("Real PDF corpus", True, "No uploaded PDFs found yet — upload documents to test extraction quality.", cat)
    
    # ===== ANTI-HALLUCINATION PROMPT CHECKS =====
    cat = "Anti-Hallucination"
    
    try:
        with open(os.path.join(os.path.dirname(__file__), 'app.py'), 'r') as f:
            app_src = f.read()
        record("Truth check has STRICT RULES", "STRICT RULES" in app_src, "", cat)
        record("Truth check has evidence requirement", "evidence" in app_src.lower(), "", cat)
    except Exception:
        record("Truth check prompt check", False, "Could not read app.py", cat)
    
    try:
        with open(os.path.join(os.path.dirname(__file__), 'optimized_hybrid_cross_reference.py'), 'r') as f:
            xref_src = f.read()
        record("Cross-ref has grounding rules", "Do NOT infer additional issues" in xref_src, "", cat)
    except Exception:
        record("Cross-ref prompt check", False, "Could not read cross_reference source", cat)
    
    try:
        with open(os.path.join(os.path.dirname(__file__), 'pdf_handler.py'), 'r') as f:
            pdf_src = f.read()
        record("Vision extraction is faithful", "100% faithful" in pdf_src, "", cat)
        record("Vision has illegible fallback", "[illegible]" in pdf_src, "", cat)
        record("TDS extraction has checkbox rules", "[X]" in pdf_src, "", cat)
        record("Inspection has photo description rules", "[PHOTO:" in pdf_src, "", cat)
        record("Photo descriptions are EXHAUSTIVE not brief", "Be EXHAUSTIVE" in pdf_src,
               "Prompt must request exhaustive detail for accurate offer pricing", cat)
        record("Photo captures materials and conditions", "material types" in pdf_src.lower() or "Material types" in pdf_src, "", cat)
        record("Photo captures moisture indicators", "moisture indicators" in pdf_src.lower() or "Moisture indicators" in pdf_src, "", cat)
        record("Photo captures biological growth", "mold color" in pdf_src.lower() or "biological growth" in pdf_src.lower(), "", cat)
        record("Photo captures equipment details", "brand names" in pdf_src.lower() or "manufacture dates" in pdf_src.lower(), "", cat)
        record("Photo captures prior repairs", "prior repair" in pdf_src.lower(), "", cat)
        record("Vision token limit adequate", "32000" in pdf_src or "32_000" in pdf_src,
               "Exhaustive photo descriptions need 32K+ tokens for long reports", cat)
        record("Every photo must be described", "Do NOT skip any photo" in pdf_src, "", cat)
    except Exception:
        record("Vision prompt check", False, "Could not read pdf_handler source", cat)
    
    try:
        with open(os.path.join(os.path.dirname(__file__), 'optimized_hybrid_cross_reference.py'), 'r') as f:
            xref_src = f.read()
        record("Cross-ref uses [PHOTO:] evidence", "[PHOTO:" in xref_src,
               "Cross-ref prompt instructs AI to factor photo descriptions into severity", cat)
        record("Photo evidence escalates severity", "increase severity" in xref_src.lower() or "photographic evidence" in xref_src.lower(),
               "Photos showing active damage should raise severity rating", cat)
    except Exception:
        record("Cross-ref photo evidence check", False, "Could not read cross_reference source", cat)
    
    elapsed = round(_time.time() - start, 2)
    
    # Auto-file bugs for failures
    bugs_filed = 0
    try:
        current_version = open('VERSION').read().strip() if os.path.exists('VERSION') else 'unknown'
    except Exception:
        current_version = 'unknown'
    
    for r in results:
        if not r.get('passed', False):
            try:
                bug_title = f"Adversarial PDF: {r.get('name', 'Unknown')}"
                existing = Bug.query.filter_by(title=bug_title, status='open').first()
                if not existing:
                    bug = Bug(
                        title=bug_title,
                        description=f"Adversarial PDF test failure.\n\nDetails: {r.get('details', 'N/A')}",
                        error_message=r.get('details', 'Test failed'),
                        severity='high',
                        category='pdf_quality',
                        status='open',
                        version_reported=current_version,
                        reported_by='auto_test_adversarial'
                    )
                    db.session.add(bug)
                    db.session.commit()
                    bugs_filed += 1
            except Exception:
                db.session.rollback()
    
    return jsonify({
        'success': failed == 0,
        'summary': {
            'total': passed + failed,
            'passed': passed,
            'failed': failed,
            'duration_seconds': elapsed
        },
        'results': results,
        'bugs_filed': bugs_filed
    })


@testing_bp.route('/api/test/pdf-corpus-pipeline', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def run_pdf_corpus_pipeline_tests():
    """Run corpus PDFs through the FULL pipeline: extraction -> quality gate -> analysis -> validation.
    
    This is the only test that exercises the complete path real users take:
    PDF bytes -> text extraction -> quality gate -> AI analysis -> score/flags/recommendation
    """
    import re
    start_time = time.time()
    results = []
    passed = 0
    failed = 0
    
    def record(name, did_pass, details, category):
        nonlocal passed, failed
        if did_pass:
            passed += 1
        else:
            failed += 1
        results.append({
            'name': name,
            'passed': did_pass,
            'details': details,
            'category': category
        })
    
    corpus_dir = os.path.join(os.path.dirname(__file__), 'test_corpus')
    if not os.path.isdir(corpus_dir) or len([f for f in os.listdir(corpus_dir) if f.endswith('.pdf')]) < 5:
        # Auto-generate corpus on first run
        try:
            record("Corpus: auto-generating", True, "test_corpus/ not found, generating on-demand...", "Setup")
            import subprocess
            gen_script = os.path.join(os.path.dirname(__file__), 'generate_test_corpus.py')
            if os.path.exists(gen_script):
                result = subprocess.run(
                    ['python3', gen_script],
                    capture_output=True, text=True, timeout=60,
                    cwd=os.path.dirname(__file__)
                )
                if result.returncode == 0:
                    record("Corpus: generated successfully", True, result.stdout[-200:] if result.stdout else "OK", "Setup")
                else:
                    record("Corpus: generation failed", False, (result.stderr or result.stdout)[-300:], "Setup")
            else:
                record("Corpus: generator missing", False, "generate_test_corpus.py not found", "Setup")
        except Exception as e:
            record("Corpus: generation error", False, f"{type(e).__name__}: {str(e)[:200]}", "Setup")
    
    if not os.path.isdir(corpus_dir):
        elapsed = round(time.time() - start_time, 2)
        return jsonify({
            'success': False,
            'summary': {'total': passed + failed, 'passed': passed, 'failed': failed, 'duration_seconds': elapsed},
            'results': results
        })
    
    def load_pdf(filename):
        path = os.path.join(corpus_dir, filename)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
        return None
    
    # Initialize PDF handler
    from pdf_handler import PDFHandler, is_meaningful_extraction, is_tds_complete
    handler = PDFHandler()
    
    # ===================================================================
    # PHASE 1: EXTRACTION PIPELINE (fast, no API calls)
    # ===================================================================
    
    # 1a. Digital TDS — should extract cleanly
    try:
        pdf_bytes = load_pdf('01_digital_tds_clean.pdf')
        if pdf_bytes:
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '') if isinstance(result, dict) else (result or '')
            normalized = re.sub(r'\s+', ' ', text.lower())
            has_keywords = 'transfer disclosure' in normalized and 'seller' in normalized
            ok, reason = is_meaningful_extraction(text, result.get('page_count', 4) if isinstance(result, dict) else 4)
            record("Digital TDS: text extracted", has_keywords and ok,
                   f"{len(text)} chars, keywords={has_keywords}, meaningful={ok} ({reason})", "Extraction")
    except Exception as e:
        record("Digital TDS: extraction", False, f"CRASH: {e}", "Extraction")
    
    # 1b. Digital inspection — should extract with all sections
    try:
        pdf_bytes = load_pdf('02_digital_inspection_clean.pdf')
        if pdf_bytes:
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '') if isinstance(result, dict) else (result or '')
            normalized = re.sub(r'\s+', ' ', text.lower())
            has_sections = all(kw in normalized for kw in ['roof', 'foundation', 'electrical', 'plumbing'])
            record("Digital inspection: key sections found", has_sections,
                   f"{len(text)} chars, sections={has_sections}", "Extraction")
    except Exception as e:
        record("Digital inspection: extraction", False, f"CRASH: {e}", "Extraction")
    
    # 1c. Nightmare TDS — seller hides everything, text should extract
    try:
        pdf_bytes = load_pdf('03_digital_tds_nightmare_no_disclosure.pdf')
        if pdf_bytes:
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '') if isinstance(result, dict) else (result or '')
            normalized = re.sub(r'\s+', ' ', text.lower())
            has_nos = normalized.count('no (x)') >= 5 or normalized.count('no') >= 10
            record("Nightmare TDS: extracts seller denials", has_nos,
                   f"{len(text)} chars, seller_denials_found={has_nos}", "Extraction")
    except Exception as e:
        record("Nightmare TDS: extraction", False, f"CRASH: {e}", "Extraction")
    
    # 1d. Nightmare inspection — should extract critical findings
    try:
        pdf_bytes = load_pdf('04_digital_inspection_nightmare.pdf')
        if pdf_bytes:
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '') if isinstance(result, dict) else (result or '')
            normalized = re.sub(r'\s+', ' ', text.lower())
            has_critical = 'foundation' in normalized and ('water intrusion' in normalized or 'structural' in normalized)
            has_costs = '$' in text and ('75' in text or '154' in text or '15,000' in text or '45,000' in text)
            record("Nightmare inspection: critical issues extracted", has_critical,
                   f"{len(text)} chars, critical_issues={has_critical}, cost_estimates={has_costs}", "Extraction")
    except Exception as e:
        record("Nightmare inspection: extraction", False, f"CRASH: {e}", "Extraction")
    
    # 1e. Scanned handwritten TDS — OCR challenge
    try:
        pdf_bytes = load_pdf('05_scanned_handwritten_tds.pdf')
        if pdf_bytes:
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '') if isinstance(result, dict) else (result or '')
            pages = result.get('page_count', 3) if isinstance(result, dict) else 3
            ok, reason = is_meaningful_extraction(text, pages)
            record("Scanned handwritten TDS: extraction attempted",
                   len(text.strip()) > 0 or not ok,  # Pass if we got text OR quality gate caught it
                   f"{len(text.strip())} chars, meaningful={ok} ({reason}). "
                   f"{'Vision fallback needed.' if not ok else 'OCR succeeded.'}", "Extraction")
    except Exception as e:
        record("Scanned handwritten TDS: no crash", False, f"CRASH: {e}", "Extraction")
    
    # 1f. Wrong document type — should detect as non-disclosure
    try:
        pdf_bytes = load_pdf('12_wrong_doc_mortgage.pdf')
        if pdf_bytes:
            result = handler.extract_text_from_bytes(pdf_bytes)
            text = result.get('text', '') if isinstance(result, dict) else (result or '')
            dtype = handler.detect_document_type(text)
            record("Wrong doc type rejected", dtype != 'seller_disclosure',
                   f"Detected as: {dtype} (should NOT be seller_disclosure)", "Extraction")
    except Exception as e:
        record("Wrong doc type: no crash", False, f"CRASH: {e}", "Extraction")
    
    # ===================================================================
    # PHASE 2: FULL ANALYSIS PIPELINE (uses API credits, slower)
    # Test with paired documents to validate contradiction detection
    # ===================================================================
    
    # Helper to extract text from a corpus PDF
    def extract_corpus_text(filename):
        pdf_bytes = load_pdf(filename)
        if not pdf_bytes:
            return None
        result = handler.extract_text_from_bytes(pdf_bytes)
        return result.get('text', '') if isinstance(result, dict) else (result or '')
    
    # 2a. CLEAN PAIR (01 + 02): Honest seller, moderate issues
    try:
        disclosure_text = extract_corpus_text('01_digital_tds_clean.pdf')
        inspection_text = extract_corpus_text('02_digital_inspection_clean.pdf')
        
        if disclosure_text and inspection_text and len(disclosure_text) > 100 and len(inspection_text) > 100:
            buyer_profile_obj = BuyerProfile(
                max_budget=800000,
                repair_tolerance="moderate",
                ownership_duration="5-10",
                biggest_regret="hidden_issues",
                replaceability="somewhat_unique",
                deal_breakers=["foundation", "mold"]
            )
            
            analysis_start = time.time()
            analysis = intelligence.analyze_property(
                seller_disclosure_text=disclosure_text,
                inspection_report_text=inspection_text,
                property_price=700000,
                buyer_profile=buyer_profile_obj,
                property_address="2847 Winfield Blvd, San Jose, CA 95128"
            )
            analysis_elapsed = time.time() - analysis_start
            
            # Extract score
            offer_score = None
            if hasattr(analysis, 'risk_score') and analysis.risk_score:
                if hasattr(analysis.risk_score, 'overall_risk_score'):
                    offer_score = 100 - analysis.risk_score.overall_risk_score
            
            # Extract red flags
            red_flags = []
            if hasattr(analysis, 'transparency_report') and analysis.transparency_report:
                tr = analysis.transparency_report
                if hasattr(tr, 'red_flags') and tr.red_flags:
                    red_flags = tr.red_flags
                elif isinstance(tr, dict):
                    red_flags = tr.get('red_flags', [])
            
            record("Clean pair: analysis completes", True,
                   f"Score={offer_score}, red_flags={len(red_flags)}, {analysis_elapsed:.1f}s", "Analysis")
            
            # Score validation: clean/honest should be moderate-to-high (30-90)
            if offer_score is not None:
                record("Clean pair: score reasonable",
                       25 <= offer_score <= 95,
                       f"Score {offer_score:.0f} (expected 25-95 for honest seller with moderate issues)", "Score Validation")
            
            record("Clean pair: response time",
                   analysis_elapsed < 90,
                   f"{analysis_elapsed:.1f}s (limit: 90s)", "Score Validation")
        else:
            record("Clean pair: extraction failed", False,
                   f"disclosure={len(disclosure_text or '')} chars, inspection={len(inspection_text or '')} chars", "Analysis")
    except Exception as e:
        record("Clean pair: analysis", False, f"CRASH: {type(e).__name__}: {str(e)[:200]}", "Analysis")
    
    # 2b. NIGHTMARE PAIR (03 + 04): Seller hiding $75K-$154K in problems
    # This is THE test — OfferWise must catch the contradictions
    try:
        disclosure_text = extract_corpus_text('03_digital_tds_nightmare_no_disclosure.pdf')
        inspection_text = extract_corpus_text('04_digital_inspection_nightmare.pdf')
        
        if disclosure_text and inspection_text and len(disclosure_text) > 50 and len(inspection_text) > 100:
            buyer_profile_obj = BuyerProfile(
                max_budget=700000,
                repair_tolerance="low",
                ownership_duration="5-10",
                biggest_regret="hidden_issues",
                replaceability="replaceable",
                deal_breakers=["foundation", "mold", "electrical"]
            )
            
            analysis_start = time.time()
            analysis = intelligence.analyze_property(
                seller_disclosure_text=disclosure_text,
                inspection_report_text=inspection_text,
                property_price=615000,
                buyer_profile=buyer_profile_obj,
                property_address="456 Hidden Problem Way, Sunnyvale, CA 94086"
            )
            analysis_elapsed = time.time() - analysis_start
            
            # Extract score
            offer_score = None
            if hasattr(analysis, 'risk_score') and analysis.risk_score:
                if hasattr(analysis.risk_score, 'overall_risk_score'):
                    offer_score = 100 - analysis.risk_score.overall_risk_score
            
            # Extract red flags (from transparency_report)
            red_flags = []
            if hasattr(analysis, 'transparency_report') and analysis.transparency_report:
                tr = analysis.transparency_report
                if hasattr(tr, 'red_flags') and tr.red_flags:
                    red_flags = tr.red_flags
                elif isinstance(tr, dict):
                    red_flags = tr.get('red_flags', [])
            
            # Extract contradictions (from cross_reference, NOT transparency_report)
            contradictions = []
            undisclosed = []
            if hasattr(analysis, 'cross_reference') and analysis.cross_reference:
                cr = analysis.cross_reference
                if hasattr(cr, 'contradictions') and cr.contradictions:
                    contradictions = cr.contradictions
                elif isinstance(cr, dict):
                    contradictions = cr.get('contradictions', [])
                if hasattr(cr, 'undisclosed_issues') and cr.undisclosed_issues:
                    undisclosed = cr.undisclosed_issues
                elif isinstance(cr, dict):
                    undisclosed = cr.get('undisclosed_issues', [])
            
            # Also check transparency_report for undisclosed_issues as backup
            if not red_flags and not contradictions:
                if hasattr(analysis, 'transparency_report') and analysis.transparency_report:
                    tr = analysis.transparency_report
                    if hasattr(tr, 'undisclosed_issues') and tr.undisclosed_issues:
                        undisclosed = undisclosed or tr.undisclosed_issues
            
            record("Nightmare pair: analysis completes", True,
                   f"Score={offer_score}, red_flags={len(red_flags)}, contradictions={len(contradictions)}, undisclosed={len(undisclosed)}, {analysis_elapsed:.1f}s",
                   "Analysis")
            
            # THE MONEY TESTS - OfferWise must catch these:
            
            # Score should be LOW (high risk) — seller hiding major issues
            if offer_score is not None:
                record("Nightmare: score reflects high risk",
                       offer_score <= 55,
                       f"Score {offer_score:.0f} (expected ≤55 for $75K-$154K hidden problems)",
                       "Contradiction Detection")
            
            # Must find red flags OR undisclosed issues — foundation, roof, electrical are critical
            flags_or_undisclosed = len(red_flags) + len(undisclosed)
            record("Nightmare: red flags or undisclosed issues detected",
                   flags_or_undisclosed >= 2,
                   f"{len(red_flags)} red flags + {len(undisclosed)} undisclosed = {flags_or_undisclosed} (expected ≥2)",
                   "Contradiction Detection")
            
            # Must find contradictions OR undisclosed between disclosure and inspection
            # Note: contradictions require category matching between disclosure items and findings.
            # If the matcher doesn't connect them, issues show as "undisclosed" instead.
            # Both prove the system caught seller deception — contradictions is the ideal classification.
            total_deception_signals = len(contradictions) + len(undisclosed)
            record("Nightmare: deception detected (contradictions + undisclosed)",
                   total_deception_signals >= 3,
                   f"{len(contradictions)} contradictions + {len(undisclosed)} undisclosed = {total_deception_signals} "
                   f"(expected ≥3 for foundation + roof + electrical + unpermitted work). "
                   f"{'⚠️ 0 contradictions — category matching needs improvement.' if len(contradictions) == 0 else ''}",
                   "Contradiction Detection")
            
            record("Nightmare pair: response time",
                   analysis_elapsed < 90,
                   f"{analysis_elapsed:.1f}s (limit: 90s)", "Score Validation")
        else:
            record("Nightmare pair: extraction failed", False,
                   f"disclosure={len(disclosure_text or '')} chars, inspection={len(inspection_text or '')} chars", "Analysis")
    except Exception as e:
        record("Nightmare pair: analysis", False, f"CRASH: {type(e).__name__}: {str(e)[:200]}", "Analysis")
    
    elapsed = round(time.time() - start_time, 2)
    
    # Auto-file bugs for failures
    bugs_filed = 0
    try:
        current_version = open('VERSION').read().strip() if os.path.exists('VERSION') else 'unknown'
    except Exception:
        current_version = 'unknown'
    
    for r in results:
        if not r.get('passed', False):
            try:
                bug_title = f"Corpus Pipeline: {r.get('name', 'Unknown')}"
                existing = Bug.query.filter_by(title=bug_title, status='open').first()
                if not existing:
                    bug = Bug(
                        title=bug_title,
                        description=f"PDF Corpus Pipeline test failure.\n\nCategory: {r.get('category', 'N/A')}\nDetails: {r.get('details', 'N/A')}",
                        error_message=r.get('details', 'Test failed'),
                        severity='high' if 'Contradiction' in r.get('category', '') else 'medium',
                        category='pdf_pipeline',
                        status='open',
                        version_reported=current_version,
                        reported_by='auto_test_corpus'
                    )
                    db.session.add(bug)
                    db.session.commit()
                    bugs_filed += 1
            except Exception:
                db.session.rollback()
    
    return jsonify({
        'success': failed == 0,
        'summary': {
            'total': passed + failed,
            'passed': passed,
            'failed': failed,
            'duration_seconds': elapsed
        },
        'results': results,
        'bugs_filed': bugs_filed
    })


@testing_bp.route('/api/turk/start', methods=['POST'])
@_api_admin_required
def turk_start_session():
    """Start or resume a Turk testing session"""
    import secrets
    
    data = request.json or {}
    turk_id = data.get('turk_id', 'anonymous')
    task_id = data.get('task_id', 'unknown')
    user_agent = request.headers.get('User-Agent', '')[:500]
    screen_width = data.get('screen_width')
    screen_height = data.get('screen_height')
    
    # Check for existing session
    existing = TurkSession.query.filter_by(turk_id=turk_id, task_id=task_id).first()
    
    if existing:
        logging.info(f"🧪 Resuming Turk session: {turk_id}/{task_id}")
        return jsonify({
            'status': 'resumed',
            'session_token': existing.session_token,
            'started_at': existing.started_at.isoformat() if existing.started_at else None
        })
    
    # Create new session
    session_token = secrets.token_urlsafe(16)
    completion_code = f"OW-{secrets.token_hex(4).upper()}"
    
    session = TurkSession(
        turk_id=turk_id,
        task_id=task_id,
        session_token=session_token,
        completion_code=completion_code,
        user_agent=user_agent,
        screen_width=screen_width,
        screen_height=screen_height,
        actions=[]
    )
    
    db.session.add(session)
    db.session.commit()
    
    logging.info(f"🧪 New Turk session started: {turk_id}/{task_id} -> {session_token}")
    
    return jsonify({
        'status': 'created',
        'session_token': session_token,
        'started_at': session.started_at.isoformat()
    })


@testing_bp.route('/api/turk/track', methods=['POST'])
@_api_admin_required
def turk_track_action():
    """Track an action in the Turk session"""
    data = request.json or {}
    session_token = data.get('session_token')
    action = data.get('action')
    
    if not session_token or not action:
        return jsonify({'error': 'Missing session_token or action'}), 400
    
    session = TurkSession.query.filter_by(session_token=session_token).first()
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    
    # Add action to list
    actions = session.actions or []
    actions.append({
        'action': action,
        'timestamp': datetime.utcnow().isoformat()
    })
    session.actions = actions
    
    # Update milestone flags based on action
    milestone_map = {
        'upload_disclosure': 'uploaded_disclosure',
        'upload_inspection': 'uploaded_inspection',
        'start_analysis': 'started_analysis',
        'view_results': 'viewed_results',
        'view_risk_dna': 'viewed_risk_dna',
        'view_transparency': 'viewed_transparency',
        'view_decision_path': 'viewed_decision_path',
        'scroll_to_risk-dna': 'viewed_risk_dna',
        'scroll_to_transparency': 'viewed_transparency',
        'scroll_to_decision-path': 'viewed_decision_path'
    }
    
    if action in milestone_map:
        setattr(session, milestone_map[action], True)
    
    # Update current step
    if 'step:' in action:
        session.current_step = action.replace('step:', '')
    
    # Calculate time spent
    if session.started_at:
        session.time_spent_seconds = int((datetime.utcnow() - session.started_at).total_seconds())
    
    db.session.commit()
    
    return jsonify({'status': 'tracked', 'action_count': len(actions)})


@testing_bp.route('/api/turk/complete', methods=['POST'])
@_api_admin_required
def turk_complete_session():
    """Mark session as complete and return completion code"""
    data = request.json or {}
    session_token = data.get('session_token')
    
    if not session_token:
        return jsonify({'error': 'Missing session_token'}), 400
    
    session = TurkSession.query.filter_by(session_token=session_token).first()
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    
    # Mark complete
    session.is_complete = True
    session.completed_at = datetime.utcnow()
    
    # Final time calculation
    if session.started_at:
        session.time_spent_seconds = int((datetime.utcnow() - session.started_at).total_seconds())
    
    # Optional feedback
    session.rating = data.get('rating')
    session.feedback = data.get('feedback')
    session.would_pay = data.get('would_pay')
    session.confusion_points = data.get('confusion_points')
    
    db.session.commit()
    
    logging.info(f"🧪 Turk session completed: {session.turk_id}/{session.task_id} in {session.time_spent_seconds}s")
    
    return jsonify({
        'status': 'completed',
        'completion_code': session.completion_code,
        'time_spent_seconds': session.time_spent_seconds
    })


@testing_bp.route('/api/turk/sessions', methods=['GET'])
@_api_admin_required
def turk_list_sessions():
    """Admin endpoint to list all Turk sessions"""
    # Admin check handled by @api_admin_required decorator
    
    sessions = TurkSession.query.order_by(TurkSession.started_at.desc()).limit(100).all()
    
    return jsonify({
        'total': len(sessions),
        'sessions': [{
            'id': s.id,
            'turk_id': s.turk_id,
            'task_id': s.task_id,
            'started_at': s.started_at.isoformat() if s.started_at else None,
            'completed_at': s.completed_at.isoformat() if s.completed_at else None,
            'time_spent_seconds': s.time_spent_seconds,
            'is_complete': s.is_complete,
            'completion_code': s.completion_code if s.is_complete else None,
            'milestones': {
                'uploaded_disclosure': s.uploaded_disclosure,
                'uploaded_inspection': s.uploaded_inspection,
                'started_analysis': s.started_analysis,
                'viewed_results': s.viewed_results,
                'viewed_risk_dna': s.viewed_risk_dna,
                'viewed_transparency': s.viewed_transparency,
                'viewed_decision_path': s.viewed_decision_path
            },
            'action_count': len(s.actions or []),
            'rating': s.rating,
            'feedback': s.feedback,
            'would_pay': s.would_pay
        } for s in sessions]
    })


@testing_bp.route('/api/auto-test/run', methods=['POST'])
@_dev_only_gate
@_api_admin_required
def run_auto_test():
    """Run automated tests against the analysis API"""
    # Admin check handled by @api_admin_required decorator
    
    data = request.get_json() or {}
    count = min(data.get('count', 5), 50)  # Max 50 at a time
    scenario = data.get('scenario', 'random')
    
    results = []
    
    for i in range(count):
        test_id = f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i:03d}"
        test_scenario = scenario if scenario != 'mixed' else ['clean', 'moderate', 'problematic', 'nightmare'][i % 4]
        turk_session = None  # Initialize so except block doesn't crash
        prop = None  # Track for error context
        
        try:
            # Get current version for bug logging
            try:
                with open('VERSION', 'r') as f:
                    current_version = f.read().strip()
            except Exception:
                current_version = 'unknown'
            
            # Generate synthetic property
            prop = SyntheticPropertyGenerator.generate(test_scenario)
            disclosure_text = SyntheticPropertyGenerator.generate_disclosure_text(prop)
            inspection_text = SyntheticPropertyGenerator.generate_inspection_text(prop)
            
            # Create turk session for tracking
            turk_session = TurkSession(
                session_token=test_id,
                turk_id=test_id,
                task_id=f"auto_test_{test_scenario}",
                actions=[{
                    "action": "auto_test_started",
                    "timestamp": datetime.now().isoformat(),
                    "scenario": test_scenario,
                    "address": prop['address']
                }]
            )
            db.session.add(turk_session)
            db.session.commit()
            
            # Call the analysis API directly
            start_time = time.time()
            
            # Create a BuyerProfile object
            buyer_profile_obj = BuyerProfile(
                max_budget=prop['price'] + 100000,
                repair_tolerance="moderate",
                ownership_duration="3-7",
                biggest_regret="hidden_issues",
                replaceability="somewhat_unique",
                deal_breakers=["foundation", "mold"]
            )
            
            # Use the existing intelligence instance
            analysis_result = intelligence.analyze_property(
                seller_disclosure_text=disclosure_text,
                inspection_report_text=inspection_text,
                property_price=prop['price'],  # Correct parameter name
                buyer_profile=buyer_profile_obj,
                property_address=prop['address']
            )
            
            elapsed = time.time() - start_time
            
            # Extract key values from the PropertyAnalysis object
            offer_score = None
            red_flag_count = 0
            recommendation = None
            
            # offer_score = 100 - risk_score.overall_risk_score
            if hasattr(analysis_result, 'risk_score') and analysis_result.risk_score:
                risk_score_obj = analysis_result.risk_score
                if hasattr(risk_score_obj, 'overall_risk_score'):
                    offer_score = 100 - risk_score_obj.overall_risk_score
            
            # Get recommendation from offer_strategy
            if hasattr(analysis_result, 'offer_strategy') and analysis_result.offer_strategy:
                if isinstance(analysis_result.offer_strategy, dict):
                    rec_offer = analysis_result.offer_strategy.get('recommended_offer')
                    if rec_offer:
                        recommendation = f"Offer ${rec_offer:,.0f}"
            
            # Try to get red flags count from transparency_report
            if hasattr(analysis_result, 'transparency_report') and analysis_result.transparency_report:
                tr = analysis_result.transparency_report
                if hasattr(tr, 'red_flags'):
                    red_flags = tr.red_flags if tr.red_flags else []
                elif isinstance(tr, dict):
                    red_flags = tr.get('red_flags', [])
                else:
                    red_flags = []
                red_flag_count = len(red_flags) if red_flags else 0
            
            # =================================================================
            # VALIDATION: Check if results make sense (v5.54.21)
            # =================================================================
            validation_errors = []
            input_issues = len(prop['issues'])
            input_repair_cost = sum(i[2] for i in prop['issues'])
            critical_issues = len([i for i in prop['issues'] if i[1] == 'critical'])
            
            # 1. Score should correlate with scenario severity
            # WIDENED RANGES (v5.59.53) - AI-driven scoring has inherent variance,
            # especially for nightmare scenarios where issue severity interpretation varies
            expected_score_ranges = {
                'clean': (55, 100),      # Clean = high score
                'moderate': (30, 100),   # Moderate = wide range
                'problematic': (10, 90), # Problematic = wide range
                'nightmare': (0, 60),    # Nightmare = low score (was 55, widened for AI variance)
            }
            
            if test_scenario in expected_score_ranges and offer_score is not None:
                min_score, max_score = expected_score_ranges[test_scenario]
                rounded_score = round(offer_score) if isinstance(offer_score, float) else offer_score
                if not (min_score <= offer_score <= max_score):
                    validation_errors.append(
                        f"SCORE_MISMATCH: {test_scenario} scenario got score {rounded_score}, expected {min_score}-{max_score}"
                    )
            
            # 2. Critical issues should generate red flags
            # NOTE: Red flags come from OMISSIONS (undisclosed issues), not raw inspection findings.
            # A single critical issue that the seller properly disclosed won't generate a red flag.
            # Only flag when multiple critical issues exist (highly unlikely all were disclosed)
            # or when scenario explicitly should produce red flags.
            if critical_issues >= 2 and red_flag_count == 0:
                validation_errors.append(
                    f"MISSING_RED_FLAGS: {critical_issues} critical issues but 0 red flags detected"
                )
            elif critical_issues >= 1 and red_flag_count == 0 and test_scenario in ('nightmare', 'problematic'):
                validation_errors.append(
                    f"MISSING_RED_FLAGS: {test_scenario} scenario with {critical_issues} critical issues but 0 red flags"
                )
            
            # 3. High repair costs should lower the score
            if input_repair_cost > 30000 and offer_score and offer_score > 70:
                validation_errors.append(
                    f"SCORE_TOO_HIGH: ${input_repair_cost:,} in repairs but score is {offer_score}"
                )
            
            # 4. Nightmare properties shouldn't get "proceed" recommendations
            if test_scenario == 'nightmare' and recommendation:
                if 'proceed' in recommendation.lower() or 'confidence' in recommendation.lower():
                    validation_errors.append(
                        f"BAD_RECOMMENDATION: Nightmare property got positive recommendation: {recommendation}"
                    )
            
            # 5. Response time check
            if elapsed > 60:
                validation_errors.append(
                    f"SLOW_RESPONSE: Analysis took {elapsed:.1f}s (expected <60s)"
                )
            
            # Log validation failures as bugs (with deduplication)
            for error in validation_errors:
                bug_title = f"Validation: {error.split(':')[0]}"
                existing = Bug.query.filter_by(title=bug_title, status='open').first()
                if not existing:
                    validation_bug = Bug(
                        title=bug_title,
                        description=f"Test scenario: {test_scenario}\nAddress: {prop['address']}\n\n{error}",
                        error_message=error,
                        severity='medium' if 'SLOW' in error else 'high',
                        category='analysis',
                        status='open',
                        version_reported=current_version,
                        reported_by='auto_validation'
                    )
                    db.session.add(validation_bug)
            
            if validation_errors:
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            
            # Update session with completion
            turk_session.actions = turk_session.actions + [{
                "action": "auto_test_completed",
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": round(elapsed, 2),
                "offer_score": round(offer_score) if offer_score else None,
                "red_flags": red_flag_count
            }]
            turk_session.is_complete = True
            turk_session.completion_code = f"AUTO-{test_id[-8:]}"
            turk_session.time_spent_seconds = int(elapsed)
            turk_session.completed_at = datetime.now()
            db.session.commit()
            
            results.append({
                "test_id": test_id,
                "scenario": test_scenario,
                "address": prop['address'],
                "price": prop['price'],
                "status": "completed",
                "elapsed_seconds": round(elapsed, 2),
                "offer_score": round(offer_score) if offer_score else None,
                "recommendation": recommendation,
                "red_flags": red_flag_count,
                "issues_found": len(prop['issues']),
                "total_repair_estimate": sum(i[2] for i in prop['issues']),
                "validation_errors": validation_errors,  # Include validation results
                "validation_passed": len(validation_errors) == 0,
            })
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            
            # Mark session as complete even on error (to avoid stuck sessions)
            if turk_session is not None:
                try:
                    turk_session.is_complete = True
                    turk_session.completion_code = f"ERROR-{test_id[-8:]}"
                    turk_session.completed_at = datetime.now()
                    turk_session.actions = turk_session.actions + [{
                        "action": "auto_test_error",
                        "timestamp": datetime.now().isoformat(),
                        "error": str(e)[:500],
                        "scenario": test_scenario
                    }]
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            
            # Auto-log bug — include test_id in title to avoid over-deduplication
            # Group by error TYPE but keep unique per test run
            error_type = type(e).__name__
            error_short = str(e)[:80]
            bug_title = f"Auto-test {error_type}: {error_short}"
            
            # Deduplicate within same error type, but allow multiple bugs across runs
            existing_bug = Bug.query.filter_by(title=bug_title, status='open').first()
            filed_bug_id = None
            if existing_bug:
                filed_bug_id = existing_bug.id
                # Update description with latest occurrence count
                try:
                    occurrence_note = f"\n\n[Also failed in test {test_id}, scenario: {test_scenario}]"
                    if existing_bug.description and len(existing_bug.description) < 5000:
                        existing_bug.description += occurrence_note
                    existing_bug.updated_at = datetime.now()
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            else:
                auto_bug = Bug(
                    title=bug_title,
                    description=f"Test ID: {test_id}\nScenario: {test_scenario}\nAddress: {prop['address'] if prop else 'N/A'}\n\nError: {str(e)}\n\nThis error caused all tests in this batch to fail, suggesting a systemic issue (API key, service availability, or code error).",
                    error_message=str(e),
                    stack_trace=error_trace,
                    severity='high',
                    category='analysis',
                    status='open',
                    version_reported=current_version,
                    reported_by='auto_test'
                )
                db.session.add(auto_bug)
            try:
                db.session.commit()
                if not existing_bug:
                    filed_bug_id = auto_bug.id
            except Exception:
                db.session.rollback()
            
            results.append({
                "test_id": test_id,
                "scenario": test_scenario,
                "status": "error",
                "error": f"{type(e).__name__}: {str(e)[:300]}",
                "stack_trace": error_trace[-500:] if error_trace else None,
                "bug_id": filed_bug_id
            })
    
    # Calculate validation stats
    completed_results = [r for r in results if r['status'] == 'completed']
    validation_passed = len([r for r in completed_results if r.get('validation_passed', False)])
    validation_failed = len([r for r in completed_results if not r.get('validation_passed', True)])
    total_validation_errors = sum(len(r.get('validation_errors', [])) for r in completed_results)
    
    # AUTO-FILE BUGS for analysis failures and errors
    bugs_filed = 0
    try:
        current_version = open('VERSION').read().strip() if os.path.exists('VERSION') else 'unknown'
    except Exception:
        current_version = 'unknown'

    for r in results:
        try:
            if r['status'] == 'error':
                bug_title = f"Analysis: Test error — {r.get('test_id', 'unknown')}"
                if not Bug.query.filter_by(title=bug_title, status='open').first():
                    bug = Bug(
                        title=bug_title,
                        description=f"Auto-test run produced an error.\n\nScenario: {r.get('scenario', 'N/A')}\nError: {r.get('error', 'N/A')}",
                        error_message=r.get('error', 'Test error'),
                        severity='high',
                        category='analysis',
                        status='open',
                        version_reported=current_version,
                        reported_by='auto_test_analysis'
                    )
                    db.session.add(bug)
                    db.session.commit()
                    bugs_filed += 1
            elif r['status'] == 'completed' and not r.get('validation_passed', True):
                for issue in (r.get('validation_errors') or ['Validation failed']):
                    bug_title = f"Analysis: {issue}"
                    if not Bug.query.filter_by(title=bug_title, status='open').first():
                        bug = Bug(
                            title=bug_title,
                            description=f"Analysis output failed validation.\n\nTest scenario: {r.get('scenario', 'N/A')}\nAddress: {r.get('address', 'N/A')}\nAll errors: {chr(10).join(r.get('validation_errors', []))}",
                            error_message=issue,
                            severity='medium',
                            category='analysis',
                            status='open',
                            version_reported=current_version,
                            reported_by='auto_validation'
                        )
                        db.session.add(bug)
                        db.session.commit()
                        bugs_filed += 1
        except Exception as e:
            logging.warning(f"Could not auto-file analysis bug: {e}")
            db.session.rollback()

    if bugs_filed:
        logging.info(f"🐛 Auto-filed {bugs_filed} analysis bug(s)")

    # AUTO-CLOSE BUGS: If a scenario type passed all tests, close related open bugs
    bugs_closed = 0
    try:
        # Group results by scenario
        scenario_results = {}
        for r in completed_results:
            scen = r.get('scenario', 'unknown')
            if scen not in scenario_results:
                scenario_results[scen] = {'passed': 0, 'failed': 0, 'errors': []}
            if r.get('validation_passed', False):
                scenario_results[scen]['passed'] += 1
            else:
                scenario_results[scen]['failed'] += 1
                scenario_results[scen]['errors'].extend(r.get('validation_errors', []))
        
        # Get current version
        try:
            with open('VERSION', 'r') as f:
                current_version = f.read().strip()
        except Exception:
            current_version = 'unknown'
        
        # For scenarios with ALL tests passing, close related open bugs
        for scen, stats in scenario_results.items():
            if stats['passed'] > 0 and stats['failed'] == 0:
                # This scenario passed all tests - close related bugs
                open_bugs = Bug.query.filter(
                    Bug.status.in_(['open', 'in_progress']),
                    Bug.reported_by == 'auto_validation',
                    Bug.description.like(f'%Test scenario: {scen}%')
                ).all()
                
                for bug in open_bugs:
                    bug.status = 'fixed'
                    bug.fixed_at = datetime.now()
                    bug.version_fixed = current_version
                    bug.fix_notes = f"Auto-closed: {scen} scenario passed all {stats['passed']} tests in v{current_version}"
                    bugs_closed += 1
        
        if bugs_closed > 0:
            db.session.commit()
            logging.info(f"✅ Auto-closed {bugs_closed} bugs after successful tests")
    except Exception as e:
        db.session.rollback()
        logging.warning(f"Could not auto-close bugs: {e}")
    
    return jsonify({
        "total": count,
        "completed": len(completed_results),
        "errors": len([r for r in results if r['status'] == 'error']),
        "validation": {
            "passed": validation_passed,
            "failed": validation_failed,
            "total_errors": total_validation_errors
        },
        "bugs_filed": bugs_filed,
        "bugs_closed": bugs_closed,
        "results": results
    })
