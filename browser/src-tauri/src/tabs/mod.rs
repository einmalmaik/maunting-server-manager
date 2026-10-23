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

pub mod aufnahme;
pub mod ereignis;
pub mod formular;
pub mod https;
pub mod ruhe;
pub mod uebersetzung;
pub mod weg;
#[cfg(windows)]
mod desktop;
#[cfg(windows)]
pub(crate) use desktop::{
    erweiterung_laden, erweiterung_popup, erweiterung_popup_schliessen, erweiterungen_abgleichen, im_profil, mit_profil,
    profil_helfer_schliessen, recht_art, recht_name,
};
#[cfg(target_os = "android")]
pub mod android;

pub use ereignis::{melden, Antwort, DownloadStand, MenueEintrag, TabEreignis, EREIGNIS};
pub use weg::{erweiterungen_setzen, extern_oeffnen, externes_protokoll, id_pruefen, navigation_erlaubt, vorab, weg, ziel_pruefen, Vorab, Weg};

#[derive(Debug, Clone, Copy, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct Rahmen {
    pub x: f64,
    pub y: f64,
    pub breite: f64,
    pub hoehe: f64,
}

#[derive(Debug, Clone, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct SplitZustand {
    pub sekundaer_id: String,
    pub primaer_rahmen: Rahmen,
    pub sekundaer_rahmen: Rahmen,
}

#[derive(Debug, Clone)]
pub struct PanelZustand {
    pub id: String,
    pub rahmen: Rahmen,
}

/// Die drei externen Web-Apps in der Seitenleiste. Ihre Kennungen sind fest,
/// damit die Oberfläche keinen fremden Panel-Host erzeugen kann.
const EXTERNE_PANEL: [&str; 4] = ["tab-panel-whatsapp", "tab-panel-discord", "tab-panel-telegram", "tab-panel-instagram"];

/// Persistente WebView-Profile der externen Dienste. Jeder Dienst hat sein
/// eigenes Profil, getrennt von normalen Tabs und von den anderen Diensten.
/// Muss zu `PANEL_PROFILE` in `TabsPlugin.kt` passen.
pub(crate) const EXTERNE_PROFILE: [&str; 4] = ["aussen_whatsapp", "aussen_discord", "aussen_telegram", "aussen_instagram"];

pub(crate) fn ist_externes_panel(id: &str) -> bool {
    EXTERNE_PANEL.contains(&id)
}

pub(crate) fn externes_panel_profil(id: &str) -> Option<&'static str> {
    match id {
        "tab-panel-whatsapp" => Some(EXTERNE_PROFILE[0]),
        "tab-panel-discord" => Some(EXTERNE_PROFILE[1]),
        "tab-panel-telegram" => Some(EXTERNE_PROFILE[2]),
        "tab-panel-instagram" => Some(EXTERNE_PROFILE[3]),
        _ => None,
    }
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
    pub split: Option<SplitZustand>,
    pub rahmen: Rahmen,
    /// Externe Web-App in der Seitenleiste, mit eigenem Rechteck.
    pub panel: Option<PanelZustand>,
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
            split: None,
            rahmen: Rahmen { x: 0.0, y: 0.0, breite: 800.0, hoehe: 600.0 },
            panel: None,
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
        if self.verdeckt {
            return false;
        }
        if self.aktiv.as_deref() == Some(id) {
            return true;
        }
        if let Some(s) = &self.split {
            return s.sekundaer_id == id;
        }
        if let Some(p) = &self.panel {
            return p.id == id;
        }
        false
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
    workspace: Option<String>,
) -> Result<(), String> {
    id_pruefen(&id)?;
    let ziel = ziel_pruefen(&url)?;
    crate::schild::schutz_dienst::bereit()?;
    eintragen(&tabs, &id, privat);
    plattform::laden(&app, &tabs, &id, ziel, privat, workspace.as_deref())
}

#[tauri::command(async)]
pub fn tab_verwerfen(app: AppHandle, id: String) -> Result<(), String> {
    id_pruefen(&id)?;
    #[cfg(windows)]
    {
        desktop::tab_verwerfen(&app, &id);
    }
    #[cfg(target_os = "android")]
    {
        let _ = app;
        let _ = id;
    }
    Ok(())
}

