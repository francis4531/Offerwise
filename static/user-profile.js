// User Profile Dropdown Functionality
(function() {
    'use strict';
    
    function initUserProfile() {
        console.log('===========================================');
        console.log('🚀 INITIALIZING USER PROFILE DROPDOWN');
        console.log('===========================================');
        
        const profileButton = document.getElementById('user-profile-button');
        const dropdownMenu = document.getElementById('user-dropdown-menu');
        const userInitial = document.getElementById('user-initial');
        const oauthBadge = document.getElementById('oauth-badge');
        const dropdownEmail = document.getElementById('user-dropdown-email');
        const dropdownTier = document.getElementById('user-dropdown-tier');
        
        console.log('Checking for required elements:');
        console.log('  profileButton:', profileButton ? '✅ Found' : '❌ NOT FOUND');
        console.log('  dropdownMenu:', dropdownMenu ? '✅ Found' : '❌ NOT FOUND');
        console.log('  userInitial:', userInitial ? '✅ Found' : '❌ NOT FOUND');
        console.log('  oauthBadge:', oauthBadge ? '✅ Found' : '❌ NOT FOUND');
        console.log('  dropdownEmail:', dropdownEmail ? '✅ Found' : '❌ NOT FOUND');
        console.log('  dropdownTier:', dropdownTier ? '✅ Found' : '❌ NOT FOUND');
        
        if (!profileButton || !dropdownMenu) {
            console.error('❌ CRITICAL: Required user profile elements not found!');
            console.error('ProfileButton:', profileButton);
            console.error('DropdownMenu:', dropdownMenu);
            console.warn('User profile dropdown will not initialize');
            return;
        }
        
        console.log('✅ All required elements found, continuing initialization...');
        
        // Toggle dropdown
        profileButton.addEventListener('click', function(e) {
            e.stopPropagation();
            dropdownMenu.classList.toggle('show');
        });
        
        // Close dropdown when clicking outside
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.user-profile-dropdown')) {
                dropdownMenu.classList.remove('show');
            }
        });
        
        // Close dropdown on escape key
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && dropdownMenu.classList.contains('show')) {
                dropdownMenu.classList.remove('show');
            }
        });
        
        // Fetch user info and populate
        console.log('===========================================');
        console.log('🔄 USER PROFILE: Starting to load...');
        console.log('Fetching from: /api/user/info');
        console.log('===========================================');
        
        fetch('/api/user/info', {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        })
        .then(response => {
            console.log('📡 Response received from /api/user/info');
            console.log('Status:', response.status);
            console.log('Status Text:', response.statusText);
            console.log('OK:', response.ok);
            console.log('Headers:', response.headers);
            
            // Handle auth failure before parsing JSON
            if (response.status === 401) {
                console.error('❌ 401 Unauthorized - Redirecting to login');
                window.location.href = '/login';
                return null;
            }
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            // Check content type before parsing
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                console.error('❌ Response is not JSON:', contentType);
                window.location.href = '/login';
                return null;
            }
            
            return response.json();
        })
        .then(data => {
            if (!data) return; // Was redirected to login
            
            console.log('📦 Raw JSON data received:', data);
            return data;
        })
        .then(user => {
            if (!user) return; // Was redirected to login
            
            console.log('===========================================');
            console.log('✅ USER PROFILE LOADED SUCCESSFULLY');
            console.log('User object:', user);
            console.log('Email:', user.email);
            console.log('Auth provider:', user.auth_provider);
            console.log('Tier:', user.tier);
            console.log('Name:', user.name);
            console.log('===========================================');
            
            // Set avatar initial (first letter of email)
            if (user.email && userInitial) {
                const initial = user.email.charAt(0).toUpperCase();
                userInitial.textContent = initial;
                console.log('✅ Set avatar initial to:', initial);
            } else {
                console.warn('⚠️ No email found, avatar will remain "?"');
            }
            
            // Set OAuth badge
            if (oauthBadge) {
                console.log('Setting OAuth badge for provider:', user.auth_provider);
                if (user.auth_provider === 'google') {
                    oauthBadge.className = 'oauth-badge google';
                    oauthBadge.textContent = 'G';
                    oauthBadge.title = 'Logged in with Google';
                    console.log('✅ Set Google badge');
                } else if (user.auth_provider === 'facebook') {
                    oauthBadge.className = 'oauth-badge facebook';
                    oauthBadge.textContent = 'F';
                    oauthBadge.title = 'Logged in with Facebook';
                    console.log('✅ Set Facebook badge');
                } else if (user.auth_provider === 'apple') {
                    oauthBadge.className = 'oauth-badge apple';
                    oauthBadge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.54 4.09l.01-.01zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z"/></svg>';
                    oauthBadge.title = 'Logged in with Apple';
                    console.log('✅ Set Apple badge');
                } else {
                    oauthBadge.className = 'oauth-badge email';
                    oauthBadge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>';
                    oauthBadge.title = 'Logged in with Email';
                    console.log('✅ Set Email badge (default)');
                }
            }
            
            // Set dropdown email and tier
            if (dropdownEmail) {
                dropdownEmail.textContent = user.email || 'user@example.com';
                console.log('✅ Set dropdown email');
            }
            if (dropdownTier) {
                const tier = user.tier || 'free';
                dropdownTier.textContent = tier.charAt(0).toUpperCase() + tier.slice(1) + ' Plan';
                console.log('✅ Set dropdown tier:', tier);
            }
            
            console.log('===========================================');
            console.log('✅ USER PROFILE SETUP COMPLETE');
            console.log('===========================================');
        })
        .catch(error => {
            console.error('===========================================');
            console.error('❌ CRITICAL ERROR loading user profile');
            console.error('Error object:', error);
            console.error('Error name:', error.name);
            console.error('Error message:', error.message);
            console.error('Error stack:', error.stack);
            console.error('===========================================');
            
            // Set fallback values
            if (userInitial) {
                userInitial.textContent = '?';
                console.log('Set avatar to "?"');
            }
            if (dropdownEmail) {
                dropdownEmail.textContent = 'Error: ' + error.message;
                console.log('Set email to error message');
            }
            if (dropdownTier) {
                dropdownTier.textContent = 'Unable to load';
                console.log('Set tier to "Unable to load"');
            }
            if (oauthBadge) {
                oauthBadge.className = 'oauth-badge email';
                oauthBadge.innerHTML = '!';
                oauthBadge.title = 'Error loading login info';
                console.log('Set badge to error state');
            }
            
            // Alert user
            console.log('Showing alert to user about error');
            alert('⚠️ Unable to load account information.\n\nError: ' + error.message + '\n\nPlease:\n1. Check browser console (F12) for details\n2. Try refreshing the page\n3. Contact support if issue persists');
        });
    }
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initUserProfile);
    } else {
        initUserProfile();
    }
})();
