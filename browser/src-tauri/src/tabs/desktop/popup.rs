//! Das Popup einer Erweiterung (`action.default_popup`). WebView2 zeigt keine
//! Leiste für Erweiterungen und damit auch kein Popup; hier ist es ein eigener
//! kleiner Controller im Profil der Seiten, unter dem Knopf der Oberfläche.
//! Es passt sich seinem Inhalt an (höchstens 800 × 600 wie in Chrome) und
//! schließt bei Fokusverlust und mit Escape. Links öffnen als Tab.
//!
//! Grenze: fragt das Popup nach „dem aktiven Tab“, findet es keinen; die
//! Tabs gehören nicht zu einem Browserfenster, das WebView2 kennt.

use std::cell::RefCell;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager};
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    AcceleratorKeyPressedEventHandler, ExecuteScriptCompletedHandler, FocusChangedEventHandler, NavigationCompletedEventHandler,
    NavigationStartingEventHandler, NewWindowRequestedEventHandler,
};
use windows::core::HSTRING;

use super::{auf_ui, controller, fehler, rechteck, umgebung};
use crate::tabs::navigation_erlaubt;

/// Die Oberfläche: das Popup ist zu (`null`) oder will einen Tab öffnen.
pub const EREIGNIS: &str = "msb:erweiterung-popup";

thread_local! {
    static POPUP: RefCell<Option<ICoreWebView2Controller>> = const { RefCell::new(None) };
}

/// Rechte obere Ecke unter dem Knopf, in CSS-Pixeln der Oberfläche.
#[derive(Clone, Copy)]
struct Anker {
    rechts: f64,
    oben: f64,
}

const MESSEN: &str = "(() => { const h = document.documentElement; const w = h.style.width; h.style.width = 'max-content'; \
const r = [h.scrollWidth, h.scrollHeight]; h.style.width = w; return r })()";

/// Bleibt die Navigation in derselben Erweiterung? Nicht über `Url::origin`:
/// für `chrome-extension:` ist die Herkunft undurchsichtig und gleicht keiner
/// anderen, auch nicht sich selbst. Das Popup wies so seine eigene Seite ab
/// und öffnete sie als Tab (in der Laufzeitprobe, 10.10.2026).
fn gleiche_erweiterung(start: &url::Url, ziel: &str) -> bool {
    url::Url::parse(ziel).is_ok_and(|z| z.scheme() == start.scheme() && z.host_str().is_some() && z.host_str() == start.host_str())
}

unsafe fn zu(app: &AppHandle) {
    if let Some(alt) = POPUP.with(|p| p.borrow_mut().take()) {
        let _ = alt.Close();
        let _ = app.emit_to("main", EREIGNIS, serde_json::json!({ "zu": true }));
    }
}

/// Fragt die Seite nach ihrer Größe und passt das Popup an. UI-Faden.
unsafe fn anpassen(app: &AppHandle, anker: Anker) {
    let Some(c) = POPUP.with(|p| p.borrow().clone()) else { return };
    let Ok(core) = c.CoreWebView2() else { return };
    let app = app.clone();
    let _ = core.ExecuteScript(
        &HSTRING::from(MESSEN),
        &ExecuteScriptCompletedHandler::create(Box::new(move |fehler, json| {
            fehler?;
            let masse: Vec<f64> = serde_json::from_str(&json).unwrap_or_default();
            let [breite, hoehe] = masse[..] else { return Ok(()) };
            let fenster = app.get_webview_window("main").and_then(|f| Some((f.inner_size().ok()?, f.scale_factor().ok()?)));
            let (fb, fh) = fenster.map(|(g, s)| (g.width as f64 / s, g.height as f64 / s)).unwrap_or((800.0, 600.0));
            let breite = breite.clamp(25.0, 800.0);
            let hoehe = hoehe.clamp(25.0, 600.0).min(fh - anker.oben - 8.0).max(25.0);
            let links = (anker.rechts - breite).clamp(0.0, (fb - breite).max(0.0));
            let _ = c.SetBounds(rechteck(&app, links, anker.oben, breite, hoehe));
            Ok(())
        })),
    );
}

/// Unter dieser Kennung meldet das Popup seine Downloads; einen Tab gibt es dazu nicht.
const POPUP_ID: &str = "erweiterung-popup";

