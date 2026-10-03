// The Rewrite tab and the Settings tab — the two panels that are mostly form: the provider picker and the
// ignored-word list. Depends on shell, model and rows.

import { $, call, say, showTab, engineUrl } from "./shell.js";
import { state } from "./model.js";
import { candidateRow, saveDraft, recheck } from "./rows.js";

export async function rewrite() {
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
    $("candidates").replaceChildren(...candidates.map((candidate) => candidateRow(candidate, (text) => {
      $("draft").value = text;
      saveDraft();
      showTab("check");
      recheck();
    })));
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

// The words harper itself accepts, each with the way back out. The endpoint takes a word out again
// (DELETE /v2/dictionary?word=…) and restarts harper-ls, so this control does what it says — and it is on
// every row rather than revealed on hover, because an action a reader has to go looking for is one they
// will not find.
export function rows(words) {
  return words.map((word) => {
    const div = document.createElement("div");
    div.className = "row";
    const label = document.createElement("span");
    label.className = "word";
    label.textContent = word;                       // the user's words, never innerHTML
    const remove = document.createElement("button");
    remove.className = "flat";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeWord(word));
    div.append(label, remove);
    return div;
  });
}

export async function load() {
  try {
    const [dictionary, ai, langs, status, pause] = await Promise.all([
      call("/v2/dictionary"), call("/v1/ai"), call("/v2/languages"), call("/status"), call("/v2/pause"),
    ]);
    // The rows need to know whether a model is configured: a style finding harper cannot rewrite gets a
    // "Rephrase" button when there is one and no button at all when there is not. "none" is the engine's
    // own word for off, and a null provider means the engine never answered.
    state.provider = ai.provider || null;
    const words = dictionary.words || [];
    $("words").replaceChildren(...rows(words));
    $("wordsNote").textContent = words.length
      ? "harper accepts these in every editor. Remove takes one back out."
      : "Nothing added yet — a word here is accepted by harper itself, in every editor.";

    // Pause status (read-only, owned by grammar-watch/grammar-pause)
    if (pause.paused) {
      const until = new Date(pause.until).toLocaleString();
      $("pauseNote").textContent = "Checker paused until " + until + ".";
    } else {
      $("pauseNote").textContent = "Checker is running.";
    }
    if (pause.blocked && pause.blocked.length) {
      $("pauseNote").textContent += " Blocked apps: " + pause.blocked.join(", ") + ".";
    }

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
        ? "The engine at " + engineUrl() + " did not answer within 6 s."
        : "No engine at " + engineUrl() + " — " + error.message, true);
  }
}

// The note under the picker is the engine's own preset hint, so a backend added to its table appears here
// with its explanation and nothing in this file changes. A preset that carries no URL of its own is the
// custom case, and the address field opens for it — but only while that field is actually empty. Keying it
// on the preset alone made a saved custom endpoint nag "give the base URL" at a filled-in field, and left
// the field folded away when the one thing you want to change is what's in it.
export function showProviderNote() {
  const picked = $("provider").selectedOptions[0];
  const needsUrl = !!picked && picked.value !== "none" && !$("url").value.trim();
  $("address").open = needsUrl;
  $("providerNote").textContent = ((picked && picked.dataset.hint) || "") +
    (needsUrl ? " Give the full base URL below." : "");
}

// Adding a word used to be the card's job. With the card gone this tab is the only place it can happen.
export async function addWord() {
  const word = $("newWord").value.trim();
  if (!word) return;
  $("add").disabled = true;
  try {
    // The same endpoint the row's "Add to dictionary" uses, so one intent has one scope: a word added
    // here is accepted by harper itself, everywhere, not silenced inside this engine alone.
    const answer = await call("/v2/dictionary", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word }),
    });
    $("newWord").value = "";
    await load();
    // The endpoint checks its own effect and says so, so this reports what happened rather than what was
    // attempted — the row button's rule, kept identical here because it is the same operation.
    say(answer.accepted
      ? "The checker now accepts " + word + ", in every editor."
      : "Saved " + word + ", but the checker still flags it — see " + answer.path, !answer.accepted);
  } catch (error) {
    say("Could not add " + word + ": " + error.message, true);
  } finally {
    $("add").disabled = false;
  }
}

// Taking a word back out, through the endpoint that can. The engine measures the result, so this reports
// what happened rather than what was attempted — and the three outcomes are genuinely different: removed
// and the checker flags it again, removed but harper knows the word on its own (a common English word needs
// no list), or it was not there to begin with.
export async function removeWord(word) {
  try {
    const answer = await call("/v2/dictionary?word=" + encodeURIComponent(word), { method: "DELETE" });
    await load();
    if (!answer.removed) {
      say(word + " was not in the list.", true);
    } else if (answer.accepted) {
      say("Took " + word + " out of the list; the checker still accepts it on its own.");
    } else {
      say("The checker flags " + word + " again.");
    }
  } catch (error) {
    say("Could not remove " + word + ": " + error.message, true);
  }
}
