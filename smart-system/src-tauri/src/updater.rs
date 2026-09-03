//! MSS — Maunting Smart System, Automatischer Updater.
//!
//! Zentrale Update-Logik für Desktop und Android:
//! - Hintergrund-Prüfung: Prüft periodisch im Hintergrund auf neuere GitHub Releases.
//! - Fallback beim Start: Sofortige Prüfung beim Start via `update_pruefen`.
//! - Desktop: Nutzt `tauri_plugin_updater` mit strikter SemVer-Prüfung.
//! - Android: Nutzt GitHub API / latest.json und `tauri_plugin_opener` für die APK-Installation.

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

#[cfg(not(target_os = "android"))]
use tauri_plugin_updater::UpdaterExt;

#[cfg(target_os = "android")]
use tauri_plugin_notification::NotificationExt;

#[cfg(target_os = "android")]
use tauri_plugin_opener::OpenerExt;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct UpdateInfo {
    pub verfuegbar: bool,
    pub aktuelle_version: String,
    pub neue_version: Option<String>,
    pub download_url: Option<String>,
    pub notizen: Option<String>,
    pub ist_android: bool,
}

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

#[tauri::command(async)]
pub async fn update_pruefen(app: AppHandle) -> Result<UpdateInfo, String> {
    let current_version = app.package_info().version.to_string();

    #[cfg(not(target_os = "android"))]
    {
        let updater = app.updater().map_err(|e| format!("Updater nicht verfügbar: {e}"))?;
        match updater.check().await {
            Ok(Some(update)) => {
                let clean_version = update.version.trim_start_matches('v').to_string();
                Ok(UpdateInfo {
                    verfuegbar: true,
                    aktuelle_version: current_version,
                    neue_version: Some(clean_version),
                    download_url: None,
                    notizen: update.body.clone(),
                    ist_android: false,
                })
            }
            Ok(None) => Ok(UpdateInfo {
                verfuegbar: false,
                aktuelle_version: current_version,
                neue_version: None,
                download_url: None,
                notizen: None,
                ist_android: false,
            }),
            Err(e) => Err(format!("Update-Prüfung fehlgeschlagen: {e}")),
        }
    }

    #[cfg(target_os = "android")]
    {
        pruefe_android_update(&app, &current_version).await
    }
}

#[tauri::command(async)]
pub async fn update_installieren(app: AppHandle) -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        let updater = app.updater().map_err(|e| format!("Updater nicht verfügbar: {e}"))?;
        let update = match updater.check().await {
            Ok(Some(u)) => u,
            Ok(None) => return Err("Kein Update verfügbar".to_string()),
            Err(e) => return Err(format!("Fehler bei Update-Prüfung: {e}")),
        };

        let app_handle = app.clone();
        let _ = app.emit("mss:update-status", serde_json::json!({
            "status": "laedt",
            "prozent": 0
        }));

        let mut heruntergeladen: usize = 0;
        let res = update.download_and_install(
            move |chunk_len, gesamt_len| {
                heruntergeladen += chunk_len;
                if let Some(gesamt) = gesamt_len {
                    let prozent = (heruntergeladen as f64 / gesamt as f64 * 100.0) as u32;
                    let _ = app_handle.emit("mss:update-status", serde_json::json!({
                        "status": "laedt",
                        "prozent": prozent
                    }));
                }
            },
            || {}
        ).await;

        match res {
            Ok(()) => {
                let _ = app.emit("mss:update-status", serde_json::json!({
                    "status": "bereit"
                }));
                Ok(())
            }
            Err(e) => {
                let _ = app.emit("mss:update-status", serde_json::json!({
                    "status": "fehler",
                    "fehler": e.to_string()
                }));
                Err(format!("Download fehlgeschlagen: {e}"))
            }
        }
    }

    #[cfg(target_os = "android")]
    {
        let current_version = app.package_info().version.to_string();
        let info = pruefe_android_update(&app, &current_version).await?;
        if let Some(url) = info.download_url {
            let _ = app.emit("mss:update-status", serde_json::json!({
                "status": "installiert_android"
            }));
            app.opener().open_url(&url, None::<&str>).map_err(|e| format!("APK konnte nicht geöffnet werden: {e}"))?;
            Ok(())
        } else {
            Err("Keine APK-Download-URL gefunden".to_string())
        }
    }
}

#[tauri::command]
pub fn app_neu_starten(app: AppHandle) {
    app.restart();
}

