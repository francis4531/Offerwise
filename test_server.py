#!/usr/bin/env python3
"""
Quick test to verify OfferWise is working at all
"""

import requests
import sys

def test_offerwise(base_url):
    """Test basic OfferWise functionality"""
    
    print("="*60)
    print("OFFERWISE QUICK TEST")
    print("="*60)
    print(f"\nTesting: {base_url}\n")
    
    # Test 1: Health check
    print("1. Testing health endpoint...")
    try:
        response = requests.get(f"{base_url}/api/health", timeout=10)
        if response.status_code == 200:
            print("   ✓ Server is running")
        else:
            print(f"   ✗ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"   ✗ Cannot reach server: {e}")
        return False
    
    # Test 2: System info (OCR availability)
    print("\n2. Checking OCR availability...")
    try:
        response = requests.get(f"{base_url}/api/system-info", timeout=10)
        if response.status_code == 200:
            data = response.json()
            print(f"   Python version: {data.get('python_version', 'unknown')}")
            print(f"   OCR available: {data.get('ocr_fully_available', False)}")
            print(f"   Tesseract installed: {data.get('tesseract_installed', False)}")
            print(f"   Poppler installed: {data.get('poppler_installed', False)}")
            
            if not data.get('ocr_fully_available'):
                print("\n   ⚠️  WARNING: OCR is NOT available")
                print("   Scanned PDFs will NOT work")
                print("   Only native text PDFs will work")
            else:
                print("\n   ✓ OCR is fully available")
                print("   All PDFs (native + scanned) will work")
        else:
            print(f"   ⚠️  System info endpoint failed: {response.status_code}")
    except Exception as e:
        print(f"   ⚠️  Could not check system info: {e}")
    
    # Test 3: Static files
    print("\n3. Testing static file access...")
    try:
        response = requests.get(f"{base_url}/", timeout=10)
        if response.status_code == 200:
            print("   ✓ Landing page accessible")
        else:
            print(f"   ✗ Landing page failed: {response.status_code}")
    except Exception as e:
        print(f"   ✗ Static files error: {e}")
    
    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)
    print("\nNext steps:")
    print("1. If OCR is NOT available:")
    print("   → Only test with native text PDFs")
    print("   → Or deploy with Docker/Heroku for OCR support")
    print("\n2. If OCR IS available:")
    print("   → All PDFs should work (scanned take 60-90 seconds)")
    print("\n3. Try uploading a PDF in the web interface")
    print(f"   → Visit: {base_url}/app")
    print("="*60)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_server.py <url>")
        print("\nExample:")
        print("  python test_server.py https://your-app.onrender.com")
        print("  python test_server.py http://localhost:5000")
        sys.exit(1)
    
    base_url = sys.argv[1].rstrip('/')
    test_offerwise(base_url)
