//! Screenshots einer Seite: der sichtbare Teil oder (nur Windows) die ganze
//! Seite, als PNG.
//!
//! Aufgenommen wird nur der Tab, der vorne liegt, und nur auf Klick. Das Bild
//! geht an die Oberfläche, die zuschneidet, kopiert oder es zum Speichern
//! zurückschickt; es liegt bis dahin nur im Speicher. Gespeichert wird wie
//! ein Download in den Download-Ordner und steht danach in dessen Liste.

use base64::Engine as _;
use serde::Serialize;
use tauri::{AppHandle, State};

use super::{id_pruefen, plattform, Tabs};
use crate::downloads;

/// Ein Bild der Seite. `abgeschnitten`: die Seite war länger, als Chromium
/// auf einmal zeichnet.
#[derive(Serialize)]
pub struct Aufnahme {
    pub png: String,
    pub abgeschnitten: bool,
}

/// So viel nimmt Rust zum Speichern an (Base64).
const HOECHSTENS: usize = 192 * 1024 * 1024;
const PNG: &[u8] = b"\x89PNG\r\n\x1a\n";
const ADRESSE_MAX: usize = 4096;

#[tauri::command(async)]
pub fn tab_aufnahme(app: AppHandle, tabs: State<'_, Tabs>, id: String, ganz: bool) -> Result<Aufnahme, String> {
    id_pruefen(&id)?;
    if tabs.0.lock().unwrap().aktiv.as_deref() != Some(id.as_str()) {
        return Err("Nur der Tab, der vorne liegt".into());
    }
    plattform::aufnahme(&app, &id, ganz)
}

/// Legt ein PNG im Download-Ordner ab und gibt zurück, wie die Datei heißt
/// (unter Windows mit Pfad). `url` ist die Seite, nur für die Download-Liste.
#[tauri::command(async)]
pub fn aufnahme_speichern(app: AppHandle, id: String, name: String, url: String, png: String) -> Result<String, String> {
    id_pruefen(&id)?;
    if png.len() > HOECHSTENS || url.len() > ADRESSE_MAX {
        return Err("Zu groß".into());
    }
    let daten = base64::engine::general_purpose::STANDARD.decode(png).map_err(|_| "Kein Bild".to_string())?;
    if !daten.starts_with(PNG) {
        return Err("Kein PNG".into());
    }
    speichern(&app, &id, &png_name(&name), url, daten)
}

/// Ein sicherer Dateiname, der auf `.png` endet.
fn png_name(name: &str) -> String {
    let name = downloads::sicherer_name(name);
    if name.to_lowercase().ends_with(".png") {
        name
    } else {
        downloads::sicherer_name(&format!("{name}.png"))
    }
}

#[cfg(windows)]
fn speichern(app: &AppHandle, id: &str, name: &str, url: String, daten: Vec<u8>) -> Result<String, String> {
    use super::{melden, DownloadStand, TabEreignis};
    let nr = downloads::naechste_nr();
    let ergebnis = downloads::erzeugt_ablegen(app, name, &daten).map(|p| p.to_string_lossy().into_owned());
    let (stand, datei) = match &ergebnis {
        Ok(pfad) => (DownloadStand::Fertig, Some(pfad.clone())),
        Err(_) => (DownloadStand::Fehler, None),
    };
    melden(app, TabEreignis::Download { id: id.to_string(), nr, stand, url, datei });
    ergebnis.map_err(|e| e.to_string())
}

/// Kotlin legt per MediaStore ab und meldet selbst (`Herunterladen.bild`).
#[cfg(target_os = "android")]
fn speichern(app: &AppHandle, id: &str, name: &str, url: String, daten: Vec<u8>) -> Result<String, String> {
    super::android::bild_speichern(app, id, name, &url, &daten)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn namen_enden_auf_png() {
        assert_eq!(png_name("Screenshot example.org 2026-10-10 01-02-03.png"), "Screenshot example.org 2026-10-10 01-02-03.png");
        assert_eq!(png_name("bild"), "bild.png");
        assert_eq!(png_name("bild.exe"), "bild.exe.png");
        assert_eq!(png_name("..\\..\\x\u{202E}gnp.exe"), "x_gnp.exe.png");
        assert_eq!(png_name("con"), "_con.png");
    }
}
