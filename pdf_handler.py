"""
OfferWise Production PDF Handler
Robust PDF text extraction with fallback strategies including OCR for scanned documents
"""

import pdfplumber
import PyPDF2
from typing import Optional, Dict, Any
from io import BytesIO
import re
import logging
import time
import os
import base64

# Optional: pdfminer for additional fallback
try:
    from pdfminer.high_level import extract_text as pdfminer_extract_text
    PDFMINER_AVAILABLE = True
except ImportError:
    PDFMINER_AVAILABLE = False

# Setup logger FIRST (needed for import logging)
logger = logging.getLogger(__name__)

# OCR support for scanned PDFs (essential for real estate documents)
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter
    OCR_AVAILABLE = True
    
    # CRITICAL: Don't import PaddleOCR at module level!
    # Importing it triggers model downloads (~150MB) and eats memory
    # Instead, import it only when needed (lazy loading)
    PADDLEOCR_AVAILABLE = True  # Assume available, check on first use
    logger.info("⚠️ PaddleOCR will be loaded on first use (lazy loading to save memory)")
except ImportError:
    OCR_AVAILABLE = False
    PADDLEOCR_AVAILABLE = False

# Google Cloud Vision API for fast cloud OCR
try:
    from google.cloud import vision
    GOOGLE_VISION_AVAILABLE = True
except ImportError:
    GOOGLE_VISION_AVAILABLE = False


