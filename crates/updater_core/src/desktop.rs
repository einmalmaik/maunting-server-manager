//! Desktop-Update-Integration via signierte Velopack-Releases.

use base64::Engine;
use sha2::Digest;
use std::io::Read;
use std::path::Path;
use std::sync::{mpsc, Arc, Mutex};
use tauri::{AppHandle, Emitter};
use velopack::sources::UpdateSource;
use velopack::{UpdateCheck, UpdateInfo as VpkUpdateInfo, UpdateManager};

use crate::config::UpdateConfig;
use crate::types::UpdateInfo;

// Same public key already trusted by the Tauri updater in released MSS and Pontis builds.
const FEED_PUBLIC_KEY: &str = "untrusted comment: minisign public key: 15B13465D5161352\nRWRSExbVZTSxFWASpSvfqUg6my3aIhblAXits5+NODHXbrZuSjDbCAXZ";
const MAX_FEED_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Clone, Default)]
pub struct DesktopUpdateState {
    pending_update: Arc<Mutex<Option<VpkUpdateInfo>>>,
    pending_restart: Arc<Mutex<Option<String>>>,
    background_running: Arc<Mutex<bool>>,
}

/// GitHub source whose Velopack feed and signature are verified before parsing.
struct SignedGithubSource {
    download_base: String,
    trusted_key: minisign_verify::PublicKey,
    package_id: &'static str,
    package_prefix: &'static str,
}

impl SignedGithubSource {
    fn new(config: &UpdateConfig) -> Result<Self, String> {
        let trusted_key = minisign_verify::PublicKey::decode(FEED_PUBLIC_KEY)
            .map_err(|e| format!("Velopack-Vertrauensschlüssel ungültig: {e}"))?;
        let (package_id, package_prefix) = match config.velopack_channel {
            "mss" => ("MauntingSmartSystem", "MauntingSmartSystem-"),
            "pontis" => ("Pontis", "Pontis-"),
            _ => return Err("Unbekannter Velopack-Updatekanal.".to_string()),
        };
        Ok(Self {
            download_base: config.velopack_feed_url.trim_end_matches('/').to_string(),
            trusted_key,
            package_id,
            package_prefix,
        })
    }

    fn asset_valid(asset: &velopack::VelopackAsset, package_id: &str) -> bool {
        let package_type = match asset.Type.as_str() {
            "Full" => "full",
            "Delta" => "delta",
            _ => return false,
        };
        asset.PackageId == package_id
            && semver::Version::parse(&asset.Version).is_ok()
            && asset.FileName == format!("{package_id}-{}-{package_type}.nupkg", asset.Version)
            && asset.SHA256.len() == 64
            && asset.SHA256.bytes().all(|byte| byte.is_ascii_hexdigit())
            && asset.Size > 0
    }

    fn sha256_file(path: &Path) -> Result<String, velopack::Error> {
        let mut file = std::fs::File::open(path).map_err(|e| {
            velopack::Error::Other(format!("Velopack-Paket konnte nicht geöffnet werden: {e}"))
        })?;
        let mut hasher = sha2::Sha256::new();
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = file.read(&mut buffer).map_err(|e| {
                velopack::Error::Other(format!("Velopack-Paket konnte nicht geprüft werden: {e}"))
            })?;
            if count == 0 {
                break;
            }
            hasher.update(&buffer[..count]);
        }
        Ok(hasher
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02X}"))
            .collect())
    }

    fn get_bytes(url: &str) -> Result<Vec<u8>, velopack::Error> {
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(std::time::Duration::from_secs(30)))
            .timeout_connect(Some(std::time::Duration::from_secs(5)))
            .build()
            .into();
        let mut response = agent
            .get(url)
            .call()
            .map_err(|e| velopack::Error::Other(format!("Velopack-Feed nicht erreichbar: {e}")))?;
        response
            .body_mut()
            .with_config()
            .limit(MAX_FEED_BYTES)
            .read_to_vec()
            .map_err(|e| {
                velopack::Error::Other(format!("Velopack-Feed konnte nicht gelesen werden: {e}"))
            })
    }
}

