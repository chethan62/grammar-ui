// One window, three tabs, over the engine's HTTP API. No Rust side: the engine sends
// Access-Control-Allow-Origin: *. Nothing is cached — the ignored words belong to the engine and the
// runner to its own config file, and either can change from a shell while this is open.

const ENGINE = localStorage.getItem("grammar-api") || "http://127.0.0.1:8875";
const $ = (id) => document.getElementById(id);
const TABS = ["check", "rewrite", "settings"];

async function call(path, options, ms) {
  // `fetch` has no timeout. Six seconds covers everything the engine answers from its own engine; a model
  // needs longer, so /v2/rewrite passes 30s — measured 0.8-5s locally, and a timeout that fires during a
  // good answer would be worse than no rewrite at all.
  const response = await fetch(ENGINE + path, { signal: AbortSignal.timeout(ms || 6000), ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || (path + " answered " + response.status));
  return body;
}

function say(text, bad) {
  $("status").textContent = text;
  $("state").textContent = bad ? "not answering" : "working";
  $("dot").className = "dot " + (bad ? "down" : "up");
}

function showTab(name) {
  // Every tab is a real <button>, so it is reachable by Tab and Enter already; role/aria-selected is what
  // makes it announced as a tab. Arrow-key roving focus is the APG refinement — not built, and not needed
  // for a three-tab window to be usable.
  for (const t of TABS) {
    $("tab-" + t).setAttribute("aria-selected", String(t === name));
    $("panel-" + t).hidden = t !== name;
  }
}
$("tab-check").parentElement.addEventListener("click", (event) => {
  const tab = event.target.closest("[role=tab]");
  if (tab) showTab(tab.id.slice(4));
});

// ---- findings: the model, the rows, the check ------------------------------------------------------
// The engine answers LanguageTool's shape; the UI wants a list it can act on. Offsets are UTF-16 code
// units by contract, so they index this string directly — `after` is what replaces `before`.
function findingsFrom(body, text) {
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

function findingRow(finding) {
  const div = document.createElement("div");
  div.className = "row finding";
  // The label is a button: it is the row's own action — go to this text in the box — and a button is the
  // only way to give that action to the keyboard as well as the mouse. Styled flat, it still reads as text.
  const label = document.createElement("button");
  label.className = "flat jump";
  label.textContent = finding.message + (finding.before ? " — " + finding.before : "");
  label.title = "Go to this text";
  label.addEventListener("click", () => jumpTo(finding));
  // textContent, never innerHTML: this is the user's own text coming back

  // Nothing to apply means no button at all. A disabled "No suggestion" is the same lesson in clutter: it
  // is on every finding the engine could not offer a replacement for, which is most of the style findings.
  const applies = finding.alts.map((alt) => {
    const button = document.createElement("button");
    button.className = "flat";
    button.textContent = "Use " + alt;
    button.addEventListener("click", async () => {
      const area = $("draft");
      area.focus();
      // setRangeText is native and takes UTF-16 indices, which is exactly what the engine sent — no diffing
      // code belongs here. Re-checking after an edit rather than tracking shifts: every later offset moves.
      area.setRangeText(alt, finding.offset, finding.offset + finding.length, "select");
      saveDraft();
      await check();
    });
    return button;
  });

  const fix = document.createElement("button");
  fix.className = "flat";
  fix.textContent = "Fix sentence";
  fix.addEventListener("click", () => fixSentence(finding));

  // A word the checker itself should accept: every editor benefits, not just this window.
  //
  // One word action, not two. "Ignore this word" and "Add to dictionary" both made a word stop being
  // flagged, so a row offering both was asking the reader to know the difference between quieting a word in
  // this window and teaching the checker it everywhere. The second is strictly the better fix and it is the
  // one that stayed; the ignore list is still in Settings, for words you want quiet without teaching.
  const word = /^[A-Za-z'’-]+$/.test(finding.before) ? finding.before : "";
  const actions = [label, ...applies, fix];
  // Rendered only when it applies, for the same reason: a disabled button on every row that is not a
  // misspelling is a control that teaches a reader nothing except that this panel is mostly unavailable.
  if (word && finding.rule === "MORFOLOGIK_RULE_EN_US") {
    const addToDictionary = document.createElement("button");
    addToDictionary.className = "flat";
    addToDictionary.textContent = "Add to dictionary";
    addToDictionary.addEventListener("click", async () => {
      addToDictionary.disabled = true;
      try {
        const answer = await call("/v2/dictionary", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ word }),
        });
        await check();
        // The endpoint checks its own effect and says so, so this reports what happened rather than what
        // was attempted: a word written but not picked up is a different outcome from one that took.
        say(answer.accepted
          ? "The checker now accepts " + word + "."
          : "Saved " + word + ", but the checker still flags it — see " + answer.path, !answer.accepted);
      } catch (error) {
        addToDictionary.disabled = false;
        say("Could not add " + word + ": " + error.message, true);
      }
    });
    actions.push(addToDictionary);
  }

  div.append(...actions);
  // The whole row is the target for a mouse; the label carries it for the keyboard. A click that landed on
  // an action button is left alone — this must never fire when someone meant Apply.
  div.addEventListener("click", (event) => { if (!event.target.closest("button")) jumpTo(finding); });
  return div;
}