class PDFHandler:
    """
    Production-grade PDF text extraction.
    Handles various PDF formats with fallback strategies.
    """
    
    def __init__(self):
        self.extraction_stats = {}
        
        # LAZY LOADING: Don't initialize PaddleOCR at startup (saves ~150MB memory)
        # Initialize only when first needed (when processing scanned PDF)
        self.paddle_ocr = None
        self._paddle_ocr_initialized = False
        self._paddle_ocr_failed = False
    
    def _get_paddle_ocr(self):
        """
        Lazy initialization of PaddleOCR.
        Only loads models when first needed, not at startup.
        This prevents memory overflow during app startup.
        
        Set DISABLE_PADDLEOCR=true to skip PaddleOCR and use only Tesseract.
        """
        # Check if PaddleOCR is disabled via environment variable
        if os.environ.get('DISABLE_PADDLEOCR', '').lower() == 'true':
            logger.info("⚠️ PaddleOCR disabled via DISABLE_PADDLEOCR env var")
            self._paddle_ocr_failed = True
            return None
        
        # Already initialized successfully
        if self.paddle_ocr is not None:
            return self.paddle_ocr
        
        # Already tried and failed
        if self._paddle_ocr_failed:
            return None
        
        # First time initialization
        if PADDLEOCR_AVAILABLE and not self._paddle_ocr_initialized:
            self._paddle_ocr_initialized = True
            try:
                # Import PaddleOCR only when needed (not at module level)
                # This prevents model downloads during app startup
                logger.info("🔄 Importing PaddleOCR (first use)...")
                from paddleocr import PaddleOCR
                
                logger.info("🔄 Initializing PaddleOCR models...")
                # Use English model, CPU mode, disable angle classification for speed
                self.paddle_ocr = PaddleOCR(
                    use_angle_cls=False,
                    lang='en',
                    use_gpu=False,
                    show_log=False
                )
                logger.info("✅ PaddleOCR initialized successfully")
                return self.paddle_ocr
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize PaddleOCR: {e}, falling back to Tesseract")
                self._paddle_ocr_failed = True
                self.paddle_ocr = None
        
        return None
    
    def _preprocess_image(self, image: Image.Image) -> Image.Image:
        """
        Optimize image for OCR: increase contrast, denoise, sharpen
        Significantly improves OCR accuracy and speed
        """
        try:
            # Convert to grayscale
            if image.mode != 'L':
                image = image.convert('L')
            
            # Increase contrast
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2.0)
            
            # Sharpen
            image = image.filter(ImageFilter.SHARPEN)
            
            # Denoise (remove noise)
            image = image.filter(ImageFilter.MedianFilter(size=3))
            
            return image
        except Exception as e:
            logger.warning(f"Image preprocessing failed: {e}, using original")
            return image
    
    def extract_text_from_file(self, pdf_path: str) -> Dict[str, Any]:
        """
        Extract text from PDF file path.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Dict with extracted text and metadata
        """
        with open(pdf_path, 'rb') as file:
            return self.extract_text_from_bytes(file.read())
    
    def extract_text_from_bytes(self, pdf_bytes: bytes, progress_callback=None, progress_key=None, ocr_progress_dict=None) -> Dict[str, Any]:
        """
        Extract text from PDF bytes (for uploaded files).
        
        Args:
            pdf_bytes: PDF file as bytes
            progress_callback: Optional callback function(current, total, message) for progress updates
            progress_key: Key to track progress in ocr_progress dict
            ocr_progress_dict: Reference to global ocr_progress dict for cancellation checks
            
        Returns:
            Dict containing:
                - text: Extracted text
                - page_count: Number of pages
                - method: Extraction method used
                - tables: Extracted tables (if any)
        """
        
        # Store cancellation check params
        self.progress_key = progress_key
        self.ocr_progress_dict = ocr_progress_dict
        
        # Try pdfplumber first (best for structure)
        try:
            result = self._extract_with_pdfplumber(pdf_bytes)
            if result and len(result['text']) > 100:
                logger.info(f"Successfully extracted text using pdfplumber ({len(result['text'])} chars)")
                return result
        except Exception as e:
            logger.warning(f"pdfplumber extraction failed: {e}")
        
        # Try pdfminer next (good for complex layouts)
        if PDFMINER_AVAILABLE:
            try:
                result = self._extract_with_pdfminer(pdf_bytes)
                if result and len(result['text']) > 100:
                    logger.info(f"Successfully extracted text using pdfminer ({len(result['text'])} chars)")
                    return result
            except Exception as e:
                logger.warning(f"pdfminer extraction failed: {e}")
        
        # Fallback to PyPDF2
        try:
            result = self._extract_with_pypdf2(pdf_bytes)
            if result and len(result['text']) > 100:
                logger.info(f"Successfully extracted text using PyPDF2 ({len(result['text'])} chars)")
                return result
        except Exception as e:
            logger.warning(f"PyPDF2 extraction failed: {e}")
        
        # SMART PAGE DETECTION: Check each page individually before full OCR
        # This is a HUGE optimization for mixed documents (50-70% time savings!)
        if OCR_AVAILABLE:
            try:
                logger.info("🔍 All text extraction failed - trying SMART PAGE DETECTION...")
                result = self._extract_with_smart_page_detection(pdf_bytes, progress_callback=progress_callback)
                if result and len(result['text']) > 100:
                    savings = result.get('optimization', {}).get('time_saved_percentage', 0)
                    if savings > 0:
                        logger.info(f"🎉 SMART DETECTION SUCCESS! Saved ~{savings}% processing time")
                    logger.info(f"Successfully extracted text ({len(result['text'])} chars) - Method: {result.get('method', 'unknown')}")
                    return result
            except Exception as e:
                logger.error(f"Smart page detection failed: {e}")
                logger.info("Falling back to traditional full-OCR approach...")
        
        # Final fallback: Traditional full OCR (only if smart detection unavailable or failed)
        if OCR_AVAILABLE:
            try:
                logger.info("Using traditional full-OCR approach (processing all pages)...")
                result = self._extract_with_ocr(pdf_bytes, progress_callback=progress_callback)
                if result and len(result['text']) > 100:
                    logger.info(f"Successfully extracted text using OCR ({len(result['text'])} chars)")
                    return result
            except Exception as e:
                logger.error(f"OCR extraction failed: {e}")
        else:
            logger.warning("OCR not available - install pytesseract and pdf2image for scanned document support")
        
        # If all else fails, return empty with detailed error
        logger.error("All PDF extraction methods failed - no text could be extracted")
        
        # Check if this might be a scanned document
        try:
            import PyPDF2
            pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
            page_count = len(pdf_reader.pages)
        except Exception:
            page_count = 0
        
        error_message = 'Could not extract text from PDF.'
        
        if not OCR_AVAILABLE:
            error_message += ' This may be a scanned image PDF. OCR support is not installed on this server. Please contact support or use a text-based PDF.'
        else:
            error_message += ' This may be a scanned image, password-protected, or corrupted file.'
        
        return {
            'text': '',
            'page_count': page_count,
            'method': 'failed',
            'tables': [],
            'error': error_message
        }
    
    def _extract_with_pdfplumber(self, pdf_bytes: bytes) -> Dict[str, Any]:
        """Extract using pdfplumber (best for structure and tables)"""
        
        text_parts = []
        tables_data = []
        page_count = 0
        
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            
            for page_num, page in enumerate(pdf.pages, 1):
                # Extract text with layout preservation for complex forms
                try:
                    # Try with layout first (better for forms)
                    page_text = page.extract_text(layout=True)
                except Exception:
                    # Fallback to regular extraction
                    page_text = page.extract_text()
                
                if page_text:
                    text_parts.append(f"\n--- Page {page_num} ---\n")
                    text_parts.append(page_text)
                
                # Extract tables with better settings for complex forms
                try:
                    tables = page.extract_tables({
                        "vertical_strategy": "lines_strict",
                        "horizontal_strategy": "lines_strict",
                        "snap_tolerance": 3,
                        "intersection_tolerance": 3
                    })
                    for table in tables:
                        if table:
                            tables_data.append({
                                'page': page_num,
                                'data': table
                            })
                except Exception:
                    # Fallback to default table extraction
                    tables = page.extract_tables()
                    for table in tables:
                        if table:
                            tables_data.append({
                                'page': page_num,
                                'data': table
                            })
        
        return {
            'text': '\n'.join(text_parts),
            'page_count': page_count,
            'method': 'pdfplumber',
            'tables': tables_data
        }
    
    def _extract_with_pdfminer(self, pdf_bytes: bytes) -> Dict[str, Any]:
        """Extract using pdfminer (good for complex layouts and forms)"""
        
        if not PDFMINER_AVAILABLE:
            raise ImportError("pdfminer not available")
        
        text = pdfminer_extract_text(BytesIO(pdf_bytes))
        
        # Get page count using PyPDF2
        try:
            pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
            page_count = len(pdf_reader.pages)
        except Exception:
            page_count = 0
        
        return {
            'text': text,
            'page_count': page_count,
            'method': 'pdfminer',
            'tables': []
        }
    
    def _extract_with_pypdf2(self, pdf_bytes: bytes) -> Dict[str, Any]:
        """Extract using PyPDF2 (fallback method)"""
        
        text_parts = []
        page_count = 0
        
        pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
        page_count = len(pdf_reader.pages)
        
        for page_num, page in enumerate(pdf_reader.pages, 1):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"\n--- Page {page_num} ---\n")
                text_parts.append(page_text)
        
        return {
            'text': '\n'.join(text_parts),
            'page_count': page_count,
            'method': 'pypdf2',
            'tables': []
        }
    
    def _extract_with_smart_page_detection(self, pdf_bytes: bytes, progress_callback=None) -> Dict[str, Any]:
        """
        SMART PAGE DETECTION: Try text extraction on each page individually before OCR.
        
        This is a HUGE optimization for mixed documents (some pages text-based, some scanned).
        Real estate documents are often mixed:
        - Seller disclosure: scanned handwriting (needs OCR)
        - Inspection report: digital PDF (text extraction works!)
        - County records: scanned stamps (needs OCR)
        
        Instead of OCR'ing everything, we:
        1. Try text extraction on EACH PAGE
        2. Only OCR pages that truly need it
        
        Result: Can cut processing time by 50-70% for mixed documents!
        
        Args:
            pdf_bytes: PDF file as bytes
            progress_callback: Optional callback function(current, total, message) for progress updates
            
        Returns:
            Dict with extracted text and metadata
        """
        logger.info("🔍 Starting SMART PAGE DETECTION - checking each page individually...")
        
        try:
            import PyPDF2
            from io import BytesIO
            
            pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
            page_count = len(pdf_reader.pages)
            
            text_parts = []
            pages_needing_ocr = []
            pages_with_text = 0
            
            logger.info(f"📄 Analyzing {page_count} pages individually...")
            
            # Try text extraction on each page
            for page_num in range(page_count):
                if progress_callback:
                    progress_callback(page_num, page_count, f'Analyzing page {page_num + 1} of {page_count}...')
                
                try:
                    page = pdf_reader.pages[page_num]
                    page_text = page.extract_text()
                    
                    # Check if page has meaningful text (at least 50 characters)
                    if page_text and len(page_text.strip()) > 50:
                        text_parts.append(page_text)
                        pages_with_text += 1
                        logger.info(f"✅ Page {page_num + 1}: Text extraction SUCCESS ({len(page_text)} chars)")
                    else:
                        # Page appears to be scanned/image-based - needs OCR
                        pages_needing_ocr.append(page_num + 1)
                        text_parts.append("")  # Placeholder
                        logger.info(f"📸 Page {page_num + 1}: Appears scanned - will need OCR")
                        
                except Exception as e:
                    logger.warning(f"⚠️ Page {page_num + 1}: Text extraction failed - {e}")
                    pages_needing_ocr.append(page_num + 1)
                    text_parts.append("")  # Placeholder
            
            # Summary
            ocr_percentage = (len(pages_needing_ocr) / page_count) * 100
            logger.info("=" * 60)
            logger.info(f"📊 SMART DETECTION RESULTS:")
            logger.info(f"   ✅ Pages with text: {pages_with_text}/{page_count} ({100-ocr_percentage:.0f}%)")
            logger.info(f"   📸 Pages needing OCR: {len(pages_needing_ocr)}/{page_count} ({ocr_percentage:.0f}%)")
            logger.info(f"   ⚡ Time savings: ~{(100-ocr_percentage):.0f}% faster than OCR-only approach!")
            logger.info("=" * 60)
            
            # If we got enough text without OCR, return it
            combined_text = '\n\n'.join([t for t in text_parts if t])
            if len(combined_text.strip()) > 200 and len(pages_needing_ocr) == 0:
                logger.info("🎉 All pages extracted via text - NO OCR NEEDED!")
                return {
                    'text': combined_text,
                    'page_count': page_count,
                    'method': 'smart_detection_text_only',
                    'tables': []
                }
            
            # If we have mixed content, OCR only the scanned pages
            if pages_needing_ocr and OCR_AVAILABLE:
                logger.info(f"🔄 OCR'ing {len(pages_needing_ocr)} scanned pages: {pages_needing_ocr}")
                
                # OCR only the pages that need it
                ocr_results = self._ocr_specific_pages(
                    pdf_bytes, 
                    pages_needing_ocr, 
                    page_count,
                    progress_callback
                )
                
                # Insert OCR results into the correct positions
                for page_num, ocr_text in ocr_results.items():
                    text_parts[page_num - 1] = ocr_text
                
                combined_text = '\n\n'.join([t for t in text_parts if t])
                
                return {
                    'text': combined_text,
                    'page_count': page_count,
                    'method': f'smart_detection_mixed (text: {pages_with_text}, ocr: {len(pages_needing_ocr)})',
                    'tables': [],
                    'optimization': {
                        'pages_with_text': pages_with_text,
                        'pages_needing_ocr': len(pages_needing_ocr),
                        'time_saved_percentage': int(100 - ocr_percentage)
                    }
                }
            
            # Fallback: if smart detection found nothing, OCR everything
            logger.warning("⚠️ Smart detection found minimal text - falling back to full OCR")
            return self._extract_with_ocr(pdf_bytes, progress_callback)
            
        except Exception as e:
            logger.error(f"❌ Smart page detection failed: {e}")
            logger.info("📸 Falling back to full OCR")
            return self._extract_with_ocr(pdf_bytes, progress_callback)
    
    def _ocr_specific_pages(self, pdf_bytes: bytes, page_numbers: list, total_pages: int, progress_callback=None) -> dict:
        """
        OCR only specific pages (not all pages).
        Used by smart page detection to OCR only scanned pages.
        
        Supports three OCR engines:
        1. Google Vision API (if USE_GOOGLE_VISION=true) - FAST, accurate, $1.50/1000 pages
        2. PaddleOCR (if enabled) - Fast but memory-intensive
        3. Tesseract (fallback) - Slow but reliable
        
        Args:
            pdf_bytes: PDF file as bytes
            page_numbers: List of page numbers to OCR (1-indexed)
            total_pages: Total number of pages in document
            progress_callback: Optional progress callback
            
        Returns:
            Dict mapping page_number -> extracted_text
        """
        logger.info(f"🔄 OCR'ing {len(page_numbers)} specific pages out of {total_pages} total")
        
        # Check if Google Vision is enabled
        use_google_vision = os.environ.get('USE_GOOGLE_VISION', 'false').lower() == 'true'
        
        if use_google_vision and GOOGLE_VISION_AVAILABLE:
            try:
                logger.info("🚀 Using Google Cloud Vision API (fast, accurate)")
                return self._ocr_with_google_vision(pdf_bytes, page_numbers, total_pages, progress_callback)
            except Exception as e:
                logger.error(f"❌ Google Vision failed: {e}")
                logger.info("⚠️ Falling back to local OCR (Tesseract/PaddleOCR)")
                # Fall through to local OCR
        
        # Local OCR (PaddleOCR or Tesseract)
        logger.info("🔄 Using local OCR (Tesseract/PaddleOCR)")
        
        import gc
        from pdf2image import convert_from_bytes
        
        # Get settings
        dpi = int(os.environ.get('OCR_DPI', '100'))
        
        results = {}
        
        for idx, page_num in enumerate(page_numbers):
            try:
                # Update progress
                if progress_callback:
                    progress_callback(
                        page_num, 
                        total_pages, 
                        f'OCR processing page {page_num} of {total_pages} ({idx + 1}/{len(page_numbers)} scanned pages)...'
                    )
                
                # Convert only this specific page
                images = convert_from_bytes(
                    pdf_bytes,
                    dpi=dpi,
                    first_page=page_num,
                    last_page=page_num
                )
                
                if images:
                    image = self._preprocess_image(images[0])
                    
                    # Try PaddleOCR first, Tesseract fallback
                    page_text = ""
                    paddle_ocr = self._get_paddle_ocr()
                    
                    if paddle_ocr:
                        try:
                            import numpy as np
                            img_array = np.array(image)
                            result = paddle_ocr.ocr(img_array, cls=False)
                            
                            if result and result[0]:
                                text_lines = [line[1][0] for line in result[0] if line[1][0]]
                                page_text = '\n'.join(text_lines)
                                logger.info(f"✅ Page {page_num}: PaddleOCR extracted {len(page_text)} chars")
                        except Exception as e:
                            logger.warning(f"⚠️ PaddleOCR failed for page {page_num}: {e}")
                    
                    # Fallback to Tesseract
                    if not page_text.strip():
                        page_text = pytesseract.image_to_string(
                            image,
                            lang='eng',
                            config='--psm 3 --oem 1'
                        )
                        logger.info(f"✅ Page {page_num}: Tesseract extracted {len(page_text)} chars")
                    
                    results[page_num] = page_text
                    
                    # Clean memory
                    del images, image
                    gc.collect()
                    
            except Exception as e:
                logger.error(f"❌ Failed to OCR page {page_num}: {e}")
                results[page_num] = ""
        
        return results
    
    def _ocr_with_google_vision(self, pdf_bytes: bytes, page_numbers: list, total_pages: int, progress_callback=None) -> dict:
        """
        OCR specific pages using Google Cloud Vision API.
        
        FAST: 44 pages in 30-60 seconds (vs 10 minutes with Tesseract)
        ACCURATE: Purpose-built for OCR, handles handwriting and forms excellently
        COST: $1.50 per 1,000 pages ($0.07 for typical 44-page doc)
        
        Requires: GOOGLE_APPLICATION_CREDENTIALS environment variable pointing to service account JSON
        
        Args:
            pdf_bytes: PDF file as bytes
            page_numbers: List of page numbers to OCR (1-indexed)
            total_pages: Total number of pages in document
            progress_callback: Optional progress callback
            
        Returns:
            Dict mapping page_number -> extracted_text
        """
        if not GOOGLE_VISION_AVAILABLE:
            raise ImportError("Google Cloud Vision not available. Install: pip install google-cloud-vision")
        
        logger.info(f"🚀 Using Google Cloud Vision API for {len(page_numbers)} pages")
        logger.info(f"💰 Estimated cost: ${(len(page_numbers) / 1000) * 1.50:.4f}")
        
        from google.cloud import vision
        import io
        
        # Initialize Vision API client
        try:
            client = vision.ImageAnnotatorClient()
        except Exception as e:
            logger.error(f"❌ Failed to initialize Google Vision client: {e}")
            logger.error("💡 Make sure GOOGLE_APPLICATION_CREDENTIALS environment variable is set")
            raise
        
        results = {}
        dpi = int(os.environ.get('OCR_DPI', '150'))  # Higher DPI for cloud OCR (better quality)
        
        for idx, page_num in enumerate(page_numbers):
            try:
                # CHECK FOR CANCELLATION - Save costs by stopping Google Vision calls
                if self.ocr_progress_dict and self.progress_key:
                    if self.ocr_progress_dict.get(self.progress_key, {}).get('cancelled', False):
                        logger.info(f"🛑 User cancelled OCR at page {page_num} - stopping Google Vision calls to save costs")
                        logger.info(f"💰 Saved: ${((len(page_numbers) - idx) / 1000) * 1.50:.4f} by stopping early")
                        # Return empty results for remaining pages
                        for remaining_page in page_numbers[idx:]:
                            results[remaining_page] = ""
                        break
                
                # Update progress (this will also check cancellation)
                if progress_callback:
                    continue_processing = progress_callback(
                        page_num,
                        total_pages,
                        f'Processing page {page_num} of {total_pages} ({idx + 1}/{len(page_numbers)} scanned pages)...'
                    )
                    # If callback returns False, stop processing
                    if continue_processing == False:
                        logger.info(f"🛑 Progress callback signaled cancellation at page {page_num}")
                        logger.info(f"💰 Saved: ${((len(page_numbers) - idx) / 1000) * 1.50:.4f}")
                        for remaining_page in page_numbers[idx:]:
                            results[remaining_page] = ""
                        break
                
                # Convert PDF page to high-quality image
                images = convert_from_bytes(
                    pdf_bytes,
                    dpi=dpi,
                    first_page=page_num,
                    last_page=page_num
                )
                
                if images:
                    # DOUBLE-CHECK CANCELLATION before expensive API call
                    if self.ocr_progress_dict and self.progress_key:
                        if self.ocr_progress_dict.get(self.progress_key, {}).get('cancelled', False):
                            logger.info(f"🛑 Cancelled before API call for page {page_num} - saved $0.0015")
                            results[page_num] = ""
                            for remaining_page in page_numbers[idx:]:
                                results[remaining_page] = ""
                            break
                    
                    # Convert PIL Image to bytes
                    img_byte_arr = io.BytesIO()
                    images[0].save(img_byte_arr, format='PNG')
                    img_byte_arr.seek(0)
                    
                    # Create Vision API Image object
                    image = vision.Image(content=img_byte_arr.getvalue())
                    
                    # Call Google Vision API - document_text_detection is optimized for dense text
                    response = client.document_text_detection(image=image)
                    
                    # Check for errors
                    if response.error.message:
                        raise Exception(f"Google Vision API error: {response.error.message}")
                    
                    # Extract text
                    if response.full_text_annotation:
                        page_text = response.full_text_annotation.text
                        logger.info(f"✅ Page {page_num}: Google Vision extracted {len(page_text)} chars")
                    else:
                        page_text = ""
                        logger.warning(f"⚠️ Page {page_num}: No text detected by Google Vision")
                    
                    results[page_num] = page_text
                    
                    # Clean memory
                    del images, img_byte_arr
                    import gc
                    gc.collect()
                    
            except Exception as e:
                logger.error(f"❌ Google Vision failed for page {page_num}: {e}")
                results[page_num] = ""
        
        logger.info(f"🎉 Google Vision completed {len(page_numbers)} pages successfully")
        return results
    
    def _extract_with_ocr(self, pdf_bytes: bytes, progress_callback=None) -> Dict[str, Any]:
        """
        Extract text using OCR (for fully scanned image PDFs).
        This is the fallback when smart page detection determines ALL pages need OCR.
        Memory-optimized: processes pages one at a time.
        
        Args:
            pdf_bytes: PDF file as bytes
            progress_callback: Optional callback function(current, total, message) for progress updates
        """
        
        if not OCR_AVAILABLE:
            raise ImportError("OCR dependencies not available. Install: pip install pytesseract pdf2image")
        
        logger.info("Converting PDF pages to images for OCR processing...")
        
        try:
            # Get DPI setting from environment (default 75 for memory efficiency)
            # Lower DPI = less memory, faster processing, slightly lower accuracy
            # For real estate docs with clear text, 75 DPI is usually sufficient
            dpi = int(os.environ.get('OCR_DPI', '75'))
            logger.info(f"Using DPI: {dpi} (set OCR_DPI env var to adjust)")
            
            # Get parallel workers setting (default 1 for memory safety on 512MB plan)
            max_workers = int(os.environ.get('OCR_PARALLEL_WORKERS', '1'))
            logger.info(f"Using {max_workers} parallel OCR workers (batch processing mode)")
            
            import gc
            from pdf2image import convert_from_bytes
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            # First, get page count
            try:
                import PyPDF2
                pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
                page_count = len(pdf_reader.pages)
            except Exception:
                page_count = 1
            
            # Notify progress: starting
            if progress_callback:
                progress_callback(0, page_count, f'Processing {page_count} pages with AI...')
            
            logger.info(f"Processing {page_count} pages with OCR (batch mode: {max_workers} pages at a time)...")
            logger.info(f"Strategy: Process in batches of {max_workers} to prevent memory overflow on 512MB plan")
            
            def process_single_page(page_num):
                """Process a single page with OCR - PaddleOCR first, Tesseract fallback"""
                try:
                    # Convert only this page
                    images = convert_from_bytes(
                        pdf_bytes, 
                        dpi=dpi,
                        first_page=page_num,
                        last_page=page_num
                    )
                    
                    if images:
                        # Preprocess image for better OCR
                        image = self._preprocess_image(images[0])
                        page_text = ""
                        
                        # Try PaddleOCR first (3x faster) - lazy load on first use
                        paddle_ocr = self._get_paddle_ocr()
                        if paddle_ocr:
                            try:
                                import numpy as np
                                # Convert PIL Image to numpy array
                                img_array = np.array(image)
                                
                                # Run PaddleOCR
                                result = paddle_ocr.ocr(img_array, cls=False)
                                
                                # Extract text from result
                                if result and result[0]:
                                    text_lines = []
                                    for line in result[0]:
                                        if line[1][0]:  # line[1][0] contains the text
                                            text_lines.append(line[1][0])
                                    page_text = '\n'.join(text_lines)
                                    
                                    if page_text.strip():
                                        # Successfully extracted with PaddleOCR
                                        del images, image, img_array
                                        return (page_num, page_text, 'paddleocr')
                                        
                            except Exception as e:
                                logger.warning(f"PaddleOCR failed for page {page_num}, falling back to Tesseract: {e}")
                        
                        # Fallback to Tesseract if PaddleOCR failed or unavailable
                        if not page_text.strip():
                            page_text = pytesseract.image_to_string(
                                image, 
                                lang='eng',
                                config='--psm 3 --oem 1'
                            )
                            method = 'tesseract'
                        else:
                            method = 'paddleocr'
                        
                        # Clear memory
                        del images, image
                        
                        if page_text.strip():
                            return (page_num, page_text, method)
                        else:
                            return (page_num, "", method)
                    else:
                        return (page_num, "", 'none')
                        
                except Exception as e:
                    logger.warning(f"Failed to process page {page_num}: {e}")
                    return (page_num, "", 'error')
            
            # Track OCR methods used
            ocr_methods = {'paddleocr': 0, 'tesseract': 0, 'error': 0, 'none': 0}
            
            # Process pages in small batches to prevent memory overflow
            page_texts = {}
            completed = 0
            batch_size = max_workers  # Process only max_workers pages at a time
            
            # Process in batches
            for batch_start in range(1, page_count + 1, batch_size):
                batch_end = min(batch_start + batch_size, page_count + 1)
                batch_pages = range(batch_start, batch_end)
                
                # Process this batch in parallel
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_page = {
                        executor.submit(process_single_page, page_num): page_num 
                        for page_num in batch_pages
                    }
                    
                    # Collect results as they complete
                    for future in as_completed(future_to_page):
                        page_num, page_text, method = future.result()
                        page_texts[page_num] = page_text
                        ocr_methods[method] = ocr_methods.get(method, 0) + 1
                        completed += 1
                        
                        # Update progress callback
                        if progress_callback:
                            progress_callback(completed, page_count, f'Processing page {completed} of {page_count}...')
                        
                        # Log progress
                        if completed % 5 == 0 or completed == page_count:
                            logger.info(f"OCR progress: {completed}/{page_count} pages completed")
                
                # Aggressive garbage collection after each batch
                gc.collect()
                logger.info(f"Batch {batch_start}-{batch_end-1} complete, memory cleaned")
            
            # Reassemble pages in correct order
            text_parts = []
            for page_num in sorted(page_texts.keys()):
                page_text = page_texts[page_num]
                if page_text:
                    text_parts.append(f"\n--- Page {page_num} (OCR) ---\n")
                    text_parts.append(page_text)
            
            full_text = '\n'.join(text_parts)
            
            # Log OCR method statistics
            primary_method = 'paddleocr' if ocr_methods['paddleocr'] > 0 else 'tesseract'
            logger.info(f"OCR completed: Extracted {len(full_text)} characters from {page_count} pages")
            logger.info(f"📊 OCR methods used: PaddleOCR: {ocr_methods['paddleocr']}, Tesseract: {ocr_methods['tesseract']}")
            
            return {
                'text': full_text,
                'page_count': page_count,
                'method': f'ocr-{primary_method}',
                'tables': [],
                'ocr_used': True,
                'ocr_stats': ocr_methods
            }
            
        except Exception as e:
            logger.error(f"OCR processing error: {e}")
            raise
    
    def detect_document_type(self, pdf_text: str) -> str:
        """
        Detect if document is seller disclosure, inspection report, or other.
        
        Returns:
            'seller_disclosure', 'inspection_report', 'hoa_docs', or 'unknown'
        """
        
        # Collapse whitespace from pdfplumber layout mode before keyword matching
        import re
        text_lower = re.sub(r'\s+', ' ', pdf_text.lower().strip())
        
        # Seller disclosure indicators
        disclosure_keywords = [
            'seller disclosure',
            'transfer disclosure',
            'real property disclosure',
            'spds',
            'disclosure statement'
        ]
        if any(kw in text_lower for kw in disclosure_keywords):
            return 'seller_disclosure'
        
        # Inspection report indicators
        inspection_keywords = [
            'inspection report',
            'home inspection',
            'property inspection',
            'inspector',
            'internachi',
            'ashi'
        ]
        if any(kw in text_lower for kw in inspection_keywords):
            return 'inspection_report'
        
        # HOA documents
        hoa_keywords = [
            'homeowners association',
            'hoa',
            'cc&r',
            'covenants',
            'hoa dues',
            'association fees'
        ]
        if any(kw in text_lower for kw in hoa_keywords):
            return 'hoa_docs'
        
        return 'unknown'


