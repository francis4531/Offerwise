"""
AI Regression Corpus v1.0
==========================
Saves known-good (input → AI output) pairs for regression testing.
Detects drift when AI behavior changes across deploys.

Usage:
    # Save a baseline
    corpus = AIRegressionCorpus()
    corpus.save_baseline('truth-check', input_data, ai_output, metadata)

    # Run regression tests
    results = corpus.run_regression_tests()
"""

import json
import hashlib
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)

CORPUS_DIR = Path('regression_corpus')


class AIRegressionCorpus:
    """Manage a corpus of known-good AI outputs for regression testing."""

    def __init__(self, corpus_dir: str = None):
        self.dir = Path(corpus_dir) if corpus_dir else CORPUS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def save_baseline(self, endpoint: str, input_data: Dict,
                      ai_output: Dict, metadata: Dict = None) -> str:
        """Save a known-good AI output as a baseline.

        Args:
            endpoint: 'truth-check', 'cross-reference', 'external-verification'
            input_data: Serializable input (text excerpts, not full PDFs)
            ai_output: The AI response that was verified as correct
            metadata: Optional context (model, version, timestamp)

        Returns:
            baseline_id (hash of input)
        """
        input_hash = hashlib.sha256(
            json.dumps(input_data, sort_keys=True).encode()
        ).hexdigest()[:12]

        baseline = {
            'id': input_hash,
            'endpoint': endpoint,
            'created_at': datetime.utcnow().isoformat(),
            'model': (metadata or {}).get('model', 'unknown'),
            'version': (metadata or {}).get('version', 'unknown'),
            'input_summary': _truncate_input(input_data),
            'expected_output': ai_output,
            'validation_rules': _derive_rules(endpoint, ai_output),
        }

        filepath = self.dir / f"{endpoint}_{input_hash}.json"
        with open(filepath, 'w') as f:
            json.dump(baseline, f, indent=2, default=str)

        logger.info(f"📸 Saved baseline {input_hash} for {endpoint}")
        return input_hash

    def list_baselines(self) -> List[Dict]:
        """List all saved baselines."""
        baselines = []
        for f in sorted(self.dir.glob('*.json')):
            with open(f) as fh:
                data = json.load(fh)
                baselines.append({
                    'id': data['id'],
                    'endpoint': data['endpoint'],
                    'created_at': data['created_at'],
                    'file': f.name,
                })
        return baselines

    def run_regression_tests(self, current_outputs: Dict[str, Dict] = None) -> List[Dict]:
        """Compare current AI outputs against saved baselines.

        Args:
            current_outputs: {baseline_id: current_ai_output}
                             If None, returns rules for manual verification.

        Returns:
            List of {baseline_id, endpoint, status, deviations}
        """
        results = []
        for f in sorted(self.dir.glob('*.json')):
            with open(f) as fh:
                baseline = json.load(fh)

            bid = baseline['id']
            endpoint = baseline['endpoint']
            rules = baseline.get('validation_rules', {})

            if current_outputs and bid in current_outputs:
                current = current_outputs[bid]
                deviations = _check_rules(rules, current)
                results.append({
                    'baseline_id': bid,
                    'endpoint': endpoint,
                    'status': 'PASS' if not deviations else 'DRIFT',
                    'deviations': deviations,
                })
            else:
                results.append({
                    'baseline_id': bid,
                    'endpoint': endpoint,
                    'status': 'SKIPPED',
                    'rules': rules,
                })

        return results

    def get_baseline(self, baseline_id: str) -> Optional[Dict]:
        """Retrieve a specific baseline by ID."""
        for f in self.dir.glob(f'*_{baseline_id}.json'):
            with open(f) as fh:
                return json.load(fh)
        return None


def _truncate_input(input_data: Dict, max_chars: int = 500) -> Dict:
    """Truncate input data for storage (don't save full PDFs)."""
    result = {}
    for k, v in input_data.items():
        if isinstance(v, str) and len(v) > max_chars:
            result[k] = v[:max_chars] + f'... [{len(v)} chars total]'
        else:
            result[k] = v
    return result


def _derive_rules(endpoint: str, output: Dict) -> Dict:
    """Derive validation rules from a known-good output.

    These rules define what 'correct' looks like — not exact match,
    but structural and range-based checks.
    """
    rules = {}

    if endpoint == 'truth-check':
        rules['trust_score_range'] = [
            max(0, output.get('trust_score', 50) - 15),
            min(100, output.get('trust_score', 50) + 15)
        ]
        rules['grade'] = output.get('grade')
        rules['red_flag_count_range'] = [
            max(0, len(output.get('red_flags', [])) - 2),
            len(output.get('red_flags', [])) + 2
        ]
        # Key red flag topics should persist across runs
        rules['expected_topics'] = [
            rf.get('title', '')[:50] for rf in output.get('red_flags', [])[:5]
        ]

    elif endpoint == 'cross-reference':
        findings = output.get('findings', [])
        rules['finding_count_range'] = [
            max(0, len(findings) - 2),
            len(findings) + 2
        ]
        rules['has_severity_ratings'] = any(
            f.get('severity') for f in findings
        )

    elif endpoint == 'external-verification':
        findings = output.get('findings', [])
        rules['finding_count_range'] = [
            max(0, len(findings) - 1),
            len(findings) + 1
        ]

    return rules


def _check_rules(rules: Dict, current: Dict) -> List[str]:
    """Check current output against validation rules."""
    deviations = []

    if 'trust_score_range' in rules:
        score = current.get('trust_score', -1)
        lo, hi = rules['trust_score_range']
        if not (lo <= score <= hi):
            deviations.append(
                f"trust_score {score} outside expected range [{lo}, {hi}]"
            )

    if 'grade' in rules:
        if current.get('grade') != rules['grade']:
            deviations.append(
                f"grade changed: expected {rules['grade']}, got {current.get('grade')}"
            )

    if 'red_flag_count_range' in rules:
        count = len(current.get('red_flags', []))
        lo, hi = rules['red_flag_count_range']
        if not (lo <= count <= hi):
            deviations.append(
                f"red_flag count {count} outside expected range [{lo}, {hi}]"
            )

    if 'finding_count_range' in rules:
        count = len(current.get('findings', []))
        lo, hi = rules['finding_count_range']
        if not (lo <= count <= hi):
            deviations.append(
                f"finding count {count} outside expected range [{lo}, {hi}]"
            )

    return deviations
