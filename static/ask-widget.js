/*!
 * ask-widget.js — OfferWise "Ask your report" component (v5.89.154)
 *
 * One reusable chat widget for every surface where a buyer sees a report:
 *   - the no-login on-ramp (/try)        context: one uploaded document
 *   - the full report (/app)             context: documents + analysis
 *   - the shared view (/opinion/<token>) context: the shared snapshot
 *
 * Self-contained: injects its own styles, renders the UI, talks to a configured
 * endpoint, and renders answers with a safe markdown subset. Usage:
 *
 *   OfferWiseAsk.mount({
 *     el: '#ask', endpoint: '/api/report/chat', payload: { analysis_id: 123 },
 *     contextLabel: 'your full analysis', contextPieces: '3 documents + OfferWise reasoning',
 *     intro: '...', findings: [{severity,text}], chips: ['...'],
 *     placeholder: 'Ask anything about your analysis…',
 *     footerNote: 'Unlimited on your plan', cta: { text:'…', href:'…', ghost:true },
 *     cap: 6, remaining: 6   // optional free-message cap for anonymous surfaces
 *   });
 */
(function () {
  'use strict';
  if (window.OfferWiseAsk) { return; }

  var STYLE_ID = 'owask-styles';
  var CSS = [
    '.owask{--c-card:#162338;--c-card2:#1b2b45;--c-line:rgba(255,255,255,.09);--c-line2:rgba(255,255,255,.14);',
    '--c-text:#eef3fb;--c-muted:#9fb0c9;--c-dim:#6b7c97;--c-orange:#f97316;--c-amber:#f59e0b;',
    '--c-ok:#34d399;--c-crit:#f87171;--c-maj:#fbbf24;--c-mod:#60a5fa;',
    'font-family:"DM Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;',
    'background:linear-gradient(180deg,#0f1a2e,#0c1526);border:1px solid var(--c-line2);border-radius:18px;',
    'padding:20px clamp(16px,3vw,26px) 22px;color:var(--c-text);box-shadow:0 18px 50px -24px rgba(0,0,0,.7)}',
    '.owask *{box-sizing:border-box}',
    '.owask-ctx{display:inline-flex;align-items:center;gap:9px;font-size:.72rem;font-weight:700;letter-spacing:.12em;',
    'text-transform:uppercase;color:var(--c-muted);background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:50px;padding:6px 13px;margin-bottom:18px}',
    '.owask-live{width:8px;height:8px;border-radius:50%;background:var(--c-ok);animation:owpulse 2.2s infinite}',
    '@keyframes owpulse{0%{box-shadow:0 0 0 0 rgba(52,211,153,.5)}70%{box-shadow:0 0 0 7px rgba(52,211,153,0)}100%{box-shadow:0 0 0 0 rgba(52,211,153,0)}}',
    '.owask-ctx b{color:var(--c-muted);font-weight:700}.owask-ctx .owask-pieces{color:#6a7c98;font-weight:600;letter-spacing:.06em}',
    '.owask-fh{display:flex;align-items:center;gap:11px;margin:6px 0 14px;color:var(--c-muted);font-size:.76rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase}',
    '.owask-fh::after{content:"";flex:1;height:1px;background:rgba(255,255,255,.08)}',
    '.owask-find{background:linear-gradient(180deg,#111a2e,#0f1830);border:1px solid rgba(255,255,255,.08);',
    'border-left:3px solid #6a7c98;border-radius:16px;padding:16px 18px;margin-bottom:11px}',
    '.owask-find.critical{border-left-color:var(--c-crit)}.owask-find.major{border-left-color:var(--c-maj)}.owask-find.moderate{border-left-color:var(--c-mod)}',
    '.owask-sev{display:inline-block;font-size:.66rem;font-weight:800;letter-spacing:.05em;text-transform:uppercase;padding:3px 9px;border-radius:7px;margin-bottom:9px}',
    '.owask-sev.critical{background:rgba(248,113,113,.16);color:var(--c-crit)}.owask-sev.major{background:rgba(251,191,36,.16);color:var(--c-maj)}.owask-sev.moderate{background:rgba(96,165,250,.16);color:var(--c-mod)}',
    '.owask-find p{font-size:.95rem;color:#d7e2f1;line-height:1.55;margin:0}',
    '.owask-thread{display:flex;flex-direction:column;gap:14px;margin-top:4px}',
    '.owask-row{display:flex;gap:10px;max-width:92%;animation:owrise .35s ease both}',
    '.owask-row.me{align-self:flex-end;flex-direction:row-reverse;max-width:78%}',
    '@keyframes owrise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}',
    '.owask-ava{width:28px;height:28px;border-radius:8px;flex-shrink:0;display:grid;place-items:center;',
    'background:linear-gradient(135deg,var(--c-orange),var(--c-amber));color:#1a1205;font-weight:800;font-size:.72rem;margin-top:2px}',
    '.owask-who{font-size:.72rem;color:var(--c-dim);font-weight:600;margin:0 0 4px 2px}',
    '.owask-bub{padding:12px 15px;border-radius:14px;font-size:.95rem;line-height:1.6}',
    '.owask-row.ai .owask-bub{background:var(--c-card);border:1px solid var(--c-line);border-top-left-radius:5px}',
    '.owask-row.me .owask-bub{background:linear-gradient(135deg,#2563eb,#3b82f6);color:#fff;border-top-right-radius:5px;white-space:pre-wrap}',
    '.owask-bub p{margin:0 0 9px}.owask-bub p:last-child{margin:0}',
    '.owask-bub ul{margin:7px 0;padding-left:19px}.owask-bub ul:last-child{margin-bottom:0}.owask-bub li{margin-bottom:4px}',
    '.owask-bub strong{color:#fff;font-weight:700}.owask-bub em{font-style:italic;color:#cfe0f7}',
    '.owask-typing .owask-bub{color:var(--c-dim)}',
    '.owask-chips{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0 2px}',
    '.owask-chip{font-size:.82rem;color:var(--c-muted);background:rgba(255,255,255,.03);border:1px solid var(--c-line2);',
    'border-radius:50px;padding:7px 13px;cursor:pointer;transition:.16s;font-family:inherit}',
    '.owask-chip:hover{color:var(--c-text);border-color:rgba(245,158,11,.5);transform:translateY(-1px)}',
    '.owask-composer{display:flex;gap:9px;margin-top:16px}',
    '.owask-inp{flex:1;background:var(--c-card);border:1px solid var(--c-line2);border-radius:12px;padding:13px 15px;',
    'color:var(--c-text);font-size:.94rem;font-family:inherit}.owask-inp:focus{outline:none;border-color:var(--c-mod)}',
    '.owask-send{background:linear-gradient(135deg,var(--c-orange),var(--c-amber));color:#1a1205;font-weight:800;border:0;',
    'border-radius:12px;padding:0 22px;font-family:inherit;font-size:.94rem;cursor:pointer}.owask-send:disabled{opacity:.45;cursor:not-allowed}',
    '.owask-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px;flex-wrap:wrap}',
    '.owask-note{font-size:.79rem;color:var(--c-dim)}',
    '.owask-cta{font-weight:700;font-size:.9rem;text-decoration:none;color:#1a1205;background:linear-gradient(135deg,var(--c-orange),var(--c-amber));padding:9px 16px;border-radius:10px}',
    '.owask-cta.ghost{background:transparent;color:var(--c-amber);border:1px solid rgba(245,158,11,.4)}',
    '.owask-ctacard{margin-top:18px;background:linear-gradient(135deg,rgba(249,115,22,.12),rgba(245,158,11,.08));',
    'border:1px solid rgba(245,158,11,.3);border-radius:14px;padding:20px;text-align:center}',
    '.owask-ctacard h4{font-family:"DM Serif Display",Georgia,serif;font-size:1.15rem;margin:0 0 7px}',
    '.owask-ctacard p{color:var(--c-muted);font-size:.92rem;margin:0 0 14px}',
    '.owask-ctacard a{display:inline-block;padding:12px 24px;background:linear-gradient(135deg,var(--c-orange),var(--c-amber));color:#1a1205;border-radius:11px;font-weight:800;text-decoration:none}'
  ].join('');

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) { return; }
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function inlineMd(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/__([^_]+)__/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*(?!\s)([^*]+?)\*(?!\*)/g, '$1<em>$2</em>');
  }
  function renderRich(text) {
    var safe = escapeHtml(text).replace(/\r/g, '');
    var blocks = safe.split(/\n{2,}/), html = '';
    blocks.forEach(function (block) {
      var lines = block.split('\n').filter(function (l) { return !/^\s*([-*_=]\s*){3,}$/.test(l); });
      if (!lines.length) { return; }
      var allBullets = lines.every(function (l) { return /^\s*[-*•]\s+/.test(l); });
      if (allBullets) {
        html += '<ul>' + lines.map(function (l) {
          return '<li>' + inlineMd(l.replace(/^\s*[-*•]\s+/, '')) + '</li>';
        }).join('') + '</ul>';
      } else {
        html += '<p>' + lines.map(inlineMd).join('<br>') + '</p>';
      }
    });
    return html || ('<p>' + safe + '</p>');
  }

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (html != null) { n.innerHTML = html; }
    return n;
  }

  function mount(opts) {
    ensureStyles();
    var root = typeof opts.el === 'string' ? document.querySelector(opts.el) : opts.el;
    if (!root) { return null; }
    root.innerHTML = '';
    root.classList.add('owask');

    var state = {
      remaining: (typeof opts.remaining === 'number') ? opts.remaining : null,
      cap: (typeof opts.cap === 'number') ? opts.cap : null,
      busy: false, capped: false
    };

    // Context strip
    if (opts.contextLabel) {
      var ctx = el('div', 'owask-ctx');
      ctx.appendChild(el('span', 'owask-live'));
      ctx.appendChild(el('b', null, 'Grounded in ' + escapeHtml(opts.contextLabel)));
      if (opts.contextPieces) { ctx.appendChild(el('span', 'owask-pieces', escapeHtml(opts.contextPieces))); }
      root.appendChild(ctx);
    }

    // Findings teaser
    if (opts.findings && opts.findings.length) {
      root.appendChild(el('div', 'owask-fh', "What I'd look at first"));
      opts.findings.forEach(function (f) {
        var sev = (f.severity || 'finding');
        var card = el('div', 'owask-find ' + sev);
        card.appendChild(el('span', 'owask-sev ' + sev, escapeHtml(sev)));
        card.appendChild(el('p', null, escapeHtml(f.text)));
        root.appendChild(card);
      });
    }

    var thread = el('div', 'owask-thread');
    root.appendChild(thread);

    function addMsg(role, text) {
      var row = el('div', 'owask-row ' + (role === 'user' ? 'me' : 'ai'));
      if (role !== 'user') {
        row.appendChild(el('div', 'owask-ava', opts.assistantAvatar || 'OW'));
        var col = el('div');
        col.appendChild(el('div', 'owask-who', opts.assistantName || 'OfferWise'));
        var bub = el('div', 'owask-bub');
        bub.innerHTML = renderRich(text);
        col.appendChild(bub);
        row.appendChild(col);
      } else {
        var ub = el('div', 'owask-bub');
        ub.textContent = text;
        row.appendChild(ub);
      }
      thread.appendChild(row);
      row.scrollIntoView({ behavior: 'smooth', block: 'end' });
      return row;
    }

    if (opts.intro) { addMsg('assistant', opts.intro); }

    // Chips
    var chipsWrap = null;
    if (opts.chips && opts.chips.length) {
      chipsWrap = el('div', 'owask-chips');
      opts.chips.forEach(function (c) {
        var chip = el('button', 'owask-chip', escapeHtml(c));
        chip.type = 'button';
        chip.addEventListener('click', function () { inp.value = c; send(); });
        chipsWrap.appendChild(chip);
      });
      root.appendChild(chipsWrap);
    }

    // Composer
    var composer = el('div', 'owask-composer');
    var inp = el('input', 'owask-inp');
    inp.type = 'text';
    inp.placeholder = opts.placeholder || 'Ask about your report…';
    inp.setAttribute('autocomplete', 'off');
    var sendBtn = el('button', 'owask-send', 'Ask');
    sendBtn.type = 'button';
    composer.appendChild(inp);
    composer.appendChild(sendBtn);
    root.appendChild(composer);

    var foot = el('div', 'owask-foot');
    var note = el('span', 'owask-note', '');
    foot.appendChild(note);
    if (opts.cta) {
      var a = el('a', 'owask-cta' + (opts.cta.ghost ? ' ghost' : ''), escapeHtml(opts.cta.text));
      a.href = opts.cta.href || '#';
      foot.appendChild(a);
    }
    root.appendChild(foot);
    var ctaArea = el('div');
    root.appendChild(ctaArea);

    function refreshNote() {
      if (state.cap != null && state.remaining != null) {
        note.textContent = state.remaining > 0
          ? state.remaining + (state.remaining === 1 ? ' free question left' : ' free questions left')
          : '';
      } else if (opts.footerNote) {
        note.textContent = opts.footerNote;
      }
    }
    refreshNote();

    function showCta(msg, url) {
      if (chipsWrap) { chipsWrap.style.display = 'none'; }
      composer.style.display = 'none';
      var card = el('div', 'owask-ctacard');
      card.appendChild(el('h4', null, 'See the whole picture'));
      card.appendChild(el('p', null, escapeHtml(msg)));
      var a = el('a', null, 'Run the full analysis — free to start');
      a.href = url || (opts.cta && opts.cta.href) || '/analyze';
      card.appendChild(a);
      ctaArea.appendChild(card);
      card.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }

    function send() {
      var q = inp.value.trim();
      if (!q || state.busy || state.capped) { return; }
      addMsg('user', q);
      inp.value = '';
      state.busy = true; sendBtn.disabled = true;
      var typing = addMsg('assistant', '…');
      typing.classList.add('owask-typing');

      var body = {};
      if (opts.payload) { for (var k in opts.payload) { body[k] = opts.payload[k]; } }
      body.message = q;

      fetch(opts.endpoint, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, status: r.status, d: d }; });
      }).then(function (res) {
        if (typing.parentNode) { thread.removeChild(typing); }
        state.busy = false; sendBtn.disabled = false;
        var d = res.d || {};
        if (d.capped) { state.capped = true; showCta(d.message, d.cta_url); return; }
        if (res.status === 410) { addMsg('assistant', d.message || 'This session has expired.'); state.capped = true; return; }
        if (!res.ok) { addMsg('assistant', d.message || "I couldn't answer that just now. Please try again."); return; }
        addMsg('assistant', d.answer || '');
        if (typeof d.messages_remaining === 'number') { state.remaining = d.messages_remaining; refreshNote(); }
        if (state.cap != null && state.remaining != null && state.remaining <= 0) {
          state.capped = true;
          showCta("That's the end of the free preview. Create a free account and I'll run the full analysis — every finding, cross-checked against the disclosures, with a defensible offer price.", (opts.cta && opts.cta.href) || '/analyze');
        }
      }).catch(function () {
        if (typing.parentNode) { thread.removeChild(typing); }
        state.busy = false; sendBtn.disabled = false;
        addMsg('assistant', "I'm having trouble right now. Please try again in a moment.");
      });
    }

    sendBtn.addEventListener('click', send);
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { send(); } });

    return { send: send, addMsg: addMsg, root: root };
  }

  window.OfferWiseAsk = { mount: mount };
})();
