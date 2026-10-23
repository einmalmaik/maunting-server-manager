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
use std::sync::atomic::{AtomicBool, Ordering};
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
mod erweiterungen;
mod formulare;
mod grundereignisse;
mod herunterladen;
mod ohne_edge;
mod popup;
mod rueckfragen;
mod schlaf;
mod standbild;
mod webview2;

pub(crate) use rueckfragen::{recht_art, recht_name};
pub use cookies::erneuern as cookies_erneuern;
pub use popup::{oeffnen as erweiterung_popup, schliessen as erweiterung_popup_schliessen};
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

/// Einstellungen → Design: Seiten dunkel rendern. Gilt für das WebView2-Profil
/// aller Tabs, auch für Tabs, die erst nach der Einstellung entstehen.
static SEITEN_DUNKEL: AtomicBool = AtomicBool::new(false);

/// Wie in wry: ohne Mini-Menü bei Markierungen und ohne SmartScreen, das jede
/// Adresse an Microsoft schickt.
const BROWSER_ARGUMENTE: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection";

/// Länger wartet ein Befehl nicht auf den UI-Faden.
const FRIST: Duration = Duration::from_secs(20);

/// Die Kulisse, wo eine Seite nichts malt: deckendes dunkles Neutralgrau.
/// WebView2 füllt jede unbemalte Fläche mit `DefaultBackgroundColor`, dessen
/// Vorgabe deckendes Weiß ist. Das Weiß lag in der Geräteansicht der
/// Entwicklerwerkzeuge neben der verkleinerten Seite (10/2026). Deckend,
/// damit kein Fenster darunter durchscheint; dunkel wie die Kulisse von
/// Chrome, in beiden Farbschemen gleich.
const KULISSE: COREWEBVIEW2_COLOR = COREWEBVIEW2_COLOR { A: 0xFF, R: 0x20, G: 0x20, B: 0x20 };

/// Legt die Kulisse auf einen Controller. Nur auf dem UI-Faden.
pub(super) fn grundieren(controller: &ICoreWebView2Controller) -> Result<(), String> {
    unsafe {
        controller.cast::<ICoreWebView2Controller2>().map_err(fehler)?.SetDefaultBackgroundColor(KULISSE).map_err(fehler)
    }
}

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
    let mut args = BROWSER_ARGUMENTE.to_string();
    let gpu_an = app
        .try_state::<crate::konfig::KonfigZustand>()
        .and_then(|z| z.0.lock().ok().map(|k| k.hardwarebeschleunigung))
        .unwrap_or(true);
    if !gpu_an {
        args.push_str(" --disable-gpu");
    }
    unsafe {
        optionen.set_additional_browser_arguments(args);
        // Absturzberichte der Tabs bleiben auf dem Rechner (Datenschutzerklärung 3.30).
        optionen.set_is_custom_crash_reporting_enabled(true);
        // Gilt je Umgebung und lässt sich später nicht umschalten. Ohne
        // installierte Erweiterung ändert es nichts (`crate::erweiterungen`).
        optionen.set_are_browser_extensions_enabled(true);
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
pub(super) fn controller(app: &AppHandle, umgebung: &ICoreWebView2Environment, privat: bool, workspace: Option<&str>) -> Result<ICoreWebView2Controller, String> {
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
        if !privat {
            if let Some(ws) = workspace {
                let saubere_id: String = ws.chars().filter(|c| c.is_alphanumeric() || *c == '_' || *c == '-').collect();
                if !saubere_id.is_empty() && saubere_id != "standard" && saubere_id != "default" {
                    optionen.SetProfileName(&HSTRING::from(format!("ws_{saubere_id}"))).map_err(fehler)?;
                }
            }
        }
        u10.CreateCoreWebView2ControllerWithOptions(hwnd, &optionen, &fertig).map_err(fehler)?;
    }
    let controller = webview2_com::wait_with_pump(rx).map_err(fehler)?.map_err(fehler)?;
    unsafe { controller.SetIsVisible(false).map_err(fehler)? };
    grundieren(&controller)?;
    Ok(controller)
}

