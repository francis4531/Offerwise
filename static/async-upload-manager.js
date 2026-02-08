/**
 * OfferWise Async Upload Handler - COMPLETE DROP-IN REPLACEMENT
 * 
 * This replaces your current upload code with async-ready version
 * Copy this entire file and include it in your HTML
 */

class OfferWiseUploadManager {
    constructor() {
        this.jobs = new Map(); // Track active jobs
        this.pollIntervals = new Map(); // Track polling intervals
    }

    /**
     * Main upload function - REPLACES your existing uploadPDF()
     * 
     * @param {File} file - The PDF file to upload
     * @param {string} documentType - 'inspection' or 'disclosure'
     * @param {Object} callbacks - { onStart, onProgress, onComplete, onError }
     */
    async uploadDocument(file, documentType, callbacks = {}) {
        const {
            onStart = () => {},
            onProgress = () => {},
            onComplete = () => {},
            onError = (err) => console.error(err)
        } = callbacks;

        try {
            // Notify start
            onStart({ type: documentType, filename: file.name });

            // Convert file to base64
            const base64 = await this.fileToBase64(file);

            // Upload to server
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
                throw new Error(error.error || `Upload failed (${response.status})`);
            }

            const data = await response.json();

            // Check if async mode (new backend)
            if (data.job_id) {
                console.log(`✅ Async upload started: ${data.job_id}`);
                
                // Store job info
                this.jobs.set(documentType, {
                    job_id: data.job_id,
                    filename: file.name,
                    status: 'processing'
                });

                // Start polling
                this.startPolling(data.job_id, documentType, onProgress, onComplete, onError);

            } else {
                // Old sync mode (backward compatibility)
                console.log('✅ Sync upload complete');
                onComplete({
                    type: documentType,
                    result: {
                        text: data.text,
                        pages: data.page_count,
                        filename: file.name
                    }
                });
            }

        } catch (error) {
            console.error('❌ Upload error:', error);
            onError({
                type: documentType,
                message: error.message,
                error: error
            });
        }
    }

    /**
     * Start polling job status
     */
    startPolling(jobId, documentType, onProgress, onComplete, onError) {
        // Clear any existing interval
        if (this.pollIntervals.has(jobId)) {
            clearInterval(this.pollIntervals.get(jobId));
        }

        let errorCount = 0;
        const maxErrors = 3;

        const interval = setInterval(async () => {
            try {
                const response = await fetch(`/api/jobs/${jobId}`);

                if (!response.ok) {
                    errorCount++;
                    if (errorCount >= maxErrors) {
                        throw new Error(`Failed to get job status (${response.status})`);
                    }
                    return; // Retry on next interval
                }

                const job = await response.json();

                // Reset error count on success
                errorCount = 0;

                // Update progress
                if (job.status === 'processing' || job.status === 'queued') {
                    onProgress({
                        type: documentType,
                        current: job.progress || 0,
                        total: job.total || 0,
                        message: job.message || 'Processing...',
                        eta: job.estimated_seconds_remaining
                    });
                }

                // Handle completion
                if (job.status === 'complete') {
                    clearInterval(interval);
                    this.pollIntervals.delete(jobId);
                    
                    console.log(`✅ Job complete: ${jobId}`);
                    
                    onComplete({
                        type: documentType,
                        result: job.result,
                        job_id: jobId
                    });
                }

                // Handle failure
                if (job.status === 'failed') {
                    clearInterval(interval);
                    this.pollIntervals.delete(jobId);
                    
                    console.error(`❌ Job failed: ${jobId}`);
                    
                    onError({
                        type: documentType,
                        message: job.error || 'Processing failed',
                        job_id: jobId
                    });
                }

            } catch (error) {
                errorCount++;
                console.error('Polling error:', error);
                
                if (errorCount >= maxErrors) {
                    clearInterval(interval);
                    this.pollIntervals.delete(jobId);
                    onError({
                        type: documentType,
                        message: 'Failed to check processing status',
                        error: error
                    });
                }
            }
        }, 1000); // Poll every second

        this.pollIntervals.set(jobId, interval);
    }

    /**
     * Get job ID for a document type
     */
    getJobId(documentType) {
        const job = this.jobs.get(documentType);
        return job ? job.job_id : null;
    }

    /**
     * Cancel all active jobs
     */
    cancelAll() {
        this.pollIntervals.forEach(interval => clearInterval(interval));
        this.pollIntervals.clear();
        this.jobs.clear();
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
            reader.onerror = () => reject(new Error('Failed to read file'));
            reader.readAsDataURL(file);
        });
    }
}

// ============================================================================
// GLOBAL INSTANCE - Use this throughout your app
// ============================================================================

const uploadManager = new OfferWiseUploadManager();

// ============================================================================
// SIMPLE USAGE FUNCTIONS - Drop-in replacements for your existing code
// ============================================================================

/**
 * Upload Inspection Report
 * REPLACES: Your existing uploadInspection() function
 */
async function uploadInspection(file) {
    await uploadManager.uploadDocument(file, 'inspection', {
        onStart: (data) => {
            console.log('Starting inspection upload:', data.filename);
            showMessage('Uploading inspection report...', 'info');
        },
        
        onProgress: (data) => {
            console.log(`Inspection progress: ${data.current}/${data.total}`);
            updateProgressBar('inspection', data.current, data.total, data.message);
        },
        
        onComplete: (data) => {
            console.log('Inspection upload complete:', data.result);
            const pages = data.result.pages || 'unknown';
            showMessage(`✓ Inspection report processed (${pages} pages)`, 'success');
            enableAnalyzeButton();
        },
        
        onError: (data) => {
            console.error('Inspection upload failed:', data.message);
            showMessage(`✗ Upload failed: ${data.message}`, 'error');
        }
    });
}

