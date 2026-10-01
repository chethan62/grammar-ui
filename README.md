# grammar-ui

The desktop side of [grammar-server](https://github.com/chethan62/grammar-server): a selection
checker and a typing watcher. **Python and stdlib — nothing to build, no browser, no
dependencies.**

There was a browser UI here, and then a suggestion card that appeared at the caret and a settings
window beside it. Both were removed at the user's request: the card was a Qt surface with its own
process, its own gate and its own two-window focus problem, and what is left does the checking
without it. This repo no longer contains a single line of UI code — no QML, no PySide6 — and the
removed work is in the history if it is ever wanted back.

## What is here

| `grammar-lookup` | checks the text you have **selected**, on **Ctrl+Alt+C** |
| `grammar-watch` | checks the sentence around your **caret** while you type, and offers a fix |
| `grammar-doctor` | is the whole chain working? Every silent failure this product has, named with its fix |
| `grammar-action` | `accept` / `dismiss` — what the keyboard shortcuts run |
| `grammar-pause` | `15m` / `1h` / `off` — silence the checker for a while, or bring it back; `--blocks` lists the applications you have ignored, `--unblock <app>` checks in one of them again |
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

Pause for a moment and the sentence around your **caret** is checked. What comes back is a desktop
notification with two buttons:

- **Fix it** applies the correction in the application, in place.
- **Copy fix** puts the corrected sentence on your clipboard instead.

The notification carries the finding with it: what was found and what it becomes, the reason the
engine gave, and the other suggestions it offered for the same words.

Nothing opens and nothing takes focus, which is why the answers are notification buttons — and why
the same two are on the keyboard, through the shortcuts this repo installs entries for:

- **Ctrl+Alt+Return** accepts, the same as *Fix it*.
- **Ctrl+Alt+Escape** dismisses: the notification goes, nothing is applied.

An application that publishes no accessible text (most terminals, Electron apps started without
`--force-renderer-accessibility`) simply gets no suggestions — the a11y bus is the only door into
another application's text, and there is no other way in.

**Ignoring a word and pausing an application used to be buttons on the card.** With the card gone
they have no UI. The engine still owns its ignore list (`GET`/`POST /v2/ignore`) and `grammar-pause`
still silences the checker and lists the applications you have paused — but both are a shell or an
HTTP call now. That is the cost of removing the surface, and it is better said than discovered.

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

A key, for the backends that want one, comes from the environment variable the preset names, or from
your desktop keyring if you put one there with `secret-tool` — the engine reads it either way and
never writes it in plaintext when a keyring is available. A machine with no
keyring (no secret-tool, no session bus, a locked wallet) falls back to that file, 0600. Either way
the value is never returned over HTTP and never stored in this repo. Settings are accepted only from
the machine the server runs on, so a phone on the LAN can check text but cannot change the backend.

MIT licensed (see `LICENSE`).
