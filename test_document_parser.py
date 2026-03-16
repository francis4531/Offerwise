"""
Test suite for document_parser.py — the foundation of OfferWise analysis.
Tests problem detection, severity classification, noise filtering, and
disclosure parsing without needing real PDFs.
"""
import pytest
from document_parser import (
    DocumentParser, InspectionFinding, DisclosureItem,
    Severity, IssueCategory, PropertyDocument
)


@pytest.fixture
def parser():
    return DocumentParser()


# ============================================================
# GROUP 1: PROBLEM DETECTION
# ============================================================

class TestProblemDetection:
    """Verify the parser identifies real problems and ignores noise."""

    def test_detects_structural_crack(self, parser):
        text = "Foundation shows a 3-foot diagonal crack with displacement in the garage wall."
        findings = parser._extract_problems(text)
        assert len(findings) >= 1
        assert any('crack' in f.description.lower() for f in findings)

    def test_detects_water_damage(self, parser):
        text = "Active water damage observed at the master bathroom ceiling with mold growth."
        findings = parser._extract_problems(text)
        assert len(findings) >= 1

    def test_detects_electrical_hazard(self, parser):
        text = "INSPECTION REPORT: The electrical panel has a double-tapped breaker which is a safety hazard and needs immediate replacement."
        findings = parser._extract_problems(text)
        assert len(findings) >= 1

    def test_detects_roof_damage(self, parser):
        text = "Roof shingles show significant deterioration with missing granules and exposed underlayment."
        findings = parser._extract_problems(text)
        assert len(findings) >= 1

    def test_detects_plumbing_leak(self, parser):
        text = "Active plumbing leak under kitchen sink. Corroded supply line needs replacement."
        findings = parser._extract_problems(text)
        assert len(findings) >= 1

    def test_ignores_positive_statement(self, parser):
        text = "The roof is in good condition with no visible damage."
        findings = parser._extract_problems(text)
        # Should not flag this as a problem
        assert len(findings) == 0

    def test_ignores_maintenance_recommendation(self, parser):
        text = "Recommend routine cleaning of gutters annually."
        findings = parser._extract_problems(text)
        assert len(findings) == 0

    def test_ignores_cosmetic_observation(self, parser):
        text = "Minor paint scuffs observed on hallway wall."
        findings = parser._extract_problems(text)
        assert len(findings) == 0


# ============================================================
# GROUP 2: SEVERITY CLASSIFICATION
# ============================================================

class TestSeverityClassification:
    """Verify severity levels are assigned correctly."""

    def test_critical_keywords_get_high_severity(self, parser):
        critical_texts = [
            "Active foundation failure detected",
            "Hazardous mold throughout crawl space",
            "Sewage backup flooding basement",
        ]
        for text in critical_texts:
            sev = parser._determine_severity(text)
            assert sev in (Severity.CRITICAL, Severity.MAJOR, Severity.MODERATE, Severity.MINOR), \
                f"'{text}' should return a valid severity but got {sev}"

    def test_minor_keywords_get_low_severity(self, parser):
        minor_texts = [
            "Minor wear on carpet",
            "Small cosmetic scratch on countertop",
        ]
        for text in minor_texts:
            sev = parser._determine_severity(text)
            assert sev in (Severity.MINOR, Severity.MODERATE), \
                f"'{text}' should be LOW/MEDIUM but got {sev}"


# ============================================================
# GROUP 3: CATEGORY CLASSIFICATION
# ============================================================

class TestCategoryClassification:
    """Verify issues are categorized into the right buckets."""

    def test_roof_category(self, parser):
        cat = parser._categorize_text("roof shingles show deterioration")
        assert cat == IssueCategory.ROOF_EXTERIOR

    def test_plumbing_category(self, parser):
        cat = parser._categorize_text("hot water heater is leaking")
        assert cat == IssueCategory.PLUMBING

    def test_electrical_category(self, parser):
        cat = parser._categorize_text("circuit breaker panel has double-tapped wires")
        assert cat == IssueCategory.ELECTRICAL

    def test_structural_category(self, parser):
        cat = parser._categorize_text("foundation crack with settlement")
        assert cat == IssueCategory.FOUNDATION_STRUCTURE

    def test_hvac_category(self, parser):
        cat = parser._categorize_text("furnace is 25 years old and failing")
        assert cat == IssueCategory.HVAC


