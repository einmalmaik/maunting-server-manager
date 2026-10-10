//! Was Tauri von der WebView2 nicht herausgibt, hängt hier direkt daran.
//!
//! - jede Anfrage eines Tabs (auch aus Iframes und Workern) geht durch den
//!   Jugend- und Suchtschutz und das Schild und wird bei einem Treffer mit
//!   403 beantwortet (ein Ping mit 204), bevor sie das Netz erreicht; Anfragen an YouTube
//!   bekommen bei sicherer Suche den eingeschränkten Modus;
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
    take_pwstr, AcceleratorKeyPressedEventHandler, CallDevToolsProtocolMethodCompletedHandler,
    ContainsFullScreenElementChangedEventHandler, DOMContentLoadedEventHandler, FaviconChangedEventHandler, GetFaviconCompletedHandler,
    HistoryChangedEventHandler, NavigationCompletedEventHandler, NavigationStartingEventHandler, ProcessFailedEventHandler,
    SourceChangedEventHandler, WebResourceRequestedEventHandler,
};
use windows::core::{Interface, BOOL, HSTRING, PWSTR};
use windows::Win32::UI::Input::KeyboardAndMouse::{GetKeyState, VK_CONTROL, VK_ESCAPE, VK_SHIFT};

use crate::schild::{self, AnfrageArt};
use crate::tabs::{melden, vorab, TabEreignis, Vorab};

pub(super) unsafe fn text(lesen: impl FnOnce(*mut PWSTR) -> windows::core::Result<()>) -> String {
    let mut wert = PWSTR::null();
    if lesen(&mut wert).is_ok() {
        take_pwstr(wert)
    } else {
        String::new()
    }
}

pub(super) unsafe fn quelle(core: &ICoreWebView2) -> String {
    text(|p| core.Source(p))
}

unsafe fn verlauf(core: &ICoreWebView2) -> (bool, bool) {
    let (mut zurueck, mut vor) = (BOOL::default(), BOOL::default());
    let _ = core.CanGoBack(&mut zurueck);
    let _ = core.CanGoForward(&mut vor);
    (zurueck.as_bool(), vor.as_bool())
}

/// Die Adresse, zu der der Tab gerade als Ganzes navigiert. Die erste
/// Dokument-Anfrage dorthin ist die Seite selbst und wird nie geblockt; ein
/// Iframe mit einer Werbeadresse dagegen schon. Danach und mit dem Ende ihrer
/// Navigation gilt sie nicht mehr: nach einer abgebrochenen Navigation (204,
/// `window.stop()`) blieb sie bis 10.10.2026 stehen, und ein Iframe mit genau
/// dieser Adresse kam am Schild vorbei.
#[derive(Default)]
struct Hauptadresse(RefCell<Option<(u64, String)>>);

impl Hauptadresse {
    fn setzen(&self, navigation: u64, url: String) {
        *self.0.borrow_mut() = Some((navigation, url));
    }

    /// Endet eine ältere Navigation erst nach dem Start der neuen, bleibt die neue.
    fn beendet(&self, navigation: u64) {
        let mut a = self.0.borrow_mut();
        if a.as_ref().is_some_and(|(n, _)| *n == navigation) {
            *a = None;
        }
    }

    /// Ist `url` die Seite selbst? Nur einmal je Navigation.
    fn nehmen(&self, url: &str) -> bool {
        let mut a = self.0.borrow_mut();
        let treffer = a.as_ref().is_some_and(|(_, u)| u == url);
        if treffer {
            *a = None;
        }
        treffer
    }
}

