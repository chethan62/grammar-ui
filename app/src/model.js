// The finding model, and it is pure: what the engine sent turned into rows, which finding is under the
// caret, which sentence one sits in, every suggestion applied in one pass, the debounce bands. No DOM and
// no fetch, so `check.mjs` imports these functions directly instead of slicing them out of a bigger file —
// which is what it used to do, by brace matching, and what broke whenever anything was reformatted.

// The engine answers LanguageTool's shape; the UI wants a list it can act on. Offsets are UTF-16 code
// units by contract, so they index this string directly — `after` is what replaces `before`.
export function findingsFrom(body, text) {
  return (body.matches || []).map((match) => {
    const offset = Number(match.offset) || 0;
    const length = Number(match.length) || 0;
    const replacements = match.replacements || [];
    // A replacement is an object with a `value` — LanguageTool's shape, and what the engine sends. Reading
    // it as a string produced "Use [object Object]" on every Apply button, which no unit test caught because
    // the fixture had been written from the assumption rather than from a real response.
    // Every alternative the engine sent, not just the first: for `misspeled` harper offers misspelled,
    // misspell and misspells, and for `wurd` its first pick is "ward" while the right one sits third. The
    // first is kept as `after` so a finding with no suggestions still reads as having none.
    const alts = replacements
      .map((r) => (typeof r === "string" ? r : (r && r.value)))
      .filter((v) => v);
    const after = alts.length ? alts[0] : null;
    // The category is what the report groups by. The engine labels every match (STYLE, GRAMMAR, MISC…), so
    // a style report is a grouping of the findings already on screen — not another request, and not a rule
    // list of our own that could drift from the engine's.
    const rule = match.rule || {};
    return {
      message: match.message || "Finding",
      rule: rule.id || "",
      category: (rule.category && (rule.category.name || rule.category.id)) || "Other",
      before: text.slice(offset, offset + length),
      after,
      alts,
      offset,
      length,
    };
  });
}

// Bands, not a formula: a formula needs its reasoning carried around, and a band is one sentence. A slow
// engine is given a LONGER wait, not a shorter one — the way to make a slow engine feel worse is to hand it
// more requests. These are grammar_core.debounce_ms, measured for this engine (0-1 ms warm, 49 ms on the
// first call): a debounce a thousand times the thing it waits for is not patience, it is the whole latency.
// `null` means nothing has been measured yet, which is not the same as fast.
export function debounceMs(lastMs) {
  if (lastMs === null) return 600;
  if (lastMs < 40) return 300;
  if (lastMs < 250) return 900;
  return 1500;
}

export const state = { findings: [], lastMs: null, timer: null, seq: 0, filter: "", partial: false, stats: null, undo: null, provider: null };

// What the list is showing. The report's counts double as a filter, so the list follows the last count
// pressed. The caret strip does not — it is about where the cursor is, not about what is being browsed.
export function drawn() {
  return state.filter ? state.findings.filter((f) => f.category === state.filter) : state.findings;
}

// The finding the caret or the selection is inside. selectionStart is the same UTF-16 index the engine's
// offsets use, so this is a comparison and not a conversion — and both ends are inclusive, because clicking
// a finding puts the selection exactly on its span, so the caret lands on the finding's own offset. A strict
// comparison hid the strip in the one case it exists for: the row you just clicked.
export function findingAt(findings, caret) {
  if (!findings.length || typeof caret !== "number") return null;
  return findings.find((f) => f.length && caret >= f.offset && caret <= f.offset + f.length) || null;
}

// The sentence a finding sits in, as [start, stop). A sentence ends at terminal punctuation or a line
// break, so the scan runs outward from the finding's own offset instead of splitting the whole draft and
// looking the finding up in it — one pass, no index bookkeeping. `end.length`, not a hardcoded 2: the
// separators are not all the same width, and a newline is one character.
export function sentenceRange(text, offset) {
  const before = text.slice(0, offset);
  let start = 0;
  for (const end of [". ", "! ", "? ", ".\n", "!\n", "?\n", "\n"]) {
    const at = before.lastIndexOf(end);
    if (at >= 0) start = Math.max(start, at + end.length);
  }
  const at = text.slice(offset).search(/[.!?](\s|$)/);
  return [start, at < 0 ? text.length : offset + at + 1];
}

// Every suggestion in one pass, right to left so an edit cannot move the offsets of the edits still to
// come. Two findings can overlap — a phrase rule whose span contains a misspelled word — and writing both
// over the same characters would duplicate or drop text, so the first one kept wins and the other is
// skipped. A finding with no suggestion is not a failure: there is simply nothing to apply for it.
export function applyAll(text, findings) {
  const edits = findings.filter((f) => f.after && f.length > 0).sort((a, b) => b.offset - a.offset);
  const used = [];
  let out = text;
  let applied = 0;
  for (const f of edits) {
    if (used.some((u) => f.offset < u.offset + u.length && u.offset < f.offset + f.length)) continue;
    used.push(f);
    out = out.slice(0, f.offset) + f.after + out.slice(f.offset + f.length);
    applied += 1;
  }
  return { text: out, applied, skipped: edits.length - applied };
}
