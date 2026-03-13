"""
OfferWise Docrepo Routes Blueprint
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
from models import db

logger = logging.getLogger(__name__)

docrepo_bp = Blueprint('docrepo', __name__)

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


def init_docrepo_blueprint(app, admin_required_fn, api_admin_required_fn,
                       api_login_required_fn, dev_only_gate_fn, limiter):
    _admin_required_ref[0] = admin_required_fn
    _api_admin_required_ref[0] = api_admin_required_fn
    _api_login_required_ref[0] = api_login_required_fn
    _dev_only_gate_ref[0] = dev_only_gate_fn
    _limiter_ref[0] = limiter
    app.register_blueprint(docrepo_bp)
    logger.info("✅ Docrepo Routes blueprint registered")



@docrepo_bp.route('/api/docrepo/catalog')
@_api_admin_required
def docrepo_catalog():
    """Return the document repository catalog (v5.62.23)"""
    import json as json_mod
    catalog_path = os.path.join(os.path.dirname(__file__), 'document_repo', 'metadata', 'catalog.json')
    if not os.path.exists(catalog_path):
        return jsonify({"error": "Catalog not found", "documents": [], "collection_stats": {"total_documents": 0}, "sources_tracked": []})
    try:
        with open(catalog_path, 'r') as f:
            catalog = json_mod.load(f)
        
        # Enrich with actual file sizes (checks persistent disk + local)
        total_bytes = 0
        for doc in catalog.get('documents', []):
            fpath = _resolve_doc_path(doc)
            if fpath:
                sz = os.path.getsize(fpath)
                doc['file_size_bytes'] = sz
                doc['file_exists'] = True
                doc['file_location'] = 'disk' if fpath.startswith(DOCREPO_DISK_PATH) else 'local'
                total_bytes += sz
            else:
                doc['file_size_bytes'] = 0
                doc['file_exists'] = False
                doc['file_location'] = 'missing'
        
        # Update total size
        if 'collection_stats' in catalog:
            catalog['collection_stats']['total_size_mb'] = round(total_bytes / (1024 * 1024), 1)
            # Add disk status
            disk_exists = os.path.isdir(DOCREPO_DISK_PATH)
            on_disk = sum(1 for d in catalog.get('documents', []) if d.get('file_location') == 'disk')
            on_local = sum(1 for d in catalog.get('documents', []) if d.get('file_location') == 'local')
            missing = sum(1 for d in catalog.get('documents', []) if d.get('file_location') == 'missing')
            catalog['collection_stats']['disk_path'] = DOCREPO_DISK_PATH
            catalog['collection_stats']['disk_mounted'] = disk_exists
            catalog['collection_stats']['on_disk'] = on_disk
            catalog['collection_stats']['on_local'] = on_local
            catalog['collection_stats']['missing'] = missing
        
        return jsonify(catalog)
    except Exception as e:
        logging.error(f"Error loading docrepo catalog: {e}")
        return jsonify({"error": "Failed to load catalog", "documents": [], "collection_stats": {"total_documents": 0}, "sources_tracked": []}), 500


@docrepo_bp.route('/api/docrepo/download/<doc_id>')
@_api_admin_required
def docrepo_download(doc_id):
    """Download a document from the repository (v5.62.23)"""
    import json as json_mod
    catalog_path = os.path.join(os.path.dirname(__file__), 'document_repo', 'metadata', 'catalog.json')
    if not os.path.exists(catalog_path):
        return jsonify({"error": "Catalog not found"}), 404
    
    try:
        with open(catalog_path, 'r') as f:
            catalog = json_mod.load(f)
        
        doc = next((d for d in catalog.get('documents', []) if d['id'] == doc_id), None)
        if not doc:
            return jsonify({"error": f"Document {doc_id} not found"}), 404
        
        fpath = _resolve_doc_path(doc)
        if not fpath:
            return jsonify({"error": f"File not found: {doc.get('filename', '')}"}), 404
        
        from flask import send_file
        return send_file(fpath, as_attachment=False, download_name=doc['filename'])
    except Exception as e:
        logging.error(f"Error downloading docrepo file: {e}")
        return jsonify({"error": "Failed to download document"}), 500


@docrepo_bp.route('/api/docrepo/test/<doc_id>')
@_api_admin_required
def docrepo_test_parser(doc_id):
    """Run a document through the extraction + parsing pipeline (v5.62.25)."""
    import json as json_mod
    import time as time_mod

    catalog_path = os.path.join(os.path.dirname(__file__), 'document_repo', 'metadata', 'catalog.json')
    if not os.path.exists(catalog_path):
        return jsonify({"error": "Catalog not found"}), 404

    with open(catalog_path, 'r') as f:
        catalog = json_mod.load(f)

    doc = next((d for d in catalog.get('documents', []) if d['id'] == doc_id), None)
    if not doc:
        return jsonify({"error": f"Document {doc_id} not found"}), 404

    fpath = _resolve_doc_path(doc)
    if not fpath:
        return jsonify({"error": f"File not found for {doc_id}"}), 404

    start = time_mod.time()
    result = {
        'doc_id': doc_id,
        'filename': doc.get('filename'),
        'category': doc.get('category'),
        'format': doc.get('format'),
        'steps': [],
        'success': False,
    }

    try:
        # Step 1: Text extraction
        fmt = doc.get('format', '')
        if fmt == 'html':
            text = _extract_text_from_html(fpath)
            method = 'html_strip'
        else:
            from pdf_handler import PDFHandler, is_meaningful_extraction
            handler = PDFHandler()
            extract_result = handler.extract_text_from_file(fpath)
            text = extract_result.get('text', '')
            method = extract_result.get('method', 'unknown')

        text_len = len(text)
        result['steps'].append({
            'step': 'extraction',
            'success': text_len > 50,
            'method': method,
            'text_length': text_len,
            'text_preview': text[:500] + ('...' if text_len > 500 else ''),
        })

        if text_len < 50:
            result['steps'].append({'step': 'parsing', 'success': False, 'error': 'Insufficient text extracted'})
            result['elapsed_ms'] = int((time_mod.time() - start) * 1000)
            return jsonify(result)

        # Step 2: Document type detection
        from pdf_handler import PDFHandler as PH2
        handler2 = PH2()
        detected_type = handler2.detect_document_type(text)
        expected_type = 'inspection_report' if doc.get('category') == 'inspection_report' else (
            'seller_disclosure' if doc.get('category') == 'disclosure_statement' else 'unknown')
        type_match = detected_type == expected_type or doc.get('category') == 'reference'
        result['steps'].append({
            'step': 'type_detection',
            'success': True,
            'detected_type': detected_type,
            'expected_type': expected_type,
            'match': type_match,
        })

        # Step 3: Parse with DocumentParser
        from document_parser import DocumentParser
        parser = DocumentParser()

        if detected_type == 'inspection_report' or (doc.get('category') == 'inspection_report' and detected_type == 'unknown'):
            parsed = parser.parse_inspection_report(text)
            findings = len(parsed.inspection_findings) if parsed.inspection_findings else 0
            address = parsed.property_address
            # Summarize findings by severity
            severity_counts = {}
            for f in (parsed.inspection_findings or []):
                sev = f.severity.value if hasattr(f.severity, 'value') else str(f.severity)
                severity_counts[sev] = severity_counts.get(sev, 0) + 1
            # Top findings
            top_findings = []
            for f in (parsed.inspection_findings or [])[:5]:
                top_findings.append({
                    'category': f.category.value if hasattr(f.category, 'value') else str(f.category),
                    'severity': f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
                    'description': f.description[:120],
                    'cost_low': f.estimated_cost_low,
                    'cost_high': f.estimated_cost_high,
                })
            result['steps'].append({
                'step': 'parsing',
                'success': findings > 0,
                'parser_type': 'inspection_report',
                'findings_count': findings,
                'address_detected': address,
                'severity_breakdown': severity_counts,
                'top_findings': top_findings,
            })
            result['success'] = findings > 0

        elif detected_type == 'seller_disclosure' or doc.get('category') == 'disclosure_statement':
            parsed = parser.parse_seller_disclosure(text)
            items = len(parsed.disclosure_items) if parsed.disclosure_items else 0
            address = parsed.property_address
            disclosed_yes = sum(1 for i in (parsed.disclosure_items or []) if i.disclosed)
            disclosed_no = items - disclosed_yes
            top_items = []
            for i in (parsed.disclosure_items or [])[:5]:
                top_items.append({
                    'category': i.category,
                    'question': i.question[:100],
                    'disclosed': i.disclosed,
                })
            result['steps'].append({
                'step': 'parsing',
                'success': items > 0,
                'parser_type': 'seller_disclosure',
                'disclosure_items_count': items,
                'disclosed_yes': disclosed_yes,
                'disclosed_no': disclosed_no,
                'address_detected': address,
                'top_items': top_items,
            })
            result['success'] = items > 0

        else:
            result['steps'].append({
                'step': 'parsing',
                'success': True,
                'parser_type': 'reference_doc',
                'note': f'Reference document detected as "{detected_type}" — no structured parsing needed',
            })
            result['success'] = True

    except Exception as e:
        logging.error(f"Parser test error for {doc_id}: {e}")
        import traceback
        result['steps'].append({
            'step': 'error',
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()[-500:],
        })

    result['elapsed_ms'] = int((time_mod.time() - start) * 1000)

    # Update catalog with test results
    try:
        for d in catalog['documents']:
            if d['id'] == doc_id:
                d['parser_tested'] = True
                d['parser_result'] = 'pass' if result['success'] else 'fail'
                d['parser_test_date'] = datetime.now().strftime('%Y-%m-%d %H:%M')
                break
        with open(catalog_path, 'w') as f:
            json_mod.dump(catalog, f, indent=2)
    except Exception as e:
        logging.warning(f"Could not update catalog: {e}")

    return jsonify(result)


@docrepo_bp.route('/api/docrepo/anonymize/<doc_id>')
@_api_admin_required
def docrepo_anonymize(doc_id):
    """
    Anonymize a document: strip PII (names, addresses, phone numbers, emails, 
    license numbers, signatures) from text. Returns anonymized text preview.
    For PDFs, creates an anonymized text version. (v5.62.25)
    """
    import json as json_mod
    import re as re_mod

    catalog_path = os.path.join(os.path.dirname(__file__), 'document_repo', 'metadata', 'catalog.json')
    with open(catalog_path, 'r') as f:
        catalog = json_mod.load(f)

    doc = next((d for d in catalog.get('documents', []) if d['id'] == doc_id), None)
    if not doc:
        return jsonify({"error": f"Document {doc_id} not found"}), 404

    fpath = _resolve_doc_path(doc)
    if not fpath:
        return jsonify({"error": f"File not found for {doc_id}"}), 404

    # Extract text
    fmt = doc.get('format', '')
    if fmt == 'html':
        text = _extract_text_from_html(fpath)
    else:
        from pdf_handler import PDFHandler
        handler = PDFHandler()
        extract_result = handler.extract_text_from_file(fpath)
        text = extract_result.get('text', '')

    original_len = len(text)
    replacements = []

    # --- PII PATTERNS ---

    # Email addresses
    email_pat = re_mod.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
    for m in email_pat.finditer(text):
        replacements.append(('email', m.group(), m.start(), m.end()))

    # Phone numbers (various formats)
    phone_pat = re_mod.compile(
        r'(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}'
    )
    for m in phone_pat.finditer(text):
        replacements.append(('phone', m.group(), m.start(), m.end()))

    # Street addresses (number + street name patterns)
    addr_pat = re_mod.compile(
        r'\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][a-zA-Z]+\s+){1,4}'
        r'(?:St(?:reet)?|Ave(?:nue)?|Blvd|Boulevard|Dr(?:ive)?|Ln|Lane|'
        r'Ct|Court|Rd|Road|Way|Pl|Place|Cir|Circle|Pkwy|Hwy|Ter(?:race)?)\b\.?'
        r'(?:\s*,?\s*(?:Apt|Suite|Ste|Unit|#)\s*\w+)?',
        re_mod.IGNORECASE
    )
    for m in addr_pat.finditer(text):
        replacements.append(('address', m.group(), m.start(), m.end()))

    # License/certificate numbers
    license_pat = re_mod.compile(
        r'(?:License|Lic|Certificate|Cert|Inspector)\s*#?\s*:?\s*#?\s*(\d{3,10})',
        re_mod.IGNORECASE
    )
    for m in license_pat.finditer(text):
        replacements.append(('license', m.group(), m.start(), m.end()))

    # URLs with personal info
    url_pat = re_mod.compile(r'https?://(?:www\.)?[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:/\S*)?')
    for m in url_pat.finditer(text):
        replacements.append(('url', m.group(), m.start(), m.end()))

    # Person names near "Inspector:", "Prepared for:", "Client:", "Seller:", "Buyer:"
    name_context_pat = re_mod.compile(
        r'(?:Inspector|Inspected\s+[Bb]y|Prepared\s+(?:for|by)|Client|Seller|Buyer|Agent|'
        r'Broker|Owner|Licensee)\s*:?\s*([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3})',
        re_mod.MULTILINE
    )
    for m in name_context_pat.finditer(text):
        replacements.append(('person_name', m.group(1), m.start(1), m.end(1)))

    # ZIP codes (standalone 5-digit or ZIP+4)
    zip_pat = re_mod.compile(r'\b\d{5}(?:-\d{4})?\b')
    for m in zip_pat.finditer(text):
        # Only redact if near address context
        context = text[max(0, m.start() - 80):m.end()]
        if any(w in context.lower() for w in ['city', 'state', 'zip', ',', 'ca ', 'wa ', 'tx ']):
            replacements.append(('zip', m.group(), m.start(), m.end()))

    # Sort by position descending for safe replacement
    replacements.sort(key=lambda x: x[2], reverse=True)

    # Remove overlapping matches (keep the longer one)
    cleaned = []
    last_start = len(text)
    for r in replacements:
        if r[3] <= last_start:
            cleaned.append(r)
            last_start = r[2]
    cleaned.reverse()

    # Apply redactions
    anon_text = text
    redaction_map = {
        'email': '[EMAIL_REDACTED]',
        'phone': '[PHONE_REDACTED]',
        'address': '[ADDRESS_REDACTED]',
        'license': '[LICENSE_REDACTED]',
        'url': '[URL_REDACTED]',
        'person_name': '[NAME_REDACTED]',
        'zip': '[ZIP_REDACTED]',
    }
    # Re-sort ascending for correct replacement
    cleaned.sort(key=lambda x: x[2], reverse=True)
    for pii_type, value, start, end in cleaned:
        anon_text = anon_text[:start] + redaction_map.get(pii_type, '[REDACTED]') + anon_text[end:]

    # Count by type
    counts = {}
    for pii_type, value, _, _ in cleaned:
        counts[pii_type] = counts.get(pii_type, 0) + 1

    # Save anonymized text
    anon_dir = os.path.join(DOCREPO_DISK_PATH, 'anonymized') if os.path.isdir(DOCREPO_DISK_PATH) else os.path.join(os.path.dirname(__file__), 'document_repo', 'anonymized')
    os.makedirs(anon_dir, exist_ok=True)
    anon_filename = f"{doc_id}_anonymized.txt"
    anon_path = os.path.join(anon_dir, anon_filename)
    with open(anon_path, 'w', encoding='utf-8') as f:
        f.write(anon_text)

    return jsonify({
        'doc_id': doc_id,
        'original_length': original_len,
        'anonymized_length': len(anon_text),
        'pii_found': sum(counts.values()),
        'pii_breakdown': counts,
        'pii_samples': [{'type': t, 'value': v[:30] + '...' if len(v) > 30 else v}
                        for t, v, _, _ in cleaned[:10]],
        'anonymized_preview': anon_text[:1000] + ('...' if len(anon_text) > 1000 else ''),
        'saved_to': anon_filename,
    })


@docrepo_bp.route('/api/docrepo/check-sources')
@_api_admin_required
def docrepo_check_sources():
    """
    Check CA DRE enforcement actions for recent months.
    Scrapes the DRE ASP endpoint which returns server-rendered HTML tables
    with disciplinary actions against licensees. (v5.62.31)
    """
    import json as json_mod
    import re as re_mod
    import requests
    from datetime import datetime, timedelta

    results = {
        'sources_checked': [],
        'new_documents_found': 0,
        'details': [],
        'enforcement_actions': [],
    }

    # --- 1. Check CA DRE Enforcement Actions (last 3 months) ---
    try:
        now = datetime.now()
        all_actions = []

        for months_ago in range(3):
            # Calculate month start/end
            dt = now.replace(day=1) - timedelta(days=months_ago * 28)
            start = dt.replace(day=1)
            # Last day of month
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)

            url = (
                f"https://secure.dre.ca.gov/publicasp/EnforcementActions.asp"
                f"?StartDate={start.strftime('%m/%d/%Y')}"
                f"&EndDate={end.strftime('%m/%d/%Y')}"
            )

            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code != 200:
                    continue

                # Parse HTML table rows: each <tr><td>...</td>...</tr> is an action
                rows = re_mod.findall(r'<tr><td>(.*?)</td></tr>', resp.text, re_mod.DOTALL)
                month_actions = []

                for row in rows:
                    cells = re_mod.findall(r'<td>(.*?)</td>', '<td>' + row)
                    if len(cells) >= 10:
                        action = {
                            'case_number': cells[0].strip(),
                            'city': cells[2].strip(),
                            'action_type': cells[3].strip(),
                            'licensee': cells[4].strip(),
                            'license_type': cells[5].strip(),
                            'address': cells[6].strip().replace('\r', ', '),
                            'filed_date': cells[7].strip(),
                            'effective_date': cells[8].strip(),
                            'violations': cells[9].strip(),
                            'category': cells[10].strip() if len(cells) > 10 else '',
                            'month': start.strftime('%B %Y'),
                        }
                        month_actions.append(action)

                all_actions.extend(month_actions)

            except Exception:
                continue

        # Categorize actions
        disclosure_related = [a for a in all_actions if any(
            code in a.get('violations', '') for code in [
                '10176', '10177(d)', '10177(g)', '10186',
                '10130', '10137', '10138', '10139',
            ]
        )]

        results['sources_checked'].append({
            'source': 'CA DRE Enforcement Actions',
            'url': 'https://secure.dre.ca.gov/publicasp/EnforcementActions.asp',
            'status': 'checked',
            'total_actions': len(all_actions),
            'disclosure_related': len(disclosure_related),
            'months_checked': 3,
            'action_types': {},
        })

        # Count action types
        action_counts = {}
        for a in all_actions:
            at = a['action_type']
            action_counts[at] = action_counts.get(at, 0) + 1
        results['sources_checked'][-1]['action_types'] = dict(
            sorted(action_counts.items(), key=lambda x: -x[1])[:10]
        )

        # Include disclosure-related actions as details
        for a in disclosure_related[:20]:
            results['details'].append({
                'source': 'CA DRE',
                'case_number': a['case_number'],
                'licensee': a['licensee'],
                'action': a['action_type'],
                'violations': a['violations'],
                'date': a['effective_date'],
                'city': a['city'],
            })

        results['enforcement_actions'] = all_actions[:50]
        results['new_documents_found'] = len(disclosure_related)

    except Exception as e:
        logging.error(f"DRE source check error: {e}")
        results['sources_checked'].append({
            'source': 'CA DRE Enforcement Actions',
            'status': 'error',
            'error': str(e),
        })

    # --- 2. InterNACHI (info only — scraping blocked) ---
    results['sources_checked'].append({
        'source': 'InterNACHI Sample Reports',
        'url': 'https://www.nachi.org',
        'status': 'info',
        'note': '13 reports downloaded. Site blocks server-side requests (403). Check manually for new samples.',
        'total_in_repo': 13,
    })

    return jsonify(results)


@docrepo_bp.route('/api/docrepo/crawler/scan')
@_api_admin_required
def docrepo_crawler_scan():
    """Mode 1: Scan all public sources for available documents (no download)."""
    try:
        from public_doc_crawler import PublicDocCrawler
        crawler = PublicDocCrawler()
        results = crawler.scan_all()
        return jsonify(results)
    except Exception as e:
        logging.error(f"Crawler scan error: {e}")
        return jsonify({'error': 'Crawler scan failed', 'sources': []}), 500


@docrepo_bp.route('/api/docrepo/crawler/crawl', methods=['POST'])
@_api_admin_required
def docrepo_crawler_crawl():
    """Mode 2: Scan and download new documents from all public sources."""
    try:
        from public_doc_crawler import PublicDocCrawler
        max_downloads = request.args.get('max', 25, type=int)
        crawler = PublicDocCrawler()
        results = crawler.crawl_all(max_downloads=min(max_downloads, 50))
        return jsonify(results)
    except Exception as e:
        logging.error(f"Crawler crawl error: {e}")
        return jsonify({'error': 'Crawler download failed', 'sources': []}), 500


@docrepo_bp.route('/api/docrepo/crawler/corpus')
@_api_admin_required
def docrepo_crawler_corpus():
    """Mode 3: Training corpus status report with coverage gaps."""
    try:
        from public_doc_crawler import PublicDocCrawler
        crawler = PublicDocCrawler()
        report = crawler.corpus_report()
        return jsonify(report)
    except Exception as e:
        logging.error(f"Corpus report error: {e}")
        return jsonify({'error': 'Corpus report failed'}), 500


@docrepo_bp.route('/api/docrepo/seed', methods=['POST'])
@_api_admin_required
def docrepo_seed():
    """
    Seed the persistent disk with documents from a tar.gz archive.
    Upload a tar.gz containing the document_repo directory structure.
    One-time operation after first deploy. (v5.62.26)
    
    Usage: curl -X POST -F "archive=@docrepo_files.tar.gz" \
           "https://getofferwise.ai/api/docrepo/seed?admin_key=..."
    """
    import tarfile
    import io

    if 'archive' not in request.files:
        return jsonify({"error": "No archive file provided. Send as 'archive' form field."}), 400

    archive_file = request.files['archive']
    fname = archive_file.filename or ''
    if not (fname.endswith(('.tar.gz', '.tgz', '.gz'))):
        return jsonify({"error": "File must be .tar.gz, .tgz, or .gz"}), 400

    # Ensure disk path exists
    target_dir = DOCREPO_DISK_PATH
    os.makedirs(target_dir, exist_ok=True)

    try:
        archive_bytes = archive_file.read()
        archive_size = len(archive_bytes)

        extracted = []
        skipped = []

        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode='r:gz') as tar:
            for member in tar.getmembers():
                # Skip directories and non-files
                if not member.isfile():
                    continue

                # Strip leading directory (e.g., "document_repo/" or "docrepo/")
                parts = member.name.split('/', 1)
                if len(parts) < 2:
                    rel_path = parts[0]
                else:
                    rel_path = parts[1]

                # Skip metadata (already in repo) and hidden files
                if rel_path.startswith('metadata/') or rel_path.startswith('.'):
                    skipped.append(rel_path)
                    continue

                # Extract to persistent disk
                dest = os.path.join(target_dir, rel_path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)

                f = tar.extractfile(member)
                if f:
                    with open(dest, 'wb') as out:
                        out.write(f.read())
                    extracted.append(rel_path)

        return jsonify({
            "success": True,
            "archive_size_mb": round(archive_size / (1024 * 1024), 1),
            "extracted": len(extracted),
            "skipped": len(skipped),
            "disk_path": target_dir,
            "files": extracted[:20],
            "note": f"Showing first 20 of {len(extracted)}" if len(extracted) > 20 else None,
        })

    except Exception as e:
        logging.error(f"Seed error: {e}")
        return jsonify({"error": "Failed to extract archive"}), 500


@docrepo_bp.route('/api/docrepo/disk-status')
@_api_admin_required
def docrepo_disk_status():
    """Check persistent disk status and document inventory. (v5.62.26)"""
    import json as json_mod

    disk_exists = os.path.isdir(DOCREPO_DISK_PATH)
    result = {
        'disk_path': DOCREPO_DISK_PATH,
        'disk_mounted': disk_exists,
        'disk_files': 0,
        'disk_size_mb': 0,
        'local_files': 0,
        'local_size_mb': 0,
        'catalog_total': 0,
        'on_disk': 0,
        'on_local': 0,
        'missing': 0,
    }

    # Count files on disk
    if disk_exists:
        for root, dirs, files in os.walk(DOCREPO_DISK_PATH):
            for f in files:
                fp = os.path.join(root, f)
                result['disk_files'] += 1
                result['disk_size_mb'] += os.path.getsize(fp)
        result['disk_size_mb'] = round(result['disk_size_mb'] / (1024 * 1024), 1)

    # Count local files
    local_base = os.path.join(os.path.dirname(__file__), 'document_repo')
    for subdir in ['inspection_reports', 'disclosure_statements', 'reference_docs', 'html_reports']:
        dirpath = os.path.join(local_base, subdir)
        if os.path.isdir(dirpath):
            for root, dirs, files in os.walk(dirpath):
                for f in files:
                    fp = os.path.join(root, f)
                    result['local_files'] += 1
                    result['local_size_mb'] += os.path.getsize(fp)
    result['local_size_mb'] = round(result['local_size_mb'] / (1024 * 1024), 1)

    # Check catalog against files
    catalog_path = os.path.join(local_base, 'metadata', 'catalog.json')
    if os.path.exists(catalog_path):
        with open(catalog_path, 'r') as f:
            catalog = json_mod.load(f)
        docs = catalog.get('documents', [])
        result['catalog_total'] = len(docs)
        for doc in docs:
            fpath = _resolve_doc_path(doc)
            if fpath:
                if fpath.startswith(DOCREPO_DISK_PATH):
                    result['on_disk'] += 1
                else:
                    result['on_local'] += 1
            else:
                result['missing'] += 1

    return jsonify(result)
