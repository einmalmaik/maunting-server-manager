//! Tabs unter Windows: je Tab ein eigener WebView2-Controller im Hauptfenster.
//!
//! Bewusst nicht über Tauri. Jede Webview, die Tauri anlegt, bekommt die
//! IPC-Brücke (`__TAURI_INTERNALS__`) und die Skripte aller Plugins: in einer
//! fremden Seite gingen `alert`, `confirm` und `Notification` an Tauri statt
//! an den Browser, und jede Seite sah die Brücke (bis 10/2026). Die Befehle
//! scheiterten zwar an den Rechten, aber eine Seite soll die Brücke gar nicht
//! erst haben. Hier gibt es keine: kein Skript von Tauri, kein
//! `ipc:`-Protokoll. Web-Nachrichten hat nur `seite.js` (`formulare.rs`).
//!
//! WebView2 will alles auf dem UI-Faden. Die Befehle kommen aus Tauris
//! Fadenpool und reichen ihre Arbeit mit `run_on_main_thread` hinüber; die
//! Controller liegen in einem `thread_local` dieses Fadens. Was danach
//! passiert (Laden, Titel, Downloads), melden Rückrufe der WebView2; der Rest
//! hängt in `webview2.rs`.

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::mpsc;
use std::time::Duration;

use tauri::{AppHandle, Manager};
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    AddScriptToExecuteOnDocumentCreatedCompletedHandler, CreateCoreWebView2ControllerCompletedHandler, CreateCoreWebView2EnvironmentCompletedHandler,
};
use windows::core::{Interface, HSTRING, PCWSTR};
use windows::Win32::Foundation::{E_POINTER, HWND, RECT};

use super::{melden, TabEreignis, Tabs};

mod absturz;
mod cookies;
mod entwickler;
mod formulare;
mod grundereignisse;
mod herunterladen;
mod ohne_edge;
mod rueckfragen;
mod schlaf;
mod standbild;
mod webview2;

pub(crate) use rueckfragen::{recht_art, recht_name};
pub use cookies::erneuern as cookies_erneuern;
pub use schlaf::{ruhen, stumm};

pub(crate) struct Nativ {
    pub controller: ICoreWebView2Controller,
    pub core: ICoreWebView2,
    pub umgebung: ICoreWebView2Environment,
    /// InPrivate: eigenes Profil im Speicher, zählt nicht für `im_profil`.
    pub privat: bool,
}

thread_local! {
    static UMGEBUNG: RefCell<Option<ICoreWebView2Environment>> = const { RefCell::new(None) };
    static NATIV: RefCell<HashMap<String, Nativ>> = RefCell::new(HashMap::new());
    static HELFER: RefCell<Option<ICoreWebView2Controller>> = const { RefCell::new(None) };
}

/// Wie in wry: ohne Mini-Menü bei Markierungen und ohne SmartScreen, das jede
/// Adresse an Microsoft schickt.
const BROWSER_ARGUMENTE: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection";

/// Länger wartet ein Befehl nicht auf den UI-Faden.
const FRIST: Duration = Duration::from_secs(20);

fn fehler(e: impl std::fmt::Display) -> String {
    e.to_string()
}

fn kein_zeiger() -> windows::core::Error {
    windows::core::Error::from_hresult(E_POINTER)
}

/// Führt `f` auf dem UI-Faden aus und wartet auf das Ergebnis. Nie vom
/// UI-Faden selbst aufrufen.
pub(crate) fn auf_ui<T: Send + 'static>(
    app: &AppHandle,
    f: impl FnOnce() -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    let (tx, rx) = mpsc::channel();
    app.run_on_main_thread(move || {
        let _ = tx.send(f());
    })
    .map_err(fehler)?;
    rx.recv_timeout(FRIST).map_err(|_| "Das Fenster hat nicht geantwortet".to_string())?
}

/// Wie [`auf_ui`], mit den Schnittstellen eines Tabs.
pub(crate) fn mit_tab<T: Send + 'static>(
    app: &AppHandle,
    id: &str,
    f: impl FnOnce(&Nativ) -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    let id = id.to_string();
    auf_ui(app, move || {
        NATIV.with(|n| n.borrow().get(&id).ok_or_else(|| "Der Tab hat keine Seite".to_string()).and_then(f))
    })
}