# =============================================================================
# TEXT QUALITY VALIDATION & VISION FALLBACK
# =============================================================================

def is_meaningful_extraction(text, page_count=1):
    """
    Check if extracted text is meaningful content vs just metadata/garbage.
    DocuSign PDFs often extract only envelope IDs and signature metadata.
    
    IMPORTANT: pdfplumber's layout=True mode pads lines with spaces to preserve
    visual layout. We must collapse whitespace before analyzing, otherwise:
    - DocuSign ratios get diluted by padding (0.68% instead of 21%)
    - Char counts are inflated (15K chars for 200 chars of actual content)
    - Keyword matching fails ("TRANSFER   DISCLOSURE" != "transfer disclosure")
    
    Returns: (is_meaningful: bool, reason: str)
    """
    if not text or len(text.strip()) < 50:
        return False, "too_short"
    
    # CRITICAL: Collapse whitespace before all analysis
    # pdfplumber layout mode inserts massive padding that fools every check
    import re
    normalized = re.sub(r'\s+', ' ', text.strip())
    
    # Check word count on normalized text
    words = normalized.split()
    if len(words) < 20:
        return False, "too_few_words"
    
    # Check if mostly DocuSign metadata (using normalized text for accurate ratio)
    docusign_indicators = ['docusign', 'envelope id', 'envelope_id']
    text_lower = normalized.lower()
    docusign_chars = sum(len(ind) * text_lower.count(ind) for ind in docusign_indicators)
    if docusign_chars > len(normalized) * 0.15:
        return False, "docusign_metadata"
    
    # Check chars per page using NORMALIZED length (not padded length)
    chars_per_page = len(normalized) / max(page_count, 1)
    if chars_per_page < 100 and page_count > 1:
        return False, f"sparse_text_{chars_per_page:.0f}_per_page"
    
    # Check for real estate / disclosure keywords
    content_keywords = [
        'property', 'seller', 'buyer', 'disclosure', 'inspection',
        'roof', 'plumbing', 'electrical', 'foundation', 'water',
        'repair', 'condition', 'defect', 'damage', 'permit',
        'known', 'aware', 'yes', 'no', 'n/a'
    ]
    keyword_hits = sum(1 for kw in content_keywords if kw in text_lower)
    
    if keyword_hits < 3 and len(normalized) < 500:
        return False, "no_real_estate_content"
    
    return True, "ok"


