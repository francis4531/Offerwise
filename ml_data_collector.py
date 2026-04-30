"""
OfferWise ML Data Collector
============================
Auto-collects training data from every analysis.
Called at the end of the analysis pipeline — zero user friction.

Collects:
1. Finding labels (text → category + severity)
2. Contradiction pairs (seller claim + inspector finding → label)
3. Co-occurrence baskets (which findings appear together)

All writes are fire-and-forget — failures never block the analysis.
"""
import json
import logging
import re

logger = logging.getLogger(__name__)


def collect_training_data(analysis_id, result_dict, property_address='', property_price=0):
    """
    Extract and store ML training data from a completed analysis.
    Each collector runs independently — a failure in one doesn't affect the others.
    """
    if not result_dict or not isinstance(result_dict, dict):
        return

    for collector, name in [
        (lambda: _collect_finding_labels(analysis_id, result_dict, property_address, property_price), 'findings'),
        (lambda: _collect_contradiction_pairs(analysis_id, result_dict), 'contradictions'),
        (lambda: _collect_cooccurrence_bucket(analysis_id, result_dict, property_address), 'cooccurrence'),
        (lambda: _archive_to_docrepo(analysis_id, result_dict, property_address, property_price), 'docrepo_archive'),
    ]:
        try:
            collector()
        except Exception as e:
            try:
                from extensions import db
                db.session.rollback()
            except Exception:
                pass
            logger.warning(f"ML {name} collection error (non-fatal): {e}")


def _extract_zip(address):
    """Extract 5-digit ZIP from address string."""
    m = re.search(r'\b(\d{5})\b', address or '')
    return m.group(1) if m else ''


def _collect_finding_labels(analysis_id, result_dict, property_address, property_price):
    """Extract labeled findings → ml_finding_labels table."""
    from extensions import db
    from models import MLFindingLabel

    # Findings can be at multiple paths depending on analysis type
    findings = result_dict.get('findings', [])
    if not findings:
        # Full analysis: nested under inspection_report.inspection_findings
        insp = result_dict.get('inspection_report', {})
        if isinstance(insp, dict):
            findings = insp.get('inspection_findings', [])
    if not findings:
        return

    zip_code = _extract_zip(property_address)
    count = 0

    for f in findings:
        if not isinstance(f, dict):
            continue
        text = (f.get('description') or f.get('text') or f.get('issue') or '').strip()
        category = (f.get('category') or f.get('system') or '').strip()
        severity = (f.get('severity') or '').strip()

        if len(text) < 10 or not category or not severity:
            continue
        if severity.lower() in ('none', 'informational', ''):
            continue

        label = MLFindingLabel(
            finding_text=text[:2000],
            category=category,
            severity=severity,
            source='ai_parse',
            confidence=0.85,
            is_validated=False,
            analysis_id=analysis_id,
            property_zip=zip_code,
            property_price=property_price or None,
        )
        db.session.add(label)
        count += 1

    if count > 0:
        db.session.commit()
        logger.info(f"ML: collected {count} finding labels from analysis {analysis_id}")


def _collect_contradiction_pairs(analysis_id, result_dict):
    """Extract cross-reference pairs → ml_contradiction_pairs table."""
    from extensions import db
    from models import MLContradictionPair

    xref = result_dict.get('cross_reference', {})
    if not xref or not isinstance(xref, dict):
        return

    count = 0

    def _parse_nested(val):
        """Parse a value that might be a dict, a string repr of a dict, or None."""
        if isinstance(val, dict):
            return val
        if isinstance(val, str) and val.startswith('{'):
            try:
                import ast
                return ast.literal_eval(val)
            except Exception:
                pass
        return {}

    def _extract_seller_text(item):
        """Extract seller claim text from a CrossReferenceMatch dict."""
        di = _parse_nested(item.get('disclosure_item', {}))
        text = (di.get('question') or di.get('raw_text') or di.get('details') or '').strip()
        if not text:
            # Fallback to the explanation field
            text = (item.get('explanation') or '').strip()
        return text

    def _extract_finding_text(item):
        """Extract inspector finding text from a CrossReferenceMatch dict."""
        fi = _parse_nested(item.get('inspection_finding', {}))
        text = (fi.get('description') or fi.get('raw_text') or '').strip()
        if not text:
            text = (item.get('description') or item.get('explanation') or '').strip()
        return text

    # Contradictions
    for c in xref.get('contradictions', []):
        if not isinstance(c, dict):
            continue
        seller = _extract_seller_text(c)
        finding = _extract_finding_text(c)
        if finding and len(finding) > 5:
            db.session.add(MLContradictionPair(
                seller_claim=(seller or c.get('explanation', ''))[:2000],
                inspector_finding=finding[:2000],
                label='contradiction',
                confidence=c.get('confidence', 0.8),
                analysis_id=analysis_id,
                source='cross_ref_engine',
            ))
            count += 1

    # Confirmed disclosures (consistent)
    for c in xref.get('confirmed_disclosures', xref.get('confirmed', [])):
        if not isinstance(c, dict):
            continue
        seller = _extract_seller_text(c)
        finding = _extract_finding_text(c)
        if seller and finding and len(finding) > 5:
            db.session.add(MLContradictionPair(
                seller_claim=seller[:2000],
                inspector_finding=finding[:2000],
                label='consistent',
                confidence=c.get('confidence', 0.8),
                analysis_id=analysis_id,
                source='cross_ref_engine',
            ))
            count += 1

    # Undisclosed issues (omissions)
    for c in xref.get('undisclosed_issues', xref.get('omissions', [])):
        if not isinstance(c, dict):
            continue
        finding = _extract_finding_text(c)
        if finding and len(finding) > 5:
            db.session.add(MLContradictionPair(
                seller_claim='',
                inspector_finding=finding[:2000],
                label='omission',
                confidence=c.get('confidence', 0.7),
                analysis_id=analysis_id,
                source='cross_ref_engine',
            ))
            count += 1

    if count > 0:
        db.session.commit()
        logger.info(f"ML: collected {count} contradiction pairs from analysis {analysis_id}")


