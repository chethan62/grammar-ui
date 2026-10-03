// The one thing about this app a green gate would otherwise not notice: it gives up on an engine that
// accepts a connection and never answers. `fetch` has no default timeout, so the guard is a single line
// — and a single line is exactly what disappears in a refactor.
//
// Everything the app exposes to a test is imported for real. src/model.js has no DOM and no fetch, and
// src/shell.js reads its engine address lazily rather than at load, so both run here as themselves. They
// used to be reached by slicing their text out of src/main.js and evaluating it, which broke whenever the
// file was laid out differently — and after the file became six modules, that arithmetic had to be re-pointed
// at each one. There is none of it left.
import { findingsFrom, applyAll, debounceMs, findingAt, sentenceRange } from "./src/model.js";
import { readFile } from "node:fs/promises";
import { createServer } from "node:net";

// A socket that accepts and holds: the shape of the hang this defends against, and the same fixture the
// manual check used (curl against it exits 28).
const hole = createServer((socket) => socket.on("data", () => {}));
await new Promise((resolve) => hole.listen(0, "127.0.0.1", resolve));
const port = hole.address().port;

// shell.js asks localStorage where the engine is, so pointing it at a port of this test's own choosing is
// just setting the key the app already reads. The stub and the DOM below have to exist before the import,
// which is why it is a dynamic one — a static import is hoisted above them.
globalThis.localStorage = { getItem: (k) => (k === "grammar-api" ? `http://127.0.0.1:${port}` : null),
                            setItem: () => {}, removeItem: () => {} };
globalThis.document = { getElementById: () => ({ textContent: "", className: "" }) };
const { call } = await import("./src/shell.js");

// The deadline is the point: without a timeout `call()` never settles, so a plain `await` would hang this
// suite rather than fail it — and a test that hangs reads as "still running", not as "the guard is gone".
const began = Date.now();
const deadline = new Promise((resolve) => { const t = setTimeout(() => resolve("hung"), 12000); t.unref?.(); });
const settled = await Promise.race([call("/v1/ai").then(() => "returned", (error) => error), deadline]);
if (settled === "hung") {
  throw new Error("call() never gave up on a socket that accepts and stays silent — the timeout is gone (waited 12 s)");
}
if (settled === "returned") throw new Error("call() returned against a socket that never answers");
if (settled.name !== "TimeoutError") throw new Error(`expected TimeoutError, got ${settled.name}: ${settled.message}`);
const took = Date.now() - began;
if (took < 4000 || took > 9000) throw new Error(`gave up after ${took} ms rather than ~6000`);
hole.close();
console.log(`  app: gives up on a hung engine after ${took} ms (TimeoutError)`);

// --- the finding model ------------------------------------------------------------------------------
// findingsFrom is imported at the top of this file, from src/model.js.

// The emoji matters: it is two UTF-16 units, so an offset counted in code units still slices this string
// directly. That is the engine's own guarantee, and the reason no offset arithmetic appears in the app.
const emojiText = "An emoji 🙂 then teh and teh again";
if (emojiText.slice(17, 20) !== "teh") throw new Error("offsets do not slice the text directly");

const shape = {
  matches: [
    // The engine's real shape: replacements are objects carrying `value`, taken from a live response.
    { message: "Possible typo", offset: 17, length: 3,
      replacements: [{ value: "the" }], rule: { id: "MORFOLOGIK_RULE_EN_US" } },
    { message: "Possible typo", offset: 25, length: 3, replacements: [], rule: { id: "MORFOLOGIK_RULE_EN_US" } },
    // and a plain string, which clients written against other LanguageTool servers send back
    { message: "Possible typo", offset: 25, length: 3,
      replacements: ["they"], rule: { id: "MORFOLOGIK_RULE_EN_US" } },
  ],
};
const modelRows = findingsFrom(shape, emojiText);
if (modelRows.length !== 3) throw new Error(`expected 3 rows, got ${modelRows.length}`);
if (modelRows[0].before !== "teh" || modelRows[0].after !== "the") throw new Error("row 0 text is wrong");
// the rule id decides whether a word-level action is safe to offer at all
if (modelRows[0].rule !== "MORFOLOGIK_RULE_EN_US") throw new Error("the rule id must survive the model");
if (findingsFrom({ matches: [{ message: "x", offset: 0, length: 1, replacements: [] }] }, "a")[0].rule !== "") {
  throw new Error("a finding with no rule must report an empty rule, not undefined");
}
if (modelRows[1].after !== null) throw new Error("a finding with no replacements must have after === null");
// the bug that shipped: an object read as a string renders as "[object Object]" on every button
if (modelRows[0].after !== "the") throw new Error(`replacement object not read: got ${modelRows[0].after}`);
if (modelRows[2].after !== "they") throw new Error(`string replacement not read: got ${modelRows[2].after}`);
// every alternative, not just the first: harper's first pick for "wurd" is "ward" while "word" is third
const many = findingsFrom({ matches: [{ message: "x", offset: 0, length: 3,
  replacements: [{ value: "ward" }, { value: "word" }, { value: "we'd" }] }] }, "wrd");
