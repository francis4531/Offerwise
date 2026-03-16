/**
 * OfferWise Cookie Consent + AI Disclaimer v1.0
 * Include this script on every page: <script src="/static/js/compliance.js"></script>
 * 
 * CCPA compliant: Provides opt-out notice for California users.
 * Loads Google Analytics only after consent.
 */

(function() {
  'use strict';

  // ========================================
  // COOKIE CONSENT BANNER
  // ========================================
  var CONSENT_KEY = 'ow_cookie_consent';
  var consent = localStorage.getItem(CONSENT_KEY);

  if (!consent) {
    var banner = document.createElement('div');
    banner.id = 'cookieConsent';
    banner.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#e2e8f0;' +
      'padding:16px 24px;display:flex;align-items:center;justify-content:space-between;gap:16px;' +
      'z-index:99999;font-size:14px;line-height:1.5;box-shadow:0 -4px 20px rgba(0,0,0,0.3);' +
      'flex-wrap:wrap;font-family:Inter,system-ui,sans-serif;';

    banner.innerHTML =
      '<div style="flex:1;min-width:200px;">' +
        'We use cookies for analytics and to improve your experience. ' +
        '<a href="/privacy" style="color:#60a5fa;text-decoration:underline;">Privacy Policy</a>' +
      '</div>' +
      '<div style="display:flex;gap:8px;flex-shrink:0;">' +
        '<button id="cookieDecline" style="padding:8px 16px;border-radius:6px;border:1px solid #475569;' +
          'background:transparent;color:#e2e8f0;cursor:pointer;font-size:13px;">Decline</button>' +
        '<button id="cookieAccept" style="padding:8px 16px;border-radius:6px;border:none;' +
          'background:#3b82f6;color:white;cursor:pointer;font-size:13px;font-weight:500;">Accept</button>' +
      '</div>';

    document.body.appendChild(banner);

    document.getElementById('cookieAccept').addEventListener('click', function() {
      localStorage.setItem(CONSENT_KEY, 'accepted');
      banner.remove();
      loadAnalytics();
    });

    document.getElementById('cookieDecline').addEventListener('click', function() {
      localStorage.setItem(CONSENT_KEY, 'declined');
      banner.remove();
    });
  } else if (consent === 'accepted') {
    loadAnalytics();
  }

  function loadAnalytics() {
    // Only load Google Analytics if consent was given
    if (typeof gtag === 'undefined') {
      var s = document.createElement('script');
      s.async = true;
      s.src = 'https://www.googletagmanager.com/gtag/js?id=G-KXYFZ5BYPL';
      document.head.appendChild(s);
      s.onload = function() {
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        window.gtag = gtag;
        gtag('js', new Date());
        gtag('config', 'G-KXYFZ5BYPL');
        // Google Ads remarketing — builds audiences for retargeting campaigns
        gtag('config', 'AW-866249339');
      };
    }
  }

  // ========================================
  // CONVERSION TRACKING HELPERS
  // ========================================
  window.OfferWiseTrack = {
    /** Track free tool usage (Risk Check, Truth Check) */
    freeToolComplete: function(tool, data) {
      if (typeof gtag === 'function') {
        gtag('event', tool + '_complete', {
          event_category: 'free_tool',
          event_label: data || ''
        });
      }
    },
    /** Track signup conversion */
    signup: function(method) {
      if (typeof gtag === 'function') {
        gtag('event', 'sign_up', { method: method || 'email' });
        // Purchase/signup conversions flow via GA4 → Google Ads linked account
      }
    },
    /** Track analysis purchase */
    purchase: function(value, credits) {
      if (typeof gtag === 'function') {
        gtag('event', 'purchase', {
          value: value,
          currency: 'USD',
          items: [{ name: 'Analysis Credits', quantity: credits }]
        });
      }
    },
    /** Track CTA click */
    ctaClick: function(label, destination) {
      if (typeof gtag === 'function') {
        gtag('event', 'cta_click', {
          event_category: 'conversion',
          event_label: label,
          destination: destination
        });
      }
    }
  };

  // ========================================
  // AI DISCLAIMER (for analysis result pages)
  // ========================================
  window.OfferWiseCompliance = {
    /**
     * Call this after displaying AI-generated analysis results.
     * Adds a dismissible disclaimer banner above the results.
     * 
     * Usage: OfferWiseCompliance.showAIDisclaimer('containerId')
     */
    showAIDisclaimer: function(containerId) {
      var container = containerId ? document.getElementById(containerId) : null;
      var target = container || document.body;

      // Don't show if already dismissed this session
      if (sessionStorage.getItem('ow_ai_disclaimer_seen')) return;

      var disclaimer = document.createElement('div');
      disclaimer.style.cssText = 'background:#1e293b;border:1px solid #334155;border-radius:8px;' +
        'padding:12px 16px;margin:0 0 16px 0;display:flex;align-items:flex-start;gap:10px;' +
        'font-size:13px;line-height:1.5;color:#94a3b8;font-family:Inter,system-ui,sans-serif;';

      disclaimer.innerHTML =
        '<span style="font-size:16px;flex-shrink:0;margin-top:1px;">⚠️</span>' +
        '<div style="flex:1;">' +
          '<strong style="color:#e2e8f0;">AI-Generated Analysis</strong> — ' +
          'This analysis is produced by AI and is for informational purposes only. ' +
          'It is not legal, financial, or professional advice. Always verify findings independently ' +
          'and consult qualified professionals before making real estate decisions.' +
        '</div>' +
        '<button onclick="this.parentElement.remove();sessionStorage.setItem(\'ow_ai_disclaimer_seen\',\'1\')" ' +
          'style="background:none;border:none;color:#64748b;cursor:pointer;font-size:18px;padding:0 4px;' +
          'flex-shrink:0;line-height:1;" aria-label="Dismiss">×</button>';

      if (container) {
        container.insertBefore(disclaimer, container.firstChild);
      } else {
        document.body.insertBefore(disclaimer, document.body.firstChild);
      }
    }
  };
})();
