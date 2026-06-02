/**
 * Property Comparison Frontend
 * Handles the entire comparison flow with animations
 */

let currentUser = null;
let comparisonResult = null;

// Load user data on page load
document.addEventListener('DOMContentLoaded', async function() {
    await loadUserData();
    setupURLValidation();
});

// Load user credits
async function loadUserData() {
    try {
        const response = await fetch('/api/user', { credentials: 'include' });
        if (!response.ok) {
            window.location.href = '/login';
            return;
        }
        
        const data = await response.json();
        currentUser = data;
        
        // Update credits display
        document.getElementById('credits-count').textContent = data.credits || 0;
        
        // Enable/disable compare button based on credits
        const compareBtn = document.getElementById('compare-btn');
        if (data.credits < 1) {
            compareBtn.disabled = true;
            compareBtn.textContent = '❌ INSUFFICIENT CREDITS - BUY MORE';
        }
    } catch (error) {
        console.error('Error loading user data:', error);
        showError('Failed to load user data. Please refresh the page.');
    }
}

// Setup URL validation with live preview
function setupURLValidation() {
    for (let i = 1; i <= 3; i++) {
        const urlInput = document.getElementById(`property${i}-url`);
        const preview = document.getElementById(`property${i}-preview`);
        const inputContainer = document.getElementById(`property${i}-input`);
        
        urlInput.addEventListener('input', function() {
            const url = this.value.trim();
            
            if (!url) {
                preview.classList.remove('show');
                inputContainer.classList.remove('valid');
                return;
            }
            
            // Try to extract address from URL
            const address = extractAddressFromURL(url);
            
            if (address) {
                preview.textContent = `✅ Detected: ${address}`;
                preview.classList.add('show');
                inputContainer.classList.add('valid');
            } else if (url.length > 10) {
                preview.textContent = `⚠️ URL format not recognized - will still work`;
                preview.classList.add('show');
                inputContainer.classList.remove('valid');
            }
        });
    }
}

// Extract address from Zillow/Redfin URL
function extractAddressFromURL(url) {
    if (!url) return null;
    
    // Zillow pattern: /homedetails/123-Main-St-City-ST-12345/123456_zpid/
    const zillowMatch = url.match(/\/homedetails\/([^\/]+)\/\d+_zpid/);
    if (zillowMatch) {
        return zillowMatch[1].replace(/-/g, ' ');
    }
    
    // Redfin pattern: /ST/City/123-Main-St-12345/home/123456
    const redfinMatch = url.match(/\/([^\/]+)\/home\/\d+$/);
    if (redfinMatch) {
        return redfinMatch[1].replace(/-/g, ' ');
    }
    
    return null;
}

// Start comparison
async function startComparison() {
    // Get inputs
    const property1Url = document.getElementById('property1-url').value.trim();
    const property2Url = document.getElementById('property2-url').value.trim();
    const property3Url = document.getElementById('property3-url').value.trim();
    
    const property1Price = document.getElementById('property1-price').value;
    const property2Price = document.getElementById('property2-price').value;
    const property3Price = document.getElementById('property3-price').value;
    
    // Validate
    if (!property1Url || !property2Url) {
        showError('Please enter at least 2 property URLs');
        return;
    }
    
    // Hide input section, show loading
    document.getElementById('input-section').style.display = 'none';
    document.getElementById('loading-section').classList.add('show');
    document.getElementById('error-message').classList.remove('show');
    
    // Animate progress bars
    animateProgress();
    
    try {
        // Call API
        const response = await fetch('/api/compare', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({
                property1_url: property1Url,
                property2_url: property2Url,
                property3_url: property3Url || null,
                property1_price: property1Price ? parseInt(property1Price) : null,
                property2_price: property2Price ? parseInt(property2Price) : null,
                property3_price: property3Price ? parseInt(property3Price) : null
            })
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || 'Comparison failed');
        }
        
        // Update credits
        document.getElementById('credits-count').textContent = data.credits_remaining;
        
        // Store result
        comparisonResult = data.result;
        
        // Wait for animations to finish
        await sleep(1500);
        
        // Hide loading, show results
        document.getElementById('loading-section').classList.remove('show');
        displayResults(data.result);
        
    } catch (error) {
        console.error('Comparison error:', error);
        document.getElementById('loading-section').classList.remove('show');
        document.getElementById('input-section').style.display = 'block';
        showError(error.message);
    }
}

