# grammar-ui

The desktop side of [grammar-server](https://github.com/chethan62/grammar-server): a selection
checker and a typing watcher. **Python and stdlib — nothing to build, no browser, no
dependencies.**

There was a browser UI here, then a suggestion card at the caret with a settings window beside it, and
now a Tauri app in [`app/`](app/). The Qt card was removed at the user's request; what replaced the
settings half of it is a webview window that runs on Linux and Windows from one source, since the
settings surface needs no caret placement and no window-manager tricks. The checking itself — the
selection tool and the typing watcher below — never had a UI in this repo and still does not: they
report through notifications and a dialog.

## What is here

| `grammar --lookup` | checks the text you have **selected**, on **Ctrl+Alt+C** — the same binary as the window |
| `grammar-watch` | checks the sentence around your **caret** while you type, and offers a fix |
| `grammar-doctor` | is the whole chain working? Every silent failure this product has, named with its fix |
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
grammar --lookup                                  # the dialog the shortcut runs
GRAMMAR_NO_UI=1 grammar --lookup                  # print the corrected text (scripts, editors)
GRAMMAR_API=http://cachyos.local:8875 grammar --lookup   # check against another machine
GRAMMAR_LANG=en-GB grammar --lookup               # check as another language
```

`grammar` is one program with two modes: no arguments opens the window, `--lookup` runs the selection
checker. The checker itself is the python helper installed beside the binary, which the mode runs — it is
an implementation detail, not a second command to remember. (`grammar-lookup` also stays on PATH for
scripts that already call it, and `grammar-doctor` checks it is there.)

The correction repeats until the text stops changing: the engine's sentence-capitalisation rule
suggests `Teh` for a typo at the start of a sentence — it is correcting the capital, not the
spelling — and only a second pass turns that into `The`.

It cannot paste for you: KWin does not implement the Wayland virtual-keyboard protocol (`wtype`
says so) and `ydotoold` is not running, so the last Ctrl+V is yours. It does paste automatically
if either becomes available.

**The shortcut is yours to bind** — System Settings → Shortcuts → *Check my selection*, or the
`kwriteconfig6` line under "Accept and dismiss from the keyboard" below, with the entry name
`grammar-lookup.desktop`. That entry carries `NoDisplay=true`: it exists so the shortcut has something
to bind to and runs the same binary as the Grammar entry, but it is deliberately not a second icon in
the menu. This Makefile writes no keys. A binding takes effect at the next login, because kglobalaccel
reads its config when it starts.

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

**Ignoring a word and pausing an application used to be buttons on the card.** The card is gone, and what
came back is uneven on purpose. Ignoring a word has a home again: the ignored list is editable in the
window's Settings tab (`GET`/`POST /v2/ignore`). The pause is *shown* there read-only (`GET /v2/pause`), so
it is visible without being settable — which is the honest split, since the watcher that owns it is
Linux-only and the window also ships on Windows. Blocking an application has no UI at all, and neither does
setting a pause: both are `grammar-pause` from a shell (`15m|1h|4h|off`, `--blocks`, `--unblock <app>`).
That is the cost of removing the surface, and it is better said than discovered.

### Completing a word

The draft completes the word you are typing, from harper's own dictionary (134,882 words) plus the words
this machine has been taught — the taught ones first, because that is the word someone is reaching for
again. `spec` offers `specular` before `specialist`; `webkit` offers `WebKitGTK`.

Two letters is the shortest question worth asking. A click accepts: in the draft the word is replaced and
the caret stays after it, and the word list's own box *fills in* rather than adding, so Add remains the only
thing that writes. Possessives come last, so `didn` still reaches `didn't` while `spec` offers words a person
would type. Affix stems (`specif`) do appear, because they are valid to harper and filtering them costs a
lookup per word.

The list comes from `GET /v2/complete?prefix=…` on the engine, so an editor client can offer the same
completions without knowing anything about harper.

## Install (Linux, current user)

```bash
make install                                  # no sudo: ~/.local/bin + the watcher unit + a launcher
systemctl --user enable --now grammar-watch
journalctl --user -fu grammar-watch           # what it is doing (GRAMMAR_WATCH_DEBUG=1 for more)
```

`make uninstall` reverses it. No unit ever references a checkout.

## Artifacts

| What | How | Carries |
| --- | --- | --- |
| `make dist` | one 32 MB `.tar.gz` | the engine, harper-ls/harper-cli, the window, the four clients, both units, the entries, the icon |
| AppImage | `npm run tauri build` in `app/` | the window alone, as one file |
| `.deb` / `.rpm` | the same command | the window alone, for those package managers |

`make dist` is the whole product in one archive — the only artifact that needs nothing but the host's
`webkit2gtk-4.1` and GTK3. It is a `~/.local`-shaped tree, so

```bash
tar -C ~/.local -xf dist/grammar-<version>-x86_64.tar.gz --strip-components=1
```

is the whole install, then the two units as INSTALL says. Verified by extracting it elsewhere and running
the engine out of it: `/status` reports its version and a real check finds `teh`, with nothing installed.

The engine half is not re-packed here: `make -C ../grammar-server package` builds the archive that already
carries harper-ls beside the binary, and this target unpacks that into the tree. Point `ENGINE_REPO=` at
wherever that checkout lives.

The AppImage carries the desktop half alone, and still needs the engine running. Flatpak is deliberately
not offered: its sandbox cannot read another application's selection, which is the entire point of
`grammar --lookup`.

One local snag, measured: Tauri runs `linuxdeploy --plugin gtk`, and that plugin searches `/usr/lib`
recursively — so a package that ships its own library copies under `/usr/lib/<name>/` (here `openshot-bin`)
is found before the system's, fails to resolve, and takes the AppImage step down while `.deb` and `.rpm`
succeed. Running `linuxdeploy` without `--plugin gtk` finishes the AppImage; a clean machine has no such
copy.

## How it is checked

Four gates, all of them the scripts' own assertions — `make test` is the same thing CI runs.

```bash
python3 desktop/test-lookup.py        # fix logic, dialog contract, live engine
python3 desktop/test-watch.py         # sentence window, answer routing, listeners, the pause list
```

The window itself is checked twice, at two levels:

```bash
make check-ui                         # drives the real window: clicks, navigation, the engine round-trip
node app/check.mjs                    # the JS modules against a stub DOM (part of `make test`)
```

`make check-ui` starts an app, clicks each tab and the buttons, and stops it again. It asserts the three
panels are really exclusive — a control from another tab on screen is a navigation bug, and the one thing
a stub DOM cannot see — that clicking **Check** reaches the engine (its own cache counters move, which
holds whatever the draft contains) and that the window then draws the *seeded* mistake as a finding. That
last assertion is about the word, not about "something appeared": the suite writes a known sentence into
the app's saved draft before launch and puts your own draft back afterwards, because a suite that fails the
moment someone's writing improves is worse than no suite. It also *types* — this desktop's Wayland session
takes no synthesised keystrokes, so the app is started on the X backend and the suite types through
`xdotool` — and asserts the autocomplete end to end: typing offers the engine's own completions, and
clicking one completes the word at the caret. It needs a display, `pyatspi`, and
`xdotool`, so it is not part of the CI gate: `make test` runs it too, and it skips there with the reason
it skipped. Clicks and navigation go through AT-SPI actions rather than synthesised input; only the typed
text goes through `xdotool`, on the X backend — KWin's Wayland session takes no keystrokes any other way.

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
