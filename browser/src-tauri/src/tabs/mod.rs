//! Tabs: jede geöffnete Seite ist eine eigene native Webview.
//!
//! Die Oberfläche (React) ist die Wahrheit über Reihenfolge, Titel und
//! Kennungen der Tabs. Rust hält nur, was es zum Anzeigen braucht: welche
//! Webviews es gibt, welche gerade vorne liegt und wo der Inhaltsbereich im
//! Fenster sitzt. Was in einem Tab passiert (lädt, neue Adresse, Titel,
//! Download, geblockte Anfragen), meldet Rust als ein Ereignis [`EREIGNIS`].
//!
//! Desktop und Android teilen diese Befehle. Darunter liegen zwei Wege:
//! `desktop.rs` (eigene WebView2-Controller im Hauptfenster, ohne Tauri-IPC) und `android.rs`
//! (native WebViews über das Kotlin-Plugin `TabsPlugin`).
//!
//! Alle Befehle sind `async`. Ein synchroner Befehl läuft auf dem
//! Hauptfaden, und eine Webview darin anzulegen blockierte unter Windows die
//! gesamte IPC: nach der ersten geladenen Seite reagierte der Browser auf
//! nichts mehr (bis 10/2026).

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Instant;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};

pub mod formular;
pub mod ruhe;
#[cfg(windows)]
mod desktop;
#[cfg(windows)]
pub(crate) use desktop::{im_profil, recht_art, recht_name};
#[cfg(target_os = "android")]
pub mod android;

/// Name des Ereignisses, über das Rust Neuigkeiten aus den Tabs meldet.
pub const EREIGNIS: &str = "msb:tab";

/// Was die Oberfläche aus einem Tab erfährt.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "art", rename_all = "snake_case")]
pub enum TabEreignis {
    /// Eine neue Seite beginnt zu laden.
    Laedt { id: String, url: String },
    /// Die Seite ist geladen.
    Geladen { id: String, url: String },
    /// Die angezeigte Adresse hat sich geändert (auch ohne Neuladen, etwa
    /// durch `history.pushState`), mit dem Stand von Zurück und Vor.
    Adresse { id: String, url: String, zurueck: bool, vor: bool },
    Titel { id: String, titel: String },
    Favicon { id: String, url: Option<String> },
    /// Die Seite will ein neues Fenster öffnen (Link mit `target=_blank`,
    /// `window.open`). Der Browser öffnet stattdessen einen Tab.
    NeuerTab { id: String, url: String },
    /// Ein Tastenkürzel, das im Tab gedrückt wurde und dem Browser gehört
    /// (Strg+T, Strg+L, …).
    Taste { id: String, taste: String },
    /// Ein Download; `nr` kennzeichnet ihn über alle Meldungen hinweg.
    Download {
        id: String,
        nr: u64,
        stand: DownloadStand,
        url: String,
        datei: Option<String>,
    },
    /// Zahl der in diesem Tab seit dem letzten Seitenwechsel geblockten
    /// Anfragen.
    Schild { id: String, werbung: u32, tracker: u32 },
    /// Der Prozess der Seite ist abgestürzt.
    Absturz { id: String },
    /// Ein Video oder eine Seite will den ganzen Bildschirm.
    Vollbild { id: String, an: bool },
    /// Ein Anmeldefeld der Seite (`formular.rs`). `url` ist die Adresse
    /// des Tabs, nicht eine Angabe der Seite.
    Formular { id: String, url: String, meldung: formular::Meldung },
    /// Rechtsklick. `x`/`y` in Pixeln der Webview; die Einträge sind die
    /// Befehle der WebView2, die gerade gehen.
    Kontextmenue {
        id: String,
        nr: u64,
        x: i32,
        y: i32,
        eintraege: Vec<MenueEintrag>,
        link: Option<String>,
        bild: Option<String>,
        auswahl: Option<String>,
        bearbeitbar: bool,
    },
    /// `alert`, `confirm`, `prompt` oder `beforeunload`.
    Dialog { id: String, nr: u64, dialog: String, herkunft: String, text: String, vorgabe: String },
    /// Die Seite will ein Recht (Kamera, Standort, …).
    Recht { id: String, nr: u64, recht: String, herkunft: String },
    /// HTTP-Anmeldung (Basic/Digest).
    Anmeldung { id: String, nr: u64, herkunft: String, bereich: String },
    /// Die Seite ließ sich nicht laden.
    Fehlerseite { id: String, url: String, grund: String },
    /// Der Jugend- und Suchtschutz sperrt die Seite; `grund` ist die
    /// Kategorie oder `eigene`.
    Gesperrt { id: String, url: String, grund: String },
    /// Ziel des Links unter dem Zeiger, leer, wenn keiner.
    Status { id: String, text: String },
    /// Stand der Suche in der Seite (`aktuell` ab 1, 0 ohne aktiven Treffer).
    Treffer { id: String, aktuell: i32, anzahl: i32 },
    /// Die Seite spielt Ton oder hört damit auf; `stumm`, wenn der Tab
    /// stummgeschaltet ist.
    Ton { id: String, spielt: bool, stumm: bool },
    /// Der Tab schläft (`ruhe.rs`) oder ist wieder wach.
    Schlaf { id: String, schlaeft: bool },
    /// Die Webview des Tabs ist weg, um Speicher zu sparen. Die Adresse
    /// behält die Oberfläche; beim nächsten Zeigen lädt sie neu.
    Verworfen { id: String },
    /// Ein Ereignis des DevTools-Protokolls für die Entwicklerwerkzeuge,
    /// unverändert bis auf Kürzungen (`desktop/entwickler.rs`).
    Protokoll { id: String, methode: String, daten: serde_json::Value },
}

