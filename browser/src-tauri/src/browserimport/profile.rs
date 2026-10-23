//! Welche Browser und Profile auf diesem Rechner liegen.
//!
//! Die Wurzeln stehen fest (`%LOCALAPPDATA%` und `%APPDATA%`); aus dem
//! Frontend kommt nur eine Profil-ID, nie ein Pfad. `pruefen` erzeugt die
//! Liste erneut und vergleicht kanonische Pfade, damit eine ID nicht aus der
//! Wurzel ausbrechen kann. Verknüpfungen und Junctions werden abgelehnt.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::BrowserProfil;

const CHROMIUM: &[(&str, &[&str])] = &[
    ("chrome", &["Google", "Chrome", "User Data"]),
    ("brave", &["BraveSoftware", "Brave-Browser", "User Data"]),
    ("edge", &["Microsoft", "Edge", "User Data"]),
    ("vivaldi", &["Vivaldi", "User Data"]),
];

const OPERA: &[(&str, &[&str])] = &[
    ("opera", &["Opera Software", "Opera Stable"]),
    ("opera_gx", &["Opera Software", "Opera GX Stable"]),
];

const AUSGESCHLOSSEN: &[&str] = &["Guest Profile", "System Profile"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Art {
    Chromium,
    Opera,
    Firefox,
}

#[derive(Debug, Clone)]
pub(crate) struct Profil {
    pub info: BrowserProfil,
    pub ordner: PathBuf,
    pub wurzel: PathBuf,
    pub art: Art,
}

fn unter(basis: &Path, teile: &[&str]) -> PathBuf {
    teile.iter().fold(basis.to_path_buf(), |pfad, teil| pfad.join(teil))
}

fn anzeige(wert: Option<&Value>, ersatz: &str) -> String {
    let text = wert.and_then(Value::as_str).unwrap_or("").trim();
    let text: String = text.chars().filter(|c| !c.is_control()).collect();
    if text.is_empty() {
        ersatz.to_string()
    } else {
        text.chars().take(120).collect()
    }
}

/// Die Profile aus `Local State`; fehlt die Datei oder ist sie leer, bleiben
/// die Ordner `Default` und `Profile N` mit brauchbaren Daten.
fn chromium(root: &Path, browser: &str) -> Vec<Profil> {
    let info = fs::read(root.join("Local State"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|wert| wert.get("profile")?.get("info_cache")?.as_object().cloned());

    let mut kandidaten: Vec<(String, String, String, Profil)> = Vec::new();
    if let Some(info) = info {
        for (ordnername, wert) in info {
            if AUSGESCHLOSSEN.contains(&ordnername.as_str()) || !gueltiger_ordnername(&ordnername) {
                continue;
            }
            let ordner = root.join(&ordnername);
            if let Some(profil) = kandidat(browser, &ordnername, anzeige(wert.get("name"), &ordnername), &ordner, root, Art::Chromium) {
                let zuletzt = wert.get("last_used").and_then(Value::as_str).unwrap_or("").to_string();
                kandidaten.push((zuletzt, ordnername, profil.info.id.clone(), profil));
            }
        }
    }

    if kandidaten.is_empty() {
        if let Ok(eintraege) = fs::read_dir(root) {
            for eintrag in eintraege.flatten() {
                if !eintrag.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                    continue;
                }
                let Some(name) = eintrag.file_name().to_str().map(str::to_string) else { continue };
                if AUSGESCHLOSSEN.contains(&name.as_str()) || (name != "Default" && !name.starts_with("Profile ")) {
                    continue;
                }
                let ordner = root.join(&name);
                if let Some(profil) = kandidat(browser, &name, name.clone(), &ordner, root, Art::Chromium) {
                    kandidaten.push((String::new(), name, profil.info.id.clone(), profil));
                }
            }
        }
    }

    kandidaten.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.to_lowercase().cmp(&b.1.to_lowercase())));
    kandidaten.into_iter().map(|(_, _, _, profil)| profil).collect()
}

/// Ein Profilordner steht direkt unter seiner Wurzel; `..`, Pfadtrenner und
/// absolute Pfade aus einer bearbeiteten `Local State` kommen nicht infrage.
fn gueltiger_ordnername(name: &str) -> bool {
    !name.is_empty()
        && name != "."
        && name != ".."
        && !name.contains(['\\', '/'])
        && !Path::new(name).is_absolute()
}

