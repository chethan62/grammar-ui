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
  let selectedIndex = -1;                      // -1 = nothing highlighted

  const clear = () => { box.replaceChildren(); asked = ""; selectedIndex = -1; };

  const token = () => {
    if (!caret) return input.value.trim();
    const found = input.value.slice(0, input.selectionStart).match(TOKEN);
    return found ? found[0] : "";
  };

  const highlight = (idx) => {
    const buttons = box.querySelectorAll("button");
    buttons.forEach((btn, i) => btn.classList.toggle("selected", i === idx));
  };

  const take = (word) => {
    if (caret) {
      accept(input, token(), word);
    } else {
      input.value = word;
      input.focus();
    }
    clear();
    if (after) after();
  };

  const suggest = async () => {
    const typed = token();
    if (typed.length < 2) { clear(); return; }
    if (typed === asked) return;
    asked = typed;
    let answer;
    try {
      answer = await call("/v2/complete?prefix=" + encodeURIComponent(typed.toLowerCase()));
    } catch {
      clear();
      return;
    }
    if (asked !== typed) return;
    selectedIndex = -1;
    box.replaceChildren(...(answer.words || []).map((word, i) => {
      const button = document.createElement("button");
      button.className = "flat";
      button.textContent = word;
      button.addEventListener("click", () => take(word));
      return button;
    }));
  };

  // Keyboard: Up/Down moves, Enter/Tab accepts, Escape dismisses. The input owns the keydown so the list
  // doesn't need focus (which would steal the caret).
  input.addEventListener("keydown", (e) => {
    const buttons = box.querySelectorAll("button");
    if (!buttons.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selectedIndex = (selectedIndex + 1) % buttons.length;
      highlight(selectedIndex);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      selectedIndex = (selectedIndex - 1 + buttons.length) % buttons.length;
      highlight(selectedIndex);
    } else if (e.key === "Enter" || e.key === "Tab") {
      if (selectedIndex >= 0) {
        e.preventDefault();
        take(buttons[selectedIndex].textContent);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      clear();
    } else {
      // Any other key means the prefix changed — the input handler will call suggest() and reset selection.
      selectedIndex = -1;
    }
  });

  return { suggest, clear };
}
