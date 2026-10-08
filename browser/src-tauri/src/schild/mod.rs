//! Das Schild: Werbe- und Trackerfilter mit der Engine von Brave (`adblock`).
//!
//! Zwei Engines, damit der Browser sagen kann, was er geblockt hat: eine aus
//! den Werbelisten (EasyList, EasyList Germany), eine aus der Trackerliste
//! (EasyPrivacy). Jede Anfrage eines Tabs geht zuerst durch die Trackerliste,
//! dann durch die Werbeliste.
//!
//! Die Listen lädt `listen.rs` im Hintergrund nach. Bis sie da sind, gilt eine
//! kleine eingebaute Liste: auch ein frisch installierter Browser ohne Netz
//! filtert die großen Netzwerke.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{LazyLock, Mutex, RwLock};

use adblock::request::Request;
use adblock::Engine;
use serde::Serialize;
use tauri::AppHandle;

use crate::konfig::Konfig;
use crate::tabs::{melden, TabEreignis};

pub mod listen;

/// Art einer Anfrage, wie die WebView sie meldet.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum AnfrageArt {
    /// Ein Dokument, das nicht die Seite selbst ist: ein Iframe.
    Dokument,
    Stylesheet,
    Bild,
    Medien,
    Schrift,
    Skript,
    Xhr,
    Websocket,
    Ping,
    Sonstiges,
}

