#!/usr/bin/env python3
"""
Test the improved PDF parser with Pendleton_Disclosures.pdf
"""

from pdf_handler import PDFHandler
import sys

def test_pdf_parser():
    """Test PDF parsing with the complex disclosure document"""
    
    pdf_path = '/mnt/user-data/uploads/Pendleton_Disclosures.pdf'
    
    print("="*80)
    print("Testing Improved PDF Parser")
    print("="*80)
    print(f"\nTest file: {pdf_path}")
    print("This is a complex 44-page disclosure form with:")
    print("  - Mixed printed and handwritten content")
    print("  - Checkboxes and form fields")
    print("  - Tables and multi-column layouts")
    print("  - Overlapping signatures and stamps")
    print("  - Small font sizes")
    print("\n" + "="*80 + "\n")
    
    # Initialize handler
    handler = PDFHandler()
    
    # Extract text
    print("Extracting text...")
    result = handler.extract_text_from_file(pdf_path)
    
    # Display results
    print("\nRESULTS:")
    print("-"*80)
    print(f"✓ Extraction method: {result['method']}")
    print(f"✓ Page count: {result['page_count']}")
    print(f"✓ Text extracted: {len(result['text']):,} characters")
    print(f"✓ Tables found: {len(result.get('tables', []))}")
    
    if result.get('error'):
        print(f"✗ Error: {result['error']}")
        return False
    
    # Check if we got meaningful content
    if len(result['text']) < 1000:
        print("\n✗ WARNING: Extracted text is too short!")
        return False
    
    # Show sample text
    print("\n" + "="*80)
    print("SAMPLE EXTRACTED TEXT (first 500 characters):")
    print("-"*80)
    print(result['text'][:500])
    print("-"*80)
    
    # Check for key content
    print("\n" + "="*80)
    print("CONTENT VALIDATION:")
    print("-"*80)
    
    text_lower = result['text'].lower()
    checks = {
        'Property address (2839 Pendleton)': '2839 pendleton' in text_lower,
        'Seller disclosure keywords': any(kw in text_lower for kw in ['seller', 'disclosure', 'property']),
        'Form fields': any(kw in text_lower for kw in ['yes', 'no', 'check']),
        'San Jose location': 'san jose' in text_lower,
    }
    
    for check_name, passed in checks.items():
        status = "✓" if passed else "✗"
        print(f"{status} {check_name}: {'PASS' if passed else 'FAIL'}")
    
    # Detect document type
    doc_type = handler.detect_document_type(result['text'])
    print(f"\n✓ Document type detected: {doc_type}")
    
    all_passed = all(checks.values())
    
    print("\n" + "="*80)
    if all_passed:
        print("✓✓✓ SUCCESS! PDF parsing is working correctly.")
        print("The complex Pendleton disclosure document was parsed successfully!")
    else:
        print("✗✗✗ ISSUES DETECTED. Some content may not have been extracted.")
    print("="*80 + "\n")
    
    return all_passed


if __name__ == "__main__":
    success = test_pdf_parser()
    sys.exit(0 if success else 1)
