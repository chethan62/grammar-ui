// Completing the word being typed, shared by the two boxes where a word is typed — the draft and the word
// list's own box — because the only thing that differs is which token is meant: a textarea has a caret, an
// input is the whole word. Callers wire their own input event; this hands them the two things they need.
//
// There is deliberately no blur handler. A blur fires before the click on a suggestion, so clearing there
// removes the button under the pointer and the click lands on nothing — the classic way this looks broken.
// The list is cleared when the word it was about is gone instead: a shorter token, an empty box, or an
// accepted suggestion.
import { call } from "./shell.js";

const TOKEN = /[A-Za-z][A-Za-z'-]*$/;          // a word as the engine's own list spells one, back from the caret

// accept replaces the word being typed with the chosen one, leaving the caret after it so typing carries on
// from there. Exported for the check: this arithmetic is the fiddliest part here, and the way it fails is a
// silently wrong word rather than an error.
export function accept(input, typed, word) {
  const end = input.selectionStart;
  const start = end - typed.length;
  input.value = input.value.slice(0, start) + word + input.value.slice(end);
  const at = start + word.length;
  input.setSelectionRange(at, at);
  input.focus();
}

export function wireCompletion({ input, box, caret, after }) {
  let asked = "";                              // the last prefix sent: the same prefix is not a new question

  const clear = () => { box.replaceChildren(); asked = ""; };

  const token = () => {
    if (!caret) return input.value.trim();
    const found = input.value.slice(0, input.selectionStart).match(TOKEN);
    return found ? found[0] : "";
  };

  const take = (word) => {
    if (caret) {
      accept(input, token(), word);            // the token is read now, not when the list was built
    } else {
      // The box holds one word, so a click fills it in rather than adding it: what is about to be added
      // stays visible, and Add is still the only thing that writes.
      input.value = word;
      input.focus();
    }
    clear();
    if (after) after();
  };

  const suggest = async () => {
    const typed = token();
    // One letter is the alphabet. The engine answers that with an empty list too, and not asking is cheaper
    // than asking and being told.
    if (typed.length < 2) { clear(); return; }
    if (typed === asked) return;
    asked = typed;
    let answer;
    try {
      answer = await call("/v2/complete?prefix=" + encodeURIComponent(typed.toLowerCase()));
    } catch {
      clear();                                 // no engine, no suggestions — the footer already says why
      return;
    }
    if (asked !== typed) return;               // an answer about a word that has since been replaced
    box.replaceChildren(...(answer.words || []).map((word) => {
      const button = document.createElement("button");
      button.className = "flat";
      button.textContent = word;               // the engine's words, never innerHTML
      button.addEventListener("click", () => take(word));
      return button;
    }));
  };

  return { suggest, clear };
}
