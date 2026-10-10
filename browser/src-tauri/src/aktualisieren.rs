//! Updates unter Windows und Android:
//! - Windows: `tauri-plugin-updater` liest `latest-msb.json` aus dem jüngsten
//!   GitHub-Release und prüft die Signatur gegen den Schlüssel in `tauri.conf.json`.
//!   Installiert wird auf Klick (Installer beendet den Browser samt Tabs).
//! - Android: Liest `latest-msb.json` / GitHub Releases API, lädt `MauntingSecureBrowser.apk`
//!   in den Cache, validiert die ZIP-Struktur und stößt die Installation über
//!   Android PackageInstaller (`USER_ACTION_NOT_REQUIRED` ab Android 12) bzw.
//!   FileProvider-Fallback an.

use tauri::AppHandle;

#[cfg(target_os = "android")]
use tauri::Manager;

/// Ein Debug-Bau (`tauri dev`) installiert nie ein Update: der Installer
/// ersetzt die installierte App, nicht den Entwicklungsbau (AGENTS.md, Punkt 67).
pub const SELBST_AKTUALISIEREN: bool = !cfg!(debug_assertions);

#[cfg(target_os = "android")]
pub struct AndroidInstallerState<R: tauri::Runtime>(pub tauri::plugin::PluginHandle<R>);

#[cfg(target_os = "android")]
pub fn init_android_installer<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    tauri::plugin::Builder::<R>::new("apkinstaller")
        .setup(|app, api| {
            let handle = api.register_android_plugin(crate::tabs::android::PAKET, "ApkInstallerPlugin")?;
            app.manage(AndroidInstallerState(handle));
            Ok(())
        })
        .build()
}

/// Prüft, ob eine Version neuer ist als die aktuelle (SemVer-Zahlenvergleich).
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

/// Prüft, ob eine Datei ein vollständiges, unbeschädigtes APK (ZIP-Archiv) ist:
/// - Datei existiert und ist mindestens 10 MB groß.
/// - Beginnt mit der ZIP-Header-Signatur `PK\x03\x04` ([0x50, 0x4B, 0x03, 0x04]).
/// - Endet mit dem End-of-Central-Directory Record `PK\x05\x06` ([0x50, 0x4B, 0x05, 0x06])
///   innerhalb der letzten 65557 Bytes.
pub fn ist_valides_apk(pfad: &std::path::Path) -> bool {
    use std::io::{Read, Seek, SeekFrom};
    let Ok(mut file) = std::fs::File::open(pfad) else {
        return false;
    };
    let Ok(meta) = file.metadata() else {
        return false;
    };
    let len = meta.len();
    if len < 10_000_000 {
        return false;
    }

    let mut header = [0u8; 4];
    if file.read_exact(&mut header).is_err() || header != [0x50, 0x4B, 0x03, 0x04] {
        return false;
    }

    let tail_len = (len.min(65557)) as usize;
    if file.seek(SeekFrom::End(-(tail_len as i64))).is_err() {
        return false;
    }

    let mut tail_buffer = vec![0u8; tail_len];
    if file.read_exact(&mut tail_buffer).is_err() {
        return false;
    }

    tail_buffer.windows(4).any(|w| w == [0x50, 0x4B, 0x05, 0x06])
}