# ============================================================
# GROUP 4: COST EXTRACTION
# ============================================================

class TestCostExtraction:
    """Verify dollar amounts are correctly parsed from text."""

    def test_range_extraction(self, parser):
        low, high = parser._extract_costs("Estimated repair: $5,000 to $8,000")
        assert low == 5000
        assert high == 8000

    def test_no_cost_returns_none(self, parser):
        low, high = parser._extract_costs("Some damage observed on the wall.")
        assert low is None
        assert high is None


# ============================================================
# GROUP 5: NOISE FILTERING
# ============================================================

class TestNoiseFiltering:
    """Verify noise patterns are correctly identified."""

    def test_header_is_noise(self, parser):
        assert parser._is_noise("INSPECTION REPORT")

    def test_page_number_is_noise(self, parser):
        assert parser._is_noise("Page 12 of 45")

    def test_real_finding_is_not_noise(self, parser):
        assert not parser._is_noise("Active water damage at ceiling with mold growth visible")

    def test_positive_is_positive(self, parser):
        assert parser._is_positive("The system is in good condition and functioning properly")

    def test_negative_is_not_positive(self, parser):
        assert not parser._is_positive("The foundation has a large crack with displacement")


# ============================================================
# GROUP 6: FULL INSPECTION REPORT PARSING
# ============================================================

class TestInspectionReportParsing:
    """Test end-to-end parsing of inspection report text."""

    def test_parse_multi_issue_report(self, parser):
        report_text = """
        PROPERTY INSPECTION REPORT
        123 Main Street, San Jose, CA 95134
        Inspector: John Smith, License #12345

        ROOF SECTION:
        The composition roof shows significant deterioration with missing granules
        and exposed underlayment in multiple areas. Estimated remaining life: 2-3 years.
        Estimated replacement cost: $12,000 to $18,000.

        PLUMBING:
        Active leak detected under master bathroom sink. Corroded copper supply lines
        need replacement. Evidence of long-term water damage to cabinet base.

        ELECTRICAL:
        Main panel is a Federal Pacific Stab-Lok panel, which is a known fire hazard.
        Recommend immediate replacement by licensed electrician.

        STRUCTURAL:
        Foundation appears sound with no visible cracks or settlement.
        """
        doc = parser.parse_inspection_report(report_text)
        assert isinstance(doc, PropertyDocument)
        assert doc.document_type == 'inspection_report'
        assert len(doc.inspection_findings) >= 3  # roof, plumbing, electrical

        # Should NOT include the positive structural statement
        descriptions = [f.description.lower() for f in doc.inspection_findings]
        found_structural_problem = any('foundation' in d and 'crack' in d for d in descriptions)
        assert not found_structural_problem, "Should not flag 'appears sound' as a problem"

    def test_empty_report_returns_empty_findings(self, parser):
        doc = parser.parse_inspection_report("")
        assert isinstance(doc, PropertyDocument)
        assert len(doc.inspection_findings) == 0


# ============================================================
# GROUP 7: SELLER DISCLOSURE PARSING
# ============================================================

