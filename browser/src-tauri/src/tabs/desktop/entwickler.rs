//! Die Entwicklerwerkzeuge des Browsers: das DevTools-Protokoll eines Tabs.
//!
//! Die Werkzeuge von Edge sind aus (`ohne_edge.rs`). Stattdessen spricht die
//! Oberfläche das DevTools-Protokoll der WebView2 im Prozess an, ohne
//! Debug-Port, den ein anderer Prozess auf dem Rechner mitbenutzen könnte.
//! Welche Bereiche laufen, schaltet die Oberfläche, und nur, solange die
//! Werkzeuge für diesen Tab offen sind: manche Seiten erkennen ein
//! eingeschaltetes `Runtime` oder `Debugger` und verhalten sich dann anders.
//!
//! Durch kommt nur, was die Werkzeuge brauchen ([`erlaubt`]). Nichts davon
//! navigiert am Filter vorbei, legt Skripte für spätere Seiten an, reicht
//! Dateien in die Seite oder schaltet Zertifikatsprüfungen ab.

use std::sync::mpsc;
use std::time::Duration;

use serde_json::Value;
use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2;
use webview2_com::{CallDevToolsProtocolMethodCompletedHandler, DevToolsProtocolEventReceivedEventHandler};
use windows::core::HSTRING;

use super::webview2::text;
use crate::tabs::{melden, TabEreignis};

/// Bereiche, deren Methoden durchgehen.
const BEREICHE: [&str; 15] = [
    "Accessibility.",
    "CSS.",
    "CacheStorage.",
    "DOM.",
    "DOMDebugger.",
    "DOMStorage.",
    "Debugger.",
    "Emulation.",
    "IndexedDB.",
    "Log.",
    "Network.",
    "Overlay.",
    "Performance.",
    "Runtime.",
    "Storage.",
];

/// Aus `Page` nur, was liest oder neu lädt.
const AUS_PAGE: [&str; 6] = ["Page.enable", "Page.disable", "Page.getFrameTree", "Page.getResourceTree", "Page.getResourceContent", "Page.reload"];

/// In einem erlaubten Bereich, aber gesperrt.
const GESPERRT: [&str; 5] = [
    // Eine Bindung ist für die Seite sichtbar und bleibt über Seitenwechsel.
    "Runtime.addBinding",
    // Abfangen und Laden am Filter vorbei.
    "Network.setRequestInterception",
    "Network.loadNetworkResource",
    // Dateien des Rechners in ein Feld der Seite, oder deren Pfad heraus.
    "DOM.setFileInputFiles",
    "DOM.getFileInfo",
];

/// Ereignisse, die an die Oberfläche gehen. Sie kommen nur, solange ihr
/// Bereich eingeschaltet ist.
const EREIGNISSE: [&str; 29] = [
    "Runtime.consoleAPICalled",
    "Runtime.exceptionThrown",
    "Runtime.executionContextsCleared",
    "Log.entryAdded",
    "Network.requestWillBeSent",
    "Network.requestWillBeSentExtraInfo",
    "Network.responseReceived",
    "Network.responseReceivedExtraInfo",
    "Network.loadingFinished",
    "Network.loadingFailed",
    "Network.requestServedFromCache",
    "Network.webSocketCreated",
    "Network.webSocketFrameSent",
    "Network.webSocketFrameReceived",
    "Network.webSocketClosed",
    "DOM.documentUpdated",
    "DOM.setChildNodes",
    "DOM.childNodeInserted",
    "DOM.childNodeRemoved",
    "DOM.childNodeCountUpdated",
    "DOM.attributeModified",
    "DOM.attributeRemoved",
    "DOM.characterDataModified",
    "CSS.styleSheetAdded",
    "Overlay.inspectNodeRequested",
    "Overlay.inspectModeCanceled",
    "Debugger.scriptParsed",
    "Debugger.paused",
    "Debugger.resumed",
];

