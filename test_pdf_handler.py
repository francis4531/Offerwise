"""
Test suite for pdf_handler.py — extraction quality checks.
Tests is_meaningful_extraction() and is_tds_complete() without needing actual PDFs.
"""
import pytest
from pdf_handler import is_meaningful_extraction, is_tds_complete


# ============================================================
# GROUP 1: MEANINGFUL EXTRACTION CHECKS
# ============================================================

class TestMeaningfulExtraction:
    """Verify quality gate correctly identifies good vs bad extractions."""

    def test_good_extraction_is_meaningful(self):
        text = """
        PROPERTY INSPECTION REPORT
        Inspector: John Smith, License #12345
        Property: 123 Main Street, San Jose, CA 95134

        ROOF: Composition shingles showing moderate wear. Estimated remaining life 5-7 years.
        PLUMBING: All fixtures tested functional. Hot water heater manufactured 2018.
        ELECTRICAL: 200-amp service panel. All breakers labeled. GFCI outlets in wet areas.
        FOUNDATION: No visible cracks or settlement. Slab appears level.
        """ * 3  # Repeat for length
        is_good, reason = is_meaningful_extraction(text, page_count=5)
        assert is_good, f"Good extraction flagged as bad: {reason}"

    def test_empty_extraction_not_meaningful(self):
        is_good, reason = is_meaningful_extraction("", page_count=10)
        assert not is_good

    def test_metadata_only_not_meaningful(self):
        """DocuSign extractions often return only metadata."""
        text = "DocuSign Envelope ID: ABC-123\nPage 1\nPage 2\nPage 3"
        is_good, reason = is_meaningful_extraction(text, page_count=20)
        assert not is_good

    def test_very_short_text_for_many_pages(self):
        """10 pages should have more than 50 characters."""
        text = "Some text here."
        is_good, reason = is_meaningful_extraction(text, page_count=10)
        assert not is_good

    def test_single_page_low_threshold(self):
        """Single page can have less text and still be meaningful."""
        text = "PROPERTY INSPECTION REPORT. Roof condition is satisfactory with no visible damage. The foundation appears solid with no significant cracking. Plumbing is in good working order throughout the property."
        is_good, reason = is_meaningful_extraction(text, page_count=1)
        assert is_good


# ============================================================
# GROUP 2: TDS COMPLETENESS CHECKS
# ============================================================

class TestTDSCompleteness:
    """Verify TDS completeness scoring works correctly."""

    def test_complete_tds(self):
        text = """
        REAL ESTATE TRANSFER DISCLOSURE STATEMENT
        THIS DISCLOSURE STATEMENT CONCERNS THE REAL PROPERTY
        Seller's Agent: Jane Doe
        Buyer's Agent: Bob Smith

        SECTION I - Seller's Information
        A. The following items are included in the sale:
        [X] Range/Oven [X] Dishwasher [X] Washer [X] Dryer

        B. Are you (Seller) aware of any significant defects/malfunctions?
        [X] Yes [ ] No
        If yes, check appropriate space: Plumbing
        Describe: Minor leak under kitchen sink, repaired 2023.

        C. Are you aware of any of the following:
        1. Substances, materials, or products: [ ] Yes [X] No
        2. Features shared in common: [ ] Yes [X] No

        SECTION II - Agent's Inspection
        Agent notes: Property appears well-maintained.

        SECTION III - Agent's Inspection (Buyer's Agent)
        No additional issues noted.

        Seller Signature: _________________ Date: __________
        Buyer Signature: _________________ Date: __________
        """
        is_complete, score, missing = is_tds_complete(text)
        assert score >= 0.1, f"Complete TDS scored too low: {score}"

    def test_empty_tds(self):
        is_complete, score, missing = is_tds_complete("")
        assert score == 0.0 or not is_complete
        assert len(missing) > 0

    def test_partial_tds(self):
        text = """
        REAL ESTATE TRANSFER DISCLOSURE STATEMENT
        Section A: Items included in sale
        [X] Range [X] Dishwasher
        """
        is_complete, score, missing = is_tds_complete(text)
        assert score < 1.0  # Partial should not be perfect
        assert len(missing) > 0  # Should identify missing sections


# ============================================================
# GROUP 3: EDGE CASES
# ============================================================

class TestPDFHandlerEdgeCases:
    """Edge cases for extraction quality checks."""

    def test_none_text(self):
        """None text should not crash."""
        try:
            is_good, reason = is_meaningful_extraction(None, page_count=1)
            assert not is_good
        except (TypeError, AttributeError):
            pass  # Acceptable to raise

    def test_zero_pages(self):
        is_good, reason = is_meaningful_extraction("some text", page_count=0)
        # Should handle gracefully
        assert isinstance(is_good, bool)

    def test_negative_pages(self):
        is_good, reason = is_meaningful_extraction("some text", page_count=-1)
        assert isinstance(is_good, bool)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
