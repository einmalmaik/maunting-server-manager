//! Die Gerätekonfiguration des Browsers: was Rust selbst braucht.
//!
//! Hier steht nur, was die native Seite liest (Panel-Adresse, Schild,
//! Download-Ordner). Darstellung, Suchmaschine und Startseite bleiben in der
//! Oberfläche. Kein Token, kein Geheimnis: das Refresh-Token liegt im
//! Schlüsselbund (`geheimnisse.rs`), das Access-Token nur im Speicher der
//! Oberfläche.
//!
//! Die Befehle heißen wie in MSS (`konfig_laden`, `konfig_aendern`), damit die
//! geteilten Bausteine der Oberfläche (Kopplung, Transport) hier unverändert
//! laufen.

use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, State};

const DATEI: &str = "msb_konfig.json";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Konfig {
    /// Adresse der Panel-API. `None`, solange der Browser nicht gekoppelt ist.
    pub backend_url: Option<String>,
    /// Eine Kopplung wurde abgeschlossen.
    pub eingerichtet: bool,
    /// Werbe- und Trackerfilter an.
    pub schild_aktiv: bool,
    /// Hosts, auf denen der Filter pausiert.
    pub schild_ausnahmen: Vec<String>,
    /// Zielordner für Downloads. `None` = Download-Ordner des Systems.
    pub download_ordner: Option<String>,
    /// Cookies, Cache und Website-Daten beim Schließen löschen.
    pub vergessen_beim_schliessen: bool,
}

impl Default for Konfig {
    fn default() -> Self {
        Self {
            backend_url: None,
            eingerichtet: false,
            schild_aktiv: true,
            schild_ausnahmen: Vec::new(),
            download_ordner: None,
            vergessen_beim_schliessen: false,
        }
    }
}

/// Die geladene Konfiguration, damit Schild und Downloads nicht bei jeder
/// Anfrage die Datei lesen.
#[derive(Default)]
pub struct KonfigZustand(pub Mutex<Konfig>);

fn pfad(app: &AppHandle) -> Result<PathBuf, String> {
    let ordner = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("Konfigurationsordner unbekannt: {e}"))?;
    fs::create_dir_all(&ordner).map_err(|e| format!("Konfigurationsordner nicht anlegbar: {e}"))?;
    Ok(ordner.join(DATEI))
}

/// Liest die Datei. Fehlt sie oder ist sie kaputt, gilt der Standard: eine
/// unlesbare Konfiguration soll den Browser nicht am Start hindern.
///
/// Steht darin mehr, als `Konfig` kennt, wird sie gleich neu geschrieben. Die
/// erste Fassung des Browsers legte dort das Access-Token im Klartext ab; ohne
/// das Neuschreiben bliebe es in der Datei liegen.
pub fn laden(app: &AppHandle) -> Konfig {
    let Ok(p) = pfad(app) else {
        return Konfig::default();
    };
    let Some(roh) = fs::read_to_string(&p).ok().and_then(|i| serde_json::from_str::<serde_json::Value>(&i).ok()) else {
        return Konfig::default();
    };
    let konfig: Konfig = serde_json::from_value(roh.clone()).unwrap_or_default();
    if hat_fremde_felder(&roh) {
        if let Err(fehler) = speichern(app, &konfig) {
            eprintln!("[MSB] Alte Konfiguration nicht bereinigt: {fehler}");
        }
    }
    konfig
}

fn hat_fremde_felder(roh: &serde_json::Value) -> bool {
    let bekannt = serde_json::to_value(Konfig::default()).unwrap_or_default();
    match (roh.as_object(), bekannt.as_object()) {
        (Some(roh), Some(bekannt)) => roh.keys().any(|k| !bekannt.contains_key(k)),
        _ => true,
    }
}

fn speichern(app: &AppHandle, konfig: &Konfig) -> Result<(), String> {
    let json = serde_json::to_string_pretty(konfig).map_err(|e| e.to_string())?;
    let ziel = pfad(app)?;
    // Erst daneben schreiben, dann umbenennen: ein Absturz mitten im Schreiben
    // hinterlässt keine halbe Datei.
    let teil = ziel.with_extension("json.part");
    fs::write(&teil, json).map_err(|e| format!("Konfiguration nicht schreibbar: {e}"))?;
    fs::rename(&teil, &ziel).map_err(|e| format!("Konfiguration nicht schreibbar: {e}"))
}

const LOKALE_HOSTS: [&str; 3] = ["localhost", "127.0.0.1", "[::1]"];

