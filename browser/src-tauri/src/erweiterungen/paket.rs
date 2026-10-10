//! Den Inhalt einer Erweiterung ablegen und lesen: ZIP aus dem CRX entpacken
//! oder einen entpackten Ordner kopieren, den Schlüssel ins Manifest setzen,
//! Name, Rechte und Symbol für die Oberfläche lesen.
//!
//! Alles hier kommt von außen. Pfade werden Teil für Teil geprüft, Größe und
//! Zahl der Dateien sind begrenzt (Punkt 78), Texte gekürzt (Punkt 135).

use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::downloads::{ist_unsichtbar, sicherer_name};

pub const DATEIEN_HOECHSTENS: usize = 10_000;
pub const BYTES_HOECHSTENS: u64 = 512 * 1024 * 1024;
const SYMBOL_HOECHSTENS: u64 = 256 * 1024;
const JSON_HOECHSTENS: u64 = 1024 * 1024;
const LISTE_HOECHSTENS: usize = 100;

/// Ein Teil eines Pfads ist nur gültig, wenn er schon so heißt, wie ein
/// sicherer Dateiname hieße: kein `..`, kein `:`, keine Gerätenamen, keine
/// unsichtbaren Zeichen.
fn teil_ok(teil: &str) -> bool {
    !teil.is_empty() && teil != "." && sicherer_name(teil) == teil
}

/// Ein relativer Pfad (`icons/48.png`) im Ordner.
fn im_ordner(ordner: &Path, pfad: &str) -> Option<PathBuf> {
    let teile: Vec<&str> = pfad.split('/').collect();
    teile.iter().all(|t| teil_ok(t)).then(|| teile.iter().fold(ordner.to_path_buf(), |p, t| p.join(t)))
}

struct Zaehler {
    dateien: usize,
    bytes: u64,
}

impl Zaehler {
    fn datei(&mut self) -> Result<(), String> {
        self.dateien += 1;
        if self.dateien > DATEIEN_HOECHSTENS {
            return Err("zu_viele_dateien".into());
        }
        Ok(())
    }

    /// Schreibt höchstens bis zur Grenze; was darüber geht, ist ein Fehler.
    fn schreiben(&mut self, quelle: &mut impl Read, ziel: &Path) -> Result<(), String> {
        let mut datei = fs::OpenOptions::new().write(true).create_new(true).open(ziel).map_err(|_| "paket_ungueltig".to_string())?;
        let rest = BYTES_HOECHSTENS - self.bytes;
        let geschrieben = io::copy(&mut quelle.take(rest + 1), &mut datei).map_err(|_| "paket_ungueltig".to_string())?;
        if geschrieben > rest {
            return Err("zu_gross".into());
        }
        self.bytes += geschrieben;
        datei.flush().map_err(|e| e.to_string())
    }
}

/// Entpackt `zip` nach `ziel`, das es noch nicht geben darf. Bricht es ab,
/// bleibt `ziel` halb; der Aufrufer schreibt deshalb neben das endgültige Ziel
/// und löscht bei einem Fehler.
pub fn entpacken(zip: &[u8], ziel: &Path) -> Result<(), String> {
    let mut archiv = zip::ZipArchive::new(io::Cursor::new(zip)).map_err(|_| "paket_ungueltig".to_string())?;
    if archiv.len() > DATEIEN_HOECHSTENS {
        return Err("zu_viele_dateien".into());
    }
    fs::create_dir(ziel).map_err(|e| e.to_string())?;
    let mut zaehler = Zaehler { dateien: 0, bytes: 0 };
    for nr in 0..archiv.len() {
        let mut eintrag = archiv.by_index(nr).map_err(|_| "paket_ungueltig".to_string())?;
        if eintrag.is_symlink() {
            return Err("paket_ungueltig".into());
        }
        let name = eintrag.name().trim_end_matches('/').to_string();
        let pfad = im_ordner(ziel, &name).ok_or("paket_ungueltig")?;
        if eintrag.is_dir() {
            fs::create_dir_all(&pfad).map_err(|e| e.to_string())?;
            continue;
        }
        zaehler.datei()?;
        if let Some(eltern) = pfad.parent() {
            fs::create_dir_all(eltern).map_err(|e| e.to_string())?;
        }
        zaehler.schreiben(&mut eintrag, &pfad)?;
    }
    Ok(())
}

