//! System-Tray mit Statusfarben.
//!
//! Der Tray ist die einzige Stelle, an der der Zustand des Assistenten
//! sichtbar ist, wenn kein Fenster offen ist: das App-Logo (bereit), Blau
//! (hört zu), Lila (denkt), Gelb (spricht). Bereit zeigt bewusst **kein**
//! farbiges Icon — ein grüner Punkt im Tray liest sich als „nimmt gerade
//! auf", und genau das tut die App im Ruhezustand nicht. Die Icons sind zur
//! Compile-Zeit eingebettet — kein Dateizugriff zur Laufzeit, nichts, das
//! ein Installer vergessen kann.

#[cfg(not(target_os = "android"))]
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager,
};
#[cfg(target_os = "android")]
use tauri::{AppHandle, Manager};

const TRAY_ID: &str = "mss";

/// (Statusname, Tooltip, Icon) — die eine Tabelle, aus der Command und
/// Anzeige lesen. Ein neuer Status ist eine neue Zeile, kein neuer Code.
const STATUS: &[(&str, &str, &[u8])] = &[
    // Bereit = das Logo, nicht der grüne Kreis: Farbe signalisiert Aktivität.
    ("bereit", "MSS — bereit", include_bytes!("../icons/32x32.png")),
    ("hoert", "MSS — hört zu", include_bytes!("../icons/status-hoert.png")),
    ("denkt", "MSS — denkt", include_bytes!("../icons/status-denkt.png")),
    ("spricht", "MSS — spricht", include_bytes!("../icons/status-spricht.png")),
];

fn status_eintrag(status: &str) -> Option<&'static (&'static str, &'static str, &'static [u8])> {
    STATUS.iter().find(|(name, _, _)| *name == status)
}

/// Baut den Tray beim App-Start. Linksklick öffnet das Hauptfenster,
/// Rechtsklick das Menü (Öffnen/Kalender/Beenden).
#[cfg(not(target_os = "android"))]
pub fn erstellen(app: &AppHandle) -> tauri::Result<()> {
    let oeffnen = MenuItem::with_id(app, "oeffnen", "Öffnen", true, None::<&str>)?;
    let kalender = MenuItem::with_id(app, "kalender", "Kalender", true, None::<&str>)?;
    let beenden = MenuItem::with_id(app, "beenden", "Beenden", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&oeffnen, &kalender, &beenden])?;

    let (_, tooltip, bytes) = status_eintrag("bereit").expect("Status 'bereit' existiert");
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(tauri::image::Image::from_bytes(bytes)?)
        .tooltip(*tooltip)
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "oeffnen" => hauptfenster_zeigen(app),
            "kalender" => {
                hauptfenster_zeigen(app);
                if let Some(fenster) = app.get_webview_window("main") {
                    let _ = fenster.emit("mss:navigiere-zu", "/kalender");
                }
            }
            // Derselbe harte Ausgang wie im Schliessen-Dialog. `app.exit(0)`
            // stand hier und reihte die Bitte nur in die Ereignisschleife
            // ein — genau der Griff, den man benutzt, *weil* die App nicht
            // mehr reagiert, half dann nicht.
            "beenden" => crate::beenden_erzwingen(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                hauptfenster_zeigen(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg(target_os = "android")]
pub fn erstellen(_app: &AppHandle) -> tauri::Result<()> {
    Ok(())
}

/// Wechselt Icon und Tooltip auf den genannten Status.
/// Ein unbekannter Status ist ein Fehler des Aufrufers, kein stiller Default.
#[cfg(not(target_os = "android"))]
pub fn set_status(app: &AppHandle, status: &str) -> Result<(), String> {
    let (_, tooltip, bytes) =
        status_eintrag(status).ok_or_else(|| format!("Unbekannter Status: {status}"))?;
    let tray = app.tray_by_id(TRAY_ID).ok_or("Tray nicht initialisiert")?;
    let icon = tauri::image::Image::from_bytes(bytes).map_err(|e| e.to_string())?;
    tray.set_icon(Some(icon)).map_err(|e| e.to_string())?;
    tray.set_tooltip(Some(*tooltip)).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(target_os = "android")]
pub fn set_status(_app: &AppHandle, _status: &str) -> Result<(), String> {
    Ok(())
}

/// Holt das Hauptfenster nach vorn (Tray-Klick, Hotkey, Menü „Öffnen“).
pub fn hauptfenster_zeigen(app: &AppHandle) {
    if let Some(fenster) = app.get_webview_window("main") {
        let _ = fenster.show();
        #[cfg(not(target_os = "android"))]
        let _ = fenster.unminimize();
        let _ = fenster.set_focus();
    }
}

#[cfg(test)]
mod tests {
    use super::status_eintrag;

    #[test]
    fn alle_vier_zustaende_existieren() {
        for status in ["bereit", "hoert", "denkt", "spricht"] {
            assert!(status_eintrag(status).is_some(), "Status {status} fehlt");
        }
    }

    #[test]
    fn unbekannter_status_ist_keiner() {
        assert!(status_eintrag("kaputt").is_none());
    }

    #[test]
    fn icons_sind_png() {
        for (name, _, bytes) in super::STATUS {
            assert!(bytes.starts_with(b"\x89PNG"), "Icon fuer {name} ist kein PNG");
        }
    }
}
