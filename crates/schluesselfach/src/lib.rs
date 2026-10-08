//! Refresh-Token und biometrische Schlüsselfächer, geteilt von MSS und MSB.
//!
//! Beide Apps koppeln sich auf dieselbe Weise an ein Panel und öffnen denselben
//! Tresor. Was dafür auf dem Gerät liegt, liegt deshalb auf demselben Weg: das
//! Refresh-Token im Schlüsselbund des Systems, das Master-Passwort für den
//! Schnelleinstieg hinter Windows Hello bzw. dem Android-Keystore.
//!
//! Getrennt sind nur die Namen. Jede App meldet beim Start ihren Dienstnamen
//! an ([`einrichten`]); so überschreibt die Kopplung des Browsers nie das Token
//! von MSS und umgekehrt.

use std::sync::OnceLock;

pub mod befehle;
pub mod biometrie;
pub mod geheimnisse;

/// Unter welchem Namen eine App ihre Einträge im Schlüsselbund ablegt.
#[derive(Debug, Clone, Copy)]
pub struct Dienst {
    /// Dienstname im Credential Manager bzw. Keychain.
    pub name: &'static str,
    /// Ein früherer Name, unter dem noch ein Token liegen kann. Wird gelesen
    /// und beim Löschen mit entfernt, aber nie neu beschrieben.
    pub frueher: Option<&'static str>,
    /// Paket der Android-App, in dem das Kotlin-Plugin `SchluesselfachPlugin`
    /// liegt.
    pub android_paket: &'static str,
}

static DIENST: OnceLock<Dienst> = OnceLock::new();

/// Legt den Dienstnamen fest. Gehört an den Anfang von `run()`, vor jeden
/// Zugriff auf Token oder Fächer. Ein zweiter Aufruf ändert nichts.
pub fn einrichten(dienst: Dienst) {
    let _ = DIENST.set(dienst);
}

pub(crate) fn dienst() -> Result<&'static Dienst, String> {
    DIENST
        .get()
        .ok_or_else(|| "Schlüsselfach ohne Dienstnamen: einrichten() fehlt.".to_string())
}
