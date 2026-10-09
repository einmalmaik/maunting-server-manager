//! Updates unter Windows: `tauri-plugin-updater` liest `latest-msb.json` aus
//! dem jüngsten GitHub-Release und prüft die Signatur gegen den Schlüssel in
//! `tauri.conf.json` (derselbe wie beim Smart System). Installiert wird nur
//! auf Klick, denn der Installer beendet den Browser samt offener Tabs.
//! „Beim Schließen vergessen“ greift dann beim nächsten Start
//! (`browserdaten::beim_start`).
//!
//! Unter Android aktualisiert sich der Browser nicht selbst; die Befehle
//! melden dort nichts.

use tauri::AppHandle;

/// Ein Debug-Bau (`tauri dev`) installiert nie ein Update: der Installer
/// ersetzt die installierte App, nicht den Entwicklungsbau (AGENTS.md, Punkt 67).
pub const SELBST_AKTUALISIEREN: bool = !cfg!(debug_assertions);

/// Die neuere Version, wenn es eine gibt.
#[tauri::command(async)]
pub async fn update_pruefen(app: AppHandle) -> Result<Option<String>, String> {
    plattform::pruefen(&app).await
}

/// Lädt das Update, prüft die Signatur und startet den Installer. Der Browser
/// endet dabei.
#[tauri::command(async)]
pub async fn update_installieren(app: AppHandle) -> Result<(), String> {
    if !SELBST_AKTUALISIEREN {
        return Err("Ein Entwicklungsbau installiert keine Updates.".into());
    }
    plattform::installieren(&app).await
}

#[cfg(desktop)]
mod plattform {
    use tauri::AppHandle;
    use tauri_plugin_updater::UpdaterExt;

    pub async fn pruefen(app: &AppHandle) -> Result<Option<String>, String> {
        let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
        Ok(update.map(|u| u.version.trim_start_matches('v').to_string()))
    }

    pub async fn installieren(app: &AppHandle) -> Result<(), String> {
        let update = app
            .updater()
            .map_err(|e| e.to_string())?
            .check()
            .await
            .map_err(|e| e.to_string())?
            .ok_or("Kein Update verfügbar")?;
        update.download_and_install(|_, _| {}, || {}).await.map_err(|e| e.to_string())
    }
}

#[cfg(mobile)]
mod plattform {
    use tauri::AppHandle;

    pub async fn pruefen(_app: &AppHandle) -> Result<Option<String>, String> {
        Ok(None)
    }

    pub async fn installieren(_app: &AppHandle) -> Result<(), String> {
        Err("Unter Android kommt ein Update als neues APK.".into())
    }
}

#[cfg(test)]
mod tests {
    #[test]
    #[cfg(debug_assertions)]
    fn debug_bau_installiert_nie_ein_update() {
        assert!(!super::SELBST_AKTUALISIEREN);
    }

    /// Der Browser liest sein eigenes Manifest. `latest.json` gehört dem
    /// Smart System; dieselbe Datei für beide Apps hätte die eine mit dem
    /// Installer der anderen überschrieben.
    #[test]
    fn der_browser_liest_sein_eigenes_manifest() {
        let konfig: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let updater = &konfig["plugins"]["updater"];
        let endpunkte = updater["endpoints"].as_array().unwrap();
        assert_eq!(endpunkte.len(), 1);
        assert_eq!(
            endpunkte[0],
            "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/latest-msb.json"
        );
        assert!(!updater["pubkey"].as_str().unwrap().is_empty());
        assert_eq!(konfig["bundle"]["createUpdaterArtifacts"], true);
    }
}
