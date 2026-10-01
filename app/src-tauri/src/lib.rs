// No Rust surface of its own: the engine already sends `Access-Control-Allow-Origin: *`, so the whole
// UI is HTML/CSS/JS in ../src and needs no command. The template's `greet` demo is gone with it.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Without this the webview paints nothing on an NVIDIA stack: measured here as a single-colour
    // window with `Failed to create GBM buffer of size 460x760` on stderr, which is WebKitGTK's
    // DMA-BUF path. It has to be set before the webview is created, and it is left alone when it is
    // already set, so the fast path can be put back by whoever wants it.
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
