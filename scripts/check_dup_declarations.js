#!/usr/bin/env node
/*
 * check_dup_declarations.js — v5.89.295
 *
 * Catches the class of bug that white-screened analysis TWICE (v5.89.293/294):
 * a duplicate lexical declaration (`let`/`const`) of the same name in the SAME
 * block scope, e.g.
 *
 *     const progressMessages = [ ... ];       // line ~4049
 *     ...
 *     const progressMessages = window.__x || progressMessages;   // line ~4119  <-- BUG
 *
 * This is valid JavaScript *syntax* (so scripts/check_jsx.js compiles it fine) but
 * a real runtime error the moment the enclosing branch executes — and the branch
 * here only runs on the analysis retry/refresh path, which no test exercised. A
 * duplicate `const`/`let` in one scope is ALWAYS a bug, so we can flag it statically
 * with zero false positives.
 *
 * Also flags a self-referential initializer (`const x = ... || x`) in the same decl,
 * which is the other half of what shipped.
 *
 * Parses the inline JSX from static/app.html with the same classic runtime the app
 * uses. Exits non-zero on any finding. Pairs with check_jsx.js (compiles) and
 * check_frontend.js (Babel pin).
 */
const fs = require('fs');
const path = require('path');
const babel = require('@babel/core');

const APP = process.env.OW_APP_HTML_OVERRIDE || path.join(__dirname, '..', 'static', 'app.html');
const html = fs.readFileSync(APP, 'utf8');

// Extract the largest inline <script type="text/babel"> block (the app).
function extractBabelScript(src) {
  const re = /<script[^>]*type=["']text\/babel["'][^>]*>([\s\S]*?)<\/script>/gi;
  let m, best = '';
  while ((m = re.exec(src))) {
    if (m[1].length > best.length) best = m[1];
  }
  return best;
}

const code = extractBabelScript(html);
if (!code || code.length < 500) {
  console.error('check_dup_declarations: could not extract the inline JSX block.');
  process.exit(2);
}

let ast;
try {
  ast = babel.parse(code, {
    presets: [[require('@babel/preset-react'), { runtime: 'classic' }]],
    parserOpts: { errorRecovery: false },
    filename: 'app.inline.jsx',
    babelrc: false,
    configFile: false,
  });
} catch (e) {
  console.error('check_dup_declarations: parse failed (check_jsx will report details): ' + e.message);
  process.exit(2);
}

const t = require('@babel/types');
const findings = [];

// A "lexical block scope" for our purpose: BlockStatement, Program, function bodies.
// We collect let/const declarators DIRECTLY in each block (not nested blocks/functions)
// and flag any name declared more than once in the same block.
function scanBlock(bodyNodes, label) {
  const seen = new Map(); // name -> first line
  for (const node of bodyNodes) {
    if (!t.isVariableDeclaration(node)) continue;
    if (node.kind !== 'let' && node.kind !== 'const') continue;
    for (const d of node.declarations) {
      // simple identifier targets only (patterns are rare here and lower-risk)
      if (!d.id || d.id.type !== 'Identifier') continue;
      const name = d.id.name;
      const line = (d.loc && d.loc.start.line) || '?';

      // (a) self-reference in initializer: const x = <expr that reads x>. Only a
      // reference at the TOP LEVEL of the initializer is the bug (`const x = a || x`).
      // References inside a nested function in the initializer are legitimate recursion
      // (`const poll = useCallback(() => { ...poll()... })`), as are property accesses
      // (`obj.x`) and object keys (`{x:1}`) — the common `const x = obj.x || []` idiom.
      if (d.init) {
        let selfRef = false;
        babel.traverse(
          t.file(t.program([t.expressionStatement(d.init)])),
          {
            Function(p) { p.skip(); },   // do not descend into nested functions
            Identifier(p) {
              if (p.node.name !== name) return;
              if (p.parentPath.isMemberExpression({ property: p.node }) &&
                  !p.parentPath.node.computed) return;
              if (p.parentPath.isOptionalMemberExpression({ property: p.node }) &&
                  !p.parentPath.node.computed) return;
              if (p.parentPath.isObjectProperty({ key: p.node }) &&
                  !p.parentPath.node.computed) return;
              selfRef = true;
            },
          }
        );
        if (selfRef) {
          findings.push(`${label}: '${name}' (line ${line}) references itself in its own ${node.kind} initializer`);
        }
      }

      // (b) duplicate declaration in the same block
      if (seen.has(name)) {
        findings.push(`${label}: '${name}' redeclared with ${node.kind} in the same scope (first at line ${seen.get(name)}, again at line ${line})`);
      } else {
        seen.set(name, line);
      }
    }
  }
}

// Walk the tree; scan the body array of every block-bearing node.
babel.traverse(ast, {
  enter(p) {
    const n = p.node;
    if (t.isBlockStatement(n)) {
      const fn = p.getFunctionParent();
      const fnName =
        (fn && fn.node.id && fn.node.id.name) ||
        (fn && fn.parentPath && fn.parentPath.node && fn.parentPath.node.id && fn.parentPath.node.id.name) ||
        'anonymous';
      scanBlock(n.body, `function ${fnName}, block@line ${(n.loc && n.loc.start.line) || '?'}`);
    } else if (t.isProgram(n)) {
      scanBlock(n.body, 'program (top level)');
    }
  },
});

if (findings.length) {
  console.error('check_dup_declarations: FAILED — duplicate/self-referential lexical declarations:');
  for (const f of findings) console.error('  \u2717 ' + f);
  console.error('\nThis is the bug class that broke analysis in v5.89.293/294. A name may be');
  console.error('declared with let/const only once per scope, and never reference itself.');
  process.exit(1);
}

console.log('check_dup_declarations: OK — no duplicate or self-referential let/const in any scope');
