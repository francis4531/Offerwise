# OfferWise Test Corpus Manifest
# Generated: 2026-06-25 00:03:01
# Total files: 16

## Clean Digital Documents (text extraction should work perfectly)
- 01_digital_tds_clean.pdf          — 4-page DocuSign-style TDS, realistic CA disclosure
- 02_digital_inspection_clean.pdf    — 15-page professional inspection report, multi-section
- 03_digital_tds_nightmare_no_disclosure.pdf — TDS where seller says "No" to everything
- 04_digital_inspection_nightmare.pdf — Inspection finding $75K-$154K in hidden problems

## Scanned/Image-Based Documents (OCR required)
- 05_scanned_handwritten_tds.pdf     — 3-page handwritten TDS on scanned paper
- 06_faded_photocopy.pdf             — Very low contrast, barely readable photocopy
- 07_crooked_scan.pdf                — Inspection report scanned at ~4 degree angle
- 08_phone_photo.pdf                 — Phone camera photo with shadows and blur
- 09_mixed_digital_scanned.pdf       — Digital cover page + scanned disclosure pages

## Inspection with Photos
- 15_inspection_with_photos.pdf      — Image-based report with embedded damage photos

## Edge Cases
- 16_redacted_disclosure.pdf         — Disclosure with blacked-out sections

## Adversarial / Invalid
- 10_blank_3pages.pdf                — Completely blank 3-page PDF
- 11_metadata_only_docusign.pdf      — DocuSign envelope metadata, no content
- 12_wrong_doc_mortgage.pdf          — Mortgage statement (wrong document type)
- 13_corrupted.pdf                   — Valid PDF header, corrupted content bytes
- 14_not_a_pdf.pdf                   — Plain text file with .pdf extension

## Test Scenarios
- Pair 01 + 02: Clean scenario (moderate issues, honest disclosure)
- Pair 03 + 04: Nightmare scenario (seller hiding $75K-$154K in problems)
- 05-09: OCR pipeline testing (various scan quality levels)
- 10-14: Adversarial input testing (graceful failure required)
- 15-16: Edge cases (photos in reports, redacted sections)