/// Größer geht kein Ereignis an die Oberfläche.
const EREIGNIS_MAX: usize = 512 * 1024;
/// Eine Nachricht eines WebSockets wird darüber gekürzt.
const RAHMEN_MAX: usize = 64 * 1024;
/// Größer wird keine Antwort (Quelltext, Antwortkörper).
const ANTWORT_MAX: usize = 16 * 1024 * 1024;
const PARAMETER_MAX: usize = 1024 * 1024;

/// Ob die Oberfläche diese Methode rufen darf.
pub(crate) fn erlaubt(methode: &str) -> bool {
    let form = methode.len() <= 80
        && methode.split('.').count() == 2
        && methode.split('.').all(|t| !t.is_empty() && t.chars().all(|c| c.is_ascii_alphanumeric()));
    form && !GESPERRT.contains(&methode) && (BEREICHE.iter().any(|b| methode.starts_with(b)) || AUS_PAGE.contains(&methode))
}

fn kuerzen(s: &str, max: usize) -> String {
    if s.len() <= max {
        return s.to_string();
    }
    let mut ende = max;
    while !s.is_char_boundary(ende) {
        ende -= 1;
    }
    format!("{}…", &s[..ende])
}

/// Hält ein Ereignis klein genug für die Oberfläche; `None`, wenn es das nicht wird.
pub(crate) fn knapp(name: &str, mut werte: Value) -> Option<Value> {
    if name.starts_with("Network.webSocketFrame") {
        if let Some(Value::String(daten)) = werte.pointer_mut("/response/payloadData") {
            *daten = kuerzen(daten, RAHMEN_MAX);
        }
    }
    let laenge = |w: &Value| serde_json::to_string(w).map(|s| s.len()).unwrap_or(usize::MAX);
    if laenge(&werte) <= EREIGNIS_MAX {
        return Some(werte);
    }
    // Den Körper einer Anfrage holt die Oberfläche bei Bedarf selbst
    // (`Network.getRequestPostData`), den Aufrufstapel braucht sie nicht ganz.
    if let Some(anfrage) = werte.get_mut("request").and_then(Value::as_object_mut) {
        anfrage.remove("postData");
        anfrage.remove("postDataEntries");
    }
    if let Some(ausloeser) = werte.get_mut("initiator").and_then(Value::as_object_mut) {
        ausloeser.remove("stack");
    }
    (laenge(&werte) <= EREIGNIS_MAX).then_some(werte)
}

pub unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    for name in EREIGNISSE {
        let empfaenger = core.GetDevToolsProtocolEventReceiver(&HSTRING::from(name))?;
        let (app, id) = (app.clone(), id.to_string());
        let mut token = 0i64;
        empfaenger.add_DevToolsProtocolEventReceived(
            &DevToolsProtocolEventReceivedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                let json = text(|p| args.ParameterObjectAsJson(p));
                if let Some(daten) = serde_json::from_str::<Value>(&json).ok().and_then(|w| knapp(name, w)) {
                    melden(&app, TabEreignis::Protokoll { id: id.clone(), methode: name.to_string(), daten });
                }
                Ok(())
            })),
            &mut token,
        )?;
    }
    Ok(())
}

/// Ruft eine Methode des Protokolls im Tab und wartet auf die Antwort.
/// `{"error":{"code":-32000,"message":"…"},"id":1}` → die Meldung; sonst der Text, wie er kam.
fn fehlertext(json: &str) -> Option<String> {
    if json.trim().is_empty() {
        return None;
    }
    let meldung = serde_json::from_str::<Value>(json)
        .ok()
        .and_then(|w| w.pointer("/error/message").or_else(|| w.get("message")).and_then(Value::as_str).map(str::to_string));
    Some(kuerzen(meldung.as_deref().unwrap_or(json), 500))
}

