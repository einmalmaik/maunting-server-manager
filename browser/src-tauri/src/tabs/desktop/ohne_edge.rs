//! Kein Edge im Browser.
//!
//! WebView2 bringt Oberfläche von Edge mit: Entwicklerwerkzeuge, Fehlerseite
//! („Hmmm… diese Seite ist nicht erreichbar“), Statusblase unten links,
//! Suchleiste, Druckvorschau und die Frage, ob ein Passwort gespeichert werden
//! soll. All das ist hier aus oder ersetzt: der Browser meldet, was passiert,
//! und die Oberfläche zeigt es in ihrem eigenen Stil. Rechtsklick, Dialoge,
//! Rechte und Anmeldung übernimmt `rueckfragen.rs`, die Werkzeuge
//! `entwickler.rs`.

use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    FindActiveMatchIndexChangedEventHandler, FindMatchCountChangedEventHandler, FindStartCompletedHandler,
    NavigationCompletedEventHandler, StatusBarTextChangedEventHandler,
};
use windows::core::{Interface, BOOL, HSTRING};

use super::webview2::text;
use crate::tabs::{melden, TabEreignis};

/// Die Schalter ohne Ereignisse; auch für das Popup einer Erweiterung
/// (`popup.rs`), das bis 10.10.2026 Entwicklerwerkzeuge und Dialoge von Edge hatte.
pub unsafe fn einstellungen(core: &ICoreWebView2) -> windows::core::Result<()> {
    let einstellungen = core.Settings()?;
    einstellungen.SetAreDevToolsEnabled(false)?;
    einstellungen.SetIsStatusBarEnabled(false)?;
    einstellungen.SetIsBuiltInErrorPageEnabled(false)?;
    // Ohne das meldet WebView2 `alert` und Co. nicht, sondern zeigt sie selbst.
    einstellungen.SetAreDefaultScriptDialogsEnabled(false)?;
    if let Ok(e4) = einstellungen.cast::<ICoreWebView2Settings4>() {
        e4.SetIsPasswordAutosaveEnabled(false)?;
        e4.SetIsGeneralAutofillEnabled(false)?;
    }
    Ok(())
}

