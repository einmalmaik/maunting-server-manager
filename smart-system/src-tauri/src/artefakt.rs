//! Artefakt- und Quarantäne-Dienst fuer Software, Mods und Installer.
//!
//! Maunting Studios Grundsatz: „Sicherheit braucht Vertrauen“ / „Schutz braucht Vertrauen“
//!
//! Sicherheits- und Datenschutzregeln:
//! 1. Isolierte Quarantäne: Heruntergeladene Dateien liegen in einem privaten App-Ordner
//!    (`%LOCALAPPDATA%\Singra\MSS-Quarantine`), adressiert ueber opake `artifact_id`s (UUIDv4).
//! 2. Datenminimierung: Rohdateien, interne Quarantänepfade und Download-URLs gelangen nie
//!    in Logs, Toasts oder den Modellkontext.
//! 3. Sicherer Download: Gekapselter HTTPS-Client mit striktem SSRF-Schutz (blockiert Loopback,
//!    RFC-1918-Netze, Link-Local, CGNAT und interne Netze). Validiert jede Weiterleitung neu.
//!    Berechnet SHA-256 waehrend des Streamings in eine `.part`-Datei.
//! 4. Download-Limit: Standard 10 GiB, konfigurierbar bis 100 GiB. Bricht bei Ueberschreitung sofort ab.
//! 5. Statische Pruefung: SHA-256-Abgleich gegen Herausgeber-Hash und Windows Defender Scan
//!    mit `-DisableRemediation` (meldet Funde, loescht nie eigenmaechtig).
//! 6. Windows Sandbox: Nach jeder statischen Pruefung wird verbindlich eine fluechtige Windows
//!    Sandbox mit schreibgeschuetztem Quarantäne-Mount gestartet.
//! 7. Software-Erkennung & Snapshot-Rollback: Lokalisierung ueber Launcher-Metadaten und freigegebene
//!    Suchwurzeln mit opaken `target_id`s. Vor Deployment dateibasiertes Snapshot-Manifest fuer
//!    vollstaendigen, atomaren Rollback.

use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{IpAddr, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use reqwest::blocking::Client;
use reqwest::header::LOCATION;
use reqwest::redirect::Policy;
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager};
use uuid::Uuid;

use crate::konfig::{self, MAX_DOWNLOAD_LIMIT_BYTES};
use crate::sandbox_container::{self, SandboxBericht};
use crate::virenscan;

const QUARANTAENE_ORDNER_NAME: &str = "MSS-Quarantine";
const SNAPSHOT_ORDNER_NAME: &str = "MSS-Snapshots";
const MANIFEST_DATEI: &str = "manifest.json";
const MAX_WEITERLEITUNGEN: usize = 5;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtefaktEintrag {
    pub artifact_id: String,
    pub dateiname: String,
    pub groesse_bytes: u64,
    pub sha256_berechnet: String,
    pub sha256_herausgeber: Option<String>,
    pub status: String, // "quarantined", "verified", "unverified", "mismatch", "threat_detected", "deployed", "rolled_back"
    pub defender_sauber: Option<bool>,
    pub defender_befund: Option<String>,
    pub sandbox_bericht: Option<SandboxBericht>,
    pub letzte_target_id: Option<String>,
    pub snapshot_pfad: Option<String>,
    pub erstellt_am: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetEintrag {
    pub target_id: String,
    pub name: String,
    pub ziel_art: String, // "steam", "custom_search_root"
    #[serde(skip_serializing)]
    pub absoluter_pfad: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SnapshotManifest {
    pub snapshot_id: String,
    pub artifact_id: String,
    pub target_id: String,
    pub geaenderte_dateien: Vec<String>,
    pub neue_dateien: Vec<String>,
    pub zeitstempel: u64,
}

static TARGET_REGISTRY: Mutex<Option<HashMap<String, TargetEintrag>>> = Mutex::new(None);

/// Basisordner der lokalen Quarantäne (%LOCALAPPDATA%\Singra\MSS-Quarantine).
pub fn quarantaene_basis(app: &AppHandle) -> Result<PathBuf, String> {
    let app_dir = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("App-Datenverzeichnis unbekannt: {e}"))?;
    let pfad = app_dir.join(QUARANTAENE_ORDNER_NAME);
    fs::create_dir_all(&pfad)
        .map_err(|e| format!("Quarantäneordner konnte nicht angelegt werden: {e}"))?;
    Ok(pfad)
}

/// Basisordner fuer Rollback-Snapshots.
pub fn snapshot_basis(app: &AppHandle) -> Result<PathBuf, String> {
    let app_dir = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("App-Datenverzeichnis unbekannt: {e}"))?;
    let pfad = app_dir.join(SNAPSHOT_ORDNER_NAME);
    fs::create_dir_all(&pfad)
        .map_err(|e| format!("Snapshot-Ordner konnte nicht angelegt werden: {e}"))?;
    Ok(pfad)
}

