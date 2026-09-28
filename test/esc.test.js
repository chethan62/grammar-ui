// The one security-relevant function in this UI: every server value lands inside an
// HTML template, most of them inside attributes (title="…"), and harper's messages
// quote the user's own words. textContent→innerHTML leaves double quotes alone, which
// is how a message containing a quote closed the attribute and injected the rest as
// markup. Run: node test/esc.test.js
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

// Pull esc() out of the browser file by brace matching, so the test needs no DOM and
// the UI keeps needing no build step.
const start = src.indexOf("function esc(s){");
assert.notStrictEqual(start, -1, "esc() is gone — did the UI stop escaping server values?");
let depth = 0, end = -1;
for (let i = src.indexOf("{", start); i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}" && --depth === 0) { end = i + 1; break; }
}
const esc = eval("(" + src.slice(start, end) + ")"); // eval is the point: the string is app.js's own
// source, read from this repo a few lines up — no external input, and the thing being
// evaluated is exactly the code under test.

assert.strictEqual(esc('a"b'), "a&quot;b", "a double quote must not end an attribute");
assert.strictEqual(esc("it's"), "it&#39;s", "a single quote must not end an attribute");
assert.strictEqual(esc("<b>x</b>"), "&lt;b&gt;x&lt;/b&gt;", "tags must not survive");
assert.strictEqual(esc("a & b"), "a &amp; b", "ampersands must be escaped first");
assert.strictEqual(esc("&lt;"), "&amp;lt;", "already-escaped text must not double-encode");
assert.strictEqual(esc(42), "42", "non-strings must not throw");

// The bug this exists for: a real harper message, as it reaches the template.
const message = 'Wordy phrase "In order to": consider "To"';
assert.ok(!esc(message).includes('"'), "no raw quote may reach an attribute value");

console.log("esc(): 6 assertions passed");