pub unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    einstellungen(core)?;
    let mut token = 0i64;

    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_NavigationCompleted(
            &NavigationCompletedEventHandler::create(Box::new(move |sender, args| {
                let (Some(core), Some(args)) = (sender, args) else { return Ok(()) };
                let mut erfolg = BOOL::default();
                args.IsSuccess(&mut erfolg)?;
                let mut status = COREWEBVIEW2_WEB_ERROR_STATUS_UNKNOWN;
                args.WebErrorStatus(&mut status)?;
                // Hat ein Server geantwortet (auch 403, 404, 503), zeigt die Seite
                // seine Antwort: eine Cloudflare-Prüfung oder eigene 404-Seite ist
                // kein Ladefehler. Abgebrochen heißt: angehalten oder gleich
                // weiter navigiert.
                let mut http = 0i32;
                if let Ok(args2) = args.cast::<ICoreWebView2NavigationCompletedEventArgs2>() {
                    let _ = args2.HttpStatusCode(&mut http);
                }
                if !erfolg.as_bool() && http == 0 && status != COREWEBVIEW2_WEB_ERROR_STATUS_OPERATION_CANCELED {
                    let url = text(|p| core.Source(p));
                    // Eben erst auf HTTPS hochgestuft: die Seite kann es nicht.
                    if let Some(unverschluesselt) = crate::tabs::https::rueckfall(&url) {
                        return core.Navigate(&windows::core::HSTRING::from(unverschluesselt));
                    }
                    melden(&app, TabEreignis::Fehlerseite { id: id.clone(), url, grund: fehler_art(status).into() });
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    if let Ok(core12) = core.cast::<ICoreWebView2_12>() {
        let (app, id) = (app.clone(), id.to_string());
        core12.add_StatusBarTextChanged(
            &StatusBarTextChangedEventHandler::create(Box::new(move |sender, _| {
                if let Some(core12) = sender.and_then(|s| s.cast::<ICoreWebView2_12>().ok()) {
                    let text = text(|p| core12.StatusBarText(p));
                    melden(&app, TabEreignis::Status { id: id.clone(), text });
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    if let Ok(core28) = core.cast::<ICoreWebView2_28>() {
        let suche = core28.Find()?;
        let melde = {
            let (app, id) = (app.clone(), id.to_string());
            move |suche: Option<ICoreWebView2Find>| {
                if let Some(suche) = suche {
                    let (mut aktuell, mut anzahl) = (0i32, 0i32);
                    let _ = suche.ActiveMatchIndex(&mut aktuell);
                    let _ = suche.MatchCount(&mut anzahl);
                    melden(&app, TabEreignis::Treffer { id: id.clone(), aktuell, anzahl });
                }
                Ok::<(), windows::core::Error>(())
            }
        };
        let zweite = melde.clone();
        suche.add_MatchCountChanged(
            &FindMatchCountChangedEventHandler::create(Box::new(move |s, _| melde(s))),
            &mut token,
        )?;
        suche.add_ActiveMatchIndexChanged(
            &FindActiveMatchIndexChangedEventHandler::create(Box::new(move |s, _| zweite(s))),
            &mut token,
        )?;
    }
    Ok(())
}

fn fehler_art(status: COREWEBVIEW2_WEB_ERROR_STATUS) -> &'static str {
    match status {
        COREWEBVIEW2_WEB_ERROR_STATUS_HOST_NAME_NOT_RESOLVED => "adresse",
        COREWEBVIEW2_WEB_ERROR_STATUS_DISCONNECTED => "offline",
        COREWEBVIEW2_WEB_ERROR_STATUS_TIMEOUT => "zeit",
        COREWEBVIEW2_WEB_ERROR_STATUS_CONNECTION_ABORTED
        | COREWEBVIEW2_WEB_ERROR_STATUS_CONNECTION_RESET
        | COREWEBVIEW2_WEB_ERROR_STATUS_CANNOT_CONNECT
        | COREWEBVIEW2_WEB_ERROR_STATUS_SERVER_UNREACHABLE => "verbindung",
        COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_COMMON_NAME_IS_INCORRECT
        | COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_EXPIRED
        | COREWEBVIEW2_WEB_ERROR_STATUS_CLIENT_CERTIFICATE_CONTAINS_ERRORS
        | COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_REVOKED
        | COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_IS_INVALID => "zertifikat",
        _ => "unbekannt",
    }
}

/// Suchen in der Seite: `start` mit Begriff, dann `weiter`/`zurueck`, `ende`.
pub fn suchen(app: &AppHandle, id: &str, richtung: String, begriff: String) -> Result<(), String> {
    super::mit_tab(app, id, move |tab| unsafe {
        let ergebnis = (|| -> windows::core::Result<()> {
            let suche = tab.core.cast::<ICoreWebView2_28>()?.Find()?;
            match richtung.as_str() {
                "weiter" => suche.FindNext(),
                "zurueck" => suche.FindPrevious(),
                "ende" => suche.Stop(),
                _ => {
                    let optionen = tab.umgebung.cast::<ICoreWebView2Environment15>()?.CreateFindOptions()?;
                    optionen.SetFindTerm(&HSTRING::from(begriff))?;
                    optionen.SetSuppressDefaultFindDialog(true)?;
                    optionen.SetShouldHighlightAllMatches(true)?;
                    suche.Start(&optionen, &FindStartCompletedHandler::create(Box::new(|_| Ok(()))))
                }
            }
        })();
        ergebnis.map_err(|e| e.to_string())
    })
}

/// Drucken über den Dialog von Windows, nicht die Vorschau von Edge.
pub fn drucken(app: &AppHandle, id: &str) -> Result<(), String> {
    super::mit_tab(app, id, |tab| unsafe {
        tab.core
            .cast::<ICoreWebView2_16>()
            .and_then(|c| c.ShowPrintUI(COREWEBVIEW2_PRINT_DIALOG_KIND_SYSTEM))
            .map_err(|e| e.to_string())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Die Gründe, für die die Oberfläche eine Fehlerseite hat (`browser.fehlerseite.*`).
    const GRUENDE: [&str; 6] = ["adresse", "offline", "zeit", "verbindung", "zertifikat", "unbekannt"];

    #[test]
    fn jede_fehlerseite_hat_text_auf_beiden_plattformen() {
        let gemeldet = [
            COREWEBVIEW2_WEB_ERROR_STATUS_HOST_NAME_NOT_RESOLVED,
            COREWEBVIEW2_WEB_ERROR_STATUS_DISCONNECTED,
            COREWEBVIEW2_WEB_ERROR_STATUS_TIMEOUT,
            COREWEBVIEW2_WEB_ERROR_STATUS_CANNOT_CONNECT,
            COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_EXPIRED,
            COREWEBVIEW2_WEB_ERROR_STATUS_UNKNOWN,
        ]
        .map(fehler_art);
        assert_eq!(gemeldet, GRUENDE);

        // Android meldet dieselben Gründe (`Tab.grund`).
        // Der Windows-Runner checkt mit CRLF aus.
        let kotlin = include_str!("../../../gen/android/app/src/main/java/com/mauntingstudios/secure_browser/Tab.kt").replace("\r\n", "\n");
        let grund = &kotlin[kotlin.find("fun grund(").unwrap()..];
        let grund = &grund[..grund.find("\n    }\n").unwrap()];
        for wort in grund.split('"').skip(1).step_by(2) {
            assert!(GRUENDE.contains(&wort), "Tab.grund meldet {wort}");
        }

        for sprache in [include_str!("../../../../../frontend/src/locales/de.json"), include_str!("../../../../../frontend/src/locales/en.json")] {
            let texte: serde_json::Value = serde_json::from_str(sprache).unwrap();
            for g in GRUENDE {
                for teil in ["Titel", "Text"] {
                    assert!(texte["browser"]["fehlerseite"][format!("{g}{teil}")].is_string(), "{g}{teil}");
                }
            }
        }
    }
}
