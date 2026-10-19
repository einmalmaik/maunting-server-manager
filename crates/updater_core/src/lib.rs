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
pub use types::{UpdateInfo, UpdateStatusEvent};
pub use semver::ist_neuer;

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

use tauri::{AppHandle, Emitter, Manager};

/// Ein Debug-Bau (`tauri dev`) installiert nie ein Update: der Installer
/// ersetzt die installierte App, nicht den Entwicklungsbau.
pub const SELBST_AKTUALISIEREN: bool = !cfg!(debug_assertions);

/// Registriert das updater_core Backend in der Tauri-App.
pub fn init_updater<R: tauri::Runtime>(builder: tauri::Builder<R>, config: UpdateConfig) -> tauri::Builder<R> {
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
            .manage(desktop::DesktopUpdateState::default())
    }
}

/// Prüft asynchron auf verfügbare Updates.
pub async fn pruefen(app: &AppHandle) -> Result<UpdateInfo, String> {
    let config = app.try_state::<UpdateConfig>()
        .ok_or_else(|| "UpdateConfig nicht registriert".to_string())?;

    #[cfg(target_os = "android")]
    {
        android::pruefe_android_update(app, &config).await
    }

    #[cfg(not(target_os = "android"))]
    {
        let state = app.try_state::<desktop::DesktopUpdateState>()
            .ok_or_else(|| "DesktopUpdateState nicht registriert".to_string())?;
        desktop::pruefe_desktop_update(app, &config, &state).await
    }
}

/// Lädt das Update im Hintergrund herunter.
pub async fn herunterladen(app: &AppHandle) -> Result<(), String> {
    if !SELBST_AKTUALISIEREN {
        return Err("Ein Entwicklungsbau lädt keine Updates.".to_string());
    }

    let config = app.try_state::<UpdateConfig>()
        .ok_or_else(|| "UpdateConfig nicht registriert".to_string())?;

    #[cfg(target_os = "android")]
    {
        // Auf Android wird im Hintergrund geladen & validiert
        android::lade_und_installiere_android(app, &config).await
    }

    #[cfg(not(target_os = "android"))]
    {
        let state = app.try_state::<desktop::DesktopUpdateState>()
            .ok_or_else(|| "DesktopUpdateState nicht registriert".to_string())?;
        desktop::lade_desktop_update(app, &config, &state).await
    }
}

/// Installiert das Update (auf Desktop: Velopack Restart; auf Android: APK-Installation).
pub async fn installieren(app: &AppHandle) -> Result<(), String> {
    if !SELBST_AKTUALISIEREN {
        return Err("Ein Entwicklungsbau installiert keine Updates.".to_string());
    }

    let config = app.try_state::<UpdateConfig>()
        .ok_or_else(|| "UpdateConfig nicht registriert".to_string())?;

    #[cfg(target_os = "android")]
    {
        android::lade_und_installiere_android(app, &config).await
    }

    #[cfg(not(target_os = "android"))]
    {
        let state = app.try_state::<desktop::DesktopUpdateState>()
            .ok_or_else(|| "DesktopUpdateState nicht registriert".to_string())?;
        desktop::installiere_desktop_update(&config, &state, true)
    }
}

/// Startet eine periodische, nicht-blockierende Hintergrund-Prüfung kurz nach dem Start (Zero-Delay Kaltstart).
pub fn starte_hintergrund_pruefung(app_handle: AppHandle) {
    if !SELBST_AKTUALISIEREN {
        return;
    }
    std::thread::spawn(move || {
        // Erst nach 45 Sekunden prüfen, damit der Kaltstart völlig ungestört bleibt.
        std::thread::sleep(std::time::Duration::from_secs(45));

        loop {
            let handle = app_handle.clone();
            tauri::async_runtime::block_on(async move {
                if let Ok(info) = pruefen(&handle).await {
                    if info.verfuegbar {
                        let ziel_version = info.neue_version.clone().unwrap_or_default();
                        let payload = serde_json::json!({
                            "version": ziel_version,
                            "notizen": info.notizen
                        });
                        let _ = handle.emit("updater:verfuegbar", &payload);
                        let _ = handle.emit("mss:update-verfuegbar", &payload);
                    }
                }
            });

            // Alle 2 Stunden erneut im Hintergrund prüfen
            std::thread::sleep(std::time::Duration::from_secs(2 * 3600));
        }
    });
}