pub fn oeffnen(app: &AppHandle, url: String, rechts: f64, oben: f64) -> Result<(), String> {
    let anker = Anker { rechts, oben };
    let app2 = app.clone();
    auf_ui(app, move || unsafe {
        zu(&app2);
        let umgebung = umgebung(&app2)?;
        let c = controller(&app2, &umgebung, false)?;
        let core = c.CoreWebView2().map_err(fehler)?;
        // Wie ein Tab: ohne Edge-Oberfläche, Downloads über Quarantäne und
        // Virenschutz. Ein Kontextmenü braucht das Popup nicht.
        super::ohne_edge::einstellungen(&core).map_err(fehler)?;
        core.Settings().and_then(|e| e.SetAreDefaultContextMenusEnabled(false)).map_err(fehler)?;
        super::herunterladen::anbinden(&app2, POPUP_ID, &core, false).map_err(fehler)?;
        let start = url::Url::parse(&url).map_err(fehler)?;
        let mut token = 0i64;
        // Der Wechsel von about:blank auf die Seite der Erweiterung nimmt dem
        // Popup den Fokus (neuer Prozess). Fokus und Fokusverlust zählen erst
        // nach dem Laden, sonst schloss es sich, bevor es zu sehen war.
        let geladen = Arc::new(AtomicBool::new(false));
        {
            // Das Popup bleibt auf seiner Erweiterung; alles andere wird ein Tab.
            let app = app2.clone();
            let start = start.clone();
            core.add_NavigationStarting(
                &NavigationStartingEventHandler::create(Box::new(move |_, args| {
                    let Some(args) = args else { return Ok(()) };
                    let ziel = super::webview2::text(|p| args.Uri(p));
                    if gleiche_erweiterung(&start, &ziel) {
                        return Ok(());
                    }
                    args.SetCancel(true)?;
                    if url::Url::parse(&ziel).is_ok_and(|u| navigation_erlaubt(&u) && u.scheme() != "about") {
                        let _ = app.emit_to("main", EREIGNIS, serde_json::json!({ "tab": ziel }));
                    }
                    Ok(())
                })),
                &mut token,
            )
            .map_err(fehler)?;
        }
        {
            let app = app2.clone();
            core.add_NewWindowRequested(
                &NewWindowRequestedEventHandler::create(Box::new(move |_, args| {
                    let Some(args) = args else { return Ok(()) };
                    args.SetHandled(true)?;
                    let mut geste = windows::core::BOOL::default();
                    args.IsUserInitiated(&mut geste)?;
                    let ziel = super::webview2::text(|p| args.Uri(p));
                    if geste.as_bool() && url::Url::parse(&ziel).is_ok_and(|u| navigation_erlaubt(&u) && u.scheme() != "about") {
                        let _ = app.emit_to("main", EREIGNIS, serde_json::json!({ "tab": ziel }));
                    }
                    Ok(())
                })),
                &mut token,
            )
            .map_err(fehler)?;
        }
        {
            // Viele Popups bauen sich erst nach dem Laden auf; dreimal messen.
            let app = app2.clone();
            let geladen = geladen.clone();
            let fokus = c.clone();
            core.add_NavigationCompleted(
                &NavigationCompletedEventHandler::create(Box::new(move |_, _| {
                    if !geladen.swap(true, Ordering::SeqCst) {
                        let _ = fokus.MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
                    }
                    anpassen(&app, anker);
                    let app = app.clone();
                    std::thread::spawn(move || {
                        for warten in [300, 700] {
                            std::thread::sleep(Duration::from_millis(warten));
                            let a = app.clone();
                            let _ = app.run_on_main_thread(move || anpassen(&a, anker));
                        }
                    });
                    Ok(())
                })),
                &mut token,
            )
            .map_err(fehler)?;
        }
        {
            let app = app2.clone();
            let geladen = geladen.clone();
            c.add_LostFocus(
                &FocusChangedEventHandler::create(Box::new(move |_, _| {
                    if !geladen.load(Ordering::SeqCst) {
                        return Ok(());
                    }
                    // Nicht im eigenen Rückruf schließen.
                    let a = app.clone();
                    let _ = app.run_on_main_thread(move || zu(&a));
                    Ok(())
                })),
                &mut token,
            )
            .map_err(fehler)?;
        }
        {
            let app = app2.clone();
            c.add_AcceleratorKeyPressed(
                &AcceleratorKeyPressedEventHandler::create(Box::new(move |_, args| {
                    let Some(args) = args else { return Ok(()) };
                    let mut art = COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN;
                    args.KeyEventKind(&mut art)?;
                    let mut taste = 0u32;
                    args.VirtualKey(&mut taste)?;
                    if art == COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN && taste == 0x1B {
                        args.SetHandled(true)?;
                        let a = app.clone();
                        let _ = app.run_on_main_thread(move || zu(&a));
                    }
                    Ok(())
                })),
                &mut token,
            )
            .map_err(fehler)?;
        }
        c.SetBounds(rechteck(&app2, (rechts - 320.0).max(0.0), oben, 320.0, 120.0)).map_err(fehler)?;
        c.SetIsVisible(true).map_err(fehler)?;
        core.Navigate(&HSTRING::from(url.as_str())).map_err(fehler)?;
        POPUP.with(|p| *p.borrow_mut() = Some(c));
        Ok(())
    })
}

pub fn schliessen(app: &AppHandle) {
    let a = app.clone();
    let _ = app.run_on_main_thread(move || unsafe { zu(&a) });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn das_popup_bleibt_auf_seiner_erweiterung() {
        let start = url::Url::parse("chrome-extension://ddkjiahejlhfcafbddmgiahcphecmpfh/popup.html").unwrap();
        assert!(gleiche_erweiterung(&start, "chrome-extension://ddkjiahejlhfcafbddmgiahcphecmpfh/popup.html"));
        assert!(gleiche_erweiterung(&start, "chrome-extension://ddkjiahejlhfcafbddmgiahcphecmpfh/dashboard.html#x"));
        assert!(!gleiche_erweiterung(&start, "chrome-extension://eimadpbcbfnmbkopoojfekhnkhdbieeh/popup.html"));
        assert!(!gleiche_erweiterung(&start, "https://ddkjiahejlhfcafbddmgiahcphecmpfh/popup.html"));
        assert!(!gleiche_erweiterung(&start, "about:blank"));
    }
}
