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

#[cfg(not(windows))]
pub async fn pruefe_verfuegbarkeit() -> bool {
    false
}

#[cfg(not(windows))]
pub async fn verifiziere_benutzer(_nachricht: &str) -> Result<bool, String> {
    Err("Biometrie wird auf diesem Betriebssystem nativ noch nicht unterstützt.".to_string())
}