/// Kopiert einen entpackten Ordner (Entwicklermodus) nach `ziel`. Verknüpfungen
/// werden nicht verfolgt: sie könnten auf Dateien außerhalb zeigen.
pub fn kopieren(quelle: &Path, ziel: &Path) -> Result<(), String> {
    fn rein(quelle: &Path, ziel: &Path, zaehler: &mut Zaehler) -> Result<(), String> {
        fs::create_dir(ziel).map_err(|e| e.to_string())?;
        for eintrag in fs::read_dir(quelle).map_err(|e| e.to_string())? {
            let eintrag = eintrag.map_err(|e| e.to_string())?;
            let name = eintrag.file_name().into_string().map_err(|_| "paket_ungueltig".to_string())?;
            if !teil_ok(&name) {
                return Err("paket_ungueltig".into());
            }
            let art = fs::symlink_metadata(eintrag.path()).map_err(|e| e.to_string())?;
            if art.is_symlink() || (!art.is_dir() && !art.is_file()) {
                return Err("verknuepfung".into());
            }
            if art.is_dir() {
                rein(&eintrag.path(), &ziel.join(&name), zaehler)?;
            } else {
                zaehler.datei()?;
                let mut datei = fs::File::open(eintrag.path()).map_err(|e| e.to_string())?;
                zaehler.schreiben(&mut datei, &ziel.join(&name))?;
            }
        }
        Ok(())
    }
    rein(quelle, ziel, &mut Zaehler { dateien: 0, bytes: 0 })
}

fn json_lesen(pfad: &Path) -> Option<Value> {
    let mut text = String::new();
    fs::File::open(pfad).ok()?.take(JSON_HOECHSTENS).read_to_string(&mut text).ok()?;
    serde_json::from_str(text.trim_start_matches('\u{feff}')).ok()
}

fn manifest(ordner: &Path) -> Result<serde_json::Map<String, Value>, String> {
    match json_lesen(&ordner.join("manifest.json")) {
        Some(Value::Object(m)) => Ok(m),
        _ => Err("manifest_fehlt".into()),
    }
}

/// Setzt den geprüften Entwicklerschlüssel als `key`, damit WebView2 der
/// Erweiterung dieselbe Kennung gibt wie Chrome. Ein `key`, den das Paket
/// selbst mitbringt, zählt nicht.
pub fn schluessel_setzen(ordner: &Path, schluessel: &[u8]) -> Result<(), String> {
    let mut m = manifest(ordner)?;
    m.insert("key".into(), base64::engine::general_purpose::STANDARD.encode(schluessel).into());
    crate::datei::ersetzen(&ordner.join("manifest.json"), serde_json::to_vec_pretty(&m).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())
}

/// Was die Oberfläche über eine Erweiterung zeigt.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Angaben {
    pub name: String,
    pub version: String,
    pub beschreibung: String,
    /// `permissions` (ohne Adressmuster).
    pub rechte: Vec<String>,
    /// Adressmuster aus `host_permissions`, `permissions` und Content Scripts.
    pub seiten: Vec<String>,
    /// Was sie später zusätzlich erfragen kann (`optional_*`).
    pub optional: Vec<String>,
    /// Data-URL.
    pub symbol: Option<String>,
    pub popup: Option<String>,
    pub optionen: Option<String>,
}

fn sauber(text: &str, laenge: usize) -> String {
    let text: String = text.chars().filter(|z| !z.is_control() && !ist_unsichtbar(*z)).take(laenge).collect();
    text.trim().to_string()
}

/// `__MSG_name__` aus `_locales/<sprache>/messages.json`, sonst aus der
/// Standardsprache des Pakets.
fn uebersetzt(ordner: &Path, m: &serde_json::Map<String, Value>, wert: &str, sprache: &str) -> String {
    let Some(schluessel) = wert.strip_prefix("__MSG_").and_then(|s| s.strip_suffix("__")) else {
        return wert.to_string();
    };
    let standard = m.get("default_locale").and_then(Value::as_str).unwrap_or("en");
    for ort in [sprache, standard] {
        let Some(datei) = im_ordner(ordner, &format!("_locales/{ort}/messages.json")) else { continue };
        if let Some(Value::Object(texte)) = json_lesen(&datei) {
            let treffer = texte.iter().find(|(k, _)| k.eq_ignore_ascii_case(schluessel));
            if let Some(text) = treffer.and_then(|(_, v)| v.get("message")).and_then(Value::as_str) {
                return text.to_string();
            }
        }
    }
    wert.to_string()
}

fn texte(wert: Option<&Value>) -> Vec<String> {
    wert.and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(|t| sauber(t, 200)).filter(|t| !t.is_empty()).collect())
        .unwrap_or_default()
}

fn ist_adressmuster(t: &str) -> bool {
    t == "<all_urls>" || t.contains("://")
}

fn dazu(liste: &mut Vec<String>, neu: impl IntoIterator<Item = String>) {
    for t in neu {
        if liste.len() < LISTE_HOECHSTENS && !liste.contains(&t) {
            liste.push(t);
        }
    }
}