// Clicking a finding takes you to the text it is about, with native selection and native scrolling. The
// engine's offsets are UTF-16 indices into this exact string, so there is nothing to convert — and the
// caret strip then shows the same finding, because the caret has just been put inside it.
function jumpTo(finding) {
  const area = $("draft");
  area.focus();
  area.setSelectionRange(finding.offset, finding.offset + finding.length);
  showAtCaret(area.value);
}

function saveDraft() {
  // A pasted document is the user's work, so it survives a close. localStorage throws when full, and losing
  // the stored copy is harmless while the live text is still in the textarea — hence the swallow.
  try { localStorage.setItem("grammar-draft", $("draft").value); } catch { /* nothing to say */ }
}

// Bands, not a formula: a formula needs its reasoning carried around, and a band is one sentence. A slow
// engine is given a LONGER wait, not a shorter one — the way to make a slow engine feel worse is to hand it
// more requests. These are grammar_core.debounce_ms, measured for this engine (0-1 ms warm, 49 ms on the
// first call): a debounce a thousand times the thing it waits for is not patience, it is the whole latency.
// `null` means nothing has been measured yet, which is not the same as fast.
function debounceMs(lastMs) {
  if (lastMs === null) return 600;
  if (lastMs < 40) return 300;
  if (lastMs < 250) return 900;
  return 1500;
}

const state = { findings: [], lastMs: null, timer: null, seq: 0, filter: "", partial: false, stats: null, undo: null };

// What the list is showing. The report's counts double as a filter, so the list follows the last count
// pressed. The caret strip does not — it is about where the cursor is, not about what is being browsed.
function drawn() {
  return state.filter ? state.findings.filter((f) => f.category === state.filter) : state.findings;
}