fn kandidat(browser: &str, ordnername: &str, name: String, ordner: &Path, wurzel: &Path, art: Art) -> Option<Profil> {
    if !fs::metadata(ordner).ok()?.is_dir() {
        return None;
    }
    let verlauf = ordner.join("History").is_file();
    let lesezeichen = ordner.join("Bookmarks").is_file();
    if !verlauf && !lesezeichen {
        return None;
    }
    Some(Profil {
        info: BrowserProfil {
            id: format!("{browser}:{ordnername}"),
            browser: browser.to_string(),
            name,
            verlauf,
            lesezeichen,
        },
        ordner: ordner.to_path_buf(),
        wurzel: wurzel.to_path_buf(),
        art,
    })
}

fn opera(ordner: &Path, browser: &str) -> Option<Profil> {
    if !fs::metadata(ordner).ok()?.is_dir() {
        return None;
    }
    let verlauf = ordner.join("History").is_file();
    let lesezeichen = ordner.join("Bookmarks").is_file();
    if !verlauf && !lesezeichen {
        return None;
    }
    Some(Profil {
        info: BrowserProfil {
            id: format!("{browser}:"),
            browser: browser.to_string(),
            name: "Standard".into(),
            verlauf,
            lesezeichen,
        },
        ordner: ordner.to_path_buf(),
        wurzel: ordner.to_path_buf(),
        art: Art::Opera,
    })
}

/// Namen aus `profiles.ini`, nach Ordner. Firefox legt dort den Anzeigenamen
/// ab, den der Nutzer beim Anlegen des Profils gesehen hat.
fn ini_namen(pfad: &Path) -> HashMap<String, String> {
    let Ok(text) = fs::read_to_string(pfad) else { return HashMap::new() };
    let mut aus = HashMap::new();
    let mut name: Option<String> = None;
    let mut profil_pfad: Option<String> = None;
    for zeile in text.lines().chain(std::iter::once("[Ende]")) {
        let zeile = zeile.trim();
        if zeile.starts_with('[') && zeile.ends_with(']') {
            if let (Some(name), Some(pfad)) = (name.take(), profil_pfad.take()) {
                let norm = pfad.replace('\\', "/");
                if let Some(ordner) = Path::new(&norm).file_name().and_then(|s| s.to_str()) {
                    let sauber: String = name.chars().filter(|c| !c.is_control()).collect();
                    if !sauber.trim().is_empty() {
                        aus.insert(ordner.to_string(), sauber.trim().chars().take(120).collect());
                    }
                }
            }
            continue;
        }
        let Some((schluessel, wert)) = zeile.split_once('=') else { continue };
        match schluessel.trim().to_ascii_lowercase().as_str() {
            "name" => name = Some(wert.trim().to_string()),
            "path" => profil_pfad = Some(wert.trim().to_string()),
            _ => {}
        }
    }
    aus
}

fn firefox(roaming: &Path) -> Vec<Profil> {
    let basis = unter(roaming, &["Mozilla", "Firefox"]);
    let ordner = basis.join("Profiles");
    let namen = ini_namen(&basis.join("profiles.ini"));
    let Ok(eintraege) = fs::read_dir(&ordner) else { return Vec::new() };
    let mut aus = Vec::new();
    for eintrag in eintraege.flatten() {
        if !eintrag.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            continue;
        }
        let Some(ordnername) = eintrag.file_name().to_str().map(str::to_string) else { continue };
        let profil_ordner = ordner.join(&ordnername);
        if !profil_ordner.join("places.sqlite").is_file() {
            continue;
        }
        let name = namen.get(&ordnername).cloned().unwrap_or_else(|| ordnername.clone());
        aus.push(Profil {
            info: BrowserProfil {
                id: format!("firefox:{ordnername}"),
                browser: "firefox".into(),
                name,
                verlauf: true,
                lesezeichen: true,
            },
            ordner: profil_ordner,
            wurzel: ordner.clone(),
            art: Art::Firefox,
        });
    }
    // `default-release` ist das Profil, das Firefox selbst anlegt und benutzt.
    let rang = |profil: &Profil| {
        let name = profil.ordner.file_name().and_then(|s| s.to_str()).unwrap_or("");
        if name.contains("default-release") {
            0
        } else if name.contains("default") {
            1
        } else {
            2
        }
    };
    aus.sort_by(|a, b| rang(a).cmp(&rang(b)).then_with(|| a.info.name.to_lowercase().cmp(&b.info.name.to_lowercase())));
    aus
}