pub fn rufen(app: &AppHandle, id: &str, methode: String, parameter: Value) -> Result<Value, String> {
    if !erlaubt(&methode) {
        return Err(format!("{methode} ist in den Entwicklerwerkzeugen nicht erlaubt"));
    }
    if !parameter.is_object() {
        return Err("Parameter müssen ein Objekt sein".into());
    }
    let parameter = parameter.to_string();
    if parameter.len() > PARAMETER_MAX {
        return Err("Parameter zu groß".into());
    }
    let rx = super::mit_tab(app, id, move |tab| {
        let (tx, rx) = mpsc::channel::<Result<String, String>>();
        unsafe {
            tab.core
                .CallDevToolsProtocolMethod(
                    &HSTRING::from(methode),
                    &HSTRING::from(parameter),
                    &CallDevToolsProtocolMethodCompletedHandler::create(Box::new(move |ergebnis, json| {
                        // Bei einem Fehler steht die Meldung des Protokolls im JSON, das HRESULT sagt nur E_INVALIDARG.
                        let _ = tx.send(ergebnis.map(|()| json.clone()).map_err(|e| fehlertext(&json).unwrap_or_else(|| e.to_string())));
                        Ok(())
                    })),
                )
                .map_err(|e| e.to_string())?;
        }
        Ok(rx)
    })?;
    let json = rx.recv_timeout(Duration::from_secs(30)).map_err(|_| "Keine Antwort von der Seite".to_string())??;
    if json.len() > ANTWORT_MAX {
        return Err("Antwort zu groß".into());
    }
    serde_json::from_str(&json).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fehler_nennen_die_meldung_des_protokolls() {
        let antwort = r#"{"error":{"code":-32000,"message":"Agent is not enabled."},"id":30}"#;
        assert_eq!(fehlertext(antwort).as_deref(), Some("Agent is not enabled."));
        assert_eq!(fehlertext(""), None);
    }

    #[test]
    fn nur_was_die_werkzeuge_brauchen() {
        for m in ["DOM.getDocument", "Runtime.evaluate", "Network.getResponseBody", "Debugger.resume", "Page.getResourceContent"] {
            assert!(erlaubt(m), "{m}");
        }
        for m in [
            "Page.navigate",
            "Page.addScriptToEvaluateOnNewDocument",
            "Page.setBypassCSP",
            "Target.createTarget",
            "Browser.close",
            "Security.setIgnoreCertificateErrors",
            "Fetch.enable",
            "Input.dispatchMouseEvent",
            "Runtime.addBinding",
            "DOM.setFileInputFiles",
            "Network.loadNetworkResource",
            "DOM.getDocument.x",
            "DOMx.getDocument",
            "DOM.",
            "",
        ] {
            assert!(!erlaubt(m), "{m}");
        }
    }

    #[test]
    fn grosse_ereignisse_werden_knapp_oder_fallen() {
        let gross = "x".repeat(EREIGNIS_MAX + 10);
        let anfrage = serde_json::json!({"requestId": "1", "request": {"url": "https://a.example/", "postData": gross}, "initiator": {"type": "script"}});
        let k = knapp("Network.requestWillBeSent", anfrage).unwrap();
        assert!(k.pointer("/request/postData").is_none());
        assert_eq!(k.pointer("/request/url").and_then(Value::as_str), Some("https://a.example/"));

        let rahmen = serde_json::json!({"requestId": "2", "response": {"payloadData": "ä".repeat(RAHMEN_MAX)}});
        let gekuerzt = knapp("Network.webSocketFrameReceived", rahmen).unwrap();
        let daten = gekuerzt.pointer("/response/payloadData").and_then(Value::as_str).unwrap();
        assert!(daten.len() <= RAHMEN_MAX + 3 && daten.ends_with('…'));

        let unteilbar = serde_json::json!({"args": [{"type": "string", "value": gross}]});
        assert!(knapp("Runtime.consoleAPICalled", unteilbar).is_none());
    }
}
