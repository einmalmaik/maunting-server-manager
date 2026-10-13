//! Was sonst der Webview-Baustein von Tauri meldet: erlaubte Navigation,
//! Laden, Titel, neue Fenster und Downloads eines Tabs. Seiten, die der
//! Jugend- und Suchtschutz sperrt, laden gar nicht erst; was `tabs::weg`
//! umschreibt (HTTPS, Tracking-Parameter, sichere Suche), lädt neu.

use std::cell::{Cell, RefCell};
use std::collections::HashMap;

use tauri::{AppHandle, Manager};
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    AddScriptToExecuteOnDocumentCreatedCompletedHandler, CreateCoreWebView2ControllerCompletedHandler,
    DocumentTitleChangedEventHandler, NavigationCompletedEventHandler, NavigationStartingEventHandler,
    NewWindowRequestedEventHandler, WindowCloseRequestedEventHandler,
};
use windows::Win32::Foundation::HWND;
use windows::core::{HSTRING, Interface};

use crate::tabs::{melden, navigation_erlaubt, weg, TabEreignis, Weg};

/// Ein Popup-Fenster, dessen Aufbau stufenweise auf dem UI-Faden weiterläuft:
/// Der Rückruf kehrt sofort zurück (Deferral), jede Stufe startet die nächste
/// per Abschlussrückruf — nirgends steht ein `wait_with_pump`. Nur auf dem
/// UI-Faden angefasst; COM-Objekte verlassen ihn nie.
struct Anstehend {
    args: ICoreWebView2NewWindowRequestedEventArgs,
    deferral: ICoreWebView2Deferral,
    app: AppHandle,
    eltern: String,
    url: String,
    privat: bool,
    neue_id: String,
    umgebung: Option<ICoreWebView2Environment>,
    controller: Option<ICoreWebView2Controller>,
    core: Option<ICoreWebView2>,
}

thread_local! {
    static ANSTEHEND: RefCell<HashMap<u64, Anstehend>> = RefCell::new(HashMap::new());
    static NAECHSTE_ANSTEHEND_NR: Cell<u64> = const { Cell::new(0) };
}

/// Ein Schritt ist gescheitert: die reservierte Kennung fällt weg, das Fenster
/// auch (statt zu hängen), und das Deferral schließt genau einmal ab.
fn popup_scheitern(schluessel: u64, grund: String) {
    if let Some(anstehend) = ANSTEHEND.with(|a| a.borrow_mut().remove(&schluessel)) {
        eprintln!("[MSB] Neuer Tab für Fenster fehlgeschlagen: {grund}");
        let tabs = anstehend.app.state::<crate::tabs::Tabs>();
        crate::tabs::austragen(&tabs, &anstehend.neue_id);
        unsafe {
            let _ = anstehend.args.SetHandled(true);
            let _ = anstehend.deferral.Complete();
        }
    }
}

/// Erste Stufe, noch im Rückruf: reserviert die Kennung und stößt den
/// Controller an. Nur Aufrufe, die sofort zurückkehren — der Rückruf kehrt
/// danach zurück, und die Fortsetzung läuft als Abschlussrückruf.
fn popup_beginnen(app: &AppHandle, schluessel: u64) {
    let gestartet: Result<(), String> = (|| {
        let (privat, neue_id) = ANSTEHEND
            .with(|m| m.borrow().get(&schluessel).map(|a| (a.privat, a.neue_id.clone())))
            .ok_or("Popup-Anfrage schon weg")?;
        // Einen Popup-Tab gibt es nur aus einem offenen Tab heraus: dessen Aufbau
        // hat das Profil längst gewärmt. Wäre es doch kalt, fällt das Fenster
        // weg, statt hier zu warten.
        let umgebung =
            super::UMGEBUNG.with(|u| u.borrow().clone()).ok_or("Kein Profil für den Popup-Tab")?;
        let tabs = app.state::<crate::tabs::Tabs>();
        crate::tabs::eintragen(&tabs, &neue_id, privat);
        ANSTEHEND.with(|m| {
            if let Some(a) = m.borrow_mut().get_mut(&schluessel) {
                a.umgebung = Some(umgebung.clone());
            }
        });
        let fenster = app.get_webview_window("main").ok_or("Hauptfenster fehlt")?;
        let hwnd = HWND(fenster.hwnd().map_err(super::fehler)?.0);
        unsafe {
            let u10 = umgebung.cast::<ICoreWebView2Environment10>().map_err(super::fehler)?;
            let optionen = u10.CreateCoreWebView2ControllerOptions().map_err(super::fehler)?;
            optionen.SetIsInPrivateModeEnabled(privat).map_err(super::fehler)?;
            u10.CreateCoreWebView2ControllerWithOptions(
                hwnd,
                &optionen,
                &CreateCoreWebView2ControllerCompletedHandler::create(Box::new(
                    move |ergebnis, controller| {
                        popup_controller_fertig(
                            schluessel,
                            ergebnis.and_then(|()| controller.ok_or_else(super::kein_zeiger)),
                        );
                        Ok(())
                    },
                )),
            )
            .map_err(super::fehler)?;
        }
        Ok(())
    })();
    if let Err(e) = gestartet {
        popup_scheitern(schluessel, e);
    }
}

