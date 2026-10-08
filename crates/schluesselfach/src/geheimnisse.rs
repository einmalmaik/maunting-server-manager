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
use tauri::{AppHandle, Manager, Runtime};

#[cfg(not(target_os = "android"))]
use keyring::Entry;

#[cfg(not(target_os = "android"))]
const KONTO: &str = "refresh_token";

const TOKEN_DATEI: &str = ".session_auth";

fn dateipfad<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    let basis = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("App-Datenverzeichnis unbekannt: {e}"))?;
    std::fs::create_dir_all(&basis).map_err(|e| e.to_string())?;
    Ok(basis.join(TOKEN_DATEI))
}

pub fn speichern<R: Runtime>(app: &AppHandle<R>, token: &str) -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        let dienst = crate::dienst()?;
        if let Ok(eintrag) = Entry::new(dienst.name, KONTO) {
            if eintrag.set_password(token).is_ok() {
                if let Ok(pfad) = dateipfad(app) {
                    if pfad.exists() {
                        let _ = std::fs::remove_file(pfad);
                    }
                }
                return Ok(());
            } else {
                // Bei Keyring-Schreibfehler eventuellen Alt-Eintrag leeren, damit kein veraltetes Token gelesen wird
                let _ = eintrag.delete_credential();
            }
        }
    }

    // Android oder Fallback bei Systemen ohne Desktop-Keyring / bei Keyring-Fehlern
    let pfad = dateipfad(app)?;
    std::fs::write(&pfad, token.as_bytes())
        .map_err(|e| format!("Token konnte nicht gespeichert werden: {e}"))?;

    Ok(())
}

/// `None` heißt: kein Token hinterlegt (frische Installation oder Logout).
pub fn laden<R: Runtime>(app: &AppHandle<R>) -> Result<Option<String>, String> {
    #[cfg(not(target_os = "android"))]
    {
        let dienst = crate::dienst()?;
        for name in std::iter::once(dienst.name).chain(dienst.frueher) {
            if let Ok(eintrag) = Entry::new(name, KONTO) {
                if let Ok(token) = eintrag.get_password() {
                    return Ok(Some(token));
                }
                // Kein Eintrag oder Fehler: nächster Name, dann die Datei.
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
pub fn loeschen<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        let dienst = crate::dienst()?;
        for name in std::iter::once(dienst.name).chain(dienst.frueher) {
            let _ = eintrag_loeschen(name);
        }
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