def _collect_cooccurrence_bucket(analysis_id, result_dict, property_address):
    """Extract finding co-occurrence set → ml_cooccurrence_buckets table."""
    from extensions import db
    from models import MLCooccurrenceBucket

    # Try multiple paths for findings
    findings = result_dict.get('findings', [])
    if not findings:
        insp = result_dict.get('inspection_report', {})
        if isinstance(insp, dict):
            findings = insp.get('inspection_findings', [])
    if not findings or len(findings) < 2:
        return

    items = set()
    for f in findings:
        if not isinstance(f, dict):
            continue
        # Category might be enum value ("plumbing"), enum name ("PLUMBING"), or display ("Plumbing")
        cat = (f.get('category') or f.get('system') or '')
        if isinstance(cat, dict):
            cat = cat.get('value', cat.get('name', ''))
        cat = str(cat).lower().strip()
        sev = (f.get('severity') or '')
        if isinstance(sev, dict):
            sev = sev.get('value', sev.get('name', ''))
        sev = str(sev).lower().strip()
        if cat and sev and sev not in ('informational', 'none', ''):
            items.add(f"{cat}:{sev}")

    if len(items) < 2:
        return

    # Check for duplicate
    existing = MLCooccurrenceBucket.query.filter_by(analysis_id=analysis_id).first()
    if existing:
        return

    bucket = MLCooccurrenceBucket(
        analysis_id=analysis_id,
        findings_set=json.dumps(sorted(items)),
        n_findings=len(items),
        property_zip=_extract_zip(property_address),
    )
    db.session.add(bucket)
    db.session.commit()
    logger.info(f"ML: collected co-occurrence bucket ({len(items)} findings) from analysis {analysis_id}")


def _archive_to_docrepo(analysis_id, result_dict, property_address, property_price):
    """Archive anonymized analysis output to the document repository.

    Saves a JSON file containing the structured analysis results (findings,
    contradictions, cost estimates, risk scores) to the persistent disk.
    Raw document text is NOT saved (privacy). Only the AI-extracted structured
    output is archived.

    Each analysis becomes a training example that can be re-mined later when
    extraction logic improves.
    """
    import os
    import hashlib
    from datetime import datetime

    docrepo_root = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
    archive_dir = os.path.join(docrepo_root, 'analyses')
    os.makedirs(archive_dir, exist_ok=True)

    # Anonymize: strip exact address but keep ZIP and city for regional patterns
    zip_code = _extract_zip(property_address)
    city = ''
    if ',' in property_address:
        parts = property_address.split(',')
        if len(parts) >= 2:
            city = parts[-2].strip()  # Usually city

    # Build the archive record — structured data only, no raw text
    archive = {
        'analysis_id': analysis_id,
        'archived_at': datetime.utcnow().isoformat(),
        'location': {'zip': zip_code, 'city': city, 'state': ''},
        'price': property_price,
        'analysis_depth': result_dict.get('analysis_depth', ''),
    }

    # Extract structured outputs for training
    # Findings with categories and severities
    findings = []
    for section_name, section in (result_dict.get('findings', {}) or {}).items():
        if isinstance(section, dict):
            for item in (section.get('items', []) or []):
                if isinstance(item, dict):
                    findings.append({
                        'text': (item.get('text') or '')[:300],
                        'category': section_name,
                        'severity': item.get('severity', ''),
                        'cost_low': item.get('cost_low'),
                        'cost_high': item.get('cost_high'),
                    })
    archive['findings'] = findings
    archive['finding_count'] = len(findings)

    # Contradictions
    xref = result_dict.get('cross_reference', {}) or {}
    contradictions = []
    for c in (xref.get('contradictions', []) or []):
        if isinstance(c, dict):
            contradictions.append({
                'seller_claim': (c.get('seller_claim') or '')[:200],
                'inspector_finding': (c.get('inspector_finding') or '')[:200],
                'severity': c.get('severity', ''),
            })
    archive['contradictions'] = contradictions

    # Cost summary
    repair = result_dict.get('repair_estimate', {}) or {}
    archive['repair_estimate'] = {
        'total_low': repair.get('total_low'),
        'total_high': repair.get('total_high'),
        'breakdown_count': len(repair.get('breakdown', []) or []),
    }

    # Risk DNA
    risk_dna = result_dict.get('risk_dna', {}) or {}
    if isinstance(risk_dna, dict):
        archive['risk_dna'] = {
            'composite_score': risk_dna.get('composite_score'),
            'structural': risk_dna.get('structural'),
            'systems': risk_dna.get('systems'),
            'environmental': risk_dna.get('environmental'),
            'financial': risk_dna.get('financial'),
        }

    # Offer strategy summary
    offer = result_dict.get('offer_strategy', {}) or {}
    if isinstance(offer, dict):
        archive['offer_strategy'] = {
            'recommended_offer': offer.get('recommended_offer'),
            'discount_pct': offer.get('discount_pct'),
        }

    # Write to disk
    file_id = f'{analysis_id}_{hashlib.md5(str(analysis_id).encode()).hexdigest()[:8]}'
    filepath = os.path.join(archive_dir, f'{file_id}.json')

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(archive, f, indent=2)

    logger.info(f"ML: archived analysis {analysis_id} to docrepo ({len(findings)} findings, {len(contradictions)} contradictions)")
