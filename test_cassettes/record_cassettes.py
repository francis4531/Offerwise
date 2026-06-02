"""
record_cassettes.py — v5.88.19

Records VCR cassettes for /api/analyze by exercising the orchestrator
against real external APIs once. Cassettes (test_cassettes/cassettes/*.yaml)
are then replayed by test_e2e_analyze_cassettes.py without any API calls,
so CI runs cost zero.

USAGE — run this once locally with real API keys set:

    cd offerwise_render
    export ANTHROPIC_API_KEY='sk-ant-...'
    export RENTCAST_API_KEY='...'      # if you have one
    export HUNTER_API_KEY='...'         # if you have one
    # Set any other API keys your orchestrator uses
    python test_cassettes/record_cassettes.py

The script will:
  1. Spin up the Flask app with a SQLite test DB
  2. Create a test user with credits
  3. Run /api/analyze for each PDF in test_corpus/
  4. Record each request/response pair to test_cassettes/cassettes/*.yaml
  5. Sanitize: strip ANTHROPIC_API_KEY, RENTCAST_API_KEY, etc. from headers
     so cassettes are safe to commit

Re-run quarterly OR after any prompt template change OR if RentCast/etc
change their response shape. Old cassettes in test_cassettes/cassettes/
get overwritten — keep them in git so you can diff what changed.

CRITICAL: cassettes contain RECORDED responses. They will NOT detect
changes in upstream APIs unless you re-record. Plan to re-record:
  - Anytime intelligence_engine.py changes (orchestrator output shape)
  - Anytime a prompt template changes (offerwise_intelligence.py)
  - Anytime you suspect a model behavior change
  - Quarterly even if nothing else triggers it
"""
import os
import sys
import json
from pathlib import Path

# Refuse to run without real API keys — recording mode requires them
if not os.environ.get('ANTHROPIC_API_KEY'):
    print('❌ ANTHROPIC_API_KEY not set — cannot record cassettes')
    print('   Set it with: export ANTHROPIC_API_KEY="sk-ant-..."')
    sys.exit(1)

# Ensure we're in the right directory
THIS_DIR = Path(__file__).parent
REPO_ROOT = THIS_DIR.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))

CASSETTE_DIR = THIS_DIR / 'cassettes'
CASSETTE_DIR.mkdir(exist_ok=True)

# Configure test env BEFORE importing app
os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-cassette-record'
os.environ['DATABASE_URL'] = 'sqlite:///test_cassette_record.db'
os.environ['ADMIN_KEY'] = 'test-admin-cassette'
os.environ['RATELIMIT_ENABLED'] = 'false'
os.environ['VCR_RECORD_MODE'] = 'all'  # Record everything fresh

# Clean any prior recording DB
if Path('test_cassette_record.db').exists():
    Path('test_cassette_record.db').unlink()

import vcr
from datetime import datetime

# Sensitive headers/params to sanitize from cassettes
SENSITIVE_HEADERS = [
    'authorization', 'x-api-key', 'anthropic-api-key',
    'cookie', 'set-cookie',
]
SENSITIVE_QUERY_PARAMS = ['api_key', 'apikey', 'key']
SENSITIVE_POST_DATA = ['api_key', 'apikey', 'key']


def _sanitize_request(request):
    """Strip secrets from cassette before write."""
    for h in SENSITIVE_HEADERS:
        if h in request.headers:
            request.headers[h] = '<REDACTED>'
    return request


def _sanitize_response(response):
    """Response sanitization — most upstream APIs don't echo secrets back,
    but we still scrub any auth headers in case."""
    if response and 'headers' in response:
        for h in SENSITIVE_HEADERS:
            if h in response['headers']:
                response['headers'][h] = ['<REDACTED>']
    return response


def _make_vcr():
    return vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode='all',  # Always re-record when this script runs
        match_on=['method', 'scheme', 'host', 'path'],  # Don't match on query/body
        filter_headers=[(h, '<REDACTED>') for h in SENSITIVE_HEADERS],
        filter_query_parameters=[(p, '<REDACTED>') for p in SENSITIVE_QUERY_PARAMS],
        filter_post_data_parameters=[(p, '<REDACTED>') for p in SENSITIVE_POST_DATA],
        before_record_request=_sanitize_request,
        before_record_response=_sanitize_response,
        decode_compressed_response=True,
    )


