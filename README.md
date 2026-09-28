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

Then point it at your server: the **API** field in the header defaults to
`http://localhost:8875` and is remembered in `localStorage`. Cross-origin
requests work because grammar-server sends `Access-Control-Allow-Origin: *`.

`Test` asks the server for its version, so you can tell "wrong URL" from
"server down".

## Features

| | |
|---|---|
| Live checking | `/v2/check` on a 400 ms debounce, issues listed with one-click replacements |
| Fix all | applies the first replacement of every issue, right to left |
| Rephrase | `/v2/rewrite` — two alternatives from a local model, click one to swap it in |
| Delivery | `/v2/stats` — words, grade level, reading time |

Rephrase needs `rewrite_model` configured on the server and a running Ollama;
without them the endpoint answers `503` and the UI shows why.

## Colours

Highlighting is driven by the LanguageTool category in the response
(`TYPOS` → spelling, `GRAMMAR` → grammar, `STYLE`/`REDUNDANCY` → style), so it
keeps working when the server adds rules.
