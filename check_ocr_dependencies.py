#!/usr/bin/env python3
"""
OfferWise OCR Dependencies Diagnostic
Checks if all required OCR dependencies are installed and working
"""

import sys
import subprocess

def check_command(cmd, name):
    """Check if a system command exists"""
    try:
        result = subprocess.run(
            [cmd, '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip().split('\n')[0]
            print(f"✅ {name}: {version}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    print(f"❌ {name}: NOT FOUND")
    return False

def check_python_package(package_name, import_name=None):
    """Check if a Python package can be imported"""
    if import_name is None:
        import_name = package_name
    
    try:
        __import__(import_name)
        print(f"✅ Python package '{package_name}': Installed")
        return True
    except ImportError as e:
        print(f"❌ Python package '{package_name}': {e}")
        return False

def main():
    print("=" * 60)
    print("OfferWise OCR Dependencies Diagnostic")
    print("=" * 60)
    print()
    
    print("1. System Dependencies:")
    print("-" * 60)
    tesseract_ok = check_command('tesseract', 'Tesseract-OCR')
    poppler_ok = check_command('pdfinfo', 'Poppler (pdf2image dependency)')
    print()
    
    print("2. Python Packages:")
    print("-" * 60)
    pdfplumber_ok = check_python_package('pdfplumber')
    pypdf2_ok = check_python_package('PyPDF2')
    pdf2image_ok = check_python_package('pdf2image')
    pytesseract_ok = check_python_package('pytesseract')
    paddleocr_ok = check_python_package('paddleocr')
    numpy_ok = check_python_package('numpy')
    
    # Check numpy version (must be < 2.0 for PaddleOCR)
    if numpy_ok:
        try:
            import numpy
            version = numpy.__version__
            major_version = int(version.split('.')[0])
            if major_version < 2:
                print(f"✅ NumPy version {version} (compatible with PaddleOCR)")
            else:
                print(f"⚠️  NumPy version {version} - PaddleOCR requires numpy < 2.0")
        except Exception as e:
            print(f"⚠️  Could not check NumPy version: {e}")
    print()
    
    print("3. OCR Functionality Test:")
    print("-" * 60)
    
    # Try to initialize PaddleOCR
    if paddleocr_ok and numpy_ok:
        try:
            print("Testing PaddleOCR initialization...")
            from paddleocr import PaddleOCR
            ocr = PaddleOCR(use_angle_cls=False, lang='en', use_gpu=False, show_log=False)
            print("✅ PaddleOCR initialized successfully")
        except Exception as e:
            print(f"❌ PaddleOCR initialization failed: {e}")
    print()
    
    print("=" * 60)
    print("Summary:")
    print("=" * 60)
    
    all_ok = all([
        tesseract_ok,
        poppler_ok,
        pdfplumber_ok,
        pypdf2_ok,
        pdf2image_ok,
        pytesseract_ok
    ])
    
    if all_ok:
        print("✅ All required dependencies are installed!")
        print("✅ OCR processing should work correctly")
        if not paddleocr_ok:
            print("⚠️  PaddleOCR not available - will use Tesseract (slower)")
    else:
        print("❌ MISSING DEPENDENCIES DETECTED")
        print()
        print("This service needs Docker runtime, not Python runtime!")
        print()
        print("To fix:")
        print("1. Check Render dashboard - is this service using 'Docker' runtime?")
        print("2. If it says 'Python' runtime, you need to create a NEW service")
        print("3. New service should auto-detect Dockerfile and use Docker runtime")
    
    return 0 if all_ok else 1

if __name__ == '__main__':
    sys.exit(main())
