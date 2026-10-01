# grammar-ui

The desktop side of [grammar-server](https://github.com/chethan62/grammar-server): a selection
checker, a typing watcher, and the suggestion card. **Python and stdlib, plus Qt (PySide6) for the
card — nothing to build,
no browser, no dependencies.**

There was a browser UI here. It was removed at the user's request: the card at the caret is where
the checking happens now, and the page was a second surface to keep in step.

## The three pieces

| | |
|---|---|
| `grammar-lookup` | checks the text you have **selected**, on **Ctrl+Alt+C** |
| `grammar-watch` | checks the sentence around your **caret** while you type, and offers a card |
| `grammar-popup.py` | the card itself — its own process, so a card that dies cannot take the watcher with it |
| `grammar-popup.py --settings` | the settings window: what it skips (ignored words, paused applications, the pause) and which model rephrases |
| `grammar-doctor` | is the whole chain working? Every silent failure this product has, named with its fix |
| `grammar-action` | `accept` / `dismiss` — what the keyboard shortcuts run |
| `grammar-pause` | `15m` / `1h` / `off` — silence the checker for a while, or bring it back; `--blocks` lists the applications you have ignored, `--unblock <app>` checks in one of them again |

None of them needs a browser extension, and none of them reads a DOM: they read the accessibility
bus, the same interface a screen reader uses. That is also the limit — an application that
publishes no accessible text (most terminals, Electron apps started without
`--force-renderer-accessibility`) cannot be served this way, because the a11y bus is the only door
into another application's text.

### Check a selection

1. select the text in any application (the primary selection is what gets checked)
2. press **Ctrl+Alt+C**
3. the findings appear in a native dialog: **Copy fixed** puts the correction on your clipboard,
   **Show fixed** shows it, **Close** does nothing
4. Ctrl+V replaces the selection

```bash
grammar-lookup                                    # the dialog the shortcut runs
GRAMMAR_NO_UI=1 grammar-lookup                    # print the corrected text (scripts, editors)
GRAMMAR_API=http://cachyos.local:8875 grammar-lookup   # check against another machine
GRAMMAR_LANG=en-GB grammar-lookup                 # check as another language
```

The correction repeats until the text stops changing: the engine's sentence-capitalisation rule
suggests `Teh` for a typo at the start of a sentence — it is correcting the capital, not the
spelling — and only a second pass turns that into `The`.

It cannot paste for you: KWin does not implement the Wayland virtual-keyboard protocol (`wtype`
says so) and `ydotoold` is not running, so the last Ctrl+V is yours. It does paste automatically
if either becomes available.

**The shortcut is yours to bind** — System Settings → Shortcuts → *Check my selection*, or the
`kwriteconfig6` line under "Accept and dismiss from the keyboard" below, with the entry name
`grammar-lookup.desktop`. This Makefile writes no keys. A binding takes effect at the next login,
because kglobalaccel reads its config when it starts.

### Suggestions as you type

Pause for a moment and the sentence around the caret is checked. The card appears next to the
caret:

```
teh                                  ← large and struck through: the problem
This word is spelled wrong.
She go → She goes
─────────────────────────
FIXES
[ Teh ] [ the ] [ tea ] [ tech ]     ← every fix the engine returned; the first wears the primary style
        [ Ignore ] [ Copy ] [ Fix sentence ]
─────────────────────────
REPHRASE
We should arrange a meeting to discuss the report.   ← click one to replace the sentence
[ tone: as-is ▾ ] [ rephrase as-is ▾ ] [ Rephrase ]
```

- **A chip** replaces the finding's own words. The engine returns the alternatives best-first, and
  the first is styled as the primary one, so which fix is offered is visible rather than inferred.
- **Fix sentence** applies every correction in the line at once, and **Copy** puts that line on the
  clipboard. **Ignore** is a real answer and applies nothing.
- **Rephrase** is made by the card itself, not the watcher. The card is a local process on
  loopback and it owns the interaction, so the few seconds a small model needs are spent there
  instead of holding up every other application's suggestions. The call runs on a thread and
  returns through the UI thread, because Qt is not thread-safe either and a frozen card is worse than a
  card. The sentence sent is the **corrected** one: handing a small model your own errors invites
  it to preserve them.
- **The words appear while the model writes them.** The request always asks to stream, and what the
  card shows above the answers is the model's own text as it arrives — replaced by the parsed
  answers, with their change lines, when it finishes. On this machine that is about 50 ms to the first
  words against 1.5–2.5 s for the whole sentence (measured across runs; a cold model adds several
  seconds to both); before this, the card could only say "Rephrasing…" for that entire time. While it
  runs the Rephrase button becomes **Cancel**, which stops the read and drops the connection — the
  engine passes the request on to the model, so the model stops too rather than finishing a sentence
  nobody will see.
- **Each answer shows what it changed**, under the answer itself: struck-through and muted for the
  words that went, full contrast for the words that arrived, against the sentence you are about to
  replace. The sentence lives in the application behind the card, so it can never be shown beside the
  answer — the diff is the part of "before and after" this card can honestly hold. It is stdlib
  `difflib` over lower-cased words, so a model that only added a capital reports no change at all; a
  word that merely moved is named on both sides, because it did move.
- **The card says where a rephrase goes, before you click.** The line under the Rephrase button
  reads `Rephrase: ollama · qwen2.5:1.5b — nothing leaves this machine`, asked of the engine's own
  `/v1/ai` when the card opens; after a rephrase it becomes the model that actually answered and how
  long it took, from the rewrite's own response. The wording is the settings panel's, deliberately —
  two surfaces disagreeing about whether your text leaves the machine would be worse than either one
  alone — and when the engine cannot say, the line names the backend and drops the claim rather than
  guess. `GRAMMAR_POPUP_DEBUG=1` prints both writes to stderr (never stdout, which is the card's
  answer channel).
- The card **never takes focus**, so typing continues while it is up. That is also why it receives no
  key events of its own: Enter and Escape arrive from the desktop's shortcut system instead, through
  `grammar-action` (below). Clicking stays the interaction that needs no setup.
- Dismissed, it says nothing; a finding is mentioned once, and there is a 5 s cooldown between
  offers. When no card can be placed — the application will not say where the caret is, or there
  is no display — a notification with the same actions appears instead.
- **Pause for an hour** is there for the meeting or the deadline you cannot stop for, and
  `grammar-pause 15m|1h|off` does the same thing from a shell or a shortcut. The pause is a timestamp
  rather than a switch, so it ends by itself: nothing has to be remembered the next morning, and a
  machine that reboots comes back checking. `grammar-pause` with no argument prints the state, and
  `grammar-doctor` reports it — a checker that has gone quiet on purpose should be able to say so.
- **Both of those answers can be taken back from a shell**, which they could not before: the card's
  "Ignore in <application>" and "Pause for an hour" each write a file and offer no way out of it, so
  `grammar-pause --blocks` lists what you have ignored and `grammar-pause --unblock Firefox` brings
  checking back in one application (`--unblock` exits 1 when there was nothing of yours to undo, and
  says which kind of nothing: a name you never added, or one of the defaults, which are always spared).
  A command rather than an icon on purpose — see the note about tray icons in the skill's
  `references/ui-surface.md`: Qt's tray silently registers nothing on this desktop, and an icon that
  is never drawn is worse than no icon at all.
- **Ignore this word** appears only when the finding is one misspelled word, because what it adds to
  is a *word* list: a phrase, a clause or a whole sentence would be a promise that cannot be kept, and
  a grammar rule that happens to span one word (`She go`) is not a spelling — the word underneath it
  would be hidden in every later sentence too. The word goes to the engine
  (`POST /v2/ignore`), which is where every client's matches come through, so one list covers the
  whole machine and a client on the LAN in one go. The toast says the rest and it matters: **harper
  still flags the word, and every other editor still shows it.** What changed is that this engine
  stops repeating itself, which is not the same thing as the word being right. `grammar-doctor`
  reports how many words are on the list, and the list itself is a one-line-per-word file you can
  edit: `~/.config/grammar-server/ignored-words` on the machine the engine runs on.

### Accept and dismiss from the keyboard

The card cannot be given the keyboard, so the two keys you would expect on it are shortcuts instead —
one command each, bound by you:

```bash
grammar-action accept     # the same as clicking "Fix sentence"
grammar-action dismiss    # the same as "Ignore", or the ✕
```

Binding them (KDE): System Settings → **Shortcuts**, find *Accept the suggestion* and *Dismiss the
suggestion* (the launcher entries this installs), and give each a key. Ctrl+Alt+Return and
Ctrl+Alt+Escape are the suggestions, and nothing here claims either one. From a shell, the same thing:

```bash
kwriteconfig6 --file kglobalshortcutsrc --group services --group grammar-accept.desktop \
              --key _launch 'Ctrl+Alt+Return'
kwriteconfig6 --file kglobalshortcutsrc --group services --group grammar-dismiss.desktop \
              --key _launch 'Ctrl+Alt+Escape'
```

A new binding takes effect at the next login, because kglobalaccel reads its config when it starts.

How it works, since it is not obvious: the command leaves a marker in
`~/.cache/grammar-server/card-action`, and the card's own process — the only one that can answer for
it — picks the marker up and exits exactly as a click would. A card discards any stale marker as it
starts and consumes the one it acts on, so a press can never be applied twice or land on a card that
was not on screen when you made it. With no card up, the command does nothing at all, and a typo in a
shortcut says so in the journal rather than doing something arbitrary.

## Install (Linux, current user)

```bash
make install                                  # no sudo: ~/.local/bin + the watcher unit + a launcher
systemctl --user enable --now grammar-watch
journalctl --user -fu grammar-watch           # what it is doing (GRAMMAR_WATCH_DEBUG=1 for more)
```

`make uninstall` reverses it. No unit ever references a checkout.

## How it is checked

Three gates, all of them the scripts' own assertions — `make test` is the same thing CI runs.

```bash
python3 desktop/test-lookup.py        # fix logic, dialog contract, live engine
python3 desktop/test-watch.py         # sentence window, answer routing, listeners, the pause list
python3 desktop/test-popup-place.py   # payload, clamp() and the flip, the keyboard marker, where the card lands
```

**The live legs are opt-in**, through `GRAMMAR_LIVE=1`:

```bash
GRAMMAR_LIVE=1 python3 desktop/test-watch.py   # drives real LibreOffice and Kate windows
```

They open real applications on the real desktop, which is their whole value — and exactly why
they are off by default. Running the gate while someone is working types into a window and offers
cards at them; a first version did that, and the person using the machine saw suggestion cards
appearing for no reason of their own. Everything else runs anywhere, display or not, which is what
lets it pass in CI.

The card's placement is measured from the X server rather than from a screenshot: the card prints
`PLACED x y (asked X Y)`, and the gate searches for its own window **by pid**, because a card
belonging to the watcher may be on screen at the same time. KWin does not implement
`wlr-screencopy`, so no compositor screenshot exists here to argue with.

## Choosing the AI backend

Rephrase needs a model, and which one is a setting rather than a rebuild. It is the server's
setting, not this repo's:

```bash
curl -s localhost:8875/v1/ai                      # what is configured, and what it offers
curl -s -X POST localhost:8875/v1/ai -H 'Content-Type: application/json' \
     -d '{"provider":"llamacpp","url":"http://127.0.0.1:8080","model":""}'
```

`ollama`, `llamacpp`, `lmstudio`, `vllm`, `openrouter`, `openai` (any server speaking
`/v1/chat/completions`), or off. The model list comes from the backend's own `GET /v1/models`.

A key, for the backends that want one, comes from the environment variable the preset names — or from
your desktop keyring, if you typed one into the settings panel, in which case the engine keeps it
there through `secret-tool` and removes the plaintext file it used to write. A machine with no
keyring (no secret-tool, no session bus, a locked wallet) falls back to that file, 0600. Either way
the value is never returned over HTTP and never stored in this repo. Settings are accepted only from
the machine the server runs on, so a phone on the LAN can check text but cannot change the backend.

MIT licensed (see `LICENSE`).