function drawFindings(text) {
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

// The finding the caret or the selection is inside. selectionStart is the same UTF-16 index the engine's
// offsets use, so this is a comparison and not a conversion — and both ends are inclusive, because clicking
// a finding puts the selection exactly on its span, so the caret lands on the finding's own offset. A strict
// comparison hid the strip in the one case it exists for: the row you just clicked.
function findingAt(findings, caret) {
  if (!findings.length || typeof caret !== "number") return null;
  return findings.find((f) => f.length && caret >= f.offset && caret <= f.offset + f.length) || null;
}

function showAtCaret(text) {
  const box = $("atCaret");
  const here = text.trim() ? findingAt(state.findings, $("draft").selectionStart) : null;
  if (!here) { box.hidden = true; box.replaceChildren(); return; }
  box.hidden = false;
  box.replaceChildren(findingRow(here));
}

function showFindings(body, text) {
  state.findings = findingsFrom(body, text);
  state.partial = !!(body.warnings && body.warnings.incompleteResults);
  return state.findings.length;
}

// ---- the report, and Fix all: what the paid tiers of the apps in this market sell -------------------
// Every suggestion in one pass, right to left so an edit cannot move the offsets of the edits still to
// come. Two findings can overlap — a phrase rule whose span contains a misspelled word — and writing both
// over the same characters would duplicate or drop text, so the first one kept wins and the other is
// skipped. A finding with no suggestion is not a failure: there is simply nothing to apply for it.
function applyAll(text, findings) {
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

function report() {
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
async function fixAll() {
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

async function liveCheck() {
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

function askFor(text, full) {
  const payload = { text };
  if (full) payload.level = "picky";                                  // the explicit ask gets the style tier
  if ($("language").value) payload.language = $("language").value;    // absent = the engine's own dialect
  return payload;
}

async function check() {
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

// The whole sentence a finding sits in, fixed in one call — the endpoint that picks harper's first
// suggestion per finding, and returns the range so the client replaces exactly what the server fixed.
async function fixSentence(finding) {
  const area = $("draft");
  const text = area.value;
  try {
    const body = await call("/v2/fix-sentence", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, offset: finding.offset }),
    });
    if (typeof body.fixed !== "string") return;
    area.focus();
    area.setRangeText(body.fixed, body.offset, body.offset + body.length, "select");
    saveDraft();
    await check();
    $("announce").textContent = "Sentence fixed.";
  } catch (error) {
    $("announce").textContent = "Could not fix the sentence: " + error.message;
  }
}

// ---- rewrite ---------------------------------------------------------------------------------------
async function rewrite() {
  // One text box in the whole window: the one on the Check tab. Two boxes both asking for "the text" made
  // the reader decide which one counted — and a chosen version already replaced the draft, so the coupling
  // was there anyway. This stops pretending otherwise.
  const text = $("draft").value;
  $("candidates").replaceChildren();
  if (!text.trim()) {
    $("rewriteState").textContent = "Nothing to rewrite yet — put the text on the Check tab.";
    return;
  }
  $("rewrite").disabled = true;
  $("rewriteState").textContent = "Asking the model…";
  try {
    // concise, and no tone. The engine's own note on this is that asking a small model for a tone makes it
    // add words rather than fix them, and that concise is the one hint worth setting — so the two fields
    // that used to be here were both a rule to learn and a worse result. A field whose honest default is
    // "leave it empty" is a field nobody should have to understand.
    const body = await call("/v2/rewrite", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, intent: "concise" }),
    }, 30000);
    const candidates = body.candidates || [];
    $("candidates").replaceChildren(...candidates.map((candidate) => {
      const div = document.createElement("div");
      div.className = "row candidate";
      const span = document.createElement("span");
      span.textContent = candidate;                 // the model's words, never innerHTML
      const use = document.createElement("button");
      use.className = "flat";
      use.textContent = "Use this";
      use.addEventListener("click", () => {
        $("draft").value = candidate;
        saveDraft();
        showTab("check");
        check();
      });
      div.append(span, use);
      return div;
    }));
    $("rewriteState").textContent = candidates.length
      ? candidates.length + (candidates.length === 1 ? " version" : " versions") +
        " from " + body.model + (body.elapsedMs ? " in " + (body.elapsedMs / 1000).toFixed(1) + " s" : "")
      : "The model returned nothing.";
  } catch (error) {
    $("rewriteState").textContent = error.name === "TimeoutError"
      ? "The model did not answer within 30 s." : "Could not rewrite: " + error.message;
  } finally {
    $("rewrite").disabled = false;
  }
}

// ---- settings --------------------------------------------------------------------------------------
function rows(words) {
  return words.map((word) => {
    const div = document.createElement("div");
    div.className = "row";
    const label = document.createElement("span");
    label.className = "word";
    label.textContent = word;                       // the user's words, never innerHTML
    const button = document.createElement("button");
    button.className = "flat";
    button.textContent = "Allow " + word;           // named for what it does *to what*
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await call("/v2/ignore", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ word, forget: true }),
        });
        await load();
        say("Stopped ignoring " + word + ".");
      } catch (error) {
        button.disabled = false;
        say("Could not remove " + word + ": " + error.message, true);
      }
    });
    div.append(label, button);
    return div;
  });
}