def is_tds_complete(text):
    """
    Check if a California TDS (Transfer Disclosure Statement) extraction 
    captured the key sections. Scanned/handwritten TDS forms often have
    partial extraction where checkboxes and handwritten notes are missed.
    
    Returns: (is_complete: bool, completeness_score: float 0-1, missing: list)
    """
    if not text:
        return False, 0.0, ["no_text"]
    
    # Collapse whitespace from pdfplumber layout mode before keyword matching
    import re
    text_lower = re.sub(r'\s+', ' ', text.lower().strip())
    missing = []
    
    # Key TDS sections that MUST be present for a complete extraction
    tds_markers = {
        'section_a_items': [
            # Section A: Property has the items checked below
            'range', 'dishwasher', 'washer', 'smoke detector', 'fire alarm',
            'garage', 'roof', 'fireplace'
        ],
        'section_b_defects': [
            # Section B: Are you aware of significant defects
            'interior walls', 'ceiling', 'floor', 'exterior wall',
            'roof', 'foundation', 'slab', 'driveway', 'sidewalk',
            'plumbing', 'electrical', 'other structural'
        ],
        'section_c_awareness': [
            # Section C: Are you aware of...
            'environmental hazard', 'asbestos', 'lead', 'mold',
            'encroachment', 'easement', 'room addition', 'structural modification',
            'permit', 'fill', 'settling', 'sliding', 'soil',
            'flooding', 'drainage', 'major damage', 'earthquake',
            'neighborhood noise', 'cc&r', 'homeowners', 'association',
            'lawsuit', 'abatement', 'citation'
        ],
        'section_d_compliance': [
            # Section D: Seller certifies compliance
            'smoke detector', 'water heater', 'braced', 'anchored', 'strapped'
        ]
    }
    
    section_scores = {}
    for section, keywords in tds_markers.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        score = hits / len(keywords) if keywords else 0
        section_scores[section] = score
        if score < 0.3:
            missing.append(section)
    
    # Overall completeness
    overall = sum(section_scores.values()) / len(section_scores) if section_scores else 0
    is_complete = overall >= 0.4 and len(missing) <= 1
    
    return is_complete, overall, missing


