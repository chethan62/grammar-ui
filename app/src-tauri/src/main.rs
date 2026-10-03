// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::Command;

fn main() {
    // One program, one menu entry. `grammar` opens the window; `grammar --lookup` runs the selection
    // checker. The checker itself stays the python helper installed beside this binary, because it is
    // the part that knows the platform: the primary selection (wl-paste, then xclip), KWin having no
    // virtual-keyboard protocol so a paste cannot be synthesised, kdialog, notify-send. Rewriting that
    // in Rust would buy nothing but new ways to get those wrong.
    if std::env::args().skip(1).any(|a| a == "--lookup") {
        std::process::exit(lookup());
    }
    app_lib::run()
}

/// The checker, resolved from this executable's own directory rather than $HOME or PATH: a GUI launch
/// has a thin environment, and a copy of this binary should find the helper that sits with it.
fn lookup() -> i32 {
    let mut helper = PathBuf::from("grammar-lookup");
    if let Ok(exe) = std::env::current_exe() {
        if let Some(bin) = exe.parent() {
            let beside = bin.join("grammar-lookup");
            if beside.is_file() {
                helper = beside;
            }
        }
    }
    // /usr/bin/python3 first, as the watcher unit does: a PATH that resolves to a virtualenv would
    // import-fail. The fallback is for machines that keep python elsewhere.
    let python = if PathBuf::from("/usr/bin/python3").is_file() {
        PathBuf::from("/usr/bin/python3")
    } else {
        PathBuf::from("python3")
    };
    match Command::new(&python).arg(&helper).status() {
        Ok(status) => status.code().unwrap_or(1),
        Err(e) => {
            eprintln!(
                "grammar --lookup: cannot run {} {}: {e}",
                python.display(),
                helper.display()
            );
            1
        }
    }
}