async function load() {
  try {
    const [ignored, ai, langs, status] = await Promise.all([
      call("/v2/ignore"), call("/v1/ai"), call("/v2/languages"), call("/status"),
    ]);
    const words = ignored.words || [];
    $("words").replaceChildren(...rows(words));
    $("wordsNote").textContent = words.length
      ? "The engine stops reporting these; removing one brings the findings back."
      : "Nothing is ignored yet.";

    // The engine's Preset struct sends id/label/hint/keyEnv/local. This read `name`, a field it has never
    // sent, so every entry said "undefined" and every option's value was the string "undefined" — the panel
    // could not name the provider it was showing, let alone reach a custom one.
    const presets = (ai.presets || []).slice();
    if (ai.provider && ai.provider !== "none" && !presets.some((p) => p.id === ai.provider)) {
      // A backend chosen from a shell can name something no preset covers: show it rather than hide it.
      presets.unshift({ id: ai.provider, label: ai.provider, url: "", model: "", hint: "" });
    }
    // "off" is not a preset — the engine treats it as its own case — so the panel supplies it.
    const off = document.createElement("option");
    off.value = "none";
    off.textContent = "off — no rewriting";
    off.dataset.hint = "Every check still works; Rewrite is simply not offered.";
    $("provider").replaceChildren(off, ...presets.map((p) => {
      const option = document.createElement("option");
      option.value = p.id;                             // the API takes this id back, so it must be the id
      option.textContent = p.label || p.id;            // and this is the name a person recognises
      option.dataset.url = p.url || "";
      option.dataset.model = p.model || "";
      option.dataset.env = p.keyEnv || "";
      option.dataset.hint = p.hint || "";
      option.selected = p.id === ai.provider;
      return option;
    }));
    $("url").value = ai.url || "";
    $("model").value = ai.model || "";
    $("apiKey").value = "";                            // the engine never sends a key back, by design
    $("models").replaceChildren(...(ai.models || []).map((m) => {
      const option = document.createElement("option");
      option.value = m;
      return option;
    }));

    // An empty value is a real state here, and it has to be: a select with no empty option silently
    // selects its first entry, so the second load (the focus reload) read that as a choice the user had
    // made — and check() then sent a language that overrode the engine's own dialect without saying so.
    const chosen = $("language").value;               // "" means: whatever the engine is configured for
    const languages = Array.isArray(langs) ? langs : (langs.languages || []);
    const options = languages.map((l) => {
      const option = document.createElement("option");
      option.value = l.longCode || l.code;            // the engine's README: clients read longCode
      option.textContent = l.name;
      return option;
    });
    const configured = document.createElement("option");
    configured.value = "";
    configured.textContent = "as configured — " + (status.dialect || "unknown");
    $("language").replaceChildren(configured, ...options);
    $("language").value = chosen;
    $("languageNote").textContent = chosen
      ? "Checking as " + $("language").selectedOptions[0].textContent + "."
      : "Whatever the engine is configured for: " + (status.dialect || "unknown") + ".";

    showProviderNote();
    const picked = $("provider").selectedOptions[0];
    if (picked && picked.dataset.env && !ai.keySet && !ai.keySource) {
      $("providerNote").textContent += " Needs " + picked.dataset.env +
        " in the environment, or a key below.";
    }
    $("keyNote").textContent = ai.keySet
      ? "A key is saved" + (ai.keySource ? " (" + ai.keySource + ")" : "") + "; blank keeps it."
      : "No key saved. Local servers usually need none.";
    $("modelNote").textContent = (ai.models || []).length
      ? "What this server reports, or type any model name."
      : "The server offers no list; type the model name it should use.";
    $("save").disabled = ai.writable === false;
    say(ai.writable === false
      ? "This engine is read-only from here: it is configured on the machine it runs on."
      : (ai.reachable ? "answering — " + (ai.models || []).length + " models"
                      : "configured, not answering"));
  } catch (error) {
    say(error.name === "TimeoutError"
        ? "The engine at " + ENGINE + " did not answer within 6 s."
        : "No engine at " + ENGINE + " — " + error.message, true);
  }
}