/// Setzt das bevorzugte Farbschema des WebView2-Profils. Wirkt live auf alle
/// Tabs dieses Profils, ohne Neustart. Ältere Runtimes ohne die Schnittstelle
/// bleiben unverändert; der Aufrufer protokolliert das nur.
fn farbschema_setzen(core: &ICoreWebView2) -> Result<(), String> {
    unsafe {
        let profil = core.cast::<ICoreWebView2_13>().map_err(fehler)?.Profile().map_err(fehler)?;
        let p3 = profil.cast::<ICoreWebView2Profile3>().map_err(fehler)?;
        let schema = if SEITEN_DUNKEL.load(Ordering::Relaxed) {
            COREWEBVIEW2_PREFERRED_COLOR_SCHEME_DARK
        } else {
            COREWEBVIEW2_PREFERRED_COLOR_SCHEME_LIGHT
        };
        p3.SetPreferredColorScheme(schema).map_err(fehler)
    }
}

/// Setzt das Farbschema für alle laufenden Tabs. Neue Tabs lesen den Wert beim
/// Aufbau in `anlegen`; private Tabs haben ein eigenes Profil und werden dort
/// ebenfalls gesetzt.
pub fn seiten_dunkel(app: &AppHandle, an: bool) -> Result<(), String> {
    SEITEN_DUNKEL.store(an, Ordering::Relaxed);
    app.run_on_main_thread(move || {
        let cores = NATIV.with(|n| n.borrow().values().map(|t| t.core.clone()).collect::<Vec<_>>());
        for core in cores {
            if let Err(e) = farbschema_setzen(&core) {
                eprintln!("[MSB] Farbschema nicht gesetzt: {e}");
            }
        }
    })
    .map_err(|e| e.to_string())
}

pub fn laden(app: &AppHandle, tabs: &Tabs, id: &str, ziel: url::Url, privat: bool, workspace: Option<&str>) -> Result<(), String> {
    let (app2, id2) = (app.clone(), id.to_string());
    let ws = workspace.map(|s| s.to_string());
    auf_ui(app, move || {
        let da = NATIV.with(|n| n.borrow().get(&id2).map(|t| t.core.clone()));
        let core = match da {
            Some(core) => core,
            None => anlegen(&app2, &id2, privat, ws.as_deref())?,
        };
        unsafe { core.Navigate(&HSTRING::from(ziel.as_str())).map_err(fehler) }
    })?;
    rahmen_anwenden(app, tabs);
    sichtbarkeit(app, tabs);
    Ok(())
}

pub fn tab_verwerfen(app: &AppHandle, id: &str) {
    let (app2, id2) = (app.clone(), id.to_string());
    let _ = app.run_on_main_thread(move || {
        schlaf::verwerfen(&app2, &id2);
    });
}

/// Chrome-Kennung für die externen Panels, mit der installierten WebView2-Version.
fn chrome_user_agent(version: &str) -> Option<String> {
    let version = version.trim();
    if version.is_empty() || !version.chars().all(|c| c.is_ascii_digit() || c == '.') {
        return None;
    }
    Some(format!("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"))
}

/// Legt den Controller eines Tabs an und hängt alles daran. UI-Faden.
fn anlegen(app: &AppHandle, id: &str, privat: bool, workspace: Option<&str>) -> Result<ICoreWebView2, String> {
    let umgebung = umgebung(app)?;
    let controller = controller(app, &umgebung, privat, workspace)?;
    let core = unsafe { controller.CoreWebView2().map_err(fehler)? };
    if let Err(e) = farbschema_setzen(&core) {
        eprintln!("[MSB] Farbschema nicht gesetzt: {e}");
    }
    unsafe {
        let einstellungen = core.Settings().map_err(fehler)?;
        // Externe Dienste sollen einen aktuellen Chrome statt eines Edge sehen.
        if super::ist_externes_panel(id) {
            let mut roh = windows::core::PWSTR::null();
            if umgebung.BrowserVersionString(&mut roh).is_ok() {
                if let Some(ua) = chrome_user_agent(&webview2_com::take_pwstr(roh)) {
                    if let Ok(s2) = einstellungen.cast::<ICoreWebView2Settings2>() {
                        let _ = s2.SetUserAgent(&HSTRING::from(ua));
                    }
                }
            }
        }
        // Web-Nachrichten nur für `seite.js`: es fängt `chrome.webview` ein,
        // bevor die Seite läuft, und nimmt es ihr weg.
        einstellungen.SetIsWebMessageEnabled(true).map_err(fehler)?;
        einstellungen.SetAreHostObjectsAllowed(false).map_err(fehler)?;
        // Beide Skripte gelten erst, wenn WebView2 die Anmeldung bestätigt hat.
        // `laden` navigiert gleich danach; kam die erste Seite schnell (Cache),
        // lief sie sonst ohne `seite.js` und sah `chrome.webview` (bis 10/2026).
        let (tx, rx) = mpsc::channel();
        core.AddScriptToExecuteOnDocumentCreated(
            &HSTRING::from(crate::seite::SKRIPT.as_str()),
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
        herunterladen::anbinden(app, id, &core, privat).map_err(fehler)?;
        webview2::einrichten(app, id, controller.clone(), umgebung.clone()).map_err(fehler)?;
        // Der erste normale Tab gleicht die Erweiterungen an, bevor er lädt:
        // was ohne offenen Tab geändert wurde (Jugendschutz), gilt sonst erst
        // auf der zweiten Seite. Private Tabs haben keine.
        if !privat && !NATIV.with(|n| n.borrow().values().any(|t| !t.privat)) {
            match erweiterungen::abgleichen(&core, &crate::erweiterungen::soll(app)) {
                Ok(laufen) => super::erweiterungen_setzen(laufen),
                Err(e) => eprintln!("[MSB] Erweiterungen nicht abgeglichen: {e}"),
            }
        }
    }
    NATIV.with(|n| n.borrow_mut().insert(id.to_string(), Nativ { controller, core: core.clone(), umgebung, privat }));
    Ok(core)
}

/// Kennung für einen Popup-Tab (`tab-p…`). Vergeben schon beim Einplanen des
/// Fenster-Rückrufs, damit der stufenweise Aufbau sie ab dem ersten Schritt
/// kennt (der Rückruf kehrt zurück, bevor der Controller steht).
pub(super) fn popup_id() -> String {
    static NAECHSTE_TAB_NR: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    let jetzt = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0);
    let nr = NAECHSTE_TAB_NR.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    format!("tab-p{}-{}", jetzt, nr)
}

