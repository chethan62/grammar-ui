// What the window does with the engine: check, live-check, the report, Fix all, the caret strip's redraw.
// Depends on shell, model and rows; nothing depends on it except the wiring, and that one way.

import { $, call } from "./shell.js";
import { state, findingsFrom, drawn, applyAll, debounceMs } from "./model.js";
import { findingRow, showAtCaret, saveDraft } from "./rows.js";

export function drawFindings(text) {
  const shown = drawn();
  $("findings").replaceChildren(...shown.map(findingRow));
  const partial = state.partial ? " (partial)" : "";
  $("found").textContent = state.findings.length
    ? (state.filter
       ? shown.length + " of " + state.findings.length + " — " + state.filter
       : state.findings.length + (state.findings.length === 1 ? " finding" : " findings") + partial)
    : "Nothing flagged" + partial;
  showAtCaret(text);
  report();
}

export function showFindings(body, text) {
  state.findings = findingsFrom(body, text);
  state.partial = !!(body.warnings && body.warnings.incompleteResults);
  return state.findings.length;
}

export function report() {
  const box = $("report");
  const stats = state.stats || {};
  box.replaceChildren();
  const counts = new Map();
  for (const f of state.findings) counts.set(f.category, (counts.get(f.category) || 0) + 1);
  // A filter with one choice is not a filter, and a chip repeating the count beside it is noise. One
  // category, no chips.
  if (counts.size > 1) {
    const chips = document.createElement("div");
    chips.className = "chips";
    for (const [category, n] of [...counts].sort((a, b) => b[1] - a[1])) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = category + " " + n;
      chip.setAttribute("aria-pressed", String(state.filter === category));
      chip.title = (state.filter === category ? "Stop showing only " : "Show only ") + category;
      chip.addEventListener("click", () => {
        state.filter = state.filter === category ? "" : category;   // one click filters, the next clears
        drawFindings($("draft").value);
      });
      chips.append(chip);
    }
    box.append(chips);
  }
  // Four numbers, then the rest behind a summary. Eight of them in one line is a wall a reader skips; the
  // reading ease and its grade are what anyone acts on, and the others are there for when you want them.
  const line = document.createElement("p");
  line.className = "note";
  line.textContent = stats.words ? [
    stats.words + " words",
    stats.sentences + (stats.sentences === 1 ? " sentence" : " sentences"),
    "reading ease " + stats.fleschReadingEase + " (" + stats.grade + ")",
    stats.readingTime,
  ].join(" · ") : "";
  line.hidden = !stats.words;
  box.append(line);
  if (stats.words) {
    const more = document.createElement("details");
    more.className = "more";
    const summary = document.createElement("summary");
    summary.textContent = "More about this text";
    const detail = document.createElement("p");
    detail.className = "note";
    detail.textContent = [
      "vocabulary variety " + Math.round((stats.uniqueRatio || 0) * 100) + "%",
      "average sentence " + stats.meanSentenceWords + " words",
      "longest sentence " + stats.longestSentenceWords + " words",
      "Flesch–Kincaid grade " + stats.fleschKincaidGrade,
      "Gunning Fog " + stats.gunningFog,
    ].join(" · ");
    more.append(summary, detail);
    box.append(more);
  }
  box.hidden = !state.findings.length && !stats.words;
}

// Fix all, and the one thing that makes it safe to press: the text before the edit is kept, so Undo is one
// click and there is nothing to confirm. Offsets only mean anything against the text they were measured
// on, so this is one pass over the current findings, not a loop that re-checks between edits.
export async function fixAll() {
  const area = $("draft");
  const before = area.value;
  const { text, applied, skipped } = applyAll(before, state.findings);
  if (!applied) { $("announce").textContent = "No finding here carries a suggestion."; return; }
  area.value = text;
  state.undo = before;
  $("undo").hidden = false;
  saveDraft();
  await check();
  $("announce").textContent = applied + (applied === 1 ? " fix applied" : " fixes applied") +
    (skipped ? ", " + skipped + " skipped as overlapping" : "") + ". Undo is available.";
}

export async function liveCheck() {
  const text = $("draft").value;
  clearTimeout(state.timer);
  if (!text.trim()) { state.findings = []; state.stats = null; drawFindings(text); return; }
  state.timer = setTimeout(async () => {
    const mine = ++state.seq;
    const began = performance.now();
    try {
      // Correctness only while typing: the style tier is what an explicit Check asks for, and the engine's
      // README makes the same distinction for editor clients ("never shown hints it did not ask for").
      //
      // Stats come along on this path too. They are the engine's own counters — a millisecond of work beside
      // the check itself — and without them the insights keep describing the text you had a moment ago: a
      // number that quietly stops matching what is in the box. Fetched in parallel and allowed to fail on
      // its own, so a stats hiccup cannot take the findings down with it.
      const [body, stats] = await Promise.all([
        call("/v2/check", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(askFor(text, false)),
        }),
        call("/v2/stats", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        }).catch(() => null),
      ]);
      // ponytail: the superseded request still runs. The engine answers in ~1 ms, so dropping its answer is
      // enough — reach for AbortController if a check ever gets expensive.
      if (mine !== state.seq) return;
      state.lastMs = performance.now() - began;
      state.stats = stats;
      showFindings(body, text);
      drawFindings(text);
    } catch (error) {
      // typing stays quiet: the footer already reports an engine that is not answering
    }
  }, debounceMs(state.lastMs));
}

export function askFor(text, full) {
  const payload = { text };
  if (full) payload.level = "picky";                                  // the explicit ask gets the style tier
  if ($("language").value) payload.language = $("language").value;    // absent = the engine's own dialect
  return payload;
}

export async function check() {
  const text = $("draft").value;
  clearTimeout(state.timer);                                         // a deliberate check outranks a pending one
  state.stats = null;
  if (!text.trim()) {
    state.findings = [];
    drawFindings(text);
    $("announce").textContent = "Nothing to check yet.";
    return;
  }
  $("check").disabled = true;
  state.seq++;                                                       // any in-flight live answer is stale now
  try {
    const body = await call("/v2/check", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(askFor(text, true)),
    });
    const n = showFindings(body, text);
    drawFindings(text);
    // The visible line beside the button already reads "2 findings". The announcement is there to say that a
    // check ran, so it says that and lets the line carry the number, rather than reading "2 findings. 2
    // findings" at whoever is listening.
    $("announce").textContent = n ? "Checked. " + $("found").textContent : "Nothing flagged.";
    // How the text reads, from the engine's own counters — one more call, only on a deliberate check. The
    // whole payload reaches the report now, rather than the one line this used to keep from it.
    state.stats = await call("/v2/stats", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    report();
  } catch (error) {
    $("found").textContent = error.name === "TimeoutError"
      ? "The engine did not answer within 6 s." : "Could not check: " + error.message;
    $("announce").textContent = $("found").textContent;
  } finally {
    $("check").disabled = false;
  }
}
