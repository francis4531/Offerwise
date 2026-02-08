// ============================================================================
// REFERRAL SYSTEM - JavaScript Functions
// ============================================================================

let referralData = null;

// Load referral stats from API
async function loadReferralCode() {
    try {
        console.log('🎁 Loading referral data...');
        
        const response = await fetch('/api/referral/stats', {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            throw new Error('Failed to load referral stats');
        }
        
        referralData = await response.json();
        
        if (referralData.success) {
            updateReferralUI(referralData);
            console.log('✅ Referral data loaded');
        } else {
            console.error('❌ Failed to load referral data:', referralData.error);
        }
    } catch (error) {
        console.error('❌ Error loading referral data:', error);
        // Show fallback UI
        document.getElementById('referral-code-input').value = 'Error loading code';
        document.getElementById('referral-link-input').value = 'Error loading link';
    }
}

// Update UI with referral data
function updateReferralUI(data) {
    // Update hero banner stats (with null checks)
    const countDisplay = document.getElementById('referral-count-display');
    const creditsDisplay = document.getElementById('referral-credits-display');
    const tierDisplay = document.getElementById('referral-tier-display');
    
    if (countDisplay) countDisplay.textContent = data.total_referrals || 0;
    if (creditsDisplay) creditsDisplay.textContent = data.credits_earned || 0;
    if (tierDisplay) tierDisplay.textContent = data.current_tier || 0;
    
    // Update tier badge
    const tierBadge = document.getElementById('referral-tier-badge');
    if (tierBadge && data.current_tier_info) {
        tierBadge.textContent = data.current_tier_info.icon || '';
    }
    
    // Update referral code and link
    const codeInput = document.getElementById('referral-code-input');
    const linkInput = document.getElementById('referral-link-input');
    
    if (codeInput) codeInput.value = data.code || 'Loading...';
    if (linkInput) linkInput.value = data.referral_url || 'Loading...';
    
    // Update tier progress
    const progressText = document.getElementById('tier-progress-text');
    const progressBar = document.getElementById('tier-progress-bar');
    const progressSection = document.getElementById('tier-progress-section');
    
    if (data.next_tier) {
        if (progressText) progressText.textContent = `${data.next_tier.remaining} more referrals to reach Tier ${data.next_tier.tier}`;
        if (progressBar) progressBar.style.width = `${data.next_tier.progress_percent}%`;
        if (progressSection) progressSection.style.display = 'block';
    } else {
        // Max tier reached
        if (progressText) progressText.textContent = 'Maximum tier reached! 👑';
        if (progressBar) progressBar.style.width = '100%';
    }
    
    // Update tier cards
    updateTierCards(data.current_tier, data.all_tiers);
    
    // Update referral history
    updateReferralHistory(data.referral_history);
}

// Update tier cards with unlock status
function updateTierCards(currentTier, allTiers) {
    for (let tier = 1; tier <= 4; tier++) {
        const card = document.getElementById(`tier-${tier}-card`);
        const status = document.getElementById(`tier-${tier}-status`);
        
        if (!card || !status) continue;
        
        if (tier <= currentTier) {
            // Unlocked
            card.style.background = 'linear-gradient(135deg, rgba(139, 92, 246, 0.2), rgba(124, 58, 237, 0.2))';
            card.style.borderColor = '#a78bfa';
            status.textContent = '✅ Unlocked';
            status.style.color = '#22c55e';
        } else {
            // Locked
            card.style.background = 'rgba(255, 255, 255, 0.03)';
            card.style.borderColor = 'rgba(255, 255, 255, 0.1)';
            status.textContent = '🔒 Locked';
            status.style.color = '#64748b';
        }
    }
}

// Update referral history table
function updateReferralHistory(history) {
    const loadingDiv = document.getElementById('referral-history-loading');
    const emptyDiv = document.getElementById('referral-history-empty');
    const tableDiv = document.getElementById('referral-history-table');
    const tbody = document.getElementById('referral-history-body');
    
    // Hide loading
    if (loadingDiv) loadingDiv.style.display = 'none';
    
    if (!history || history.length === 0) {
        // Show empty state
        if (emptyDiv) emptyDiv.style.display = 'block';
        if (tableDiv) tableDiv.style.display = 'none';
        return;
    }
    
    // Show table
    if (emptyDiv) emptyDiv.style.display = 'none';
    if (tableDiv) tableDiv.style.display = 'block';
    
    // Build table rows
    tbody.innerHTML = history.map(ref => {
        const date = new Date(ref.signup_date).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric'
        });
        
        return `
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 10px 0; color: #f1f5f9; font-weight: 600; font-size: 15px;">${ref.name}</td>
                <td style="padding: 10px 0; color: #94a3b8; font-size: 13px;">${date}</td>
                <td style="padding: 10px 0; text-align: right; color: #22c55e; font-weight: 600; font-size: 15px;">+${ref.credits_awarded}</td>
            </tr>
        `;
    }).join('');
}

