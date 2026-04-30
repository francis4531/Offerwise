"""
PDF Worker - Processes PDFs asynchronously in background threads
"""
import threading
import logging
import gc  # For memory management
import base64
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)


class PDFWorker:
    """Processes PDF jobs asynchronously"""
    
    def __init__(self, job_manager, pdf_handler, max_workers=10):
        self.job_manager = job_manager
        self.pdf_handler = pdf_handler
        # Use thread pool to limit concurrent OCR operations
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pdf_worker")
        logger.info(f"✅ PDFWorker initialized with {max_workers} max workers")
    
    def process_pdf_async(self, job_id: str):
        """Queue PDF for async processing"""
        logger.info(f"📤 Queuing job {job_id} for processing")
        self.executor.submit(self._process_job, job_id)
    
    def _process_job(self, job_id: str):
        """Process a PDF job (runs in background thread)"""
        import time
        from datetime import datetime, timedelta
        
        start_time = datetime.now()
        timeout_seconds = 600  # 10 minutes
        
        try:
            # Get job
            job = self.job_manager.get_job(job_id)
            if not job:
                logger.error(f"❌ Job {job_id} not found")
                return
            
            if not job.pdf_bytes:
                self.job_manager.fail_job(job_id, "PDF data missing")
                return
            
            logger.info(f"🔄 Starting processing for job {job_id}: {job.filename}")
            
            # Create progress callback with timeout check
            def progress_callback(current, total, message):
                # Check for timeout
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > timeout_seconds:
                    logger.error(f"⏰ Job {job_id} timeout after {elapsed:.0f}s at page {current}/{total}")
                    raise TimeoutError(f"Processing timeout after {elapsed:.0f} seconds")
                
                self.job_manager.update_progress(job_id, current, total, message)
                logger.info(f"📊 Job {job_id}: {current}/{total} - {message} (elapsed: {elapsed:.0f}s)")
            
            # Extract text from PDF
            extraction_result = self.pdf_handler.extract_text_from_bytes(
                job.pdf_bytes,
                progress_callback=progress_callback
            )
            
            elapsed = (datetime.now() - start_time).total_seconds()
            
            # Build result
            result = {
                'text': extraction_result['text'],
                'filename': job.filename,
                'pages': extraction_result.get('page_count', extraction_result.get('pages', 0)),
                'page_count': extraction_result.get('page_count', extraction_result.get('pages', 0)),
                'chars': len(extraction_result['text']),
                'method': extraction_result.get('method', 'unknown'),
                'ocr_used': extraction_result.get('ocr_used', False)
            }
            
            # QUALITY CHECK: Verify extracted text is meaningful
            # DocuSign and image-based PDFs often extract only metadata
            from pdf_handler import is_meaningful_extraction, extract_text_via_vision, is_tds_complete
            page_count = result.get('page_count', 1)
            is_meaningful, reason = is_meaningful_extraction(result['text'], page_count)
            
            # Detect document type from filename and extracted text
            doc_type = None
            fname_lower = (job.filename or '').lower()
            text_lower = result['text'].lower() if result['text'] else ''
            if any(kw in fname_lower for kw in ['disclosure', 'tds', 'seller', 'transfer']) or \
               any(kw in text_lower for kw in ['transfer disclosure', 'seller discloses', 'tds revised', 'civil code']):
                doc_type = 'seller_disclosure'
            elif any(kw in fname_lower for kw in ['inspection', 'inspector', 'report']) or \
                 any(kw in text_lower for kw in ['inspection report', 'inspector', 'internachi', 'ashi']):
                doc_type = 'inspection_report'
            logger.info(f"📄 Job {job_id}: Detected document type: {doc_type or 'generic'}")
            
            # For TDS documents: check completeness even if text seems meaningful
            # Text extractors often get printed form labels but miss handwritten answers
            needs_vision = not is_meaningful
            
            if is_meaningful and doc_type == 'seller_disclosure':
                tds_complete, tds_score, tds_missing = is_tds_complete(result['text'])
                logger.info(f"📋 Job {job_id}: TDS completeness: {tds_score:.2f}, missing: {tds_missing}")
                if not tds_complete or tds_score < 0.5:
                    logger.warning(f"⚠️ Job {job_id}: TDS extraction incomplete (score: {tds_score:.2f}), upgrading to vision")
                    needs_vision = True
            
            if needs_vision:
                if not is_meaningful:
                    logger.warning(f"⚠️ Job {job_id}: Text extraction not meaningful (reason: {reason}, {result['chars']} chars from {page_count} pages)")
                logger.info(f"🔄 Job {job_id}: Using Anthropic vision for accurate extraction...")
                
                # Encode PDF bytes as base64 for vision API
                pdf_b64 = base64.b64encode(job.pdf_bytes).decode('utf-8')
                vision_result = extract_text_via_vision(pdf_b64, document_type=doc_type)
                
                if vision_result and vision_result.get('text'):
                    old_chars = result['chars']
                    result['text'] = vision_result['text']
                    result['chars'] = len(vision_result['text'])
                    result['method'] = 'anthropic_vision'
                    result['ocr_used'] = True
                    result['vision_fallback'] = True
                    logger.info(f"✅ Job {job_id}: Vision fallback succeeded! {old_chars} → {result['chars']} chars")
                else:
                    logger.warning(f"⚠️ Job {job_id}: Vision fallback also failed, using original extraction")
            
            logger.info(f"✅ Job {job_id} complete: {result['pages']} pages, {result['chars']} chars, method={result['method']} in {elapsed:.0f}s")
            
            # Mark as complete
            self.job_manager.complete_job(job_id, result)
            
            # Force garbage collection to free memory immediately
            gc.collect()
            logger.info(f"🧹 Memory cleanup performed after job {job_id}")
            
        except TimeoutError as e:
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.error(f"⏰ Job {job_id} timed out after {elapsed:.0f}s")
            self.job_manager.fail_job(job_id, f"Processing timeout - job took longer than {timeout_seconds}s ({elapsed:.0f}s elapsed)")
            
        except Exception as e:
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.error(f"❌ PDF processing failed for job {job_id} after {elapsed:.0f}s: {e}", exc_info=True)
            self.job_manager.fail_job(job_id, str(e))
    
    def get_stats(self):
        """Get worker statistics"""
        return {
            'max_workers': self.executor._max_workers,
            'active_threads': threading.active_count(),
            'jobs': self.job_manager.get_job_count()
        }


# Create global worker instance (will be initialized in app.py after pdf_handler is created)
pdf_worker = None

def initialize_worker(job_manager, pdf_handler, max_workers=10):
    """Initialize the global PDF worker"""
    global pdf_worker
    pdf_worker = PDFWorker(job_manager, pdf_handler, max_workers)
    logger.info("✅ Global PDFWorker initialized")
    return pdf_worker