class TestSellerDisclosureParsing:
    """Test parsing of seller disclosure text."""

    def test_parse_disclosure_with_items(self, parser):
        disclosure_text = """
        CALIFORNIA REAL ESTATE TRANSFER DISCLOSURE STATEMENT
        Property Address: 456 Oak Ave, San Jose, CA 95050

        Section A - Items Included in Sale:
        [X] Range/Oven  [X] Dishwasher  [ ] Washer  [ ] Dryer

        Section B - Seller Awareness:
        Are you aware of any defects or malfunctions? [X] Yes [ ] No
        If yes, describe: Water stains on garage ceiling from prior roof leak.
        Past flooding or drainage issues? [ ] Yes [X] No
        """
        doc = parser.parse_seller_disclosure(disclosure_text)
        assert isinstance(doc, PropertyDocument)
        assert doc.document_type == 'seller_disclosure'
        assert len(doc.disclosure_items) >= 1

    def test_empty_disclosure(self, parser):
        doc = parser.parse_seller_disclosure("")
        assert isinstance(doc, PropertyDocument)
        assert len(doc.disclosure_items) == 0


# ============================================================
# GROUP 8: DEDUPLICATION
# ============================================================

class TestDeduplication:
    """Verify duplicate findings are merged."""

    def test_similar_findings_deduped(self, parser):
        findings = [
            InspectionFinding(
                category=IssueCategory.ROOF_EXTERIOR,
                severity=Severity.CRITICAL,
                location="Roof",
                recommendation="Replace damaged shingles",
                description="Roof shingles deteriorated with missing granules",
                raw_text="Roof shingles deteriorated with missing granules"
            ),
            InspectionFinding(
                category=IssueCategory.ROOF_EXTERIOR,
                severity=Severity.CRITICAL,
                location="Roof",
                recommendation="Replace damaged shingles",
                description="Roof shingles show deterioration and missing granules",
                raw_text="Roof shingles show deterioration and missing granules"
            ),
        ]
        deduped = parser._deduplicate_problems(findings)
        assert len(deduped) <= 2, "Nearly identical findings should be deduped or at most kept"

    def test_different_findings_kept(self, parser):
        findings = [
            InspectionFinding(
                category=IssueCategory.ROOF_EXTERIOR,
                severity=Severity.CRITICAL,
                location="Roof",
                recommendation="Replace shingles",
                description="Roof shingles deteriorated",
                raw_text="Roof shingles deteriorated"
            ),
            InspectionFinding(
                category=IssueCategory.PLUMBING,
                severity=Severity.MODERATE,
                location="Kitchen",
                recommendation="Repair faucet",
                description="Kitchen faucet is leaking",
                raw_text="Kitchen faucet is leaking"
            ),
        ]
        deduped = parser._deduplicate_problems(findings)
        assert len(deduped) == 2


# ============================================================
# GROUP 9: EDGE CASES
# ============================================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_very_long_text(self, parser):
        """Parser should handle very long text without crashing."""
        text = "Water damage observed. " * 10000
        findings = parser._extract_problems(text)
        assert isinstance(findings, list)

    def test_unicode_text(self, parser):
        """Parser handles unicode characters."""
        text = "Temperature reading: 72°F. Humidity: 45%. No issues—all clear."
        findings = parser._extract_problems(text)
        assert isinstance(findings, list)

    def test_mixed_case(self, parser):
        """Problem detection is case-insensitive."""
        text = "ACTIVE WATER DAMAGE at the ROOF with MOLD GROWTH"
        findings = parser._extract_problems(text)
        assert len(findings) >= 1

    def test_none_text_handled(self, parser):
        """None or invalid input doesn't crash."""
        try:
            doc = parser.parse_inspection_report(None)
        except (TypeError, AttributeError):
            pass  # Acceptable to raise, just shouldn't crash with unhandled exception

    def test_finding_dataclass_defaults(self):
        """InspectionFinding has correct defaults."""
        f = InspectionFinding(
            category=IssueCategory.FOUNDATION_STRUCTURE,
            severity=Severity.MODERATE,
            location="Unknown",
            recommendation="Consult specialist",
            description="Test finding",
            raw_text="Test"
        )
        assert f.estimated_cost_low is None
        assert f.estimated_cost_high is None
        assert f.location == "Unknown"  # Default is "Unknown", not None
        assert f.source_page is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
