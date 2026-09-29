// Gate for style.css: every text pair must clear WCAG AA, and every token the gate
// checks must exist. Reading the tokens out of the stylesheet (rather than copying the
// hex values here) is the point — a colour edited in the CSS is checked, and a token
// renamed can only fail, never silently stop being checked.
//
// Run: node test/contrast.test.js

const fs = require('fs');
const path = require('path');

const CSS = fs.readFileSync(path.join(__dirname, '..', 'style.css'), 'utf8');

// A token in this list that cannot be found is a failure, not a skip: it means the
// stylesheet was renamed or restructured and the pairs below would quietly pass.
const NEEDED = [
  'bg', 'surface', 'card', 'sunken', 'border-strong',
  'fg', 'muted',
  'accent', 'accent-ink', 'accent-hover',
  'focus', 'err', 'spell', 'style', 'ok',
  'mark-grammar-bg', 'mark-grammar-ink',
  'mark-spelling-bg', 'mark-spelling-ink',
  'mark-style-bg', 'mark-style-ink',
  'mark-typography-bg', 'mark-typography-ink',
];

// [foreground, background, minimum]. 4.5 is AA for body text; 3.0 covers the focus ring
// and the semantic colours used as small bold marks. Borders are checked at 1.4 only:
// WCAG 1.4.11 asks 3:1 for control boundaries, and a 3:1 border on white is near-black,
// which reads as heavy on a surface like this — a deliberate, named tradeoff. The focus
// ring carries the affordance instead.
const PAIRS = [
  ['fg', 'bg', 4.5], ['fg', 'surface', 4.5], ['fg', 'card', 4.5], ['fg', 'sunken', 4.5],
  ['muted', 'bg', 4.5], ['muted', 'surface', 4.5], ['muted', 'card', 4.5], ['muted', 'sunken', 4.5],
  ['accent-ink', 'accent', 4.5], ['accent-ink', 'accent-hover', 4.5],
  ['ok', 'card', 4.5], ['err', 'card', 4.5], ['spell', 'card', 4.5], ['style', 'card', 4.5],
  ['focus', 'bg', 3.0], ['focus', 'surface', 3.0], ['focus', 'card', 3.0],
  ['mark-grammar-ink', 'mark-grammar-bg', 4.5],
  ['mark-spelling-ink', 'mark-spelling-bg', 4.5],
  ['mark-style-ink', 'mark-style-bg', 4.5],
  ['mark-typography-ink', 'mark-typography-bg', 4.5],
  ['border-strong', 'card', 1.4], ['border-strong', 'bg', 1.4],
];

// The light scheme is the first :root block; the dark one lives in the media query. Both
// are parsed for real, so "dark mode was dropped" fails here too.
function tokens(block) {
  const out = {};
  const re = /--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g;
  let m;
  while ((m = re.exec(block))) out[m[1]] = m[2];
  return out;
}

function firstRoot(css) {
  const i = css.indexOf(':root');
  if (i < 0) return null;
  const open = css.indexOf('{', i);
  return css.slice(open + 1, css.indexOf('}', open));
}

function darkRoot(css) {
  const i = css.search(/@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)/);
  if (i < 0) return null;
  const j = css.indexOf(':root', i);
  if (j < 0) return null;
  const open = css.indexOf('{', j);
  return css.slice(open + 1, css.indexOf('}', open));
}

function luminance(hex) {
  let h = hex.replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const [r, g, b] = [0, 2, 4].map((i) => {
    const v = parseInt(h.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

let pass = 0;
let fail = 0;

const light = firstRoot(CSS);
if (!light) {
  console.log('  FAIL: no :root block found in style.css');
  process.exit(1);
}
const schemes = { light: tokens(light) };

const dark = darkRoot(CSS);
if (!dark) {
  console.log('  FAIL: no prefers-color-scheme: dark block — dark mode was dropped');
  fail += 1;
} else {
  schemes.dark = tokens(dark);
}

for (const [name, set] of Object.entries(schemes)) {
  // Tokens that are declared once (shared across schemes) live in the light block.
  const merged = Object.assign({}, schemes.light, set);
  for (const token of NEEDED) {
    if (!merged[token]) {
      console.log('  FAIL: [%s] --%s is missing (renamed? then this gate must be updated)', name, token);
      fail += 1;
    }
  }
  for (const [fg, bg, min] of PAIRS) {
    if (!merged[fg] || !merged[bg]) continue; // reported above as missing
    const r = ratio(merged[fg], merged[bg]);
    const ok = r >= min;
    if (ok) pass += 1;
    else fail += 1;
    if (!ok) {
      console.log('  FAIL: [%s] %s on %s = %s:1, needs %s:1 (%s on %s)',
        name, fg, bg, r.toFixed(2), min.toFixed(1), merged[fg], merged[bg]);
    }
  }
  console.log('  %s: %d pairs checked', name, PAIRS.length);
}

console.log('contrast: %d assertions - %s', pass + fail, fail ? 'FAILED' : 'passed');
process.exit(fail ? 1 : 0);
