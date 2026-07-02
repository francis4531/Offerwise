/* exit-feedback.js — point-of-abandonment micro-survey.
 *
 * Captures WHY someone leaves, at the moment they leave. Design goals that make
 * it actually collect data instead of nothing:
 *   - one tap = one submission (navigator.sendBeacon, fires even during unload)
 *   - triggers on desktop exit-intent AND a post-value idle timer (mobile-safe)
 *   - shows once per session, non-blocking, dismissible — never a conversion trap
 *
 * Usage (on any surface, after the value moment is on screen):
 *   OfferWiseExit.mount({ context: 'findings' });
 *   // or customize:
 *   OfferWiseExit.mount({ context:'report', question:'Before you go — what stopped you?',
 *                         options:[{reason:'trust',label:"Don't trust the number"}, ...],
 *                         idleMs: 8000, exitIntent: true });
 */
(function () {
  if (window.OfferWiseExit) return;

  var ENDPOINT = '/api/feedback/exit';
  var DEFAULT_OPTIONS = [
    { reason: 'trust',           label: "I don't trust the number" },
    { reason: 'price',           label: "Not worth the price" },
    { reason: 'got_what_needed', label: "Got what I needed" },
    { reason: 'browsing',        label: "Just browsing" },
    { reason: 'other',           label: "Something else" }
  ];

  function injectStyles() {
    if (document.getElementById('ow-exit-styles')) return;
    var css = ''
      + '.ow-exit{position:fixed;z-index:99999;right:18px;bottom:18px;max-width:340px;width:calc(100% - 36px);'
      + 'background:#fff;color:#111;border:1px solid #e4e7ec;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.18);'
      + 'padding:16px 16px 14px;font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;'
      + 'transform:translateY(16px);opacity:0;transition:transform .28s ease,opacity .28s ease}'
      + '.ow-exit.ow-in{transform:translateY(0);opacity:1}'
      + '.ow-exit-q{font-weight:650;margin:0 24px 10px 0}'
      + '.ow-exit-x{position:absolute;top:10px;right:12px;border:0;background:none;font-size:18px;line-height:1;'
      + 'color:#98a2b3;cursor:pointer;padding:2px}'
      + '.ow-exit-opt{display:block;width:100%;text-align:left;margin:6px 0;padding:9px 12px;border:1px solid #e4e7ec;'
      + 'border-radius:9px;background:#f9fafb;color:#111;cursor:pointer;font:inherit;transition:background .12s,border-color .12s}'
      + '.ow-exit-opt:hover{background:#eef2ff;border-color:#c7d2fe}'
      + '.ow-exit-ta{width:100%;box-sizing:border-box;margin-top:6px;padding:8px 10px;border:1px solid #e4e7ec;border-radius:9px;'
      + 'font:inherit;resize:vertical;min-height:52px}'
      + '.ow-exit-send{margin-top:8px;padding:8px 14px;border:0;border-radius:9px;background:#2563eb;color:#fff;'
      + 'font:inherit;font-weight:600;cursor:pointer}'
      + '.ow-exit-thanks{padding:6px 0 2px;font-weight:600}'
      + '@media (max-width:520px){.ow-exit{right:10px;left:10px;bottom:10px;width:auto;max-width:none}}';
    var s = document.createElement('style');
    s.id = 'ow-exit-styles';
    s.textContent = css;
    document.head.appendChild(s);
  }

  function send(payload) {
    try {
      var body = JSON.stringify(payload);
      if (navigator.sendBeacon) {
        navigator.sendBeacon(ENDPOINT, new Blob([body], { type: 'application/json' }));
      } else {
        fetch(ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                          body: body, keepalive: true, credentials: 'same-origin' });
      }
    } catch (e) { /* never let feedback capture throw */ }
  }

  function mount(opts) {
    opts = opts || {};
    var context = opts.context || 'page';
    var question = opts.question || 'Before you go — what\u2019s holding you back?';
    var options = opts.options || DEFAULT_OPTIONS;
    var idleMs = typeof opts.idleMs === 'number' ? opts.idleMs : 8000;
    var exitIntent = opts.exitIntent !== false;
    var onceKey = 'ow_exit_shown_' + context;

    var shown = false, dismissed = false, card = null, idleTimer = null, engaged = false;

    function alreadyShown() {
      try { return sessionStorage.getItem(onceKey) === '1'; } catch (e) { return false; }
    }
    function markShown() {
      try { sessionStorage.setItem(onceKey, '1'); } catch (e) {}
    }

    function close() {
      dismissed = true;
      if (card) { card.classList.remove('ow-in'); setTimeout(function () { if (card) card.remove(); }, 300); }
      teardown();
    }

    function record(reason, label, text) {
      send({ context: context, reason: reason, reason_label: label || '',
             text: text || '', url: location.pathname + location.search });
    }

    function thanks() {
      if (!card) return;
      card.innerHTML = '<div class="ow-exit-thanks">Thanks \uD83D\uDE4F — that helps.</div>';
      setTimeout(close, 1600);
    }

    function pickOther() {
      if (!card) return;
      card.querySelector('.ow-exit-opts').style.display = 'none';
      var wrap = document.createElement('div');
      var ta = document.createElement('textarea');
      ta.className = 'ow-exit-ta';
      ta.placeholder = 'What would have made this work for you?';
      var btn = document.createElement('button');
      btn.className = 'ow-exit-send';
      btn.textContent = 'Send';
      btn.onclick = function () { record('other', 'Something else', ta.value); thanks(); };
      wrap.appendChild(ta); wrap.appendChild(btn);
      card.appendChild(wrap);
      try { ta.focus(); } catch (e) {}
    }

    function show() {
      if (shown || dismissed || alreadyShown()) return;
      shown = true; markShown();
      injectStyles();
      card = document.createElement('div');
      card.className = 'ow-exit';
      card.setAttribute('role', 'dialog');
      var html = '<button class="ow-exit-x" aria-label="Close">\u00D7</button>'
               + '<div class="ow-exit-q"></div><div class="ow-exit-opts"></div>';
      card.innerHTML = html;
      card.querySelector('.ow-exit-q').textContent = question;
      var optsEl = card.querySelector('.ow-exit-opts');
      options.forEach(function (o) {
        var b = document.createElement('button');
        b.className = 'ow-exit-opt';
        b.textContent = o.label;
        b.onclick = function () {
          if (o.reason === 'other') { pickOther(); }
          else { record(o.reason, o.label); thanks(); }
        };
        optsEl.appendChild(b);
      });
      card.querySelector('.ow-exit-x').onclick = function () {
        record('dismissed', 'Closed without answering'); close();
      };
      document.body.appendChild(card);
      requestAnimationFrame(function () { card.classList.add('ow-in'); });
    }

    // triggers
    function onMouseLeave(e) { if ((e.clientY || 0) <= 0) show(); }
    function markEngaged() { engaged = true; }
    function resetIdle() {
      if (idleTimer) clearTimeout(idleTimer);
      // only arm the idle-exit prompt once the user has actually engaged with
      // content (scrolled/tapped) — so it never fires on a landing form.
      if (engaged) idleTimer = setTimeout(show, idleMs);
    }
    function teardown() {
      document.removeEventListener('mouseleave', onMouseLeave);
      document.removeEventListener('scroll', markEngaged, true);
      document.removeEventListener('touchstart', markEngaged, true);
      document.removeEventListener('scroll', resetIdle, true);
      document.removeEventListener('mousemove', resetIdle, true);
      document.removeEventListener('touchstart', resetIdle, true);
      if (idleTimer) clearTimeout(idleTimer);
    }

    if (alreadyShown()) return { trigger: function () {} };
    if (exitIntent) document.addEventListener('mouseleave', onMouseLeave);
    if (idleMs > 0) {
      document.addEventListener('scroll', markEngaged, true);
      document.addEventListener('touchstart', markEngaged, true);
      document.addEventListener('scroll', resetIdle, true);
      document.addEventListener('mousemove', resetIdle, true);
      document.addEventListener('touchstart', resetIdle, true);
    }
    return { trigger: show, close: close };
  }

  window.OfferWiseExit = { mount: mount };
})();
