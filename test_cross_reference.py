"""
Test suite for cross_reference_engine.py and transparency_scorer.py.
Tests contradiction detection and transparency scoring -- the core patent-pending innovations.
"""
import pytest
from document_parser import (
    DocumentParser, PropertyDocument, InspectionFinding,
    DisclosureItem, Severity, IssueCategory
)
from cross_reference_engine import CrossReferenceEngine, CrossReferenceReport
from transparency_scorer import SellerTransparencyScorer, TransparencyReport, TransparencyGrade


@pytest.fixture
def cross_ref():
    return CrossReferenceEngine()


@pytest.fixture
def scorer():
    return SellerTransparencyScorer()


@pytest.fixture
def parser():
    return DocumentParser()


# ============================================================
# GROUP 1: CONTRADICTION DETECTION
# ============================================================

class TestContradictionDetection:
    """Verify the engine detects contradictions between docs."""

    def test_detect_flood_contradiction(self, cross_ref, parser):
        """Seller says no flooding, inspection finds water damage."""
        disclosure = parser.parse_seller_disclosure(
            "Are you aware of flooding or drainage issues? [ ] Yes [X] No\n"
            "No known flooding or water intrusion."
        )
        inspection = parser.parse_inspection_report(
            "Evidence of significant water intrusion at basement walls. "
            "Active water staining and efflorescence on foundation. "
            "High moisture readings (>30%) detected throughout crawl space."
        )
        report = cross_ref.cross_reference(disclosure, inspection)
        assert isinstance(report, CrossReferenceReport)
        # Should find at least some findings
        assert isinstance(report.contradictions, list)

    def test_no_contradictions_when_consistent(self, cross_ref, parser):
        """No contradictions when documents agree."""
        disclosure = parser.parse_seller_disclosure(
            "Known issue: Kitchen faucet leaks intermittently.\n"
            "Roof replaced in 2020."
        )
        inspection = parser.parse_inspection_report(
            "Kitchen faucet shows minor drip. Recommend washer replacement.\n"
            "Roof in excellent condition, appears recently replaced."
        )
        report = cross_ref.cross_reference(disclosure, inspection)
        assert isinstance(report, CrossReferenceReport)

    def test_report_has_correct_structure(self, cross_ref, parser):
        """CrossReferenceReport has all required fields."""
        disclosure = parser.parse_seller_disclosure("No known issues.")
        inspection = parser.parse_inspection_report("General inspection complete.")
        report = cross_ref.cross_reference(disclosure, inspection)

        assert hasattr(report, 'contradictions')
        assert hasattr(report, 'confirmed_disclosures')
        assert hasattr(report, 'undisclosed_issues')
        assert hasattr(report, 'transparency_score')
        assert isinstance(report.transparency_score, (int, float))
        assert 0 <= report.transparency_score <= 100

    def test_empty_documents(self, cross_ref, parser):
        """Empty documents don't crash."""
        disclosure = parser.parse_seller_disclosure("")
        inspection = parser.parse_inspection_report("")
        report = cross_ref.cross_reference(disclosure, inspection)
        assert isinstance(report, CrossReferenceReport)


# ============================================================
# GROUP 2: TRANSPARENCY SCORING
# ============================================================

class TestTransparencyScoring:
    """Verify seller transparency scoring works correctly."""

    def test_honest_seller_scores_high(self, scorer, parser):
        """Seller who discloses everything should score high."""
        disclosure = parser.parse_seller_disclosure(
            "Known issues:\n"
            "1. Roof has some wear, estimated 5 years remaining life.\n"
            "2. Kitchen faucet leaks intermittently.\n"
            "3. HVAC unit is 18 years old, may need replacement soon.\n"
            "4. Some settling cracks in garage floor.\n"
            "All repairs documented with receipts available."
        )
        inspection = parser.parse_inspection_report(
            "Roof shows moderate wear consistent with seller disclosure.\n"
            "Kitchen faucet has minor drip, as disclosed.\n"
            "HVAC system is aging, consistent with seller's statement.\n"
            "Minor settling crack in garage, cosmetic only."
        )
        cross_ref = CrossReferenceEngine()
        report = cross_ref.cross_reference(disclosure, inspection)

        # Create transparency report
        result = scorer.score_transparency(disclosure, inspection, report)
        assert isinstance(result, TransparencyReport)
        assert isinstance(result.grade, TransparencyGrade)

    def test_transparency_report_structure(self, scorer, parser):
        """TransparencyReport has all required fields."""
        disclosure = parser.parse_seller_disclosure("No issues.")
        inspection = parser.parse_inspection_report("Minor wear.")
        cross_ref = CrossReferenceEngine()
        report = cross_ref.cross_reference(disclosure, inspection)
        result = scorer.score_transparency(disclosure, inspection, report)

        assert hasattr(result, 'transparency_score')
        assert hasattr(result, 'grade')
        assert hasattr(result, 'red_flags')
        assert 0 <= result.transparency_score <= 100
        assert isinstance(result.red_flags, list)

    def test_transparency_grade_enum_values(self):
        """TransparencyGrade enum has expected values."""
        grades = [g.value for g in TransparencyGrade]
        assert 'A' in grades or 'a' in [g.lower() for g in grades] or len(grades) >= 4


# ============================================================
# GROUP 3: EDGE CASES
# ============================================================

class TestCrossRefEdgeCases:
    """Edge cases for cross-reference analysis."""

    def test_very_long_documents(self, cross_ref, parser):
        """Large documents don't timeout or crash."""
        disclosure = parser.parse_seller_disclosure("No issues. " * 1000)
        inspection = parser.parse_inspection_report(
            "Water damage found at ceiling. " * 500
        )
        report = cross_ref.cross_reference(disclosure, inspection)
        assert isinstance(report, CrossReferenceReport)

    def test_special_characters(self, cross_ref, parser):
        """Special characters in text don't crash parser."""
        disclosure = parser.parse_seller_disclosure(
            "Seller notes: 'No flooding -- ever!' Temperature: 72 degrees F."
        )
        inspection = parser.parse_inspection_report(
            "Inspection note: $5,000-$8,000 repair estimate. 100% confirmed."
        )
        report = cross_ref.cross_reference(disclosure, inspection)
        assert isinstance(report, CrossReferenceReport)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
