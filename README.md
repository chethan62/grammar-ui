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

## Install (Linux, current user)

```bash
make install
systemctl --user enable --now grammar-ui
```

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