#[derive(Debug, Clone, Serialize)]
pub struct MenueEintrag {
    pub befehl: i32,
    /// Name der WebView2 (`copy`, `paste`, `saveImageAs`, …).
    pub name: String,
}

/// Die Antwort der Oberfläche auf eine Rückfrage der Seite.
#[derive(Debug, Serialize, serde::Deserialize)]
#[serde(tag = "art", rename_all = "snake_case")]
pub enum Antwort {
    /// `None`: die Oberfläche hat selbst gehandelt oder nichts gewählt.
    Menue { befehl: Option<i32> },
    Dialog { ok: bool, text: Option<String> },
    Recht { erlauben: bool },
    /// Ohne Benutzer: abgebrochen.
    Anmeldung { benutzer: Option<String>, passwort: Option<String> },
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DownloadStand {
    Start,
    /// Geladen, der Virenschutz prüft.
    Pruefung,
    Fertig,
    /// Der Virenschutz hat die Datei abgelehnt; sie ist gelöscht.
    Blockiert,
    Fehler,
    /// Auf dem Gerät war kein Platz mehr (Android).
    Speicher,
}

pub fn melden(app: &AppHandle, ereignis: TabEreignis) {
    if matches!(ereignis, TabEreignis::Gesperrt { .. }) {
        crate::schild::schutz_dienst::treffer(app);
    }
    let _ = app.emit_to("main", EREIGNIS, ereignis);
}

#[derive(Debug, Clone, Copy, PartialEq, serde::Deserialize)]
pub struct Rahmen {
    pub x: f64,
    pub y: f64,
    pub breite: f64,
    pub hoehe: f64,
}

#[derive(Debug, Clone)]
pub struct TabDaten {
    pub privat: bool,
    /// Seit wann der Tab nicht vorne liegt; `None`, solange er vorne ist.
    pub verborgen_seit: Option<Instant>,
}

pub struct Zustand {
    pub tabs: HashMap<String, TabDaten>,
    pub aktiv: Option<String>,
    pub rahmen: Rahmen,
    /// Die Oberfläche legt gerade etwas über den Inhaltsbereich (Dialog,
    /// Vorschlagsliste). Native Webviews liegen immer oben, deshalb muss der
    /// Tab so lange weg.
    pub verdeckt: bool,
    /// Ein Tab zeigt gerade ein Video im Vollbild.
    pub vollbild: Option<String>,
    pub leistung: ruhe::Leistung,
}

#[derive(Default)]
pub struct Tabs(pub Mutex<Zustand>);

impl Default for Zustand {
    fn default() -> Self {
        Self {
            tabs: HashMap::new(),
            aktiv: None,
            rahmen: Rahmen { x: 0.0, y: 0.0, breite: 800.0, hoehe: 600.0 },
            verdeckt: false,
            vollbild: None,
            leistung: ruhe::Leistung::default(),
        }
    }
}

impl Zustand {
    /// Soll dieser Tab gerade zu sehen sein?
    pub fn sichtbar(&self, id: &str) -> bool {
        if let Some(v) = &self.vollbild {
            return v == id;
        }
        !self.verdeckt && self.aktiv.as_deref() == Some(id)
    }

