/**
 * SEAMLESS UPLOAD-TO-ANALYSIS FLOW
 * Handles the complete flow from upload → processing → analysis
 */

class OfferWiseAnalysisFlow {
    constructor() {
        this.inspectionJobId = null;
        this.disclosureJobId = null;
        this.pollIntervals = new Map();
    }

    /**
     * Upload document and track job
     * @param {File} file - The PDF file
     * @param {string} documentType - 'inspection' or 'disclosure'
     * @param {Function} onProgress - Progress callback
     * @param {Function} onComplete - Completion callback
     */
    async uploadDocument(file, documentType, onProgress, onComplete) {
        try {
            // Convert to base64
            const base64 = await this.fileToBase64(file);
            
            // Upload
            const response = await fetch('/api/upload-pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
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
            
            // Store job ID
            if (documentType === 'inspection') {
                this.inspectionJobId = data.job_id;
            } else {
                this.disclosureJobId = data.job_id;
            }
            
            console.log(`✅ ${documentType} upload started, job_id: ${data.job_id}`);
            
            // Start polling
            this.pollJobUntilComplete(data.job_id, documentType, onProgress, onComplete);
            
        } catch (error) {
            console.error('Upload error:', error);
            throw error;
        }
    }

    /**
     * Poll job until complete
     */
    pollJobUntilComplete(jobId, documentType, onProgress, onComplete) {
        const pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/jobs/${jobId}`);
                const job = await response.json();
                
                // Update progress
                if (job.status === 'processing' || job.status === 'queued') {
                    onProgress({
                        type: documentType,
                        current: job.progress,
                        total: job.total,
                        message: job.message,
                        eta: job.estimated_seconds_remaining
                    });
                }
                
                // Handle completion
                if (job.status === 'complete') {
                    clearInterval(pollInterval);
                    this.pollIntervals.delete(jobId);
                    onComplete({
                        type: documentType,
                        result: job.result
                    });
                }
                
                // Handle failure
                if (job.status === 'failed') {
                    clearInterval(pollInterval);
                    this.pollIntervals.delete(jobId);
                    throw new Error(job.error || 'Processing failed');
                }
                
            } catch (error) {
                console.error('Poll error:', error);
            }
        }, 1000);
        
        this.pollIntervals.set(jobId, pollInterval);
    }

    /**
     * Analyze property with automatic job handling
     * @param {Object} analysisData - Analysis parameters
     * @param {Function} onStillProcessing - Called if docs still processing
     * @param {Function} onComplete - Called when analysis complete
     */
    async analyzeProperty(analysisData, onStillProcessing, onComplete) {
        try {
            // Add job IDs to request if available
            const requestData = {
                ...analysisData,
                inspection_job_id: this.inspectionJobId,
                disclosure_job_id: this.disclosureJobId
            };
            
            // If we have job IDs, we need to specify which is which
            if (this.inspectionJobId) {
                requestData.job_id = this.inspectionJobId;
                requestData.document_type = 'inspection';
            }
            
            console.log('📊 Starting analysis...', requestData);
            
            const response = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestData)
            });
            
            // Handle 202 (still processing)
            if (response.status === 202) {
                const data = await response.json();
                console.log('⏳ Document still processing, retrying...', data);
                
                onStillProcessing(data);
                
                // Retry after specified delay
                const retryDelay = (data.retry_after || 2) * 1000;
                setTimeout(() => {
                    this.analyzeProperty(analysisData, onStillProcessing, onComplete);
                }, retryDelay);
                
                return;
            }
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Analysis failed');
            }
            
            const result = await response.json();
            console.log('✅ Analysis complete!', result);
            
            onComplete(result);
            
        } catch (error) {
            console.error('❌ Analysis error:', error);
            throw error;
        }
    }

    /**
     * Check if both documents are ready for analysis
     */
    areDocumentsReady() {
        // For now, if we have job IDs, assume they're processing
        // You can enhance this to check actual job status
        return this.inspectionJobId !== null || this.disclosureJobId !== null;
    }

    /**
     * Clear job IDs (for new analysis)
     */
    reset() {
        this.inspectionJobId = null;
        this.disclosureJobId = null;
        
        // Clear all polling intervals
        this.pollIntervals.forEach(interval => clearInterval(interval));
        this.pollIntervals.clear();
    }

    /**
     * Convert file to base64
     */
    fileToBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const base64 = reader.result.split(',')[1];
                resolve(base64);
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }
}

/**
 * USAGE EXAMPLE:
 */

// Initialize flow manager
const analysisFlow = new OfferWiseAnalysisFlow();

// Upload inspection report
const inspectionFile = document.querySelector('#inspection-upload').files[0];
analysisFlow.uploadDocument(
    inspectionFile,
    'inspection',
    // Progress callback
    (progress) => {
        console.log(`Inspection: ${progress.current}/${progress.total} pages`);
        updateInspectionProgress(progress.current, progress.total, progress.message);
    },
    // Complete callback
    (data) => {
        console.log('Inspection processing complete!');
        showInspectionReady();
        enableAnalyzeButton();  // Enable analyze when ready
    }
);

// Upload disclosure
const disclosureFile = document.querySelector('#disclosure-upload').files[0];
analysisFlow.uploadDocument(
    disclosureFile,
    'disclosure',
    (progress) => {
        console.log(`Disclosure: ${progress.current}/${progress.total} pages`);
        updateDisclosureProgress(progress.current, progress.total, progress.message);
    },
    (data) => {
        console.log('Disclosure processing complete!');
        showDisclosureReady();
        enableAnalyzeButton();  // Enable analyze when ready
    }
);

// When user clicks "Analyze"
document.querySelector('#analyze-button').addEventListener('click', () => {
    analysisFlow.analyzeProperty(
        {
            property_address: '123 Main St',
            property_price: 925000,
            buyer_profile: {
                max_budget: 1000000,
                risk_tolerance: 'medium'
            }
        },
        // Still processing callback
        (data) => {
            showMessage(`Still processing... ${data.message}`);
        },
        // Complete callback
        (result) => {
            showAnalysisResults(result);
        }
    );
});

/**
 * EXAMPLE UI UPDATE FUNCTIONS:
 */

function updateInspectionProgress(current, total, message) {
    const progressBar = document.querySelector('#inspection-progress');
    const statusText = document.querySelector('#inspection-status');
    
    if (progressBar && total > 0) {
        progressBar.value = current;
        progressBar.max = total;
    }
    
    if (statusText) {
        statusText.textContent = `${current}/${total} pages - ${message}`;
    }
}

function showInspectionReady() {
    const statusText = document.querySelector('#inspection-status');
    if (statusText) {
        statusText.textContent = '✓ Inspection report ready';
        statusText.classList.add('success');
    }
}

function enableAnalyzeButton() {
    const button = document.querySelector('#analyze-button');
    if (button && analysisFlow.areDocumentsReady()) {
        button.disabled = false;
        button.textContent = 'Analyze Property';
    }
}

function showMessage(message) {
    console.log(message);
    // TODO: Show in your UI
    // Example: toast notification, status bar, etc.
}

function showAnalysisResults(result) {
    console.log('Analysis results:', result);
    // TODO: Redirect to results page or display in modal
    // Example: window.location.href = `/analysis/${result.analysis_id}`;
}
