//! Das Refresh-Token der Kopplung, im Schlüsselbund des Systems.
//!
//! Projektregel (hartes Stoppschild): Tokens liegen nie im Klartext. Unter
//! Windows liegt das Token im Credential Manager, unter Android im Fach
//! [`FACH_SITZUNG`] des Schlüsselfachs: verschlüsselt mit einem AES-Schlüssel
//! im Keystore, der das Gerät nicht verlässt. Das Fach fragt nicht nach, die
//! App meldet sich beim Start selbst an.
//!
//! Bis 08.10.2026 lag das Token unter Android (und unter Windows, wenn der
//! Credential Manager scheiterte) im Klartext in `.session_auth`. Liegt diese
//! Datei noch da, wandert sie beim Lesen ins Fach und verschwindet.
//!
//! Das Access-Token wird gar nicht persistiert: es lebt nur im Speicher
//! des Frontends und wird nach Neustart über den Refresh-Weg neu geholt.

use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

#[cfg(not(target_os = "android"))]
use keyring::Entry;

#[cfg(not(target_os = "android"))]
const KONTO: &str = "refresh_token";

/// Das Fach des Tokens unter Android. Nur hier benutzt, nie von der Oberfläche
/// erreichbar (`biometrie::pruefe_fach` kennt es nicht).
#[cfg(target_os = "android")]
const FACH_SITZUNG: &str = "session_refresh_token";

/// Die Klartextdatei von früher.
const ALTE_DATEI: &str = ".session_auth";

fn alte_datei(app: &AppHandle) -> Result<PathBuf, String> {
    let basis = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("App-Datenverzeichnis unbekannt: {e}"))?;
    Ok(basis.join(ALTE_DATEI))
}

pub fn speichern(app: &AppHandle, token: &str) -> Result<(), String> {
    ablegen(app, token)?;
    // Erst nach dem Ablegen: scheitert es, gilt die alte Anmeldung weiter.
    if let Ok(datei) = alte_datei(app) {
        let _ = std::fs::remove_file(datei);
    }
    Ok(())
}

/// `None` heißt: kein Token hinterlegt (frische Installation oder Logout).
pub fn laden(app: &AppHandle) -> Result<Option<String>, String> {
    mit_altbestand(|| lesen(app), |token| ablegen(app, token), &alte_datei(app)?)
}

/// Idempotent: ein fehlender Eintrag ist kein Fehler — Logout soll nie an
/// „war schon weg“ scheitern.
pub fn loeschen(app: &AppHandle) -> Result<(), String> {
    #[cfg(not(target_os = "android"))]
    {
        let dienst = crate::dienst()?;
        for name in std::iter::once(dienst.name).chain(dienst.frueher) {
            let _ = eintrag_loeschen(name);
        }
    }
    #[cfg(target_os = "android")]
    {
        crate::biometrie::android_ruf::<()>(app, "loeschen", serde_json::json!({ "fach": FACH_SITZUNG }))?;
    }
    if let Ok(datei) = alte_datei(app) {
        let _ = std::fs::remove_file(datei);
    }
    Ok(())
}

/// Liest das Token aus dem Schlüsselbund. Eine Klartextdatei von früher wandert
/// dabei hinein und verschwindet; scheitert das Ablegen, bleibt sie liegen,
/// sonst wäre die Kopplung verloren.
fn mit_altbestand(
    lesen: impl Fn() -> Result<Option<String>, String>,
    ablegen: impl Fn(&str) -> Result<(), String>,
    datei: &Path,
) -> Result<Option<String>, String> {
    if let Some(token) = lesen()? {
        // Abgelegt, aber die Datei blieb (Abbruch dazwischen): sie fällt jetzt.
        let _ = std::fs::remove_file(datei);
        return Ok(Some(token));
    }
    let alt = match std::fs::read_to_string(datei) {
        Ok(inhalt) => inhalt.trim().to_string(),
        Err(_) => return Ok(None),
    };
    if !alt.is_empty() {
        ablegen(&alt)?;
    }
    std::fs::remove_file(datei).map_err(|e| format!("Alte Token-Datei nicht löschbar: {e}"))?;
    Ok(Some(alt).filter(|t| !t.is_empty()))
}