/// Hängt alles an den Kern eines Popup-Tabs: die Skripte gelten schon (der
/// stufenweise Aufbau in `grundereignisse` hat ihre Rückrufe abgewartet), hier
/// kommen nur die Anbindungen dazu, ohne jedes Warten. UI-Faden.
pub(super) fn popup_einrichten(
    app: &AppHandle,
    id: &str,
    controller: ICoreWebView2Controller,
    core: &ICoreWebView2,
    umgebung: ICoreWebView2Environment,
    privat: bool,
) -> Result<(), String> {
    unsafe {
        // Wie in `anlegen`: `seite.js` braucht die Nachrichten, die Seite
        // bekommt keine Host-Objekte.
        let einstellungen = core.Settings().map_err(fehler)?;
        einstellungen.SetIsWebMessageEnabled(true).map_err(fehler)?;
        einstellungen.SetAreHostObjectsAllowed(false).map_err(fehler)?;
        grundereignisse::anbinden(app, id, core).map_err(fehler)?;
        formulare::anbinden(app, id, core).map_err(fehler)?;
        schlaf::anbinden(app, id, core).map_err(fehler)?;
        herunterladen::anbinden(app, id, core, privat).map_err(fehler)?;
        webview2::einrichten(app, id, controller.clone(), umgebung.clone()).map_err(fehler)?;
    }
    NATIV.with(|n| n.borrow_mut().insert(id.to_string(), Nativ { controller, core: core.clone(), umgebung, privat }));
    Ok(())
}

/// Liest das Profil eines Tabs auf dem UI-Faden (für den Popup-Aufbau, bevor
/// der Rückruf zurückkehrt).
pub(super) fn tab_privat(eltern_id: &str) -> bool {
    NATIV.with(|n| n.borrow().get(eltern_id).map(|t| t.privat).unwrap_or(false))
}

/// Stößt den Abbruch eines laufenden Downloads an (`nr` aus
/// `TabEreignis::Download`). Der Abbruch selbst läuft auf dem UI-Faden.
pub fn download_abbrechen(app: &AppHandle, nr: u64) -> Result<bool, String> {
    auf_ui(app, move || Ok(herunterladen::abbrechen(nr)))
}

