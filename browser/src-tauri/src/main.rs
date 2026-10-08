// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
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