#[cfg(not(target_os = "android"))]
fn ablegen(_app: &AppHandle, token: &str) -> Result<(), String> {
    Entry::new(crate::dienst()?.name, KONTO)
        .and_then(|eintrag| eintrag.set_password(token))
        .map_err(|e| format!("Token konnte nicht im Schlüsselbund gespeichert werden: {e}"))
}

#[cfg(not(target_os = "android"))]
fn lesen(_app: &AppHandle) -> Result<Option<String>, String> {
    let dienst = crate::dienst()?;
    for name in std::iter::once(dienst.name).chain(dienst.frueher) {
        if let Ok(token) = Entry::new(name, KONTO).and_then(|e| e.get_password()) {
            return Ok(Some(token));
        }
    }
    Ok(None)
}

#[cfg(target_os = "android")]
fn ablegen(app: &AppHandle, token: &str) -> Result<(), String> {
    crate::biometrie::android_ruf::<()>(
        app,
        "speichern",
        serde_json::json!({ "fach": FACH_SITZUNG, "geheimnis": token }),
    )
}

#[cfg(target_os = "android")]
fn lesen(app: &AppHandle) -> Result<Option<String>, String> {
    crate::biometrie::android_ruf::<crate::biometrie::WertAntwort>(
        app,
        "lesen",
        serde_json::json!({ "fach": FACH_SITZUNG }),
    )
    .map(|a| a.wert)
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    fn datei(name: &str, inhalt: Option<&str>) -> PathBuf {
        let pfad = std::env::temp_dir().join(format!("msb-token-{name}-{}", std::process::id()));
        let _ = std::fs::remove_file(&pfad);
        if let Some(inhalt) = inhalt {
            std::fs::write(&pfad, inhalt).unwrap();
        }
        pfad
    }

    #[test]
    fn die_klartextdatei_wandert_in_den_schluesselbund_und_verschwindet() {
        let pfad = datei("wandert", Some("token-alt\n"));
        let abgelegt = RefCell::new(None);
        let ablegen = |t: &str| {
            *abgelegt.borrow_mut() = Some(t.to_string());
            Ok(())
        };
        let ergebnis = mit_altbestand(|| Ok(None), ablegen, &pfad);
        assert_eq!(ergebnis.unwrap().as_deref(), Some("token-alt"));
        assert_eq!(abgelegt.borrow().as_deref(), Some("token-alt"));
        assert!(!pfad.exists());
    }

    #[test]
    fn scheitert_das_ablegen_bleibt_die_datei_und_damit_die_kopplung() {
        let pfad = datei("bleibt", Some("token-alt"));
        let ergebnis = mit_altbestand(|| Ok(None), |_| Err("Keystore weg".into()), &pfad);
        assert!(ergebnis.is_err());
        assert!(pfad.exists());
        std::fs::remove_file(&pfad).unwrap();
    }

    #[test]
    fn das_abgelegte_token_gilt_und_eine_liegengebliebene_datei_faellt() {
        let pfad = datei("faellt", Some("token-alt"));
        let ergebnis = mit_altbestand(|| Ok(Some("token-neu".into())), |_| panic!("nicht ablegen"), &pfad);
        assert_eq!(ergebnis.unwrap().as_deref(), Some("token-neu"));
        assert!(!pfad.exists());
    }

    #[test]
    fn ohne_token_und_ohne_datei_ist_nichts_da() {
        let pfad = datei("leer", None);
        assert_eq!(mit_altbestand(|| Ok(None), |_| panic!("nicht ablegen"), &pfad).unwrap(), None);
        let pfad = datei("leerzeile", Some("  \n"));
        assert_eq!(mit_altbestand(|| Ok(None), |_| panic!("nicht ablegen"), &pfad).unwrap(), None);
        assert!(!pfad.exists());
    }
}
