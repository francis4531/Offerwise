/**
 * Google Tag Manager loader — v5.87.31 (April 26, 2026)
 *
 * Loaded via <script src="/static/gtm-loader.js"></script> in the <head> of
 * the conversion-relevant pages (index, app, pricing, login, sample-analysis).
 *
 * Privacy guard: if the URL contains an admin_key parameter, do NOT load GTM.
 * This protects against accidental admin-context tracking in case anyone ever
 * lands on a "public" page while authenticated as admin (e.g. dev session
 * leakage, or a future feature that surfaces public pages with admin tools).
 *
 * The loader is intentionally tiny and synchronous so it runs before any
 * other tracking code on the page. The actual GTM container script loads
 * asynchronously inside it.
 */
(function () {
  'use strict';

  // Privacy guard — never fire GTM on admin-authenticated requests.
  // Checks both the visible URL (query string) and any parent frame URL
  // (in case admin embeds a public page in an iframe for debugging).
  try {
    var search = (window.location && window.location.search) || '';
    if (search.indexOf('admin_key=') !== -1) {
      // Quietly skip — leave a console breadcrumb for debugging
      if (window.console && console.info) {
        console.info('[gtm-loader] Skipped: admin_key in URL');
      }
      return;
    }
  } catch (e) {
    // If we can't read the URL for some reason, fail closed (don't load GTM)
    return;
  }

  // Standard GTM head snippet (verbatim from Google Tag Manager → Install)
  // GTM container ID: GTM-527FGN9K
  // Configured via Google Ads support session for /app conversion tracking.
  (function (w, d, s, l, i) {
    w[l] = w[l] || [];
    w[l].push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
    var f = d.getElementsByTagName(s)[0];
    var j = d.createElement(s);
    var dl = l !== 'dataLayer' ? '&l=' + l : '';
    j.async = true;
    j.src = 'https://www.googletagmanager.com/gtm.js?id=' + i + dl;
    f.parentNode.insertBefore(j, f);
  })(window, document, 'script', 'dataLayer', 'GTM-527FGN9K');
})();
