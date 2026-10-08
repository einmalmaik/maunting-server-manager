//! Das rahmenlose Hauptfenster: Minimieren, Maximieren, Schließen. Auf
//! Android gibt es kein Fenster dazu; die Oberfläche zeigt die Knöpfe dort nicht.

use tauri::AppHandle;
#[cfg(desktop)]
use tauri::Manager;

#[cfg(not(desktop))]
#[tauri::command(async)]
pub fn fenster_aktion(_app: AppHandle, _aktion: String) -> Result<(), String> {
    Err("Kein Fenster auf diesem Gerät".into())
}

#[cfg(desktop)]
#[tauri::command(async)]
pub fn fenster_aktion(app: AppHandle, aktion: String) -> Result<(), String> {
    let fenster = app.get_webview_window("main").ok_or("Hauptfenster fehlt")?;
    match aktion.as_str() {
        "minimieren" => fenster.minimize(),
        "maximieren" => {
            if fenster.is_maximized().unwrap_or(false) {
                fenster.unmaximize()
            } else {
                fenster.maximize()
            }
        }
        "schliessen" => fenster.close(),
        _ => return Err("Unbekannte Fensteraktion".into()),
    }
    .map_err(|e| e.to_string())
}