if (many[0].alts.length !== 3) throw new Error(`expected 3 alternatives, got ${many[0].alts.length}`);
if (many[0].alts.join(",") !== "ward,word,we'd") throw new Error(`alternatives wrong: ${many[0].alts}`);
if (many[0].after !== "ward") throw new Error("after must stay the engine's first pick");
if (findingsFrom({}, emojiText).length !== 0) throw new Error("a response with no matches must be no rows");
if (findingsFrom({ matches: [{ message: "x", offset: 9999, length: 4, replacements: [] }] }, emojiText).length !== 1) {
  throw new Error("an out-of-range offset must still be a row, not a crash");
}
// the category is what the report groups by, so it has to survive the model
const styled = findingsFrom({ matches: [{ message: "x", offset: 0, length: 1, replacements: [],
  rule: { id: "R", category: { id: "STYLE", name: "Style" } } }] }, "a")[0];
if (styled.category !== "Style") throw new Error(`the rule category must survive: got ${styled.category}`);
const uncategorised = findingsFrom({ matches: [{ message: "x", offset: 0, length: 1, replacements: [] }] }, "a")[0];
if (uncategorised.category !== "Other") throw new Error("a finding with no category must land in Other");
console.log("  app: the finding model holds (UTF-16 offsets, empty replacements, out-of-range, category)");

// --- Fix all: every suggestion in one pass ----------------------------------------------------------
// The offset arithmetic is the whole risk: applying left to right moves every offset still to be applied.
// applyAll is the real one from src/model.js, imported above — no copy, and no eval.

const edit = (offset, length, after) => ({ offset, length, after, before: "x", rule: "", category: "Other" });
const two = applyAll("teh wurd here", [edit(0, 3, "the"), edit(4, 4, "word")]);
if (two.text !== "the word here" || two.applied !== 2) throw new Error(`two fixes: ${two.text} (${two.applied})`);
// the engine returns findings in text order; the result must not depend on it
const reversed = applyAll("teh wurd here", [edit(4, 4, "word"), edit(0, 3, "the")]);
if (reversed.text !== two.text) throw new Error("the result must not depend on the order findings arrive in");
// a finding with no suggestion is nothing to apply, not a failure
const none = applyAll("teh wurd", [edit(0, 3, null), edit(4, 4, "word")]);
if (none.text !== "teh word" || none.applied !== 1 || none.skipped !== 0) {
  throw new Error(`no-suggestion row: ${none.text}, applied ${none.applied}, skipped ${none.skipped}`);
}
// an overlapping pair must not write twice over the same characters: one is kept, the other skipped
const over = applyAll("the cat sat", [edit(0, 7, "A"), edit(4, 3, "B")]);
if (over.text !== "the B sat" || over.applied !== 1 || over.skipped !== 1) {
  throw new Error(`overlap: ${JSON.stringify(over.text)}, applied ${over.applied}, skipped ${over.skipped}`);
}
// the bug that shipped once already: offsets are UTF-16 code units, so an emoji before a fix must not
// shift it — the slice is by code unit, and this is the check that says so
const emojiFix = applyAll("🙂 teh", [edit(3, 3, "the")]);
if (emojiFix.text !== "🙂 the") throw new Error(`emoji offset: ${JSON.stringify(emojiFix.text)}`);
console.log("  app: Fix all applies every suggestion in one pass, in either arrival order");

