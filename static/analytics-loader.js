/**
 * OfferWise Analytics Loader
 * ==========================
 * Fetches GA4 measurement ID from server, then loads gtag.js.
 * Include on any page: <script src="/static/analytics-loader.js" defer></script>
 *
 * Safe to include everywhere — gracefully no-ops if GA4_MEASUREMENT_ID is not set.
 */
(function() {
    'use strict';
    
    // Avoid double-loading
    if (window.__ow_analytics_loaded) return;
    window.__ow_analytics_loaded = true;
    
    fetch('/api/config/analytics')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var id = data.ga4_id;
            if (!id || !/^G-[A-Z0-9]+$/i.test(id)) return;
            
            // Load gtag.js
            var script = document.createElement('script');
            script.async = true;
            script.src = 'https://www.googletagmanager.com/gtag/js?id=' + id;
            document.head.appendChild(script);
            
            // Initialize dataLayer and gtag
            window.dataLayer = window.dataLayer || [];
            window.gtag = function() { window.dataLayer.push(arguments); };
            window.gtag('js', new Date());
            window.gtag('config', id, {
                send_page_view: true,
                cookie_flags: 'SameSite=None;Secure'
            });
        })
        .catch(function() {
            // Silent fail — analytics should never break the page
        });
})();
