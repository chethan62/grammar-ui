# grammar-ui

A static UI for [grammar-server](https://github.com/chethan62/grammar-server) —
and for any LanguageTool-compatible API, since it only speaks the public
endpoints.

**No framework, no build step, no dependencies.** Three files: `index.html`,
`app.js`, `style.css`.

## Run it

```bash
python3 -m http.server 8899      # or any static server, or just open index.html
```

(8899 is free on this box; 8888 is held by `passt`.)

Then point it at your server: the **API** field in the header defaults to the host that
served the page, port 8875 (`http://localhost:8875` on this machine, and the machine's own
address when you opened the page from a phone), and is remembered in `localStorage`.
Cross-origin requests work because grammar-server sends `Access-Control-Allow-Origin: *`.

`Test` asks the server for its version, so you can tell "wrong URL" from
"server down".

## Check text anywhere (no browser)

`grammar-lookup` checks whatever is **selected in the application you are in** — an editor,
a chat window, a terminal, a PDF, a text field — and needs no browser:

1. select the text (any app — the primary selection is what gets checked)
2. press **Ctrl+Alt+C**
3. the findings appear in a native dialog: **Copy fixed** puts the corrected text on your
   clipboard, **Show fixed** shows it instead, **Close** does nothing
4. Ctrl+V replaces your selection

The correction is the same one the web UI's *Fix all* produces, and it repeats until the
text stops changing: the engine's sentence-capitalisation rule suggests `Teh` for a typo at
the start of a sentence (it is correcting the capital, not the spelling), and only a second
pass turns that into `The`.

The dialog is `kdialog` on KDE and `zenity` elsewhere; if neither is present it falls back
to a notification with the fix already on your clipboard. It never fails silently.

```bash
grammar-lookup                     # dialog, as the shortcut runs it
GRAMMAR_NO_UI=1 grammar-lookup     # print the corrected text (handy in editors and scripts)
GRAMMAR_API=http://cachyos.local:8875 grammar-lookup   # check against another machine
GRAMMAR_LANG=en-GB grammar-lookup                      # check as a different language
python3 desktop/test-lookup.py     # the gate: fix logic, dialog contract, live engine
```

**The binding takes effect at the next login.** kglobalaccel reads `kglobalshortcutsrc`
when it starts, so `make install` writes `Ctrl+Alt+C` for the launcher but the running
session keeps its old set; log out and back in (or add it in System Settings → Shortcuts).

Two things it deliberately does not do. It cannot paste the fix for you: KWin does not
implement the Wayland virtual-keyboard protocol (`wtype` says so) and `ydotoold` is not
running, so the last Ctrl+V is yours — it does paste automatically if either becomes
available. And it checks on demand, not while you type; that would need an input method
(fcitx5/ibus), which is a different project.

## Install (Linux, current user)

```bash
make install
systemctl --user enable --now grammar-ui
```

Also installs `~/.local/bin/grammar-lookup` and its launcher entry, which is what
Ctrl+Alt+C runs.

Copies the three files to `~/.local/share/grammar-ui` and installs a user unit that
serves them on `0.0.0.0:8899` (python's stdlib server; no dependency to keep
patched, no build step). It binds the LAN so a phone can open the UI; the engine is
opened to the LAN to match, and grammar-server's README says what that costs. `--bind
127.0.0.1` in the unit and `make install` puts it back to loopback-only. The unit carries a soft `Wants=grammar-server.service`, so
the pair comes up together at login. `make uninstall` reverses it, `make serve` runs
it in the foreground for development, `make test` runs the CI gate.

## Features

| | |
|---|---|
| Live checking | `/v2/check` on a 400 ms debounce, issues listed with one-click replacements |
| Style tier | the request asks for `level=picky`, so wordiness, preferred terms (`e-mail` → `email`) and passive-voice hints appear alongside grammar |
| Fix all | applies the first replacement of every issue, right to left |
| Rephrase | `/v2/rewrite` — two alternatives from a local model, click one to swap it in |
| Delivery | `/v2/stats` — words, grade level, reading time |

Rephrase needs `rewrite_model` configured on the server and a running Ollama;
without them the endpoint answers `503` and the UI shows why.

## How it is checked

There is no build step, so there is nothing to compile — but the escaping rule is not
optional, because every string from the server is pasted into an HTML template, most of
them into attributes. `test/esc.test.js` pulls `esc()` out of `app.js` and asserts it
handles quotes, tags, ampersands and non-strings; CI runs that plus `node --check app.js`.

```bash
node test/esc.test.js
```

## Colours

Highlighting is driven by the LanguageTool category in the response
(`TYPOS` → spelling, `GRAMMAR` → grammar, `STYLE`/`REDUNDANCY` → style), so it
keeps working when the server adds rules.

MIT licensed (see `LICENSE`).
