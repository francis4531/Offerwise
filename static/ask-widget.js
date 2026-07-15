/*!
 * ask-widget.js — OfferWise "Ask your report" component (v5.89.292)
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
    '.owask-summary{font-family:"DM Serif Display",Georgia,serif;font-size:1.4rem;line-height:1.32;color:var(--c-text);margin:2px 0 16px}',
    '.owask-find{background:linear-gradient(180deg,#111a2e,#0f1830);border:1px solid rgba(255,255,255,.08);',
    'border-left:3px solid #6a7c98;border-radius:16px;padding:16px 18px;margin-bottom:11px}',
    '.owask-find.critical{border-left-color:var(--c-crit)}.owask-find.major{border-left-color:var(--c-maj)}.owask-find.moderate{border-left-color:var(--c-mod)}',
    '.owask-sev{display:inline-block;font-size:.66rem;font-weight:800;letter-spacing:.05em;text-transform:uppercase;padding:3px 9px;border-radius:7px;margin-bottom:9px}',
    '.owask-sev.critical{background:rgba(248,113,113,.16);color:var(--c-crit)}.owask-sev.major{background:rgba(251,191,36,.16);color:var(--c-maj)}.owask-sev.moderate{background:rgba(96,165,250,.16);color:var(--c-mod)}',
    '.owask-find p{font-size:.95rem;color:#d7e2f1;line-height:1.55;margin:0}',
    '.owask-find.HIGH{border-left-color:#f87171}.owask-find.MODERATE{border-left-color:#fbbf24}.owask-find.LOW{border-left-color:#34d399}',
    '.owask-rh1{font-family:"DM Serif Display",Georgia,serif;font-size:clamp(1.7rem,4.6vw,2.5rem);line-height:1.1;letter-spacing:-.01em;margin:2px 0 12px;font-weight:400;color:var(--c-text)}',
    '.owask-rh1 .n{color:#ff7a30}',
    '.owask-rsub{color:var(--c-muted);font-size:1rem;max-width:60ch;margin-bottom:4px}',
    '.owask-rmeta{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0 6px}',
    '.owask-rchip{background:#111a2e;border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:13px 17px;min-width:104px}',
    '.owask-rk{font-size:.63rem;color:#6a7c98;text-transform:uppercase;letter-spacing:.07em;font-weight:700;margin-bottom:3px}',
    '.owask-rv{font-family:"DM Serif Display",serif;font-size:1.5rem;line-height:1;color:var(--c-text)}',
    '.owask-rgrade.g-a .owask-rv,.owask-rgrade.g-b .owask-rv{color:#34d399}.owask-rgrade.g-c .owask-rv{color:#f5a623}.owask-rgrade.g-d .owask-rv,.owask-rgrade.g-f .owask-rv{color:#f87171}',
    '.owask-rbar{height:4px;border-radius:3px;margin-top:9px;background:linear-gradient(90deg,#34d399,#fbbf24,#f87171);position:relative}',
    '.owask-rpin{position:absolute;top:-3px;width:10px;height:10px;border-radius:50%;background:#fff;box-shadow:0 0 0 3px rgba(255,255,255,.15);transform:translateX(-50%)}',
    '.owask-rgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;align-items:start;margin-bottom:2px}',
    '@media (max-width:680px){.owask-rgrid{grid-template-columns:1fr}}',
    '.owask-rtop{display:flex;align-items:center;gap:12px;margin-bottom:8px}',
    '.owask-ric{font-size:1.5rem;line-height:1}',
    '.owask-rti{font-weight:700;font-size:1.12rem;letter-spacing:-.01em;color:var(--c-text)}',
    '.owask-rright{margin-left:auto;text-align:right;flex-shrink:0}',
    '.owask-rlv{font-size:.6rem;font-weight:800;letter-spacing:.05em;text-transform:uppercase;padding:4px 9px;border-radius:50px;display:inline-block}',
    '.owask-rlv.HIGH{background:rgba(248,113,113,.16);color:#f87171}.owask-rlv.MODERATE{background:rgba(251,191,36,.15);color:#fbbf24}.owask-rlv.LOW{background:rgba(52,211,153,.15);color:#34d399}',
    '.owask-rcost{font-size:.82rem;color:var(--c-muted);margin-top:5px;font-weight:600}.owask-rcost b{color:var(--c-text);font-weight:700}',
    '.owask-rde{font-size:.93rem;color:var(--c-muted);margin-bottom:11px;line-height:1.55}',
    '.owask-rwhy{display:flex;gap:9px;font-size:.88rem;color:#f3cd96;background:rgba(245,166,35,.07);border:1px solid rgba(245,166,35,.2);border-radius:11px;padding:11px 13px;line-height:1.5}',
    '.owask-rwhylbl{color:#f5a623;font-weight:800;white-space:nowrap;text-transform:uppercase;font-size:.67rem;letter-spacing:.05em;padding-top:2px}',
    '.owask-rcta{margin:20px 0 6px;background:linear-gradient(135deg,rgba(249,115,22,.12),rgba(245,158,11,.07));border:1px solid rgba(245,158,11,.3);border-radius:18px;padding:24px;text-align:center}',
    '.owask-rcta .ey{font-size:.66rem;letter-spacing:.14em;text-transform:uppercase;color:#f5a623;font-weight:800;margin-bottom:10px}',
    '.owask-rcta h4{font-family:"DM Serif Display",serif;font-size:1.35rem;font-weight:400;margin:0 0 9px;color:var(--c-text)}',
    '.owask-rcta p{color:var(--c-muted);font-size:.93rem;max-width:54ch;margin:0 auto 16px}',
    '.owask-rcta .rchips{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:18px}',
    '.owask-rcta .rchip{font-size:.8rem;color:var(--c-muted);background:rgba(255,255,255,.04);border:1px solid var(--c-line2);border-radius:50px;padding:7px 13px}',
    '.owask-rcta a{display:inline-block;background:linear-gradient(135deg,var(--c-orange),var(--c-amber));color:#1a1205;font-weight:800;padding:13px 26px;border-radius:12px;text-decoration:none}',
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

  var _LVL = { critical: 'HIGH', major: 'MODERATE', moderate: 'LOW' };
  function _gradePct(g) {
    return { A: 12, B: 32, C: 52, D: 74, F: 92 }[(g || 'C').toUpperCase()] || 52;
  }
  function _cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }

  // Render the full report shell (exposure hero -> stat boxes -> rich two-column
  // cards -> conversion CTA). Used by the no-login on-ramp when opts.report is set.
  function renderReport(root, opts) {
    var rep = opts.report || {};
    var exposure = Number(rep.exposure || 0);
    var hasPrice = exposure > 0;
    var grade = (rep.grade || '').toString().toUpperCase().slice(0, 1) || 'C';
    var findings = opts.findings || [];

    var h1 = el('div', 'owask-rh1');
    if (hasPrice) {
      h1.innerHTML = 'We found <span class="n">~$' + exposure.toLocaleString() +
        '</span> in likely repair exposure.';
    } else {
      h1.innerHTML = 'This one is worth a <span class="n">closer look</span> before you offer.';
    }
    root.appendChild(h1);
    if (opts.summary) { root.appendChild(el('div', 'owask-rsub', escapeHtml(opts.summary))); }

    var meta = el('div', 'owask-rmeta');
    var gradeChip = el('div', 'owask-rchip owask-rgrade g-' + grade.toLowerCase());
    gradeChip.innerHTML = '<div class="owask-rk">Repair grade</div>' +
      '<div class="owask-rv">' + escapeHtml(grade) + '</div>' +
      '<div class="owask-rbar"><div class="owask-rpin" style="left:' + _gradePct(grade) + '%"></div></div>';
    meta.appendChild(gradeChip);
    var cCount = el('div', 'owask-rchip');
    cCount.innerHTML = '<div class="owask-rk">Items found</div><div class="owask-rv">' + findings.length + '</div>';
    meta.appendChild(cCount);
    if (hasPrice) {
      var cExp = el('div', 'owask-rchip');
      cExp.innerHTML = '<div class="owask-rk">Est. exposure</div><div class="owask-rv">$' + exposure.toLocaleString() + '</div>';
      meta.appendChild(cExp);
    }
    root.appendChild(meta);

    root.appendChild(el('div', 'owask-fh', 'What your document reveals'));
    var grid = el('div', 'owask-rgrid');
    findings.forEach(function (f) {
      var sev = (f.severity || 'moderate');
      var lvl = _LVL[sev] || 'MODERATE';
      var card = el('div', 'owask-find ' + lvl);
      var costHtml = f.cost ? '<div class="owask-rcost">Est. <b>$' + Number(f.cost).toLocaleString() + '</b></div>' : '';
      card.innerHTML =
        '<div class="owask-rtop">' +
          '<span class="owask-ric">' + escapeHtml(f.icon || '\u26a0\ufe0f') + '</span>' +
          '<span class="owask-rti">' + escapeHtml(f.title || '') + '</span>' +
          '<span class="owask-rright"><span class="owask-rlv ' + lvl + '">' + escapeHtml(_cap(sev)) + '</span>' + costHtml + '</span>' +
        '</div>' +
        (f.detail ? '<div class="owask-rde">' + escapeHtml(f.detail) + '</div>' : '') +
        (f.why ? '<div class="owask-rwhy"><span class="owask-rwhylbl">Why it matters</span><span>' + escapeHtml(f.why) + '</span></div>' : '');
      grid.appendChild(card);
    });
    root.appendChild(grid);

    if (opts.reportCta) {
      var c = opts.reportCta;
      var chips = (c.chips || []).map(function (x) { return '<span class="rchip">' + escapeHtml(x) + '</span>'; }).join('');
      var cta = el('div', 'owask-rcta');
      cta.innerHTML =
        '<div class="ey">' + escapeHtml(c.eyebrow || 'Free first read') + '</div>' +
        '<h4>' + escapeHtml(c.title || '') + '</h4>' +
        '<p>' + escapeHtml(c.body || '') + '</p>' +
        (chips ? '<div class="rchips">' + chips + '</div>' : '') +
        '<a href="' + escapeHtml(c.href || '/analyze') + '">' + escapeHtml(c.text || 'Get my free full analysis \u2192') + '</a>';
      root.appendChild(cta);
    }
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

    // Rich report shell (on-ramp) OR the simple findings teaser.
    if (opts.report) {
      renderReport(root, opts);
    } else {
      if (opts.summary) {
        root.appendChild(el('div', 'owask-summary', escapeHtml(opts.summary)));
      }
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
    }

    // reportOnly: render just the report shell (no chat). The chat lives in a
    // separate mount (e.g. a docked Scout rail).
    if (opts.reportOnly) {
      return { root: root };
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

  // Reusable Scout rail: the single source of truth for how Scout attaches to a
  // report. Scout is NEVER inline in a report — it always sits as a floating
  // bottom-right launcher ("Ask Scout about this") that opens a docked rail. Any
  // report surface calls OfferWiseAsk.mountRail(chatOpts); the report itself
  // renders inline separately (e.g. via mount({...,reportOnly:true})).
  function ensureRailStyles() {
    if (document.getElementById('owask-rail-styles')) { return; }
    var css = [
      ".owsr-launch{position:fixed;right:22px;bottom:22px;z-index:99996;display:flex;align-items:center;gap:8px;background:linear-gradient(135deg,#ff7a30,#f59e0b);color:#1a1205;font-weight:800;border:0;border-radius:50px;padding:13px 20px;cursor:pointer;font-family:'DM Sans',-apple-system,sans-serif;font-size:.95rem;box-shadow:0 10px 30px -8px rgba(245,158,11,.6)}",
      "body.owsr-open .owsr-launch{display:none}",
      ".owsr-rail{position:fixed;top:0;right:0;height:100vh;width:392px;max-width:100vw;z-index:99997;background:#101d33;border-left:1px solid rgba(255,255,255,.13);box-shadow:-24px 0 60px -30px rgba(0,0,0,.8);transform:translateX(103%);transition:transform .3s ease;display:flex;flex-direction:column}",
      "body.owsr-open .owsr-rail{transform:none}",
      ".owsr-hd{display:flex;align-items:center;justify-content:space-between;padding:15px 16px 11px;border-bottom:1px solid rgba(255,255,255,.08);font-family:'DM Sans',-apple-system,sans-serif}",
      ".owsr-hd b{font-family:'DM Serif Display',Georgia,serif;font-size:1.18rem;color:#eef3fb;display:block;line-height:1.1}",
      ".owsr-sub{font-size:.72rem;color:#6b7c97}",
      ".owsr-x{background:none;border:0;color:#9fb0c9;font-size:1.7rem;line-height:1;cursor:pointer;padding:0 4px}",
      ".owsr-x:hover{color:#eef3fb}",
      ".owsr-mount{flex:1;overflow:auto;padding:14px 14px 20px}",
      "@media(min-width:961px){body{transition:padding-right .3s ease}body.owsr-open{padding-right:392px}}",
      "@media(max-width:960px){.owsr-rail{width:100vw}}"
    ].join("");
    var st = document.createElement('style');
    st.id = 'owask-rail-styles'; st.textContent = css;
    document.head.appendChild(st);
  }

  function mountRail(opts) {
    opts = opts || {};
    ensureRailStyles();
    // Single rail per page; remount swaps the chat for a new report.
    var inst = window.__owAskRail;
    if (!inst) {
      var launch = document.createElement('button');
      launch.type = 'button'; launch.className = 'owsr-launch';
      var rail = document.createElement('div'); rail.className = 'owsr-rail';
      rail.innerHTML = '<div class="owsr-hd"><div><b></b><span class="owsr-sub"></span></div>' +
        '<button class="owsr-x" type="button" aria-label="Close">\u00D7</button></div>' +
        '<div class="owsr-mount"></div>';
      document.body.appendChild(launch);
      document.body.appendChild(rail);
      var mountEl = rail.querySelector('.owsr-mount');
      inst = {
        launch: launch, rail: rail, mountEl: mountEl,
        titleEl: rail.querySelector('.owsr-hd b'),
        subEl: rail.querySelector('.owsr-sub'),
        chatOpts: null, mounted: false,
        open: function () {
          if (!inst.mounted && inst.chatOpts) {
            var co = {}; for (var k in inst.chatOpts) { co[k] = inst.chatOpts[k]; }
            co.el = mountEl; co.report = null; co.reportCta = null; co.reportOnly = false;
            mount(co); inst.mounted = true;
          }
          document.body.classList.add('owsr-open');
        },
        close: function () { document.body.classList.remove('owsr-open'); },
        show: function () { inst.launch.style.display = ''; },
        hide: function () { inst.launch.style.display = 'none'; document.body.classList.remove('owsr-open'); }
      };
      launch.addEventListener('click', inst.open);
      rail.querySelector('.owsr-x').addEventListener('click', inst.close);
      window.__owAskRail = inst;
    }
    // Default label honors the rule: always "Ask Scout about this".
    inst.launch.innerHTML = '\uD83D\uDCAC ' + escapeHtml(opts.launchLabel || 'Ask Scout about this');
    inst.titleEl.textContent = opts.railTitle || (opts.assistantName || 'Scout');
    inst.subEl.textContent = opts.railSubtitle || 'Your OfferWise guide';
    inst.chatOpts = opts;
    inst.mounted = false;          // new report => remount chat on next open
    inst.mountEl.innerHTML = '';
    inst.launch.style.display = '';
    return inst;
  }

  window.OfferWiseAsk = { mount: mount, mountRail: mountRail };
})();
