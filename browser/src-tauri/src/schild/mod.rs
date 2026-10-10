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
use tauri::{AppHandle, Manager};

use crate::konfig::Konfig;
use crate::tabs::{melden, TabEreignis};

pub mod gesamt;
pub mod kategorien;
pub mod listen;
pub mod netzzeit;
pub mod schutz;
pub mod schutz_dienst;
pub mod sperre;
pub mod verfolgung;

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
    /// Android nennt keine Art, nur Adresse und Kopfzeilen: geraten aus
    /// `Accept` und Endung. Was unklar bleibt, ist `Sonstiges`; Regeln ohne
    /// Typangabe greifen dann trotzdem.
    pub fn raten(url: &str, accept: &str) -> AnfrageArt {
        let accept = accept.to_ascii_lowercase();
        if accept.starts_with("text/html") {
            return AnfrageArt::Dokument;
        }
        if accept.starts_with("text/css") {
            return AnfrageArt::Stylesheet;
        }
        if accept.starts_with("image/") {
            return AnfrageArt::Bild;
        }
        let pfad = url.split(['?', '#']).next().unwrap_or("").to_ascii_lowercase();
        let endung = pfad.rsplit_once('/').map_or("", |(_, name)| name).rsplit_once('.').map_or("", |(_, e)| e);
        match endung {
            "js" | "mjs" => AnfrageArt::Skript,
            "css" => AnfrageArt::Stylesheet,
            "png" | "jpg" | "jpeg" | "gif" | "webp" | "avif" | "svg" | "ico" => AnfrageArt::Bild,
            "woff" | "woff2" | "ttf" | "otf" => AnfrageArt::Schrift,
            "mp4" | "webm" | "m3u8" | "m4s" | "mp3" | "ogg" => AnfrageArt::Medien,
            _ => AnfrageArt::Sonstiges,
        }
    }

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

    /// Status und Text für eine geblockte Anfrage. Ein Ping (`sendBeacon`,
    /// `<a ping>`) erwartet keine Antwort; 204 sieht für die Seite aus wie
    /// angenommen, ein 403 meldet sie mancherorts als Fehler weiter.
    pub fn sperrantwort(self) -> (i32, &'static str) {
        match self {
            AnfrageArt::Ping => (204, "No Content"),
            _ => (403, "Blocked by MSB"),
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
    /// Tabs, deren aktuelle Seite schon eine Cookie-Ablehnung gezählt hat.
    cookies: Mutex<HashSet<String>>,
}

static SCHILD: LazyLock<Schild> = LazyLock::new(|| Schild {
    werbung: RwLock::new(listen::engine_aus(&[listen::EINGEBAUT_WERBUNG])),
    tracker: RwLock::new(listen::engine_aus(&[listen::EINGEBAUT_TRACKER])),
    aktiv: AtomicBool::new(true),
    ausnahmen: RwLock::new(HashSet::new()),
    zaehler: Mutex::new(HashMap::new()),
    cookies: Mutex::new(HashSet::new()),
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
    !pausiert(&h, &SCHILD.ausnahmen.read().unwrap())
}

/// Schalter und Ausnahmen, wie sie das Cookie-Skript in jedem Rahmen prüft
/// (`crate::cookies`).
pub fn stand_fuer_seiten() -> (bool, Vec<String>) {
    let mut ausnahmen: Vec<String> = SCHILD.ausnahmen.read().unwrap().iter().cloned().collect();
    ausnahmen.sort();
    (SCHILD.aktiv.load(Ordering::Relaxed), ausnahmen)
}

/// Steht der Host oder eine Elterndomain mit Punkt in den Ausnahmen? Der Host
/// selbst zählt auch ohne Punkt (`localhost`). Die Oberfläche zeigt dasselbe
/// (`schildPausiert` in `geraetKonfig.ts`, gleiche Fälle in
/// `schildAusnahmen.faelle.json`).
fn pausiert(host: &str, ausnahmen: &HashSet<String>) -> bool {
    let mut rest = host;
    loop {
        if ausnahmen.contains(rest) {
            return true;
        }
        match rest.split_once('.') {
            Some((_, eltern)) if eltern.contains('.') => rest = eltern,
            _ => return false,
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
    let ((werbung, tracker), erster) = {
        let mut zaehler = SCHILD.zaehler.lock().unwrap();
        let eintrag = zaehler.entry(tab.to_string()).or_default();
        let erster = *eintrag == (0, 0);
        match treffer {
            Treffer::Werbung => eintrag.0 += 1,
            Treffer::Tracker => eintrag.1 += 1,
        }
        (*eintrag, erster)
    };
    if zaehlt_insgesamt(app, tab) {
        gesamt::zaehlen(treffer);
        if erster {
            gesamt::seite_zaehlen();
        }
    }
    melden(app, TabEreignis::Schild { id: tab.to_string(), werbung, tracker });
    true
}

/// Private Tabs hinterlassen keine Spur, auch keine Zahl (Punkt 158); ein
/// unbekannter Tab zählt ebenso nicht.
fn zaehlt_insgesamt(app: &AppHandle, tab: &str) -> bool {
    let tabs = app.state::<crate::tabs::Tabs>();
    let z = tabs.0.lock().unwrap();
    z.tabs.get(tab).is_some_and(|t| !t.privat)
}

/// Das Cookie-Skript hat auf der Seite im Tab einen Hinweis abgelehnt
/// (`seite.js` meldet es, `tabs::cookies_gemeldet`). Gezählt
/// wird höchstens eine Ablehnung je Seitenaufruf, sonst könnte die Seite die
/// Statistik mit eigenen Meldungen treiben.
pub fn cookies_abgelehnt(app: &AppHandle, tab: &str) {
    if !SCHILD.cookies.lock().unwrap().insert(tab.to_string()) {
        return;
    }
    if zaehlt_insgesamt(app, tab) {
        gesamt::cookie_zaehlen();
    }
}

/// Neue Seite im Tab: der Zähler beginnt von vorn.
pub fn seitenwechsel(app: &AppHandle, tab: &str) {
    SCHILD.cookies.lock().unwrap().remove(tab);
    let vorher = SCHILD.zaehler.lock().unwrap().remove(tab);
    if vorher.is_some() {
        melden(app, TabEreignis::Schild { id: tab.to_string(), werbung: 0, tracker: 0 });
    }
}

pub fn tab_vergessen(tab: &str) {
    SCHILD.zaehler.lock().unwrap().remove(tab);
    SCHILD.cookies.lock().unwrap().remove(tab);
}

/// Was nach dem Laden einer Seite ausgeblendet wird.
pub struct Kosmetik {
    pub css: String,
    /// Die Seite schaltet die allgemeinen Regeln ab (`$generichide`).
    pub generichide: bool,
    pub ausnahmen: HashSet<String>,
}

/// Ein Selektor aus einer Filterliste darf nur Selektor sein. Mit `{` oder `}`
/// schrieb er eigene Regeln in das Stylesheet der Seite, mit `@` am Anfang
/// (`@import url(…);a`) lud er als erste Regel fremdes CSS, ein `\` am Ende
/// oder ein Kommentar schluckte den Block dahinter (bis 10.10.2026).
fn nur_selektor(s: &str) -> bool {
    let s = s.trim();
    !s.is_empty() && !s.contains(['{', '}']) && !s.starts_with('@') && !s.contains("/*") && !s.ends_with('\\')
}

fn verstecken(selektoren: impl IntoIterator<Item = String>) -> String {
    // Je Regel ein eigener Block: ein ungültiger Selektor würde sonst den
    // ganzen Block ungültig machen.
    selektoren
        .into_iter()
        .filter(|s| nur_selektor(s))
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

/// Sammelt Klassen und IDs der Seite, höchstens je 4000; die Antwort geht an
/// [`allgemeine_kosmetik`].
pub const KLASSEN_SAMMELN: &str = r#"(() => {
  const k = new Set(), i = new Set();
  for (const el of document.querySelectorAll('[class],[id]')) {
    if (el.id && i.size < 4000) i.add(el.id);
    for (const c of el.classList) { if (k.size < 4000) k.add(c); }
  }
  return { k: [...k], i: [...i] };
})()"#;

/// Hängt CSS der Kosmetik als eigenes `<style>` an die Seite.
pub fn stil_skript(css: &str) -> String {
    let css = serde_json::to_string(css).unwrap_or_else(|_| "\"\"".into());
    format!(
        "(() => {{ const s = document.createElement('style'); s.dataset.msb = 'schild'; s.textContent = {css}; (document.head || document.documentElement).appendChild(s); }})()"
    )
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

    #[test]
    fn ein_selektor_schreibt_keine_eigenen_regeln() {
        let css = verstecken(
            [
                ".werbung",
                "div[style=\"position: fixed; top: 0\"]",
                "a{} body{background:url(https://x.example/)}",
                "@import url(https://x.example/a.css);a",
                " @media all",
                ".a /* rest",
                ".a\\",
                "",
            ]
            .map(String::from),
        );
        assert_eq!(
            css,
            ".werbung{display:none!important}\ndiv[style=\"position: fixed; top: 0\"]{display:none!important}"
        );
    }

    #[test]
    fn ein_geblockter_ping_bekommt_204() {
        assert_eq!(AnfrageArt::Ping.sperrantwort().0, 204);
        for art in [AnfrageArt::Dokument, AnfrageArt::Skript, AnfrageArt::Xhr, AnfrageArt::Bild, AnfrageArt::Sonstiges] {
            assert_eq!(art.sperrantwort().0, 403, "{art:?}");
        }
    }

    fn mit_regeln(werbung: &str, tracker: &str) {
        engine_setzen(Treffer::Werbung, listen::engine_aus(&[werbung]));
        engine_setzen(Treffer::Tracker, listen::engine_aus(&[tracker]));
        konfig_uebernehmen(&Konfig::default());
    }

    #[test]
    fn ausnahmen_gelten_wie_in_der_oberflaeche() {
        #[derive(serde::Deserialize)]
        struct Fall {
            host: String,
            ausnahmen: HashSet<String>,
            pausiert: bool,
        }
        let faelle: Vec<Fall> =
            serde_json::from_str(include_str!("../../../../frontend/src/browser/services/schildAusnahmen.faelle.json")).unwrap();
        for f in faelle {
            assert_eq!(pausiert(&f.host, &f.ausnahmen), f.pausiert, "{} mit {:?}", f.host, f.ausnahmen);
        }
    }

    #[test]
    fn android_raet_die_art_einer_anfrage() {
        use AnfrageArt::*;
        assert_eq!(AnfrageArt::raten("https://x.example/a.js?v=2", "*/*"), Skript);
        assert_eq!(AnfrageArt::raten("https://x.example/frame", "text/html,application/xhtml+xml"), Dokument);
        assert_eq!(AnfrageArt::raten("https://x.example/p", "image/avif,image/webp"), Bild);
        assert_eq!(AnfrageArt::raten("https://x.example/s.CSS", ""), Stylesheet);
        assert_eq!(AnfrageArt::raten("https://x.example/api/daten", "application/json"), Sonstiges);
        assert_eq!(AnfrageArt::raten("https://x.example/ordner.js/", "*/*"), Sonstiges);
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
