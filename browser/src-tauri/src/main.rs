// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// Velopack ist der einzige Windows-Installer und -Updater. Seine Hooks
/// ersetzen die NSIS-Hooks (`windows/hooks.nsh` bis 10/2026): nach Installation
/// und Update meldet sich der Browser bei Windows als `StartMenuInternet`-Client
/// an, bei der Entfernung nimmt er es zurück. Auf Android gibt es Velopack nicht.
#[cfg(not(target_os = "android"))]
fn velopack_starten() {
    #[allow(unused_mut)]
    let mut app = updater_core::velopack_app();
    #[cfg(windows)]
    {
        app = app
            .on_after_install_fast_callback(|_| {
                let _ = maunting_secure_browser_lib::widget::standardbrowser_registrieren();
            })
            .on_after_update_fast_callback(|_| {
                let _ = maunting_secure_browser_lib::widget::standardbrowser_registrieren();
            })
            .on_before_uninstall_fast_callback(|_| {
                let _ = maunting_secure_browser_lib::widget::standardbrowser_entfernen();
            });
    }
    app.run();
}

fn main() {
    #[cfg(not(target_os = "android"))]
    velopack_starten();

    // Im Release gelten keine WebView2-Vorgaben aus der Umgebung: sie ersetzten
    // die Argumente der Tabs (etwa um einen Debug-Port oder ohne SmartScreen-
    // Schalter) oder lenkten auf eine andere Laufzeit und einen anderen
    // Datenordner. Unter `tauri dev` braucht die Probe sie (AGENTS.md 123).
    #[cfg(not(debug_assertions))]
    for (name, _) in std::env::vars_os() {
        if name.to_string_lossy().to_ascii_uppercase().starts_with("WEBVIEW2_") {
            std::env::remove_var(name);
        }
    }
    maunting_secure_browser_lib::run();
}
