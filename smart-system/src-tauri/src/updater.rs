//! MSS — Maunting Smart System, Automatischer Updater.
//!
//! Prüft nach dem Anwendungsstart asynchron gegen echte GitHub Releases
//! (über die kanonische `latest.json`). Liegt ein Release vor, lädt der
//! Updater das Paket herunter, prüft Signatur und Prüfsumme, installiert
//! es sauber und stößt einen Neustart der Anwendung an.

use tauri::AppHandle;

#[cfg(not(target_os = "android"))]
use tauri_plugin_updater::UpdaterExt;

pub fn pruefe_und_installiere_update_hintergrund(app_handle: AppHandle) {
    #[cfg(not(target_os = "android"))]
    tauri::async_runtime::spawn(async move {
        // 5 Sekunden Pause nach dem Start, damit die Oberfläche sofort
        // reagiert und der Render-Thread nicht behindert wird.
        std::thread::sleep(std::time::Duration::from_secs(5));

        let updater = match app_handle.updater() {
            Ok(u) => u,
            Err(e) => {
                eprintln!("[MSS Updater] Plugin nicht verfügbar: {e}");
                return;
            }
        };

        match updater.check().await {
            Ok(Some(update)) => {
                let ziel_version = update.version.clone();
                println!("[MSS Updater] Neues Release gefunden: v{ziel_version}. Starte Download...");

                let mut heruntergeladen: usize = 0;
                let ergebnis = update
                    .download_and_install(
                        |chunk_len, gesamt_len| {
                            heruntergeladen += chunk_len;
                            if let Some(gesamt) = gesamt_len {
                                println!("[MSS Updater] Download: {heruntergeladen} / {gesamt} Bytes");
                            }
                        },
                        || {
                            println!("[MSS Updater] Download abgeschlossen. Installiere Update...");
                        },
                    )
                    .await;

                match ergebnis {
                    Ok(()) => {
                        println!("[MSS Updater] Update erfolgreich installiert. Starte MSS neu...");
                        app_handle.restart();
                    }
                    Err(e) => {
                        eprintln!("[MSS Updater] Fehler bei der Installation des Updates: {e}");
                    }
                }
            }
            Ok(None) => {
                println!("[MSS Updater] MSS ist auf dem aktuellen Stand.");
            }
            Err(e) => {
                eprintln!("[MSS Updater] Fehler bei der Update-Prüfung: {e}");
            }
        }
    });
}
