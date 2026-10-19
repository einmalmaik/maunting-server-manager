//! Desktop-Update-Integration via Velopack (Windows & Linux).
//!
//! Nutzt den Velopack `UpdateManager` für Delta-Updates und One-Click-Installationen.
//! Wichtig: `VelopackApp::build().run()` muss in `main()` als allererstes aufgerufen werden.

use std::sync::mpsc;
use std::sync::Mutex;
use tauri::{AppHandle, Emitter};
use velopack::sources::HttpSource;
use velopack::{UpdateCheck, UpdateInfo as VpkUpdateInfo, UpdateManager};
use crate::config::UpdateConfig;
use crate::types::UpdateInfo;

pub struct DesktopUpdateState {
    pub pending_update: Mutex<Option<VpkUpdateInfo>>,
}

impl Default for DesktopUpdateState {
    fn default() -> Self {
        Self {
            pending_update: Mutex::new(None),
        }
    }
}

pub async fn pruefe_desktop_update(
    app: &AppHandle,
    config: &UpdateConfig,
    state: &DesktopUpdateState,
) -> Result<UpdateInfo, String> {
    let current_version = app.package_info().version.to_string();
    let source = HttpSource::new(config.velopack_feed_url);
    let options = velopack::UpdateOptions {
        ExplicitChannel: Some(config.velopack_channel.to_string()),
        ..Default::default()
    };
    let um = UpdateManager::new(source, Some(options), None)
        .map_err(|e| format!("Velopack UpdateManager konnte nicht initialisiert werden: {e}"))?;

    let check = um
        .check_for_updates()
        .map_err(|e| format!("Velopack Update-Prüfung fehlgeschlagen: {e}"))?;

    match check {
        UpdateCheck::UpdateAvailable(updates) => {
            let ziel_version = updates.TargetFullRelease.Version.to_string();
            let mut guard = state.pending_update.lock().map_err(|_| "Lock-Fehler".to_string())?;
            *guard = Some(*updates);

            Ok(UpdateInfo {
                verfuegbar: true,
                aktuelle_version: current_version,
                neue_version: Some(ziel_version),
                download_url: None,
                notizen: None,
                ist_android: false,
            })
        }
        UpdateCheck::RemoteIsEmpty | UpdateCheck::NoUpdateAvailable => {
            let mut guard = state.pending_update.lock().map_err(|_| "Lock-Fehler".to_string())?;
            *guard = None;

            Ok(UpdateInfo {
                verfuegbar: false,
                aktuelle_version: current_version,
                neue_version: None,
                download_url: None,
                notizen: None,
                ist_android: false,
            })
        }
    }
}

pub async fn lade_desktop_update(
    app: &AppHandle,
    config: &UpdateConfig,
    state: &DesktopUpdateState,
) -> Result<(), String> {
    let source = HttpSource::new(config.velopack_feed_url);
    let options = velopack::UpdateOptions {
        ExplicitChannel: Some(config.velopack_channel.to_string()),
        ..Default::default()
    };
    let um = UpdateManager::new(source, Some(options), None)
        .map_err(|e| format!("Velopack UpdateManager Fehler: {e}"))?;

    let updates = {
        let guard = state.pending_update.lock().map_err(|_| "Lock-Fehler".to_string())?;
        guard.clone().ok_or_else(|| "Kein geprüftes Update im State vorhanden. Bitte zuerst prüfen.".to_string())?
    };

    let app_handle = app.clone();
    let initial_status = serde_json::json!({
        "status": "laedt",
        "prozent": 0
    });
    let _ = app.emit("updater:status", &initial_status);
    let _ = app.emit("mss:update-status", &initial_status);

    // Velopack download_updates via std::sync::mpsc::channel
    let (tx, rx) = mpsc::channel::<i16>();
    let handle_for_thread = app_handle.clone();

    // Progress-Receiver Thread
    let progress_thread = std::thread::spawn(move || {
        while let Ok(pct) = rx.recv() {
            let prozent = pct.clamp(0, 100) as u32;
            let status_payload = serde_json::json!({
                "status": "laedt",
                "prozent": prozent
            });
            let _ = handle_for_thread.emit("updater:status", &status_payload);
            let _ = handle_for_thread.emit("mss:update-status", &status_payload);
        }
    });

    let res = um.download_updates(&updates, Some(tx));
    let _ = progress_thread.join();

    match res {
        Ok(()) => {
            let status_payload = serde_json::json!({
                "status": "bereit",
                "prozent": 100
            });
            let _ = app.emit("updater:status", &status_payload);
            let _ = app.emit("mss:update-status", &status_payload);

            let bereit_payload = serde_json::json!({
                "version": updates.TargetFullRelease.Version.to_string()
            });
            let _ = app.emit("updater:bereit", &bereit_payload);
            let _ = app.emit("mss:update-bereit", &bereit_payload);
            Ok(())
        }
        Err(e) => {
            let fehler = format!("Velopack Download fehlgeschlagen: {e}");
            let fehler_payload = serde_json::json!({
                "status": "fehler",
                "fehler": &fehler
            });
            let _ = app.emit("updater:status", &fehler_payload);
            let _ = app.emit("mss:update-status", &fehler_payload);
            Err(fehler)
        }
    }
}

pub fn installiere_desktop_update(
    config: &UpdateConfig,
    state: &DesktopUpdateState,
    restart: bool,
) -> Result<(), String> {
    let source = HttpSource::new(config.velopack_feed_url);
    let options = velopack::UpdateOptions {
        ExplicitChannel: Some(config.velopack_channel.to_string()),
        ..Default::default()
    };
    let um = UpdateManager::new(source, Some(options), None)
        .map_err(|e| format!("Velopack UpdateManager Fehler: {e}"))?;

    let updates = {
        let guard = state.pending_update.lock().map_err(|_| "Lock-Fehler".to_string())?;
        guard.clone().ok_or_else(|| "Kein heruntergeladenes Update verfügbar.".to_string())?
    };

    if restart {
        um.apply_updates_and_restart(&updates.TargetFullRelease)
            .map_err(|e| format!("Fehler beim Anwenden des Updates und Neustart: {e}"))?;
    } else {
        um.wait_exit_then_apply_updates(&updates.TargetFullRelease, false, false, Vec::<String>::new())
            .map_err(|e| format!("Fehler beim Registrieren für Exit-Update: {e}"))?;
    }

    Ok(())
}
