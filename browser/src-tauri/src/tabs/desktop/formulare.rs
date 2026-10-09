//! Die Nachrichten von `seite.js` und das Füllen (`tabs::formular`).
//!
//! Der oberste Rahmen schickt an die Webview, ein Unterrahmen an sein
//! `ICoreWebView2Frame` ([`rahmen_anbinden`]); dort gilt nur, was eine Kasse
//! braucht (`formular::unten`). Die Adressen kommen aus `Source()` der Webview
//! und des Absenders, nie aus der Nachricht.

use std::cell::RefCell;
use std::collections::HashMap;

use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::{ICoreWebView2, ICoreWebView2Frame, ICoreWebView2Frame2, ICoreWebView2_4};
use webview2_com::{FrameCreatedEventHandler, FrameDestroyedEventHandler, FrameWebMessageReceivedEventHandler, WebMessageReceivedEventHandler};
use windows::core::{Interface, HSTRING};

use super::webview2::text;
use crate::tabs::formular::{self, gleiche_herkunft, lesen};

/// So viele Rahmen einer Seite merkt sich ein Tab; Werbung lädt Rahmen nach,
/// die ältesten fallen heraus.
const RAHMEN_MAX: usize = 64;

thread_local! {
    /// Je Tab die Unterrahmen, die sich gemeldet haben, mit ihrer Herkunft.
    static RAHMEN: RefCell<HashMap<String, Vec<(ICoreWebView2Frame2, String)>>> = RefCell::new(HashMap::new());
}

pub(super) unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let (app, id) = (app.clone(), id.to_string());
    let mut token = 0i64;
    core.add_WebMessageReceived(
        &WebMessageReceivedEventHandler::create(Box::new({
            let (app, id) = (app.clone(), id.clone());
            move |sender, args| {
                let (Some(core), Some(args)) = (sender, args) else { return Ok(()) };
                let roh = text(|p| args.TryGetWebMessageAsString(p));
                let Some(meldung) = lesen(&roh) else { return Ok(()) };
                // `Source()` der Webview ist nach dem Commit schon die neue Seite,
                // während eine späte Nachricht noch vom alten Dokument kommt.
                // `args.Source()` nennt den Absender; beide müssen passen.
                let url = text(|p| core.Source(p));
                let absender = text(|p| args.Source(p));
                if gleiche_herkunft(&url, &absender) {
                    formular::oben(&app, &id, &url, meldung);
                }
                Ok(())
            }
        })),
        &mut token,
    )?;
    rahmen_anbinden(&app, &id, core)
}

/// Hört auf die Rahmen, die die Seite direkt enthält. Kassen wie Stripe und
/// Adyen legen ihre Felder dort hinein; tiefer verschachtelte Rahmen bleiben
/// außen vor, denn die oberste Seite kann nur ihre eigenen bestätigen.
unsafe fn rahmen_anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let Ok(core4) = core.cast::<ICoreWebView2_4>() else { return Ok(()) };
    let (app, id) = (app.clone(), id.to_string());
    let mut token = 0i64;
    core4.add_FrameCreated(
        &FrameCreatedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else { return Ok(()) };
            let Ok(rahmen) = args.Frame()?.cast::<ICoreWebView2Frame2>() else { return Ok(()) };
            let (app, id, id_weg) = (app.clone(), id.clone(), id.clone());
            let mut token = 0i64;
            rahmen.add_WebMessageReceived(
                &FrameWebMessageReceivedEventHandler::create(Box::new(move |sender, args| {
                    let (Some(rahmen), Some(args)) = (sender, args) else { return Ok(()) };
                    // Die Webview nicht im Rückruf ihres Rahmens festhalten: das gäbe einen Kreis.
                    let Some(url) = super::NATIV.with(|n| n.borrow().get(&id).map(|t| text(|p| t.core.Source(p)))) else {
                        return Ok(());
                    };
                    let roh = text(|p| args.TryGetWebMessageAsString(p));
                    let absender = text(|p| args.Source(p));
                    let herkunft = formular::unten(&app, &id, &url, &absender, &roh);
                    if let Ok(rahmen) = rahmen.cast::<ICoreWebView2Frame2>() {
                        merken(&id, rahmen, herkunft);
                    }
                    Ok(())
                })),
                &mut token,
            )?;
            rahmen.add_Destroyed(
                &FrameDestroyedEventHandler::create(Box::new(move |sender, _| {
                    if let Some(rahmen) = sender.and_then(|r: ICoreWebView2Frame| r.cast::<ICoreWebView2Frame2>().ok()) {
                        merken(&id_weg, rahmen, None);
                    }
                    Ok(())
                })),
                &mut token,
            )?;
            Ok(())
        })),
        &mut token,
    )
}

/// Setzt die Herkunft eines Rahmens; `None` nimmt ihn heraus.
fn merken(id: &str, rahmen: ICoreWebView2Frame2, herkunft: Option<String>) {
    RAHMEN.with(|r| {
        let mut r = r.borrow_mut();
        let liste = r.entry(id.to_string()).or_default();
        liste.retain(|(f, _)| *f != rahmen);
        if let Some(herkunft) = herkunft {
            if liste.len() >= RAHMEN_MAX {
                liste.remove(0);
            }
            liste.push((rahmen, herkunft));
        }
    });
}

pub(super) fn vergessen(id: &str) {
    RAHMEN.with(|r| r.borrow_mut().remove(id));
}

pub fn fuellen(app: &AppHandle, id: &str, fuer: &str, nachricht: String) -> Result<(), String> {
    let fuer = fuer.to_string();
    super::mit_tab(app, id, move |tab| unsafe {
        let jetzt = text(|p| tab.core.Source(p));
        if !gleiche_herkunft(&jetzt, &fuer) {
            return Err("Die Seite hat inzwischen gewechselt".into());
        }
        tab.core.PostWebMessageAsJson(&HSTRING::from(nachricht)).map_err(|e| e.to_string())
    })
}

/// An jeden Rahmen der Herkunft `rahmen`, wenn die Seite noch auf `fuer` steht.
/// `seite.js` im Rahmen prüft die Herkunft noch einmal: ein Rahmen, der
/// inzwischen woanders steht, hat sich vielleicht noch nicht neu gemeldet.
pub fn fuellen_rahmen(app: &AppHandle, id: &str, fuer: &str, rahmen: &str, nachricht: String) -> Result<(), String> {
    let (id2, fuer, rahmen) = (id.to_string(), fuer.to_string(), rahmen.to_string());
    super::mit_tab(app, id, move |tab| unsafe {
        let jetzt = text(|p| tab.core.Source(p));
        if !gleiche_herkunft(&jetzt, &fuer) {
            return Err("Die Seite hat inzwischen gewechselt".into());
        }
        let ziele: Vec<ICoreWebView2Frame2> = RAHMEN.with(|r| {
            let r = r.borrow();
            r.get(&id2).map(|l| l.iter().filter(|(_, h)| *h == rahmen).map(|(f, _)| f.clone()).collect()).unwrap_or_default()
        });
        if ziele.is_empty() {
            return Err("Das Formular ist nicht mehr da".into());
        }
        let nachricht = HSTRING::from(nachricht);
        for ziel in ziele {
            ziel.PostWebMessageAsJson(&nachricht).map_err(|e| e.to_string())?;
        }
        Ok(())
    })
}