    /// Legt `id` nach vorne; der bisherige vordere Tab gilt ab `jetzt` als verborgen.
    pub fn aktiv_setzen(&mut self, id: Option<String>, jetzt: Instant) {
        if let Some(alt) = self.aktiv.take().and_then(|a| self.tabs.get_mut(&a)) {
            alt.verborgen_seit.get_or_insert(jetzt);
        }
        if let Some(neu) = id.as_ref().and_then(|i| self.tabs.get_mut(i)) {
            neu.verborgen_seit = None;
        }
        self.aktiv = id;
    }
}

/// Kennungen kommen aus der Oberfläche und werden zum Label einer Webview.
/// Erlaubt ist nur `tab-` mit Buchstaben, Ziffern und Strich: so kann eine
/// Kennung nie `main` heißen und nie die Rechte der Oberfläche erben
/// (`capabilities/oberflaeche.json` gilt nur für die Webview `main`).
pub fn id_pruefen(id: &str) -> Result<(), String> {
    let rest = id.strip_prefix("tab-").ok_or("Ungültige Tab-Kennung")?;
    if rest.is_empty() || rest.len() > 40 || !rest.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("Ungültige Tab-Kennung".into());
    }
    Ok(())
}

/// Hosts, unter denen Tauri die eigene Oberfläche und ihre IPC ausliefert.
/// Ein Tab darf sie nie laden: dort liefe fremder Inhalt mit dem Ursprung der
/// Oberfläche.
const APP_HOSTS: [&str; 3] = ["tauri.localhost", "ipc.localhost", "asset.localhost"];

/// Darf ein Tab diese Adresse als Seite öffnen?
pub fn navigation_erlaubt(url: &url::Url) -> bool {
    match url.scheme() {
        "http" | "https" => !url.host_str().is_some_and(|h| APP_HOSTS.contains(&h.trim_end_matches('.'))),
        // Leere Seite, Blob- und Data-Adressen, die eine Seite selbst erzeugt.
        "about" | "blob" | "data" => true,
        _ => false,
    }
}