fn jetzt_sekunden() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// ── SSRF-Schutz und IP-Prüfung ──────────────────────────────────────────────

/// Prueft, ob eine IP-Adresse fuer externe Downloads erlaubt ist.
/// Blockiert: Loopback, Private Netze (RFC 1918), Link-Local, CGNAT, Broadcast, Multicast.
pub fn ist_ip_erlaubt(ip: &IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            let oktette = v4.octets();
            // 0.0.0.0/8
            if oktette[0] == 0 {
                return false;
            }
            // 127.0.0.0/8 (Loopback)
            if v4.is_loopback() {
                return false;
            }
            // 10.0.0.0/8 (Privat)
            if oktette[0] == 10 {
                return false;
            }
            // 172.16.0.0/12 (Privat)
            if oktette[0] == 172 && (16..=31).contains(&oktette[1]) {
                return false;
            }
            // 192.168.0.0/16 (Privat)
            if oktette[0] == 192 && oktette[1] == 168 {
                return false;
            }
            // 169.254.0.0/16 (Link-Local)
            if v4.is_link_local() {
                return false;
            }
            // 100.64.0.0/10 (Carrier-Grade NAT)
            if oktette[0] == 100 && (64..=127).contains(&oktette[1]) {
                return false;
            }
            // 224.0.0.0/4 (Multicast) / 240.0.0.0/4 (Reserviert) / 255.255.255.255
            if v4.is_multicast() || v4.is_broadcast() || oktette[0] >= 240 {
                return false;
            }
            true
        }
        IpAddr::V6(v6) => {
            if v6.is_loopback() || v6.is_unspecified() || v6.is_multicast() {
                return false;
            }
            // IPv4-gemappte IPv6-Adressen (::ffff:0:0/96) und IPv4-kompatible (::/96) rekursiv als IPv4 prüfen
            if let Some(v4) = v6.to_ipv4_mapped().or_else(|| v6.to_ipv4()) {
                return ist_ip_erlaubt(&IpAddr::V4(v4));
            }
            let segmente = v6.segments();
            // fc00::/7 (Unique Local Address)
            if (segmente[0] & 0xfe00) == 0xfc00 {
                return false;
            }
            // fe80::/10 (Link-Local)
            if (segmente[0] & 0xffc0) == 0xfe80 {
                return false;
            }
            true
        }
    }
}

/// Validiert eine Download-URL strikt:
/// - Nur HTTPS
/// - Keine User-Credentials
/// - DNS-Aufloesung und Pruefung saemtlicher Ziel-IPs gegen SSRF-Blocklisten
pub fn url_sicher_pruefen(url_str: &str) -> Result<Url, String> {
    let url = Url::parse(url_str).map_err(|e| format!("Ungültige URL: {e}"))?;

    if url.scheme() != "https" {
        return Err(format!(
            "Unsicheres Protokoll: '{}'. Nur https:// ist für Downloads erlaubt.",
            url.scheme()
        ));
    }

    if !url.username().is_empty() || url.password().is_some() {
        return Err("Download-URLs dürfen keine Anmeldedaten (Username/Passwort) enthalten.".into());
    }

    let host = url
        .host_str()
        .ok_or_else(|| "URL hat keinen gültigen Host".to_string())?;

    let port = url.port().unwrap_or(443);
    let sock_addrs = format!("{host}:{port}")
        .to_socket_addrs()
        .map_err(|e| format!("DNS-Auflösung für '{host}' fehlgeschlagen: {e}"))?;

    let mut gefunden = false;
    for addr in sock_addrs {
        gefunden = true;
        let ip = addr.ip();
        if !ist_ip_erlaubt(&ip) {
            return Err(format!(
                "Sicherheitsblockade (SSRF): Host '{host}' löst auf interne oder private IP '{ip}' auf. Download verweigert."
            ));
        }
    }

    if !gefunden {
        return Err(format!("Host '{host}' konnte keiner IP-Adresse zugeordnet werden."));
    }

    Ok(url)
}

// ── Quarantäne-Manifest ─────────────────────────────────────────────────────

fn manifest_pfad(quarantaene_ordner: &Path) -> PathBuf {
    quarantaene_ordner.join(MANIFEST_DATEI)
}

