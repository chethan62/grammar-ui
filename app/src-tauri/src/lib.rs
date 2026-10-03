// No Rust surface of its own: the engine already sends `Access-Control-Allow-Origin: *`, so the whole
// UI is HTML/CSS/JS in ../src and needs no command. The template's `greet` demo is gone with it.
//
// The one thing the shell does besides show the webview is remember where the window was: a window that
// comes back where the user put it is one less thing to rearrange every launch, and the position file is
// two numbers in the app's own data dir — nothing more is worth a plugin.
use tauri::Manager;

fn position_file(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_data_dir().ok().map(|dir| dir.join("window-pos.json"))
}

fn saved_position(app: &tauri::AppHandle) -> Option<(i32, i32)> {
    let text = std::fs::read_to_string(position_file(app)?).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    Some((value.get("x")?.as_i64()? as i32, value.get("y")?.as_i64()? as i32))
}

fn save_position(app: &tauri::AppHandle, x: i32, y: i32) {
    if let Some(file) = position_file(app) {
        let _ = std::fs::write(file, format!("{{\"x\":{x},\"y\":{y}}}"));
    }
}

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
        .setup(|app| {
            // Setup runs before the window is mapped, so this is a placement hint the compositor honours.
            // A stale position (a monitor that is gone) is left for the window manager to rescue: KWin
            // moves off-screen windows back on, and the next close writes the corrected one.
            if let Some(window) = app.get_webview_window("main") {
                if let Some((x, y)) = saved_position(app.handle()) {
                    let _ = window.set_position(tauri::PhysicalPosition::new(x, y));
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // The position is saved on every move, not on close: measured, WM_DELETE tears the window down
            // as Destroyed without a CloseRequested, so the close path never fires. The last move is the
            // final position anyway, and this survives every way the window can go away.
            match event {
                tauri::WindowEvent::Moved(position) => {
                    save_position(window.app_handle(), position.x, position.y);
                }
                tauri::WindowEvent::CloseRequested { .. } => {
                    if let Ok(position) = window.outer_position() {
                        save_position(window.app_handle(), position.x, position.y);
                    }
                }
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