// Animate progress bars
function animateProgress() {
    const bars = [
        { bar: 'progress1-bar', percent: 'progress1-percent', duration: 1200 },
        { bar: 'progress2-bar', percent: 'progress2-percent', duration: 1400 },
        { bar: 'progress3-bar', percent: 'progress3-percent', duration: 1300 }
    ];
    
    bars.forEach((item, index) => {
        setTimeout(() => {
            const barElement = document.getElementById(item.bar);
            const percentElement = document.getElementById(item.percent);
            
            let progress = 0;
            const interval = setInterval(() => {
                progress += 5;
                if (progress >= 100) {
                    progress = 100;
                    clearInterval(interval);
                }
                barElement.style.width = progress + '%';
                percentElement.textContent = progress + '%';
            }, item.duration / 20);
        }, index * 200);
    });
}

// Display results
function displayResults(result) {
    // Update winner card
    const winnerProp = result[`property${result.winner_property_num}`];
    
    document.getElementById('winner-address').textContent = winnerProp.address;
    document.getElementById('winner-grade').textContent = `GRADE ${winnerProp.grade}`;
    document.getElementById('winner-grade').className = `winner-grade grade-${winnerProp.grade.toLowerCase().replace('+', '')}`;
    document.getElementById('winner-score').textContent = `Score: ${winnerProp.offer_score}/100`;
    document.getElementById('winner-reason-text').textContent = result.winner_reason;
    
    // Winner metrics
    const metricsHTML = `
        <div class="metric">
            <div class="metric-label">Listing Price</div>
            <div class="metric-value">$${winnerProp.listing_price.toLocaleString()}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Repairs</div>
            <div class="metric-value">$${winnerProp.estimated_repair_cost_low.toLocaleString()}-$${winnerProp.estimated_repair_cost_high.toLocaleString()}</div>
        </div>
        <div class="metric">
            <div class="metric-label">True Cost</div>
            <div class="metric-value">$${winnerProp.true_cost.toLocaleString()}</div>
        </div>
        <div class="metric">
            <div class="metric-label">5-Year Value</div>
            <div class="metric-value">$${winnerProp.estimated_5yr_value.toLocaleString()}</div>
        </div>
        <div class="metric">
            <div class="metric-label">ROI</div>
            <div class="metric-value">${winnerProp.estimated_roi_percent > 0 ? '+' : ''}${winnerProp.estimated_roi_percent.toFixed(1)}%</div>
        </div>
        <div class="metric">
            <div class="metric-label">Critical Issues</div>
            <div class="metric-value">${winnerProp.critical_issues_count}</div>
        </div>
    `;
    document.getElementById('winner-metrics').innerHTML = metricsHTML;
    
    // Comparison grid - show all properties
    const gridHTML = result.rankings.map(ranking => {
        const prop = result[`property${ranking.property_num}`];
        const medals = ['🥇', '🥈', '🥉'];
        const gradeClass = prop.grade.toLowerCase().replace('+', '');
        
        return `
            <div class="property-card">
                <div class="rank-badge">${medals[ranking.rank - 1]}</div>
                <div class="card-address">${prop.address}</div>
                <div class="card-grade-score">
                    <span class="card-grade grade-${gradeClass}">${prop.grade}</span>
                    <span class="card-score">${prop.offer_score}/100</span>
                </div>
                <div class="card-metrics">
                    <div class="card-metric">
                        <span class="card-metric-label">Listing Price</span>
                        <span class="card-metric-value">$${prop.listing_price.toLocaleString()}</span>
                    </div>
                    <div class="card-metric">
                        <span class="card-metric-label">Repairs</span>
                        <span class="card-metric-value">$${prop.estimated_repair_cost_low.toLocaleString()}-$${prop.estimated_repair_cost_high.toLocaleString()}</span>
                    </div>
                    <div class="card-metric">
                        <span class="card-metric-label">True Cost</span>
                        <span class="card-metric-value">$${prop.true_cost.toLocaleString()}</span>
                    </div>
                    <div class="card-metric">
                        <span class="card-metric-label">5-Year Value</span>
                        <span class="card-metric-value">$${prop.estimated_5yr_value.toLocaleString()}</span>
                    </div>
                    <div class="card-metric">
                        <span class="card-metric-label">ROI</span>
                        <span class="card-metric-value">${prop.estimated_roi_percent > 0 ? '+' : ''}${prop.estimated_roi_percent.toFixed(1)}%</span>
                    </div>
                    <div class="card-metric">
                        <span class="card-metric-label">Critical Issues</span>
                        <span class="card-metric-value">${prop.critical_issues_count}</span>
                    </div>
                </div>
                <button class="btn btn-primary" style="width: 100%;" onclick="analyzeProperty('${prop.address}', '${prop.listing_url}')">
                    🔍 Analyze Deeper
                </button>
            </div>
        `;
    }).join('');
    
    document.getElementById('comparison-grid').innerHTML = gridHTML;
    
    // Show results section
    document.getElementById('results-section').classList.add('show');
}