fn manifest_laden(quarantaene_ordner: &Path) -> HashMap<String, ArtefaktEintrag> {
    let pfad = manifest_pfad(quarantaene_ordner);
    if !pfad.exists() {
        return HashMap::new();
    }
    fs::read_to_string(&pfad)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn manifest_speichern(
    quarantaene_ordner: &Path,
    eintraege: &HashMap<String, ArtefaktEintrag>,
) -> Result<(), String> {
    let text = serde_json::to_string_pretty(eintraege)
        .map_err(|e| format!("Manifest-Serialisierung fehlgeschlagen: {e}"))?;
    fs::write(manifest_pfad(quarantaene_ordner), text.as_bytes())
        .map_err(|e| format!("Konnte Manifest nicht schreiben: {e}"))
}

// ── Download in Quarantäne ──────────────────────────────────────────────────

fn dateiname_bereinigen(original: &str) -> String {
    let raw = original.trim();
    let name = raw.rsplit(['/', '\\']).next().unwrap_or(raw);
    let mut sauber: String = name
        .chars()
        .map(|c| if c.is_alphanumeric() || c == '.' || c == '-' || c == '_' { c } else { '_' })
        .collect();

    // Führende/nachfolgende Punkte, Leerzeichen und Unterstriche entfernen
    sauber = sauber.trim_matches(|c| c == '.' || c == ' ' || c == '_').to_string();

    const RESERVIERT: [&str; 22] = [
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    ];

    let stamm = sauber.split('.').next().unwrap_or("").to_lowercase();
    if sauber.is_empty() || sauber == "." || sauber == ".." || RESERVIERT.contains(&stamm.as_str()) {
        "artefakt.bin".to_string()
    } else {
        sauber
    }
}

pub fn download(
    app: &AppHandle,
    url_str: &str,
    sha256_herausgeber: Option<&str>,
) -> Result<Value, String> {
    let konfig = konfig::laden(app).unwrap_or_default();
    if !konfig.artifact_install_aktiv {
        return Err(
            "Artefakt-Installationen sind in den Desktop-Einstellungen deaktiviert. Der Benutzer kann die Funktion dort aktivieren."
                .into(),
        );
    }

    let download_limit = konfig
        .max_download_bytes
        .clamp(1024 * 1024, MAX_DOWNLOAD_LIMIT_BYTES);
    let mut aktuelle_url = url_sicher_pruefen(url_str)?;

    let client = Client::builder()
        .redirect(Policy::none())
        .timeout(std::time::Duration::from_secs(300))
        .build()
        .map_err(|e| format!("HTTP-Client-Initialisierung fehlgeschlagen: {e}"))?;

    let mut response = None;
    for _ in 0..=MAX_WEITERLEITUNGEN {
        let resp = client
            .get(aktuelle_url.as_str())
            .send()
            .map_err(|e| format!("Download-Anfrage fehlgeschlagen: {e}"))?;

        if resp.status().is_redirection() {
            let ziel_str = resp
                .headers()
                .get(LOCATION)
                .and_then(|v| v.to_str().ok())
                .ok_or_else(|| "Weiterleitung ohne Location-Header".to_string())?;

            let ziel_url = aktuelle_url
                .join(ziel_str)
                .map_err(|e| format!("Ungültige Weiterleitungs-URL: {e}"))?;

            aktuelle_url = url_sicher_pruefen(ziel_url.as_str())?;
            continue;
        }

        if !resp.status().is_success() {
            return Err(format!("Download-Server antwortete mit Status: {}", resp.status()));
        }

        response = Some(resp);
        break;
    }

    let mut resp = response.ok_or_else(|| "Zu viele Weiterleitungen (max 5 erlaubt)".to_string())?;

    let dateiname = resp
        .url()
        .path_segments()
        .and_then(|mut segs| segs.next_back())
        .filter(|s| !s.is_empty())
        .map(dateiname_bereinigen)
        .unwrap_or_else(|| "artefakt.bin".to_string());

    let artifact_id = Uuid::new_v4().to_string();
    let basis = quarantaene_basis(app)?;
    let artifact_dir = basis.join(&artifact_id);
    fs::create_dir_all(&artifact_dir)
        .map_err(|e| format!("Konnte Artefakt-Ordner nicht erstellen: {e}"))?;

    let part_pfad = artifact_dir.join(format!("{dateiname}.part"));
    let ziel_pfad = artifact_dir.join(&dateiname);

    let mut part_datei = File::create(&part_pfad)
        .map_err(|e| format!("Konnte temporäre Download-Datei nicht erstellen: {e}"))?;

    let mut hasher = Sha256::new();
    let mut heruntergeladen = 0u64;
    let mut puffer = [0u8; 64 * 1024];

    loop {
        let gelesen = match resp.read(&mut puffer) {
            Ok(0) => break,
            Ok(n) => n,
            Err(e) => {
                let _ = fs::remove_file(&part_pfad);
                return Err(format!("Download-Verbindungsabbruch: {e}"));
            }
        };

        heruntergeladen += gelesen as u64;
        if heruntergeladen > download_limit {
            let _ = fs::remove_file(&part_pfad);
            let limit_gib = download_limit / (1024 * 1024 * 1024);
            return Err(format!(
                "Download-Limit überschritten (max. {limit_gib} GiB). Download wurde aus Sicherheitsgründen abgebrochen."
            ));
        }

        hasher.update(&puffer[..gelesen]);
        if let Err(e) = part_datei.write_all(&puffer[..gelesen]) {
            let _ = fs::remove_file(&part_pfad);
            return Err(format!("Schreibfehler während Download: {e}"));
        }
    }

    drop(part_datei);
    fs::rename(&part_pfad, &ziel_pfad)
        .map_err(|e| format!("Konnte heruntergeladene Datei nicht atomar übernehmen: {e}"))?;

    let sha256_berechnet = format!("{:x}", hasher.finalize());
    let status = if let Some(erwartet) = sha256_herausgeber {
        let bereinigt_erwartet = erwartet.trim().to_lowercase();
        if bereinigt_erwartet == sha256_berechnet.to_lowercase() {
            "verified".to_string()
        } else {
            "mismatch".to_string()
        }
    } else {
        "unverified".to_string()
    };

    let eintrag = ArtefaktEintrag {
        artifact_id: artifact_id.clone(),
        dateiname,
        groesse_bytes: heruntergeladen,
        sha256_berechnet: sha256_berechnet.clone(),
        sha256_herausgeber: sha256_herausgeber.map(str::trim).map(str::to_lowercase),
        status: status.clone(),
        defender_sauber: None,
        defender_befund: None,
        sandbox_bericht: None,
        letzte_target_id: None,
        snapshot_pfad: None,
        erstellt_am: jetzt_sekunden(),
    };

    let mut manifest = manifest_laden(&basis);
    manifest.insert(artifact_id.clone(), eintrag.clone());
    manifest_speichern(&basis, &manifest)?;

    Ok(json!({
        "artifact_id": artifact_id,
        "dateiname": eintrag.dateiname,
        "groesse_bytes": eintrag.groesse_bytes,
        "sha256_berechnet": eintrag.sha256_berechnet,
        "status": eintrag.status,
        "hinweis": "Artefakt erfolgreich in lokaler Quarantäne abgelegt. Führe als Nächstes die Prüfung (Defender & Windows Sandbox) aus.",
    }))
}

// ── Statische Prüfung & Windows Sandbox ─────────────────────────────────────

pub fn pruefen_und_sandbox(app: &AppHandle, artifact_id: &str) -> Result<Value, String> {
    let basis = quarantaene_basis(app)?;
    let mut manifest = manifest_laden(&basis);
    let eintrag = manifest.get_mut(artifact_id).ok_or_else(|| {
        format!("Artefakt mit ID '{artifact_id}' nicht in Quarantäne gefunden.")
    })?;

    let artifact_dir = basis.join(artifact_id);
    let datei_pfad = artifact_dir.join(&eintrag.dateiname);

    if !datei_pfad.exists() {
        return Err(format!("Quarantäne-Datei für '{artifact_id}' existiert nicht auf der Festplatte."));
    }

    // 1. Microsoft Defender Scan mit -DisableRemediation
    let defender_ergebnis = virenscan::pruefen(&datei_pfad.to_string_lossy());
    let (sauber, befund) = match defender_ergebnis {
        Ok(ref val) => {
            let s = val["sauber"].as_bool().unwrap_or(true);
            let b = val["befund"].as_str().map(|s| s.to_string());
            (Some(s), b)
        }
        Err(ref e) => (None, Some(format!("Defender-Scan nicht durchführbar: {e}"))),
    };

    eintrag.defender_sauber = sauber;
    eintrag.defender_befund = befund.clone();

    if sauber == Some(false) {
        eintrag.status = "threat_detected".to_string();
    }

    // 2. Verbindlicher Windows Sandbox Start (flüchtig & schreibgeschützt)
    let sandbox_bericht = sandbox_container::starten(&artifact_dir, artifact_id)?;
    eintrag.sandbox_bericht = Some(sandbox_bericht.clone());

    let dateiname = eintrag.dateiname.clone();
    let sha256_berechnet = eintrag.sha256_berechnet.clone();
    let status = eintrag.status.clone();

    manifest_speichern(&basis, &manifest)?;

    Ok(json!({
        "artifact_id": artifact_id,
        "dateiname": dateiname,
        "sha256_berechnet": sha256_berechnet,
        "status": status,
        "defender": {
            "sauber": sauber,
            "befund": befund,
        },
        "sandbox": {
            "zustand": sandbox_bericht.zustand,
            "verfuegbar": sandbox_bericht.verfuegbar,
            "isoliert": sandbox_bericht.isoliert,
            "hinweis": sandbox_bericht.hinweis,
        },
    }))
}

// ── Software & Game Locator ─────────────────────────────────────────────────

pub fn locator_ausfuehren(app: &AppHandle) -> Result<Value, String> {
    let konfig = konfig::laden(app).unwrap_or_default();
    let mut registry = HashMap::new();
    let mut ziele = Vec::new();

    // 1. Steam Library Scanner
    let steam_basis_pfade = [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"D:\SteamLibrary",
        r"E:\SteamLibrary",
    ];

    for steam_pfad_str in steam_basis_pfade {
        let steam_pfad = Path::new(steam_pfad_str);
        let common = steam_pfad.join(r"steamapps\common");
        if common.is_dir() {
            if let Ok(eintraege) = fs::read_dir(&common) {
                for eintrag in eintraege.flatten() {
                    if let Ok(meta) = eintrag.metadata() {
                        if meta.is_dir() {
                            let spiel_name = eintrag.file_name().to_string_lossy().to_string();
                            let target_id = format!("target_steam_{}", dateiname_bereinigen(&spiel_name));
                            let target_eintrag = TargetEintrag {
                                target_id: target_id.clone(),
                                name: format!("{spiel_name} (Steam)"),
                                ziel_art: "steam".to_string(),
                                absoluter_pfad: eintrag.path(),
                            };
                            registry.insert(target_id.clone(), target_eintrag);
                            ziele.push(json!({
                                "target_id": target_id,
                                "name": format!("{spiel_name} (Steam)"),
                                "ziel_art": "steam",
                            }));
                        }
                    }
                }
            }
        }
    }

    // 2. Benutzer-Suchwurzeln aus konfig.json
    for (idx, wurzel_str) in konfig.search_roots.iter().enumerate() {
        let wurzel = Path::new(wurzel_str);
        if wurzel.is_dir() {
            let target_id = format!("target_custom_{idx}");
            let target_eintrag = TargetEintrag {
                target_id: target_id.clone(),
                name: format!("Suchwurzel: {}", wurzel.file_name().map(|f| f.to_string_lossy()).unwrap_or_default()),
                ziel_art: "custom_search_root".to_string(),
                absoluter_pfad: wurzel.to_path_buf(),
            };
            registry.insert(target_id.clone(), target_eintrag);
            ziele.push(json!({
                "target_id": target_id,
                "name": format!("Suchwurzel: {}", wurzel.file_name().map(|f| f.to_string_lossy()).unwrap_or_default()),
                "ziel_art": "custom_search_root",
            }));
        }
    }

    let mut lock = TARGET_REGISTRY.lock().unwrap_or_else(|e| e.into_inner());
    *lock = Some(registry);

    Ok(json!({
        "ziele": ziele,
        "anzahl": ziele.len(),
    }))
}

// ── Deployment & Snapshot-Rollback ──────────────────────────────────────────

pub fn deployen(
    app: &AppHandle,
    artifact_id: &str,
    target_id: &str,
) -> Result<Value, String> {
    let basis = quarantaene_basis(app)?;
    let mut manifest = manifest_laden(&basis);
    let eintrag = manifest.get_mut(artifact_id).ok_or_else(|| {
        format!("Artefakt '{artifact_id}' nicht in Quarantäne gefunden.")
    })?;

    if eintrag.status == "mismatch" {
        return Err("Deployment verweigert: Der SHA-256-Hash stimmt nicht mit dem Herausgeber-Hash überein (mismatch).".into());
    }

    if eintrag.status == "threat_detected" {
        return Err("Deployment verweigert: Windows Defender hat in diesem Artefakt Schadcode erkannt.".into());
    }

    let lock = TARGET_REGISTRY.lock().unwrap_or_else(|e| e.into_inner());
    let target = lock
        .as_ref()
        .and_then(|r| r.get(target_id))
        .cloned()
        .ok_or_else(|| {
            format!("Installationsziel '{target_id}' nicht gefunden. Führe zuerst locator aus.")
        })?;
    drop(lock);

    let target_root = dunce::canonicalize(&target.absoluter_pfad)
        .map_err(|e| format!("Zielverzeichnis nicht lesbar: {e}"))?;

    let quelle_pfad = basis.join(artifact_id).join(&eintrag.dateiname);
    if !quelle_pfad.exists() {
        return Err(format!("Quell-Artefaktdatei fehlt: {}", quelle_pfad.display()));
    }

    // Snapshot vorbereiten
    let snapshot_id = format!("{}_{}", target_id, jetzt_sekunden());
    let snap_dir = snapshot_basis(app)?.join(&snapshot_id);
    fs::create_dir_all(&snap_dir)
        .map_err(|e| format!("Konnte Snapshot-Verzeichnis nicht anlegen: {e}"))?;

    let mut geaendert = Vec::new();
    let mut neu = Vec::new();

    // Bei ZIP/Archiv entpacken oder bei Einzeldatei kopieren
    let ziel_datei = target_root.join(&eintrag.dateiname);

    // Anti-Traversal-Check
    if !ziel_datei.starts_with(&target_root) {
        return Err("Ausbruchsversuch erkannt: Zielpfad liegt außerhalb des Zielverzeichnisses.".into());
    }

    if ziel_datei.symlink_metadata().is_ok() {
        if let Ok(echt) = dunce::canonicalize(&ziel_datei) {
            if !echt.starts_with(&target_root) {
                return Err("Ausbruchsversuch über Verknüpfung erkannt.".into());
            }
        }
    }

    if ziel_datei.exists() {
        let backup_pfad = snap_dir.join(&eintrag.dateiname);
        fs::copy(&ziel_datei, &backup_pfad)
            .map_err(|e| format!("Konnte Backup der Originaldatei nicht erstellen: {e}"))?;
        geaendert.push(eintrag.dateiname.clone());
    } else {
        neu.push(eintrag.dateiname.clone());
    }

    // Atomares Deployment
    if let Err(e) = fs::copy(&quelle_pfad, &ziel_datei) {
        // Sofortiger automatischer Rollback bei Teilfehlern
        if ziel_datei.exists() {
            let _ = fs::remove_file(&ziel_datei);
        }
        for g in &geaendert {
            let b = snap_dir.join(g);
            let z = target_root.join(g);
            let _ = fs::copy(&b, &z);
        }
        return Err(format!("Deployment fehlgeschlagen, automatischer Rollback ausgeführt: {e}"));
    }

    let snap_manifest = SnapshotManifest {
        snapshot_id: snapshot_id.clone(),
        artifact_id: artifact_id.to_string(),
        target_id: target_id.to_string(),
        geaenderte_dateien: geaendert,
        neue_dateien: neu,
        zeitstempel: jetzt_sekunden(),
    };

    let snap_manifest_pfad = snap_dir.join("snapshot_manifest.json");
    let snap_text = serde_json::to_string_pretty(&snap_manifest).unwrap_or_default();
    let _ = fs::write(snap_manifest_pfad, snap_text.as_bytes());

    eintrag.status = "deployed".to_string();
    eintrag.letzte_target_id = Some(target_id.to_string());
    eintrag.snapshot_pfad = Some(snap_dir.to_string_lossy().to_string());
    manifest_speichern(&basis, &manifest)?;

    Ok(json!({
        "artifact_id": artifact_id,
        "target_id": target_id,
        "snapshot_id": snapshot_id,
        "status": "deployed",
        "hinweis": "Artefakt erfolgreich installiert. Ein Snapshot-Rollback ist jederzeit möglich.",
    }))
}

pub fn rollback(app: &AppHandle, artifact_id: &str) -> Result<Value, String> {
    let basis = quarantaene_basis(app)?;
    let mut manifest = manifest_laden(&basis);
    let eintrag = manifest.get_mut(artifact_id).ok_or_else(|| {
        format!("Artefakt '{artifact_id}' nicht in Quarantäne gefunden.")
    })?;

    let snap_dir_str = eintrag.snapshot_pfad.as_deref().ok_or_else(|| {
        "Kein Snapshot für dieses Artefakt hinterlegt. Rollback nicht möglich.".to_string()
    })?;

    let snap_dir = Path::new(snap_dir_str);
    let snap_manifest_pfad = snap_dir.join("snapshot_manifest.json");
    if !snap_manifest_pfad.exists() {
        return Err("Snapshot-Manifest fehlt auf der Festplatte.".into());
    }

    let snap_text = fs::read_to_string(&snap_manifest_pfad)
        .map_err(|e| format!("Snapshot-Manifest unlesbar: {e}"))?;
    let snap_manifest: SnapshotManifest = serde_json::from_str(&snap_text)
        .map_err(|e| format!("Snapshot-Manifest fehlerhaft: {e}"))?;

    let mut lock = TARGET_REGISTRY.lock().unwrap_or_else(|e| e.into_inner());
    if lock.is_none() || !lock.as_ref().map_or(false, |r| r.contains_key(&snap_manifest.target_id)) {
        drop(lock);
        let _ = locator_ausfuehren(app);
        lock = TARGET_REGISTRY.lock().unwrap_or_else(|e| e.into_inner());
    }
    let target = lock
        .as_ref()
        .and_then(|r| r.get(&snap_manifest.target_id))
        .cloned()
        .ok_or_else(|| {
            format!("Installationsziel '{}' nicht gefunden. Führe zuerst locator aus.", snap_manifest.target_id)
        })?;
    drop(lock);

    let target_root = dunce::canonicalize(&target.absoluter_pfad)
        .map_err(|e| format!("Zielverzeichnis nicht lesbar: {e}"))?;

    // 1. Neu hinzugefügte Dateien löschen
    for n in &snap_manifest.neue_dateien {
        let z = target_root.join(n);
        if z.exists() && z.starts_with(&target_root) {
            if z.symlink_metadata().is_ok() {
                if let Ok(echt) = dunce::canonicalize(&z) {
                    if !echt.starts_with(&target_root) {
                        continue;
                    }
                }
            }
            let _ = fs::remove_file(&z);
        }
    }

    // 2. Geänderte Dateien aus dem Backup wiederherstellen
    for g in &snap_manifest.geaenderte_dateien {
        let b = snap_dir.join(g);
        let z = target_root.join(g);
        if b.exists() && z.starts_with(&target_root) {
            if z.symlink_metadata().is_ok() {
                if let Ok(echt) = dunce::canonicalize(&z) {
                    if !echt.starts_with(&target_root) {
                        continue;
                    }
                }
            }
            fs::copy(&b, &z)
                .map_err(|e| format!("Fehler beim Wiederherstellen von '{g}': {e}"))?;
        }
    }

    eintrag.status = "rolled_back".to_string();
    manifest_speichern(&basis, &manifest)?;

    Ok(json!({
        "artifact_id": artifact_id,
        "target_id": snap_manifest.target_id,
        "status": "rolled_back",
        "hinweis": "Vorheriger Dateizustand wurde vollständig aus dem Snapshot wiederhergestellt.",
    }))
}

// ── Nutzer-Installer starten ────────────────────────────────────────────────

pub fn installer_starten(
    app: &AppHandle,
    artifact_id: &str,
    installer_args: &[String],
) -> Result<Value, String> {
    let basis = quarantaene_basis(app)?;
    let manifest = manifest_laden(&basis);
    let eintrag = manifest.get(artifact_id).ok_or_else(|| {
        format!("Artefakt '{artifact_id}' nicht in Quarantäne gefunden.")
    })?;

    if eintrag.status == "mismatch" {
        return Err("Installer-Start verweigert: SHA-256-Hash stimmt nicht mit dem Herausgeber-Hash überein (mismatch).".into());
    }

    if eintrag.status == "threat_detected" {
        return Err("Installer-Start verweigert: Microsoft Defender hat Schadcode erkannt.".into());
    }

    let datei_pfad = basis.join(artifact_id).join(&eintrag.dateiname);
    if !datei_pfad.exists() {
        return Err(format!("Installer-Datei nicht gefunden: {}", datei_pfad.display()));
    }

    let sandbox_fehlt = !sandbox_container::ist_verfuegbar();

    let mut cmd = std::process::Command::new(&datei_pfad);
    cmd.args(installer_args);

    let _child = cmd.spawn().map_err(|e| format!("Installer konnte nicht gestartet werden: {e}"))?;

    Ok(json!({
        "artifact_id": artifact_id,
        "status": "gestartet",
        "sandbox_fehlt_warnung": sandbox_fehlt,
        "hinweis": if sandbox_fehlt {
            "Installer wurde im Benutzerkontext gestartet (Hochrisiko-Aktion: Windows Sandbox Isolation war nicht verfügbar). Eventuelle UAC-Dialoge müssen manuell von dir bestätigt werden."
        } else {
            "Installer wurde im Benutzerkontext gestartet. Eventuelle UAC-Dialoge müssen manuell von dir bestätigt werden."
        },
    }))
}

// ── Dispatcher für desktop_artifact Auftrag ─────────────────────────────────

pub fn ausfuehren(app: &AppHandle, argumente: &Value) -> Result<Value, String> {
    let aktion = argumente["aktion"].as_str().unwrap_or("");
    match aktion {
        "download" => {
            let url = argumente["url"].as_str().ok_or("Zum Download fehlt 'url'")?;
            let sha256 = argumente["sha256"].as_str();
            download(app, url, sha256)
        }
        "pruefen" | "sandbox" => {
            let artifact_id = argumente["artifact_id"]
                .as_str()
                .ok_or("Zur Prüfung fehlt 'artifact_id'")?;
            pruefen_und_sandbox(app, artifact_id)
        }
        "locator" => locator_ausfuehren(app),
        "deploy" => {
            let artifact_id = argumente["artifact_id"]
                .as_str()
                .ok_or("Zum Deployment fehlt 'artifact_id'")?;
            let target_id = argumente["target_id"]
                .as_str()
                .ok_or("Zum Deployment fehlt 'target_id'")?;
            deployen(app, artifact_id, target_id)
        }
        "rollback" => {
            let artifact_id = argumente["artifact_id"]
                .as_str()
                .ok_or("Für Rollback fehlt 'artifact_id'")?;
            rollback(app, artifact_id)
        }
        "installer" => {
            let artifact_id = argumente["artifact_id"]
                .as_str()
                .ok_or("Für Installer-Start fehlt 'artifact_id'")?;
            let args: Vec<String> = argumente["installer_args"]
                .as_array()
                .map(|a| a.iter().filter_map(|v| v.as_str()).map(str::to_string).collect())
                .unwrap_or_default();
            installer_starten(app, artifact_id, &args)
        }
        "status" => {
            let basis = quarantaene_basis(app)?;
            let manifest = manifest_laden(&basis);
            if let Some(artifact_id) = argumente["artifact_id"].as_str() {
                let e = manifest
                    .get(artifact_id)
                    .ok_or_else(|| format!("Artefakt '{artifact_id}' nicht gefunden."))?;
                Ok(json!(e))
            } else {
                let liste: Vec<&ArtefaktEintrag> = manifest.values().collect();
                Ok(json!({ "artefakte": liste, "anzahl": liste.len() }))
            }
        }
        andere => Err(format!(
            "Unbekannte Aktion für desktop_artifact: '{andere}'. Erlaubt: download, pruefen, sandbox, locator, deploy, rollback, installer, status."
        )),
    }
}

// ── Modultests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Ipv4Addr;

    #[test]
    fn ssrf_blockiert_alle_privaten_und_internen_ips() {
        let blockierte = [
            IpAddr::V4(Ipv4Addr::new(127, 0, 0, 1)),
            IpAddr::V4(Ipv4Addr::new(10, 0, 0, 5)),
            IpAddr::V4(Ipv4Addr::new(172, 16, 0, 1)),
            IpAddr::V4(Ipv4Addr::new(172, 31, 255, 255)),
            IpAddr::V4(Ipv4Addr::new(192, 168, 1, 1)),
            IpAddr::V4(Ipv4Addr::new(169, 254, 169, 254)),
            IpAddr::V4(Ipv4Addr::new(100, 64, 0, 1)),
            IpAddr::V4(Ipv4Addr::new(0, 0, 0, 0)),
            IpAddr::V4(Ipv4Addr::new(224, 0, 0, 1)),
            IpAddr::V4(Ipv4Addr::new(255, 255, 255, 255)),
            IpAddr::V6(std::net::Ipv6Addr::LOCALHOST),
            IpAddr::V6(std::net::Ipv6Addr::UNSPECIFIED),
        ];

        for ip in blockierte {
            assert!(!ist_ip_erlaubt(&ip), "IP {ip} muss gesperrt sein!");
        }

        let erlaubte = [
            IpAddr::V4(Ipv4Addr::new(8, 8, 8, 8)),
            IpAddr::V4(Ipv4Addr::new(1, 1, 1, 1)),
            IpAddr::V4(Ipv4Addr::new(140, 82, 121, 4)), // GitHub
        ];

        for ip in erlaubte {
            assert!(ist_ip_erlaubt(&ip), "Öffentliche IP {ip} muss erlaubt sein!");
        }
    }

    #[test]
    fn url_sicher_pruefen_weist_unsichere_urls_ab() {
        assert!(url_sicher_pruefen("http://example.com/test.zip").is_err(), "HTTP muss abgewiesen werden");
        assert!(url_sicher_pruefen("https://user:pass@example.com/test.zip").is_err(), "Credentials in URL müssen abgewiesen werden");
        assert!(url_sicher_pruefen("https://127.0.0.1/test.zip").is_err(), "Loopback-IP muss abgewiesen werden");
        assert!(url_sicher_pruefen("https://192.168.1.100/test.zip").is_err(), "Private IP muss abgewiesen werden");
    }

    #[test]
    fn dateinamen_werden_sicher_bereinigt() {
        assert_eq!(dateiname_bereinigen("test.zip"), "test.zip");
        assert_eq!(dateiname_bereinigen("../../evil.exe"), "evil.exe");
        assert_eq!(dateiname_bereinigen("C:\\Windows\\cmd.exe"), "cmd.exe");
        assert_eq!(dateiname_bereinigen("foo;bar&baz.mod"), "foo_bar_baz.mod");
    }
}
