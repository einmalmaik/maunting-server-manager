//! Was Tauri von der WebView2 nicht herausgibt, hängt hier direkt daran.
//!
//! - jede Anfrage eines Tabs (auch aus Iframes und Workern) geht durch das
//!   Schild und wird bei einem Treffer mit 403 beantwortet, bevor sie das
//!   Netz erreicht;
//! - Adresse, Zurück/Vor und Favicon, auch bei `history.pushState`;
//! - Kosmetik: nach `DOMContentLoaded` blendet ein Stylesheet die Werbeplätze
//!   aus, die die Filterlisten für diese Seite kennen;
//! - Tastenkürzel, die dem Browser gehören, und Vollbild für Videos.
//!
//! Alle Rückrufe laufen auf dem UI-Faden der WebView2; deshalb reicht `Rc`.

use std::cell::RefCell;
use std::rc::Rc;

use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    take_pwstr, AcceleratorKeyPressedEventHandler, ContainsFullScreenElementChangedEventHandler,
    DOMContentLoadedEventHandler, ExecuteScriptCompletedHandler, FaviconChangedEventHandler,
    HistoryChangedEventHandler, NavigationStartingEventHandler, ProcessFailedEventHandler,
    SourceChangedEventHandler, WebResourceRequestedEventHandler,
};
use windows::core::{Interface, BOOL, HSTRING, PWSTR};
use windows::Win32::UI::Input::KeyboardAndMouse::{GetKeyState, VK_CONTROL, VK_SHIFT};

use crate::schild::{self, AnfrageArt};
use crate::tabs::{melden, TabEreignis};

pub(super) unsafe fn text(lesen: impl FnOnce(*mut PWSTR) -> windows::core::Result<()>) -> String {
    let mut wert = PWSTR::null();
    if lesen(&mut wert).is_ok() {
        take_pwstr(wert)
    } else {
        String::new()
    }
}

unsafe fn quelle(core: &ICoreWebView2) -> String {
    text(|p| core.Source(p))
}

unsafe fn verlauf(core: &ICoreWebView2) -> (bool, bool) {
    let (mut zurueck, mut vor) = (BOOL::default(), BOOL::default());
    let _ = core.CanGoBack(&mut zurueck);
    let _ = core.CanGoForward(&mut vor);
    (zurueck.as_bool(), vor.as_bool())
}