impl AnfrageArt {
    /// Die Bezeichnung, die die Filterlisten verwenden (`$script`, `$subdocument`, …).
    fn als_filtertyp(self) -> &'static str {
        match self {
            AnfrageArt::Dokument => "sub_frame",
            AnfrageArt::Stylesheet => "stylesheet",
            AnfrageArt::Bild => "image",
            AnfrageArt::Medien => "media",
            AnfrageArt::Schrift => "font",
            AnfrageArt::Skript => "script",
            AnfrageArt::Xhr => "xmlhttprequest",
            AnfrageArt::Websocket => "websocket",
            AnfrageArt::Ping => "ping",
            AnfrageArt::Sonstiges => "other",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Treffer {
    Werbung,
    Tracker,
}

pub struct Schild {
    werbung: RwLock<Engine>,
    tracker: RwLock<Engine>,
    aktiv: AtomicBool,
    ausnahmen: RwLock<HashSet<String>>,
    /// Geblockte Anfragen je Tab seit dem letzten Seitenwechsel.
    zaehler: Mutex<HashMap<String, (u32, u32)>>,
}

static SCHILD: LazyLock<Schild> = LazyLock::new(|| Schild {
    werbung: RwLock::new(listen::engine_aus(&[listen::EINGEBAUT_WERBUNG])),
    tracker: RwLock::new(listen::engine_aus(&[listen::EINGEBAUT_TRACKER])),
    aktiv: AtomicBool::new(true),
    ausnahmen: RwLock::new(HashSet::new()),
    zaehler: Mutex::new(HashMap::new()),
});

/// Übernimmt Schalter und Ausnahmen aus der Konfiguration.
pub fn konfig_uebernehmen(konfig: &Konfig) {
    SCHILD.aktiv.store(konfig.schild_aktiv, Ordering::Relaxed);
    *SCHILD.ausnahmen.write().unwrap() = konfig.schild_ausnahmen.iter().cloned().collect();
}

/// Ersetzt eine Engine, sobald die Listen geladen sind.
pub(crate) fn engine_setzen(art: Treffer, engine: Engine) {
    match art {
        Treffer::Werbung => *SCHILD.werbung.write().unwrap() = engine,
        Treffer::Tracker => *SCHILD.tracker.write().unwrap() = engine,
    }
}

fn host(url: &str) -> Option<String> {
    url::Url::parse(url).ok()?.host_str().map(|h| h.trim_start_matches("www.").to_lowercase())
}

/// Ist das Schild für diese Seite an? Aus heißt: global aus oder die Seite
/// (bzw. eine ihrer Elterndomains) steht in den Ausnahmen.
pub fn aktiv_fuer(seite: &str) -> bool {
    if !SCHILD.aktiv.load(Ordering::Relaxed) {
        return false;
    }
    let Some(h) = host(seite) else { return true };
    let ausnahmen = SCHILD.ausnahmen.read().unwrap();
    let mut rest = h.as_str();
    loop {
        if ausnahmen.contains(rest) {
            return false;
        }
        match rest.split_once('.') {
            Some((_, eltern)) if eltern.contains('.') => rest = eltern,
            _ => return true,
        }
    }
}

/// Reine Entscheidung ohne Zählen: Werbung, Tracker oder durchlassen.
pub fn einstufen(url: &str, seite: &str, art: AnfrageArt) -> Option<Treffer> {
    if !(url.starts_with("http://") || url.starts_with("https://") || url.starts_with("ws")) {
        return None;
    }
    if !aktiv_fuer(seite) {
        return None;
    }
    let anfrage = Request::new(url, seite, art.als_filtertyp(), "GET").ok()?;
    if SCHILD.tracker.read().unwrap().check_network_request(&anfrage).should_block() {
        return Some(Treffer::Tracker);
    }
    if SCHILD.werbung.read().unwrap().check_network_request(&anfrage).should_block() {
        return Some(Treffer::Werbung);
    }
    None
}

/// Prüft eine Anfrage eines Tabs. `true` heißt blocken; der Zähler des Tabs
/// steigt, und die Oberfläche erfährt den neuen Stand.
pub fn pruefen(app: &AppHandle, tab: &str, url: &str, seite: &str, art: AnfrageArt) -> bool {
    let Some(treffer) = einstufen(url, seite, art) else { return false };
    let (werbung, tracker) = {
        let mut zaehler = SCHILD.zaehler.lock().unwrap();
        let eintrag = zaehler.entry(tab.to_string()).or_default();
        match treffer {
            Treffer::Werbung => eintrag.0 += 1,
            Treffer::Tracker => eintrag.1 += 1,
        }
        *eintrag
    };
    melden(app, TabEreignis::Schild { id: tab.to_string(), werbung, tracker });
    true
}

/// Neue Seite im Tab: der Zähler beginnt von vorn.
pub fn seitenwechsel(app: &AppHandle, tab: &str) {
    let vorher = SCHILD.zaehler.lock().unwrap().remove(tab);
    if vorher.is_some() {
        melden(app, TabEreignis::Schild { id: tab.to_string(), werbung: 0, tracker: 0 });
    }
}

pub fn tab_vergessen(_app: &AppHandle, tab: &str) {
    SCHILD.zaehler.lock().unwrap().remove(tab);
}

/// Was nach dem Laden einer Seite ausgeblendet wird.
pub struct Kosmetik {
    pub css: String,
    /// Die Seite schaltet die allgemeinen Regeln ab (`$generichide`).
    pub generichide: bool,
    pub ausnahmen: HashSet<String>,
}

fn verstecken(selektoren: impl IntoIterator<Item = String>) -> String {
    // Je Regel ein eigener Block: ein ungültiger Selektor würde sonst den
    // ganzen Block ungültig machen.
    selektoren
        .into_iter()
        .map(|s| format!("{s}{{display:none!important}}"))
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn kosmetik(url: &str) -> Option<Kosmetik> {
    if !aktiv_fuer(url) || !url.starts_with("http") {
        return None;
    }
    let mut css = Vec::new();
    let mut generichide = false;
    let mut ausnahmen = HashSet::new();
    for engine in [&SCHILD.werbung, &SCHILD.tracker] {
        let r = engine.read().unwrap().url_cosmetic_resources(url);
        css.extend(r.hide_selectors);
        generichide |= r.generichide;
        ausnahmen.extend(r.exceptions);
    }
    Some(Kosmetik { css: verstecken(css), generichide, ausnahmen })
}

#[derive(serde::Deserialize)]
struct KlassenUndIds {
    #[serde(default)]
    k: Vec<String>,
    #[serde(default)]
    i: Vec<String>,
}

/// Allgemeine Regeln (`##.werbung`) für die Klassen und IDs, die die Seite
/// tatsächlich verwendet. `json` kommt aus der Seite und wird entsprechend
/// knapp behandelt.
pub fn allgemeine_kosmetik(url: &str, json: &str, ausnahmen: &HashSet<String>) -> Option<String> {
    if !aktiv_fuer(url) || json.len() > 512 * 1024 {
        return None;
    }
    let gefunden: KlassenUndIds = serde_json::from_str(json).ok()?;
    let klassen: Vec<String> = gefunden.k.into_iter().take(4000).filter(|s| s.len() < 200).collect();
    let ids: Vec<String> = gefunden.i.into_iter().take(4000).filter(|s| s.len() < 200).collect();
    let mut selektoren = Vec::new();
    for engine in [&SCHILD.werbung, &SCHILD.tracker] {
        selektoren.extend(engine.read().unwrap().hidden_class_id_selectors(&klassen, &ids, ausnahmen));
    }
    (!selektoren.is_empty()).then(|| verstecken(selektoren))
}

#[derive(Serialize)]
pub struct SchildStand {
    pub aktiv: bool,
    pub listen: Vec<listen::ListenStand>,
}

#[tauri::command(async)]
pub fn schild_stand(app: AppHandle) -> SchildStand {
    SchildStand { aktiv: SCHILD.aktiv.load(Ordering::Relaxed), listen: listen::stand(&app) }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mit_regeln(werbung: &str, tracker: &str) {
        engine_setzen(Treffer::Werbung, listen::engine_aus(&[werbung]));
        engine_setzen(Treffer::Tracker, listen::engine_aus(&[tracker]));
        konfig_uebernehmen(&Konfig::default());
    }

    // Die Engine ist global; die Tests laufen deshalb in einem.
    #[test]
    fn filtert_nach_listen_und_ausnahmen() {
        mit_regeln("||werbung.example^\n##.anzeige\nseite.example##.banner", "||zaehler.example^$third-party");
        let seite = "https://seite.example/artikel";

        assert_eq!(einstufen("https://werbung.example/a.js", seite, AnfrageArt::Skript), Some(Treffer::Werbung));
        assert_eq!(einstufen("https://zaehler.example/p.gif", seite, AnfrageArt::Bild), Some(Treffer::Tracker));
        assert_eq!(einstufen("https://seite.example/bild.png", seite, AnfrageArt::Bild), None);
        // $third-party: auf der eigenen Seite des Zählers kein Treffer.
        assert_eq!(einstufen("https://zaehler.example/p.gif", "https://zaehler.example/", AnfrageArt::Bild), None);

        // Ausnahme für die Seite und ihre Unterdomains.
        let mut k = Konfig::default();
        k.schild_ausnahmen = vec!["seite.example".into()];
        konfig_uebernehmen(&k);
        assert_eq!(einstufen("https://werbung.example/a.js", "https://news.seite.example/", AnfrageArt::Skript), None);
        assert_eq!(einstufen("https://werbung.example/a.js", "https://andere.example/", AnfrageArt::Skript), Some(Treffer::Werbung));

        // Global aus.
        k.schild_ausnahmen.clear();
        k.schild_aktiv = false;
        konfig_uebernehmen(&k);
        assert_eq!(einstufen("https://werbung.example/a.js", seite, AnfrageArt::Skript), None);
        konfig_uebernehmen(&Konfig::default());

        // Kosmetik: seitenspezifisch und allgemein nach Klassen.
        let kosmetik = kosmetik(seite).unwrap();
        assert!(kosmetik.css.contains(".banner{display:none!important}"));
        let css = allgemeine_kosmetik(seite, r#"{"k":["anzeige","text"],"i":[]}"#, &kosmetik.ausnahmen).unwrap();
        assert!(css.contains(".anzeige"));
        assert!(!css.contains(".text"));
        assert!(allgemeine_kosmetik(seite, "kein json", &kosmetik.ausnahmen).is_none());
    }
}