// --- the debounce bands -----------------------------------------------------------------------------
// Ported from grammar_core.debounce_ms, whose docstring explains them. The boundaries are the whole risk
// in a port (39 vs 40, 249 vs 250), so every one of them is asserted, and null is separately asserted
// because "nothing measured yet" is not "fast". debounceMs comes from src/model.js.
for (const [input, want] of [[null, 600], [0, 300], [39, 300], [40, 900], [249, 900], [250, 1500], [10000, 1500]]) {
  const got = debounceMs(input);
  if (got !== want) throw new Error(`debounceMs(${input}) = ${got}, expected ${want}`);
}
console.log("  app: the debounce bands match grammar_core.debounce_ms at every boundary");

// --- the preset contract ------------------------------------------------------------------------------
// The panel builds its provider picker from GET /v1/ai. It once read `p.name`, a field the engine has never
// sent, so every entry rendered as "undefined" and the custom-endpoint preset became unreachable — the bug
// class this file exists for: a client written against an assumed response rather than a real one. This
// checks the fields the panel reads against the fields the API actually sends, and skips (like every other
// live precondition here) when no engine is running.
// The picker lives in src/panels.js now, so the contract is checked against that file's text.
const panels = await readFile(new URL("./src/panels.js", import.meta.url), "utf8");
const readsId = panels.includes("option.value = p.id") && panels.includes("p.label || p.id");
if (!readsId) throw new Error("load() no longer builds the provider picker from the preset's id/label");
if (/\bp\.name\b/.test(panels)) throw new Error("panels.js reads p.name, which GET /v1/ai does not send");

let live = "";
try {
  const answer = await fetch("http://127.0.0.1:8875/v1/ai", { signal: AbortSignal.timeout(4000) });
  live = answer.ok ? "ok" : "";
} catch { /* no engine: the shape below cannot be checked, and that is not a failure */ }
if (!live) {
  console.log("  app: the preset contract was not checked (no engine running)");
} else {
  const state = await (await fetch("http://127.0.0.1:8875/v1/ai", { signal: AbortSignal.timeout(4000) })).json();
  if (!Array.isArray(state.presets) || !state.presets.length) throw new Error("GET /v1/ai sent no presets");
  for (const p of state.presets) {
    if (typeof p.id !== "string" || !p.id) throw new Error(`a preset has no id: ${JSON.stringify(p)}`);
    if (typeof p.label !== "string" || !p.label) throw new Error(`preset ${p.id} has no label to show`);
    if (typeof p.url !== "string") throw new Error(`preset ${p.id} has a non-string url`);
  }
  // The custom-endpoint case the panel depends on: a preset with no URL of its own, which is what makes the
  // address field open. If it ever disappears, adding a custom AI is gone, and that should fail loudly here.
  const custom = state.presets.filter((p) => !p.url);
  if (!custom.length) throw new Error("no preset without a url — the custom-endpoint case is gone");
  console.log(`  app: the preset contract holds (${state.presets.length} presets, ${custom.length} custom, ` +
              `labels present, id == what POST accepts)`);
}

// --- which finding is under the caret ---------------------------------------------------------------
// The strip shows the finding at the caret. Clicking a row selects that finding's own span, so the caret
// lands *on* its offset, and the end of the span is a caret position too — both ends inclusive is the whole
// rule. It was a strict comparison until a real browser run showed the strip refusing to appear for the one
// finding it had just been asked about.
// findingAt is the real one from src/model.js, imported at the top.
const spans = [{ offset: 0, length: 3 }, { offset: 10, length: 5 }];
for (const [caret, want] of [[0, spans[0]], [2, spans[0]], [3, spans[0]],
                            [4, null], [10, spans[1]], [13, spans[1]], [15, spans[1]], [16, null]]) {
  if (findingAt(spans, caret) !== want) {
    throw new Error(`findingAt(caret=${caret}) = ${JSON.stringify(findingAt(spans, caret))}, expected ${JSON.stringify(want)}`);
  }
}
if (findingAt([], 3) !== null) throw new Error("no findings, no strip");
if (findingAt(spans, undefined) !== null) throw new Error("no caret, no strip");
if (findingAt([{ offset: 5, length: 0 }], 5) !== null) throw new Error("a zero-length finding has nothing to sit in");
console.log("  app: the caret strip finds the finding under the caret, on its own offset and at its end");

