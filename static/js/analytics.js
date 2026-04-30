/**
 * OfferWise Analytics & Retargeting
 * Loads GA4 + Google Ads conversion tracking when configured.
 * 
 * Configuration: Add to any page's <head>:
 *   <meta name="ga-measurement-id" content="G-XXXXXXXXXX">
 *   <script src="/static/js/analytics.js" defer></script>
 * 
 * Or the script will fetch the ID from /api/config/analytics
 */
(function() {
  'use strict';
  
  function loadGA4(measurementId) {
    if (!measurementId || measurementId === 'G-XXXXXXXXXX') return;
    
    // Load gtag.js
    var script = document.createElement('script');
    script.async = true;
    script.src = 'https://www.googletagmanager.com/gtag/js?id=' + measurementId;
    document.head.appendChild(script);
    
    // Initialize
    window.dataLayer = window.dataLayer || [];
    function gtag() { dataLayer.push(arguments); }
    window.gtag = gtag;
    gtag('js', new Date());
    gtag('config', measurementId, {
      page_path: window.location.pathname,
      anonymize_ip: true
    });
    
    // Track page views on SPA navigation
    var lastPath = window.location.pathname;
    setInterval(function() {
      if (window.location.pathname !== lastPath) {
        lastPath = window.location.pathname;
        gtag('event', 'page_view', { page_path: lastPath });
      }
    }, 1000);
  }
  
  // Try meta tag first
  var meta = document.querySelector('meta[name="ga-measurement-id"]');
  if (meta && meta.content && meta.content !== 'G-XXXXXXXXXX') {
    loadGA4(meta.content);
    return;
  }
  
  // Fall back to API config
  fetch('/api/config/analytics')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ga4_id) loadGA4(data.ga4_id);
    })
    .catch(function() { /* Analytics not configured — silent */ });
})();
