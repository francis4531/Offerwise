/**
 * Async PDF Upload Handler
 * Handles upload, job creation, and real-time progress polling
 */

class AsyncPDFUploader {
    constructor() {
        this.pollInterval = null;
        this.currentJobId = null;
    }

    /**
     * Upload PDF and start polling for results
     */
    async uploadPDF(file, onProgress, onComplete, onError) {
        try {
            // Convert file to base64
            const base64 = await this.fileToBase64(file);
            
            // Upload and create job
            console.log('📤 Uploading PDF...');
            const response = await fetch('/api/upload-pdf', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    pdf_base64: base64,
                    filename: file.name
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Upload failed');
            }

            const data = await response.json();
            this.currentJobId = data.job_id;
            
            console.log(`✅ Job created: ${this.currentJobId}`);
            
            // Start polling for status
            this.startPolling(onProgress, onComplete, onError);
            
        } catch (error) {
            console.error('❌ Upload error:', error);
            onError(error.message);
        }
    }

    /**
     * Start polling job status
     */
    startPolling(onProgress, onComplete, onError) {
        // Clear any existing poll
        this.stopPolling();
        
        // Poll every second
        this.pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/jobs/${this.currentJobId}`);
                
                if (!response.ok) {
                    throw new Error('Failed to get job status');
                }
                
                const job = await response.json();
                
                // Update progress
                if (job.status === 'processing' || job.status === 'queued') {
                    onProgress({
                        current: job.progress,
                        total: job.total,
                        message: job.message,
                        status: job.status,
                        eta: job.estimated_seconds_remaining
                    });
                }
                
                // Handle completion
                if (job.status === 'complete') {
                    this.stopPolling();
                    console.log('✅ Job complete!');
                    onComplete(job.result);
                }
                
                // Handle failure
                if (job.status === 'failed') {
                    this.stopPolling();
                    console.error('❌ Job failed:', job.error);
                    onError(job.error || 'Processing failed');
                }
                
            } catch (error) {
                console.error('❌ Polling error:', error);
                // Don't stop polling on temporary errors
            }
        }, 1000);
    }

    /**
     * Stop polling
     */
    stopPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
    }

    /**
     * Convert file to base64
     */
    fileToBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                // Remove data URL prefix (data:application/pdf;base64,)
                const base64 = reader.result.split(',')[1];
                resolve(base64);
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    /**
     * Cancel current job
     */
    cancel() {
        this.stopPolling();
        this.currentJobId = null;
    }
}

/**
 * Usage Example:
 * 
 * const uploader = new AsyncPDFUploader();
 * 
 * uploader.uploadPDF(
 *     file,
 *     // Progress callback
 *     (progress) => {
 *         console.log(`Progress: ${progress.current}/${progress.total}`);
 *         updateProgressBar(progress.current, progress.total);
 *         updateStatusMessage(progress.message);
 *         if (progress.eta) {
 *             updateETA(progress.eta);
 *         }
 *     },
 *     // Complete callback
 *     (result) => {
 *         console.log('Complete!', result);
 *         displayResults(result.text, result.pages);
 *     },
 *     // Error callback
 *     (error) => {
 *         console.error('Failed:', error);
 *         showError(error);
 *     }
 * );
 */

// Example UI update functions
function updateProgressBar(current, total) {
    const percent = total > 0 ? (current / total) * 100 : 0;
    const progressBar = document.querySelector('.progress-bar');
    const progressText = document.querySelector('.progress-text');
    
    if (progressBar) {
        progressBar.style.width = `${percent}%`;
    }
    
    if (progressText) {
        progressText.textContent = `${current}/${total} pages`;
    }
}

function updateStatusMessage(message) {
    const statusElement = document.querySelector('.status-message');
    if (statusElement) {
        statusElement.textContent = message;
    }
}

function updateETA(seconds) {
    const etaElement = document.querySelector('.eta-message');
    if (etaElement) {
        const minutes = Math.floor(seconds / 60);
        const secs = seconds % 60;
        if (minutes > 0) {
            etaElement.textContent = `About ${minutes}m ${secs}s remaining`;
        } else {
            etaElement.textContent = `About ${secs}s remaining`;
        }
    }
}

function showError(message) {
    const errorElement = document.querySelector('.error-message');
    if (errorElement) {
        errorElement.textContent = message;
        errorElement.style.display = 'block';
    }
}

function displayResults(text, pages) {
    console.log(`Received ${pages} pages, ${text.length} characters`);
    // Handle results based on your app's needs
}