/**
 * Upload Disclosure
 * REPLACES: Your existing uploadDisclosure() function
 */
async function uploadDisclosure(file) {
    await uploadManager.uploadDocument(file, 'disclosure', {
        onStart: (data) => {
            console.log('Starting disclosure upload:', data.filename);
            showMessage('Uploading disclosure...', 'info');
        },
        
        onProgress: (data) => {
            console.log(`Disclosure progress: ${data.current}/${data.total}`);
            updateProgressBar('disclosure', data.current, data.total, data.message);
        },
        
        onComplete: (data) => {
            console.log('Disclosure upload complete:', data.result);
            const pages = data.result.pages || 'unknown';
            showMessage(`✓ Disclosure processed (${pages} pages)`, 'success');
            enableAnalyzeButton();
        },
        
        onError: (data) => {
            console.error('Disclosure upload failed:', data.message);
            showMessage(`✗ Upload failed: ${data.message}`, 'error');
        }
    });
}

// ============================================================================
// UI UPDATE FUNCTIONS - Customize these for your app
// ============================================================================

/**
 * Show message to user
 * CUSTOMIZE THIS to match your UI
 */
function showMessage(message, type = 'info') {
    console.log(`[${type.toUpperCase()}] ${message}`);
    
    // Update status div
    const statusEl = document.getElementById('upload-status');
    if (statusEl) {
        statusEl.textContent = message;
        statusEl.style.display = 'block';
        
        // Color based on type
        if (type === 'error') {
            statusEl.style.background = '#ffebee';
            statusEl.style.color = '#c62828';
            statusEl.style.border = '1px solid #ef5350';
        } else if (type === 'success') {
            statusEl.style.background = '#e8f5e9';
            statusEl.style.color = '#388e3c';
            statusEl.style.border = '1px solid #66bb6a';
        } else {
            statusEl.style.background = '#e3f2fd';
            statusEl.style.color = '#1976d2';
            statusEl.style.border = '1px solid #42a5f5';
        }
        
        // Auto-hide success messages after 5 seconds
        if (type === 'success' || type === 'info') {
            setTimeout(() => {
                statusEl.style.display = 'none';
            }, 5000);
        }
    }
    
    // Also show browser alert for errors (fallback)
    if (type === 'error') {
        alert(message);
    }
}

/**
 * Update progress bar
 * CUSTOMIZE THIS to match your UI
 */
function updateProgressBar(documentType, current, total, message) {
    // Show progress container
    const progressContainer = document.getElementById(`${documentType}-progress`);
    if (progressContainer) {
        progressContainer.style.display = 'block';
    }
    
    // Update progress bar
    const progressBar = document.getElementById(`${documentType}-progress-bar`);
    if (progressBar && total > 0) {
        const percent = (current / total) * 100;
        progressBar.style.width = `${percent}%`;
        progressBar.textContent = `${Math.round(percent)}%`;
    }
    
    // Update progress text
    const progressText = document.getElementById(`${documentType}-progress-text`);
    if (progressText) {
        progressText.textContent = total > 0 
            ? `${current}/${total} pages - ${message}`
            : message;
    }
}

/**
 * Enable analyze button when both documents ready
 */
function enableAnalyzeButton() {
    const analyzeBtn = document.getElementById('analyze-button');
    if (analyzeBtn) {
        analyzeBtn.disabled = false;
        analyzeBtn.textContent = 'Analyze Property';
    }
}

// ============================================================================
// FILE INPUT HANDLERS - Wire these up to your file inputs
// ============================================================================

/**
 * Handle inspection file input
 * ADD THIS to your inspection file input
 */
function handleInspectionUpload(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    // Validate file
    if (!file.type.includes('pdf')) {
        showMessage('Please upload a PDF file', 'error');
        return;
    }
    
    if (file.size > 15 * 1024 * 1024) {
        showMessage('File too large (max 15MB)', 'error');
        return;
    }
    
    // Upload
    uploadInspection(file);
}

/**
 * Handle disclosure file input
 * ADD THIS to your disclosure file input
 */
function handleDisclosureUpload(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    // Validate file
    if (!file.type.includes('pdf')) {
        showMessage('Please upload a PDF file', 'error');
        return;
    }
    
    if (file.size > 15 * 1024 * 1024) {
        showMessage('File too large (max 15MB)', 'error');
        return;
    }
    
    // Upload
    uploadDisclosure(file);
}

// ============================================================================
// AUTO-WIRE - Automatically connect to file inputs (optional)
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Wire up inspection upload
    const inspectionInput = document.getElementById('inspection-upload');
    if (inspectionInput) {
        inspectionInput.addEventListener('change', handleInspectionUpload);
        console.log('✅ Inspection upload handler wired');
    }
    
    // Wire up disclosure upload
    const disclosureInput = document.getElementById('disclosure-upload');
    if (disclosureInput) {
        disclosureInput.addEventListener('change', handleDisclosureUpload);
        console.log('✅ Disclosure upload handler wired');
    }
});

// ============================================================================
// CLEANUP - Call on page unload
// ============================================================================

window.addEventListener('beforeunload', () => {
    uploadManager.cancelAll();
});

console.log('✅ OfferWise Async Upload Manager loaded');
