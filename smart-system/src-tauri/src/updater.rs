//! MSS — Maunting Smart System, Automatischer Updater.
//!
//! Prüft nach dem Anwendungsstart asynchron gegen echte GitHub Releases
//! (über die kanonische `latest.json`).
//! - Desktop (Windows/Linux/macOS): Lädt das Paket herunter, prüft Signatur
//!   und Prüfsumme, installiert es sauber und stößt einen Neustart an.
//! - Android (APK): Prüft die neueste Version gegen `latest.json`, benachrichtigt
//!   den Nutzer via nativer Benachrichtigung und sendet ein Event an das Frontend
//!   mit dem direkten Download-Link zur aktuellen APK.

use tauri::AppHandle;

#[cfg(not(target_os = "android"))]
use tauri_plugin_updater::UpdaterExt;

#[cfg(target_os = "android")]
use tauri::Emitter;
#[cfg(target_os = "android")]
use tauri_plugin_notification::NotificationExt;

pub fn ist_neuer(ziel: &str, aktuell: &str) -> bool {
    let ziel_teile: Vec<u64> = ziel
        .trim_start_matches('v')
        .split('.')
        .filter_map(|s| s.parse().ok())
        .collect();
    let akt_teile: Vec<u64> = aktuell
        .trim_start_matches('v')
        .split('.')
        .filter_map(|s| s.parse().ok())
        .collect();

    for (z, a) in ziel_teile.iter().zip(akt_teile.iter()) {
        if z > a {
            return true;
        }
        if z < a {
            return false;
        }
    }
    ziel_teile.len() > akt_teile.len()
}

#[allow(unused_variables)]
pub fn pruefe_und_installiere_update_hintergrund(app_handle: AppHandle) {
    #[cfg(not(target_os = "android"))]
    std::thread::spawn(move || {
        // 5 Sekunden Pause nach dem Start, damit die Oberfläche sofort
        // reagiert und der Render-Thread nicht behindert wird.
        std::thread::sleep(std::time::Duration::from_secs(5));

        tauri::async_runtime::block_on(async move {
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
    });

    #[cfg(target_os = "android")]
    std::thread::spawn(move || {
        // 5 Sekunden Pause nach dem Start
        std::thread::sleep(std::time::Duration::from_secs(5));

        let client = match reqwest::blocking::Client::builder()
            .user_agent("MauntingSmartSystem-Android")
            .timeout(std::time::Duration::from_secs(10))
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[MSS Android Updater] HTTP-Client konnte nicht erstellt werden: {e}");
                return;
            }
        };

        // 1. Primär: Offizielle GitHub Releases API abfragen (immer verfügbar, kein latest.json nötig)
        let mut latest_version: Option<String> = None;
        let mut download_url = "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSmartSystem.apk".to_string();

        if let Ok(resp) = client
            .get("https://api.github.com/repos/einmalmaik/maunting-server-manager/releases/latest")
            .header("Accept", "application/vnd.github+json")
            .send()
        {
            if resp.status().is_success() {
                if let Ok(text) = resp.text() {
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                        if let Some(tag) = parsed.get("tag_name").and_then(|v| v.as_str()) {
                            latest_version = Some(tag.trim_start_matches('v').to_string());
                        }
                        if let Some(assets) = parsed.get("assets").and_then(|a| a.as_array()) {
                            for asset in assets {
                                if let Some(name) = asset.get("name").and_then(|n| n.as_str()) {
                                    if name == "MauntingSmartSystem.apk" {
                                        if let Some(url) = asset.get("browser_download_url").and_then(|u| u.as_str()) {
                                            download_url = url.to_string();
                                        }
                                        break;
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // 2. Fallback: latest.json prüfen, falls GitHub API nicht erreichbar war
        if latest_version.is_none() {
            if let Ok(resp) = client
                .get("https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/latest.json")
                .send()
            {
                if resp.status().is_success() {
                    if let Ok(text) = resp.text() {
                        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                            if let Some(v) = parsed.get("version").and_then(|v| v.as_str()) {
                                latest_version = Some(v.trim_start_matches('v').to_string());
                            }
                        }
                    }
                }
            }
        }

        let latest_version_str = match latest_version {
            Some(v) => v,
            None => {
                eprintln!("[MSS Android Updater] Konnte kein neueres Release ermitteln (GitHub API / latest.json unerreichbar)");
                return;
            }
        };

        let current_version = app_handle.package_info().version.to_string();
        if ist_neuer(&latest_version_str, &current_version) {
            println!("[MSS Android Updater] Neues Release gefunden: v{latest_version_str} (aktuell: v{current_version})");

            let _ = app_handle.notification().builder()
                .title("MSS Update verfügbar")
                .body(format!("Version v{latest_version_str} ist verfügbar. Tippe zum Herunterladen der neuen APK."))
                .show();

            let _ = app_handle.emit("mss:apk-update-verfuegbar", serde_json::json!({
                "version": latest_version_str,
                "aktuell": current_version,
                "apk_url": download_url
            }));
        } else {
            println!("[MSS Android Updater] MSS APK ist auf dem aktuellen Stand (v{current_version}).");
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_vergleich_erkennt_neuere_versionen() {
        assert!(ist_neuer("0.2.0", "0.1.9"));
        assert!(ist_neuer("1.0.0", "0.9.9"));
        assert!(ist_neuer("v0.1.10", "0.1.9"));
        assert!(ist_neuer("0.1.9.1", "0.1.9"));
        assert!(!ist_neuer("0.1.9", "0.1.9"));
        assert!(!ist_neuer("0.1.8", "0.1.9"));
        assert!(!ist_neuer("0.1.0", "1.0.0"));
    }
}