/// Was mit einer Navigation im Tab geschieht. Dieselbe Entscheidung für
/// WebView2 (`desktop/grundereignisse.rs`) und Android (`android/bruecke.rs`),
/// auch für Weiterleitungen, nicht nur für die erste Adresse.
#[derive(Debug, PartialEq)]
pub enum Weg {
    Laden,
    /// Keine Seite, die ein Tab öffnen darf (`navigation_erlaubt`).
    Verboten,
    /// Der Jugend- und Suchtschutz sperrt sie; die Kategorie oder `eigene`.
    Gesperrt(&'static str),
    /// Dieselbe Suche mit erzwungener sicherer Suche (`schild/sperre.rs`).
    Umleiten(String),
}

pub fn weg(url: &str) -> Weg {
    if !url::Url::parse(url).is_ok_and(|u| navigation_erlaubt(&u)) {
        return Weg::Verboten;
    }
    if let Some(grund) = crate::schild::sperre::gesperrt(url) {
        return Weg::Gesperrt(grund.name());
    }
    match crate::schild::sperre::sichere_suche(url) {
        Some(sicher) => Weg::Umleiten(sicher),
        None => Weg::Laden,
    }
}

/// Adressen, die die Oberfläche zum Öffnen schickt: nur Webseiten.
pub fn ziel_pruefen(url: &str) -> Result<url::Url, String> {
    let geparst = url::Url::parse(url).map_err(|_| "Ungültige Adresse".to_string())?;
    if !matches!(geparst.scheme(), "http" | "https") || !navigation_erlaubt(&geparst) {
        return Err("Diese Adresse kann der Browser nicht öffnen.".into());
    }
    Ok(geparst)
}

/// Öffnet eine Seite in einem Tab. Gibt es den Tab noch nicht als Webview,
/// entsteht er jetzt; sonst navigiert er.
#[tauri::command(async)]
pub fn tab_laden(
    app: AppHandle,
    tabs: State<'_, Tabs>,
    id: String,
    url: String,
    privat: bool,
) -> Result<(), String> {
    id_pruefen(&id)?;
    let ziel = ziel_pruefen(&url)?;
    crate::schild::schutz_dienst::bereit();
    eintragen(&tabs, &id, privat);
    plattform::laden(&app, &tabs, &id, ziel, privat)
}

/// Merkt sich einen Tab, bevor seine Webview entsteht.
pub(crate) fn eintragen(tabs: &Tabs, id: &str, privat: bool) {
    let mut z = tabs.0.lock().unwrap();
    let vorne = z.aktiv.as_deref() == Some(id);
    z.tabs.entry(id.to_string()).or_insert(TabDaten { privat, verborgen_seit: (!vorne).then(Instant::now) });
}

/// Legt einen Tab nach vorne. `None` heißt: kein Tab mit Webview vorne (etwa
/// die Startseite eines neuen Tabs).
#[tauri::command(async)]
pub fn tab_aktivieren(app: AppHandle, tabs: State<'_, Tabs>, id: Option<String>) -> Result<(), String> {
    if let Some(id) = &id {
        id_pruefen(id)?;
    }
    tabs.0.lock().unwrap().aktiv_setzen(id, Instant::now());
    plattform::sichtbarkeit(&app, &tabs);
    Ok(())
}

#[tauri::command(async)]
pub fn tab_schliessen(app: AppHandle, tabs: State<'_, Tabs>, id: String) -> Result<(), String> {
    id_pruefen(&id)?;
    {
        let mut z = tabs.0.lock().unwrap();
        z.tabs.remove(&id);
        if z.aktiv.as_deref() == Some(&id) {
            z.aktiv = None;
        }
        if z.vollbild.as_deref() == Some(&id) {
            z.vollbild = None;
        }
    }
    crate::schild::tab_vergessen(&app, &id);
    plattform::schliessen(&app, &id);
    Ok(())
}

/// Zurück, Vor, Neu laden, Anhalten: über die Webview, nie als Skript der
/// Seite. Als `history.back()` konnte eine Seite den Zurück-Knopf abschalten,
/// indem sie `History.prototype.back` überschrieb, und eine hängende Seite
/// ließ sich nicht anhalten (bis 09.10.2026).
#[tauri::command(async)]
pub fn tab_aktion(app: AppHandle, id: String, aktion: String) -> Result<(), String> {
    id_pruefen(&id)?;
    if !matches!(aktion.as_str(), "zurueck" | "vor" | "neu_laden" | "anhalten") {
        return Err("Unbekannte Aktion".into());
    }
    plattform::aktion(&app, &id, &aktion)
}

/// Wo der Inhaltsbereich im Fenster liegt (logische Pixel).
#[tauri::command(async)]
pub fn tabs_rahmen(app: AppHandle, tabs: State<'_, Tabs>, rahmen: Rahmen) -> Result<(), String> {
    tabs.0.lock().unwrap().rahmen = rahmen;
    plattform::rahmen_anwenden(&app, &tabs);
    Ok(())
}

/// Blendet den Tab aus, solange die Oberfläche etwas über den Inhalt legt.
#[tauri::command(async)]
pub fn tabs_verdecken(app: AppHandle, tabs: State<'_, Tabs>, verdeckt: bool) -> Result<(), String> {
    tabs.0.lock().unwrap().verdeckt = verdeckt;
    plattform::sichtbarkeit(&app, &tabs);
    Ok(())
}

/// Antwort auf Rechtsklick, Dialog, Recht oder Anmeldung eines Tabs.
#[tauri::command(async)]
pub fn tab_antworten(app: AppHandle, nr: u64, antwort: Antwort) -> Result<(), String> {
    plattform::antworten(&app, nr, antwort)
}

/// Eine Methode des DevTools-Protokolls im Tab, für die Entwicklerwerkzeuge.
/// Was durchgeht, entscheidet `entwickler::erlaubt`.
#[tauri::command(async)]
pub fn tab_protokoll(app: AppHandle, id: String, methode: String, parameter: serde_json::Value) -> Result<serde_json::Value, String> {
    id_pruefen(&id)?;
    plattform::protokoll(&app, &id, methode, parameter)
}

/// Suchen in der Seite: `richtung` ist `start`, `weiter`, `zurueck` oder `ende`.
#[tauri::command(async)]
pub fn tab_suchen(app: AppHandle, id: String, richtung: String, begriff: String) -> Result<(), String> {
    id_pruefen(&id)?;
    plattform::suchen(&app, &id, richtung, begriff)
}

#[tauri::command(async)]
pub fn tab_drucken(app: AppHandle, id: String) -> Result<(), String> {
    id_pruefen(&id)?;
    plattform::drucken(&app, &id)
}

/// Ein Bild des vorderen Tabs (JPEG), das die Oberfläche zeigt, solange der
/// Tab verdeckt ist.
#[tauri::command(async)]
pub fn tab_standbild(app: AppHandle, tabs: State<'_, Tabs>) -> Result<tauri::ipc::Response, String> {
    plattform::standbild(&app, &tabs).map(tauri::ipc::Response::new)
}

/// Schließt alle Tabs. Die Oberfläche ruft das beim Start: nach einem
/// Neuladen der Oberfläche (etwa nach der Kopplung) stünden sonst Webviews
/// ohne Tab herum.
#[tauri::command(async)]
pub fn tabs_zuruecksetzen(app: AppHandle, tabs: State<'_, Tabs>) -> Result<(), String> {
    let ids: Vec<String> = {
        let mut z = tabs.0.lock().unwrap();
        let ids = z.tabs.keys().cloned().collect();
        *z = Zustand { rahmen: z.rahmen, leistung: z.leistung.clone(), ..Zustand::default() };
        ids
    };
    for id in ids {
        crate::schild::tab_vergessen(&app, &id);
        plattform::schliessen(&app, &id);
    }
    Ok(())
}

/// Schaltet den Ton eines Tabs stumm oder wieder an.
#[tauri::command(async)]
pub fn tab_stumm(app: AppHandle, id: String, stumm: bool) -> Result<(), String> {
    id_pruefen(&id)?;
    #[cfg(windows)]
    return plattform::stumm(&app, &id, stumm);
    #[cfg(not(windows))]
    {
        let _ = (app, stumm);
        Ok(())
    }
}

/// Gibt der Oberfläche den Tastaturfokus zurück (Klick in die Adresszeile,
/// während eine Seite ihn hatte).
#[tauri::command(async)]
pub fn oberflaeche_fokussieren(app: AppHandle) -> Result<(), String> {
    #[cfg(windows)]
    plattform::oberflaeche_fokussieren(&app);
    #[cfg(not(windows))]
    let _ = app;
    Ok(())
}

/// Der Ordner mit Cookies, Cache und Speicher der Seiten.
pub fn seitenprofil(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|d| d.join("seiten"))
        .map_err(|e| format!("Datenordner unbekannt: {e}"))
}

