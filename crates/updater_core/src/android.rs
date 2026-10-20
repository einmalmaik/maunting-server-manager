//! Android-Update-Pipeline (100 % Erhalt aller Mechanismen):
//! 1. Stufe 1: 2-stufige Versionsprüfung (Rate-Limit-Bypass über statisches JSON + Fallback GitHub API).
//! 2. Stufe 2: Streaming-Download & strikte APK-Validierung (ZIP-Magic 0x50 0x4B 0x03 0x04 & EOCD 0x50 0x4B 0x05 0x06).
//! 3. Stufe 3: Caching (.version) & PackageInstaller-Aufruf (Plugin `ApkInstallerPlugin`).

use std::path::Path;
use tauri::{AppHandle, Emitter, Manager};
use crate::config::UpdateConfig;
use crate::types::UpdateInfo;
use crate::semver::ist_neuer;

#[cfg(target_os = "android")]
pub struct AndroidInstallerState<R: tauri::Runtime>(pub tauri::plugin::PluginHandle<R>);

#[cfg(target_os = "android")]
pub fn init_android_installer<R: tauri::Runtime>(package_name: &'static str) -> tauri::plugin::TauriPlugin<R> {
    tauri::plugin::Builder::<R>::new("apkinstaller")
        .setup(move |app, api| {
            let handle = api.register_android_plugin(package_name, "ApkInstallerPlugin")?;
            app.manage(AndroidInstallerState(handle));
            Ok(())
        })
        .build()
}

pub fn erstelle_android_client(user_agent: &str) -> Result<reqwest::Client, String> {
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let tls_config = rustls::ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();

    reqwest::Client::builder()
        .user_agent(format!("{user_agent}-Android"))
        .connect_timeout(std::time::Duration::from_secs(15))
        .timeout(std::time::Duration::from_secs(30))
        .use_preconfigured_tls(tls_config)
        .build()
        .map_err(|e| format!("Konnte Android-HTTP-Client nicht erstellen: {e}"))
}

pub fn erstelle_android_download_client(user_agent: &str) -> Result<reqwest::Client, String> {
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let tls_config = rustls::ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();

    reqwest::Client::builder()
        .user_agent(format!("{user_agent}-Android"))
        .connect_timeout(std::time::Duration::from_secs(30))
        .read_timeout(std::time::Duration::from_secs(60))
        .use_preconfigured_tls(tls_config)
        .build()
        .map_err(|e| format!("Konnte Android-Download-Client nicht erstellen: {e}"))
}

pub use crate::apk::ist_valides_apk;

pub fn installiere_android_apk(app: &AppHandle, apk_pfad: &Path) -> Result<(), String> {
    #[cfg(target_os = "android")]
    {
        let pfad_str = apk_pfad.to_string_lossy().to_string();
        if let Some(installer) = app.try_state::<AndroidInstallerState<tauri::Wry>>() {
            installer.0.run_mobile_plugin::<()>("installApk", serde_json::json!({
                "apkPath": pfad_str
            })).map_err(|e| format!("APK-Installation über Android-Installer fehlgeschlagen: {e}"))?;
            Ok(())
        } else {
            Err("Android-Installer-Plugin nicht registriert".to_string())
        }
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, apk_pfad);
        Err("installiere_android_apk wird nur unter Android ausgeführt.".to_string())
    }
}

