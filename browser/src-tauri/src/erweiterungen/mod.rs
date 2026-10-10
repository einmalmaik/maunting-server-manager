//! Erweiterungen aus dem Chrome Web Store und entpackte Ordner (nur Windows;
//! die Android-WebView kennt keine). Die Befehle gibt es auf beiden
//! Plattformen, unter Android antworten sie mit `nur_windows`.

#[cfg(windows)]
pub mod crx;
#[cfg(windows)]
pub mod dienst;
#[cfg(windows)]
pub mod laden;
#[cfg(windows)]
pub mod liste;
#[cfg(windows)]
pub mod paket;

#[cfg(windows)]
pub use dienst::{beim_start, schutz_geaendert, soll};

use serde_json::Value;
use tauri::AppHandle;

#[cfg(not(windows))]
const NUR_WINDOWS: &str = "nur_windows";

macro_rules! nur_windows {
    ($($aufruf:tt)*) => {{
        #[cfg(windows)]
        return $($aufruf)*;
        #[cfg(not(windows))]
        return Err(NUR_WINDOWS.to_string());
    }};
}

#[tauri::command(async)]
pub fn erweiterungen_liste(app: AppHandle) -> Result<Value, String> {
    let _ = &app;
    nur_windows!(Ok(dienst::stand(&app)))
}

/// Lädt und prüft ein Paket (Store-Link oder Kennung, im Entwicklermodus ein
/// Ordner) und liefert Name und Rechte für die Rückfrage.
#[tauri::command(async)]
pub async fn erweiterung_pruefen(app: AppHandle, quelle: String, entpackt: bool, sprache: String) -> Result<Value, String> {
    let _ = (&app, &quelle, entpackt, &sprache);
    nur_windows!(dienst::pruefen(app, quelle, entpackt, sprache).await)
}

#[tauri::command(async)]
pub fn erweiterung_installieren(app: AppHandle, vorgang: String) -> Result<Value, String> {
    let _ = (&app, &vorgang);
    nur_windows!(dienst::installieren(&app, &vorgang))
}

#[tauri::command(async)]
pub fn erweiterung_verwerfen(app: AppHandle, vorgang: String) -> Result<(), String> {
    let _ = (&app, &vorgang);
    nur_windows!({
        dienst::verwerfen(&app, &vorgang);
        Ok(())
    })
}

#[tauri::command(async)]
pub fn erweiterung_schalten(app: AppHandle, id: String, an: bool) -> Result<Value, String> {
    let _ = (&app, &id, an);
    nur_windows!(dienst::schalten(&app, &id, an))
}

#[tauri::command(async)]
pub fn erweiterung_anheften(app: AppHandle, id: String, an: bool) -> Result<Value, String> {
    let _ = (&app, &id, an);
    nur_windows!(dienst::anheften(&app, &id, an))
}

#[tauri::command(async)]
pub fn erweiterung_entfernen(app: AppHandle, id: String) -> Result<Value, String> {
    let _ = (&app, &id);
    nur_windows!(dienst::entfernen(&app, &id))
}

#[tauri::command(async)]
pub fn erweiterungen_entwicklermodus(app: AppHandle, an: bool) -> Result<Value, String> {
    let _ = (&app, an);
    nur_windows!(dienst::entwicklermodus(&app, an))
}

/// Öffnet das Popup der Erweiterung unter dem Knopf: `rechts` und `oben` in
/// CSS-Pixeln der Oberfläche.
#[tauri::command(async)]
pub fn erweiterung_popup(app: AppHandle, id: String, rechts: f64, oben: f64) -> Result<(), String> {
    let _ = (&app, &id, rechts, oben);
    nur_windows!(dienst::seite(&app, &id, true).and_then(|url| crate::tabs::erweiterung_popup(&app, url, rechts, oben)))
}

#[tauri::command(async)]
pub fn erweiterung_popup_schliessen(app: AppHandle) -> Result<(), String> {
    let _ = &app;
    nur_windows!({
        crate::tabs::erweiterung_popup_schliessen(&app);
        Ok(())
    })
}

/// Adresse der Optionsseite; die Oberfläche öffnet sie als Tab.
#[tauri::command(async)]
pub fn erweiterung_optionen(app: AppHandle, id: String) -> Result<String, String> {
    let _ = (&app, &id);
    nur_windows!(dienst::seite(&app, &id, false))
}
