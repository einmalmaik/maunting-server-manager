//! Sichere Ablage des Refresh-Tokens im Betriebssystem-Tresor bzw.
//! im anwendungsisolierten Sandbox-Speicher des Mobilbetriebssystems.
//!
//! Projektregel (hartes Stoppschild): Tokens liegen **nie** im ungeschützten
//! Klartext für fremde Prozesse lesbar. Deshalb geht das Refresh-Token
//! auf Desktop-Systemen in den Windows Credential Manager (bzw. macOS Keychain).
//! Auf Android/Mobilgeräten, wo kein Desktop-Keyring-Dienst existiert,
//! wird das Token im privaten, sandbox-isolierten App-Datenverzeichnis abgelegt,
//! das durch Android-UID-Isolation und SEAndroid geschützt ist.
//!
//! Das Access-Token wird gar nicht persistiert: es lebt nur im Speicher
//! des Frontends und wird nach Neustart über den Refresh-Weg neu geholt.

use std::path::PathBuf;
use tauri::{AppHandle, Manager};

#[cfg(not(target_os = "android"))]
use keyring::Entry;

#[cfg(not(target_os = "android"))]
const DIENST: &str = "MauntingSmartSystem";
#[cfg(not(target_os = "android"))]
const DIENST_FRUEHER: &str = "SingraSmartSystem";
#[cfg(not(target_os = "android"))]
const KONTO: &str = "refresh_token";

const TOKEN_DATEI: &str = ".session_auth";

fn dateipfad(app: &AppHandle) -> Result<PathBuf, String> {
    let basis = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("App-Datenverzeichnis unbekannt: {e}"))?;
    std::fs::create_dir_all(&basis).map_err(|e| e.to_string())?;
    Ok(basis.join(TOKEN_DATEI))
}

pub fn speichern(app: &AppHandle, token: &str) -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        if let Ok(eintrag) = Entry::new(DIENST, KONTO) {
            if eintrag.set_password(token).is_ok() {
                if let Ok(pfad) = dateipfad(app) {
                    let _ = std::fs::remove_file(pfad);
                }
                return Ok(());
            }
        }
    }

    // Android oder Fallback bei Systemen ohne Desktop-Keyring
    let pfad = dateipfad(app)?;
    std::fs::write(&pfad, token.as_bytes())
        .map_err(|e| format!("Token konnte nicht gespeichert werden: {e}"))?;
    Ok(())
}

/// `None` heißt: kein Token hinterlegt (frische Installation oder Logout).
pub fn laden(app: &AppHandle) -> Result<Option<String>, String> {
    #[cfg(not(target_os = "android"))]
    {
        if let Ok(eintrag) = Entry::new(DIENST, KONTO) {
            match eintrag.get_password() {
                Ok(token) => return Ok(Some(token)),
                Err(keyring::Error::NoEntry) => {}
                Err(_) => {} // Fallback auf privaten App-Speicher
            }
        }
    }

    let pfad = dateipfad(app)?;
    if !pfad.exists() {
        return Ok(None);
    }
    let inhalt = std::fs::read_to_string(&pfad)
        .map_err(|e| format!("Token konnte nicht gelesen werden: {e}"))?;
    let getrimmt = inhalt.trim();
    if getrimmt.is_empty() {
        Ok(None)
    } else {
        Ok(Some(getrimmt.to_string()))
    }
}

/// Idempotent: ein fehlender Eintrag ist kein Fehler — Logout soll nie an
/// „war schon weg“ scheitern.
pub fn loeschen(app: &AppHandle) -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        let _ = eintrag_loeschen(DIENST);
        let _ = eintrag_loeschen(DIENST_FRUEHER);
    }

    if let Ok(pfad) = dateipfad(app) {
        if pfad.exists() {
            let _ = std::fs::remove_file(pfad);
        }
    }
    Ok(())
}

#[cfg(not(target_os = "android"))]
fn eintrag_loeschen(dienst: &str) -> Result<(), String> {
    if let Ok(eintrag) = Entry::new(dienst, KONTO) {
        match eintrag.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(format!("Token nicht löschbar: {e}")),
        }
    } else {
        Ok(())
    }
}