/// Zweite Stufe: der Controller steht. Versteckt ihn und meldet `seite.js` an;
/// dessen Rückruf setzt fort.
fn popup_controller_fertig(schluessel: u64, controller: windows::core::Result<ICoreWebView2Controller>) {
    let controller = match controller {
        Ok(c) => c,
        Err(e) => return popup_scheitern(schluessel, super::fehler(e)),
    };
    let core = ANSTEHEND.with(|m| {
        let mut m = m.borrow_mut();
        let a = m.get_mut(&schluessel)?;
        let core = unsafe { controller.SetIsVisible(false).ok().and(controller.CoreWebView2().ok())? };
        a.controller = Some(controller.clone());
        a.core = Some(core.clone());
        Some(core)
    });
    let Some(core) = core else {
        return popup_scheitern(schluessel, "Popup-Anfrage schon weg".to_string());
    };
    let gestartet: Result<(), String> = unsafe {
        core.AddScriptToExecuteOnDocumentCreated(
            &HSTRING::from(crate::seite::SKRIPT.as_str()),
            &AddScriptToExecuteOnDocumentCreatedCompletedHandler::create(Box::new(move |ergebnis, _| {
                popup_skript_fertig(schluessel, ergebnis);
                Ok(())
            })),
        )
        .map_err(super::fehler)
        .map(|_| ())
    };
    if let Err(e) = gestartet {
        popup_scheitern(schluessel, e);
    }
}

/// Dritte Stufe: `seite.js` gilt. Meldet das Cookie-Skript an; dessen Rückruf
/// stellt fertig.
fn popup_skript_fertig(schluessel: u64, ergebnis: windows::core::Result<()>) {
    if let Err(e) = ergebnis {
        return popup_scheitern(schluessel, super::fehler(e));
    }
    let start = ANSTEHEND.with(|m| {
        m.borrow()
            .get(&schluessel)
            .and_then(|a| a.core.clone().map(|core| (a.neue_id.clone(), core)))
    });
    let Some((id, core)) = start else {
        return popup_scheitern(schluessel, "Popup-Anfrage schon weg".to_string());
    };
    let gestartet: Result<(), String> = unsafe {
        super::cookies::anmelden_ohne_warten(&id, &core, move |ergebnis| match ergebnis {
            Ok(()) => popup_fertigstellen(schluessel),
            Err(e) => popup_scheitern(schluessel, super::fehler(e)),
        })
        .map_err(super::fehler)
    };
    if let Err(e) = gestartet {
        popup_scheitern(schluessel, e);
    }
}

/// Letzte Stufe: bindet alles an, trägt den Tab ein, übergibt ihn an das
/// Fenster und schließt das Deferral genau einmal ab. Ein inzwischen
/// geschlossener Ursprungstab lässt das Fenster wegfallen, statt die Seite
/// hängen zu lassen.
fn popup_fertigstellen(schluessel: u64) {
    let Some(anstehend) = ANSTEHEND.with(|a| a.borrow_mut().remove(&schluessel)) else { return };
    let bereit = (anstehend.controller.clone(), anstehend.core.clone(), anstehend.umgebung.clone());
    let (controller, core, umgebung) =
        match bereit {
            (Some(c), Some(k), Some(u)) => (c, k, u),
            _ => {
                let tabs = anstehend.app.state::<crate::tabs::Tabs>();
                crate::tabs::austragen(&tabs, &anstehend.neue_id);
                unsafe {
                    let _ = anstehend.args.SetHandled(true);
                    let _ = anstehend.deferral.Complete();
                }
                return;
            }
        };
    let tabs = anstehend.app.state::<crate::tabs::Tabs>();
    // Erweiterungen gleicht nur der erste normale Tab ab (`anlegen`); ein Popup
    // hat stets einen offenen Ursprungstab und braucht das nie.
    match super::popup_einrichten(&anstehend.app, &anstehend.neue_id, controller, &core, umgebung, anstehend.privat)
    {
        Ok(()) => {
            super::rahmen_anwenden(&anstehend.app, &tabs);
            let uebergeben = unsafe {
                anstehend.args.SetNewWindow(&core).and_then(|()| anstehend.args.SetHandled(true))
            };
            if uebergeben.is_ok() {
                melden(
                    &anstehend.app,
                    TabEreignis::NeuerTab {
                        id: anstehend.eltern.clone(),
                        neue_id: Some(anstehend.neue_id.clone()),
                        url: anstehend.url.clone(),
                    },
                );
            } else {
                unsafe {
                    let _ = anstehend.args.SetHandled(true);
                }
            }
            unsafe {
                let _ = anstehend.deferral.Complete();
            }
        }
        Err(e) => popup_scheitern_nach_abtrag(&anstehend, e),
    }
}