// The note under the picker is the engine's own preset hint, so a backend added to its table appears here
// with its explanation and nothing in this file changes. A preset that carries no URL of its own is the
// custom case, and the address field opens for it — but only while that field is actually empty. Keying it
// on the preset alone made a saved custom endpoint nag "give the base URL" at a filled-in field, and left
// the field folded away when the one thing you want to change is what's in it.
function showProviderNote() {
  const picked = $("provider").selectedOptions[0];
  const needsUrl = !!picked && picked.value !== "none" && !$("url").value.trim();
  $("address").open = needsUrl;
  $("providerNote").textContent = ((picked && picked.dataset.hint) || "") +
    (needsUrl ? " Give the full base URL below." : "");
}

$("provider").addEventListener("change", () => {
  const picked = $("provider").selectedOptions[0];
  if (picked) { $("url").value = picked.dataset.url; $("model").value = picked.dataset.model; }
  // The key belongs to the provider it was issued by, so switching throws away anything typed for the last
  // one rather than saving it against the wrong service.
  $("apiKey").value = "";
  showProviderNote();
});

// Adding a word used to be the card's job. With the card gone this tab is the only place it can happen.
async function addWord() {
  const word = $("newWord").value.trim();
  if (!word) return;
  $("add").disabled = true;
  try {
    await call("/v2/ignore", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word }),
    });
    $("newWord").value = "";
    await load();
    say("Now ignoring " + word + ".");
  } catch (error) {
    say("Could not add " + word + ": " + error.message, true);
  } finally {
    $("add").disabled = false;
  }
}
$("add").addEventListener("click", addWord);
$("newWord").addEventListener("keydown", (event) => { if (event.key === "Enter") addWord(); });

// The caret moves without an edit, so the strip follows it: selectionchange covers the keyboard, the
// mouse and a click into the box.
document.addEventListener("selectionchange", () => {
  if (document.activeElement === $("draft")) showAtCaret($("draft").value);
});
$("check").addEventListener("click", check);
$("fixall").addEventListener("click", fixAll);
$("undo").addEventListener("click", async () => {
  if (state.undo === null) return;
  $("draft").value = state.undo;                                     // the text exactly as it was
  state.undo = null;
  $("undo").hidden = true;
  saveDraft();
  await check();
  $("announce").textContent = "Undone.";
});
$("draft").addEventListener("input", () => {
  // An edit after Fix all makes the kept copy the wrong thing to go back to, so Undo goes with it.
  state.undo = null; $("undo").hidden = true;
  saveDraft(); liveCheck();
});
// Ctrl+Enter checks, matching the add field that submits on Enter — and it must not swallow plain Enter,
// which inserts a newline in a textarea.
$("draft").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); check(); }
});
$("rewrite").addEventListener("click", rewrite);

$("save").addEventListener("click", async () => {
  $("save").disabled = true;
  $("saveState").textContent = "Saving…";
  try {
    const payload = { provider: $("provider").value, url: $("url").value.trim(),
                      model: $("model").value.trim() };
    // An empty key means "keep the saved one" to the engine, so a blank field must not be sent at all.
    if ($("apiKey").value) payload.apiKey = $("apiKey").value;
    await call("/v1/ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await load();
    $("saveState").textContent = "Saved.";
    say("Saved.");
  } catch (error) {
    $("saveState").textContent = "";
    say("Could not save: " + error.message, true);
  } finally {
    $("save").disabled = false;
  }
});

// A shell command or another machine can change either list while this is open, and the point of the window
// is to show them. Reloading when it regains focus lands exactly when someone is about to read it — and it
// never touches a draft, which lives in its own element.
window.addEventListener("focus", load);

$("draft").value = localStorage.getItem("grammar-draft") || "";
showTab("check");
if ($("draft").value) liveCheck();
load();