#[cfg(target_os = "android")]
async fn pruefe_android_update(_app: &AppHandle, current_version: &str) -> Result<UpdateInfo, String> {
    let client = reqwest::Client::builder()
        .user_agent("MauntingSmartSystem-Android")
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())?;

    let mut latest_version: Option<String> = None;
    let mut download_url = "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSmartSystem.apk".to_string();
    let mut notizen: Option<String> = None;

    // 1. Primär: GitHub Releases API
    if let Ok(resp) = client
        .get("https://api.github.com/repos/einmalmaik/maunting-server-manager/releases/latest")
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
    {
        if resp.status().is_success() {
            if let Ok(text) = resp.text().await {
                if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(tag) = parsed.get("tag_name").and_then(|v| v.as_str()) {
                        latest_version = Some(tag.trim_start_matches('v').to_string());
                    }
                    if let Some(body) = parsed.get("body").and_then(|b| b.as_str()) {
                        notizen = Some(body.to_string());
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

    // 2. Fallback: latest.json
    if latest_version.is_none() {
        if let Ok(resp) = client
            .get("https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/latest.json")
            .send()
            .await
        {
            if resp.status().is_success() {
                if let Ok(text) = resp.text().await {
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                        if let Some(v) = parsed.get("version").and_then(|v| v.as_str()) {
                            latest_version = Some(v.trim_start_matches('v').to_string());
                        }
                        if let Some(notes) = parsed.get("notes").and_then(|n| n.as_str()) {
                            notizen = Some(notes.to_string());
                        }
                    }
                }
            }
        }
    }

    let Some(version_str) = latest_version else {
        return Err("Konnte neuestes Release nicht von GitHub abrufen".to_string());
    };

    let verfuegbar = ist_neuer(&version_str, current_version);
    Ok(UpdateInfo {
        verfuegbar,
        aktuelle_version: current_version.to_string(),
        neue_version: Some(version_str),
        download_url: Some(download_url),
        notizen,
        ist_android: true,
    })
}

#[allow(unused_variables)]
pub fn pruefe_und_installiere_update_hintergrund(app_handle: AppHandle) {
    std::thread::spawn(move || {
        // 4 Sekunden Pause nach dem Start, damit die Oberfläche sofort
        // reagiert und der Render-Thread nicht behindert wird.
        std::thread::sleep(std::time::Duration::from_secs(4));

        tauri::async_runtime::block_on(async move {
            #[cfg(not(target_os = "android"))]
            {
                let updater = match app_handle.updater() {
                    Ok(u) => u,
                    Err(e) => {
                        eprintln!("[MSS Updater] Plugin nicht verfügbar: {e}");
                        return;
                    }
                };

                match updater.check().await {
                    Ok(Some(update)) => {
                        let ziel_version = update.version.trim_start_matches('v').to_string();
                        println!("[MSS Updater] Hintergrund: Neues Release gefunden: v{ziel_version}. Starte Download...");

                        let _ = app_handle.emit("mss:update-verfuegbar", serde_json::json!({
                            "version": ziel_version,
                            "notizen": update.body.clone()
                        }));

                        let ergebnis = update
                            .download_and_install(
                                |_, _| {},
                                || {
                                    println!("[MSS Updater] Download abgeschlossen. Vorbereitet für Neustart...");
                                },
                            )
                            .await;

                        match ergebnis {
                            Ok(()) => {
                                println!("[MSS Updater] Update v{ziel_version} erfolgreich installiert. Bereit für Neustart.");
                                let _ = app_handle.emit("mss:update-bereit", serde_json::json!({
                                    "version": ziel_version
                                }));
                            }
                            Err(e) => {
                                eprintln!("[MSS Updater] Fehler bei Hintergrund-Download: {e}");
                            }
                        }
                    }
                    Ok(None) => {
                        println!("[MSS Updater] MSS ist auf dem aktuellen Stand.");
                    }
                    Err(e) => {
                        eprintln!("[MSS Updater] Fehler bei der Hintergrund-Update-Prüfung: {e}");
                    }
                }
            }

            #[cfg(target_os = "android")]
            {
                let current_version = app_handle.package_info().version.to_string();
                if let Ok(info) = pruefe_android_update(&app_handle, &current_version).await {
                    if info.verfuegbar {
                        let ziel_version = info.neue_version.unwrap_or_default();
                        println!("[MSS Android Updater] Neues Release gefunden: v{ziel_version}");

                        let _ = app_handle.notification().builder()
                            .title("MSS Update verfügbar")
                            .body(format!("Version v{ziel_version} ist verfügbar. Tippe zum Aktualisieren."))
                            .show();

                        let _ = app_handle.emit("mss:update-verfuegbar", serde_json::json!({
                            "version": ziel_version,
                            "apk_url": info.download_url,
                            "notizen": info.notizen
                        }));
                    } else {
                        println!("[MSS Android Updater] MSS APK ist auf dem aktuellen Stand (v{current_version}).");
                    }
                }
            }
        });
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
