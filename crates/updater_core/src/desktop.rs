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
    /// Ein Download zur Zeit: Hintergrund und manueller Anstoß (Einstellungen)
    /// können sonst dieselbe Datei gleichzeitig schreiben.
    bereitet_vor: Arc<Mutex<bool>>,
}

/// Setzt die Downloadsperre zurück, auch wenn der Lauf mit einem Fehler endet.
struct VorbereitungLaeuft<'a>(&'a Arc<Mutex<bool>>);

impl Drop for VorbereitungLaeuft<'_> {
    fn drop(&mut self) {
        if let Ok(mut laeuft) = self.0.lock() {
            *laeuft = false;
        }
    }
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
    // Läuft schon eine Vorbereitung (Hintergrund oder Einstellungen), meldet
    // sie das Ergebnis selbst; ein zweiter Lauf ist ein No-op.
    {
        let mut laeuft = state
            .bereitet_vor
            .lock()
            .map_err(|_| "Update-Lock-Fehler".to_string())?;
        if *laeuft {
            return Ok(());
        }
        *laeuft = true;
    }
    let _wache = VorbereitungLaeuft(&state.bereitet_vor);

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

    let (tx, rx) = mpsc::channel::<i16>();
    let progress_app = app.clone();
    let progress_thread = std::thread::spawn(move || {
        while let Ok(percent) = rx.recv() {
            let payload =
                serde_json::json!({ "status": "laedt", "prozent": percent.clamp(0, 100) as u32 });
            let _ = progress_app.emit("updater:status", &payload);
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
        "updater:status",
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

/// Prüft kurz nach dem Start und **bereitet** ein gefundenes Update still vor:
/// Download und Prüfsumme laufen ohne Hinweis, gemeldet wird erst `updater:bereit`,
/// wenn nur noch der Neustart fehlt. Es gibt keine „Update verfügbar“-Meldung
/// mehr, die zum Herunterladen auffordert — das Badge erscheint erst danach.
///
/// Velopack wendet ein so vorbereitetes Paket beim nächsten Start selbst an
/// (`VelopackApp` mit Standard-Auto-Apply); der Knopf im Badge startet sofort neu.
pub fn starte_hintergrund_bereitstellung(
    app: AppHandle,
    config: UpdateConfig,
    state: DesktopUpdateState,
) {
    std::thread::spawn(move || {
        let _ = tauri::async_runtime::block_on(async move {
            std::thread::sleep(std::time::Duration::from_secs(45));
            loop {
                // Liegt schon ein Paket für den Neustart bereit, gibt es nichts zu tun.
                let schon_bereit = state
                    .pending_restart
                    .lock()
                    .map(|restart| restart.is_some())
                    .unwrap_or(true);
                if !schon_bereit {
                    match pruefe_desktop_update(&app, &config, &state).await {
                        Ok(info) if info.verfuegbar => {
                            if let Err(error) =
                                lade_desktop_update(&app, &config, &state).await
                            {
                                let failure =
                                    serde_json::json!({ "status": "fehler", "fehler": error });
                                let _ = app.emit("updater:status", &failure);
                            }
                        }
                        Ok(_) => {}
                        Err(error) => {
                            let failure =
                                serde_json::json!({ "status": "fehler", "fehler": error });
                            let _ = app.emit("updater:status", &failure);
                        }
                    }
                }
                std::thread::sleep(std::time::Duration::from_secs(2 * 3600));
            }
        });
    });
}

#[cfg(test)]
mod tests {
    use super::SignedGithubSource;
    use velopack::sources::UpdateSource;
    use velopack::{VelopackAsset, VelopackAssetFeed};

    /// Feed, Signatur, Paket und Schlüssel stammen aus `tauri signer sign --app-version`
    /// (CLI 2.12.1) und liegen unter `tests/fixtures/velopack`. Der Testschlüssel
    /// ist ein Wegwerfschlüssel; der Vertrauensanker im Betrieb bleibt
    /// `FEED_PUBLIC_KEY`.
    const FEED: &str = include_str!("../tests/fixtures/velopack/feed.json");
    const FEED_SIGNATUR: &str = include_str!("../tests/fixtures/velopack/feed.sig");
    const FEED_SIGNATUR_FALSCHE_VERSION: &str =
        include_str!("../tests/fixtures/velopack/feed.falsche-version.sig");
    const FEED_SIGNATUR_FREMDER_SCHLUESSEL: &str =
        include_str!("../tests/fixtures/velopack/feed.fremder-schluessel.sig");
    const TEST_SCHLUESSEL: &str = include_str!("../tests/fixtures/velopack/test-schluessel.pub");
    const FREMDER_SCHLUESSEL: &str =
        include_str!("../tests/fixtures/velopack/fremder-schluessel.pub");
    const PAKET: &[u8] = include_bytes!("../tests/fixtures/velopack/paket.bin");

    /// Steht für GitHub, das bei einem fehlenden Asset 404 antwortet.
    mod server {
        use std::io::{Read, Write};
        use std::net::{TcpListener, TcpStream};
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::sync::Arc;
        use std::thread::JoinHandle;
        use std::time::Duration;

        pub struct FeedServer {
            pub base_url: String,
            stop: Arc<AtomicBool>,
            lauf: Option<JoinHandle<()>>,
        }

        impl FeedServer {
            pub fn starte(antworten: Vec<(String, Vec<u8>)>) -> Self {
                let listener = TcpListener::bind("127.0.0.1:0").expect("freier Port");
                let adresse = listener.local_addr().expect("Adresse");
                listener.set_nonblocking(true).expect("nicht blockierend");
                let stop = Arc::new(AtomicBool::new(false));
                let wache = Arc::clone(&stop);
                let lauf = std::thread::spawn(move || {
                    while !wache.load(Ordering::Relaxed) {
                        match listener.accept() {
                            Ok((strom, _)) => bediene(strom, &antworten),
                            Err(_) => std::thread::sleep(Duration::from_millis(5)),
                        }
                    }
                });
                Self {
                    base_url: format!("http://{adresse}"),
                    stop,
                    lauf: Some(lauf),
                }
            }
        }

        impl Drop for FeedServer {
            fn drop(&mut self) {
                self.stop.store(true, Ordering::Relaxed);
                if let Some(lauf) = self.lauf.take() {
                    let _ = lauf.join();
                }
            }
        }

        fn bediene(mut strom: TcpStream, antworten: &[(String, Vec<u8>)]) {
            let mut kopf = Vec::new();
            let mut puffer = [0_u8; 512];
            while !kopf.windows(4).any(|fenster| fenster == b"\r\n\r\n") {
                match strom.read(&mut puffer) {
                    Ok(0) | Err(_) => return,
                    Ok(gelesen) => kopf.extend_from_slice(&puffer[..gelesen]),
                }
            }
            let kopf = String::from_utf8_lossy(&kopf);
            let pfad = kopf
                .lines()
                .next()
                .and_then(|zeile| zeile.split(' ').nth(1))
                .unwrap_or_default()
                .trim_start_matches('/')
                .to_string();
            let ergebnis = match antworten.iter().find(|(name, _)| *name == pfad) {
                Some((_, inhalt)) => {
                    let kopfzeilen = format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        inhalt.len()
                    );
                    strom
                        .write_all(kopfzeilen.as_bytes())
                        .and_then(|_| strom.write_all(inhalt))
                }
                None => strom.write_all(
                    b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                ),
            };
            let _ = ergebnis.and_then(|_| strom.flush());
        }
    }

    fn quelle(server: &server::FeedServer, schluessel: &str) -> SignedGithubSource {
        SignedGithubSource {
            download_base: server.base_url.clone(),
            trusted_key: minisign_verify::PublicKey::decode(schluessel.trim())
                .expect("Test-Schlüssel ist lesbar"),
            package_id: "Pontis",
            package_prefix: "Pontis-",
        }
    }

    fn paket_asset() -> VelopackAsset {
        paket_asset_mit(PAKET, PAKET.len() as u64)
    }

    fn paket_asset_mit(hash_daten: &[u8], groesse: u64) -> VelopackAsset {
        use sha2::Digest;
        VelopackAsset {
            PackageId: "Pontis".to_string(),
            Version: "5.5.2".to_string(),
            Type: "Full".to_string(),
            FileName: "Pontis-5.5.2-full.nupkg".to_string(),
            SHA256: sha2::Sha256::digest(hash_daten)
                .iter()
                .map(|byte| format!("{byte:02X}"))
                .collect(),
            Size: groesse,
            ..Default::default()
        }
    }

    fn zielordner(name: &str) -> std::path::PathBuf {
        let ordner =
            std::env::temp_dir().join(format!("updater_core_{}_{name}", std::process::id()));
        std::fs::create_dir_all(&ordner).expect("Zielordner");
        ordner
    }

    fn feed_von(signatur: &str) -> Result<VelopackAssetFeed, velopack::Error> {
        let server = server::FeedServer::starte(vec![
            ("releases.pontis.json".to_string(), FEED.as_bytes().to_vec()),
            (
                "releases.pontis.json.sig".to_string(),
                signatur.trim().as_bytes().to_vec(),
            ),
        ]);
        let quelle = quelle(&server, TEST_SCHLUESSEL);
        quelle.get_release_feed("pontis", &velopack::bundle::Manifest::default(), "")
    }

    /// Der Betriebsweg: Feed und Signatur über HTTP, Prüfung gegen den
    /// hinterlegten Schlüssel, Bindung der Signatur an die App-Version.
    #[test]
    fn nimmt_einen_echt_signierten_feed_an() {
        let feed = feed_von(FEED_SIGNATUR).expect("signierter Feed");
        assert_eq!(feed.Assets.len(), 2);
        assert_eq!(feed.Assets[0].FileName, "Pontis-5.5.2-full.nupkg");
        assert_eq!(feed.Assets[0].Version, "5.5.2");
        assert_eq!(feed.Assets[1].Type, "Delta");
    }

    #[test]
    fn weist_einen_feed_ohne_signatur_ab() {
        let server = server::FeedServer::starte(vec![(
            "releases.pontis.json".to_string(),
            FEED.as_bytes().to_vec(),
        )]);
        let quelle = quelle(&server, TEST_SCHLUESSEL);
        let fehler = quelle
            .get_release_feed("pontis", &velopack::bundle::Manifest::default(), "")
            .expect_err("ohne Signatur kein Feed");
        assert!(
            fehler.to_string().contains("nicht erreichbar"),
            "unerwarteter Fehler: {fehler}"
        );
    }

    #[test]
    fn weist_einen_nach_traeumt_signierten_feed_ab() {
        let manipuliert = FEED.replace("Pontis-5.5.2-full.nupkg", "Pontis-5.5.2-delta.nupkg");
        let server = server::FeedServer::starte(vec![
            ("releases.pontis.json".to_string(), manipuliert.into_bytes()),
            (
                "releases.pontis.json.sig".to_string(),
                FEED_SIGNATUR.trim().as_bytes().to_vec(),
            ),
        ]);
        let quelle = quelle(&server, TEST_SCHLUESSEL);
        let fehler = quelle
            .get_release_feed("pontis", &velopack::bundle::Manifest::default(), "")
            .expect_err("manipulierter Feed");
        assert!(
            fehler
                .to_string()
                .contains("Signaturprüfung fehlgeschlagen"),
            "unerwarteter Fehler: {fehler}"
        );
    }

    #[test]
    fn weist_eine_signatur_eines_fremden_schluessels_ab() {
        let fehler = feed_von(FEED_SIGNATUR_FREMDER_SCHLUESSEL).expect_err("fremde Signatur");
        assert!(
            fehler
                .to_string()
                .contains("Signaturprüfung fehlgeschlagen"),
            "unerwarteter Fehler: {fehler}"
        );
    }

    /// `tauri signer sign --app-version` bindet die Version in den signierten
    /// Kommentar. Steht sie nicht im Feed, ist der Feed nicht der gemeinte.
    #[test]
    fn weist_eine_signatur_fuer_eine_andere_app_version_ab() {
        let fehler = feed_von(FEED_SIGNATUR_FALSCHE_VERSION)
            .expect_err("Signatur passt zu keiner Version im Feed");
        assert!(
            fehler
                .to_string()
                .contains("signierte Version ist nicht im Velopack-Feed enthalten"),
            "unerwarteter Fehler: {fehler}"
        );
    }

    #[test]
    fn laedt_das_signierte_paket_und_meldet_den_fortschritt() {
        let server = server::FeedServer::starte(vec![(
            "Pontis-5.5.2-full.nupkg".to_string(),
            PAKET.to_vec(),
        )]);
        let quelle = quelle(&server, TEST_SCHLUESSEL);
        let ziel = zielordner("richtig").join("Pontis-5.5.2-full.nupkg");
        let (sender, empfaenger) = std::sync::mpsc::channel();
        quelle
            .download_release_entry(&paket_asset(), &ziel, Some(sender))
            .expect("Paket geladen");
        assert_eq!(std::fs::read(&ziel).expect("Paket liegt bereit"), PAKET);
        assert_eq!(empfaenger.recv().expect("Fortschritt"), 100);
        let _ = std::fs::remove_file(&ziel);
    }

    #[test]
    fn weist_ein_paket_mit_falscher_pruefsumme_ab() {
        let server = server::FeedServer::starte(vec![(
            "Pontis-5.5.2-full.nupkg".to_string(),
            PAKET.to_vec(),
        )]);
        let quelle = quelle(&server, TEST_SCHLUESSEL);
        let ziel = zielordner("hash").join("Pontis-5.5.2-full.nupkg");
        let fehler = quelle
            .download_release_entry(
                &paket_asset_mit(b"anderes Paket", PAKET.len() as u64),
                &ziel,
                None,
            )
            .expect_err("falsche Prüfsumme");
        assert!(
            fehler.to_string().contains("Paket-Hash"),
            "unerwarteter Fehler: {fehler}"
        );
        assert!(!ziel.exists(), "abgelehntes Paket bleibt nicht liegen");
    }

    #[test]
    fn weist_ein_paket_mit_falscher_groesse_ab() {
        let server = server::FeedServer::starte(vec![(
            "Pontis-5.5.2-full.nupkg".to_string(),
            PAKET.to_vec(),
        )]);
        let quelle = quelle(&server, TEST_SCHLUESSEL);
        let ziel = zielordner("groesse").join("Pontis-5.5.2-full.nupkg");
        let mut asset = paket_asset();
        asset.Size = 3;
        let fehler = quelle
            .download_release_entry(&asset, &ziel, None)
            .expect_err("falsche Größe");
        assert!(
            fehler.to_string().contains("Paketgröße"),
            "unerwarteter Fehler: {fehler}"
        );
        assert!(!ziel.exists(), "abgelehntes Paket bleibt nicht liegen");
    }

    #[test]
    fn laedt_keinen_pfad_ausserhalb_des_paketordners() {
        let server = server::FeedServer::starte(vec![]);
        let quelle = quelle(&server, TEST_SCHLUESSEL);
        let mut asset = paket_asset();
        asset.FileName = "../../evil-full.nupkg".to_string();
        let ziel = zielordner("pfad").join("Pontis-5.5.2-full.nupkg");
        let fehler = quelle
            .download_release_entry(&asset, &ziel, None)
            .expect_err("Pfad im Dateinamen");
        assert!(
            fehler.to_string().contains("abgelehnt"),
            "unerwarteter Fehler: {fehler}"
        );
    }

    #[test]
    fn test_schluessel_ist_nicht_der_vertrauensanker_des_betriebs() {
        assert!(!TEST_SCHLUESSEL.contains("15B13465D5161352"));
        assert!(!FREMDER_SCHLUESSEL.contains("15B13465D5161352"));
    }

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

    /// Desktop meldet nur noch das fertig vorbereitete Paket. Eine zweite
    /// Ereignisfamilie mit `mss`-Präfix hatte bis 10/2026 einen eigenen Dialog
    /// gefüttert, der neben dem Badge zum Herunterladen aufforderte.
    #[test]
    fn meldet_nur_bereite_updates_und_nur_unter_einem_ereignisnamen() {
        let quelle = include_str!("desktop.rs");
        let verfuegbar = ["updater", "verfuegbar"].join(":");
        let zweitname = ["mss", "update"].join(":");
        assert!(!quelle.contains(&verfuegbar));
        assert!(!quelle.contains(&zweitname));
        let bereitstellung = quelle
            .split("pub fn starte_hintergrund_bereitstellung")
            .nth(1)
            .unwrap();
        assert!(
            bereitstellung.contains("lade_desktop_update"),
            "die Hintergrundprüfung muss das Update selbst vorbereiten"
        );
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
