"""test_foundation_fabrication.py — v5.89.333

Regression lock for the 13180 Edgemont, Frisco TX fabrication bug.

A partner (Raees) ran a real Frisco TX deal through OfferWise. Both the seller's
disclosure and the SmartTeam inspection state the foundation is SOUND — the inspection
says "No indications of significant foundation movement were observed" and the disclosure
marks Foundation/Slab defects as "No". Yet the report invented a
FOUNDATION & STRUCTURE · CRITICAL · $25,000-$60,000 finding — its single largest line
item and the anchor of the whole (wrong) offer.

Root cause (see document_parser.py): the rules-based sentence extractor's negation and
noise guards had holes. Clean structural statements using "movement" (not in the negation
vocabulary) and legal/microbial disclaimers containing "structural"/"critical" slipped
through, became findings, were keyword-bucketed as FOUNDATION, inherited CRITICAL, and
were priced at $25-60k. The merge step then ADDED this to the (correct) AI output as
"something AI missed".

These tests lock BOTH halves of the fix:
  1. The parser's guards now filter those exact sentences (unit-level, deterministic).
  2. Real problems from the same report are still kept (no over-suppression).
"""

import importlib.util
import os
import sys
import unittest


def _load_parser():
    for k in ('document_parser',):
        if k in sys.modules:
            del sys.modules[k]
    spec = importlib.util.spec_from_file_location('document_parser', 'document_parser.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['document_parser'] = mod
    spec.loader.exec_module(mod)
    return mod


class TestFoundationFabrication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_parser()
        cls.parser = cls.mod.DocumentParser()

    # ── the exact sentences that were fabricated into a critical foundation finding ──
    CLEAN_FOUNDATION = (
        "No indications of significant foundation movement were observed inside the "
        "home or on the exterior walls.")
    NO_REINFORCEMENT_DAMAGE = (
        "No exposed or damaged reinforcement or concrete damage was observed on the "
        "perimeter of the slab.")
    LIABILITY_DISCLAIMER = (
        "Therefore the Inspector's liability is specifically limited to those situations "
        "where it can be conclusively shown that at the time of inspection the structural "
        "component inspected was inoperable.")
    MICROBIAL_DISCLAIMER = (
        "Proper remediation is absolutely critical for deterring microbial growth if its "
        "existence is proven.")

    # ── real problems from the SAME inspection that MUST still be extracted ──
    # These use vocabulary the rules extractor genuinely recognizes (damage/leak/loose).
    # Note: some real findings (e.g. "openings must be covered", "did not function") are
    # only caught by the AI extractor, not the rules path — which is precisely why AI is
    # now authoritative. We assert here on the ones the rules path CAN see, to prove the
    # fix didn't over-suppress the guard vocabulary.
    REAL_SPRINKLER_LEAK = (
        "In zone 6 of the sprinkler system there is a leak in the underground drip "
        "tubing adjacent to the right front corner of the house.")
    REAL_LOOSE_ESCUTCHEON = (
        "The escutcheon at the water heater gas vent pipe is loose and not properly "
        "covering the opening in the brick wall, allowing rainwater to enter.")
    REAL_MISSING_INSULATION = (
        "The heat on the walls and ceilings indicates a lack of insulation on the attic "
        "side of the drywall that should have been installed during construction.")

    def _is_finding(self, sentence):
        """True if the parser would treat this sentence as an actual problem/finding."""
        if self.parser._is_noise(sentence):
            return False
        if self.parser._is_positive(sentence):
            return False
        return self.parser._indicates_problem(sentence)

    # ---- fabrications must NOT become findings ----
    def test_clean_foundation_statement_is_not_a_finding(self):
        self.assertFalse(self._is_finding(self.CLEAN_FOUNDATION),
                         "a clean 'no foundation movement' statement must not become a finding")

    def test_no_reinforcement_damage_is_not_a_finding(self):
        self.assertFalse(self._is_finding(self.NO_REINFORCEMENT_DAMAGE))

    def test_liability_disclaimer_is_not_a_finding(self):
        self.assertFalse(self._is_finding(self.LIABILITY_DISCLAIMER),
                         "contract liability boilerplate must not become a finding")

    def test_microbial_disclaimer_is_not_a_finding(self):
        self.assertFalse(self._is_finding(self.MICROBIAL_DISCLAIMER),
                         "microbial/IAQ disclaimer must not become a finding")

    # ---- real problems MUST still be findings (no over-suppression) ----
    def test_sprinkler_leak_is_still_a_finding(self):
        self.assertTrue(self._is_finding(self.REAL_SPRINKLER_LEAK),
                        "a real sprinkler leak must still be extracted")

    def test_loose_escutcheon_is_still_a_finding(self):
        self.assertTrue(self._is_finding(self.REAL_LOOSE_ESCUTCHEON),
                        "a real loose/leaking escutcheon must still be extracted")

    # ---- the merge no longer injects rules findings over AI output ----
    def test_ai_success_means_rules_findings_are_not_merged(self):
        """When AI extraction succeeds it is authoritative; the fabrication-prone rules
        additions must not be merged in. We assert the merge method is no longer called
        on the success path by checking the source logic flag."""
        import inspect
        src = inspect.getsource(self.mod.DocumentParser.parse_inspection_report)
        self.assertIn('AI authoritative', src,
                      "success path must use AI findings directly, not _merge_ai_and_rules")
        self.assertNotIn('_merge_ai_and_rules(ai_findings, rules_findings)', src,
                         "the fabrication-prone merge must not run on the AI-success path")


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestProvenanceGate(unittest.TestCase):
    """v5.89.335: the path-independent guard. Any finding whose source text is not in the
    document is dropped, regardless of which code path produced it. This is the durable
    fix after three surgical fixes to specific paths each failed to stop the fabricated
    foundation finding on 13180 Edgemont."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_parser()
        cls.p = cls.mod.DocumentParser.__new__(cls.mod.DocumentParser)
        cls.F = cls.mod.InspectionFinding
        cls.DOC = (
            "A. FOUNDATIONS. This inspector is not a structural engineer. Foundation "
            "Performance: No indications of significant foundation movement were observed "
            "inside the home or on the exterior walls. "
            "The exhaust fan in the laundry room did not function at the time of the "
            "inspection. In zone 6 of the sprinkler system there is a leak in the "
            "underground drip tubing adjacent to the right front corner of the house.")

    def _f(self, category, quote):
        return self.F(category=category, severity='moderate', location='',
                      description=quote, recommendation='', raw_text=quote, source_quote=quote)

    def test_fabricated_foundation_finding_is_dropped(self):
        fab = self._f('foundation_structure',
                      'references potential foundation or structural concerns that require further evaluation')
        kept = self.p._gate_findings_by_provenance([fab], self.DOC)
        self.assertEqual(len(kept), 0,
                         "a foundation finding with no supporting document sentence must be dropped")

    def test_fabricated_mold_finding_is_dropped(self):
        fab = self._f('environmental',
                      'Water stains found in the inspection indicate moisture intrusion behind the walls')
        kept = self.p._gate_findings_by_provenance([fab], self.DOC)
        self.assertEqual(len(kept), 0,
                         "a mold finding quoting text not in the document must be dropped")

    def test_real_findings_survive_the_gate(self):
        real = [
            self._f('hvac_systems',
                    'The exhaust fan in the laundry room did not function at the time of the inspection'),
            self._f('plumbing',
                    'there is a leak in the underground drip tubing adjacent to the right front corner of the house'),
        ]
        kept = self.p._gate_findings_by_provenance(real, self.DOC)
        self.assertEqual(len(kept), 2, "real findings whose quotes are in the document must survive")

    def test_mixed_batch_keeps_only_verifiable(self):
        batch = [
            self._f('foundation_structure', 'references potential foundation or structural concerns'),
            self._f('hvac_systems',
                    'The exhaust fan in the laundry room did not function at the time of the inspection'),
        ]
        kept = self.p._gate_findings_by_provenance(batch, self.DOC)
        cats = {k.category for k in kept}
        self.assertNotIn('foundation_structure', cats)
        self.assertIn('hvac_systems', cats)

    def test_empty_document_does_not_silently_drop(self):
        # if we somehow have no document text, don't nuke all findings
        f = self._f('plumbing', 'some finding text here that is long enough')
        kept = self.p._gate_findings_by_provenance([f], '')
        self.assertEqual(len(kept), 1, "with no document text, do not silently drop findings")
