/**
 * OfferWise Analytics Loader
 * ==========================
 * Fetches GA4, Google Ads, and Reddit pixel IDs from server, then loads all SDKs.
 * Include on any page: <script src="/static/analytics-loader.js" defer></script>
 *
 * Required env vars on server:
 *   GA4_MEASUREMENT_ID        — e.g. G-XXXXXXXXXX
 *   GOOGLE_ADS_CONVERSION_ID  — e.g. AW-XXXXXXXXX
 *   GOOGLE_ADS_SIGNUP_LABEL   — e.g. AbCdEfGhIjk (signup conversion label)
 *   GOOGLE_ADS_PURCHASE_LABEL — e.g. XxXxXxXxXxX (purchase conversion label)
 *   REDDIT_PIXEL_ID           — e.g. t2_xxxxxxxx
 */
(function() {
    'use strict';

    if (window.__ow_analytics_loaded) return;
    window.__ow_analytics_loaded = true;

    fetch('/api/config/analytics')
        .then(function(r) { return r.json(); })
        .then(function(cfg) {

            // ── GA4 ──────────────────────────────────────────────────────
            var ga4 = cfg.ga4_id;
            if (ga4 && /^G-[A-Z0-9]+$/i.test(ga4)) {
                var s = document.createElement('script');
                s.async = true;
                s.src = 'https://www.googletagmanager.com/gtag/js?id=' + ga4;
                document.head.appendChild(s);
                window.dataLayer = window.dataLayer || [];
                window.gtag = function() { window.dataLayer.push(arguments); };
                window.gtag('js', new Date());
                window.gtag('config', ga4, { send_page_view: true, cookie_flags: 'SameSite=None;Secure' });
            }

            // ── Google Ads conversion tag ────────────────────────────────
            var gadsId = cfg.gads_id;
            if (gadsId && /^AW-[0-9]+$/i.test(gadsId)) {
                // Load gtag for Google Ads if not already loaded by GA4
                if (!window.gtag) {
                    var gs = document.createElement('script');
                    gs.async = true;
                    gs.src = 'https://www.googletagmanager.com/gtag/js?id=' + gadsId;
                    document.head.appendChild(gs);
                    window.dataLayer = window.dataLayer || [];
                    window.gtag = function() { window.dataLayer.push(arguments); };
                    window.gtag('js', new Date());
                }
                window.gtag('config', gadsId);
                window.__ow_gads_id = gadsId;
                window.__ow_gads_signup_label   = cfg.gads_signup_label   || '';
                window.__ow_gads_purchase_label = cfg.gads_purchase_label || '';
            }

            // ── Reddit Pixel ─────────────────────────────────────────────
            var rPixel = cfg.reddit_pixel_id;
            if (rPixel) {
                !function(w,d){
                    if(!w.rdt){
                        var p=w.rdt=function(){p.sendEvent?p.sendEvent.apply(p,arguments):p.callQueue.push(arguments)};
                        p.callQueue=[];
                        var t=d.createElement('script');
                        t.src='https://www.redditstatic.com/ads/v2/rdtpixel.js';
                        t.async=1;
                        var s=d.getElementsByTagName('script')[0];
                        s.parentNode.insertBefore(t,s);
                    }
                }(window,document);
                window.rdt('init', rPixel, { optOut: false, useDecimalCurrencyValues: true });
                window.rdt('track', 'PageVisit');
                window.__ow_reddit_pixel_id = rPixel;
            }

            // ── owFireSignupConversion — call after new user signs up ────
            window.owFireSignupConversion = function(email) {
                // GA4
                if (window.gtag) {
                    window.gtag('event', 'sign_up', { method: 'OfferWise' });
                }
                // Google Ads signup conversion
                if (window.gtag && window.__ow_gads_id && window.__ow_gads_signup_label) {
                    window.gtag('event', 'conversion', {
                        send_to: window.__ow_gads_id + '/' + window.__ow_gads_signup_label,
                    });
                }
                // Reddit signup
                if (window.rdt) {
                    window.rdt('track', 'SignUp', { email: email || '' });
                }
            };

            // ── owFirePurchaseConversion — call after successful payment ─
            window.owFirePurchaseConversion = function(amount, planName, transactionId) {
                var val = parseFloat(amount) || 0;
                var txn = transactionId || Date.now().toString();
                // GA4
                if (window.gtag) {
                    window.gtag('event', 'purchase', {
                        transaction_id: txn,
                        value: val,
                        currency: 'USD',
                        items: [{ item_name: planName || 'OfferWise Analysis', quantity: 1, price: val }]
                    });
                }
                // Google Ads purchase conversion
                if (window.gtag && window.__ow_gads_id && window.__ow_gads_purchase_label) {
                    window.gtag('event', 'conversion', {
                        send_to: window.__ow_gads_id + '/' + window.__ow_gads_purchase_label,
                        value: val,
                        currency: 'USD',
                        transaction_id: txn,
                    });
                }
                // Reddit purchase
                if (window.rdt) {
                    window.rdt('track', 'Purchase', {
                        value: val,
                        currency: 'USD',
                        itemCount: 1,
                    });
                }
            };

        })
        .catch(function() { /* Silent fail — analytics must never break the page */ });
})();
