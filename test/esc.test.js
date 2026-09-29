// The two functions in this UI worth testing on their own: esc(), because every
// server value lands inside an HTML template (most of them inside attributes, and
// harper's messages quote the user's own words), and normalizeApi(), because the
// API field is free text and "localhost:8875" parses as the scheme "localhost:" and
// cannot be fetched at all. Run: node test/esc.test.js
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

// Pull a function out of the browser file by brace matching, so the test needs no DOM
// and the UI keeps needing no build step. eval is the point: the string is app.js's
// own source, read from this repo a few lines up — no external input, and the thing
// being evaluated is exactly the code under test.
function extract(signature) {
  const start = src.indexOf(signature);
  assert.notStrictEqual(start, -1, `${signature} is gone from app.js`);
  let depth = 0;
  for (let i = src.indexOf("{", start); i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return eval("(" + src.slice(start, i + 1) + ")");
  }
  assert.fail(`${signature} has unbalanced braces`);
}

const esc = extract("function esc(s){");

assert.strictEqual(esc('a"b'), "a&quot;b", "a double quote must not end an attribute");
assert.strictEqual(esc("it's"), "it&#39;s", "a single quote must not end an attribute");
assert.strictEqual(esc("<b>x</b>"), "&lt;b&gt;x&lt;/b&gt;", "tags must not survive");
assert.strictEqual(esc("a & b"), "a &amp; b", "ampersands must be escaped first");
assert.strictEqual(esc("&lt;"), "&amp;lt;", "already-escaped text must not double-encode");
assert.strictEqual(esc(42), "42", "non-strings must not throw");

// The bug this exists for: a real harper message, as it reaches the template.
const message = 'Wordy phrase "In order to": consider "To"';
assert.ok(!esc(message).includes('"'), "no raw quote may reach an attribute value");

const normalizeApi = extract("function normalizeApi(v){");

assert.strictEqual(normalizeApi("localhost:8875"), "http://localhost:8875", "a missing scheme is added");
assert.strictEqual(normalizeApi("  http://localhost:8875  "), "http://localhost:8875", "it trims");
assert.strictEqual(normalizeApi("https://grammar.example.com"), "https://grammar.example.com", "https survives");
assert.strictEqual(normalizeApi("http://localhost:8875/"), "http://localhost:8875/", "the slash is not mangled here (api() strips it)");
assert.strictEqual(normalizeApi(""), "", "empty stays empty, so the stored default is used");

console.log("esc(): 7 assertions, normalizeApi(): 5 assertions — passed");