/// Zeigt den vorderen Tab, versteckt alle anderen. Der vordere wacht auf,
/// auch wenn ein Dialog ihn gerade verdeckt (`schlaf.rs`).
pub fn sichtbarkeit(app: &AppHandle, tabs: &Tabs) {
    let stand: HashMap<String, (bool, bool)> = {
        let z = tabs.0.lock().unwrap();
        let vorne = |id: &str| {
            if z.panel.as_ref().is_some_and(|p| p.id == id) {
                return z.sichtbar(id);
            }
            if let Some(v) = &z.vollbild {
                v == id
            } else if let Some(s) = &z.split {
                z.aktiv.as_deref() == Some(id) || s.sekundaer_id == id
            } else {
                z.aktiv.as_deref() == Some(id)
            }
        };
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
    let (r, split, aktiv, vollbild, panel) = {
        let z = tabs.0.lock().unwrap();
        (z.rahmen, z.split.clone(), z.aktiv.clone(), z.vollbild.clone(), z.panel.clone())
    };
    if vollbild.is_some() {
        return;
    }
    let panel_rect = panel.as_ref().map(|p| rechteck(app, p.rahmen.x, p.rahmen.y, p.rahmen.breite, p.rahmen.hoehe));
    if let Some(s) = split {
        let rect_primaer = rechteck(app, s.primaer_rahmen.x, s.primaer_rahmen.y, s.primaer_rahmen.breite, s.primaer_rahmen.hoehe);
        let rect_sekundaer = rechteck(app, s.sekundaer_rahmen.x, s.sekundaer_rahmen.y, s.sekundaer_rahmen.breite, s.sekundaer_rahmen.hoehe);
        let default_rect = rechteck(app, r.x, r.y, r.breite, r.hoehe);
        let primaer_id = aktiv.unwrap_or_default();
        let _ = app.run_on_main_thread(move || {
            NATIV.with(|n| {
                for (id, tab) in n.borrow().iter() {
                    let ziel_rect = if let (Some(panel), Some(rect)) = (&panel, panel_rect) {
                        if id == &panel.id {
                            rect
                        } else if !primaer_id.is_empty() && id == &primaer_id {
                            rect_primaer
                        } else if id == &s.sekundaer_id {
                            rect_sekundaer
                        } else {
                            default_rect
                        }
                    } else if !primaer_id.is_empty() && id == &primaer_id {
                        rect_primaer
                    } else if id == &s.sekundaer_id {
                        rect_sekundaer
                    } else {
                        default_rect
                    };
                    unsafe {
                        let _ = tab.controller.SetBounds(ziel_rect);
                    }
                }
            })
        });
    } else {
        let rechteck = rechteck(app, r.x, r.y, r.breite, r.hoehe);
        let _ = app.run_on_main_thread(move || {
            NATIV.with(|n| {
                for (id, tab) in n.borrow().iter() {
                    let ziel_rect = if let (Some(panel), Some(rect)) = (&panel, panel_rect) {
                        if id == &panel.id { rect } else { rechteck }
                    } else {
                        rechteck
                    };
                    unsafe {
                        let _ = tab.controller.SetBounds(ziel_rect);
                    }
                }
            })
        });
    }
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
                herunterladen::schliessen(&id, tab.controller);
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

/// Löscht Cookies, lokalen Speicher und Cache gezielt für die aktuelle Herkunft (Origin)
/// dieses Tabs und lädt die Seite anschließend neu.
pub fn seite_zuruecksetzen(app: &AppHandle, id: &str) -> Result<(), String> {
    mit_tab(app, id, move |tab| unsafe {
        let url_str = webview2::quelle(&tab.core);
        if url_str.is_empty() {
            return Ok(());
        }
        if let Ok(parsed) = url::Url::parse(&url_str) {
            let origin = parsed.origin().ascii_serialization();
            let host = parsed.host_str().unwrap_or("").to_string();

            // 1. CDP Storage.clearDataForOrigin: leert Cookies, LocalStorage, IndexedDB, Cache etc.
            let param_storage = serde_json::json!({
                "origin": origin,
                "storageTypes": "cookies,storage,local_storage,indexeddb,websql,service_workers,cache_storage"
            });
            let _ = tab.core.CallDevToolsProtocolMethod(
                &windows::core::HSTRING::from("Storage.clearDataForOrigin"),
                &windows::core::HSTRING::from(param_storage.to_string()),
                None,
            );

            // 2. CDP Network.deleteCookies für den spezifischen Host
            if !host.is_empty() {
                let param_network = serde_json::json!({
                    "domain": host
                });
                let _ = tab.core.CallDevToolsProtocolMethod(
                    &windows::core::HSTRING::from("Network.deleteCookies"),
                    &windows::core::HSTRING::from(param_network.to_string()),
                    None,
                );
            }

            // 3. LocalStorage & SessionStorage via Runtime.evaluate (ohne Nutzergeste) leeren
            webview2::ohne_geste(
                &tab.core,
                "try { localStorage.clear(); sessionStorage.clear(); } catch(e) {}",
                |_| {},
            );

            // 4. Frisches Neuladen der Seite
            let _ = tab.core.Reload();
        }
        Ok(())
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
pub fn standbild(app: &AppHandle, tabs: &Tabs, id: Option<&str>) -> Result<Vec<u8>, String> {
    let id = match id {
        Some(id) => id.to_string(),
        None => tabs.0.lock().unwrap().aktiv.clone().ok_or("Kein Tab vorne")?,
    };
    standbild::aufnehmen(app, &id)
}

pub fn aufnahme(app: &AppHandle, id: &str, ganz: bool) -> Result<super::aufnahme::Aufnahme, String> {
    use base64::Engine as _;
    if ganz {
        let (png, abgeschnitten) = standbild::ganz(app, id)?;
        return Ok(super::aufnahme::Aufnahme { png, abgeschnitten });
    }
    let png = base64::engine::general_purpose::STANDARD.encode(standbild::sichtbar(app, id)?);
    Ok(super::aufnahme::Aufnahme { png, abgeschnitten: false })
}

pub fn antworten(app: &AppHandle, nr: u64, antwort: super::Antwort) -> Result<(), String> {
    app.run_on_main_thread(move || rueckfragen::erledigen(nr, antwort)).map_err(fehler)
}

/// Eine Methode des DevTools-Protokolls. `tab-oberflaeche` meint die Webview
/// der Oberfläche selbst (Startseite ohne offenen Tab); alles andere einen Tab.
pub fn protokoll(app: &AppHandle, id: &str, methode: String, parameter: serde_json::Value) -> Result<serde_json::Value, String> {
    if id == entwickler::OBERFLAECHE {
        entwickler::oberflaeche_aufrufen(app, methode, parameter)
    } else {
        entwickler::rufen(app, id, methode, parameter)
    }
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
pub(crate) fn im_profil<T: Send + 'static>(
    app: &AppHandle,
    f: impl FnOnce(&ICoreWebView2) -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    let app2 = app.clone();
    auf_ui(app, move || {
        if let Some(core) = NATIV.with(|n| n.borrow().values().find(|t| !t.privat).map(|t| t.core.clone())) {
            return f(&core);
        }
        let umgebung = umgebung(&app2)?;
        let helfer = controller(&app2, &umgebung, false, None)?;
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

/// Gleicht die Erweiterungen im Profil der Seiten an und gibt die laufenden
/// Kennungen zurück. `nur_offen`: nur, wenn ein normaler Tab offen ist (sonst
/// holt `anlegen` es nach); dann `None`.
pub fn erweiterungen_abgleichen(
    app: &AppHandle,
    soll: Vec<crate::erweiterungen::liste::Soll>,
    nur_offen: bool,
) -> Result<Option<Vec<String>>, String> {
    if nur_offen {
        return auf_ui(app, move || match NATIV.with(|n| n.borrow().values().find(|t| !t.privat).map(|t| t.core.clone())) {
            Some(core) => unsafe { erweiterungen::abgleichen(&core, &soll) }.map(Some),
            None => Ok(None),
        });
    }
    im_profil(app, move |core| unsafe { erweiterungen::abgleichen(core, &soll) }).map(Some)
}

/// Lädt einen entpackten Ordner ins Profil der Seiten; die Kennung vergibt WebView2.
pub fn erweiterung_laden(app: &AppHandle, ordner: std::path::PathBuf) -> Result<String, String> {
    im_profil(app, move |core| unsafe { erweiterungen::laden(core, &ordner) })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Die Kulisse bleibt deckend und dunkel: Transparent ließ das Fenster
    /// durchscheinen, Hellgrau oder Weiß brachte das Weiß der Geräteansicht
    /// zurück (10/2026).
    #[test]
    fn die_kulisse_bleibt_dunkel_und_undurchsichtig() {
        assert_eq!(KULISSE.A, 0xFF, "die Kulisse deckt, sie scheint nicht durch");
        let helligkeit = (u16::from(KULISSE.R) + u16::from(KULISSE.G) + u16::from(KULISSE.B)) / 3;
        assert!(helligkeit <= 64, "die Kulisse bleibt dunkel, Helligkeit {helligkeit}");
    }

    #[test]
    fn bildet_chrome_kennung_aus_der_webview_version() {
        let ua = chrome_user_agent("141.0.3537.57").expect("Kennung");
        assert!(ua.contains("Chrome/141.0.3537.57"));
        assert!(!ua.contains("Edg/"));
        assert!(chrome_user_agent("").is_none());
        assert!(chrome_user_agent("141.0.3537.57-beta").is_none());
    }
}
