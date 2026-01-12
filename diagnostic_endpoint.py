# Add this to app.py - Diagnostic endpoint

@app.route('/api/system-info', methods=['GET'])
def system_info():
    """Check what's actually available on the server"""
    import subprocess
    import sys
    
    info = {
        'python_version': sys.version,
        'ocr_available': False,
        'tesseract_installed': False,
        'poppler_installed': False,
        'dependencies': {}
    }
    
    # Check Python packages
    try:
        import pytesseract
        info['dependencies']['pytesseract'] = 'installed'
    except ImportError:
        info['dependencies']['pytesseract'] = 'missing'
    
    try:
        import pdf2image
        info['dependencies']['pdf2image'] = 'installed'
    except ImportError:
        info['dependencies']['pdf2image'] = 'missing'
    
    try:
        from PIL import Image
        info['dependencies']['Pillow'] = 'installed'
    except ImportError:
        info['dependencies']['Pillow'] = 'missing'
    
    # Check system commands
    try:
        result = subprocess.run(['tesseract', '--version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            info['tesseract_installed'] = True
            info['tesseract_version'] = result.stdout.split('\n')[0]
    except:
        info['tesseract_installed'] = False
    
    try:
        result = subprocess.run(['pdfinfo', '-v'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            info['poppler_installed'] = True
            info['poppler_version'] = result.stdout.split('\n')[0]
    except:
        info['poppler_installed'] = False
    
    # Overall OCR availability
    info['ocr_available'] = (
        info['dependencies'].get('pytesseract') == 'installed' and
        info['dependencies'].get('pdf2image') == 'installed' and
        info['dependencies'].get('Pillow') == 'installed' and
        info['tesseract_installed'] and
        info['poppler_installed']
    )
    
    return jsonify(info)
