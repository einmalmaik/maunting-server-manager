use std::fs;
use std::path::PathBuf;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrowserKonfig {
    pub backend_url: Option<String>,
    pub access_token: Option<String>,
    pub standard_suchmaschine: String, // "google", "duckduckgo", "ecosia", "brave", "bing", "searxng"
    pub searxng_url: Option<String>,
    pub adblock_aktiv: bool,
    pub tracker_schutz: bool,
    pub cookies_beim_schliessen_loeschen: bool,
    pub javascript_erlaubt: bool,
    pub ki_chat_aktiv: bool,
    pub messenger_aktiv: bool,
    pub passwort_manager_aktiv: bool,
    pub autofill_aktiv: bool,
    pub theme: String, // "system", "dark", "light"
    pub download_ordner: Option<String>,
}

impl Default for BrowserKonfig {
    fn default() -> Self {
        Self {
            backend_url: None,
            access_token: None,
            standard_suchmaschine: "google".to_string(),
            searxng_url: None,
            adblock_aktiv: true,
            tracker_schutz: true,
            cookies_beim_schliessen_loeschen: false,
            javascript_erlaubt: true,
            ki_chat_aktiv: true,
            messenger_aktiv: true,
            passwort_manager_aktiv: true,
            autofill_aktiv: true,
            theme: "system".to_string(),
            download_ordner: None,
        }
    }
}

fn konfig_pfad(app: &AppHandle) -> Result<PathBuf, String> {
    let mut pfad = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("Konfigurationspfad nicht ermittelbar: {e}"))?;
    fs::create_dir_all(&pfad).map_err(|e| format!("Ordner konnte nicht erstellt werden: {e}"))?;
    pfad.push("msb_konfig.json");
    Ok(pfad)
}

#[tauri::command]
pub fn konfig_laden(app: AppHandle) -> Result<BrowserKonfig, String> {
    let pfad = konfig_pfad(&app)?;
    if !pfad.exists() {
        return Ok(BrowserKonfig::default());
    }
    let inhalt = fs::read_to_string(&pfad).map_err(|e| format!("Fehler beim Lesen der Konfig: {e}"))?;
    serde_json::from_str(&inhalt).map_err(|e| format!("Ungültiges JSON in Konfiguration: {e}"))
}

#[tauri::command]
pub fn konfig_speichern(app: AppHandle, konfig: BrowserKonfig) -> Result<(), String> {
    let pfad = konfig_pfad(&app)?;
    let json = serde_json::to_string_pretty(&konfig)
        .map_err(|e| format!("Fehler bei Serialisierung: {e}"))?;
    fs::write(&pfad, json).map_err(|e| format!("Fehler beim Schreiben der Konfig: {e}"))?;
    Ok(())
}
