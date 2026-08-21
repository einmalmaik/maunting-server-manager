//! Sichere Ablage des Refresh-Tokens im Betriebssystem-Tresor.
//!
//! Projektregel (hartes Stoppschild): Tokens liegen **nie** im Klartext auf
//! der Platte. Deshalb geht das Refresh-Token in den Windows Credential
//! Manager (bzw. macOS Keychain) — verschlüsselt vom Betriebssystem, an das
//! Benutzerkonto gebunden. Das Access-Token wird gar nicht persistiert: es
//! lebt nur im Speicher des Frontends und wird nach Neustart über den
//! Refresh-Weg neu geholt.
//!
//! Nur ein Eintrag, fester Dienst- und Kontoname: die App spricht mit genau
//! einem Backend (konfig.json kennt die URL), mehr Verwaltung wäre Code ohne
//! Anlass.

use keyring::Entry;

const DIENST: &str = "SingraSmartSystem";
const KONTO: &str = "refresh_token";

fn eintrag() -> Result<Entry, String> {
    Entry::new(DIENST, KONTO).map_err(|e| format!("Tresor nicht erreichbar: {e}"))
}

pub fn speichern(token: &str) -> Result<(), String> {
    eintrag()?
        .set_password(token)
        .map_err(|e| format!("Token nicht speicherbar: {e}"))
}

/// `None` heißt: kein Token hinterlegt (frische Installation oder Logout).
pub fn laden() -> Result<Option<String>, String> {
    match eintrag()?.get_password() {
        Ok(token) => Ok(Some(token)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("Token nicht lesbar: {e}")),
    }
}

/// Idempotent: ein fehlender Eintrag ist kein Fehler — Logout soll nie an
/// „war schon weg“ scheitern.
pub fn loeschen() -> Result<(), String> {
    match eintrag()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("Token nicht löschbar: {e}")),
    }
}
