// The window itself: which tab is showing, every listener in one place, and the boot. The only module that
// knows all the others — the leaves never reach up, so nothing here can surprise a reader of them.

// One window, three tabs, over the engine's HTTP API. No Rust side: the engine sends
// Access-Control-Allow-Origin: *. Nothing is cached — the ignored words belong to the engine and the
// runner to its own config file, and either can change from a shell while this is open.

import { $, showTab, call, say } from "./shell.js";
import { state } from "./model.js";
import { showAtCaret, saveDraft, setRecheck, recheck } from "./rows.js";
import { check, liveCheck, fixAll } from "./flow.js";
import { load, rewrite, addWord, showProviderNote } from "./panels.js";
import { wireCompletion } from "./wordcomplete.js";

// A row's actions end by re-checking the text, and they reach check() through this seam rather than by
// importing it (see rows.js: importing it there would make rows and flow a cycle). Wired once, here.
setRecheck(check);

$("tab-check").parentElement.addEventListener("click", (event) => {
  const tab = event.target.closest("[role=tab]");
  if (tab) showTab(tab.id.slice(4));
});

$("provider").addEventListener("change", () => {
  const picked = $("provider").selectedOptions[0];
  if (picked) { $("url").value = picked.dataset.url; $("model").value = picked.dataset.model; }
  // The key belongs to the provider it was issued by, so switching throws away anything typed for the last
  // one rather than saving it against the wrong service.
  $("apiKey").value = "";
  showProviderNote();
});

$("add").addEventListener("click", addWord);
// The completion's keydown is wired BEFORE the Enter-to-add one on purpose: listeners run in registration
// order and preventDefault does not stop a sibling listener, so this order is what makes Enter on a
// highlighted suggestion take the word and then add it — the other order reads the box before it is filled
// and adds the partial prefix instead. Measured: the dictionary gained "spec" from Entering "specular".
const addWords = wireCompletion({ input: $("newWord"), box: $("addWords") });
$("newWord").addEventListener("input", () => addWords.suggest());
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
// Completion for the two boxes a word is typed into. The draft's caret decides which word is meant; the word
// list's box holds one word, so its whole value is the token. A click fills that box in rather than adding
// the word, so an accepted suggestion still goes through Add where the user can see what they are adding.
const draftWords = wireCompletion({
  input: $("draft"), box: $("draftWords"), caret: true, after: () => recheck(),
});

$("draft").addEventListener("input", () => {
  // An edit after Fix all makes the kept copy the wrong thing to go back to, so Undo goes with it.
  state.undo = null; $("undo").hidden = true;
  saveDraft(); liveCheck();
  draftWords.suggest();
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
