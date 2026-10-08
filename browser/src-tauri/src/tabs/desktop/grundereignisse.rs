//! Was sonst der Webview-Baustein von Tauri meldet: erlaubte Navigation,
//! Laden, Titel, neue Fenster und Downloads eines Tabs.

use std::path::PathBuf;

use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    DocumentTitleChangedEventHandler, DownloadStartingEventHandler, NavigationCompletedEventHandler,
    NavigationStartingEventHandler, NewWindowRequestedEventHandler, StateChangedEventHandler,
};
use windows::core::{Interface, HSTRING};

use crate::tabs::{melden, navigation_erlaubt, DownloadStand, TabEreignis};

/// Was sonst der Webview-Baustein von Tauri meldet: erlaubte Navigation,
/// Laden, Titel, neue Fenster, Downloads.
pub(super) unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let mut token = 0i64;
    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_NavigationStarting(
            &NavigationStartingEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                let url = super::webview2::text(|p| args.Uri(p));
                if !url::Url::parse(&url).is_ok_and(|u| navigation_erlaubt(&u)) {
                    return args.SetCancel(true);
                }
                melden(&app, TabEreignis::Laedt { id: id.clone(), url });
                Ok(())
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
    if let Ok(core4) = core.cast::<ICoreWebView2_4>() {
        let (app, id) = (app.clone(), id.to_string());
        core4.add_DownloadStarting(
            &DownloadStartingEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                // Ohne die Download-Leiste von Edge; der Browser hat seine eigene.
                args.SetHandled(true)?;
                let vorgang = args.DownloadOperation()?;
                let url = super::webview2::text(|p| vorgang.Uri(p));
                let vorschlag = PathBuf::from(super::webview2::text(|p| args.ResultFilePath(p)));
                let adresse = url::Url::parse(&url).unwrap_or_else(|_| url::Url::parse("about:blank").unwrap());
                let ziel = crate::downloads::ziel(&app, &adresse, &vorschlag);
                args.SetResultFilePath(&HSTRING::from(ziel.as_os_str()))?;
                melden(
                    &app,
                    TabEreignis::Download {
                        id: id.clone(),
                        stand: DownloadStand::Start,
                        url: url.clone(),
                        datei: Some(ziel.to_string_lossy().into_owned()),
                    },
                );
                let (app, id) = (app.clone(), id.clone());
                let mut t = 0i64;
                vorgang.add_StateChanged(
                    &StateChangedEventHandler::create(Box::new(move |sender, _| {
                        let Some(vorgang) = sender else { return Ok(()) };
                        let mut stand = COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
                        vorgang.State(&mut stand)?;
                        let stand = match stand {
                            COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED => DownloadStand::Fertig,
                            COREWEBVIEW2_DOWNLOAD_STATE_INTERRUPTED => DownloadStand::Fehler,
                            _ => return Ok(()),
                        };
                        let datei = super::webview2::text(|p| vorgang.ResultFilePath(p));
                        melden(&app, TabEreignis::Download { id: id.clone(), stand, url: url.clone(), datei: Some(datei) });
                        Ok(())
                    })),
                    &mut t,
                )?;
                Ok(())
            })),
            &mut token,
        )?;
    }
    Ok(())
}