fn seite_im_paket(ordner: &Path, wert: Option<&Value>) -> Option<String> {
    let pfad = wert?.as_str()?.split(['?', '#']).next()?.trim_start_matches('/');
    im_ordner(ordner, pfad)?.is_file().then(|| pfad.to_string())
}

fn symbol(ordner: &Path, m: &serde_json::Map<String, Value>) -> Option<String> {
    let groessen = m.get("icons").and_then(Value::as_object)?;
    let mut alle: Vec<(u32, &str)> = groessen.iter().filter_map(|(g, p)| Some((g.parse().ok()?, p.as_str()?))).collect();
    // Das größte bis 128 px, sonst das kleinste.
    alle.sort_by_key(|(g, _)| (*g > 128, if *g <= 128 { u32::MAX - g } else { *g }));
    let (_, pfad) = alle.first()?;
    let mut bytes = Vec::new();
    fs::File::open(im_ordner(ordner, pfad.trim_start_matches('/'))?).ok()?.take(SYMBOL_HOECHSTENS + 1).read_to_end(&mut bytes).ok()?;
    let art = if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        "png"
    } else if bytes.starts_with(b"\xff\xd8\xff") {
        "jpeg"
    } else if bytes.len() > 12 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        "webp"
    } else {
        return None;
    };
    (bytes.len() as u64 <= SYMBOL_HOECHSTENS)
        .then(|| format!("data:image/{art};base64,{}", base64::engine::general_purpose::STANDARD.encode(&bytes)))
}