pub(super) unsafe fn einrichten(
    app: &AppHandle,
    id: &str,
    controller: ICoreWebView2Controller,
    umgebung: ICoreWebView2Environment,
) -> windows::core::Result<()> {
    let core = controller.CoreWebView2()?;
    let mut token = 0i64;

    // Die Adresse, zu der der Tab gerade als Ganzes navigiert. Diese eine
    // Anfrage ist die Seite selbst und wird nie geblockt; ein Iframe mit einer
    // Werbeadresse dagegen schon.
    let hauptadresse = Rc::new(RefCell::new(String::new()));

    {
        let (app, id, hauptadresse) = (app.clone(), id.to_string(), hauptadresse.clone());
        core.add_NavigationStarting(
            &NavigationStartingEventHandler::create(Box::new(move |_, args| {
                if let Some(args) = args {
                    *hauptadresse.borrow_mut() = text(|p| args.Uri(p));
                    schild::seitenwechsel(&app, &id);
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    {
        let (app, id) = (app.clone(), id.to_string());
        let melde_adresse = move |core: Option<ICoreWebView2>| {
            if let Some(core) = core {
                let url = quelle(&core);
                let (zurueck, vor) = verlauf(&core);
                melden(&app, TabEreignis::Adresse { id: id.clone(), url, zurueck, vor });
            }
            Ok::<(), windows::core::Error>(())
        };
        let zweite = melde_adresse.clone();
        core.add_SourceChanged(
            &SourceChangedEventHandler::create(Box::new(move |core, _| melde_adresse(core))),
            &mut token,
        )?;
        core.add_HistoryChanged(
            &HistoryChangedEventHandler::create(Box::new(move |core, _| zweite(core))),
            &mut token,
        )?;
    }

    if let Ok(core15) = core.cast::<ICoreWebView2_15>() {
        let (app, id) = (app.clone(), id.to_string());
        core15.add_FaviconChanged(
            &FaviconChangedEventHandler::create(Box::new(move |sender, _| {
                if let Some(core15) = sender.and_then(|s| s.cast::<ICoreWebView2_15>().ok()) {
                    let url = text(|p| core15.FaviconUri(p));
                    let url = (url.starts_with("https://") || url.starts_with("http://")).then_some(url);
                    melden(&app, TabEreignis::Favicon { id: id.clone(), url });
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    // Jede Anfrage, auch aus Iframes, Service- und Shared-Workern. Ältere
    // WebView2 ohne `_22` sehen nur die Anfragen des Dokuments selbst.
    let alle = HSTRING::from("*");
    match core.cast::<ICoreWebView2_22>() {
        Ok(core22) => core22.AddWebResourceRequestedFilterWithRequestSourceKinds(
            &alle,
            COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,
            COREWEBVIEW2_WEB_RESOURCE_REQUEST_SOURCE_KINDS_ALL,
        )?,
        Err(_) => core.AddWebResourceRequestedFilter(&alle, COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL)?,
    }
    {
        let (app, id, hauptadresse) = (app.clone(), id.to_string(), hauptadresse.clone());
        core.add_WebResourceRequested(
            &WebResourceRequestedEventHandler::create(Box::new(move |sender, args| {
                let (Some(core), Some(args)) = (sender, args) else { return Ok(()) };
                let anfrage = args.Request()?;
                let url = text(|p| anfrage.Uri(p));
                let mut kontext = COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL;
                args.ResourceContext(&mut kontext)?;
                if kontext == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_DOCUMENT && *hauptadresse.borrow() == url {
                    return Ok(());
                }
                let seite = quelle(&core);
                if schild::pruefen(&app, &id, &url, &seite, anfrage_art(kontext)) {
                    let antwort = umgebung.CreateWebResourceResponse(
                        None,
                        403,
                        &HSTRING::from("Blocked by MSB"),
                        &HSTRING::from(""),
                    )?;
                    args.SetResponse(&antwort)?;
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    if let Ok(core2) = core.cast::<ICoreWebView2_2>() {
        core2.add_DOMContentLoaded(
            &DOMContentLoadedEventHandler::create(Box::new(move |sender, _| {
                if let Some(core) = sender {
                    kosmetik_anwenden(&core);
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_ContainsFullScreenElementChanged(
            &ContainsFullScreenElementChangedEventHandler::create(Box::new(move |sender, _| {
                if let Some(core) = sender {
                    let mut an = BOOL::default();
                    core.ContainsFullScreenElement(&mut an)?;
                    super::vollbild(&app, &id, an.as_bool());
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_ProcessFailed(
            &ProcessFailedEventHandler::create(Box::new(move |_, _| {
                melden(&app, TabEreignis::Absturz { id: id.clone() });
                Ok(())
            })),
            &mut token,
        )?;
    }

    {
        let (app, id) = (app.clone(), id.to_string());
        controller.add_AcceleratorKeyPressed(
            &AcceleratorKeyPressedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                let mut art = COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN;
                args.KeyEventKind(&mut art)?;
                let mut taste = 0u32;
                args.VirtualKey(&mut taste)?;
                let alt = art == COREWEBVIEW2_KEY_EVENT_KIND_SYSTEM_KEY_DOWN;
                if art != COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN && !alt {
                    return Ok(());
                }
                let strg = GetKeyState(VK_CONTROL.0 as i32) < 0;
                let umschalt = GetKeyState(VK_SHIFT.0 as i32) < 0;
                if let Some(name) = kuerzel(taste, strg, umschalt, alt) {
                    args.SetHandled(true)?;
                    melden(&app, TabEreignis::Taste { id: id.clone(), taste: name.to_string() });
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    super::ohne_edge::anbinden(app, id, &core)?;
    super::rueckfragen::anbinden(app, id, &core)?;
    super::entwickler::anbinden(app, id, &core)?;
    Ok(())
}

fn anfrage_art(kontext: COREWEBVIEW2_WEB_RESOURCE_CONTEXT) -> AnfrageArt {
    match kontext {
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_DOCUMENT => AnfrageArt::Dokument,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_STYLESHEET => AnfrageArt::Stylesheet,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_IMAGE => AnfrageArt::Bild,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_MEDIA => AnfrageArt::Medien,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FONT => AnfrageArt::Schrift,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_SCRIPT => AnfrageArt::Skript,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_XML_HTTP_REQUEST | COREWEBVIEW2_WEB_RESOURCE_CONTEXT_FETCH => {
            AnfrageArt::Xhr
        }
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_WEBSOCKET => AnfrageArt::Websocket,
        COREWEBVIEW2_WEB_RESOURCE_CONTEXT_PING => AnfrageArt::Ping,
        _ => AnfrageArt::Sonstiges,
    }
}

/// Die Kürzel, die dem Browser gehören, auch wenn gerade die Seite den Fokus
/// hat. Zurück, Vor und Neu laden behält die WebView2 selbst; Suchen, Drucken
/// und die Entwicklerwerkzeuge zeigten Oberfläche von Edge und gehören deshalb
/// dem Browser. F7 (Caret-Browsing) fragte in einem Edge-Dialog und ist still.
pub(crate) fn kuerzel(taste: u32, strg: bool, umschalt: bool, alt: bool) -> Option<&'static str> {
    const TAB: u32 = 0x09;
    const F3: u32 = 0x72;
    const F6: u32 = 0x75;
    const F7: u32 = 0x76;
    const F12: u32 = 0x7B;
    let buchstabe = char::from_u32(taste).filter(|c| c.is_ascii_uppercase());
    match (strg, umschalt, alt, buchstabe, taste) {
        (true, false, false, Some('T'), _) => Some("neuer_tab"),
        (true, true, false, Some('T'), _) => Some("tab_wiederherstellen"),
        (true, true, false, Some('N'), _) => Some("privater_tab"),
        (true, false, false, Some('N'), _) => Some("neuer_tab"),
        (true, false, false, Some('W'), _) => Some("tab_schliessen"),
        (true, false, false, Some('L'), _) | (false, false, false, None, F6) => Some("adresszeile"),
        (true, false, false, Some('D'), _) => Some("lesezeichen"),
        (true, false, false, Some('H'), _) => Some("verlauf"),
        (true, false, false, Some('J'), _) => Some("downloads"),
        (true, false, false, None, TAB) => Some("naechster_tab"),
        (true, true, false, None, TAB) => Some("voriger_tab"),
        (false, false, true, Some('S'), _) => Some("singra"),
        (true, false, false, Some('F'), _) => Some("suchen"),
        (true, false, false, Some('G'), _) | (false, false, false, None, F3) => Some("weitersuchen"),
        (true, true, false, Some('G'), _) | (false, true, false, None, F3) => Some("zuruecksuchen"),
        (true, false, false, Some('P'), _) => Some("drucken"),
        (true, true, false, Some('I' | 'J' | 'C'), _) | (false, false, false, None, F12) => Some("entwickler"),
        (false, false, false, None, F7) => Some("still"),
        _ => None,
    }
}

/// Blendet aus, was die Filterlisten für diese Seite kennen: erst die
/// seitenspezifischen Regeln, dann (falls die Seite das nicht abschaltet) die
/// allgemeinen, passend zu den Klassen und IDs, die auf der Seite vorkommen.
unsafe fn kosmetik_anwenden(core: &ICoreWebView2) {
    let url = quelle(core);
    let Some(kosmetik) = schild::kosmetik(&url) else { return };
    if !kosmetik.css.is_empty() {
        let _ = core.ExecuteScript(&HSTRING::from(stil_skript(&kosmetik.css)), OHNE_ANTWORT);
    }
    if kosmetik.generichide {
        return;
    }
    let ausnahmen = kosmetik.ausnahmen;
    let core_spaeter = core.clone();
    let _ = core.ExecuteScript(
        &HSTRING::from(KLASSEN_SAMMELN),
        &ExecuteScriptCompletedHandler::create(Box::new(move |fehler, json| {
            fehler?;
            if let Some(css) = schild::allgemeine_kosmetik(&url, &json, &ausnahmen) {
                let _ = core_spaeter.ExecuteScript(&HSTRING::from(stil_skript(&css)), OHNE_ANTWORT);
            }
            Ok(())
        })),
    );
}

const OHNE_ANTWORT: Option<&ICoreWebView2ExecuteScriptCompletedHandler> = None;

/// Sammelt Klassen und IDs der Seite, höchstens je 4000.
const KLASSEN_SAMMELN: &str = r#"(() => {
  const k = new Set(), i = new Set();
  for (const el of document.querySelectorAll('[class],[id]')) {
    if (el.id && i.size < 4000) i.add(el.id);
    for (const c of el.classList) { if (k.size < 4000) k.add(c); }
  }
  return { k: [...k], i: [...i] };
})()"#;

fn stil_skript(css: &str) -> String {
    let css = serde_json::to_string(css).unwrap_or_else(|_| "\"\"".into());
    format!(
        "(() => {{ const s = document.createElement('style'); s.dataset.msb = 'schild'; s.textContent = {css}; (document.head || document.documentElement).appendChild(s); }})()"
    )
}

#[cfg(test)]
mod tests {
    use super::kuerzel;

    #[test]
    fn kuerzel_des_browsers() {
        assert_eq!(kuerzel('T' as u32, true, false, false), Some("neuer_tab"));
        assert_eq!(kuerzel('T' as u32, true, true, false), Some("tab_wiederherstellen"));
        assert_eq!(kuerzel('L' as u32, true, false, false), Some("adresszeile"));
        assert_eq!(kuerzel(0x09, true, false, false), Some("naechster_tab"));
        assert_eq!(kuerzel(0x09, true, true, false), Some("voriger_tab"));
        assert_eq!(kuerzel('S' as u32, false, false, true), Some("singra"));
        // Was der Seite gehört, bleibt bei ihr.
        assert_eq!(kuerzel('C' as u32, true, false, false), None);
        assert_eq!(kuerzel('F' as u32, true, false, false), Some("suchen"));
        assert_eq!(kuerzel(0x7B, false, false, false), Some("entwickler"));
        assert_eq!(kuerzel('I' as u32, true, true, false), Some("entwickler"));
        assert_eq!(kuerzel('P' as u32, true, false, false), Some("drucken"));
        assert_eq!(kuerzel('A' as u32, true, false, false), None);
        assert_eq!(kuerzel('T' as u32, false, false, false), None);
    }
}