impl UpdateSource for SignedGithubSource {
    fn get_release_feed(
        &self,
        channel: &str,
        _app: &velopack::bundle::Manifest,
        _staged_user_id: &str,
    ) -> Result<velopack::VelopackAssetFeed, velopack::Error> {
        let feed_url = format!("{}/releases.{}.json", self.download_base, channel);
        let feed_bytes = Self::get_bytes(&feed_url)?;
        let signature_url = format!("{feed_url}.sig");
        let signature_bytes = Self::get_bytes(&signature_url)?;
        let signature_text = base64::engine::general_purpose::STANDARD
            .decode(&signature_bytes)
            .map_err(|e| velopack::Error::Other(format!("Velopack-Feed-Signatur ungültig: {e}")))?;
        let signature_text = std::str::from_utf8(&signature_text)
            .map_err(|e| velopack::Error::Other(format!("Velopack-Feed-Signatur ungültig: {e}")))?;
        let signature = minisign_verify::Signature::decode(signature_text)
            .map_err(|e| velopack::Error::Other(format!("Velopack-Feed-Signatur ungültig: {e}")))?;
        let trusted_comment = signature.trusted_comment();
        let signatur_version = trusted_comment
            .split('\t')
            .find_map(|field| field.strip_prefix("version:"))
            .ok_or_else(|| {
                velopack::Error::Other(
                    "Velopack-Feed-Signatur enthält keine signierte App-Version.".to_string(),
                )
            })?;
        // Reject legacy, non-prehashed signatures; only current minisign signatures are accepted.
        self.trusted_key
            .verify(&feed_bytes, &signature, false)
            .map_err(|e| {
                velopack::Error::Other(format!("Velopack-Feed-Signaturprüfung fehlgeschlagen: {e}"))
            })?;
        let json = std::str::from_utf8(&feed_bytes)
            .map_err(|e| velopack::Error::Other(format!("Velopack-Feed ungültig: {e}")))?;
        let feed: velopack::VelopackAssetFeed =
            serde_json::from_str(json).map_err(velopack::Error::Json)?;
        if !feed
            .Assets
            .iter()
            .any(|asset| asset.Version == signatur_version && asset.Type == "Full")
        {
            return Err(velopack::Error::Other(
                "Die signierte Version ist nicht im Velopack-Feed enthalten.".to_string(),
            ));
        }
        for asset in &feed.Assets {
            if !Self::asset_valid(asset, self.package_id)
                || !asset.FileName.starts_with(self.package_prefix)
            {
                return Err(velopack::Error::Other(
                    "Velopack-Feed enthält einen ungültigen Paket-Eintrag.".to_string(),
                ));
            }
        }
        Ok(feed)
    }

    fn download_release_entry(
        &self,
        asset: &velopack::VelopackAsset,
        local_file: &Path,
        progress_sender: Option<mpsc::Sender<i16>>,
    ) -> Result<(), velopack::Error> {
        if !Self::asset_valid(asset, self.package_id)
            || !asset.FileName.starts_with(self.package_prefix)
        {
            return Err(velopack::Error::Other(
                "Velopack-Paket wurde abgelehnt.".to_string(),
            ));
        }
        let url = format!("{}/{}", self.download_base, asset.FileName);
        let mut response = ureq::Agent::config_builder()
            .timeout_global(Some(std::time::Duration::from_secs(30)))
            .timeout_connect(Some(std::time::Duration::from_secs(5)))
            .build()
            .new_agent()
            .get(&url)
            .call()
            .map_err(|e| velopack::Error::Other(format!("Velopack-Paket nicht erreichbar: {e}")))?;
        let mut file = std::fs::File::create(local_file).map_err(|e| {
            velopack::Error::Other(format!("Velopack-Paket konnte nicht angelegt werden: {e}"))
        })?;
        let mut downloaded = 0_u64;
        let mut buffer = [0_u8; 64 * 1024];
        let mut reader = response
            .body_mut()
            .as_reader()
            .take(asset.Size.saturating_add(1));
        loop {
            let count = reader.read(&mut buffer).map_err(|e| {
                velopack::Error::Other(format!("Velopack-Paket konnte nicht gelesen werden: {e}"))
            })?;
            if count == 0 {
                break;
            }
            if downloaded.saturating_add(count as u64) > asset.Size {
                let _ = std::fs::remove_file(local_file);
                return Err(velopack::Error::Other(
                    "Velopack-Paket überschreitet die signierte Paketgröße.".to_string(),
                ));
            }
            use std::io::Write;
            file.write_all(&buffer[..count]).map_err(|e| {
                velopack::Error::Other(format!(
                    "Velopack-Paket konnte nicht gespeichert werden: {e}"
                ))
            })?;
            downloaded += count as u64;
            if let Some(sender) = &progress_sender {
                let percent =
                    ((downloaded.saturating_mul(100) / asset.Size.max(1)).min(100)) as i16;
                let _ = sender.send(percent);
            }
        }
        if downloaded != asset.Size {
            let _ = std::fs::remove_file(local_file);
            return Err(velopack::Error::Other(
                "Velopack-Paketgröße stimmt nicht mit dem signierten Feed überein.".to_string(),
            ));
        }
        file.sync_all().map_err(|e| {
            velopack::Error::Other(format!("Velopack-Paket konnte nicht gesichert werden: {e}"))
        })?;
        drop(file);
        let actual_hash = Self::sha256_file(local_file)?;
        if !actual_hash.eq_ignore_ascii_case(&asset.SHA256) {
            let _ = std::fs::remove_file(local_file);
            return Err(velopack::Error::Other(
                "Velopack-Paket-Hash stimmt nicht mit dem signierten Feed überein.".to_string(),
            ));
        }
        Ok(())
    }
}

