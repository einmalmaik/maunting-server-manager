//! Speichert den Datenexport des Kontos dort, wo der Mensch es im Dialog sagt.
//!
//! Den Pfad bestimmt nur der Speichern-Dialog, nie das Fenster: ein Befehl, der
//! Bytes an einen Pfad aus JS schreibt, wäre ein Schreibzugriff auf die ganze
//! Platte für jedes Skript im WebView. Aus JS kommen nur die Bytes (roher
//! IPC-Körper) und ein Dateiname als Vorschlag.

use tauri::ipc::{InvokeBody, Request};
use tauri_plugin_dialog::DialogExt;

/// Nur Buchstaben, Ziffern, Punkt, Strich und Unterstrich, und immer `.zip`.
pub fn sicherer_dateiname(vorschlag: &str) -> String {
    let name: String = vorschlag
        .chars()
        .filter(|z| z.is_ascii_alphanumeric() || matches!(z, '.' | '-' | '_'))
        .take(80)
        .collect();
    let name = name.trim_start_matches('.');
    if name.is_empty() {
        return "msm-datenexport.zip".to_string();
    }
    if name.to_ascii_lowercase().ends_with(".zip") {
        name.to_string()
    } else {
        format!("{name}.zip")
    }
}

/// Öffnet den Speichern-Dialog und schreibt. `false` heißt: abgebrochen.
#[tauri::command(async)]
pub async fn export_speichern(app: tauri::AppHandle, request: Request<'_>) -> Result<bool, String> {
    let InvokeBody::Raw(inhalt) = request.body() else {
        return Err("Der Export kam nicht als Datei an.".to_string());
    };
    let inhalt = inhalt.clone();
    let vorschlag = request
        .headers()
        .get("x-dateiname")
        .and_then(|wert| wert.to_str().ok())
        .unwrap_or("");
    let dateiname = sicherer_dateiname(vorschlag);

    // Der Dialog blockiert bis zur Antwort, deshalb nicht auf dem Hauptthread.
    let ziel = tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_file_name(dateiname)
            .add_filter("ZIP", &["zip"])
            .blocking_save_file()
    })
    .await
    .map_err(|fehler| fehler.to_string())?;

    let Some(ziel) = ziel else {
        return Ok(false);
    };
    let pfad = ziel
        .into_path()
        .map_err(|_| "An diesem Ort kann die App nicht speichern.".to_string())?;
    std::fs::write(&pfad, inhalt).map_err(|fehler| format!("Speichern fehlgeschlagen: {fehler}"))?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::sicherer_dateiname;

    #[test]
    fn pfadteile_fallen_weg() {
        assert_eq!(sicherer_dateiname("../../Windows/system32/x"), "Windowssystem32x.zip");
        assert_eq!(sicherer_dateiname("C:\\Users\\a.zip"), "CUsersa.zip");
    }

    #[test]
    fn immer_zip_und_nie_leer() {
        assert_eq!(sicherer_dateiname("msm-datenexport-2026-09-27.zip"), "msm-datenexport-2026-09-27.zip");
        assert_eq!(sicherer_dateiname("bericht.exe"), "bericht.exe.zip");
        assert_eq!(sicherer_dateiname("..."), "msm-datenexport.zip");
        assert_eq!(sicherer_dateiname(""), "msm-datenexport.zip");
    }
}
