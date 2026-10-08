//! Die Nachrichten von `seite.js` und das Füllen (`tabs::formular`).
//!
//! Nur der oberste Rahmen schickt: Nachrichten aus Unterrahmen kämen an
//! `ICoreWebView2Frame`, dort hört niemand zu. Die Adresse kommt aus
//! `Source()` der Webview, nie aus der Nachricht.

use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2;
use webview2_com::WebMessageReceivedEventHandler;
use windows::core::HSTRING;

use super::webview2::text;
use crate::tabs::formular::{gleiche_herkunft, lesen};
use crate::tabs::{melden, TabEreignis};

pub(super) unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let (app, id) = (app.clone(), id.to_string());
    let mut token = 0i64;
    core.add_WebMessageReceived(
        &WebMessageReceivedEventHandler::create(Box::new(move |sender, args| {
            let (Some(core), Some(args)) = (sender, args) else { return Ok(()) };
            let roh = text(|p| args.TryGetWebMessageAsString(p));
            let Some(meldung) = lesen(&roh) else { return Ok(()) };
            let url = text(|p| core.Source(p));
            if url.starts_with("https://") || url.starts_with("http://") {
                melden(&app, TabEreignis::Formular { id: id.clone(), url, meldung });
            }
            Ok(())
        })),
        &mut token,
    )
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