/// Merkt sich einen Tab, bevor seine Webview entsteht.
pub(crate) fn eintragen(tabs: &Tabs, id: &str, privat: bool) {
    let mut z = tabs.0.lock().unwrap();
    let vorne = z.aktiv.as_deref() == Some(id);
    z.tabs.entry(id.to_string()).or_insert(TabDaten { privat, verborgen_seit: (!vorne).then(Instant::now) });
}

/// Vergisst einen Tab, dessen Webview nie entstanden ist (etwa nach einem
/// fehlgeschlagenen Popup-Aufbau). Die Oberfläche hat von ihm nie erfahren.
pub(crate) fn austragen(tabs: &Tabs, id: &str) {
    let mut z = tabs.0.lock().unwrap();
    z.tabs.remove(id);
    if z.aktiv.as_deref() == Some(id) {
        z.aktiv = None;
    }
    if z.vollbild.as_deref() == Some(id) {
        z.vollbild = None;
    }
    if z.panel.as_ref().is_some_and(|p| p.id == id) {
        z.panel = None;
    }
}

/// Bricht einen laufenden Download ab (`nr` aus `TabEreignis::Download`).
/// Gibt an, ob der Abbruch angestoßen wurde. Unter Android lädt der
/// Systemdienst (`Herunterladen.kt`); dort gibt es nichts abzubrechen.
#[tauri::command(async)]
pub fn download_abbrechen(app: AppHandle, nr: u64) -> Result<bool, String> {
    #[cfg(windows)]
    {
        plattform::download_abbrechen(&app, nr)
    }
    #[cfg(target_os = "android")]
    {
        let _ = (app, nr);
        Ok(false)
    }
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

/// Eine Nachricht von `seite.js` aus dem obersten Rahmen, auf beiden
/// Plattformen. `url` ist die Adresse aus der Webview; dass der Absender
/// dieselbe Herkunft hat, ist schon geprüft (Punkt 134).
pub fn nachricht_oben(app: &AppHandle, id: &str, url: &str, roh: &str) {
    if cookies_gemeldet(app, id, roh) {
        return;
    }
    if let Some(stueck) = uebersetzung::lesen(roh) {
        uebersetzung::stueck(app, id, url, stueck);
    } else if let Some(meldung) = formular::lesen(roh) {
        formular::oben(app, id, url, meldung);
    }
}

/// Was `seite.js` schickt, wenn das Cookie-Skript in einem Rahmen der Seite
/// einen Hinweis abgelehnt hat (`cookies/eingang.js`).
pub const COOKIES_ABGELEHNT: &str = r#"{"t":"cookies"}"#;

/// Zählt eine Cookie-Ablehnung, aus dem obersten Rahmen wie aus einem
/// Unterrahmen; `true`, wenn `roh` diese Meldung war.
pub fn cookies_gemeldet(app: &AppHandle, id: &str, roh: &str) -> bool {
    let ja = roh == COOKIES_ABGELEHNT;
    if ja {
        crate::schild::cookies_abgelehnt(app, id);
    }
    ja
}

/// Die Seite im Tab wechselt oder der Tab geht: was an ihr hing, gilt nicht mehr.
pub fn seite_vergessen(id: &str) {
    formular::vergessen(id);
    uebersetzung::vergessen(id);
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
        if z.panel.as_ref().is_some_and(|p| p.id == id) {
            z.panel = None;
        }
    }
    crate::schild::tab_vergessen(&id);
    seite_vergessen(&id);
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

/// Löscht alle Spuren der aktuellen Seite (Cookies, LocalStorage, Cache) und lädt sie neu.
#[tauri::command(async)]
pub fn tab_seite_zuruecksetzen(app: AppHandle, id: String) -> Result<(), String> {
    id_pruefen(&id)?;
    #[cfg(windows)]
    {
        desktop::seite_zuruecksetzen(&app, &id)
    }
    #[cfg(not(windows))]
    {
        plattform::aktion(&app, &id, "neu_laden")
    }
}

/// Wo der Inhaltsbereich im Fenster liegt (logische Pixel).
#[tauri::command(async)]
pub fn tabs_rahmen(app: AppHandle, tabs: State<'_, Tabs>, rahmen: Rahmen) -> Result<(), String> {
    tabs.0.lock().unwrap().rahmen = rahmen;
    plattform::rahmen_anwenden(&app, &tabs);
    Ok(())
}

/// Geometrie einer externen Web-App in der Seitenleiste. `None` versteckt sie
/// nur; die WebView und ihre Sitzung bleiben erhalten.
#[tauri::command(async)]
pub fn panel_rahmen(app: AppHandle, tabs: State<'_, Tabs>, id: String, rahmen: Option<Rahmen>) -> Result<(), String> {
    id_pruefen(&id)?;
    if !ist_externes_panel(&id) {
        return Err("Kein externes Panel".into());
    }
    {
        let mut z = tabs.0.lock().unwrap();
        z.panel = rahmen.map(|rahmen| PanelZustand { id, rahmen });
    }
    plattform::rahmen_anwenden(&app, &tabs);
    plattform::sichtbarkeit(&app, &tabs);
    Ok(())
}

/// Teilt den Anzeigebereich für zwei Webviews gleichzeitig (Desktop & Mobile).
#[tauri::command(async)]
pub fn tabs_split_setzen(
    app: AppHandle,
    tabs: State<'_, Tabs>,
    primaer_id: String,
    primaer_rahmen: Rahmen,
    sekundaer_id: String,
    sekundaer_rahmen: Rahmen,
) -> Result<(), String> {
    id_pruefen(&primaer_id)?;
    id_pruefen(&sekundaer_id)?;
    {
        let mut z = tabs.0.lock().unwrap();
        z.aktiv = Some(primaer_id.clone());
        z.tabs.entry(primaer_id.clone()).or_insert(TabDaten { privat: false, verborgen_seit: None });
        z.tabs.entry(sekundaer_id.clone()).or_insert(TabDaten { privat: false, verborgen_seit: None });
        z.split = Some(SplitZustand {
            sekundaer_id,
            primaer_rahmen,
            sekundaer_rahmen,
        });
    }
    plattform::rahmen_anwenden(&app, &tabs);
    plattform::sichtbarkeit(&app, &tabs);
    Ok(())
}

/// Hebt die geteilte Ansicht auf und stellt den Vollmodus für den aktiven Tab wieder her.
#[tauri::command(async)]
pub fn tabs_split_aufheben(app: AppHandle, tabs: State<'_, Tabs>) -> Result<(), String> {
    {
        let mut z = tabs.0.lock().unwrap();
        z.split = None;
    }
    plattform::rahmen_anwenden(&app, &tabs);
    plattform::sichtbarkeit(&app, &tabs);
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
/// Das Standbild eines Tabs (Vordergrund ohne `id`, sonst der genannte Tab).
/// Für die geteilte Ansicht braucht die Oberfläche beide Seiten.
#[tauri::command(async)]
pub fn tab_standbild(app: AppHandle, tabs: State<'_, Tabs>, id: Option<String>) -> Result<tauri::ipc::Response, String> {
    if let Some(id) = &id {
        id_pruefen(id)?;
    }
    plattform::standbild(&app, &tabs, id.as_deref()).map(tauri::ipc::Response::new)
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

/// Lagert einen Tab in ein neues, separates Browserfenster aus (Desktop/Windows).
#[cfg(desktop)]
#[tauri::command(async)]
pub fn tab_auslagern(url: String, x: i32, y: i32, privat: Option<bool>) -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| format!("Executable nicht ermittelbar: {e}"))?;
    let mut cmd = std::process::Command::new(exe);
    cmd.arg("--neues-fenster");
    if !url.is_empty() {
        cmd.arg(&url);
    }
    #[cfg(windows)]
    let (x, y) = {
        use windows::Win32::Foundation::POINT;
        use windows::Win32::UI::WindowsAndMessaging::GetCursorPos;
        let mut pt = POINT::default();
        if unsafe { GetCursorPos(&mut pt) }.is_ok() {
            (pt.x, pt.y)
        } else {
            (x, y)
        }
    };
    cmd.arg("--x").arg(x.to_string());
    cmd.arg("--y").arg(y.to_string());
    if privat.unwrap_or(false) {
        cmd.arg("--privat");
    }
    #[cfg(windows)]
    unsafe {
        use windows::Win32::UI::WindowsAndMessaging::{AllowSetForegroundWindow, ASFW_ANY};
        let _ = AllowSetForegroundWindow(ASFW_ANY);
    }
    cmd.spawn().map_err(|e| format!("Fehler beim Starten des neuen Fensters: {e}"))?;
    Ok(())
}

#[cfg(not(desktop))]
#[tauri::command(async)]
pub fn tab_auslagern(_url: String, _x: i32, _y: i32, _privat: Option<bool>) -> Result<(), String> {
    Err("Tabs auslagern gibt es nur auf dem Desktop".into())
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

/// Einstellungen → Design: Seiten bevorzugt dunkel rendern. Windows setzt das
/// Farbschema des WebView2-Profils, Android erlaubt das algorithmische
/// Abdunkeln der WebView. Eine Einstellung, beide Plattformen.
#[tauri::command(async)]
pub fn tabs_darstellung(app: AppHandle, an: bool) -> Result<(), String> {
    plattform::seiten_dunkel(&app, an)
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

    #[test]
    fn externe_panel_kennungen_sind_begrenzt() {
        assert!(ist_externes_panel("tab-panel-whatsapp"));
        assert!(ist_externes_panel("tab-panel-discord"));
        assert!(ist_externes_panel("tab-panel-telegram"));
        assert!(ist_externes_panel("tab-panel-instagram"));
        assert!(!ist_externes_panel("tab-panel-youtube"));
        assert!(!ist_externes_panel("tab-a"));
    }

    #[test]
    fn externe_dienste_haben_getrennte_profile() {
        assert_eq!(externes_panel_profil("tab-panel-whatsapp"), Some("aussen_whatsapp"));
        assert_eq!(externes_panel_profil("tab-panel-discord"), Some("aussen_discord"));
        assert_eq!(externes_panel_profil("tab-panel-telegram"), Some("aussen_telegram"));
        assert_eq!(externes_panel_profil("tab-panel-instagram"), Some("aussen_instagram"));
        assert_eq!(externes_panel_profil("tab-a"), None);
        let eindeutig: std::collections::HashSet<_> = EXTERNE_PROFILE.iter().collect();
        assert_eq!(eindeutig.len(), EXTERNE_PROFILE.len(), "Profile sind eindeutig");
        assert_eq!(EXTERNE_PROFILE.len(), 4);
    }

    #[test]
    fn panel_bleibt_neben_dem_haupttab_sichtbar_und_wird_aufgeraeumt() {
        let rahmen = Rahmen { x: 1.0, y: 2.0, breite: 300.0, hoehe: 400.0 };
        let mut z = Zustand { aktiv: Some("tab-a".into()), ..Zustand::default() };
        assert!(!z.sichtbar("tab-panel-whatsapp"));
        z.panel = Some(PanelZustand { id: "tab-panel-whatsapp".into(), rahmen });
        assert!(z.sichtbar("tab-panel-whatsapp"));
        assert!(z.sichtbar("tab-a"));
        z.verdeckt = true;
        assert!(!z.sichtbar("tab-panel-whatsapp"));
        z.verdeckt = false;
        z.vollbild = Some("tab-b".into());
        assert!(!z.sichtbar("tab-panel-whatsapp"));
        z.vollbild = Some("tab-panel-whatsapp".into());
        assert!(z.sichtbar("tab-panel-whatsapp"));

        let tabs = Tabs::default();
        tabs.0.lock().unwrap().panel = Some(PanelZustand { id: "tab-panel-whatsapp".into(), rahmen });
        austragen(&tabs, "tab-panel-whatsapp");
        assert!(tabs.0.lock().unwrap().panel.is_none());
    }
}
