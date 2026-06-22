#!/usr/bin/env node
/*
 * Push-time guard against the Babel white-screen class of regression.
 *
 *   1. static/app.html must PIN @babel/standalone to an exact version. The
 *      unpinned CDN URL is precisely what let Babel jump to v8 and white-screen
 *      prod (the compiled JSX gained an injected `import`). This fails the build
 *      if the pin is ever removed.
 *   2. The inline JSX must compile cleanly under the classic runtime (which is
 *      what the pinned Babel uses) — catches a syntax error before it ships.
 *
 * Pairs with scripts/smoke_test.py: this catches code-side breaks at push time;
 * the scheduled smoke catches external/runtime breaks against live prod.
 */
const fs = require('fs');
const path = require('path');

const appHtml = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.html'), 'utf8');
let failed = false;
const fail = (m) => { console.error('  \u2717 ' + m); failed = true; };
const ok = (m) => console.log('  \u2713 ' + m);

// 1) Babel must be pinned to an exact version.
const pinned = /unpkg\.com\/@babel\/standalone@\d+\.\d+\.\d+\/babel\.min\.js/.test(appHtml);
const unpinned = /unpkg\.com\/@babel\/standalone\/babel\.min\.js/.test(appHtml);
if (unpinned || !pinned) {
  fail('@babel/standalone is NOT pinned to an exact version in static/app.html ' +
       '(an unpinned CDN URL caused the Babel-8 white-screen).');
} else {
  ok('@babel/standalone pinned to ' + appHtml.match(/@babel\/standalone@(\d+\.\d+\.\d+)/)[1]);
}

// 2) Inline JSX must compile cleanly (classic runtime = no injected import).
const m = appHtml.match(/<script type="text\/babel">([\s\S]*?)<\/script>/);
if (!m) {
  fail('no <script type="text/babel"> block found in static/app.html');
} else {
  try {
    const babel = require('@babel/core');
    const out = babel.transformSync(m[1], {
      presets: [['@babel/preset-react', { runtime: 'classic' }]],
      filename: 'app.jsx',
      compact: false,
    }).code;
    if (/from\s+["']react\/jsx-runtime/.test(out)) {
      fail('compiled output injects a react/jsx-runtime import — would white-screen as a classic script.');
    } else {
      ok('inline JSX compiles cleanly (classic runtime, no injected import).');
    }
  } catch (e) {
    fail('inline JSX failed to compile: ' + String(e.message || e).split('\n')[0]);
  }
}

if (failed) {
  console.error('\u2717 frontend check FAILED');
  process.exit(1);
}
console.log('\u2713 frontend check passed');
