"""
PDF Worker - Processes PDFs asynchronously in background threads
"""
import threading
import logging
import gc  # For memory management
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