pub(crate) fn finde_mit(local: &Path, roaming: &Path) -> Vec<Profil> {
    let mut aus = Vec::new();
    for (browser, teile) in CHROMIUM {
        aus.extend(chromium(&unter(local, teile), browser));
    }
    for (browser, teile) in OPERA {
        if let Some(profil) = opera(&unter(roaming, teile), browser) {
            aus.push(profil);
        }
    }
    aus.extend(firefox(roaming));
    aus
}

pub(crate) fn finde() -> Vec<Profil> {
    let (Some(local), Some(roaming)) = (std::env::var_os("LOCALAPPDATA"), std::env::var_os("APPDATA")) else {
        return Vec::new();
    };
    finde_mit(Path::new(&local), Path::new(&roaming))
}

fn ist_verknuepfung(meta: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    // FILE_ATTRIBUTE_REPARSE_POINT: Symlink, Junction oder ähnliches.
    meta.file_attributes() & 0x400 != 0
}

/// Prüft eine ID aus dem Frontend gegen die frisch erkannte Liste: der Ordner
/// muss ein echter Ordner unter seiner Wurzel sein.
pub(crate) fn pruefen(profil: &Profil) -> Result<(), &'static str> {
    let meta = fs::symlink_metadata(&profil.ordner).map_err(|_| "profil_unbekannt")?;
    if !meta.is_dir() || ist_verknuepfung(&meta) {
        return Err("profil_unbekannt");
    }
    let ordner = profil.ordner.canonicalize().map_err(|_| "profil_unbekannt")?;
    let wurzel = profil.wurzel.canonicalize().map_err(|_| "profil_unbekannt")?;
    if ordner != wurzel && !ordner.starts_with(&wurzel) {
        return Err("profil_unbekannt");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::browserimport::testordner;

    fn datei(pfad: &Path, inhalt: &[u8]) {
        fs::create_dir_all(pfad.parent().unwrap()).unwrap();
        fs::write(pfad, inhalt).unwrap();
    }

    #[test]
    fn erkennt_chromium_profile_aus_local_state_und_ignoriert_gaeste() {
        let t = testordner("profile-chromium");
        let root = unter(&t.0, &["Google", "Chrome", "User Data"]);
        datei(
            &root.join("Local State"),
            br#"{"profile":{"info_cache":{
                "Default":{"name":"Person 1","last_used":"2026-10-01T10:00:00Z"},
                "Profile 1":{"name":"Arbeit","last_used":"2026-10-20T10:00:00Z"},
                "Guest Profile":{"name":"Gast"},
                "Profile 9":{"name":"Weg"}}}}"#,
        );
        datei(&root.join("Default").join("History"), b"");
        datei(&root.join("Default").join("Bookmarks"), b"{}");
        datei(&root.join("Profile 1").join("History"), b"");
        datei(&root.join("Guest Profile").join("History"), b"");

        let profile: Vec<Profil> = finde_mit(&t.0, &t.0).into_iter().filter(|p| p.info.browser == "chrome").collect();
        assert_eq!(profile.iter().map(|p| p.info.id.as_str()).collect::<Vec<_>>(), ["chrome:Profile 1", "chrome:Default"]);
        assert_eq!(profile[0].info.name, "Arbeit");
        assert!(profile[0].info.verlauf && !profile[0].info.lesezeichen);
        assert!(profile[1].info.lesezeichen);
        assert!(profile.iter().all(|p| !p.info.id.contains('\\') && !p.info.id.contains('/')));
    }

    #[test]
    fn faellt_ohne_local_state_auf_die_ordner_zurueck() {
        let t = testordner("profile-fallback");
        let root = unter(&t.0, &["Vivaldi", "User Data"]);
        datei(&root.join("Default").join("Bookmarks"), b"{}");
        datei(&root.join("Profile 2").join("History"), b"");
        datei(&root.join("Guest Profile").join("History"), b"");

        let profile: Vec<Profil> = finde_mit(&t.0, &t.0).into_iter().filter(|p| p.info.browser == "vivaldi").collect();
        assert_eq!(profile.len(), 2);
        assert_eq!(profile[0].info.id, "vivaldi:Default");
        assert_eq!(profile[1].info.id, "vivaldi:Profile 2");
    }

    #[test]
    fn ignoriert_pfadanteile_in_der_local_state() {
        let t = testordner("profile-ausbruch");
        let root = unter(&t.0, &["Google", "Chrome", "User Data"]);
        let fremd = t.0.join("Fremd");
        datei(&fremd.join("History"), b"");
        datei(&root.join("Default").join("History"), b"");
        let state = serde_json::json!({"profile":{"info_cache":{
            "../Fremd":{"name":"Fremd"},
            "Default":{"name":"Person 1"}}}});
        datei(&root.join("Local State"), serde_json::to_string(&state).unwrap().as_bytes());

        let profile = chromium(&root, "chrome");
        assert_eq!(profile.iter().map(|p| p.info.id.as_str()).collect::<Vec<_>>(), ["chrome:Default"]);
    }

    #[test]
    fn erkennt_opera_als_ein_profil_ohne_ordneranteil() {
        let t = testordner("profile-opera");
        let ordner = unter(&t.0, &["Opera Software", "Opera Stable"]);
        datei(&ordner.join("History"), b"");
        datei(&ordner.join("Bookmarks"), b"{}");

        let profile: Vec<Profil> = finde_mit(&t.0, &t.0).into_iter().filter(|p| p.info.browser == "opera").collect();
        assert_eq!(profile.len(), 1);
        assert_eq!(profile[0].info.id, "opera:");
        assert_eq!(profile[0].info.name, "Standard");
        assert!(profile[0].info.verlauf && profile[0].info.lesezeichen);
    }

    #[test]
    fn erkennt_firefox_profile_und_liest_die_namen_aus_der_ini() {
        let t = testordner("profile-firefox");
        let basis = unter(&t.0, &["Mozilla", "Firefox"]);
        datei(
            &basis.join("profiles.ini"),
            b"[Profile0]\nName=Standard\nIsRelative=1\nPath=Profiles/abc.default-release\n\n[Profile1]\nName=Alt\nPath=Profiles/xyz.default\n",
        );
        datei(&basis.join("Profiles").join("abc.default-release").join("places.sqlite"), b"");
        datei(&basis.join("Profiles").join("xyz.default").join("places.sqlite"), b"");
        datei(&basis.join("Profiles").join("leer"), b"");

        let profile = firefox(&t.0);
        assert_eq!(profile.len(), 2);
        assert_eq!(profile[0].info.id, "firefox:abc.default-release");
        assert_eq!(profile[0].info.name, "Standard");
        assert_eq!(profile[1].info.name, "Alt");
        assert!(profile.iter().all(|p| p.info.verlauf && p.info.lesezeichen));
    }

    #[test]
    fn lehnt_ordner_ausserhalb_der_wurzel_ab() {
        let t = testordner("profile-pfad");
        let wurzel = t.0.join("User Data");
        let drinnen = wurzel.join("Default");
        let draussen = t.0.join("Fremd");
        fs::create_dir_all(&drinnen).unwrap();
        fs::create_dir_all(&draussen).unwrap();

        let bauen = |ordner: &Path| Profil {
            info: BrowserProfil {
                id: "chrome:Default".into(),
                browser: "chrome".into(),
                name: "Person 1".into(),
                verlauf: true,
                lesezeichen: true,
            },
            ordner: ordner.to_path_buf(),
            wurzel: wurzel.clone(),
            art: Art::Chromium,
        };
        assert!(pruefen(&bauen(&drinnen)).is_ok());
        assert_eq!(pruefen(&bauen(&draussen)), Err("profil_unbekannt"));
        assert_eq!(pruefen(&bauen(&wurzel.join("Fehlt"))), Err("profil_unbekannt"));
    }

    #[test]
    fn lehnt_eine_verknuepfung_als_profilordner_ab() {
        use std::os::windows::fs::symlink_dir;
        let t = testordner("profile-symlink");
        let wurzel = t.0.join("User Data");
        let ziel = t.0.join("Ablage");
        fs::create_dir_all(&wurzel).unwrap();
        fs::create_dir_all(&ziel).unwrap();
        let verknuepfung = wurzel.join("Default");
        if symlink_dir(&ziel, &verknuepfung).is_err() {
            // Ohne Entwicklermodus darf der Test keine Verknüpfung anlegen.
            return;
        }
        let profil = Profil {
            info: BrowserProfil {
                id: "chrome:Default".into(),
                browser: "chrome".into(),
                name: "Person 1".into(),
                verlauf: true,
                lesezeichen: true,
            },
            ordner: verknuepfung,
            wurzel,
            art: Art::Chromium,
        };
        assert_eq!(pruefen(&profil), Err("profil_unbekannt"));
    }
}