/// Dieselbe Regel wie in MSS: `https://` ist Pflicht, `http://` nur auf dem
/// eigenen Rechner. Über diese Adresse gehen Kopplungscode und Refresh-Token.
pub fn backend_url_verboten(url: &str) -> Option<String> {
    let Ok(geparst) = url::Url::parse(url) else {
        return Some("Die Adresse ist keine gültige URL.".into());
    };
    if !geparst.username().is_empty() || geparst.password().is_some() {
        return Some("Die Adresse darf keine Zugangsdaten enthalten.".into());
    }
    match geparst.scheme() {
        "https" => None,
        "http" => {
            let host = geparst.host_str().unwrap_or_default();
            if LOKALE_HOSTS.contains(&host) {
                None
            } else {
                Some("Die Panel-Adresse muss mit https:// beginnen.".into())
            }
        }
        _ => Some("Die Panel-Adresse muss mit https:// beginnen.".into()),
    }
}

/// Mischt einzelne Felder in die Konfiguration. Unbekannte Felder werden
/// abgewiesen statt still übergangen: ein Tippfehler in der Oberfläche soll
/// auffallen.
pub fn felder_einmischen(basis: &Konfig, felder: serde_json::Value) -> Result<Konfig, String> {
    let serde_json::Value::Object(felder) = felder else {
        return Err("Erwartet wurde ein Objekt mit Feldern.".into());
    };
    let mut wert = serde_json::to_value(basis).map_err(|e| e.to_string())?;
    let ziel = wert.as_object_mut().expect("Konfig ist ein Objekt");
    for (name, neu) in felder {
        if !ziel.contains_key(&name) {
            return Err(format!("Unbekanntes Konfigurationsfeld: {name}"));
        }
        ziel.insert(name, neu);
    }
    let mut konfig: Konfig = serde_json::from_value(wert).map_err(|e| e.to_string())?;
    if let Some(url) = konfig.backend_url.as_deref() {
        let url = url.trim().trim_end_matches('/').to_string();
        if let Some(grund) = backend_url_verboten(&url) {
            return Err(grund);
        }
        konfig.backend_url = Some(url);
    }
    konfig.schild_ausnahmen = konfig
        .schild_ausnahmen
        .iter()
        .map(|h| h.trim().to_lowercase())
        .filter(|h| !h.is_empty())
        .collect();
    konfig.schild_ausnahmen.sort();
    konfig.schild_ausnahmen.dedup();
    Ok(konfig)
}

#[tauri::command]
pub fn konfig_laden(zustand: State<'_, KonfigZustand>) -> Konfig {
    zustand.0.lock().unwrap().clone()
}

#[tauri::command]
pub fn konfig_aendern(
    app: AppHandle,
    zustand: State<'_, KonfigZustand>,
    felder: serde_json::Value,
) -> Result<Konfig, String> {
    let mut aktuell = zustand.0.lock().unwrap();
    let neu = felder_einmischen(&aktuell, felder)?;
    speichern(&app, &neu)?;
    crate::schild::konfig_uebernehmen(&neu);
    *aktuell = neu.clone();
    Ok(neu)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn panel_adresse_nur_mit_tls_oder_lokal() {
        assert!(backend_url_verboten("https://panel.example.com").is_none());
        assert!(backend_url_verboten("http://localhost:8000").is_none());
        assert!(backend_url_verboten("http://127.0.0.1:8000").is_none());
        assert!(backend_url_verboten("http://panel.example.com").is_some());
        assert!(backend_url_verboten("http://localhost@fremd.example").is_some());
        assert!(backend_url_verboten("ftp://panel.example.com").is_some());
        assert!(backend_url_verboten("kein link").is_some());
    }

    #[test]
    fn einmischen_setzt_nur_bekannte_felder() {
        let basis = Konfig::default();
        let neu = felder_einmischen(&basis, json!({"backend_url": "https://p.example/", "eingerichtet": true})).unwrap();
        assert_eq!(neu.backend_url.as_deref(), Some("https://p.example"));
        assert!(neu.eingerichtet);
        assert!(felder_einmischen(&basis, json!({"access_token": "x"})).is_err());
        assert!(felder_einmischen(&basis, json!({"backend_url": "http://fremd.example"})).is_err());
    }

    #[test]
    fn ausnahmen_werden_bereinigt() {
        let neu = felder_einmischen(
            &Konfig::default(),
            json!({"schild_ausnahmen": [" Heise.de ", "heise.de", ""]}),
        )
        .unwrap();
        assert_eq!(neu.schild_ausnahmen, vec!["heise.de".to_string()]);
    }

    #[test]
    fn alte_datei_mit_unbekannten_feldern_bleibt_lesbar() {
        // Die erste Fassung schrieb `access_token` und weitere Felder in die
        // Datei. Gelesen werden nur noch die bekannten, das Token fällt weg.
        let alt = r#"{"backend_url":null,"access_token":"geheim","adblock_aktiv":true}"#;
        let k: Konfig = serde_json::from_str(alt).unwrap();
        assert_eq!(k, Konfig::default());
        // … und die Datei wird beim Laden neu geschrieben, damit das Token
        // nicht in ihr liegen bleibt.
        assert!(hat_fremde_felder(&serde_json::from_str(alt).unwrap()));
        assert!(!hat_fremde_felder(&serde_json::to_value(Konfig::default()).unwrap()));
    }
}