/// Die Umgebung aller Tabs: ein eigenes Profil (`seitenprofil`), getrennt von
/// dem der Oberfläche. Nur auf dem UI-Faden.
fn umgebung(app: &AppHandle) -> Result<ICoreWebView2Environment, String> {
    if let Some(u) = UMGEBUNG.with(|u| u.borrow().clone()) {
        return Ok(u);
    }
    let profil = super::seitenprofil(app)?;
    let (tx, rx) = mpsc::channel();
    let optionen = webview2_com::CoreWebView2EnvironmentOptions::default();
    unsafe {
        optionen.set_additional_browser_arguments(BROWSER_ARGUMENTE.to_string());
        // Absturzberichte der Tabs bleiben auf dem Rechner (Datenschutzerklärung 3.30).
        optionen.set_is_custom_crash_reporting_enabled(true);
        CreateCoreWebView2EnvironmentWithOptions(
            PCWSTR::null(),
            &HSTRING::from(profil.as_os_str()),
            &ICoreWebView2EnvironmentOptions::from(optionen),
            &CreateCoreWebView2EnvironmentCompletedHandler::create(Box::new(move |ergebnis, umgebung| {
                let _ = tx.send(ergebnis.and_then(|()| umgebung.ok_or_else(kein_zeiger)));
                Ok(())
            })),
        )
        .map_err(fehler)?;
    }
    let u = webview2_com::wait_with_pump(rx).map_err(fehler)?.map_err(fehler)?;
    UMGEBUNG.with(|slot| *slot.borrow_mut() = Some(u.clone()));
    Ok(u)
}

/// Ein unsichtbarer Controller im Hauptfenster. Nur auf dem UI-Faden.
fn controller(app: &AppHandle, umgebung: &ICoreWebView2Environment, privat: bool) -> Result<ICoreWebView2Controller, String> {
    let fenster = app.get_webview_window("main").ok_or("Hauptfenster fehlt")?;
    let hwnd = HWND(fenster.hwnd().map_err(fehler)?.0);
    let (tx, rx) = mpsc::channel();
    let fertig = CreateCoreWebView2ControllerCompletedHandler::create(Box::new(move |ergebnis, controller| {
        let _ = tx.send(ergebnis.and_then(|()| controller.ok_or_else(kein_zeiger)));
        Ok(())
    }));
    unsafe {
        let u10 = umgebung.cast::<ICoreWebView2Environment10>().map_err(fehler)?;
        let optionen = u10.CreateCoreWebView2ControllerOptions().map_err(fehler)?;
        optionen.SetIsInPrivateModeEnabled(privat).map_err(fehler)?;
        u10.CreateCoreWebView2ControllerWithOptions(hwnd, &optionen, &fertig).map_err(fehler)?;
    }
    let controller = webview2_com::wait_with_pump(rx).map_err(fehler)?.map_err(fehler)?;
    unsafe { controller.SetIsVisible(false).map_err(fehler)? };
    Ok(controller)
}

pub fn laden(app: &AppHandle, tabs: &Tabs, id: &str, ziel: url::Url, privat: bool) -> Result<(), String> {
    let (app2, id2) = (app.clone(), id.to_string());
    auf_ui(app, move || {
        let da = NATIV.with(|n| n.borrow().get(&id2).map(|t| t.core.clone()));
        let core = match da {
            Some(core) => core,
            None => anlegen(&app2, &id2, privat)?,
        };
        unsafe { core.Navigate(&HSTRING::from(ziel.as_str())).map_err(fehler) }
    })?;
    rahmen_anwenden(app, tabs);
    sichtbarkeit(app, tabs);
    Ok(())
}

/// Legt den Controller eines Tabs an und hängt alles daran. UI-Faden.
fn anlegen(app: &AppHandle, id: &str, privat: bool) -> Result<ICoreWebView2, String> {
    let umgebung = umgebung(app)?;
    let controller = controller(app, &umgebung, privat)?;
    let core = unsafe { controller.CoreWebView2().map_err(fehler)? };
    unsafe {
        let einstellungen = core.Settings().map_err(fehler)?;
        // Web-Nachrichten nur für `seite.js`: es fängt `chrome.webview` ein,
        // bevor die Seite läuft, und nimmt es ihr weg.
        einstellungen.SetIsWebMessageEnabled(true).map_err(fehler)?;
        einstellungen.SetAreHostObjectsAllowed(false).map_err(fehler)?;
        // Beide Skripte gelten erst, wenn WebView2 die Anmeldung bestätigt hat.
        // `laden` navigiert gleich danach; kam die erste Seite schnell (Cache),
        // lief sie sonst ohne `seite.js` und sah `chrome.webview` (bis 10/2026).
        let (tx, rx) = mpsc::channel();
        core.AddScriptToExecuteOnDocumentCreated(
            &HSTRING::from(crate::seite::SKRIPT),
            &AddScriptToExecuteOnDocumentCreatedCompletedHandler::create(Box::new(move |ergebnis, _| {
                let _ = tx.send(ergebnis);
                Ok(())
            })),
        )
        .map_err(fehler)?;
        webview2_com::wait_with_pump(rx).map_err(fehler)?.map_err(fehler)?;
        let cookies_da = cookies::anmelden(id, &core).map_err(fehler)?;
        webview2_com::wait_with_pump(cookies_da).map_err(fehler)?.map_err(fehler)?;
        grundereignisse::anbinden(app, id, &core).map_err(fehler)?;
        formulare::anbinden(app, id, &core).map_err(fehler)?;
        schlaf::anbinden(app, id, &core).map_err(fehler)?;
        webview2::einrichten(app, id, controller.clone(), umgebung.clone()).map_err(fehler)?;
    }
    NATIV.with(|n| n.borrow_mut().insert(id.to_string(), Nativ { controller, core: core.clone(), umgebung, privat }));
    Ok(core)
}