// The sentence a finding sits in is what the model is handed, because a rule that can only flag a sentence
// cannot rewrite it. The boundaries are where this goes wrong — a lastIndexOf that found nothing returns
// -1, and a separator list hardcoding 2 for a newline's width is one off for every line-broken sentence.
// sentenceRange is the real one from src/model.js, imported at the top.
const pair = "The report was written by the team. It was reviewed.";   // 52 chars; sentence 1 ends at 35
for (const [offset, want] of [
  [0, [0, 35]],      // the opening word: nothing behind it, so the sentence starts at 0
  [11, [0, 35]],     // the passive finding's own offset, mid-sentence
  [34, [0, 35]],     // the first sentence's closing period
  [36, [36, 52]],    // the second sentence, one past the space after the period
  [39, [36, 52]],    // inside it, so the start is sentence 2 and not 0
  [51, [36, 52]],    // its closing period, at the end of the draft
]) {
  const got = sentenceRange(pair, offset);
  if (got[0] !== want[0] || got[1] !== want[1]) {
    throw new Error(`sentenceRange(${offset}) = ${JSON.stringify(got)}, expected ${JSON.stringify(want)}`);
  }
}
if (JSON.stringify(sentenceRange("just words", 2)) !== "[0,10]") {
  throw new Error("no punctuation: the whole draft is the sentence");
}
if (JSON.stringify(sentenceRange("one\ntwo", 5)) !== "[4,7]") {
  throw new Error("a newline ends a sentence as much as a period does");
}
// And the row's action and harper's replacements are wired together: a finding harper offers a replacement
// for gets its sentence fixer, one it can only see gets the model. Asserted as a contract on the source the
// way the preset check is, because the alternative is a DOM — which is what the browser run covers, and CI
// has no chromium for. Without this the two drift silently and a row offers "Fix sentence" for a finding
// with nothing to fix, which is the bug this replaced: measured on `The report was written by the team.`,
// where /v2/fix-sentence returned the text byte-identical.
const rowsSrc = await readFile(new URL("./src/rows.js", import.meta.url), "utf8");
const rowStart = rowsSrc.indexOf("export function findingRow(");
if (rowStart < 0) throw new Error("findingRow() is gone from src/rows.js");
const rowBody = rowsSrc.slice(rowStart, rowsSrc.indexOf("\n}\n", rowStart));
if (!rowBody.includes("if (finding.alts.length)")) {
  throw new Error("the row no longer branches on whether harper has a replacement for it");
}
if (!rowBody.includes('sentenceAction.textContent = "Rephrase"')) {
  throw new Error("the row no longer offers the model the findings harper cannot fix");
}
if (!rowBody.includes('state.provider !== "none"')) {
  throw new Error("the row offers Rephrase without checking that a model is configured");
}
console.log("  app: the row's action follows whether harper can fix the finding, model when it cannot");

// --- the layering seam ------------------------------------------------------------------------------
// rows.js and flow.js are deliberately not a cycle: a row's actions reach check() through `recheck`, which
// the wiring points at it once. The failure this guards is the quiet one — an unwired seam that does
// nothing, so a row's action appears to work and silently does not re-check. So it must throw until wired.
//
// rows.js imports shell.js, which reads localStorage as it loads and node has none: the stub is what makes
// the module importable here at all, and it is assigned before the dynamic import for that reason (a static
// one would be hoisted above it).
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
// The namespace, not a destructured copy: `recheck` is an exported `let`, and destructuring reads its value
// once, so a copy pins the stub. The namespace property is the live binding.
const rows = await import("./src/rows.js");
let unwired = null;
try { rows.recheck(); } catch (error) { unwired = error; }
if (!unwired) throw new Error("an unwired recheck() did nothing — a row's action would silently not re-check");
// And once wired, the name rows.js calls is the wired one — that is what the export being a live binding
// buys, and what makes the seam work without either module importing the other.
let landed = 0;
rows.setRecheck(() => { landed += 1; });
rows.recheck();
if (landed !== 1) throw new Error("setRecheck() did not take: the seam still holds the stub, so every row action would throw");
console.log("  app: the recheck seam throws until wired, then is the wired check()");

