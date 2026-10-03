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

/// The checker, resolved to an ABSOLUTE path: this binary's own directory first (which is where `make
/// install` and the bundle both put it), then a search of PATH.
///
/// The PATH search is not optional. A bare `grammar-lookup` handed to python is not a PATH lookup — it is
/// an argument, and python opens it relative to the working directory. Inside an AppImage that directory
/// is the bundle, so the host's helper was never found and the mode failed with
/// `can't open file '…/.mount_Gramma…/usr/grammar-lookup'`.
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
    if !helper.is_absolute() {
        if let Some(found) = std::env::var_os("PATH").and_then(|paths| {
            std::env::split_paths(&paths)
                .map(|dir| dir.join("grammar-lookup"))
                .find(|candidate| candidate.is_file())
        }) {
            helper = found;
        }
    }
    if !helper.is_file() {
        eprintln!(
            "grammar --lookup: no checker at {} — install the clients with `make install`",
            helper.display()
        );
        return 1;
    }
    // /usr/bin/python3 first, as the watcher unit does: a PATH that resolves to a virtualenv would
    // import-fail. The fallback is for machines that keep python elsewhere.
    let python = if PathBuf::from("/usr/bin/python3").is_file() {
        PathBuf::from("/usr/bin/python3")
    } else {
        PathBuf::from("python3")
    };
    let mut command = Command::new(&python);
    command.arg(&helper);
    // The helper is a host tool: it runs the host's python and talks to the host's engine. An AppImage
    // exports PYTHONHOME and PYTHONPATH pointing inside its own mount, and a python started with those
    // cannot find its standard library — measured from inside the bundle, it printed
    // "PYTHONHOME = '…/.mount_Gramma…/usr/'" and died before a single Python frame. Only those two are
    // removed: the rest of the environment (DISPLAY, PATH, the selection tools) is what the helper needs.
    for injected in ["PYTHONHOME", "PYTHONPATH"] {
        command.env_remove(injected);
    }
    match command.status() {
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
