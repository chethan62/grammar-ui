// The whole app: no Rust side, because the engine already sends `Access-Control-Allow-Origin: *` and
// every call goes straight to it over HTTP. Nothing is cached — the ignored words belong to the engine
// and the runner to its own config file, and either can change from a shell while this is open.

const ENGINE = localStorage.getItem("grammar-api") || "http://127.0.0.1:8875";
const $ = (id) => document.getElementById(id);

async function call(path, options) {
  const response = await fetch(ENGINE + path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || (path + " answered " + response.status));
  return body;
}

function say(text, bad) {
  $("status").textContent = text;
  $("state").textContent = bad ? "not answering" : "working";
  $("dot").className = "dot " + (bad ? "down" : "up");
}

function row(word) {
  const div = document.createElement("div");
  div.className = "row";
  const label = document.createElement("span");
  label.textContent = word;                       // textContent, never innerHTML: these are the user's words
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
}

async function load() {
  try {
    const [ignored, ai] = await Promise.all([call("/v2/ignore"), call("/v1/ai")]);
    const words = ignored.words || [];
    $("words").replaceChildren(...words.map(row));
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

    $("providerNote").textContent = ai.provider === "none"
      ? "Every check still works; Rephrase is simply not offered."
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
    say("No engine at " + ENGINE + " — " + error.message, true);
  }
}

$("provider").addEventListener("change", () => {
  const picked = $("provider").selectedOptions[0];
  if (picked) { $("url").value = picked.dataset.url; $("model").value = picked.dataset.model; }
});

// Adding a word used to be the card's job. With the card gone this window is the only place it can
// happen, so the field is here rather than left to curl.
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

// A card, a shell command or another machine can change either list while this is open, and the whole
// point of the window is to show those two lists. Reloading when it regains focus lands exactly when
// someone is about to read it. It is only safe because this is a normal window: the old panel could
// not do this without repainting under the user's hands.
window.addEventListener("focus", load);

$("test").addEventListener("click", load);        // the same round trip: /v1/ai asks the backend

$("save").addEventListener("click", async () => {
  $("save").disabled = true;
  say("Saving…");
  try {
    await call("/v1/ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: $("provider").value, url: $("url").value.trim(),
                             model: $("model").value.trim() }),
    });
    await load();
    say("Saved.");
  } catch (error) {
    say("Could not save: " + error.message, true);
  } finally {
    $("save").disabled = false;
  }
});

load();