#[cfg(target_os = "android")]
pub fn installiere_android_apk(app: &AppHandle, apk_pfad: &std::path::Path) -> Result<(), String> {
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

#[cfg(target_os = "android")]
fn erstelle_android_client() -> Result<reqwest::Client, String> {
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let tls_config = rustls::ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();

    reqwest::Client::builder()
        .user_agent("MauntingSecureBrowser-Android")
        .connect_timeout(std::time::Duration::from_secs(15))
        .timeout(std::time::Duration::from_secs(30))
        .use_preconfigured_tls(tls_config)
        .build()
        .map_err(|e| format!("Konnte Android-HTTP-Client nicht erstellen: {e}"))
}

#[cfg(target_os = "android")]
fn erstelle_android_download_client() -> Result<reqwest::Client, String> {
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let tls_config = rustls::ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();

    reqwest::Client::builder()
        .user_agent("MauntingSecureBrowser-Android")
        .connect_timeout(std::time::Duration::from_secs(30))
        .read_timeout(std::time::Duration::from_secs(60))
        .use_preconfigured_tls(tls_config)
        .build()
        .map_err(|e| format!("Konnte Android-Download-Client nicht erstellen: {e}"))
}

/// Die neuere Version, wenn es eine gibt.
#[tauri::command(async)]
pub async fn update_pruefen(app: AppHandle) -> Result<Option<String>, String> {
    plattform::pruefen(&app).await
}

/// Lädt das Update, prüft die Signatur bzw. APK und startet den Installer.
#[tauri::command(async)]
pub async fn update_installieren(app: AppHandle) -> Result<(), String> {
    if !SELBST_AKTUALISIEREN {
        return Err("Ein Entwicklungsbau installiert keine Updates.".into());
    }
    plattform::installieren(&app).await
}

#[cfg(desktop)]
mod plattform {
    use tauri::AppHandle;
    use tauri_plugin_updater::UpdaterExt;

    pub async fn pruefen(app: &AppHandle) -> Result<Option<String>, String> {
        let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
        Ok(update.map(|u| u.version.trim_start_matches('v').to_string()))
    }

    pub async fn installieren(app: &AppHandle) -> Result<(), String> {
        let update = app
            .updater()
            .map_err(|e| e.to_string())?
            .check()
            .await
            .map_err(|e| e.to_string())?
            .ok_or("Kein Update verfügbar")?;
        update.download_and_install(|_, _| {}, || {}).await.map_err(|e| e.to_string())
    }
}

#[cfg(mobile)]
mod plattform {
    use tauri::AppHandle;

    #[cfg(target_os = "android")]
    use tauri::Manager;

    pub async fn pruefen(app: &AppHandle) -> Result<Option<String>, String> {
        #[cfg(target_os = "android")]
        {
            let current_version = app.package_info().version.to_string();
            let client = super::erstelle_android_client()?;

            let mut latest_version: Option<String> = None;

            // 1. Primär: latest-msb.json (Release Asset, direkt erreichbar, kein GitHub API Rate-Limit)
            if let Ok(resp) = client
                .get("https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/latest-msb.json")
                .send()
                .await
            {
                if resp.status().is_success() {
                    if let Ok(text) = resp.text().await {
                        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                            if let Some(v) = parsed.get("version").and_then(|v| v.as_str()) {
                                latest_version = Some(v.trim_start_matches('v').to_string());
                            }
                        }
                    }
                }
            }

            // 2. Sekundär: GitHub Releases API
            if latest_version.is_none() {
                if let Ok(resp) = client
                    .get("https://api.github.com/repos/einmalmaik/maunting-server-manager/releases/latest")
                    .header("Accept", "application/vnd.github+json")
                    .header("User-Agent", "MauntingSecureBrowser")
                    .send()
                    .await
                {
                    if resp.status().is_success() {
                        if let Ok(text) = resp.text().await {
                            if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                                if let Some(tag) = parsed.get("tag_name").and_then(|v| v.as_str()) {
                                    latest_version = Some(tag.trim_start_matches('v').to_string());
                                }
                            }
                        }
                    }
                }
            }

            let Some(version_str) = latest_version else {
                return Err("Konnte neuestes Release nicht von GitHub abrufen".to_string());
            };

            if super::ist_neuer(&version_str, &current_version) {
                Ok(Some(version_str))
            } else {
                Ok(None)
            }
        }

        #[cfg(not(target_os = "android"))]
        {
            let _ = app;
            Ok(None)
        }
    }

    pub async fn installieren(app: &AppHandle) -> Result<(), String> {
        #[cfg(target_os = "android")]
        {
            let neu = pruefen(app).await?;
            let ziel_version = neu.ok_or_else(|| "Kein Update verfügbar".to_string())?;

            let download_url = "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSecureBrowser.apk";

            let cache_dir = app.path().app_cache_dir().map_err(|e| e.to_string())?;
            std::fs::create_dir_all(&cache_dir).map_err(|e| e.to_string())?;
            let apk_pfad = cache_dir.join("MauntingSecureBrowser.apk");
            let version_pfad = cache_dir.join("MauntingSecureBrowser.apk.version");

            let bereits_im_cache = if apk_pfad.exists() && !ziel_version.is_empty() {
                if super::ist_valides_apk(&apk_pfad) {
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
                return super::installiere_android_apk(app, &apk_pfad);
            }

            let client = super::erstelle_android_download_client()?;
            let mut resp = client.get(download_url).send().await.map_err(|e| format!("Download fehlgeschlagen: {e}"))?;

            if !resp.status().is_success() {
                return Err(format!("Download-Server antwortete mit Status: {}", resp.status()));
            }

            let gesamt_len = resp.content_length();
            let mut heruntergeladen: u64 = 0;
            let tmp_apk = cache_dir.join("MauntingSecureBrowser.apk.tmp");
            let _ = std::fs::remove_file(&tmp_apk);
            let mut file = std::fs::File::create(&tmp_apk).map_err(|e| format!("Konnte temporäre APK nicht anlegen: {e}"))?;

            use std::io::Write;
            loop {
                match resp.chunk().await {
                    Ok(Some(chunk)) => {
                        if let Err(e) = file.write_all(&chunk) {
                            drop(file);
                            let _ = std::fs::remove_file(&tmp_apk);
                            return Err(format!("Fehler beim Schreiben der APK: {e}"));
                        }
                        heruntergeladen += chunk.len() as u64;
                    }
                    Ok(None) => break,
                    Err(e) => {
                        drop(file);
                        let _ = std::fs::remove_file(&tmp_apk);
                        return Err(format!("Download nach {heruntergeladen} Bytes abgebrochen: {e}"));
                    }
                }
            }
            drop(file);

            if let Some(gesamt) = gesamt_len {
                if gesamt > 0 && heruntergeladen != gesamt {
                    let _ = std::fs::remove_file(&tmp_apk);
                    return Err(format!("APK unvollständig empfangen: {heruntergeladen} von {gesamt} Bytes."));
                }
            }

            if !super::ist_valides_apk(&tmp_apk) {
                let _ = std::fs::remove_file(&tmp_apk);
                return Err("Heruntergeladene APK-Datei ist ungültig oder beschädigt.".to_string());
            }

            std::fs::rename(&tmp_apk, &apk_pfad).map_err(|e| format!("Konnte APK nicht finalisieren: {e}"))?;
            let _ = std::fs::write(&version_pfad, &ziel_version);

            super::installiere_android_apk(app, &apk_pfad)
        }

        #[cfg(not(target_os = "android"))]
        {
            let _ = app;
            Err("Unter dieser Plattform nicht verfügbar.".into())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(debug_assertions)]
    fn debug_bau_installiert_nie_ein_update() {
        assert!(!super::SELBST_AKTUALISIEREN);
    }

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

    #[test]
    fn ist_valides_apk_prueft_existenz_mindestgroesse_und_zip_struktur() {
        use std::io::Write;
        let temp_dir = std::env::temp_dir();

        // 1. Nicht existierende Datei
        let nicht_da = temp_dir.join("msb_test_nicht_existent.apk");
        assert!(!ist_valides_apk(&nicht_da));

        // 2. Datei zu klein (< 10 MB)
        let zu_klein = temp_dir.join("msb_test_zu_klein.apk");
        std::fs::write(&zu_klein, &[0x50, 0x4B, 0x03, 0x04, 0x00, 0x00]).unwrap();
        assert!(!ist_valides_apk(&zu_klein));
        let _ = std::fs::remove_file(&zu_klein);

        // 3. Datei >= 10 MB, aber falscher Header (kein ZIP)
        let kein_zip = temp_dir.join("msb_test_kein_zip.apk");
        {
            let mut f = std::fs::File::create(&kein_zip).unwrap();
            f.write_all(&[0x00; 10_000_050]).unwrap();
        }
        assert!(!ist_valides_apk(&kein_zip));
        let _ = std::fs::remove_file(&kein_zip);

        // 4. Datei >= 10 MB, ZIP-Header vorhanden, aber EOCD fehlt (abgebrochener Download)
        let unvollstaendig = temp_dir.join("msb_test_unvollstaendig.apk");
        {
            let mut f = std::fs::File::create(&unvollstaendig).unwrap();
            f.write_all(&[0x50, 0x4B, 0x03, 0x04]).unwrap();
            f.write_all(&vec![0xAA; 10_000_050]).unwrap();
        }
        assert!(!ist_valides_apk(&unvollstaendig));
        let _ = std::fs::remove_file(&unvollstaendig);

        // 5. Datei >= 10 MB, ZIP-Header vorhanden und EOCD am Ende vorhanden (vollständig)
        let vollstaendig = temp_dir.join("msb_test_vollstaendig.apk");
        {
            let mut f = std::fs::File::create(&vollstaendig).unwrap();
            f.write_all(&[0x50, 0x4B, 0x03, 0x04]).unwrap();
            f.write_all(&vec![0x00; 10_000_000]).unwrap();
            // EOCD Signatur + 18 Dummy Bytes
            f.write_all(&[0x50, 0x4B, 0x05, 0x06]).unwrap();
            f.write_all(&[0x00; 18]).unwrap();
        }
        assert!(ist_valides_apk(&vollstaendig));
        let _ = std::fs::remove_file(&vollstaendig);
    }

    /// Der Browser liest sein eigenes Manifest. `latest.json` gehört dem
    /// Smart System; dieselbe Datei für beide Apps hätte die eine mit dem
    /// Installer der anderen überschrieben.
    #[test]
    fn der_browser_liest_sein_eigenes_manifest() {
        let konfig: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let updater = &konfig["plugins"]["updater"];
        let endpunkte = updater["endpoints"].as_array().unwrap();
        assert_eq!(endpunkte.len(), 1);
        assert_eq!(
            endpunkte[0],
            "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/latest-msb.json"
        );
        assert!(!updater["pubkey"].as_str().unwrap().is_empty());
        assert_eq!(konfig["bundle"]["createUpdaterArtifacts"], true);
    }
}
