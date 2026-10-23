//! Daten aus installierten Browsern lesen, einmalig und still.
//!
//! Unter Windows liegen Lesezeichen, Verlauf und Symbole von Chrome, Brave,
//! Edge, Vivaldi, Opera und Firefox als Dateien auf dem Rechner. Der Import
//! liest sie, normalisiert sie und gibt sie an die Oberfläche; gespeichert
//! wird dort, wo die Daten schon liegen (`msb:verlauf`, `msb:einstellungen`,
//! `msb:bilder`). Es entsteht keine zweite Browserdatenbank.
//!
//! Gelesen wird ausschließlich eine Kopie im eigenen Datenordner
//! (`schatten.rs`): der Quell-Browser darf geöffnet bleiben und bekommt keine
//! Sperre zu spüren. Passwörter, Karten, Cookies und Formulardaten werden
//! nicht angefasst.
//!
//! `rusqlite` (gebündelt, nur Windows) ist die einzige neue Abhängigkeit. Sie
//! liest die SQLite-Dateien der Browser; unter Android gibt es diese Profile
//! nicht, deshalb kompiliert das Modul dort ohne sie und antwortet
//! `nur_windows`.
//!
//! Begründung nach `docs/agent-rules/dependencies.md`: `std` kann SQLite nicht
//! lesen, und die Browserdaten liegen ausschließlich in diesem Format.
//! `rusqlite` ist die gepflegte Bindung an die offizielle SQLite-Bibliothek;
//! die gebündelte Fassung hängt nicht von einer Installation des Rechners ab.
//! Sie berührt keine Credentials, öffnet nur Kopien, nur lesend
//! (`query_only`, `trusted_schema=OFF`) und schreibt keine Datenbank. Die API
//! ist klein und hier hinter `chromium.rs`/`firefox.rs` gekapselt; Entfernen
//! heißt: dieses Modul und die Windows-Dependency streichen. Lizenz: MIT
//! (rusqlite), SQLite gemeinfrei. Alternativen wie eine eigene SQLite-Bindung
//! oder ein JSON-Export der Browser bieten die History nicht in dieser Form.

use std::collections::HashSet;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;

#[cfg(windows)]
mod bilder;
#[cfg(windows)]
mod chromium;
#[cfg(windows)]
mod firefox;
#[cfg(windows)]
mod profile;
#[cfg(windows)]
mod schatten;

/// Mehr Verlauf übernimmt niemand sinnvoll; das Älteste fällt weg.
pub const VERLAUF_MAX: usize = 5000;
pub const LESEBZEICHEN_MAX: usize = 2000;
pub const TOP_SITES_MAX: usize = 20;
/// So viele Herkünfte bekommen ein Lesezeichen-Symbol.
pub const BILDER_MAX: usize = 128;
pub const ADRESSE_MAX: usize = 2048;
pub const TITEL_MAX: usize = 300;
pub const ORDNER_NAME_MAX: usize = 80;
pub const ORDNER_TIEFE_MAX: usize = 24;
/// Größer passt eine Symbol-Datenadresse nicht sicher in den localStorage.
pub const BILD_LAENGE_MAX: usize = 8 * 1024;
/// Schutz gegen einen Baum, der sich selbst enthält oder ins Leere wächst.
pub const KNOTEN_MAX: usize = 20_000;

