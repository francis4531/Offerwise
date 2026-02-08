/**
 * ASYNC UPLOAD COMPATIBILITY HANDLER
 * Drop-in replacement for handleUploadResponse()
 * Automatically detects async vs sync responses and handles appropriately
 */

/**
 * Handle upload response - works with both sync and async modes
 */
function handleUploadResponse(response) {
    // Check if this is an async response (has job_id)
    if (response.job_id || response.async) {
        // ASYNC MODE - Start polling
        console.log('📤 Async upload detected, job_id:', response.job_id);
        
        // Show better message
        showUploadMessage('Upload complete! Processing document...', 'info');
        
        // Start polling for results
        startPollingJob(response.job_id);
        
    } else {
        // SYNC MODE (old behavior) - Results already available
        console.log('📄 Sync upload, pages:', response.page_count);
        
        // Show success with page count
        const pages = response.page_count || 'unknown';
        showUploadMessage(`Inspection uploaded (${pages} pages)`, 'success');
        
        // Process results immediately
        processUploadedDocument(response);
    }
}

/**
 * Start polling for job completion
 */
function startPollingJob(jobId) {
    let pollCount = 0;
    const maxPolls = 120; // Max 2 minutes of polling
    
    const pollInterval = setInterval(async () => {
        pollCount++;
        
        try {
            const response = await fetch(`/api/jobs/${jobId}`);
            
            if (!response.ok) {
                throw new Error('Failed to get job status');
            }
            
            const job = await response.json();
            
            // Update progress message
            if (job.status === 'processing') {
                const progress = job.total > 0 
                    ? ` (${job.progress}/${job.total} pages)`
                    : '';
                showUploadMessage(`Processing${progress}...`, 'info');
            }
            
            // Handle completion
            if (job.status === 'complete') {
                clearInterval(pollInterval);
                
                // Show success with actual page count
                const pages = job.result?.pages || 'unknown';
                showUploadMessage(`Document processed (${pages} pages)`, 'success');
                
                // Process results
                processUploadedDocument(job.result);
            }
            
            // Handle failure
            if (job.status === 'failed') {
                clearInterval(pollInterval);
                showUploadMessage(`Upload failed: ${job.error}`, 'error');
            }
            
            // Safety: Stop after max polls
            if (pollCount >= maxPolls) {
                clearInterval(pollInterval);
                showUploadMessage('Processing is taking longer than expected. Please refresh the page.', 'warning');
            }
            
        } catch (error) {
            console.error('Polling error:', error);
            // Don't stop polling on temporary errors
        }
    }, 1000);
}

/**
 * Show upload message (replace with your actual UI function)
 */
function showUploadMessage(message, type = 'info') {
    console.log(`[${type.toUpperCase()}] ${message}`);
    
    // Example: Show browser alert (replace with your UI)
    if (typeof alert !== 'undefined' && type === 'success') {
        alert(message);
    }
    
    // TODO: Replace with your actual UI notification system
    // Example:
    // if (window.showToast) {
    //     window.showToast(message, type);
    // }
}

/**
 * Process uploaded document (replace with your actual processing)
 */
function processUploadedDocument(result) {
    console.log('Processing document:', result);
    
    // TODO: Replace with your actual document processing
    // Example:
    // if (result.text) {
    //     displayExtractedText(result.text);
    // }
    // if (result.pages) {
    //     updatePageCount(result.pages);
    // }
}

/**
 * USAGE:
 * 
 * // In your upload handler, replace:
 * // OLD CODE:
 * // const response = await uploadPDF(file);
 * // alert(`Inspection uploaded (${response.page_count} pages)`);
 * 
 * // NEW CODE:
 * const response = await uploadPDF(file);
 * handleUploadResponse(response);  // ← This handles both sync and async!
 */
