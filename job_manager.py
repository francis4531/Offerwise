"""
Job Manager - Tracks async PDF processing jobs
"""
import uuid
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

class PDFJob:
    """Represents a PDF processing job"""
    
    def __init__(self, job_id: str, user_id: int, filename: str):
        self.job_id = job_id
        self.user_id = user_id
        self.filename = filename
        self.status = 'queued'  # queued, processing, complete, failed
        self.progress = 0
        self.total = 0
        self.message = 'Queued for processing'
        self.result = None
        self.error = None
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.pdf_bytes = None  # Store temporarily
    
    def to_dict(self):
        """Convert to dictionary for JSON response"""
        data = {
            'job_id': self.job_id,
            'status': self.status,
            'progress': self.progress,
            'total': self.total,
            'message': self.message,
            'created_at': self.created_at.isoformat()
        }
        
        if self.started_at:
            data['started_at'] = self.started_at.isoformat()
        
        if self.completed_at:
            data['completed_at'] = self.completed_at.isoformat()
            duration = (self.completed_at - self.created_at).total_seconds()
            data['duration_seconds'] = round(duration, 2)
        
        # Add result if complete
        if self.status == 'complete' and self.result:
            data['result'] = self.result
        
        # Add error if failed
        if self.status == 'failed' and self.error:
            data['error'] = self.error
        
        # Add ETA if processing
        if self.status == 'processing' and self.total > 0 and self.progress > 0:
            elapsed = (datetime.now() - self.started_at).total_seconds()
            rate = self.progress / elapsed if elapsed > 0 else 0
            remaining_pages = self.total - self.progress
            eta_seconds = remaining_pages / rate if rate > 0 else 0
            data['estimated_seconds_remaining'] = int(eta_seconds)
        
        return data


class JobManager:
    """Manages async PDF processing jobs"""
    
    def __init__(self):
        self.jobs: Dict[str, PDFJob] = {}
        self.lock = threading.Lock()
        logger.info("✅ JobManager initialized")
    
    def create_job(self, user_id: int, filename: str, pdf_bytes: bytes) -> str:
        """Create a new job and return job ID"""
        job_id = str(uuid.uuid4())
        job = PDFJob(job_id, user_id, filename)
        job.pdf_bytes = pdf_bytes  # Store for processing
        
        with self.lock:
            self.jobs[job_id] = job
        
        logger.info(f"📋 Created job {job_id} for user {user_id}: {filename}")
        return job_id
    
    def get_job(self, job_id: str) -> Optional[PDFJob]:
        """Get job by ID"""
        with self.lock:
            return self.jobs.get(job_id)
    
    def update_progress(self, job_id: str, current: int, total: int, message: str):
        """Update job progress"""
        with self.lock:
            if job_id in self.jobs:
                job = self.jobs[job_id]
                job.progress = current
                job.total = total
                job.message = message
                if job.status == 'queued':
                    job.status = 'processing'
                    job.started_at = datetime.now()
                    logger.info(f"▶️  Job {job_id} started processing")
    
    def complete_job(self, job_id: str, result: dict):
        """Mark job as complete"""
        with self.lock:
            if job_id in self.jobs:
                job = self.jobs[job_id]
                job.status = 'complete'
                job.result = result
                job.completed_at = datetime.now()
                job.message = 'Processing complete'
                # Clear PDF bytes to free memory
                job.pdf_bytes = None
                
                duration = (job.completed_at - job.created_at).total_seconds()
                logger.info(f"✅ Job {job_id} completed in {duration:.1f}s")
    
    def fail_job(self, job_id: str, error: str):
        """Mark job as failed"""
        with self.lock:
            if job_id in self.jobs:
                job = self.jobs[job_id]
                job.status = 'failed'
                job.error = error
                job.completed_at = datetime.now()
                job.message = f'Failed: {error}'
                # Clear PDF bytes to free memory
                job.pdf_bytes = None
                
                logger.error(f"❌ Job {job_id} failed: {error}")
    
    def cleanup_old_jobs(self, hours: int = 24):
        """Remove jobs older than X hours"""
        cutoff = datetime.now() - timedelta(hours=hours)
        with self.lock:
            old_jobs = [
                job_id for job_id, job in self.jobs.items()
                if job.completed_at and job.completed_at < cutoff
            ]
            for job_id in old_jobs:
                del self.jobs[job_id]
            
            if old_jobs:
                logger.info(f"🧹 Cleaned up {len(old_jobs)} old jobs")
    
    def get_job_count(self) -> dict:
        """Get count of jobs by status"""
        with self.lock:
            counts = {
                'queued': 0,
                'processing': 0,
                'complete': 0,
                'failed': 0,
                'total': len(self.jobs)
            }
            for job in self.jobs.values():
                if job.status in counts:
                    counts[job.status] += 1
            return counts


# Global job manager instance
job_manager = JobManager()