pub(crate) const TAG_MS: i64 = 24 * 60 * 60 * 1000;
/// Chromium zählt Mikrosekunden seit 1601, Unix seit 1970.
const WEBKIT_OFFSET_MS: i64 = 11_644_473_600_000;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct BrowserProfil {
    pub id: String,
    pub browser: String,
    pub name: String,
    pub verlauf: bool,
    pub lesezeichen: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ImportLesezeichen {
    pub url: String,
    pub titel: String,
    pub ordner: Vec<String>,
    pub zeit: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ImportVerlauf {
    pub url: String,
    pub titel: String,
    pub zeit: i64,
    pub anzahl: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ImportTopSite {
    pub url: String,
    pub titel: String,
}

/// Ein Symbol als `data:image/png;base64,…`; `herkunft` ist der Schlüssel im
/// Bildspeicher der Oberfläche (`msb:bilder`).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ImportBild {
    pub herkunft: String,
    pub daten: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct ImportZahlen {
    pub lesezeichen: usize,
    pub verlauf: usize,
    pub top_sites: usize,
    pub bilder: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ImportWarnung {
    pub code: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ImportErgebnis {
    pub browser: String,
    pub profil: String,
    pub dauer_ms: u64,
    pub lesezeichen: Vec<ImportLesezeichen>,
    pub verlauf: Vec<ImportVerlauf>,
    pub top_sites: Vec<ImportTopSite>,
    pub bilder: Vec<ImportBild>,
    pub zahlen: ImportZahlen,
    pub warnungen: Vec<ImportWarnung>,
}

#[cfg(windows)]
#[derive(Debug, Default, PartialEq)]
pub(crate) struct Gelesen {
    pub lesezeichen: Vec<ImportLesezeichen>,
    pub verlauf: Vec<ImportVerlauf>,
    pub top_sites: Vec<ImportTopSite>,
    pub roh_bilder: Vec<RohBild>,
    pub warnungen: Vec<ImportWarnung>,
}

/// Ein Symbol aus einer Browser-Datenbank, noch nicht dekodiert. Mehrere
/// Bitmaps derselben Herkunft stehen als Kandidaten bereit; das größte zuerst.
#[cfg(windows)]
#[derive(Debug, PartialEq)]
pub(crate) struct RohBild {
    pub herkunft: String,
    pub kandidaten: Vec<Vec<u8>>,
}

/// Eine Webadresse, die gespeichert werden darf: http(s), ohne Zugangsdaten,
/// höchstens `ADRESSE_MAX` Zeichen.
pub(crate) fn web_adresse(url: &str) -> Option<String> {
    let u = url::Url::parse(url.trim()).ok()?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() {
        return None;
    }
    if !u.username().is_empty() || u.password().is_some() {
        return None;
    }
    let text = u.to_string();
    (text.len() <= ADRESSE_MAX).then_some(text)
}

/// Herkunft (Schema, Host, Port) ohne Pfad, wie sie der Bildspeicher als
/// Schlüssel benutzt.
pub(crate) fn herkunft(url: &str) -> Option<String> {
    let u = url::Url::parse(url).ok()?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() {
        return None;
    }
    if !u.username().is_empty() || u.password().is_some() {
        return None;
    }
    Some(u.origin().ascii_serialization())
}

pub(crate) fn kurzer_titel(titel: &str, url: &str) -> String {
    let titel = titel.trim();
    let titel = if titel.is_empty() { url } else { titel };
    if titel.chars().count() <= TITEL_MAX {
        return titel.to_string();
    }
    let mut kurz: String = titel.chars().take(TITEL_MAX - 1).collect();
    kurz.push('…');
    kurz
}

/// Ein Ordnername, wie er gespeichert wird; `None` für einen leeren.
pub(crate) fn ordner_name(name: &str) -> Option<String> {
    let sauber: String = name.chars().filter(|c| !c.is_control()).collect();
    let sauber = sauber.trim();
    if sauber.is_empty() {
        return None;
    }
    Some(sauber.chars().take(ORDNER_NAME_MAX).collect())
}

fn jetzt_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
}

fn klemme(ms: i64) -> Option<i64> {
    if ms <= 0 {
        return None;
    }
    let jetzt = jetzt_ms();
    Some(if ms > jetzt + TAG_MS { jetzt } else { ms })
}

/// Chromium: Mikrosekunden seit 1601.
pub(crate) fn zeit_webkit(us: i64) -> Option<i64> {
    if us <= 0 {
        return None;
    }
    klemme(us / 1000 - WEBKIT_OFFSET_MS)
}

/// Firefox: Mikrosekunden seit 1970.
pub(crate) fn zeit_firefox(us: i64) -> Option<i64> {
    if us <= 0 {
        return None;
    }
    klemme(us / 1000)
}

pub(crate) fn warnung(warnungen: &mut Vec<ImportWarnung>, code: &str) {
    if !warnungen.iter().any(|w| w.code == code) {
        warnungen.push(ImportWarnung { code: code.to_string() });
    }
}

/// Herkünfte, für die ein Symbol gelesen wird: Lesezeichen zuerst, dann
/// Top-Sites, jede einmal.
pub(crate) fn gewuenschte_herkuenfte(
    lesezeichen: &[ImportLesezeichen],
    top_sites: &[ImportTopSite],
    warnungen: &mut Vec<ImportWarnung>,
) -> Vec<String> {
    let mut aus = Vec::new();
    let mut gesehen = HashSet::new();
    for url in lesezeichen.iter().map(|l| &l.url).chain(top_sites.iter().map(|t| &t.url)) {
        let Some(h) = herkunft(url) else { continue };
        if !gesehen.insert(h.clone()) {
            continue;
        }
        if aus.len() >= BILDER_MAX {
            warnung(warnungen, "bilder_gekuerzt");
            break;
        }
        aus.push(h);
    }
    aus
}

#[cfg(windows)]
pub(crate) fn hat_tabelle(conn: &rusqlite::Connection, name: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
        [name],
        |_| Ok(()),
    )
    .is_ok()
}

/// Die Spalten einer Tabelle, klein geschrieben. Tabellennamen kommen nur aus
/// der festen Liste der Leser, nie aus einer Datei.
#[cfg(windows)]
pub(crate) fn spalten(conn: &rusqlite::Connection, tabelle: &str) -> HashSet<String> {
    let mut aus = HashSet::new();
    let Ok(mut stmt) = conn.prepare(&format!("PRAGMA table_info({tabelle})")) else { return aus };
    if let Ok(zeilen) = stmt.query_map([], |r| r.get::<_, String>(1)) {
        for name in zeilen.flatten() {
            aus.insert(name.to_ascii_lowercase());
        }
    }
    aus
}

#[cfg(windows)]
pub(crate) fn wert_text(wert: rusqlite::types::ValueRef<'_>) -> String {
    match wert {
        rusqlite::types::ValueRef::Text(t) => String::from_utf8_lossy(t).into_owned(),
        rusqlite::types::ValueRef::Integer(v) => v.to_string(),
        rusqlite::types::ValueRef::Real(v) => v.to_string(),
        _ => String::new(),
    }
}

#[cfg(windows)]
pub(crate) fn wert_zahl(wert: rusqlite::types::ValueRef<'_>) -> i64 {
    match wert {
        rusqlite::types::ValueRef::Integer(v) => v,
        rusqlite::types::ValueRef::Real(v) => v as i64,
        rusqlite::types::ValueRef::Text(t) => std::str::from_utf8(t).ok().and_then(|s| s.parse().ok()).unwrap_or(0),
        _ => 0,
    }
}

/// Entfernt beim Start Kopien, die ein Absturz liegen ließ.
pub fn beim_start(app: &tauri::AppHandle) {
    #[cfg(windows)]
    schatten::raeume_auf(app);
    #[cfg(not(windows))]
    let _ = app;
}

#[tauri::command(async)]
pub fn detect_installed_browsers() -> Result<Vec<BrowserProfil>, String> {
    #[cfg(windows)]
    {
        Ok(profile::finde().into_iter().map(|p| p.info).collect())
    }
    #[cfg(not(windows))]
    {
        Err("nur_windows".into())
    }
}

#[tauri::command]
pub async fn import_browser_data(app: tauri::AppHandle, profil_id: String) -> Result<ImportErgebnis, String> {
    #[cfg(not(windows))]
    {
        let _ = (app, profil_id);
        Err("nur_windows".into())
    }
    #[cfg(windows)]
    {
        tauri::async_runtime::spawn_blocking(move || importieren(&app, &profil_id))
            .await
            .map_err(|_| "abgebrochen".to_string())?
    }
}

#[cfg(windows)]
fn importieren(app: &tauri::AppHandle, profil_id: &str) -> Result<ImportErgebnis, String> {
    let beginn = std::time::Instant::now();
    let profil = profile::finde()
        .into_iter()
        .find(|p| p.info.id == profil_id)
        .ok_or_else(|| "profil_unbekannt".to_string())?;
    profile::pruefen(&profil).map_err(str::to_string)?;

    let schatten = schatten::Schatten::neu(app).map_err(str::to_string)?;
    let mut gelesen = match profil.art {
        profile::Art::Chromium | profile::Art::Opera => {
            let mut vorhanden = false;
            for (name, max) in [
                ("History", 512 * 1024 * 1024),
                ("Bookmarks", 64 * 1024 * 1024),
                ("Top Sites", 256 * 1024 * 1024),
                ("Favicons", 256 * 1024 * 1024),
            ] {
                vorhanden |= schatten.kopiere(&profil.ordner.join(name), &profil.ordner, name, max).map_err(str::to_string)?;
            }
            if !vorhanden {
                return Err("profil_leer".into());
            }
            chromium::lesen(&schatten)
        }
        profile::Art::Firefox => {
            let mut vorhanden = false;
            for (name, max) in [("places.sqlite", 512 * 1024 * 1024), ("favicons.sqlite", 256 * 1024 * 1024)] {
                vorhanden |= schatten.kopiere(&profil.ordner.join(name), &profil.ordner, name, max).map_err(str::to_string)?;
            }
            if !vorhanden {
                return Err("profil_leer".into());
            }
            firefox::lesen(&schatten).map_err(str::to_string)?
        }
    };

    let kacheln: HashSet<String> = gelesen.top_sites.iter().filter_map(|t| herkunft(&t.url)).collect();
    let (bilder, bild_warnungen) = bilder::aufbereiten(app, &kacheln, &gelesen.roh_bilder);
    gelesen.warnungen.extend(bild_warnungen);
    gelesen.warnungen.sort_by(|a, b| a.code.cmp(&b.code));
    gelesen.warnungen.dedup_by(|a, b| a.code == b.code);

    let zahlen = ImportZahlen {
        lesezeichen: gelesen.lesezeichen.len(),
        verlauf: gelesen.verlauf.len(),
        top_sites: gelesen.top_sites.len(),
        bilder: bilder.len(),
    };

    Ok(ImportErgebnis {
        browser: profil.info.browser,
        profil: profil.info.name,
        dauer_ms: beginn.elapsed().as_millis() as u64,
        lesezeichen: gelesen.lesezeichen,
        verlauf: gelesen.verlauf,
        top_sites: gelesen.top_sites,
        bilder,
        zahlen,
        warnungen: gelesen.warnungen,
    })
}

#[cfg(test)]
pub(crate) struct Testordner(pub std::path::PathBuf);

#[cfg(test)]
impl Testordner {
    pub(crate) fn neu(name: &str) -> Self {
        use std::sync::atomic::{AtomicU64, Ordering};
        static NR: AtomicU64 = AtomicU64::new(0);
        let pfad = std::env::temp_dir().join(format!(
            "msb-import-test-{}-{name}-{}",
            std::process::id(),
            NR.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&pfad);
        std::fs::create_dir_all(&pfad).unwrap();
        Self(pfad)
    }
}

#[cfg(test)]
impl Drop for Testordner {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[cfg(test)]
pub(crate) fn testordner(name: &str) -> Testordner {
    Testordner::neu(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nimmt_nur_webadressen_ohne_zugangsdaten() {
        assert_eq!(web_adresse(" https://de.wikipedia.org/wiki/A ").as_deref(), Some("https://de.wikipedia.org/wiki/A"));
        assert_eq!(web_adresse("http://example.org/").as_deref(), Some("http://example.org/"));
        for schlecht in [
            "javascript:alert(1)",
            "file:///C:/Users/a.html",
            "data:text/html,x",
            "msb://einstellungen",
            "https://benutzer:geheim@example.org/",
            "",
        ] {
            assert_eq!(web_adresse(schlecht), None, "{schlecht}");
        }
        assert_eq!(web_adresse(&format!("https://example.org/{}", "x".repeat(ADRESSE_MAX))), None);
    }

    #[test]
    fn die_herkunft_ist_ohne_pfad_und_zugangsdaten() {
        assert_eq!(herkunft("https://Example.org:8443/pfad?q=1").as_deref(), Some("https://example.org:8443"));
        assert_eq!(herkunft("http://user:pw@example.org/"), None);
        assert_eq!(herkunft("chrome://settings"), None);
    }

    #[test]
    fn kuerzt_titel_und_ordnernamen_an_zeichen() {
        assert_eq!(kurzer_titel("  ", "https://a.example/"), "https://a.example/");
        assert_eq!(kurzer_titel("ä".repeat(TITEL_MAX + 5).as_str(), "x"), format!("{}…", "ä".repeat(TITEL_MAX - 1)));
        assert_eq!(ordner_name("  Dev\nRust  ").as_deref(), Some("DevRust"));
        assert_eq!(ordner_name(" \u{0} "), None);
        assert_eq!(ordner_name(&"x".repeat(ORDNER_NAME_MAX + 10)).unwrap().chars().count(), ORDNER_NAME_MAX);
    }

    #[test]
    fn rechnet_die_zeiten_der_browser_um() {
        assert_eq!(zeit_webkit(WEBKIT_OFFSET_MS * 1000), None, "Unix-Anfang ist kein Besuch");
        assert_eq!(zeit_webkit(WEBKIT_OFFSET_MS * 1000 + 1_000_000), Some(1000));
        assert_eq!(zeit_firefox(1_000_000), Some(1000));
        assert_eq!(zeit_firefox(0), None);
        assert_eq!(zeit_webkit(-1), None);
        assert_eq!(zeit_firefox(-1), None);
        let zukunft = jetzt_ms() + 2 * TAG_MS;
        assert!(zeit_firefox(zukunft * 1000).unwrap() <= jetzt_ms() + 1000);
    }
}
