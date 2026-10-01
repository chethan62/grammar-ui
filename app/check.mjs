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
