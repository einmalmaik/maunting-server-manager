//! Das rahmenlose Hauptfenster: Minimieren, Maximieren, Schließen. Auf
//! Android gibt es kein Fenster dazu; die Oberfläche zeigt die Knöpfe dort nicht.

use tauri::AppHandle;
#[cfg(desktop)]
use tauri::Manager;

#[derive(serde::Serialize, Clone, Debug)]
pub struct FensterStartInfo {
    pub ist_neues_fenster: bool,
    pub start_url: Option<String>,
    pub privat: bool,
}

#[derive(Clone, Debug, Default)]
pub struct StartArgumente {
    pub ist_neues_fenster: bool,
    pub start_url: Option<String>,
    pub privat: bool,
}

#[cfg(desktop)]
pub fn fenster_nach_vorn(fenster: &tauri::WebviewWindow) {
    let _ = fenster.unminimize();
    let _ = fenster.show();
    let _ = fenster.set_focus();
    #[cfg(windows)]
    if let Ok(hwnd) = fenster.hwnd() {
        use windows::Win32::Foundation::HWND;
        use windows::Win32::UI::WindowsAndMessaging::{
            BringWindowToTop, SetForegroundWindow, ShowWindow, SW_SHOW,
        };
        unsafe {
            let h = HWND(hwnd.0);
            let _ = ShowWindow(h, SW_SHOW);
            let _ = BringWindowToTop(h);
            let _ = SetForegroundWindow(h);
        }
    }
}

/// Informationen zum Start dieses Fensterprozesses (z. B. als ausgelagertes Einzelfenster).
#[tauri::command(async)]
pub fn fenster_start_info(app: AppHandle) -> Result<FensterStartInfo, String> {
    #[cfg(desktop)]
    {
        if let Some(start) = app.try_state::<StartArgumente>() {
            return Ok(FensterStartInfo {
                ist_neues_fenster: start.ist_neues_fenster,
                start_url: start.start_url.clone(),
                privat: start.privat,
            });
        }
    }
    Ok(FensterStartInfo {
        ist_neues_fenster: false,
        start_url: None,
        privat: false,
    })
}

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
        "neustarten" => {
            tauri::process::restart(&app.env());
        }
        _ => return Err("Unbekannte Fensteraktion".into()),
    }
    .map_err(|e| e.to_string())
}

