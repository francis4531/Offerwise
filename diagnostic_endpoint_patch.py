# DIAGNOSTIC ENDPOINT FOR OCR 502 ERRORS
# Add this to your app.py to diagnose the issue

# Add after the other /api/ routes (around line 860)

@app.route('/api/diagnostic/ocr-check')
def diagnostic_ocr_check():
    """
    Diagnostic endpoint to check if OCR dependencies are available
    Visit: https://your-app.onrender.com/api/diagnostic/ocr-check
    """
    import subprocess
    
    results = {
        'service_name': 'OfferWise',
        'version': '3.8.4',
        'runtime_detected': 'unknown',
        'system_dependencies': {},
        'python_packages': {},
        'ocr_ready': False,
        'recommendations': []
    }
    
    # Check system commands
    print("Checking system dependencies...")
    for cmd, name, required in [
        ('tesseract', 'Tesseract OCR', True),
        ('pdfinfo', 'Poppler Utils', True),
        ('python3', 'Python', False)
    ]:
        try:
            result = subprocess.run(
                [cmd, '--version'], 
                capture_output=True, 
                text=True, 
                timeout=2
            )
            version_line = result.stdout.strip().split('\n')[0] if result.stdout else result.stderr.strip().split('\n')[0]
            results['system_dependencies'][name] = {
                'installed': True,
                'required': required,
                'version': version_line[:100]  # Limit length
            }
            print(f"✅ {name}: {version_line[:50]}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            results['system_dependencies'][name] = {
                'installed': False,
                'required': required,
                'error': f'{type(e).__name__}'
            }
            print(f"❌ {name}: Not found")
            if required:
                results['recommendations'].append(f"Install {name} (requires Docker runtime)")
    
    # Check Python packages
    print("Checking Python packages...")
    for package_name, import_name in [
        ('PaddleOCR', 'paddleocr'),
        ('PyTesseract', 'pytesseract'),
        ('pdf2image', 'pdf2image'),
        ('NumPy', 'numpy'),
        ('pdfplumber', 'pdfplumber'),
        ('PyPDF2', 'PyPDF2')
    ]:
        try:
            module = __import__(import_name)
            version = getattr(module, '__version__', 'unknown')
            results['python_packages'][package_name] = {
                'installed': True,
                'version': version
            }
            print(f"✅ {package_name}: {version}")
            
            # Special check for NumPy version
            if import_name == 'numpy':
                major_version = int(version.split('.')[0])
                if major_version >= 2:
                    results['python_packages'][package_name]['warning'] = 'PaddleOCR requires numpy < 2.0'
                    results['recommendations'].append('Downgrade NumPy to 1.x for PaddleOCR compatibility')
        except ImportError as e:
            results['python_packages'][package_name] = {
                'installed': False,
                'error': str(e)
            }
            print(f"❌ {package_name}: {e}")
    
    # Determine runtime
    tesseract_ok = results['system_dependencies'].get('Tesseract OCR', {}).get('installed', False)
    poppler_ok = results['system_dependencies'].get('Poppler Utils', {}).get('installed', False)
    
    if tesseract_ok and poppler_ok:
        results['runtime_detected'] = 'Docker ✅'
        results['ocr_ready'] = True
    else:
        results['runtime_detected'] = 'Python ❌'
        results['ocr_ready'] = False
        results['recommendations'].append('CRITICAL: Switch to Docker runtime to enable OCR processing')
        results['recommendations'].append('Current Python runtime cannot install system dependencies')
        results['recommendations'].append('Solution: Create new Render service with Docker runtime')
    
    # Try PaddleOCR initialization (only if packages available)
    if results['python_packages'].get('PaddleOCR', {}).get('installed'):
        try:
            print("Testing PaddleOCR initialization...")
            from paddleocr import PaddleOCR
            # Don't actually initialize (slow), just check import works
            results['paddleocr_test'] = 'Import successful (not initialized to save time)'
        except Exception as e:
            results['paddleocr_test'] = f'Failed: {str(e)}'
            results['recommendations'].append('PaddleOCR import failed - check NumPy compatibility')
    
    # Generate summary
    results['summary'] = {
        'can_process_text_pdfs': results['python_packages'].get('pdfplumber', {}).get('installed', False),
        'can_process_scanned_pdfs': results['ocr_ready'],
        'production_ready': results['ocr_ready'],
        'immediate_action_required': not results['ocr_ready']
    }
    
    print("\n" + "="*60)
    print("DIAGNOSTIC COMPLETE")
    print("="*60)
    print(f"Runtime: {results['runtime_detected']}")
    print(f"OCR Ready: {results['ocr_ready']}")
    print(f"Production Ready: {results['summary']['production_ready']}")
    
    return jsonify(results)


# Add this helper route too for quick text check
@app.route('/api/diagnostic/quick-check')
def diagnostic_quick_check():
    """
    Ultra-fast check - just returns if OCR is available
    Visit: https://your-app.onrender.com/api/diagnostic/quick-check
    """
    try:
        import subprocess
        tesseract = subprocess.run(['tesseract', '--version'], capture_output=True, timeout=1)
        ocr_available = tesseract.returncode == 0
    except:
        ocr_available = False
    
    return jsonify({
        'ocr_available': ocr_available,
        'runtime': 'Docker' if ocr_available else 'Python',
        'status': 'OK' if ocr_available else 'ERROR',
        'message': 'OCR dependencies installed' if ocr_available else 'Missing OCR dependencies - need Docker runtime'
    })
