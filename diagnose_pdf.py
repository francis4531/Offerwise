#!/usr/bin/env python3
"""
PDF Diagnostic Tool - Tests all extraction methods on a PDF including OCR
"""

import sys
import logging
from pdf_handler import PDFHandler, OCR_AVAILABLE

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname)s - %(message)s'
)

def diagnose_pdf(pdf_path):
    """Run diagnostics on a PDF file"""
    
    print("="*80)
    print("PDF DIAGNOSTIC TOOL")
    print("="*80)
    print(f"\nTesting: {pdf_path}\n")
    
    # Check OCR availability
    print("OCR Status:")
    if OCR_AVAILABLE:
        print("  ✓ OCR is AVAILABLE (can process scanned PDFs)")
    else:
        print("  ✗ OCR is NOT available")
        print("    Install: pip install pytesseract pdf2image Pillow")
        print("    System: apt-get install poppler-utils tesseract-ocr")
    print()
    
    try:
        # Read the file
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        print(f"✓ File size: {len(pdf_bytes):,} bytes ({len(pdf_bytes)/1024/1024:.2f} MB)")
        print()
        
        # Initialize handler
        handler = PDFHandler()
        
        # Test extraction
        print("-"*80)
        print("TESTING EXTRACTION")
        print("-"*80)
        
        result = handler.extract_text_from_bytes(pdf_bytes)
        
        print(f"\nResult:")
        print(f"  Method used: {result.get('method', 'unknown')}")
        if result.get('ocr_used'):
            print(f"  🔍 OCR was used (scanned document)")
        print(f"  Page count: {result.get('page_count', 0)}")
        print(f"  Text length: {len(result.get('text', ''))}")
        print(f"  Tables found: {len(result.get('tables', []))}")
        
        if result.get('error'):
            print(f"  ⚠️  Error: {result['error']}")
        
        # Show sample text
        text = result.get('text', '')
        if text:
            print(f"\n" + "-"*80)
            print("SAMPLE TEXT (first 500 chars):")
            print("-"*80)
            print(text[:500])
            print()
        else:
            print(f"\n❌ NO TEXT EXTRACTED")
        
        # Diagnosis
        print("\n" + "="*80)
        print("DIAGNOSIS")
        print("="*80)
        
        if result.get('method') == 'failed':
            print("❌ FAILED: All extraction methods failed")
            print("\nPossible causes:")
            print("  1. PDF is a scanned image (no text layer)")
            if not OCR_AVAILABLE:
                print("     ⚠️  OCR is NOT installed - this would likely fix it!")
                print("     Install: pip install pytesseract pdf2image Pillow")
                print("     System: sudo apt-get install poppler-utils tesseract-ocr")
            print("  2. PDF is password-protected")
            print("  3. PDF is corrupted")
            print("  4. PDF uses unusual encoding")
            print("\nSolutions:")
            if not OCR_AVAILABLE:
                print("  • INSTALL OCR (most likely fix for scanned PDFs)")
            print("  • Try re-exporting the PDF from the original source")
            print("  • Remove password protection")
            print("  • Try a different PDF")
        elif result.get('method') == 'ocr':
            print("✓ SUCCESS: Text extracted using OCR")
            print(f"  This was a scanned image PDF")
            print(f"  {len(text)} characters from {result['page_count']} pages")
            print(f"\n  ⏱️  OCR processing took longer but succeeded!")
            print(f"  📄 Scanned PDFs are common for real estate documents")
        elif len(text) < 100:
            print("⚠️  WARNING: Very little text extracted")
            print(f"   Only {len(text)} characters found")
            print("\nThis might be:")
            print("  • A mostly blank PDF")
            print("  • A PDF with mostly images")
            print("  • A PDF with very small text that parser missed")
            if not OCR_AVAILABLE:
                print("  • A scanned PDF (install OCR to confirm)")
        else:
            print("✓ SUCCESS: Text extracted successfully")
            print(f"  {len(text)} characters from {result['page_count']} pages")
            if result.get('method') != 'ocr':
                print(f"  Native PDF with text layer (fast extraction)")
        
        print("="*80)
        
    except FileNotFoundError:
        print(f"❌ ERROR: File not found: {pdf_path}")
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python diagnose_pdf.py <path_to_pdf>")
        print("\nExample:")
        print("  python diagnose_pdf.py document.pdf")
        sys.exit(1)
    
    diagnose_pdf(sys.argv[1])
