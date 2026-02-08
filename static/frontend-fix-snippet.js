/**
 * SIMPLE ONE-LINE FIX FOR YOUR FRONTEND
 * 
 * Find where you show the upload success message and replace it with this:
 */

// ============================================================================
// OPTION 1: If you're using alerts
// ============================================================================

// ❌ BAD - Shows "null pages", "0 pages", "undefined pages"
alert(`Disclosure uploaded (${response.page_count} pages)`);

// ✅ GOOD - Only shows page count when actually available
alert(response.page_count && response.page_count > 0 
    ? `Disclosure uploaded (${response.page_count} pages)`
    : 'Upload complete! Processing document...'
);


// ============================================================================
// OPTION 2: If you're setting DOM text
// ============================================================================

// ❌ BAD
element.textContent = `Uploaded (${data.page_count} pages)`;

// ✅ GOOD
element.textContent = data.page_count && data.page_count > 0
    ? `Uploaded (${data.page_count} pages)`
    : 'Processing...';


// ============================================================================
// OPTION 3: If you're using a showMessage function
// ============================================================================

// ❌ BAD
showMessage(`✓ Inspection uploaded (${response.page_count} pages)`);

// ✅ GOOD
if (response.page_count && response.page_count > 0) {
    showMessage(`✓ Inspection uploaded (${response.page_count} pages)`);
} else {
    showMessage('✓ Upload complete! Processing document...');
}


// ============================================================================
// OPTION 4: Universal fix (works everywhere)
// ============================================================================

// Add this helper function at the top of your file:
function getPageCountMessage(response) {
    if (response.page_count && response.page_count > 0) {
        return `(${response.page_count} pages)`;
    }
    return '(processing...)';
}

// Then use it everywhere:
alert(`Disclosure uploaded ${getPageCountMessage(response)}`);
element.textContent = `Uploaded ${getPageCountMessage(data)}`;
showMessage(`✓ Inspection uploaded ${getPageCountMessage(response)}`);


// ============================================================================
// THE PATTERN: ALWAYS CHECK IF PAGE COUNT IS VALID
// ============================================================================

// Rule: Only show page_count if it exists AND is > 0
// Check: response.page_count && response.page_count > 0
// This prevents: null, undefined, 0, NaN, false, etc.

// ✅ SAFE
response.page_count && response.page_count > 0 ? `(${response.page_count} pages)` : '(processing...)'

// ✅ ALSO SAFE
response.page_count > 0 ? `(${response.page_count} pages)` : '(processing...)'

// ✅ MOST EXPLICIT
(typeof response.page_count === 'number' && response.page_count > 0) ? `(${response.page_count} pages)` : '(processing...)'


// ============================================================================
// FIND & REPLACE IN YOUR CODE
// ============================================================================

// Search for these patterns in your code:
// 1. ${response.page_count}
// 2. ${data.page_count}
// 3. .page_count
// 4. "pages)"
// 5. alert(
// 6. showMessage(

// And fix each one to check if page_count is valid first!