/// Zeigt den vorderen Tab, versteckt alle anderen. Der vordere wacht auf,
/// auch wenn ein Dialog ihn gerade verdeckt (`schlaf.rs`).
pub fn sichtbarkeit(app: &AppHandle, tabs: &Tabs) {
    let stand: HashMap<String, (bool, bool)> = {
        let z = tabs.0.lock().unwrap();
        let vorne = |id: &str| z.vollbild.as_deref().or(z.aktiv.as_deref()) == Some(id);
        z.tabs.keys().map(|id| (id.clone(), (z.sichtbar(id), vorne(id)))).collect()
    };
    let app2 = app.clone();
    let _ = app.run_on_main_thread(move || {
        NATIV.with(|n| {
            for (id, tab) in n.borrow().iter() {
                let (zeigen, vorne) = stand.get(id).copied().unwrap_or((false, false));
                unsafe {
                    schlaf::zeigen(&app2, id, tab, vorne);
                    let _ = tab.controller.SetIsVisible(zeigen);
                }
            }
        })
    });
    oberflaeche_nach_unten(app);
}

/// Die Oberfläche ist selbst ein Kindfenster des Hauptfensters, so groß wie
/// das Fenster. Rückt sie in der Z-Reihenfolge vor die Tabs (Fokus, Neuaufbau
/// durch wry), deckt sie die Seite zu: geladen, aber nicht zu sehen. Deshalb
/// liegt sie nach jedem Zeigen ganz unten.
fn oberflaeche_nach_unten(app: &AppHandle) {
    use windows::Win32::UI::WindowsAndMessaging::{SetWindowPos, HWND_BOTTOM, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE};
    if let Some(oberflaeche) = app.get_webview_window("main") {
        let _ = oberflaeche.with_webview(|p| unsafe {
            let mut hwnd = HWND::default();
            if p.controller().ParentWindow(&mut hwnd).is_ok() && !hwnd.is_invalid() {
                let _ = SetWindowPos(hwnd, Some(HWND_BOTTOM), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
            }
        });
    }
}

fn rechteck(app: &AppHandle, x: f64, y: f64, breite: f64, hoehe: f64) -> RECT {
    let faktor = app.get_webview_window("main").and_then(|f| f.scale_factor().ok()).unwrap_or(1.0);
    let p = |v: f64| (v * faktor).round() as i32;
    RECT { left: p(x), top: p(y), right: p(x + breite), bottom: p(y + hoehe) }
}

pub fn rahmen_anwenden(app: &AppHandle, tabs: &Tabs) {
    let (r, vollbild) = {
        let z = tabs.0.lock().unwrap();
        (z.rahmen, z.vollbild.clone())
    };
    if vollbild.is_some() {
        return;
    }
    let rechteck = rechteck(app, r.x, r.y, r.breite, r.hoehe);
    let _ = app.run_on_main_thread(move || {
        NATIV.with(|n| {
            for tab in n.borrow().values() {
                unsafe {
                    let _ = tab.controller.SetBounds(rechteck);
                }
            }
        })
    });
    oberflaeche_nach_unten(app);
}

pub fn schliessen(app: &AppHandle, id: &str) {
    let id = id.to_string();
    let _ = app.run_on_main_thread(move || {
        rueckfragen::vergessen(&id);
        cookies::vergessen(&id);
        formulare::vergessen(&id);
        if let Some(tab) = NATIV.with(|n| n.borrow_mut().remove(&id)) {
            unsafe {
                let _ = tab.controller.Close();
            }
        }
    });
}

/// Zurück, Vor, Neu laden, Anhalten (`tab_aktion`).
pub fn aktion(app: &AppHandle, id: &str, aktion: &str) -> Result<(), String> {
    let aktion = aktion.to_string();
    mit_tab(app, id, move |tab| unsafe {
        match aktion.as_str() {
            "zurueck" => tab.core.GoBack(),
            "vor" => tab.core.GoForward(),
            "neu_laden" => tab.core.Reload(),
            _ => tab.core.Stop(),
        }
        .map_err(fehler)
    })
}

/// Ein Tab will den ganzen Bildschirm (Video) oder gibt ihn zurück. Läuft als
/// Rückruf der WebView2 schon auf dem UI-Faden.
pub fn vollbild(app: &AppHandle, id: &str, an: bool) {
    let tabs = app.state::<Tabs>();
    tabs.0.lock().unwrap().vollbild = if an { Some(id.to_string()) } else { None };
    let Some(fenster) = app.get_webview_window("main") else { return };
    let _ = fenster.set_fullscreen(an);
    if an {
        // Die Größe des Bildschirms, nicht die des Fensters: das Fenster ist in
        // diesem Moment noch nicht umgestellt.
        if let Ok(Some(bildschirm)) = fenster.current_monitor() {
            let g = bildschirm.size();
            let ganz = RECT { left: 0, top: 0, right: g.width as i32, bottom: g.height as i32 };
            NATIV.with(|n| {
                if let Some(tab) = n.borrow().get(id) {
                    unsafe {
                        let _ = tab.controller.SetBounds(ganz);
                    }
                }
            });
        }
    } else {
        rahmen_anwenden(app, &tabs);
    }
    sichtbarkeit(app, &tabs);
    melden(app, TabEreignis::Vollbild { id: id.to_string(), an });
}

/// Ein JPEG des vorderen Tabs, siehe `standbild.rs`.
pub fn standbild(app: &AppHandle, tabs: &Tabs) -> Result<Vec<u8>, String> {
    let aktiv = tabs.0.lock().unwrap().aktiv.clone().ok_or("Kein Tab vorne")?;
    standbild::aufnehmen(app, &aktiv)
}

pub fn antworten(app: &AppHandle, nr: u64, antwort: super::Antwort) -> Result<(), String> {
    app.run_on_main_thread(move || rueckfragen::erledigen(nr, antwort)).map_err(fehler)
}

pub fn protokoll(app: &AppHandle, id: &str, methode: String, parameter: serde_json::Value) -> Result<serde_json::Value, String> {
    entwickler::rufen(app, id, methode, parameter)
}

pub fn suchen(app: &AppHandle, id: &str, richtung: String, begriff: String) -> Result<(), String> {
    ohne_edge::suchen(app, id, richtung, begriff)
}

pub fn fuellen(app: &AppHandle, id: &str, fuer: &str, nachricht: String) -> Result<(), String> {
    formulare::fuellen(app, id, fuer, nachricht)
}

pub fn fuellen_rahmen(app: &AppHandle, id: &str, fuer: &str, rahmen: &str, nachricht: String) -> Result<(), String> {
    formulare::fuellen_rahmen(app, id, fuer, rahmen, nachricht)
}

pub fn drucken(app: &AppHandle, id: &str) -> Result<(), String> {
    ohne_edge::drucken(app, id)
}

/// Der Fokus geht an die Webview der Oberfläche, nicht nur an das Fenster:
/// das hat ihn schon, während eine Seite tippt.
pub fn oberflaeche_fokussieren(app: &AppHandle) {
    if let Some(oberflaeche) = app.get_webview_window("main") {
        let _ = oberflaeche.with_webview(|p| unsafe {
            let _ = p.controller().MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
        });
    }
    oberflaeche_nach_unten(app);
}

/// Ein Kern im Profil der Seiten, für Arbeit ohne Tab (Browserdaten löschen):
/// ein offener Tab oder ein unsichtbarer Helfer, der bis zum nächsten Aufruf
/// bleibt. `f` startet die Arbeit nur; warten muss der Aufrufer.
pub(crate) fn im_profil(app: &AppHandle, f: impl FnOnce(&ICoreWebView2) -> Result<(), String> + Send + 'static) -> Result<(), String> {
    let app2 = app.clone();
    auf_ui(app, move || {
        if let Some(core) = NATIV.with(|n| n.borrow().values().find(|t| !t.privat).map(|t| t.core.clone())) {
            return f(&core);
        }
        let umgebung = umgebung(&app2)?;
        let helfer = controller(&app2, &umgebung, false)?;
        let ergebnis = unsafe { helfer.CoreWebView2().map_err(fehler) }.and_then(|core| f(&core));
        HELFER.with(|h| {
            if let Some(alt) = h.borrow_mut().replace(helfer) {
                unsafe {
                    let _ = alt.Close();
                }
            }
        });
        ergebnis
    })
}