/// Wie [`popup_scheitern`], wenn der Eintrag schon aus der Karte genommen ist.
fn popup_scheitern_nach_abtrag(anstehend: &Anstehend, grund: String) {
    eprintln!("[MSB] Neuer Tab für Fenster fehlgeschlagen: {grund}");
    let tabs = anstehend.app.state::<crate::tabs::Tabs>();
    crate::tabs::austragen(&tabs, &anstehend.neue_id);
    unsafe {
        let _ = anstehend.args.SetHandled(true);
        let _ = anstehend.deferral.Complete();
    }
}

pub(super) unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let mut token = 0i64;
    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_NavigationStarting(
            &NavigationStartingEventHandler::create(Box::new(move |sender, args| {
                let Some(args) = args else { return Ok(()) };
                let url = super::webview2::text(|p| args.Uri(p));
                // Auch Weiterleitungen kommen hier vorbei, nicht nur die erste Adresse.
                let mut weiterleitung = windows::core::BOOL::default();
                args.IsRedirected(&mut weiterleitung)?;
                match weg(&url, weiterleitung.as_bool()) {
                    Weg::Verboten => args.SetCancel(true),
                    Weg::Gesperrt(grund) => {
                        melden(&app, TabEreignis::Gesperrt { id: id.clone(), url, grund: grund.into() });
                        args.SetCancel(true)
                    }
                    Weg::Umleiten(sicher) => {
                        args.SetCancel(true)?;
                        match sender {
                            Some(core) => core.Navigate(&HSTRING::from(sicher)),
                            None => Ok(()),
                        }
                    }
                    Weg::Laden => {
                        melden(&app, TabEreignis::Laedt { id: id.clone(), url });
                        Ok(())
                    }
                }
            })),
            &mut token,
        )?;
    }
    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_NavigationCompleted(
            &NavigationCompletedEventHandler::create(Box::new(move |sender, _| {
                if let Some(core) = sender {
                    let url = super::webview2::text(|p| core.Source(p));
                    melden(&app, TabEreignis::Geladen { id: id.clone(), url });
                }
                Ok(())
            })),
            &mut token,
        )?;
    }
    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_DocumentTitleChanged(
            &DocumentTitleChangedEventHandler::create(Box::new(move |sender, _| {
                if let Some(core) = sender {
                    let titel = super::webview2::text(|p| core.DocumentTitle(p));
                    melden(&app, TabEreignis::Titel { id: id.clone(), titel });
                }
                Ok(())
            })),
            &mut token,
        )?;
    }
    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_NewWindowRequested(
            &NewWindowRequestedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                // Nur nach einem Klick oder einer Taste. Den Popup-Blocker von
                // Chromium gibt es in der WebView2 nicht; ohne diese Prüfung
                // machte jedes `window.open` einer Seite einen Tab auf (bis 09.10.2026).
                let mut geste = windows::core::BOOL::default();
                args.IsUserInitiated(&mut geste)?;
                let url = super::webview2::text(|p| args.Uri(p));
                let ziel_erlaubt = if url.is_empty() || url == "about:blank" {
                    true
                } else if let Ok(u) = url::Url::parse(&url) {
                    navigation_erlaubt(&u) && matches!(u.scheme(), "http" | "https" | "about")
                } else {
                    false
                };
                if !geste.as_bool() || !ziel_erlaubt {
                    args.SetHandled(true)?;
                    return Ok(());
                }

                // Der Aufbau (Controller, Skripte) wartet auf WebView2-Rückrufe
                // und darf hier nicht per `wait_with_pump` stehen: Auf dem
                // UI-Faden führt `run_on_main_thread` die Arbeit sofort
                // verschachtelt aus, und WebView2 stellt Abschlüsse nicht zu,
                // solange sein eigener Rückruf noch läuft (bis 10/2026 hing so
                // der ganze Browser am Klick auf ein Popup). Das Deferral hält
                // das Fenster offen; `popup_beginnen` stößt nur An, was sofort
                // zurückkehrt, und jede Stufe setzt per Abschlussrückruf fort.
                // COM-Objekte verlassen den UI-Faden nie.
                let deferral = args.GetDeferral()?;
                let schluessel = ANSTEHEND.with(|a| {
                    let mut a = a.borrow_mut();
                    let schluessel = NAECHSTE_ANSTEHEND_NR.with(|n| {
                        let n0 = n.get();
                        n.set(n0 + 1);
                        n0
                    });
                    a.insert(schluessel, Anstehend {
                        args,
                        deferral,
                        app: app.clone(),
                        eltern: id.clone(),
                        url,
                        privat: super::tab_privat(&id),
                        neue_id: super::popup_id(),
                        umgebung: None,
                        controller: None,
                        core: None,
                    });
                    schluessel
                });
                popup_beginnen(&app, schluessel);
                Ok(())
            })),
            &mut token,
        )?;
    }
    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_WindowCloseRequested(
            &WindowCloseRequestedEventHandler::create(Box::new(move |_sender, _| {
                melden(&app, TabEreignis::Schliessen { id: id.clone() });
                Ok(())
            })),
            &mut token,
        )?;
    }
    Ok(())
}
