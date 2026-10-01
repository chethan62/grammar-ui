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
    return {
      message: match.message || "Finding",
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
  const label = document.createElement("span");
  label.textContent = finding.message + (finding.before ? " — " + finding.before : "");
  // textContent, never innerHTML: this is the user's own text coming back

  const applies = (finding.alts.length ? finding.alts : [null]).map((alt) => {
    const button = document.createElement("button");
    button.className = "flat";
    button.textContent = alt ? "Use " + alt : "No suggestion";
    button.disabled = !alt;
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

  const ignore = document.createElement("button");
  ignore.className = "flat";
  ignore.textContent = "Ignore this word";
  ignore.disabled = !/[A-Za-z]/.test(finding.before);
  ignore.addEventListener("click", async () => {
    ignore.disabled = true;
    try {
      await call("/v2/ignore", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word: finding.before }),
      });
      await check();
      say("Stopped reporting " + finding.before + ".");
    } catch (error) {
      ignore.disabled = false;
      say("Could not ignore " + finding.before + ": " + error.message, true);
    }
  });

  div.append(label, ...applies, fix, ignore);
  return div;
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

const state = { findings: [], lastMs: null, timer: null, seq: 0 };

function drawFindings(text) {
  $("findings").replaceChildren(...state.findings.map((f) => findingRow(f, text)));
}

function showFindings(body, text) {
  state.findings = findingsFrom(body, text);
  const partial = !!(body.warnings && body.warnings.incompleteResults);
  const n = state.findings.length;
  $("found").textContent = n
    ? n + (n === 1 ? " finding" : " findings") + (partial ? " (partial)" : "")
    : "Nothing flagged" + (partial ? " — but the check was partial" : "");
  return n;
}

async function liveCheck() {
  const text = $("draft").value;
  clearTimeout(state.timer);
  if (!text.trim()) { state.findings = []; drawFindings(text); $("found").textContent = ""; return; }
  state.timer = setTimeout(async () => {
    const mine = ++state.seq;
    const began = performance.now();
    try {
      // Correctness only while typing: the style tier is what an explicit Check asks for, and the engine's
      // README makes the same distinction for editor clients ("never shown hints it did not ask for").
      const body = await call("/v2/check", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(askFor(text, false)),
      });
      // ponytail: the superseded request still runs. The engine answers in ~1 ms, so dropping its answer is
      // enough — reach for AbortController if a check ever gets expensive.
      if (mine !== state.seq) return;
      state.lastMs = performance.now() - began;
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
  $("summary").textContent = "";
  if (!text.trim()) {
    state.findings = [];
    drawFindings(text);
    $("found").textContent = "";
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
    $("announce").textContent = n
      ? n + (n === 1 ? " finding" : " findings") + ". " + $("found").textContent
      : "Nothing flagged.";
    // How the text reads, from the engine's own counters — one more call, only on a deliberate check.
    const stats = await call("/v2/stats", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    $("summary").textContent = stats.words
      ? stats.words + " words · " + stats.sentences + (stats.sentences === 1 ? " sentence" : " sentences") +
        " · reading ease " + stats.fleschReadingEase + " (" + stats.grade + ") · " + stats.readingTime
      : "";
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
  const text = $("source").value;
  const intent = $("intent").value.trim();
  $("candidates").replaceChildren();
  if (!text.trim()) { $("rewriteState").textContent = "Nothing to rewrite yet."; return; }
  if (!/^[a-z]{1,20}$/.test(intent)) {
    // The engine answers 400 "invalid 'intent': one lower-case word, up to 20 letters" and no UI would have
    // told you; saying it here costs one line and saves a round trip.
    $("rewriteState").textContent = "Intent must be one lower-case word, up to 20 letters.";
    $("intent").focus();
    return;
  }
  $("rewrite").disabled = true;
  $("rewriteState").textContent = "Asking the model…";
  try {
    const payload = { text, intent };
    if ($("tone").value.trim()) payload.tone = $("tone").value.trim();
    const body = await call("/v2/rewrite", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }, 30000);
    const candidates = body.candidates || [];
    $("candidates").replaceChildren(...candidates.map((candidate) => {
      const div = document.createElement("div");
      div.className = "row candidate";
      const span = document.createElement("span");
      span.textContent = candidate;                 // the model's words, never innerHTML
      const use = document.createElement("button");
      use.className = "flat";
      use.textContent = "Check this";
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

    const presets = (ai.presets || []).slice();
    if (!presets.some((p) => p.name === ai.provider)) {
      presets.unshift({ name: ai.provider || "none", url: "", model: "" });
    }
    $("provider").replaceChildren(...presets.map((p) => {
      const option = document.createElement("option");
      option.value = p.name;
      option.textContent = p.name === "none" ? "off" : p.name;
      option.dataset.url = p.url || "";
      option.dataset.model = p.model || "";
      option.dataset.env = p.keyEnv || "";
      option.selected = p.name === ai.provider;
      return option;
    }));
    const picked = $("provider").selectedOptions[0];
    $("url").value = ai.url || "";
    $("model").value = ai.model || "";
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

    $("providerNote").textContent = ai.provider === "none"
      ? "Every check still works; Rewrite is simply not offered."
      : (picked && picked.dataset.env && !ai.keySet
         ? "Needs " + picked.dataset.env + " in the environment, or a key in your keyring."
         : (String(ai.url || "").includes("127.0.0.1")
            ? "Local: nothing leaves this machine." : ""));
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

$("provider").addEventListener("change", () => {
  const picked = $("provider").selectedOptions[0];
  if (picked) { $("url").value = picked.dataset.url; $("model").value = picked.dataset.model; }
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

$("check").addEventListener("click", check);
$("draft").addEventListener("input", () => { saveDraft(); liveCheck(); });
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
    await call("/v1/ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: $("provider").value, url: $("url").value.trim(),
                             model: $("model").value.trim() }),
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
