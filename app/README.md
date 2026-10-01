# grammar-ui app

The settings window: the ignored words and the AI runner, in one Tauri v2 window that runs on Linux and
Windows from this same source.

**No Rust of its own.** The engine sends `Access-Control-Allow-Origin: *`, so `src/main.js` calls it
directly with `fetch` — `/v1/ai` for the runner and `/v2/ignore` for the words. `src-tauri/src/lib.rs`
exists only to set one environment variable (below) and start the window.

```bash
npm install
npm run tauri dev        # a window, against whatever engine is on 127.0.0.1:8875
npm run tauri build      # .deb/.rpm/.AppImage here, .msi/NSIS on Windows
```

Set `localStorage["grammar-api"]` if the engine is somewhere else.

**`WEBKIT_DISABLE_DMABUF_RENDERER=1` is not optional on this box and is set in `lib.rs`.** Without it the
webview paints nothing: measured as a window filled with a single colour and
`Failed to create GBM buffer of size 460x760` on stderr, with `10de:1f99, driver (null)` in the GL log.
It is set only when it is not already set, so the fast path can be put back.

## Still to come

**Paused applications** and **Pause** are in the old window and not this one. Both are owned by files
(`~/.config/grammar-server/blocked-apps`, `~/.cache/grammar-server/paused-until`) rather than by the
engine's HTTP surface, so each needs a small Rust command; the ignored words and the runner were the two
whose owner already speaks HTTP.