#[cfg(windows)]
use desktop as plattform;
#[cfg(target_os = "android")]
use android as plattform;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kennung_kann_nie_die_oberflaeche_sein() {
        assert!(id_pruefen("tab-a1b2").is_ok());
        assert!(id_pruefen("main").is_err());
        assert!(id_pruefen("tab-").is_err());
        assert!(id_pruefen("tab-../main").is_err());
        assert!(id_pruefen(&format!("tab-{}", "a".repeat(41))).is_err());
    }

    #[test]
    fn tabs_laden_nie_die_oberflaeche() {
        let u = |s: &str| url::Url::parse(s).unwrap();
        assert!(navigation_erlaubt(&u("https://example.com/")));
        assert!(navigation_erlaubt(&u("about:blank")));
        assert!(!navigation_erlaubt(&u("http://tauri.localhost/browser.html")));
        assert!(!navigation_erlaubt(&u("https://ipc.localhost/konfig_laden")));
        assert!(!navigation_erlaubt(&u("tauri://localhost/")));
        assert!(!navigation_erlaubt(&u("file:///C:/Windows/win.ini")));
        assert!(!navigation_erlaubt(&u("javascript:alert(1)")));
    }

    #[test]
    fn navigation_entscheidet_ohne_webview() {
        assert_eq!(weg("https://example.com/"), Weg::Laden);
        assert_eq!(weg("http://tauri.localhost/browser.html"), Weg::Verboten);
        assert_eq!(weg("file:///C:/Windows/win.ini"), Weg::Verboten);
        assert_eq!(weg("intent://scan/#Intent;scheme=zxing;end"), Weg::Verboten);
        assert_eq!(weg("kaputt"), Weg::Verboten);
    }

    #[test]
    fn die_oberflaeche_oeffnet_nur_webseiten() {
        assert!(ziel_pruefen("https://www.google.com/search?q=x").is_ok());
        assert!(ziel_pruefen("about:blank").is_err());
        assert!(ziel_pruefen("data:text/html,hallo").is_err());
        assert!(ziel_pruefen("http://tauri.localhost/").is_err());
        // Mit Punkt am Ende derselbe Host; bis 09.10.2026 galt er als fremd.
        assert!(ziel_pruefen("https://ipc.localhost./konfig_laden").is_err());
        assert!(!navigation_erlaubt(&url::Url::parse("http://TAURI.localhost./browser.html").unwrap()));
    }

    #[test]
    fn vollbild_und_verdecken_bestimmen_die_sichtbarkeit() {
        let mut z = Zustand { aktiv: Some("tab-a".into()), ..Zustand::default() };
        assert!(z.sichtbar("tab-a"));
        assert!(!z.sichtbar("tab-b"));
        z.verdeckt = true;
        assert!(!z.sichtbar("tab-a"));
        z.vollbild = Some("tab-b".into());
        assert!(z.sichtbar("tab-b"));
        assert!(!z.sichtbar("tab-a"));
    }
}
