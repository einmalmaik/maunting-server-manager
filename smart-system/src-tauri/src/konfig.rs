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

/// Vorgaben der beiden globalen Hotkeys — Konstanten, damit `Default`,
/// Registrierung und Doku dieselbe Wahrheit tragen.
pub const HOTKEY_FENSTER_VORGABE: &str = "Alt+Space";
pub const HOTKEY_SPRACHE_VORGABE: &str = "Alt+Shift+Space";

#[derive(Serialize, Deserialize, Clone)]
#[serde(default)]
pub struct AppKonfig {
    /// Basis-URL des MSM-Backends (z. B. https://panel.example.com).
    pub backend_url: Option<String>,
    /// Arbeitsverzeichnis der Sandbox (Phase 5: einziger Schreibbereich der KI).
    pub sandbox_pfad: Option<String>,
    /// Der Einrichtungs-Assistent wurde vollständig durchlaufen.
    pub eingerichtet: bool,
    /// Globaler Hotkey: Hauptfenster zeigen/verstecken. `None` heißt bewusst
    /// deaktiviert. Fehlt das Feld in einer alten Datei, gilt die Vorgabe —
    /// deshalb der eigene `Default` unten statt `#[derive(Default)]`, dessen
    /// `None` hieße „abgeschaltet" statt „wie bisher".
    pub hotkey_fenster: Option<String>,
    /// Globaler Hotkey: Sprachsitzung im Overlay starten/beenden.
    pub hotkey_sprache: Option<String>,
}

impl Default for AppKonfig {
    fn default() -> Self {
        Self {
            backend_url: None,
            sandbox_pfad: None,
            eingerichtet: false,
            hotkey_fenster: Some(HOTKEY_FENSTER_VORGABE.into()),
            hotkey_sprache: Some(HOTKEY_SPRACHE_VORGABE.into()),
        }
    }
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
    use super::{
        sandbox_pfad_verboten, AppKonfig, HOTKEY_FENSTER_VORGABE, HOTKEY_SPRACHE_VORGABE,
    };

    #[test]
    fn alte_datei_ohne_hotkey_felder_bekommt_die_vorgaben() {
        // Die Felder kamen später dazu. Eine Datei aus der Zeit davor darf
        // die Hotkeys nicht verlieren — `Alt+Space` war immer an.
        let alt = r#"{"backend_url":"https://panel.example.com","sandbox_pfad":null,"eingerichtet":true}"#;
        let konfig: AppKonfig = serde_json::from_str(alt).unwrap();
        assert_eq!(konfig.hotkey_fenster.as_deref(), Some(HOTKEY_FENSTER_VORGABE));
        assert_eq!(konfig.hotkey_sprache.as_deref(), Some(HOTKEY_SPRACHE_VORGABE));
    }

    #[test]
    fn null_heisst_deaktiviert_und_ueberlebt_den_roundtrip() {
        // `null` ist eine Entscheidung, kein fehlendes Feld: der Benutzer hat
        // den Hotkey abgeschaltet, und Speichern + Laden darf daraus nicht
        // wieder die Vorgabe machen.
        let text = r#"{"hotkey_fenster":null,"hotkey_sprache":"Ctrl+Shift+K"}"#;
        let konfig: AppKonfig = serde_json::from_str(text).unwrap();
        assert_eq!(konfig.hotkey_fenster, None);
        assert_eq!(konfig.hotkey_sprache.as_deref(), Some("Ctrl+Shift+K"));

        let gespeichert = serde_json::to_string(&konfig).unwrap();
        let wieder: AppKonfig = serde_json::from_str(&gespeichert).unwrap();
        assert_eq!(wieder.hotkey_fenster, None);
        assert_eq!(wieder.hotkey_sprache.as_deref(), Some("Ctrl+Shift+K"));
    }

    #[test]
    fn windows_verzeichnis_ist_tabu() {
        assert!(sandbox_pfad_verboten("C:\\Windows"));
        assert!(sandbox_pfad_verboten("C:\\Windows\\System32"));
        assert!(sandbox_pfad_verboten("c:/windows/temp"));
    }

    #[test]
    fn benutzerordner_sind_erlaubt() {
        assert!(!sandbox_pfad_verboten("C:\\Users\\alex\\MSS-Sandbox"));
        assert!(!sandbox_pfad_verboten("D:\\Projekte"));
        // "Windows" als Namensbestandteil ausserhalb des Systempfads ist ok.
        assert!(!sandbox_pfad_verboten("C:\\WindowsAppsBackup"));
    }
}
