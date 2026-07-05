#!/usr/bin/env node
/*
 * check_jsx.js — build guard for the in-browser JSX in static/app.html.
 *
 * app.html renders its whole report through a single <script type="text/babel">
 * block compiled in the browser by @babel/standalone. node --check can't parse
 * JSX, so the admin guard (check_html_js.py) doesn't cover it — yet app.html is
 * the most edit-prone, most fragile file in the tree. This guard compiles that
 * block with Babel + preset-react exactly the way the browser would, and FAILS
 * CLOSED on any syntax error, mapping the error back to the real app.html line.
 *
 * It validates SYNTAX (does the JSX parse/compile), which is the class of bug that
 * white-screens the report. It does not run the app.
 *
 * Usage: node scripts/check_jsx.js [static/app.html ...]
 */
const fs = require('fs');
const path = require('path');

let babel;
try {
  babel = require('@babel/core');
  require.resolve('@babel/preset-react');
} catch (e) {
  console.error('check_jsx: FAILED — @babel/core + @babel/preset-react not installed.');
  console.error('  Install them so the build can validate app.html JSX: npm install @babel/core @babel/preset-react');
  process.exit(1);
}

const repoRoot = path.dirname(__dirname);
const targets = process.argv.slice(2);
if (targets.length === 0) targets.push('static/app.html');

const BLOCK_RE = /<script\b[^>]*type=["']text\/babel["'][^>]*>([\s\S]*?)<\/script>/gi;

function lineOfIndex(text, idx) {
  let line = 1;
  for (let i = 0; i < idx && i < text.length; i++) if (text[i] === '\n') line++;
  return line;
}

let failures = 0;
let checkedFiles = 0;
let checkedBlocks = 0;

for (const t of targets) {
  const p = path.isAbsolute(t) ? t : path.join(repoRoot, t);
  if (!fs.existsSync(p)) {
    console.error(`check_jsx: ${t}: file not found`);
    failures++;
    continue;
  }
  checkedFiles++;
  const src = fs.readFileSync(p, 'utf8');
  let m;
  let found = false;
  while ((m = BLOCK_RE.exec(src)) !== null) {
    found = true;
    checkedBlocks++;
    const body = m[1];
    const startLine = lineOfIndex(src, m.index + m[0].indexOf(body));
    try {
      babel.transformSync(body, {
        presets: [require('@babel/preset-react')],
        babelrc: false,
        configFile: false,
        filename: 'app.jsx',
        compact: false,
        sourceType: 'script',
      });
    } catch (err) {
      failures++;
      let msg = err.message || String(err);
      // Babel reports "(lineInBlock:col)" — remap to the real source line.
      const lm = msg.match(/\((\d+):(\d+)\)/);
      let where = '';
      if (lm) {
        const realLine = startLine + parseInt(lm[1], 10) - 1;
        where = `${t}:${realLine} (block starts at line ${startLine})`;
      } else {
        where = `${t} (block starts at line ${startLine})`;
      }
      console.error(`check_jsx: JSX did not compile -> ${where}`);
      console.error('  ' + msg.split('\n').slice(0, 6).join('\n  '));
    }
  }
  if (!found) {
    console.error(`check_jsx: ${t}: no <script type="text/babel"> block found`);
    failures++;
  }
}

if (failures > 0) {
  console.error(`check_jsx: FAILED (${failures} problem(s))`);
  process.exit(1);
}
console.log(`check_jsx: OK — ${checkedFiles} file(s), ${checkedBlocks} JSX block(s) compile cleanly`);
process.exit(0);