// --- the window with no engine ----------------------------------------------------------------------
// The failure paths, which nothing above this line ever ran: every harness in this file works against a
// live engine or a socket of its own, so the branch that reports a *dead* one was never executed. That is
// exactly where the last regression hid — panels.js used a name it never imported, so with the engine down
// Settings threw `ReferenceError: ENGINE is not defined` instead of saying "No engine at …", which is the
// one thing that branch exists to say. A green gate did not notice, because the gate never got there.
//
// So this block runs the *whole window* against a `fetch` that always fails: it imports app.js, which both
// registers every listener and boots, then fires each handler and requires that each one reports its
// failure rather than throwing. app.js had no coverage at all before this, and every assertion is on the
// text the branch produced — not on "it did not throw" — so it proves the branch ran.
//
// It has to be last, because it takes over globals.
{
  const recorded = {};
  const listeners = new Map();
  const noop = () => {};
  const el = (id) => {
    const b = { id, dataset: {}, selectedOptions: [], classList: { add: noop, remove: noop },
      addEventListener: (type, fn) => listeners.set(id + ":" + type, fn),
      setAttribute: noop, append: noop, appendChild: noop, replaceChildren: noop, remove: noop,
      focus: noop, setSelectionRange: noop, setRangeText: noop, closest: () => null,
      value: "", hidden: false, disabled: false, className: "", children: [], selectionStart: 0 };
    Object.defineProperty(b, "textContent", { get: () => recorded[id], set: (v) => { recorded[id] = v; } });
    b.parentElement = b;
    return b;
  };
  const cache = new Map();
  // The draft comes from localStorage at boot (app.js reads it and assigns), so that is where to put it —
  // seeding the element instead is overwritten the moment the entry is imported.
  const store = new Map([["grammar-draft", "a draft, so a check and a rewrite reach the engine"]]);
  globalThis.document = { getElementById: (id) => cache.get(id) || (cache.set(id, el(id)), cache.get(id)),
                          createElement: () => el("x"), addEventListener: (t, fn) => listeners.set("document:" + t, fn),
                          activeElement: null };
  globalThis.window = { addEventListener: (t, fn) => listeners.set("window:" + t, fn) };
  globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null),
                              setItem: (k, v) => store.set(k, String(v)),
                              removeItem: (k) => store.delete(k) };
  globalThis.fetch = () => Promise.reject(new TypeError("fetch failed"));

  // Importing the entry registers the listeners and runs the boot, so a missing import anywhere in the
  // wiring surfaces here as a ReferenceError rather than silently at someone's first click.
  await import("./src/app.js");
  if (!/^No engine at http:\/\//.test(recorded.status || "")) {
    throw new Error(`on boot with the engine down the footer said ${JSON.stringify(recorded.status)} — it must name the engine it could not reach`);
  }
  if (recorded.state !== "not answering") {
    throw new Error(`on boot with the engine down the status said ${JSON.stringify(recorded.state)}`);
  }

  // Every listener the wiring is supposed to install. A missing one is a control that does nothing.
  const wired = ["tab-check:click", "provider:change", "add:click", "newWord:keydown",
                 "document:selectionchange", "check:click", "fixall:click", "undo:click",
                 "draft:input", "draft:keydown", "rewrite:click", "save:click", "window:focus"];
  const unwired = wired.filter((k) => !listeners.has(k));
  if (unwired.length) throw new Error(`nothing is listening for: ${unwired.join(", ")}`);

  const fire = async (key, event = {}) => listeners.get(key)({
    preventDefault: noop, target: { closest: () => null }, ...event });

  for (const key of wired) await fire(key);            // none of these may throw
  await fire("save:click");                            // the same handler, checked for what it said
  if (!/^Could not save: /.test(recorded.status || "")) {
    throw new Error(`a save that cannot reach the engine reported ${JSON.stringify(recorded.status)}`);
  }
  cache.get("newWord").value = "zzz";                  // a word to add, so addWord() reaches the engine
  await fire("add:click");
  if (!/^Could not add zzz: /.test(recorded.status || "")) {
    throw new Error(`adding a word with no engine reported ${JSON.stringify(recorded.status)}`);
  }
  await fire("rewrite:click");
  if (!/^Could not rewrite: /.test(recorded.rewriteState || "")) {
    throw new Error(`a rewrite with no engine reported ${JSON.stringify(recorded.rewriteState)}`);
  }
  const { check } = await import("./src/flow.js");
  await check();
  if (!/^Could not check: |^The engine did not answer/.test(recorded.found || "")) {
    throw new Error(`with the engine down a check reported ${JSON.stringify(recorded.found)} — the failure branch did not run`);
  }
  console.log(`  app: with no engine, all ${wired.length} listeners fire and each reports the failure`);
}
