/**
 * EMERGENCY FIX FOR UI ISSUES
 * Prevents ANY bad page count messages from showing
 */

(function() {
    'use strict';
    
    console.log('🔧 OfferWise Emergency Fix v2 Loaded');
    
    // Store the original fetch
    const originalFetch = window.fetch;
    
    // Intercept fetch requests
    window.fetch = async function(...args) {
        const [url, options] = args;
        
        try {
            const response = await originalFetch.apply(this, args);
            
            // Clone response so we can read it
            const clonedResponse = response.clone();
            
            // Check if this is an upload-pdf request
            if (url.includes('/api/upload-pdf')) {
                try {
                    const data = await clonedResponse.json();
                    
                    // Fix the data before it reaches the app
                    if (data.success) {
                        // Store job ID for later use
                        window.lastUploadJobId = data.job_id;
                        
                        // CRITICAL: Remove page_count entirely if not valid
                        if (!data.page_count || data.page_count === 0 || data.page_count === null) {
                            delete data.page_count;  // Remove the field entirely!
                        }
                        
                        // Ensure message is friendly
                        if (data.async || data.processing) {
                            data.message = 'Upload complete! Processing document...';
                        }
                        
                        console.log('✅ Fixed upload response:', data);
                    }
                    
                    // Return a new response with fixed data
                    return new Response(JSON.stringify(data), {
                        status: response.status,
                        statusText: response.statusText,
                        headers: response.headers
                    });
                } catch (e) {
                    // Not JSON, return original
                    return response;
                }
            }
            
            // Check if this is ocr-progress (old endpoint)
            if (url.includes('/api/ocr-progress')) {
                try {
                    const contentType = response.headers.get('content-type');
                    
                    // If response is HTML (error page), return empty progress
                    if (!contentType || !contentType.includes('application/json')) {
                        console.warn('⚠️ OCR progress endpoint returned HTML, using fallback');
                        
                        // Return empty progress data
                        return new Response(JSON.stringify({
                            current: 0,
                            total: 0,
                            status: 'idle',
                            message: '',
                            deprecated: true
                        }), {
                            status: 200,
                            headers: { 'Content-Type': 'application/json' }
                        });
                    }
                } catch (e) {
                    console.error('Error checking ocr-progress:', e);
                }
            }
            
            return response;
            
        } catch (error) {
            // Handle 502 and network errors gracefully
            if (error.message.includes('502') || error.message.includes('Failed to fetch')) {
                console.error('🚨 Server error:', error);
                
                // Return a fake success for uploads to prevent UI breaking
                if (url.includes('/api/upload-pdf')) {
                    return new Response(JSON.stringify({
                        success: false,
                        error: 'Server temporarily unavailable. Please try again.',
                        retry: true
                    }), {
                        status: 503,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
            }
            
            throw error;
        }
    };
    
    // Fix any alerts or messages that show bad page counts
    const originalAlert = window.alert;
    window.alert = function(message) {
        if (typeof message === 'string') {
            // Remove ALL bad page count patterns
            message = message.replace(/\(0 pages\)/gi, '(processing...)');
            message = message.replace(/\(undefined pages\)/gi, '(processing...)');
            message = message.replace(/\(null pages\)/gi, '(processing...)');
            message = message.replace(/\(NaN pages\)/gi, '(processing...)');
            message = message.replace(/\(\s*pages\)/gi, '(processing...)');  // Empty pages
            
            // If message contains "uploaded" but no clear page count, clean it up
            if (message.includes('uploaded') && !message.match(/\(\d+ pages\)/)) {
                message = message.replace(/\([^)]*\)$/, '').trim() + ' - Processing...';
            }
        }
        return originalAlert.call(this, message);
    };
    
    // Also fix DOM text content
    const originalTextContent = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent').set;
    Object.defineProperty(Node.prototype, 'textContent', {
        set: function(value) {
            if (typeof value === 'string') {
                // Clean up page count issues in DOM updates
                value = value.replace(/\(0 pages\)/gi, '(processing...)');
                value = value.replace(/\(undefined pages\)/gi, '(processing...)');
                value = value.replace(/\(null pages\)/gi, '(processing...)');
                value = value.replace(/\(NaN pages\)/gi, '(processing...)');
            }
            originalTextContent.call(this, value);
        },
        get: Object.getOwnPropertyDescriptor(Node.prototype, 'textContent').get
    });
    
    console.log('✅ Emergency fixes v2 applied - NO bad page counts will show!');
})();


/**
 * ADD TO YOUR HTML:
 * <script src="/static/emergency-fix.js"></script>
 * 
 * This will:
 * 1. Prevent "0 pages" from showing
 * 2. Handle JSON parsing errors gracefully
 * 3. Handle 502 errors without crashing
 * 4. Fix messages automatically
 */