def extract_text_via_vision(pdf_base64, api_key=None, document_type=None):
    """
    Use Anthropic's document vision API to extract text from a PDF.
    Fallback for when text extraction fails (scanned docs, DocuSign, handwritten).
    
    Uses document-type-specific prompts for maximum accuracy.
    
    Args:
        pdf_base64: Base64-encoded PDF (without data URL prefix)
        api_key: Anthropic API key (or reads from env)
        document_type: 'seller_disclosure', 'inspection_report', or None
        
    Returns:
        dict with 'text' key containing extracted text, or None on failure
    """
    import os
    
    if not api_key:
        api_key = os.environ.get('ANTHROPIC_API_KEY')
    
    if not api_key:
        logger.warning("Cannot use vision fallback: ANTHROPIC_API_KEY not set")
        return None
    
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        
        # Use TDS-specific prompt for seller disclosures
        if document_type == 'seller_disclosure':
            extraction_prompt = """Extract ALL text from this California Real Estate Transfer Disclosure Statement (TDS). Be 100% faithful to what appears on the page. This is a legal document and every detail matters.

CRITICAL INSTRUCTIONS:
1. For EVERY checkbox: write [X] if checked/marked, [ ] if empty/unchecked. Do NOT guess.
2. For EVERY handwritten entry: transcribe exactly what is written, even if messy. If truly illegible, write [illegible].
3. For Section A (property items): list every item and whether it is checked [X] or unchecked [ ].
4. For Section B (defects/malfunctions): list every category checked and the handwritten description if any.
5. For Section C (awareness questions 1-16): for each numbered question, state whether answered Yes [X], No [X], or left blank [ ].
6. For Section D (seller certification): capture all text and dates.
7. Capture ALL signatures, dates, initials, and agent information.
8. Capture the property address, date, and any DocuSign or envelope identifiers.
9. Do NOT add any text that does not appear in the document.
10. Do NOT interpret or summarize. Extract raw text only.

If a page is blank or contains only signatures, still note what is visible."""
        elif document_type == 'inspection_report':
            extraction_prompt = """Extract ALL text AND describe ALL photos from this home inspection report.

TEXT EXTRACTION RULES:
1. Capture every section header, finding, and recommendation.
2. For ratings/conditions (e.g., Satisfactory, Marginal, Deficient, Not Inspected): transcribe exactly as shown.
3. Include all photo captions and labels if present.
4. For handwritten notes: transcribe exactly. If illegible, write [illegible].
5. Capture inspector name, license number, date, and property address.
6. Include all page numbers and section references.

PHOTO DESCRIPTION RULES (CRITICAL - photos contain key evidence for offer pricing):
7. For EVERY photo in the report, add a DETAILED description in this format:
   [PHOTO: exhaustive factual description of everything visible]
8. Be EXHAUSTIVE, not brief. Describe EVERY observable detail:
   - Material types and condition (e.g., "copper pipe with green patina", "composition shingle with granule loss")
   - Colors and discoloration (e.g., "dark brown water stain with black edges suggesting mold growth", "orange rust streaks")
   - Damage characteristics: size, shape, pattern, extent (e.g., "diagonal crack approximately 3 feet long with 1/4 inch displacement")
   - Moisture indicators: stains, efflorescence, water marks, condensation, pooling
   - Biological growth: mold color and coverage area, moss, algae, wood rot, fungal damage
   - Equipment details: brand names, model numbers, rating plates, manufacture dates if visible
   - Safety issues: exposed wiring, missing covers, improper connections, trip hazards
   - Surrounding context: what room/area, nearby components, whether issue appears isolated or widespread
   - Signs of prior repair: patches, sealant, different materials, paint over damage
9. Include approximate dimensions and scale whenever possible.
10. Note the location context if visible (e.g., "north wall of garage", "under kitchen sink", "attic space above master bedroom").
11. Do NOT speculate about causes, age, severity ratings, or repair costs. Describe only what you SEE.
12. Do NOT skip any photo. Every embedded image must get a [PHOTO: ...] description.
13. If a photo is too dark, blurry, or unclear, write [PHOTO: image too dark/blurry to fully assess, but can see: (describe whatever IS visible)].
14. If multiple issues are visible in one photo, describe ALL of them.

INTEGRITY RULES:
15. Do NOT fabricate text that does not appear in the document.
16. Do NOT invent photo descriptions for photos that do not exist.
17. Keep photo descriptions factual and observational only - no opinions."""
        else:
            extraction_prompt = """Extract ALL text content from this document exactly as written. Be 100% faithful to what appears on the page. Preserve the structure, including section headers, checkboxes (mark checked boxes as [X] and unchecked as [ ]), form fields and their values, handwritten entries, and all printed text. Do not summarize, interpret, paraphrase, or add any text that does not appear in the document. Include every piece of text visible on every page. If text is unclear or illegible, write [illegible] rather than guessing."""
        
        _t0 = time.time()
        # Use streaming for large responses (max_tokens=32000 requires streaming)
        # client.messages.stream() yields chunks and returns a final Message on exit.
        _messages_payload = [{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_base64
                    }
                },
                {
                    "type": "text",
                    "text": extraction_prompt
                }
            ]
        }]
        with client.messages.stream(
            model="claude-sonnet-4-5-20250929",
            max_tokens=32000,
            messages=_messages_payload,
        ) as _stream:
            extracted_text_raw = _stream.get_final_text()
            response = _stream.get_final_message()
        try:
            try:
                from app import app as _ow_app, db as _ow_db
            except Exception:
                _ow_app, _ow_db = None, None
            from ai_cost_tracker import track_ai_call as _track
            _track(response, "pdf-ocr", (time.time() - _t0) * 1000, db=_ow_db, app=_ow_app)
        except Exception:
            pass
        
        extracted_text = extracted_text_raw.strip()
        
        if extracted_text and len(extracted_text) > 100:
            logger.info(f"Vision extraction succeeded: {len(extracted_text)} chars (type: {document_type or 'generic'})")
            return {
                'text': extracted_text,
                'method': 'anthropic_vision',
                'ocr_used': True,
                'document_type': document_type
            }
        else:
            logger.warning(f"Vision extraction returned insufficient text: {len(extracted_text)} chars")
            return None
            
    except Exception as e:
        # Classify the error type for better diagnostics
        err_str = str(e)
        status = getattr(e, 'status_code', None)
        if status in (429, 529) or 'overloaded' in err_str.lower() or 'rate' in err_str.lower():
            logger.warning(f"Vision extraction: transient API error ({status or 'unknown'}) — will retry on next request")
        elif 'streaming' in err_str.lower() or 'stream' in err_str.lower():
            logger.warning(f"Vision extraction: streaming error — {err_str[:100]}")
        else:
            logger.warning(f"Vision extraction failed: {err_str[:200]}")
        return None
