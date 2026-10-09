//! Was den Browser unter Android von außen startet: das Such-Widget
//! (`SuchWidget.kt`, `WidgetActivity.kt`) mit Suche, Spracheingabe oder einem
//! Foto für die Bildsuche, und Links aus anderen Apps (`WidgetPlugin.kt`).
//! Was anstößt, holt die Oberfläche ab (`widget_start`); jeden neuen Anstoß
//! meldet Rust als [`EREIGNIS`]. Dazu, ob der Browser der Standardbrowser
//! ist. Auf dem Desktop gibt es nichts davon, die Befehle tun nichts.

use serde_json::Value;
use tauri::{AppHandle, State};

#[cfg(target_os = "android")]
mod android;
#[cfg(target_os = "android")]
pub use android::init;
#[cfg(target_os = "android")]
use android as plattform;
#[cfg(not(target_os = "android"))]
mod ohne;
#[cfg(not(target_os = "android"))]
use ohne as plattform;

use crate::tabs::{id_pruefen, weg, ziel_pruefen, Tabs, Weg};

pub const EREIGNIS: &str = "msb:widget";

/// `{art: "suche" | "text" | "bild", text?}` oder nichts.
#[tauri::command(async)]
pub fn widget_start(app: AppHandle) -> Result<Option<Value>, String> {
    plattform::start(&app)
}

/// Die gewählte Suchmaschine sucht Bilder: das Widget zeigt die Kamera.
#[tauri::command(async)]
pub fn widget_stand(app: AppHandle, bildsuche: bool) -> Result<(), String> {
    plattform::stand(&app, bildsuche)
}

/// Liegt das Widget schon auf dem Startbildschirm (`liegt`), lässt es sich
/// per Bitte ablegen (`anheftbar`) oder nur von Hand (`nein`)?
#[tauri::command(async)]
pub fn widget_lage(app: AppHandle) -> Result<String, String> {
    plattform::lage(&app)
}

/// Bittet den Startbildschirm, das Widget aufzunehmen; der fragt selbst nach.
#[tauri::command(async)]
pub fn widget_anheften(app: AppHandle) -> Result<bool, String> {
    plattform::anheften(&app)
}

/// Nach der Suche aus dem Widget: die Tastatur für die Adresszeile, die
/// die Oberfläche fokussiert hat (ein `focus()` aus Skript zeigt sie nicht).
#[tauri::command(async)]
pub fn tastatur_zeigen(app: AppHandle) -> Result<(), String> {
    plattform::tastatur_zeigen(&app)
}

/// Ist der Browser der Standardbrowser des Telefons?
#[tauri::command(async)]
pub fn standardbrowser(app: AppHandle) -> Result<bool, String> {
    plattform::standardbrowser(&app)
}

/// Android fragt selbst nach; danach der Stand.
#[tauri::command(async)]
pub fn standardbrowser_werden(app: AppHandle) -> Result<bool, String> {
    plattform::standardbrowser_werden(&app)
}

/// Öffnet die Standard-Apps in den Einstellungen, wenn Android nicht mehr fragt.
#[tauri::command(async)]
pub fn standardbrowser_einstellungen(app: AppHandle) -> Result<bool, String> {
    plattform::standardbrowser_einstellungen(&app)
}

/// Ein Formularfeld der Suchmaschine: nur Buchstaben und `_`.
fn feld_pruefen(feld: &str) -> Result<(), String> {
    let gut = (1..=32).contains(&feld.len()) && feld.chars().all(|c| c.is_ascii_alphabetic() || c == '_');
    gut.then_some(()).ok_or_else(|| "Ungültiges Feld".into())
}

/// Schickt das Foto des Widgets in einem neuen Tab an die Bildsuche. Nur über
/// HTTPS und nur an eine Adresse, die Sperre und Jugendschutz durchlassen.
#[tauri::command(async)]
pub fn bildsuche(
    app: AppHandle,
    tabs: State<'_, Tabs>,
    id: String,
    privat: bool,
    url: String,
    feld: String,
    base64: bool,
) -> Result<(), String> {
    id_pruefen(&id)?;
    feld_pruefen(&feld)?;
    let ziel = ziel_pruefen(&url)?;
    crate::schild::schutz_dienst::bereit();
    if ziel.scheme() != "https" || weg(&url, false) != Weg::Laden {
        return Err("Diese Bildsuche kann der Browser nicht öffnen.".into());
    }
    crate::tabs::eintragen(&tabs, &id, privat);
    plattform::bildsuche(&app, &tabs, &id, privat, ziel, &feld, base64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn felder_bleiben_namen() {
        assert!(feld_pruefen("encoded_image").is_ok());
        assert!(feld_pruefen("imageBin").is_ok());
        for schlecht in ["", "a\"b", "a b", "x".repeat(33).as_str(), "feld<"] {
            assert!(feld_pruefen(schlecht).is_err(), "{schlecht}");
        }
    }
}