def main():
    print('🎬 Recording /api/analyze cassettes...')
    print(f'   Cassette dir: {CASSETTE_DIR}')
    print(f'   Started: {datetime.now().isoformat()}')
    print()

    from app import app
    from models import db, User

    # Create a test user with plenty of credits
    test_email = 'cassette_recorder@e2e-cassette.test.example.com'
    with app.app_context():
        existing = User.query.filter_by(email=test_email).first()
        if existing:
            db.session.delete(existing)
            db.session.commit()

        user = User(
            email=test_email,
            name='Cassette Recorder',
            auth_provider='email', tier='enterprise',
            analysis_credits=100,
            analyses_completed=0,
            stripe_customer_id='cus_cassette_recorder',
        )
        user.set_password('CassetteRecord123!')
        db.session.add(user)
        db.session.commit()
        uid = user.id
        print(f'✅ Test user created: id={uid}')

    client = app.test_client(use_cookies=True)
    with client.session_transaction() as sess:
        sess['_user_id'] = str(uid)
        sess['_fresh'] = True
    print('✅ Test session established')
    print()

    # ---- Cassette 1: address-only (no documents) ----
    cassette_name = 'analyze_address_only.yaml'
    print(f'🎙️  Recording: {cassette_name}')
    print('   Request: address-only analysis, $500K property')

    vcr_inst = _make_vcr()
    try:
        with vcr_inst.use_cassette(cassette_name):
            r = client.post('/api/analyze', json={
                'property_address': '123 Cassette Test Lane, San Jose, CA',
                'property_price': 500000,
                # No disclosure or inspection text → address_only path
            }, headers={'Origin': 'https://www.getofferwise.ai'})
        print(f'   Response: {r.status_code}')
        if r.status_code == 200:
            data = r.get_json()
            print(f'   Risk score: {data.get("risk_score", {}).get("composite_score")}')
            print(f'   Recommended offer: ${data.get("offer_strategy", {}).get("recommended_offer", "N/A"):,}'
                  if isinstance(data.get('offer_strategy', {}).get('recommended_offer'), (int, float))
                  else f'   Recommended offer: {data.get("offer_strategy", {}).get("recommended_offer")}')
        else:
            print(f'   ⚠️  Non-200 response: {r.data[:200]!r}')
        print()
    except Exception as e:
        print(f'   ❌ ERROR: {e}')
        print()

    # ---- Cassette 2: full disclosure path with clean PDF ----
    pdf_path = REPO_ROOT / 'test_corpus' / '01_digital_tds_clean.pdf'
    if pdf_path.exists():
        cassette_name = 'analyze_clean_disclosure.yaml'
        print(f'🎙️  Recording: {cassette_name}')
        print(f'   Request: full analysis with {pdf_path.name}')

        # Read and base64 encode the PDF
        import base64
        pdf_bytes = pdf_path.read_bytes()
        pdf_b64 = base64.b64encode(pdf_bytes).decode('ascii')

        vcr_inst = _make_vcr()
        try:
            with vcr_inst.use_cassette(cassette_name):
                r = client.post('/api/analyze', json={
                    'property_address': '456 Clean Disclosure St, Oakland, CA',
                    'property_price': 750000,
                    'seller_disclosure_pdf_base64': pdf_b64,
                    'seller_disclosure_filename': pdf_path.name,
                }, headers={'Origin': 'https://www.getofferwise.ai'})
            print(f'   Response: {r.status_code}')
            if r.status_code == 200:
                data = r.get_json()
                print(f'   Risk score: {data.get("risk_score", {}).get("composite_score")}')
            print()
        except Exception as e:
            print(f'   ❌ ERROR: {e}')
            print()
    else:
        print(f'⚠️  Skipping disclosure cassette: {pdf_path} not found')
        print()

    # ---- Cassette 3: nightmare disclosure ("nothing to disclose" red flags) ----
    pdf_path = REPO_ROOT / 'test_corpus' / '03_digital_tds_nightmare_no_disclosure.pdf'
    if pdf_path.exists():
        cassette_name = 'analyze_nightmare_disclosure.yaml'
        print(f'🎙️  Recording: {cassette_name}')
        print(f'   Request: full analysis with {pdf_path.name}')

        import base64
        pdf_bytes = pdf_path.read_bytes()
        pdf_b64 = base64.b64encode(pdf_bytes).decode('ascii')

        vcr_inst = _make_vcr()
        try:
            with vcr_inst.use_cassette(cassette_name):
                r = client.post('/api/analyze', json={
                    'property_address': '789 Nightmare Rd, Berkeley, CA',
                    'property_price': 900000,
                    'seller_disclosure_pdf_base64': pdf_b64,
                    'seller_disclosure_filename': pdf_path.name,
                }, headers={'Origin': 'https://www.getofferwise.ai'})
            print(f'   Response: {r.status_code}')
            if r.status_code == 200:
                data = r.get_json()
                print(f'   Risk score: {data.get("risk_score", {}).get("composite_score")}')
                # Nightmare disclosure should produce HIGH risk score
                composite = data.get('risk_score', {}).get('composite_score') or 0
                if composite < 40:
                    print(f'   ⚠️  Suspiciously LOW risk for nightmare disclosure ({composite})')
                else:
                    print(f'   ✅ High risk score as expected ({composite})')
            print()
        except Exception as e:
            print(f'   ❌ ERROR: {e}')
            print()
    else:
        print(f'⚠️  Skipping nightmare cassette: {pdf_path} not found')
        print()

    # ---- Cleanup test DB ----
    if Path('test_cassette_record.db').exists():
        Path('test_cassette_record.db').unlink()

    # ---- Summary ----
    cassettes = sorted(CASSETTE_DIR.glob('*.yaml'))
    print('=' * 60)
    print(f'✅ Recording complete: {len(cassettes)} cassettes saved')
    for c in cassettes:
        size_kb = c.stat().st_size / 1024
        print(f'   • {c.name} ({size_kb:.1f} KB)')
    print()
    print('NEXT STEPS:')
    print('  1. Review the cassettes in test_cassettes/cassettes/')
    print('     and confirm no API keys leaked through.')
    print('  2. git add test_cassettes/cassettes/*.yaml')
    print('  3. git commit -m "Record /api/analyze cassettes (date)"')
    print('  4. Run pytest test_e2e_analyze_cassettes.py to verify replay works')


if __name__ == '__main__':
    main()
