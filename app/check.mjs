// The one thing about this app a green gate would otherwise not notice: it gives up on an engine that
// accepts a connection and never answers. `fetch` has no default timeout, so the guard is a single line
// — and a single line is exactly what disappears in a refactor.
//
// `call()` is pulled out of src/main.js by brace matching, the way the old browser test pulled `esc()`
// out of app.js: the app's own source runs here, not a copy of it that could drift.

import { readFile } from "node:fs/promises";
import { createServer } from "node:net";

const source = await readFile(new URL("./src/main.js", import.meta.url), "utf8");
const start = source.indexOf("async function call(");
if (start < 0) throw new Error("call() is gone from src/main.js");
const end = source.indexOf("\n}\n", start) + 3;
const body = source.slice(start, end);
if (!body.includes("AbortSignal.timeout")) {
  throw new Error("call() no longer sets a timeout — a hung engine would freeze the window");
}

// A socket that accepts and holds: the shape of the hang this defends against, and the same fixture the
// manual check used (curl against it exits 28).
const hole = createServer((socket) => socket.on("data", () => {}));
await new Promise((resolve) => hole.listen(0, "127.0.0.1", resolve));
const port = hole.address().port;

// `new Function` on interpolated text is a code-injection shape, and here the text is this repo's own
// src/main.js — the subject of the test. Anyone who can change that file can already run anything this
// app can; there is no boundary being crossed, and there is no way to run a function that only exists
// as source without evaluating it.
const call = new Function("ENGINE", `${body}\nreturn call;`)(`http://127.0.0.1:${port}`);
const began = Date.now();
try {
  await call("/v1/ai");
  throw new Error("call() returned against a socket that never answers");
} catch (error) {
  const took = Date.now() - began;
  if (error.name !== "TimeoutError") throw new Error(`expected TimeoutError, got ${error.name}: ${error.message}`);
  if (took < 4000 || took > 9000) throw new Error(`gave up after ${took} ms rather than ~6000`);
  console.log(`  app: gives up on a hung engine after ${took} ms (TimeoutError)`);
} finally {
  hole.close();
}

// --- the finding model ------------------------------------------------------------------------------
// Same trick as call(): pull the app's own function out by brace matching rather than copying it.
const mStart = source.indexOf("function findingsFrom(");
if (mStart < 0) throw new Error("findingsFrom() is gone from src/main.js");
const mBody = source.slice(mStart, source.indexOf("\n}\n", mStart) + 3);
const findingsFrom = new Function(`${mBody}\nreturn findingsFrom;`)();

// The emoji matters: it is two UTF-16 units, so an offset counted in code units still slices this string
// directly. That is the engine's own guarantee, and the reason no offset arithmetic appears in the app.
const emojiText = "An emoji 🙂 then teh and teh again";
if (emojiText.slice(17, 20) !== "teh") throw new Error("offsets do not slice the text directly");

const shape = {
  matches: [
    { message: "Possible typo", offset: 17, length: 3, replacements: ["the"], rule: { id: "MORFOLOGIK_RULE_EN_US" } },
    { message: "Possible typo", offset: 25, length: 3, replacements: [], rule: { id: "MORFOLOGIK_RULE_EN_US" } },
  ],
};
const modelRows = findingsFrom(shape, emojiText);
if (modelRows.length !== 2) throw new Error(`expected 2 rows, got ${modelRows.length}`);
if (modelRows[0].before !== "teh" || modelRows[0].after !== "the") throw new Error("row 0 text is wrong");
if (modelRows[1].after !== null) throw new Error("a finding with no replacements must have after === null");
if (findingsFrom({}, emojiText).length !== 0) throw new Error("a response with no matches must be no rows");
if (findingsFrom({ matches: [{ message: "x", offset: 9999, length: 4, replacements: [] }] }, emojiText).length !== 1) {
  throw new Error("an out-of-range offset must still be a row, not a crash");
}
console.log("  app: the finding model holds (UTF-16 offsets, empty replacements, out-of-range)");
