//! Auf dem Desktop gibt es kein Widget. Von außen kommen nur Links: Windows
//! startet den Browser mit der Adresse als Argument, wenn er der
//! Standardbrowser ist (`standardbrowser_registrieren`). Läuft er schon, reicht das
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
    matches!(url.scheme(), "http" | "https" | "msb" | "pontis").then(|| url.to_string())
}

pub fn start(_: &AppHandle) -> Result<Option<Value>, String> {    Ok(LINK.lock().unwrap().take().map(|info| {
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

/// Das Wetter-Widget gibt es nur unter Android.
pub fn wetter_setzen(
    _: &AppHandle,
    _ort: &str,
    _land: &str,
    _lat: f64,
    _lon: f64,
    _einheit: &str,
    _token: &str,
) -> Result<(), String> {
    Err("Das Wetter-Widget gibt es nur unter Android".into())
}

pub fn wetter_lage(_: &AppHandle) -> Result<String, String> {
    Ok("nein".into())
}

pub fn wetter_anheften(_: &AppHandle) -> Result<bool, String> {
    Ok(false)
}

#[cfg(windows)]
pub fn standardbrowser(_: &AppHandle) -> Result<bool, String> {
    Ok(windows_standard::ist_standard())
}

/// Meldet den Browser bei Windows als `StartMenuInternet`-Client an. Das
/// gehörte bis 10/2026 in den NSIS-Installer; seit Velopack der einzige
/// Installer ist, rufen es dessen Hooks (`main.rs`), der Start (`lib.rs`) und
/// der Knopf „Standardbrowser werden“. Ohne Anmeldung listet Windows den
/// Browser nicht.
#[cfg(windows)]
pub fn standardbrowser_registrieren() -> Result<(), String> {
    // Ein Debug-Bau fässt die installierte App nicht an (AGENTS.md, Punkt 67):
    // er liefe sonst als Standardbrowser und jede besuchte Adresse öffnete
    // die Dev-Sitzung samt Debug-Port.
    if cfg!(debug_assertions) {
        return Ok(());
    }
    let exe = std::env::current_exe()
        .map_err(|e| format!("Eigener Programmpfad nicht lesbar: {e}"))?;
    windows_standard::registrieren(&exe)
}

/// Nimmt die Anmeldung bei der Deinstallation zurück.
#[cfg(windows)]
pub fn standardbrowser_entfernen() -> Result<(), String> {
    windows_standard::entfernen()
}

/// Windows lässt eine App sich nicht selbst festlegen; es zeigt die
/// Standard-Apps des Browsers, dort wählt man ihn. Vorher die eigene
/// Anmeldung nachholen: Installationen vor dem Velopack-Wechsel haben sie
/// nur im NSIS-Installer geschrieben.
#[cfg(windows)]
pub fn standardbrowser_werden(_: &AppHandle) -> Result<bool, String> {
    standardbrowser_registrieren()?;
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
        // Beide eigenen Schemata dürfen von außen kommen; `msb` bleibt für
        // bestehende Verknüpfungen lesbar, `pontis` ist der neue Name.
        assert!(link_aus("pontis://einstellungen/suche").is_some());
        assert!(link_aus("msb://einstellungen").is_some());
        // Groß-/Kleinschreibung des Schemas ist für URLs unerheblich.
        assert_eq!(link_aus("HTTPS://Example.ORG/A").as_deref(), Some("https://example.org/A"));
        for fremd in [
            "--remote-debugging-port=1",
            "file:///C:/Windows/win.ini",
            "javascript:alert(1)",
            "data:text/html,<b>x</b>",
            "ftp://example.org/a",
            "mailto:hilfe@example.org",
            "tel:+4930123456",
            "//example.org/ohne-schema",
            "https://",
            "C:\\a.html",
            "ms-settings:",
        ] {
            assert_eq!(link_aus(fremd), None, "{fremd}");
        }
        assert_eq!(link_aus(&format!("https://example.org/{}", "a".repeat(9000))), None);
        // Genau an der Grenze bleibt die Adresse erhalten.
        let grenze = format!("https://example.org/{}", "a".repeat(super::LINK_MAX - "https://example.org/".len()));
        assert_eq!(grenze.len(), super::LINK_MAX);
        assert!(link_aus(&grenze).is_some());
    }
}