/// Liest die Angaben aus dem Manifest. `sprache` ist die App-Sprache (`de`).
pub fn angaben(ordner: &Path, sprache: &str) -> Result<Angaben, String> {
    let m = manifest(ordner)?;
    let text = |feld: &str, laenge| {
        let wert = m.get(feld).and_then(Value::as_str).unwrap_or_default();
        sauber(&uebersetzt(ordner, &m, wert, sprache), laenge)
    };
    let name = text("name", 80);
    if name.is_empty() {
        return Err("manifest_fehlt".into());
    }
    let alle = texte(m.get("permissions"));
    let mut seiten = Vec::new();
    dazu(&mut seiten, alle.iter().filter(|t| ist_adressmuster(t)).cloned());
    dazu(&mut seiten, texte(m.get("host_permissions")));
    if let Some(skripte) = m.get("content_scripts").and_then(Value::as_array) {
        for skript in skripte {
            dazu(&mut seiten, texte(skript.get("matches")));
        }
    }
    let mut rechte = Vec::new();
    dazu(&mut rechte, alle.into_iter().filter(|t| !ist_adressmuster(t)));
    let mut optional = Vec::new();
    dazu(&mut optional, texte(m.get("optional_permissions")));
    dazu(&mut optional, texte(m.get("optional_host_permissions")));
    let aktion = m.get("action").or_else(|| m.get("browser_action"));
    let optionen = m.get("options_ui").and_then(|o| o.get("page")).or_else(|| m.get("options_page"));
    Ok(Angaben {
        version: text("version", 40),
        beschreibung: text("description", 300),
        name,
        rechte,
        seiten,
        optional,
        symbol: symbol(ordner, &m),
        popup: seite_im_paket(ordner, aktion.and_then(|a| a.get("default_popup"))),
        optionen: seite_im_paket(ordner, optionen),
    })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) fn ordner(name: &str) -> PathBuf {
        let o = std::env::temp_dir().join(format!("msb-erw-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&o);
        o
    }

    pub(crate) fn zip_aus(dateien: &[(&str, &[u8])]) -> Vec<u8> {
        let mut aus = io::Cursor::new(Vec::new());
        let mut z = zip::ZipWriter::new(&mut aus);
        for (name, inhalt) in dateien {
            z.start_file(*name, zip::write::SimpleFileOptions::default()).unwrap();
            z.write_all(inhalt).unwrap();
        }
        z.finish().unwrap();
        aus.into_inner()
    }

    const MANIFEST: &[u8] = br#"{
        "manifest_version": 3, "name": "__MSG_appName__", "version": "1.2.3", "default_locale": "en",
        "description": "Blockt\u202e Werbung", "permissions": ["storage", "tabs", "https://a.example/*"],
        "host_permissions": ["<all_urls>"], "optional_permissions": ["downloads"],
        "content_scripts": [{ "matches": ["https://b.example/*", "<all_urls>"], "js": ["c.js"] }],
        "icons": { "16": "i/16.png", "48": "i/48.png", "256": "i/256.png" },
        "action": { "default_popup": "popup.html" }, "options_ui": { "page": "../../aussen.html" }
    }"#;

    #[test]
    fn entpackt_und_liest_name_rechte_und_symbol() {
        let o = ordner("lesen");
        let zip = zip_aus(&[
            ("manifest.json", MANIFEST),
            ("_locales/de/messages.json", br#"{ "APPNAME": { "message": "Werbeblocker" } }"#),
            ("_locales/en/messages.json", br#"{ "appName": { "message": "Ad blocker" } }"#),
            ("i/48.png", b"\x89PNG\r\n\x1a\nmittel"),
            ("i/16.png", b"\x89PNG\r\n\x1a\nklein"),
            ("popup.html", b"<p>"),
        ]);
        entpacken(&zip, &o).unwrap();
        let a = angaben(&o, "de").unwrap();
        assert_eq!(a.name, "Werbeblocker");
        assert_eq!(angaben(&o, "fr").unwrap().name, "Ad blocker");
        assert_eq!(a.beschreibung, "Blockt Werbung");
        assert_eq!(a.rechte, ["storage", "tabs"]);
        assert_eq!(a.seiten, ["https://a.example/*", "<all_urls>", "https://b.example/*"]);
        assert_eq!(a.optional, ["downloads"]);
        assert!(a.symbol.unwrap().ends_with(&base64::engine::general_purpose::STANDARD.encode(b"\x89PNG\r\n\x1a\nmittel")));
        assert_eq!(a.popup.as_deref(), Some("popup.html"));
        // Eine Seite außerhalb des Pakets gibt es nicht.
        assert_eq!(a.optionen, None);
        fs::remove_dir_all(&o).unwrap();
    }

    #[test]
    fn pfade_aus_dem_paket_bleiben_im_ordner() {
        for boese in ["../x.js", "a/../../x.js", "/etc/x", "C:/x.js", "a\\..\\..\\x.js", "con.js", "a/x:stream", "x\u{202e}sj.exe"] {
            let o = ordner("pfad");
            let zip = zip_aus(&[("manifest.json", b"{}"), (boese, b"x")]);
            assert!(entpacken(&zip, &o).is_err(), "{boese}");
            assert!(!o.parent().unwrap().join("x.js").exists());
            let _ = fs::remove_dir_all(&o);
        }
    }

    #[test]
    fn zu_viele_dateien_werden_abgelehnt() {
        let namen: Vec<String> = (0..=DATEIEN_HOECHSTENS).map(|n| format!("{n}.js")).collect();
        let dateien: Vec<(&str, &[u8])> = namen.iter().map(|n| (n.as_str(), &b""[..])).collect();
        let o = ordner("viele");
        assert_eq!(entpacken(&zip_aus(&dateien), &o).unwrap_err(), "zu_viele_dateien");
        let _ = fs::remove_dir_all(&o);
    }

    #[test]
    fn eine_datei_ueber_der_grenze_wird_nicht_ganz_geschrieben() {
        let o = ordner("gross");
        fs::create_dir_all(&o).unwrap();
        let mut z = Zaehler { dateien: 0, bytes: BYTES_HOECHSTENS - 10 };
        assert_eq!(z.schreiben(&mut io::repeat(0).take(11), &o.join("a")).unwrap_err(), "zu_gross");
        assert_eq!(fs::metadata(o.join("a")).unwrap().len(), 11);
        let mut z = Zaehler { dateien: 0, bytes: BYTES_HOECHSTENS - 10 };
        z.schreiben(&mut io::repeat(0).take(10), &o.join("b")).unwrap();
        fs::remove_dir_all(&o).unwrap();
    }

    #[test]
    fn der_schluessel_des_pakets_ersetzt_einen_mitgebrachten() {
        let o = ordner("key");
        entpacken(&zip_aus(&[("manifest.json", b"\xef\xbb\xbf{ \"name\": \"X\", \"key\": \"fremd\" }")]), &o).unwrap();
        schluessel_setzen(&o, b"\x30\x01").unwrap();
        let m = json_lesen(&o.join("manifest.json")).unwrap();
        assert_eq!(m["key"], "MAE=");
        assert_eq!(m["name"], "X");
        fs::remove_dir_all(&o).unwrap();
    }

    #[test]
    fn kopiert_einen_ordner_ohne_verknuepfungen() {
        let (quelle, ziel) = (ordner("quelle"), ordner("ziel"));
        fs::create_dir_all(quelle.join("js")).unwrap();
        fs::write(quelle.join("manifest.json"), br#"{ "name": "Eigen", "version": "0.1" }"#).unwrap();
        fs::write(quelle.join("js/a.js"), b"1").unwrap();
        kopieren(&quelle, &ziel).unwrap();
        assert_eq!(fs::read(ziel.join("js/a.js")).unwrap(), b"1");
        assert_eq!(angaben(&ziel, "de").unwrap().name, "Eigen");
        fs::remove_dir_all(&ziel).unwrap();
        fs::remove_dir_all(&quelle).unwrap();
    }
}
