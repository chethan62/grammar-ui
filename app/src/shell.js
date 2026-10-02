// The window and the engine: the DOM shorthand, the live region, which tab is showing, and the one HTTP
// call. A leaf — it imports nothing, so every other module can use it without forming a cycle.

export const ENGINE = localStorage.getItem("grammar-api") || "http://127.0.0.1:8875";

export const $ = (id) => document.getElementById(id);

export const TABS = ["check", "rewrite", "settings"];

export async function call(path, options, ms) {
  // `fetch` has no timeout. Six seconds covers everything the engine answers from its own engine; a model
  // needs longer, so /v2/rewrite passes 30s — measured 0.8-5s locally, and a timeout that fires during a
  // good answer would be worse than no rewrite at all.
  const response = await fetch(ENGINE + path, { signal: AbortSignal.timeout(ms || 6000), ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || (path + " answered " + response.status));
  return body;
}

export function say(text, bad) {
  $("status").textContent = text;
  $("state").textContent = bad ? "not answering" : "working";
  $("dot").className = "dot " + (bad ? "down" : "up");
}

export function showTab(name) {
  // Every tab is a real <button>, so it is reachable by Tab and Enter already; role/aria-selected is what
  // makes it announced as a tab. Arrow-key roving focus is the APG refinement — not built, and not needed
  // for a three-tab window to be usable.
  for (const t of TABS) {
    $("tab-" + t).setAttribute("aria-selected", String(t === name));
    $("panel-" + t).hidden = t !== name;
  }
}
