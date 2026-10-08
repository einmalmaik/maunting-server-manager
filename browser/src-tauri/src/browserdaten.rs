//! Browserdaten der Seiten löschen: Cookies, Cache, Website-Speicher,
//! Berechtigungen. Getroffen wird nur das Seitenprofil (`tabs::seitenprofil`),
//! nie die Oberfläche mit Tresor und Messenger.
//!
//! Gelöscht wird über die WebView2 selbst (`ClearBrowsingDataAll`): solange
//! ihr Browserprozess läuft, ist der Ordner gesperrt. Gibt es gerade keinen
//! Tab, nimmt der Befehl einen unsichtbaren Helfer im Profil (`tabs::im_profil`).
//!
//! Mit „Beim Schließen vergessen“ läuft dasselbe beim Schließen des Fensters,
//! und beim Start fällt der Ordner, falls das Schließen nicht mehr dazu kam.

use std::time::Duration;

use tauri::AppHandle;

const FRIST: Duration = Duration::from_secs(15);

#[tauri::command(async)]
pub fn seitendaten_loeschen(app: AppHandle) -> Result<(), String> {
    loeschen(&app)
}

pub fn loeschen(app: &AppHandle) -> Result<(), String> {
    plattform::leeren(app)
}

/// Beim Start, bevor eine Webview das Profil öffnet.
pub fn beim_start(app: &AppHandle, vergessen: bool) {
    if !vergessen {
        return;
    }
    if let Ok(ordner) = crate::tabs::seitenprofil(app) {
        if let Err(fehler) = std::fs::remove_dir_all(&ordner) {
            if fehler.kind() != std::io::ErrorKind::NotFound {
                eprintln!("[MSB] Seitendaten beim Start nicht gelöscht: {fehler}");
            }
        }
    }
}

#[cfg(windows)]
mod plattform {
    use std::sync::mpsc;

    use tauri::AppHandle;
    use webview2_com::ClearBrowsingDataCompletedHandler;
    use webview2_com::Microsoft::Web::WebView2::Win32::{ICoreWebView2Profile2, ICoreWebView2_13};
    use windows::core::Interface;

    use super::FRIST;

    pub fn leeren(app: &AppHandle) -> Result<(), String> {
        let (tx, rx) = mpsc::channel::<Result<(), String>>();
        crate::tabs::im_profil(app, move |core| unsafe {
            core.cast::<ICoreWebView2_13>()
                .and_then(|c| c.Profile())
                .and_then(|p| p.cast::<ICoreWebView2Profile2>())
                .and_then(|profil| {
                    profil.ClearBrowsingDataAll(&ClearBrowsingDataCompletedHandler::create(Box::new(move |ergebnis| {
                        let _ = tx.send(ergebnis.map_err(|e| e.to_string()));
                        Ok(())
                    })))
                })
                .map_err(|e| e.to_string())
        })?;
        rx.recv_timeout(FRIST).map_err(|_| "Löschen hat nicht geantwortet".to_string())?
    }
}

#[cfg(not(windows))]
mod plattform {
    use tauri::AppHandle;

    pub fn leeren(_app: &AppHandle) -> Result<(), String> {
        Err("Auf dieser Plattform noch nicht verfügbar".into())
    }
}
