# grammar-ui app

The window: checking, rewriting, and the settings for both — one Tauri v2 app that runs on Linux and
Windows from this same source. Checking is live as you type; a finding can be applied by itself or the
whole text at once, with an undo; the report groups what the engine found by its own categories and says
how the text reads.

**No Rust of its own.** The engine sends `Access-Control-Allow-Origin: *`, so `src/main.js` calls it
directly with `fetch` — `/v2/check` and `/v2/stats` for the text, `/v2/rewrite` for a rephrasing,
`/v2/fix-sentence` and `/v2/dictionary` and `/v2/ignore` for the actions on a finding, `/v1/ai` for the
runner, `/v2/languages` and `/status` on load. `src-tauri/src/lib.rs` exists only to set one environment
variable (below) and start the window.

```bash
npm install
npm run tauri dev        # a window, against whatever engine is on 127.0.0.1:8875
npm run tauri build      # .deb/.rpm/.AppImage here, .msi/NSIS on Windows
```

Set `localStorage["grammar-api"]` if the engine is somewhere else.

**The AppImage does not build on this machine, and that is the machine's doing.** linuxdeploy walks the
dependencies of the system WebKitGTK stack with its own old `strip`, which cannot read the `.relr.dyn`
section these new libraries use (`NO_STRIP=1` silences that step), and then trips over a *different* app's
private copy of glib under `/usr/lib/openshot`. On a clean runner neither happens: CI builds the AppImage
and the Windows bundles, so `npm run tauri build` here is for the deb and the rpm. Measured sizes: deb
2.4 MB, AppImage 81 MB (the GTK plugin bundles the whole WebKitGTK stack, which the deb depends on
instead), Windows 2.1 MB msi + 1.4 MB installer.

**`WEBKIT_DISABLE_DMABUF_RENDERER=1` is not optional on this box and is set in `lib.rs`.** Without it the
webview paints nothing: measured as a window filled with a single colour and
`Failed to create GBM buffer of size 460x760` on stderr, with `10de:1f99, driver (null)` in the GL log.
It is set only when it is not already set, so the fast path can be put back.

## What is deliberately not here

**Pause** and **blocked applications**. They belong to the watcher, not to this window: `paused-until` and
`blocked-apps` are read by `grammar-watch` and written by `grammar-pause`, and the engine's HTTP surface has
no pause in it at all. The window is also the wrong home for them — it is the cross-platform client and the
watcher is Linux-only, so a Pause button here would be a control for a daemon a Windows user does not have.
`grammar-pause 15m|1h|off` is the interface, and it also lists and unblocks applications.
