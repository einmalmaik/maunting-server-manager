//! Unkritische App-Einstellungen als JSON im App-Datenverzeichnis.
//!
//! Hier liegt **nie** ein Geheimnis: Tokens gehören in den OS-Tresor
//! (geheimnisse.rs), hier stehen nur Backend-URL, Sandbox-Pfad und der
//! Einrichtungsstand. Die Datei ist bewusst trivial — eine Struktur, eine
//! Datei, kein Migrationssystem: neue Felder kommen mit `#[serde(default)]`
//! dazu und fehlen in alten Dateien einfach.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

const DATEI: &str = "konfig.json";

#[derive(Serialize, Deserialize, Clone, Default)]
#[serde(default)]
pub struct AppKonfig {
    /// Basis-URL des MSM-Backends (z. B. https://panel.example.com).
    pub backend_url: Option<String>,
    /// Arbeitsverzeichnis der Sandbox (Phase 5: einziger Schreibbereich der KI).
    pub sandbox_pfad: Option<String>,
    /// Der Einrichtungs-Assistent wurde vollständig durchlaufen.
    pub eingerichtet: bool,
}

fn pfad(app: &AppHandle) -> Result<PathBuf, String> {
    let basis = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("App-Datenverzeichnis unbekannt: {e}"))?;
    std::fs::create_dir_all(&basis).map_err(|e| e.to_string())?;
    Ok(basis.join(DATEI))
}

pub fn laden(app: &AppHandle) -> Result<AppKonfig, String> {
    let pfad = pfad(app)?;
    if !pfad.exists() {
        return Ok(AppKonfig::default());
    }
    let text = std::fs::read_to_string(&pfad).map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| format!("konfig.json unlesbar: {e}"))
}

/// Anti-Zerstörungs-Invariante, erste Schranke: die Sandbox darf nie in
/// Systemverzeichnissen liegen. Phase 5 prüft zusätzlich bei **jedem**
/// Dateizugriff — das hier verhindert nur, dass so ein Pfad überhaupt
/// gespeichert wird.
fn sandbox_pfad_verboten(pfad: &str) -> bool {
    let normal = Path::new(pfad)
        .to_string_lossy()
        .to_lowercase()
        .replace('/', "\\");
    let windir = std::env::var("WINDIR")
        .unwrap_or_else(|_| "C:\\Windows".into())
        .to_lowercase();
    normal == windir || normal.starts_with(&format!("{windir}\\"))
}

pub fn speichern(app: &AppHandle, konfig: &AppKonfig) -> Result<(), String> {
    if let Some(sandbox) = konfig.sandbox_pfad.as_deref() {
        if sandbox_pfad_verboten(sandbox) {
            return Err("Die Sandbox darf nicht im Windows-Verzeichnis liegen".into());
        }
    }
    if let Some(url) = konfig.backend_url.as_deref() {
        if !(url.starts_with("https://") || url.starts_with("http://")) {
            return Err("Backend-URL muss mit https:// oder http:// beginnen".into());
        }
    }
    let text = serde_json::to_string_pretty(konfig).map_err(|e| e.to_string())?;
    std::fs::write(pfad(app)?, text).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::sandbox_pfad_verboten;

    #[test]
    fn windows_verzeichnis_ist_tabu() {
        assert!(sandbox_pfad_verboten("C:\\Windows"));
        assert!(sandbox_pfad_verboten("C:\\Windows\\System32"));
        assert!(sandbox_pfad_verboten("c:/windows/temp"));
    }

    #[test]
    fn benutzerordner_sind_erlaubt() {
        assert!(!sandbox_pfad_verboten("C:\\Users\\alex\\SingraSandbox"));
        assert!(!sandbox_pfad_verboten("D:\\Projekte"));
        // "Windows" als Namensbestandteil ausserhalb des Systempfads ist ok.
        assert!(!sandbox_pfad_verboten("C:\\WindowsAppsBackup"));
    }
}
