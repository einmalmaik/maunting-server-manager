//! Das Such-Widget unter Android (`SuchWidget.kt`, `WidgetActivity.kt`):
//! Suche, Spracheingabe oder ein Foto für die Bildsuche. Was es anstößt, holt
//! die Oberfläche ab (`widget_start`); jeden neuen Anstoß meldet Rust als
//! [`EREIGNIS`]. Auf dem Desktop gibt es kein Widget, die Befehle tun nichts.

use serde_json::Value;
use tauri::{AppHandle, State};

use crate::tabs::{id_pruefen, weg, ziel_pruefen, Tabs, Weg};

pub const EREIGNIS: &str = "msb:widget";

/// `{art: "suche" | "text" | "bild", text?}` oder nichts.
#[tauri::command(async)]
pub fn widget_start(app: AppHandle) -> Result<Option<Value>, String> {
    #[cfg(target_os = "android")]
    return crate::tabs::android::widget_start(&app);
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Ok(None)
    }
}

/// Die gewählte Suchmaschine sucht Bilder: das Widget zeigt die Kamera.
#[tauri::command(async)]
pub fn widget_stand(app: AppHandle, bildsuche: bool) -> Result<(), String> {
    #[cfg(target_os = "android")]
    return crate::tabs::android::widget_stand(&app, bildsuche);
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, bildsuche);
        Ok(())
    }
}

/// Liegt das Widget schon auf dem Startbildschirm (`liegt`), lässt es sich
/// per Bitte ablegen (`anheftbar`) oder nur von Hand (`nein`)?
#[tauri::command(async)]
pub fn widget_lage(app: AppHandle) -> Result<String, String> {
    #[cfg(target_os = "android")]
    return crate::tabs::android::widget_lage(&app);
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Ok("nein".into())
    }
}

/// Bittet den Startbildschirm, das Widget aufzunehmen; der fragt selbst nach.
#[tauri::command(async)]
pub fn widget_anheften(app: AppHandle) -> Result<bool, String> {
    #[cfg(target_os = "android")]
    return crate::tabs::android::widget_anheften(&app);
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Ok(false)
    }
}

/// Nach der Suche aus dem Widget: die Tastatur für die Adresszeile, die
/// die Oberfläche fokussiert hat (ein `focus()` aus Skript zeigt sie nicht).
#[tauri::command(async)]
pub fn tastatur_zeigen(app: AppHandle) -> Result<(), String> {
    #[cfg(target_os = "android")]
    return crate::tabs::android::tastatur_zeigen(&app);
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Ok(())
    }
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
    if ziel.scheme() != "https" || weg(&url) != Weg::Laden {
        return Err("Diese Bildsuche kann der Browser nicht öffnen.".into());
    }
    crate::tabs::eintragen(&tabs, &id, privat);
    #[cfg(target_os = "android")]
    return crate::tabs::android::bildsuche(&app, &tabs, &id, privat, ziel, &feld, base64);
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, base64);
        Err("Die Bildsuche gibt es nur auf Android.".into())
    }
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