// Analyze winner (deep analysis)
function analyzeWinner() {
    if (!comparisonResult) return;
    
    const winnerProp = comparisonResult[`property${comparisonResult.winner_property_num}`];
    // Redirect to upload page where user can upload disclosure/inspection documents
    window.location.href = '/app';
}

// Analyze specific property
function analyzeProperty(address, url) {
    // Redirect to upload page where user can upload disclosure/inspection documents
    window.location.href = '/app';
}

// Share results
function shareResults() {
    if (!comparisonResult) return;
    
    const winnerProp = comparisonResult[`property${comparisonResult.winner_property_num}`];
    const text = `I compared 3 properties and ${winnerProp.address} came out on top with a ${winnerProp.grade} grade! 🏆 Check out OfferWise AI to compare your properties: https://www.getofferwise.ai/compare`;
    
    // Try native share API
    if (navigator.share) {
        navigator.share({
            title: 'Property Comparison Results',
            text: text,
            url: window.location.href
        }).catch(err => console.log('Share cancelled'));
    } else {
        // Fallback to Twitter/Facebook
        const shareMenu = confirm('Share on Twitter?\n\nOK = Twitter\nCancel = Copy to clipboard');
        if (shareMenu) {
            window.open(`https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}`, '_blank');
        } else {
            navigator.clipboard.writeText(text);
            alert('✅ Results copied to clipboard!');
        }
    }
}

// New comparison
function newComparison() {
    // Clear inputs
    document.getElementById('property1-url').value = '';
    document.getElementById('property2-url').value = '';
    document.getElementById('property3-url').value = '';
    document.getElementById('property1-price').value = '';
    document.getElementById('property2-price').value = '';
    document.getElementById('property3-price').value = '';
    
    // Clear previews
    document.querySelectorAll('.address-preview').forEach(el => el.classList.remove('show'));
    document.querySelectorAll('.property-input').forEach(el => el.classList.remove('valid'));
    
    // Reset progress bars
    document.querySelectorAll('.progress-bar-fill').forEach(el => el.style.width = '0%');
    document.querySelectorAll('[id$="-percent"]').forEach(el => el.textContent = '0%');
    
    // Show input, hide results
    document.getElementById('input-section').style.display = 'block';
    document.getElementById('results-section').classList.remove('show');
    
    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Show error message
function showError(message) {
    const errorEl = document.getElementById('error-message');
    errorEl.textContent = message;
    errorEl.classList.add('show');
}

// Helper: sleep
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
