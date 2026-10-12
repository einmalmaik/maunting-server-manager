//! Was sonst der Webview-Baustein von Tauri meldet: erlaubte Navigation,
//! Laden, Titel, neue Fenster und Downloads eines Tabs. Seiten, die der
//! Jugend- und Suchtschutz sperrt, laden gar nicht erst; was `tabs::weg`
//! umschreibt (HTTPS, Tracking-Parameter, sichere Suche), lädt neu.

use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    DocumentTitleChangedEventHandler, NavigationCompletedEventHandler, NavigationStartingEventHandler,
    NewWindowRequestedEventHandler, WindowCloseRequestedEventHandler,
};
use windows::core::HSTRING;

use crate::tabs::{melden, navigation_erlaubt, weg, TabEreignis, Weg};

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

                let deferral = args.GetDeferral()?;
                match super::neuer_tab_fenster(&app, &id) {
                    Ok((neue_id, neue_core)) => {
                        args.SetNewWindow(&neue_core)?;
                        args.SetHandled(true)?;
                        let _ = deferral.Complete();
                        melden(
                            &app,
                            TabEreignis::NeuerTab {
                                id: id.clone(),
                                neue_id: Some(neue_id),
                                url,
                            },
                        );
                    }
                    Err(e) => {
                        eprintln!("[MSB] Neuer Tab für Fenster fehlgeschlagen: {e}");
                        let _ = args.SetHandled(true);
                        let _ = deferral.Complete();
                    }
                }
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