// Copy referral code
function copyReferralCode(event) {
    const input = document.getElementById('referral-code-input');
    input.select();
    input.setSelectionRange(0, 99999);
    
    navigator.clipboard.writeText(input.value).then(() => {
        const button = event ? event.target : event.currentTarget;
        const originalText = button.textContent;
        const originalBg = button.style.background;
        
        button.textContent = '✅ Copied!';
        button.style.background = '#22c55e';
        
        setTimeout(() => {
            button.textContent = originalText;
            button.style.background = originalBg;
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy:', err);
        alert('Failed to copy. Please select and copy manually.');
    });
}

// Copy referral link
function copyReferralLink(event) {
    const input = document.getElementById('referral-link-input');
    input.select();
    input.setSelectionRange(0, 99999);
    
    navigator.clipboard.writeText(input.value).then(() => {
        const button = event ? event.target : event.currentTarget;
        const originalText = button.textContent;
        const originalBg = button.style.background;
        
        button.textContent = '✅ Copied!';
        button.style.background = '#22c55e';
        
        setTimeout(() => {
            button.textContent = originalText;
            button.style.background = originalBg;
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy:', err);
        alert('Failed to copy. Please select and copy manually.');
    });
}

// Share via Email
function shareViaEmail() {
    if (!referralData || !referralData.share_text) {
        alert('Please wait for referral data to load');
        return;
    }
    
    const subject = encodeURIComponent(referralData.share_text.email_subject);
    const body = encodeURIComponent(referralData.share_text.email_body);
    window.open(`mailto:?subject=${subject}&body=${body}`, '_blank');
}

// Share via Twitter
function shareViaTwitter() {
    if (!referralData || !referralData.share_text) {
        alert('Please wait for referral data to load');
        return;
    }
    
    const text = encodeURIComponent(referralData.share_text.twitter);
    window.open(`https://twitter.com/intent/tweet?text=${text}`, '_blank', 'width=600,height=400');
}

// Share via Facebook
function shareViaFacebook() {
    if (!referralData) {
        alert('Please wait for referral data to load');
        return;
    }
    
    window.open(
        `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(referralData.referral_url)}`,
        '_blank',
        'width=600,height=400'
    );
}

// Share via WhatsApp
function shareViaWhatsApp() {
    if (!referralData || !referralData.share_text) {
        alert('Please wait for referral data to load');
        return;
    }
    
    const text = encodeURIComponent(referralData.share_text.whatsapp);
    window.open(`https://wa.me/?text=${text}`, '_blank');
}

// Share via LinkedIn
function shareViaLinkedIn() {
    if (!referralData) {
        alert('Please wait for referral data to load');
        return;
    }
    
    window.open(
        `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(referralData.referral_url)}`,
        '_blank',
        'width=600,height=400'
    );
}
