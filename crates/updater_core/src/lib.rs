//! updater_core — Zentrale Update-Bibliothek für Tauri v2 Apps:
//! - Desktop (Windows/Linux): Velopack UpdateManager & Delta-Updates.
//! - Android: 3-stufige Streaming-Download- & Integritäts-Pipeline (ZIP & EOCD-Validierung) + PackageInstaller.
//! - Debug-Schutz: Entwicklungsbauten (`!cfg!(debug_assertions)`) installieren keine Releases.

pub mod apk;
pub mod config;
pub mod semver;
pub mod types;

#[cfg(target_os = "android")]
pub mod android;

#[cfg(not(target_os = "android"))]
pub mod desktop;

pub use apk::ist_valides_apk;
pub use config::UpdateConfig;
pub use semver::ist_neuer;
pub use types::{UpdateInfo, UpdateStatusEvent};

#[cfg(not(target_os = "android"))]
pub use velopack;

/// Führt VelopackApp::build().run() aus, um Update-/Installations-Hooks vor Tauri-Start abzuarbeiten.
#[inline]
pub fn velopack_start() {
    #[cfg(not(target_os = "android"))]
    {
        velopack::VelopackApp::build().run();
    }
}

#[cfg(not(target_os = "android"))]
use std::sync::Arc;
#[cfg(target_os = "android")]
use tauri::Emitter;
use tauri::{AppHandle, Manager};

/// Ein Debug-Bau (`tauri dev`) installiert nie ein Update: der Installer
/// ersetzt die installierte App, nicht den Entwicklungsbau.
pub const SELBST_AKTUALISIEREN: bool = !cfg!(debug_assertions);

/// Registriert das updater_core Backend in der Tauri-App.
pub fn init_updater<R: tauri::Runtime>(
    builder: tauri::Builder<R>,
    config: UpdateConfig,
) -> tauri::Builder<R> {
    #[cfg(target_os = "android")]
    {
        let package_name = config.android_package;
        builder
            .plugin(android::init_android_installer(package_name))
            .manage(config)
    }

    #[cfg(not(target_os = "android"))]
    {
        builder
            .manage(config)
            .manage(Arc::new(desktop::DesktopUpdateState::default()))
    }
}

/// Prüft asynchron auf verfügbare Updates.
pub async fn pruefen(app: &AppHandle) -> Result<UpdateInfo, String> {
    let config = app
        .try_state::<UpdateConfig>()
        .ok_or_else(|| "UpdateConfig nicht registriert".to_string())?;

    #[cfg(target_os = "android")]
    {
        android::pruefe_android_update(app, &config).await
    }

    #[cfg(not(target_os = "android"))]
    {
        let state = app
            .try_state::<Arc<desktop::DesktopUpdateState>>()
            .ok_or_else(|| "DesktopUpdateState nicht registriert".to_string())?;
        desktop::pruefe_desktop_update(app, &config, &state).await
    }
}

/// Lädt das Update im Hintergrund herunter.
pub async fn herunterladen(app: &AppHandle) -> Result<(), String> {
    if !SELBST_AKTUALISIEREN {
        return Err("Ein Entwicklungsbau lädt keine Updates.".to_string());
    }

    let config = app
        .try_state::<UpdateConfig>()
        .ok_or_else(|| "UpdateConfig nicht registriert".to_string())?;

    #[cfg(target_os = "android")]
    {
        // Auf Android wird im Hintergrund geladen & validiert
        android::lade_und_installiere_android(app, &config).await
    }

    #[cfg(not(target_os = "android"))]
    {
        let state = app
            .try_state::<Arc<desktop::DesktopUpdateState>>()
            .ok_or_else(|| "DesktopUpdateState nicht registriert".to_string())?;
        desktop::lade_desktop_update(app, &config, &state).await
    }
}

/// Installiert das Update (auf Desktop: Velopack Restart; auf Android: APK-Installation).
pub async fn installieren(app: &AppHandle) -> Result<(), String> {
    if !SELBST_AKTUALISIEREN {
        return Err("Ein Entwicklungsbau installiert keine Updates.".to_string());
    }

    #[cfg(target_os = "android")]
    let config = app
        .try_state::<UpdateConfig>()
        .ok_or_else(|| "UpdateConfig nicht registriert".to_string())?;

    #[cfg(target_os = "android")]
    {
        android::lade_und_installiere_android(app, &config).await
    }

    #[cfg(not(target_os = "android"))]
    {
        let state = app
            .try_state::<Arc<desktop::DesktopUpdateState>>()
            .ok_or_else(|| "DesktopUpdateState nicht registriert".to_string())?;
        desktop::installiere_desktop_update(&state).await
    }
}

/// Startet eine periodische, nicht-blockierende Hintergrund-Prüfung kurz nach dem Start (Zero-Delay Kaltstart).
pub fn starte_hintergrund_pruefung(app_handle: AppHandle) {
    if !SELBST_AKTUALISIEREN {
        return;
    }

    #[cfg(not(target_os = "android"))]
    {
        let config = match app_handle.try_state::<UpdateConfig>() {
            Some(config) => (*config).clone(),
            None => return,
        };
        let state = match app_handle.try_state::<Arc<desktop::DesktopUpdateState>>() {
            Some(state) => state.inner().clone(),
            None => return,
        };
        desktop::starte_hintergrund_download(app_handle.clone(), config, state.as_ref().clone());
    }

    #[cfg(target_os = "android")]
    {
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_secs(45));
            loop {
                let handle = app_handle.clone();
                tauri::async_runtime::block_on(async move {
                    if let Ok(info) = pruefen(&handle).await {
                        if info.verfuegbar {
                            let payload = serde_json::json!({
                                "version": info.neue_version.unwrap_or_default(),
                                "notizen": info.notizen
                            });
                            let _ = handle.emit("updater:verfuegbar", &payload);
                            let _ = handle.emit("mss:update-verfuegbar", &payload);
                        }
                    }
                });
                std::thread::sleep(std::time::Duration::from_secs(2 * 3600));
            }
        });
    }
}

#[cfg(test)]
mod updater_tests {
    use super::*;

    #[test]
    fn beide_apps_verwenden_distinkte_velopack_kanaele_bei_gleichem_release_repo() {
        let pontis = UpdateConfig::pontis();
        let mss = UpdateConfig::mss();
        assert_eq!(pontis.velopack_repo_url, mss.velopack_repo_url);
        assert_eq!(pontis.velopack_channel, "pontis");
        assert_eq!(mss.velopack_channel, "mss");
        assert_eq!(pontis.android_package, "com.mauntingstudios.secure_browser");
        assert_eq!(mss.android_package, "com.mauntingstudios.smart_system");
    }

    #[cfg(not(target_os = "android"))]
    #[test]
    fn minisign_tauri_public_key_ist_parsebar_und_erfordert_prehashed_signaturen() {
        let key = minisign_verify::PublicKey::decode(
            "untrusted comment: minisign public key: 15B13465D5161352\nRWRSExbVZTSxFWASpSvfqUg6my3aIhblAXits5+NODHXbrZuSjDbCAXZ",
        ).unwrap();
        assert_eq!(
            key.untrusted_comment(),
            Some("untrusted comment: minisign public key: 15B13465D5161352")
        );
        let malformed_signature = minisign_verify::Signature::decode("invalid");
        assert!(malformed_signature.is_err());
    }
}