pub(super) unsafe fn einrichten(
    app: &AppHandle,
    id: &str,
    controller: ICoreWebView2Controller,
    umgebung: ICoreWebView2Environment,
) -> windows::core::Result<()> {
    let core = controller.CoreWebView2()?;
    let mut token = 0i64;

    let hauptadresse = Rc::new(Hauptadresse::default());

    {
        let (app, id, hauptadresse) = (app.clone(), id.to_string(), hauptadresse.clone());
        core.add_NavigationStarting(
            &NavigationStartingEventHandler::create(Box::new(move |_, args| {
                if let Some(args) = args {
                    let mut navigation = 0u64;
                    args.NavigationId(&mut navigation)?;
                    hauptadresse.setzen(navigation, text(|p| args.Uri(p)));
                    schild::seitenwechsel(&app, &id);
                    crate::tabs::seite_vergessen(&id);
                }
                Ok(())
            })),
            &mut token,
        )?;
    }
    {
        let hauptadresse = hauptadresse.clone();
        core.add_NavigationCompleted(
            &NavigationCompletedEventHandler::create(Box::new(move |_, args| {
                if let Some(args) = args {
                    let mut navigation = 0u64;
                    args.NavigationId(&mut navigation)?;
                    hauptadresse.beendet(navigation);
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
                let Some(core15) = sender.and_then(|s| s.cast::<ICoreWebView2_15>().ok()) else { return Ok(()) };
                let url = text(|p| core15.FaviconUri(p));
                if !(url.starts_with("https://") || url.starts_with("http://")) {
                    melden(&app, TabEreignis::Favicon { id: id.clone(), url: None });
                    return Ok(());
                }
                // Das Bild aus der Webview, als Data-Adresse: die Oberfläche lud
                // die Adresse sonst selbst, mit ihrem Profil und am Schild vorbei
                // (bis 09.10.2026).
                let (app, id) = (app.clone(), id.clone());
                core15.GetFavicon(
                    COREWEBVIEW2_FAVICON_IMAGE_FORMAT_PNG,
                    &GetFaviconCompletedHandler::create(Box::new(move |fehler, strom| {
                        let bild = fehler.ok().and(strom).and_then(|s| unsafe { favicon_lesen(&s) });
                        melden(&app, TabEreignis::Favicon { id, url: bild });
                        Ok(())
                    })),
                )
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
                // Was der Jugend- und Suchtschutz sperrt, kommt auch nicht als
                // Iframe, Bild oder Skript einer anderen Seite. Die Seite selbst
                // meldet `NavigationStarting` als gesperrt (`grundereignisse.rs`).
                let hauptdokument = kontext == COREWEBVIEW2_WEB_RESOURCE_CONTEXT_DOCUMENT && hauptadresse.nehmen(&url);
                let entscheid = vorab(&url, hauptdokument);
                if matches!(entscheid, Vorab::Laden { youtube: true } | Vorab::Schild { youtube: true }) {
                    anfrage.Headers()?.SetHeader(&HSTRING::from("YouTube-Restrict"), &HSTRING::from("Strict"))?;
                }
                let art = anfrage_art(kontext);
                let blocken = match entscheid {
                    Vorab::Blocken(_) => true,
                    Vorab::Laden { .. } => false,
                    Vorab::Schild { .. } => schild::pruefen(&app, &id, &url, &quelle(&core), art),
                };
                if blocken {
                    let (status, text) = art.sperrantwort();
                    let antwort = umgebung.CreateWebResourceResponse(
                        None,
                        status,
                        &HSTRING::from(text),
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
            &ProcessFailedEventHandler::create(Box::new(move |_, args| {
                if let Some(args) = args {
                    super::absturz::verarbeiten(&app, &id, &args);
                }
                Ok(())
            })),
            &mut token,
        )?;
    }

    {
        let (app, id, seite) = (app.clone(), id.to_string(), core.clone());
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
                if taste == VK_ESCAPE.0 as u32 && !alt && im_vollbild(&app, &id) {
                    // Escape beendet das Vollbild immer, auch wenn die Seite
                    // die Taste für sich will (`navigator.keyboard.lock`): eine
                    // Seite im Vollbild kann sonst die Leisten des Browsers
                    // nachbauen. `seite.js` nimmt das Element heraus, damit die
                    // Seite es weiß; das Fenster geht auch ohne `seite.js` zurück.
                    args.SetHandled(true)?;
                    let _ = seite.PostWebMessageAsString(&HSTRING::from(VOLLBILD_AUS));
                    super::vollbild(&app, &id, false);
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

/// Die Nachricht an `seite.js`, die das Vollbild der Seite beendet.
const VOLLBILD_AUS: &str = r#"{"t":"vollbild_aus"}"#;

fn im_vollbild(app: &AppHandle, id: &str) -> bool {
    use tauri::Manager;
    app.state::<crate::tabs::Tabs>().0.lock().unwrap().vollbild.as_deref() == Some(id)
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
    const KOMMA: u32 = 0xBC;
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
        (true, false, false, None, KOMMA) => Some("einstellungen"),
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
        ohne_geste(core, &schild::stil_skript(&kosmetik.css), |_| {});
    }
    if kosmetik.generichide {
        return;
    }
    let ausnahmen = kosmetik.ausnahmen;
    let core_spaeter = core.clone();
    ohne_geste(core, schild::KLASSEN_SAMMELN, move |json| {
        if let Some(css) = schild::allgemeine_kosmetik(&url, json, &ausnahmen) {
            ohne_geste(&core_spaeter, &schild::stil_skript(&css), |_| {});
        }
    });
}

/// Ein Skript des Browsers in der Seite, ohne Nutzergeste; `danach` bekommt
/// den Rückgabewert als JSON. `ExecuteScript` läuft in der WebView2 wie ein
/// Klick: jede Seite bekam nach dem Laden 5 s Aktivierung (Popups, Ton,
/// Vollbild, Zwischenablage) und danach `hasBeenActive` (Laufzeitprobe
/// 10.10.2026). Ein dauerhaftes `Runtime.enable` braucht es dafür nicht.
pub(super) unsafe fn ohne_geste(core: &ICoreWebView2, skript: &str, danach: impl FnOnce(&str) + 'static) {
    let parameter = serde_json::json!({ "expression": skript, "returnByValue": true, "userGesture": false });
    let mut danach = Some(danach);
    let _ = core.CallDevToolsProtocolMethod(
        &HSTRING::from("Runtime.evaluate"),
        &HSTRING::from(parameter.to_string()),
        &CallDevToolsProtocolMethodCompletedHandler::create(Box::new(move |ergebnis, json| {
            ergebnis?;
            let wert = serde_json::from_str::<serde_json::Value>(&json).ok().and_then(|w| w.pointer("/result/value").map(|v| v.to_string()));
            if let (Some(wert), Some(danach)) = (wert, danach.take()) {
                danach(&wert);
            }
            Ok(())
        })),
    );
}

/// Ein Favicon als `data:image/png;base64,…`; `None`, wenn leer oder größer als 32 KiB.
unsafe fn favicon_lesen(strom: &windows::Win32::System::Com::IStream) -> Option<String> {
    use base64::Engine;
    const HOECHSTENS: usize = 32 * 1024;
    let mut bild = Vec::new();
    let mut puffer = [0u8; 4096];
    loop {
        let mut gelesen = 0u32;
        strom.Read(puffer.as_mut_ptr().cast(), puffer.len() as u32, Some(&mut gelesen)).ok().ok()?;
        if gelesen == 0 {
            break;
        }
        bild.extend_from_slice(&puffer[..gelesen as usize]);
        if bild.len() > HOECHSTENS {
            return None;
        }
    }
    (!bild.is_empty()).then(|| format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(bild)))
}

#[cfg(test)]
mod tests {
    use super::{kuerzel, Hauptadresse};

    #[test]
    fn die_hauptadresse_gilt_einmal_und_nur_fuer_ihre_navigation() {
        let a = Hauptadresse::default();
        a.setzen(1, "https://werbung.example/".into());
        assert!(a.nehmen("https://werbung.example/"));
        assert!(!a.nehmen("https://werbung.example/"), "ein Iframe danach ist keine Seite");

        // Abgebrochen, ohne dass das Dokument angefragt wurde.
        a.setzen(2, "https://werbung.example/".into());
        a.beendet(2);
        assert!(!a.nehmen("https://werbung.example/"));

        // Die vorige Navigation endet erst nach dem Start der neuen.
        a.setzen(3, "https://neu.example/".into());
        a.beendet(2);
        assert!(a.nehmen("https://neu.example/"));
    }

    /// `ExecuteScript` läuft wie ein Klick und gab jeder Seite nach dem Laden
    /// eine Aktivierung (bis 10.10.2026). In Tabs laufen Skripte des Browsers
    /// nur über `ohne_geste`; das Popup einer Erweiterung öffnet der Nutzer selbst.
    #[test]
    fn skripte_in_seiten_laufen_ohne_nutzergeste() {
        let verboten = concat!(".Execute", "Script(");
        for (datei, quelltext) in [
            ("desktop.rs", include_str!("../desktop.rs")),
            ("absturz.rs", include_str!("absturz.rs")),
            ("cookies.rs", include_str!("cookies.rs")),
            ("entwickler.rs", include_str!("entwickler.rs")),
            ("erweiterungen.rs", include_str!("erweiterungen.rs")),
            ("formulare.rs", include_str!("formulare.rs")),
            ("grundereignisse.rs", include_str!("grundereignisse.rs")),
            ("herunterladen.rs", include_str!("herunterladen.rs")),
            ("ohne_edge.rs", include_str!("ohne_edge.rs")),
            ("rueckfragen.rs", include_str!("rueckfragen.rs")),
            ("schlaf.rs", include_str!("schlaf.rs")),
            ("standbild.rs", include_str!("standbild.rs")),
            ("webview2.rs", include_str!("webview2.rs")),
        ] {
            assert!(!quelltext.contains(verboten), "{datei}");
        }
    }

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
        assert_eq!(kuerzel(0xBC, true, false, false), Some("einstellungen"));
        assert_eq!(kuerzel('A' as u32, true, false, false), None);
        assert_eq!(kuerzel('T' as u32, false, false, false), None);
    }
}
