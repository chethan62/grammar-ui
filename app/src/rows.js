// A finding becomes a row, and a row's actions: go to it, apply one of its alternatives, fix the sentence
// with harper or rephrase it with the model, teach the checker a word. Depends on shell + model.

// `recheck` is the one seam in the layering. A row's actions end by re-checking the text, and check() lives
// in flow.js, which draws rows — importing it here would make the two modules a cycle. So flow and panels
// import this and the wiring points it at check() once, which keeps the dependency running one way: leaves
// never reach up. It throws until then rather than silently doing nothing, because a row whose action
// appears to work and does not is the worst version of this.

import { $, call, say } from "./shell.js";
import { state, findingAt, sentenceRange } from "./model.js";

export let recheck = () => {
  throw new Error("recheck() is not wired: app.js must call setRecheck(check)");
};
export function setRecheck(fn) { recheck = fn; }

export function findingRow(finding) {
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
      await recheck();
    });
    return button;
  });

  // The row's sentence-level action, and the division of labour picks which one it is. A finding harper has
  // a replacement for is one harper can act on: "Fix sentence" applies its first suggestion per finding
  // across the whole sentence — a batch convenience, since the buttons above already do it one at a time.
  // A finding with no replacements is one harper can only see. Measured: `The report was written by the
  // team.` comes back PASSIVE_VOICE_SIMPLE with zero replacements and /v2/fix-sentence hands it back
  // unchanged, so that row's "Fix sentence" did nothing when pressed. That is exactly the model's job —
  // rules for what rules can express, the model for what they cannot, and no button when neither applies.
  const sentenceAction = document.createElement("button");
  sentenceAction.className = "flat";
  if (finding.alts.length) {
    sentenceAction.textContent = "Fix sentence";
    sentenceAction.addEventListener("click", () => fixSentence(finding));
  } else if (state.provider && state.provider !== "none") {
    const [s0, s1] = sentenceRange($("draft").value, finding.offset);
    sentenceAction.textContent = "Rephrase";
    sentenceAction.title = "Ask the model to rewrite just this sentence";
    sentenceAction.addEventListener("click", () => rephraseSentence(finding, s0, s1, div));
  }

  // A word the checker itself should accept: every editor benefits, not just this window.
  //
  // One word action, not two. "Ignore this word" and "Add to dictionary" both made a word stop being
  // flagged, so a row offering both was asking the reader to know the difference between quieting a word in
  // this window and teaching the checker it everywhere. The second is strictly the better fix and it is the
  // one that stayed; the ignore list is still in Settings, for words you want quiet without teaching.
  const word = /^[A-Za-z'’-]+$/.test(finding.before) ? finding.before : "";
  const actions = [label, ...applies];
  if (sentenceAction.textContent) actions.push(sentenceAction);
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
        await recheck();
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
export function jumpTo(finding) {
  const area = $("draft");
  area.focus();
  area.setSelectionRange(finding.offset, finding.offset + finding.length);
  showAtCaret(area.value);
}

export function saveDraft() {
  // A pasted document is the user's work, so it survives a close. localStorage throws when full, and losing
  // the stored copy is harmless while the live text is still in the textarea — hence the swallow.
  try { localStorage.setItem("grammar-draft", $("draft").value); } catch { /* nothing to say */ }
}

export function showAtCaret(text) {
  const box = $("atCaret");
  const here = text.trim() ? findingAt(state.findings, $("draft").selectionStart) : null;
  if (!here) { box.hidden = true; box.replaceChildren(); return; }
  box.hidden = false;
  box.replaceChildren(findingRow(here));
}

// The whole sentence a finding sits in, fixed in one call — the endpoint that picks harper's first
// suggestion per finding, and returns the range so the client replaces exactly what the server fixed.
export async function fixSentence(finding) {
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
    await recheck();
    $("announce").textContent = "Sentence fixed.";
  } catch (error) {
    $("announce").textContent = "Could not fix the sentence: " + error.message;
  }
}

// One candidate from the model, and what "Use this" does with it. The action differs by caller — the
// Rewrite tab replaces the whole draft, a row's Rephrase replaces only the sentence it came from — so the
// row is shared and the handler is passed in.
export function candidateRow(candidate, onUse) {
  const div = document.createElement("div");
  div.className = "row candidate";
  const span = document.createElement("span");
  span.textContent = candidate;                   // the model's words, never innerHTML
  const use = document.createElement("button");
  use.className = "flat";
  use.textContent = "Use this";
  use.addEventListener("click", () => onUse(candidate));
  div.append(span, use);
  return div;
}

// The model's half of the division: a sentence harper can flag but cannot rewrite. Only this sentence goes
// to the model — sending the whole draft to fix one passive clause would rewrite text the reader was happy
// with, which is the thing a rewrite tool must never do. The verdict stays harper's either way: whatever
// comes back goes into the draft and is checked again, so the model proposes and the checker disposes.
export async function rephraseSentence(finding, start, stop, row) {
  const area = $("draft");
  const sentence = area.value.slice(start, stop).trim();
  if (!sentence) return;
  // A second press replaces the first answer rather than stacking a column of them under the row.
  let box = row.nextElementSibling;
  if (!box || !box.classList.contains("candidates")) {
    box = document.createElement("div");
    box.className = "candidates";
    row.after(box);
  }
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = "Asking the model…";
  box.replaceChildren(note);
  try {
    const body = await call("/v2/rewrite", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: sentence, intent: "concise" }),
    }, 30000);
    const candidates = body.candidates || [];
    if (!candidates.length) {
      note.textContent = "The model returned nothing.";
    } else {
      box.replaceChildren(...candidates.map((chosen) => candidateRow(chosen, (text) => {
        area.focus();
        // setRangeText, so only this sentence moves and every other offset in the draft stays put.
        area.setRangeText(text, start, stop, "select");
        saveDraft();
        recheck();
      })));
    }
  } catch (error) {
    note.textContent = error.name === "TimeoutError"
      ? "The model did not answer within 30 s." : "Could not rephrase: " + error.message;
    box.replaceChildren(note);
  }
}
