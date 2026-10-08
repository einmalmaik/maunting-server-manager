//! Was sonst der Webview-Baustein von Tauri meldet: erlaubte Navigation,
//! Laden, Titel, neue Fenster und Downloads eines Tabs. Seiten, die der
//! Jugend- und Suchtschutz sperrt, laden gar nicht erst; Suchen ohne sichere
//! Suche laden mit ihr (`schild/sperre.rs`).

use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    DocumentTitleChangedEventHandler, NavigationCompletedEventHandler, NavigationStartingEventHandler,
    NewWindowRequestedEventHandler,
};
use windows::core::HSTRING;

use crate::tabs::{melden, navigation_erlaubt, weg, TabEreignis, Weg};

/// Was sonst der Webview-Baustein von Tauri meldet: erlaubte Navigation,
/// Laden, Titel, neue Fenster, Downloads.
pub(super) unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let mut token = 0i64;
    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_NavigationStarting(
            &NavigationStartingEventHandler::create(Box::new(move |sender, args| {
                let Some(args) = args else { return Ok(()) };
                let url = super::webview2::text(|p| args.Uri(p));
                // Auch Weiterleitungen kommen hier vorbei, nicht nur die erste Adresse.
                match weg(&url) {
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
                // Nie ein Fenster: die Oberfläche öffnet einen Tab, wenn sie will.
                args.SetHandled(true)?;
                let url = super::webview2::text(|p| args.Uri(p));
                if url::Url::parse(&url).is_ok_and(|u| navigation_erlaubt(&u) && matches!(u.scheme(), "http" | "https")) {
                    melden(&app, TabEreignis::NeuerTab { id: id.clone(), url });
                }
                Ok(())
            })),
            &mut token,
        )?;
    }
    super::herunterladen::anbinden(app, id, core)?;
    Ok(())
}