fn update_manager(config: &UpdateConfig) -> Result<UpdateManager, String> {
    let source = SignedGithubSource::new(config)?;
    let options = velopack::UpdateOptions {
        ExplicitChannel: Some(config.velopack_channel.to_string()),
        ..Default::default()
    };
    UpdateManager::new(source, Some(options), None)
        .map_err(|e| format!("Velopack UpdateManager konnte nicht initialisiert werden: {e}"))
}

fn update_manager_from_running_app() -> Result<UpdateManager, String> {
    UpdateManager::new(velopack::sources::NoneSource {}, None, None)
        .map_err(|e| format!("Velopack-Installation konnte nicht gefunden werden: {e}"))
}

pub async fn pruefe_desktop_update(
    app: &AppHandle,
    config: &UpdateConfig,
    state: &DesktopUpdateState,
) -> Result<UpdateInfo, String> {
    let manager = update_manager(config)?;
    let current_version = app.package_info().version.to_string();
    let check = tauri::async_runtime::spawn_blocking(move || manager.check_for_updates())
        .await
        .map_err(|e| format!("Update-Prüfung konnte nicht abgeschlossen werden: {e}"))?
        .map_err(|e| format!("Velopack Update-Prüfung fehlgeschlagen: {e}"))?;
    match check {
        UpdateCheck::UpdateAvailable(updates) => {
            let version = updates.TargetFullRelease.Version.clone();
            *state
                .pending_update
                .lock()
                .map_err(|_| "Update-Lock-Fehler".to_string())? = Some(*updates);
            Ok(UpdateInfo {
                verfuegbar: true,
                aktuelle_version: current_version,
                neue_version: Some(version),
                download_url: None,
                notizen: None,
                ist_android: false,
            })
        }
        UpdateCheck::RemoteIsEmpty | UpdateCheck::NoUpdateAvailable => {
            *state
                .pending_update
                .lock()
                .map_err(|_| "Update-Lock-Fehler".to_string())? = None;
            *state
                .pending_restart
                .lock()
                .map_err(|_| "Update-Lock-Fehler".to_string())? = None;
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
    let manager = update_manager(config)?;
    let updates = state
        .pending_update
        .lock()
        .map_err(|_| "Update-Lock-Fehler".to_string())?
        .clone()
        .ok_or_else(|| "Kein geprüftes Update vorhanden.".to_string())?;
    let version = updates.TargetFullRelease.Version.clone();
    let _ = app.emit(
        "updater:status",
        serde_json::json!({ "status": "laedt", "prozent": 0 }),
    );
    let _ = app.emit(
        "mss:update-status",
        serde_json::json!({ "status": "laedt", "prozent": 0 }),
    );

    let (tx, rx) = mpsc::channel::<i16>();
    let progress_app = app.clone();
    let progress_thread = std::thread::spawn(move || {
        while let Ok(percent) = rx.recv() {
            let payload =
                serde_json::json!({ "status": "laedt", "prozent": percent.clamp(0, 100) as u32 });
            let _ = progress_app.emit("updater:status", &payload);
            let _ = progress_app.emit("mss:update-status", &payload);
        }
    });
    let result =
        tauri::async_runtime::spawn_blocking(move || manager.download_updates(&updates, Some(tx)))
            .await
            .map_err(|e| format!("Velopack-Download konnte nicht abgeschlossen werden: {e}"));
    let _ = progress_thread.join();
    result?.map_err(|e| format!("Velopack-Download fehlgeschlagen: {e}"))?;

    *state
        .pending_restart
        .lock()
        .map_err(|_| "Update-Lock-Fehler".to_string())? = Some(version.clone());
    let _ = app.emit("updater:bereit", serde_json::json!({ "version": version }));
    let _ = app.emit(
        "mss:update-bereit",
        serde_json::json!({ "version": version }),
    );
    let _ = app.emit(
        "updater:status",
        serde_json::json!({ "status": "bereit", "prozent": 100 }),
    );
    let _ = app.emit(
        "mss:update-status",
        serde_json::json!({ "status": "bereit", "prozent": 100 }),
    );
    Ok(())
}

/// Applies only a previously downloaded and confirmed pending update.
pub async fn installiere_desktop_update(state: &DesktopUpdateState) -> Result<(), String> {
    let version = state
        .pending_restart
        .lock()
        .map_err(|_| "Update-Lock-Fehler".to_string())?
        .clone()
        .ok_or_else(|| "Kein vorbereitetes Update zum Neustart vorhanden.".to_string())?;
    let manager = tauri::async_runtime::spawn_blocking(update_manager_from_running_app)
        .await
        .map_err(|e| format!("Velopack-Installation konnte nicht geprüft werden: {e}"))??;
    let pending = manager
        .get_update_pending_restart()
        .ok_or_else(|| "Das vorbereitete Velopack-Update ist nicht mehr vorhanden.".to_string())?;
    if pending.Version != version {
        return Err(
            "Das vorbereitete Update stimmt nicht mehr mit der angebotenen Version überein."
                .to_string(),
        );
    }
    tauri::async_runtime::spawn_blocking(move || manager.apply_updates_and_restart(&pending))
        .await
        .map_err(|e| format!("Update-Neustart konnte nicht abgeschlossen werden: {e}"))?
        .map_err(|e| format!("Fehler beim Anwenden des Updates und Neustart: {e}"))
}

pub fn starte_hintergrund_download(
    app: AppHandle,
    config: UpdateConfig,
    state: DesktopUpdateState,
) {
    std::thread::spawn(move || {
        let _ = tauri::async_runtime::block_on(async move {
            std::thread::sleep(std::time::Duration::from_secs(45));
            loop {
                let already_running = match state.background_running.lock() {
                    Ok(mut running) if !*running => {
                        *running = true;
                        false
                    }
                    _ => true,
                };
                if already_running {
                    std::thread::sleep(std::time::Duration::from_secs(2 * 3600));
                    continue;
                }

                match pruefe_desktop_update(&app, &config, &state).await {
                    Ok(info) if info.verfuegbar => {
                        let version = info.neue_version.clone().unwrap_or_default();
                        let payload = serde_json::json!({ "version": version });
                        let _ = app.emit("updater:verfuegbar", &payload);
                        let _ = app.emit("mss:update-verfuegbar", &payload);
                    }
                    Ok(_) => {}
                    Err(error) => {
                        let failure = serde_json::json!({ "status": "fehler", "fehler": error });
                        let _ = app.emit("updater:status", &failure);
                        let _ = app.emit("mss:update-status", &failure);
                    }
                }
                if let Ok(mut running) = state.background_running.lock() {
                    *running = false;
                }
                std::thread::sleep(std::time::Duration::from_secs(2 * 3600));
            }
        });
    });
}

#[cfg(test)]
mod tests {
    use super::SignedGithubSource;
    use velopack::VelopackAsset;

    fn asset(package_id: &str, asset_type: &str, file_name: &str) -> VelopackAsset {
        VelopackAsset {
            PackageId: package_id.to_string(),
            Version: "5.5.2".to_string(),
            Type: asset_type.to_string(),
            FileName: file_name.to_string(),
            SHA256: "a".repeat(64),
            Size: 42,
            ..Default::default()
        }
    }

    #[test]
    fn erlaubt_nur_vollstaendige_velopack_pakete_mit_deterministischem_namen() {
        assert!(SignedGithubSource::asset_valid(
            &asset("Pontis", "Full", "Pontis-5.5.2-full.nupkg"),
            "Pontis",
        ));
        assert!(SignedGithubSource::asset_valid(
            &asset("Pontis", "Delta", "Pontis-5.5.2-delta.nupkg"),
            "Pontis",
        ));
        assert!(!SignedGithubSource::asset_valid(
            &asset(
                "Pontis",
                "Full",
                "https://evil.invalid/Pontis-5.5.2-full.nupkg"
            ),
            "Pontis",
        ));
        assert!(!SignedGithubSource::asset_valid(
            &asset(
                "MauntingSmartSystem",
                "Full",
                "MauntingSmartSystem-5.5.2-full.nupkg"
            ),
            "Pontis",
        ));
        assert!(!SignedGithubSource::asset_valid(
            &asset("Pontis", "Full", "Pontis-5.5.2-full.nupkg"),
            "MauntingSmartSystem",
        ));
    }
}