pub async fn pruefe_android_update(app: &AppHandle, config: &UpdateConfig) -> Result<UpdateInfo, String> {
    let current_version = app.package_info().version.to_string();
    let client = erstelle_android_client(config.user_agent)?;

    let mut latest_version: Option<String> = None;
    let mut download_url = format!("{}/{}", config.release_download_base, config.apk_file_name);
    let mut notizen: Option<String> = None;

    // 1. Primär: statisches Release Asset (z. B. latest-msb.json / latest.json), kein GitHub API Rate-Limit
    let asset_url = format!("{}/{}", config.release_download_base, config.asset_json_name);
    if let Ok(resp) = client.get(&asset_url).send().await {
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

    // 2. Sekundär: GitHub Releases API (exakte URL ohne willkürliche Umbenennung)
    if latest_version.is_none() {
        if let Ok(resp) = client
            .get(config.fallback_api_url)
            .header("Accept", "application/vnd.github+json")
            .header("User-Agent", config.user_agent)
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
                                    if name == config.apk_file_name {
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
    }

    let Some(version_str) = latest_version else {
        return Err("Konnte neuestes Release nicht von GitHub abrufen".to_string());
    };

    let verfuegbar = ist_neuer(&version_str, &current_version);
    Ok(UpdateInfo {
        verfuegbar,
        aktuelle_version: current_version,
        neue_version: Some(version_str),
        download_url: Some(download_url),
        notizen,
        ist_android: true,
    })
}

pub async fn lade_und_installiere_android(app: &AppHandle, config: &UpdateConfig) -> Result<(), String> {
    let info = pruefe_android_update(app, config).await?;
    if !info.verfuegbar {
        return Err("Kein Update verfügbar".to_string());
    }

    let Some(url) = info.download_url else {
        let fehler = "Keine APK-Download-URL gefunden".to_string();
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "fehler",
            "fehler": &fehler
        }));
        return Err(fehler);
    };

    let cache_dir = app.path().app_cache_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&cache_dir).map_err(|e| e.to_string())?;
    let apk_pfad = cache_dir.join(config.apk_file_name);
    let version_pfad = cache_dir.join(format!("{}.version", config.apk_file_name));
    let ziel_version = info.neue_version.unwrap_or_default();

    let bereits_im_cache = if apk_pfad.exists() && !ziel_version.is_empty() {
        if ist_valides_apk(&apk_pfad) {
            std::fs::read_to_string(&version_pfad)
                .map(|v| v.trim() == ziel_version)
                .unwrap_or(false)
        } else {
            let _ = std::fs::remove_file(&apk_pfad);
            let _ = std::fs::remove_file(&version_pfad);
            false
        }
    } else {
        false
    };

    if bereits_im_cache {
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "bereit",
            "prozent": 100
        }));
        installiere_android_apk(app, &apk_pfad)?;
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "installiert_android"
        }));
        return Ok(());
    }

    let _ = app.emit("updater:status", serde_json::json!({
        "status": "laedt",
        "prozent": 0
    }));

    let client = erstelle_android_download_client(config.user_agent)?;
    let mut resp = client.get(&url).send().await.map_err(|e| {
        let fehler = format!("Download fehlgeschlagen: {e}");
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "fehler",
            "fehler": &fehler
        }));
        fehler
    })?;

    if !resp.status().is_success() {
        let fehler = format!("Download-Server antwortete mit Status: {}", resp.status());
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "fehler",
            "fehler": &fehler
        }));
        return Err(fehler);
    }

    let gesamt_len = resp.content_length();
    let mut heruntergeladen: u64 = 0;
    let tmp_apk = cache_dir.join(format!("{}.tmp", config.apk_file_name));
    let _ = std::fs::remove_file(&tmp_apk);
    let mut file = std::fs::File::create(&tmp_apk).map_err(|e| {
        let fehler = format!("Konnte temporäre APK nicht anlegen: {e}");
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "fehler",
            "fehler": &fehler
        }));
        fehler
    })?;

    use std::io::Write;
    loop {
        match resp.chunk().await {
            Ok(Some(chunk)) => {
                if let Err(e) = file.write_all(&chunk) {
                    drop(file);
                    let _ = std::fs::remove_file(&tmp_apk);
                    let fehler = format!("Fehler beim Schreiben der APK: {e}");
                    let _ = app.emit("updater:status", serde_json::json!({
                        "status": "fehler",
                        "fehler": &fehler
                    }));
                    return Err(fehler);
                }
                heruntergeladen += chunk.len() as u64;
                if let Some(gesamt) = gesamt_len {
                    if gesamt > 0 {
                        let prozent = (heruntergeladen as f64 / gesamt as f64 * 100.0) as u32;
                        let _ = app.emit("updater:status", serde_json::json!({
                            "status": "laedt",
                            "prozent": prozent
                        }));
                    }
                }
            }
            Ok(None) => break,
            Err(e) => {
                drop(file);
                let _ = std::fs::remove_file(&tmp_apk);
                let fehler = format!("Download nach {heruntergeladen} Bytes abgebrochen: {e}");
                let _ = app.emit("updater:status", serde_json::json!({
                    "status": "fehler",
                    "fehler": &fehler
                }));
                return Err(fehler);
            }
        }
    }
    drop(file);

    if let Some(gesamt) = gesamt_len {
        if gesamt > 0 && heruntergeladen != gesamt {
            let _ = std::fs::remove_file(&tmp_apk);
            let fehler = format!("APK unvollständig empfangen: {heruntergeladen} von {gesamt} Bytes.");
            let _ = app.emit("updater:status", serde_json::json!({
                "status": "fehler",
                "fehler": &fehler
            }));
            return Err(fehler);
        }
    }

    if !ist_valides_apk(&tmp_apk) {
        let _ = std::fs::remove_file(&tmp_apk);
        let fehler = "Heruntergeladene APK-Datei ist ungültig oder beschädigt.".to_string();
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "fehler",
            "fehler": &fehler
        }));
        return Err(fehler);
    }

    std::fs::rename(&tmp_apk, &apk_pfad).map_err(|e| {
        let fehler = format!("Konnte APK nicht finalisieren: {e}");
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "fehler",
            "fehler": &fehler
        }));
        fehler
    })?;
    let _ = std::fs::write(&version_pfad, &ziel_version);

    let _ = app.emit("updater:status", serde_json::json!({
        "status": "bereit",
        "prozent": 100
    }));

    if let Err(e) = installiere_android_apk(app, &apk_pfad) {
        let fehler = format!("Installation fehlgeschlagen: {e}");
        let _ = app.emit("updater:status", serde_json::json!({
            "status": "fehler",
            "fehler": &fehler
        }));
        return Err(fehler);
    }

    let _ = app.emit("updater:status", serde_json::json!({
        "status": "installiert_android"
    }));
    Ok(())
}

