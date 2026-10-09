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

use tauri::{AppHandle, Manager, State};

pub mod ereignis;
pub mod formular;
pub mod https;
pub mod ruhe;
pub mod weg;
#[cfg(windows)]
mod desktop;
#[cfg(windows)]
pub(crate) use desktop::{im_profil, recht_art, recht_name};
#[cfg(target_os = "android")]
pub mod android;

pub use ereignis::{melden, Antwort, DownloadStand, MenueEintrag, TabEreignis, EREIGNIS};
pub use weg::{id_pruefen, navigation_erlaubt, vorab, weg, ziel_pruefen, Vorab, Weg};

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
    crate::schild::tab_vergessen(&id);
    formular::vergessen(&id);
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
        crate::schild::tab_vergessen(&id);
        plattform::schliessen(&app, &id);
    }
    Ok(())
}

/// Schaltet den Ton eines Tabs stumm oder wieder an.
#[tauri::command(async)]
pub fn tab_stumm(app: AppHandle, id: String, stumm: bool) -> Result<(), String> {
    id_pruefen(&id)?;
    plattform::stumm(&app, &id, stumm)
}

/// Gibt der Oberfläche den Tastaturfokus zurück (Klick in die Adresszeile,
/// während eine Seite ihn hatte).
#[tauri::command(async)]
pub fn oberflaeche_fokussieren(app: AppHandle) -> Result<(), String> {
    plattform::oberflaeche_fokussieren(&app);
    Ok(())
}

/// Der Ordner mit Cookies, Cache und Speicher der Seiten.
pub fn seitenprofil(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|d| d.join("seiten"))
        .map_err(|e| format!("Datenordner unbekannt: {e}"))
}

/// Setzt das Cookie-Skript in allen Tabs neu (`crate::cookies`): es trägt
/// den Stand des Schilds und gilt ab dem nächsten Dokument.
pub fn cookies_erneuern(app: &AppHandle) {
    plattform::cookies_erneuern(app);
}

#[cfg(windows)]
use desktop as plattform;
#[cfg(target_os = "android")]
use android as plattform;

#[cfg(test)]
mod tests {
    use super::*;

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
