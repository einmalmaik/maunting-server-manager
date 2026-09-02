//! Biometrische Authentifizierung (Windows Hello / Fingerabdruck / Gesicht / PIN)
//! über die native Windows Runtime API (UserConsentVerifier).

#[cfg(windows)]
use windows::Security::Credentials::UI::{
    UserConsentVerificationResult, UserConsentVerifier, UserConsentVerifierAvailability,
};

#[cfg(windows)]
pub async fn pruefe_verfuegbarkeit() -> bool {
    match UserConsentVerifier::CheckAvailabilityAsync() {
        Ok(op) => match op.await {
            Ok(availability) => availability == UserConsentVerifierAvailability::Available,
            Err(_) => false,
        },
        Err(_) => false,
    }
}

#[cfg(windows)]
pub async fn verifiziere_benutzer(nachricht: &str) -> Result<bool, String> {
    let msg = windows::core::HSTRING::from(nachricht);
    match UserConsentVerifier::RequestVerificationAsync(&msg) {
        Ok(op) => match op.await {
            Ok(result) => Ok(result == UserConsentVerificationResult::Verified),
            Err(e) => Err(format!("Windows Hello Fehler: {e}")),
        },
        Err(e) => Err(format!("Windows Hello konnte nicht gestartet werden: {e}")),
    }
}

#[cfg(not(target_os = "android"))]
use keyring::Entry;

#[cfg(not(target_os = "android"))]
const DIENST: &str = "MauntingSmartSystem";
#[cfg(not(target_os = "android"))]
const KONTO_VAULT_BIO: &str = "vault_biometric_key";

pub fn speichere_biometrie_geheimnis(geheimnis: &str) -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        let eintrag = Entry::new(DIENST, KONTO_VAULT_BIO)
            .map_err(|e| format!("Fehler beim Zugriff auf Windows Credential Store: {e}"))?;
        eintrag
            .set_password(geheimnis)
            .map_err(|e| format!("Fehler beim Speichern im Windows Credential Store: {e}"))?;
        Ok(())
    }
    #[cfg(target_os = "android")]
    {
        let _ = geheimnis;
        Err("Android nutzt native Keystore-Bindung.".to_string())
    }
}

pub fn loesche_biometrie_geheimnis() -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        if let Ok(eintrag) = Entry::new(DIENST, KONTO_VAULT_BIO) {
            match eintrag.delete_credential() {
                Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
                Err(e) => Err(format!("Fehler beim Löschen des biometrischen Schlüssels: {e}")),
            }
        } else {
            Ok(())
        }
    }
    #[cfg(target_os = "android")]
    {
        Ok(())
    }
}

pub async fn entsperre_mit_biometrie(nachricht: &str) -> Result<String, String> {
    #[cfg(windows)]
    {
        // 1. ZUERST Windows Hello Biometrie / PIN abfragen
        let msg = windows::core::HSTRING::from(nachricht);
        let op = UserConsentVerifier::RequestVerificationAsync(&msg)
            .map_err(|e| format!("Windows Hello konnte nicht initialisiert werden: {e}"))?;
        let verif_result = op
            .await
            .map_err(|e| format!("Windows Hello Fehler: {e}"))?;

        if verif_result != UserConsentVerificationResult::Verified {
            return Err("Biometrische Authentifizierung verweigert oder abgebrochen.".to_string());
        }

        // 2. ERST NACH erfolgreicher Verifikation aus dem Windows Credential Store lesen!
        let eintrag = Entry::new(DIENST, KONTO_VAULT_BIO)
            .map_err(|e| format!("Schlüssel-Tresor nicht erreichbar: {e}"))?;
        let secret = eintrag
            .get_password()
            .map_err(|e| format!("Kein biometrischer Schlüssel hinterlegt: {e}"))?;

        Ok(secret)
    }
    #[cfg(not(windows))]
    {
        let _ = nachricht;
        Err("Biometrische Entsperrung wird auf dieser Plattform nicht unterstützt.".to_string())
    }
}

#[cfg(not(windows))]
pub async fn pruefe_verfuegbarkeit() -> bool {
    false
}

#[cfg(not(windows))]
pub async fn verifiziere_benutzer(_nachricht: &str) -> Result<bool, String> {
    Err("Biometrie wird auf diesem Betriebssystem nativ noch nicht unterstützt.".to_string())
}
