//! Auf dem Desktop gibt es kein Widget. Von außen kommen nur Links: Windows
//! startet den Browser mit der Adresse als Argument, wenn er der
//! Standardbrowser ist (`windows/hooks.nsh`). Läuft er schon, reicht das
//! Single-Instance-Plugin die Argumente des zweiten Aufrufs herüber.

use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};

use crate::tabs::Tabs;

#[cfg(windows)]
mod windows_standard;

/// Länger ist keine Adresse, die der Browser speichert (`ADRESSE_MAX`).
const LINK_MAX: usize = 8192;

#[derive(Clone, Debug, PartialEq)]
struct LinkStart {
    url: String,
    privat: bool,
}

static LINK: Mutex<Option<LinkStart>> = Mutex::new(None);

/// Nimmt den ersten http(s)-Link aus den Argumenten eines Aufrufs; alles
/// andere fällt weg. Ob ein Tab die Adresse laden darf, prüft `tabs::weg`.
pub fn link_aufnehmen(app: &AppHandle, argumente: &[String]) {
    let link = argumente.iter().skip(1).find_map(|a| link_aus(a));
    let privat = argumente.iter().any(|a| a == "--privat");
    if link.is_some() || privat {
        *LINK.lock().unwrap() = Some(LinkStart {
            url: link.unwrap_or_default(),
            privat,
        });
        let _ = app.emit(super::EREIGNIS, ());
    }
}

pub fn link_aus(argument: &str) -> Option<String> {
    if argument.len() > LINK_MAX {
        return None;
    }
    let url = url::Url::parse(argument).ok()?;
    matches!(url.scheme(), "http" | "https" | "msb").then(|| url.to_string())
}

pub fn start(_: &AppHandle) -> Result<Option<Value>, String> {
    Ok(LINK.lock().unwrap().take().map(|info| {
        json!({
            "art": "link",
            "url": info.url,
            "privat": info.privat,
        })
    }))
}

pub fn stand(_: &AppHandle, _: bool) -> Result<(), String> {
    Ok(())
}

pub fn lage(_: &AppHandle) -> Result<String, String> {
    Ok("nein".into())
}

pub fn anheften(_: &AppHandle) -> Result<bool, String> {
    Ok(false)
}

#[cfg(windows)]
pub fn standardbrowser(_: &AppHandle) -> Result<bool, String> {
    Ok(windows_standard::ist_standard())
}

/// Windows lässt eine App sich nicht selbst festlegen; es zeigt die
/// Standard-Apps des Browsers, dort wählt man ihn.
#[cfg(windows)]
pub fn standardbrowser_werden(_: &AppHandle) -> Result<bool, String> {
    windows_standard::einstellungen_oeffnen()?;
    Ok(windows_standard::ist_standard())
}

#[cfg(windows)]
pub fn standardbrowser_einstellungen(app: &AppHandle) -> Result<bool, String> {
    standardbrowser_werden(app)
}

#[cfg(not(windows))]
pub fn standardbrowser(_: &AppHandle) -> Result<bool, String> {
    Ok(false)
}

#[cfg(not(windows))]
pub fn standardbrowser_werden(_: &AppHandle) -> Result<bool, String> {
    Err("Nur unter Windows und Android.".into())
}

#[cfg(not(windows))]
pub fn standardbrowser_einstellungen(_: &AppHandle) -> Result<bool, String> {
    Err("Nur unter Windows und Android.".into())
}

pub fn tastatur_zeigen(_: &AppHandle) -> Result<(), String> {
    Ok(())
}

pub fn bildsuche(_: &AppHandle, _: &Tabs, _: &str, _: bool, _: url::Url, _: &str, _: bool) -> Result<(), String> {
    Err("Die Bildsuche gibt es nur auf Android.".into())
}

#[cfg(test)]
mod tests {
    use super::link_aus;

    #[test]
    fn nimmt_nur_webadressen_von_aussen() {
        assert_eq!(link_aus("https://example.org/a?b=1").as_deref(), Some("https://example.org/a?b=1"));
        assert_eq!(link_aus("http://example.org").as_deref(), Some("http://example.org/"));
        for fremd in ["--remote-debugging-port=1", "file:///C:/Windows/win.ini", "javascript:alert(1)", "C:\\a.html", "ms-settings:"] {
            assert_eq!(link_aus(fremd), None, "{fremd}");
        }
        assert_eq!(link_aus(&format!("https://example.org/{}", "a".repeat(9000))), None);
    }
}
