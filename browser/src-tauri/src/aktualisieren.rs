//! Browser Updates: Dünner Wrapper um die zentrale `updater_core` Crate.
//! - Desktop: Velopack-Integration für GitHub Releases & One-Click-Installationen.
//! - Android: 3-stufige Streaming-, Integritäts- und PackageInstaller-Pipeline.

use tauri::AppHandle;
use updater_core::UpdateInfo;

pub use updater_core::{ist_neuer, SELBST_AKTUALISIEREN};

#[cfg(target_os = "android")]
pub use updater_core::android::ist_valides_apk;

/// Prüft, ob eine neue Version verfügbar ist.
#[tauri::command(async)]
pub async fn update_pruefen(app: AppHandle) -> Result<UpdateInfo, String> {
    updater_core::pruefen(&app).await
}

/// Lädt das Update im Hintergrund herunter.
#[tauri::command(async)]
pub async fn update_herunterladen(app: AppHandle) -> Result<(), String> {
    updater_core::herunterladen(&app).await
}

/// Führt die Installation aus (Desktop: Velopack Neustart; Android: APK-Installation).
#[tauri::command(async)]
pub async fn update_installieren(app: AppHandle) -> Result<(), String> {
    updater_core::installieren(&app).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(debug_assertions)]
    fn debug_bau_installiert_nie_ein_update() {
        assert!(!SELBST_AKTUALISIEREN);
    }

    #[test]
    fn version_vergleich_erkennt_neuere_versionen() {
        assert!(ist_neuer("0.2.0", "0.1.9"));
        assert!(ist_neuer("1.0.0", "0.9.9"));
        assert!(ist_neuer("v0.1.10", "0.1.9"));
        assert!(ist_neuer("0.1.9.1", "0.1.9"));
        assert!(!ist_neuer("0.1.9", "0.1.9"));
        assert!(!ist_neuer("0.1.8", "0.1.9"));
        assert!(!ist_neuer("0.1.0", "1.0.0"));
    }
}
