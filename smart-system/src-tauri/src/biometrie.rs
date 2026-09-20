//! Biometrische Bestätigung und das Schlüsselfach des Betriebssystems.
//!
//! Zwei Plattformen, ein Vertrag. Was oben aufruft — Tresor wie Messenger —
//! sieht nur die Funktionen dieser Datei und muss nicht wissen, worauf es läuft:
//!
//! - **Windows**: `UserConsentVerifier` fragt (Windows Hello), der Credential
//!   Store verwahrt. Zwei getrennte Schritte: erst bestätigen, dann lesen.
//! - **Android**: der Hardware-Keystore verwahrt **und** fragt in einem Zug. Der
//!   Schlüssel liegt dort so, dass ihn kein Prozess auslesen kann, und die
//!   Hardware gibt ihn erst nach einem erfolgreichen `BiometricPrompt` frei.
//!   Der Weg dorthin geht über das Kotlin-Plugin `SchluesselfachPlugin`.
//! - **Alles andere** (Web, Linux ohne Keyring): keins von beidem, ehrlich
//!   gemeldet statt still fehlgeschlagen.
//!
//! Der Unterschied „fragt selbst" ist kein Detail, sondern sichtbar: siehe
//! [`speicher_fragt_selbst`].

use tauri::AppHandle;
#[cfg(target_os = "android")]
use tauri::Manager;

#[cfg(windows)]
use windows::Security::Credentials::UI::{
    UserConsentVerificationResult, UserConsentVerifier, UserConsentVerifierAvailability,
};

#[cfg(not(target_os = "android"))]
use keyring::Entry;

#[cfg(not(target_os = "android"))]
const DIENST: &str = "MauntingSmartSystem";

/// Das Fach des Tresors.
///
/// Es hieß immer schon so und muss weiter so heißen: wer den biometrischen
/// Schnelleinstieg vor diesem Update eingerichtet hat, hat sein Geheimnis unter
/// diesem Namen liegen. Ein umbenanntes Fach wäre für den Credential Store ein
/// leeres Fach, und der Tresor fragte plötzlich wieder nach dem Master-Passwort.
pub const FACH_TRESOR: &str = "vault_biometric_key";

/// Das Fach des Messengers. Eigener PIN, eigenes Fach.
pub const FACH_MESSENGER: &str = "messenger_biometric_key";

/// Das Fach für das Gerätegeheimnis des Messengers.
///
/// Darin liegen 32 Zufallsbytes, die in die Schlüsselableitung des PIN
/// einfließen. Sie sind **die eine Hälfte** und ohne PIN wertlos — deshalb
/// braucht das Lesen hier, anders als bei den beiden Fächern oben, keine
/// Abfrage. Eine solche Abfrage würde das reine PIN-Entsperren unmöglich machen,
/// und genau das muss auf jedem Gerät ohne Biometrie gehen.
///
/// Der Zweck ist ein anderer: eine kopierte Festplatte ist auf einem fremden
/// Gerät wertlos, weil dieses Geheimnis dort fehlt.
pub const FACH_MESSENGER_GERAET: &str = "messenger_device_secret";

/// Fächernamen, die von außen kommen dürfen.
///
/// Der Name geht in eine Ressource des Betriebssystems — in den Credential Store
/// oder in einen Keystore-Alias. Eine freie Zeichenkette aus dem Frontend hätte
/// hier nichts verloren: sie wäre ein Weg, in fremde Einträge derselben
/// Anwendung zu schreiben. Drei feste Namen, alles andere wird abgewiesen.
///
/// Die Android-Seite prüft dasselbe noch einmal in `Schluesselfach.pruefeFach`.
/// Doppelt, weil beide Seiten für sich stehen müssen.
fn pruefe_fach(fach: &str) -> Result<(), String> {
    match fach {
        FACH_TRESOR | FACH_MESSENGER | FACH_MESSENGER_GERAET => Ok(()),
        _ => Err(format!("Unbekanntes Schlüsselfach: {fach}")),
    }
}

// ── Android: die Brücke zum Kotlin-Plugin ─────────────────────────────────

#[cfg(target_os = "android")]
pub struct SchluesselfachState<R: tauri::Runtime>(pub tauri::plugin::PluginHandle<R>);

/// Meldet `SchluesselfachPlugin` bei Tauri an. Wird in `lib.rs` eingehängt.
#[cfg(target_os = "android")]
pub fn init_android_schluesselfach<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    tauri::plugin::Builder::<R>::new("schluesselfach")
        .setup(|app, api| {
            let handle = api
                .register_android_plugin("com.mauntingstudios.smart_system", "SchluesselfachPlugin")?;
            app.manage(SchluesselfachState(handle));
            Ok(())
        })
        .build()
}

#[cfg(target_os = "android")]
#[derive(serde::Deserialize)]
struct WertAntwort {
    /// Fehlt, wenn nichts hinterlegt ist: `JSObject.put(key, null)` nimmt den
    /// Schlüssel drüben heraus, statt ihn auf null zu setzen.
    #[serde(default)]
    wert: Option<String>,
}

#[cfg(target_os = "android")]
#[derive(serde::Deserialize)]
struct JaNein {
    #[serde(default)]
    wert: bool,
}

/// Ruft das Kotlin-Plugin und wartet auf seine Antwort.
///
/// Der Aufruf hält den Faden auf, bis drüben `resolve` oder `reject` fällt — bei
/// einer Abfrage also so lange, wie der Mensch braucht. Deshalb darf ihn nur ein
/// `#[tauri::command(async)]` auslösen: auf dem Hauptthread stünde die Anwendung
/// und der Prompt käme gar nicht erst auf den Bildschirm.
#[cfg(target_os = "android")]
fn android_ruf<T: serde::de::DeserializeOwned>(
    app: &AppHandle,
    befehl: &str,
    daten: serde_json::Value,
) -> Result<T, String> {
    let fach = app
        .try_state::<SchluesselfachState<tauri::Wry>>()
        .ok_or_else(|| "Das Schlüsselfach dieses Geräts ist nicht erreichbar.".to_string())?;
    fach.0
        .run_mobile_plugin::<T>(befehl, daten)
        .map_err(|e| format!("Schlüsselfach: {e}"))
}

// ── Der gemeinsame Vertrag ────────────────────────────────────────────────

/// Kann sich der Mensch auf diesem Gerät biometrisch bestätigen?
pub async fn pruefe_verfuegbarkeit(app: &AppHandle) -> bool {
    #[cfg(windows)]
    {
        let _ = app;
        match UserConsentVerifier::CheckAvailabilityAsync() {
            Ok(op) => match op.await {
                Ok(availability) => availability == UserConsentVerifierAvailability::Available,
                Err(_) => false,
            },
            Err(_) => false,
        }
    }
    #[cfg(target_os = "android")]
    {
        // Bewusst nicht `tauri-plugin-biometric::status()`: das prüft
        // `BIOMETRIC_WEAK`, während die Schlüssel hier an `BIOMETRIC_STRONG`
        // oder die Bildschirmsperre gebunden sind. Ein Gerät mit nur schwacher
        // Gesichtserkennung bekäme sonst ein „ja" und scheiterte danach beim
        // Anlegen des Schlüssels.
        android_ruf::<JaNein>(app, "moeglich", serde_json::json!({}))
            .map(|a| a.wert)
            .unwrap_or(false)
    }
    #[cfg(not(any(windows, target_os = "android")))]
    {
        let _ = app;
        false
    }
}

/// Fragt nach einer Bestätigung, ohne dabei ein Geheimnis anzufassen.
pub async fn verifiziere_benutzer(app: &AppHandle, nachricht: &str) -> Result<bool, String> {
    #[cfg(windows)]
    {
        let _ = app;
        let msg = windows::core::HSTRING::from(nachricht);
        match UserConsentVerifier::RequestVerificationAsync(&msg) {
            Ok(op) => match op.await {
                Ok(result) => Ok(result == UserConsentVerificationResult::Verified),
                Err(e) => Err(format!("Windows Hello Fehler: {e}")),
            },
            Err(e) => Err(format!("Windows Hello konnte nicht gestartet werden: {e}")),
        }
    }
    #[cfg(target_os = "android")]
    {
        // Die reine Bestätigung kann `tauri-plugin-biometric` schon, und es ist
        // ohnehin eingehängt. Ein zweites Stück Kotlin für dieselbe Aufgabe wäre
        // eine Kopie, die irgendwann auseinanderläuft.
        use tauri_plugin_biometric::{AuthOptions, BiometricExt};
        let optionen = AuthOptions {
            allow_device_credential: true,
            ..Default::default()
        };
        match app.biometric().authenticate(nachricht.to_string(), optionen) {
            Ok(()) => Ok(true),
            Err(e) => Err(format!("Bestätigung fehlgeschlagen: {e}")),
        }
    }
    #[cfg(not(any(windows, target_os = "android")))]
    {
        let _ = (app, nachricht);
        Err("Biometrie wird auf diesem Betriebssystem nativ noch nicht unterstützt.".to_string())
    }
}

/// Fragt der Schlüsselspeicher dieser Plattform beim **Ablegen** von sich aus
/// nach einer Bestätigung?
///
/// Windows sagt nein: der Credential Store nimmt ein Geheimnis wortlos entgegen,
/// Hello kommt erst beim Lesen. Wer dort einen Schnelleinstieg einrichtet, muss
/// vorher einmal ausdrücklich bestätigen — sonst bände jemand an einem
/// unbeaufsichtigten Rechner seinen eigenen Finger an ein fremdes Geheimnis.
///
/// Android sagt ja: der Keystore-Schlüssel ist an eine frische Bestätigung
/// gebunden, schon zum Verschlüsseln. Eine zusätzliche Abfrage davor wäre
/// derselbe Fingerabdruck zweimal hintereinander — und das sieht nicht nach
/// Sorgfalt aus, sondern nach einem Fehler.
pub fn speicher_fragt_selbst() -> bool {
    cfg!(target_os = "android")
}

/// Meldet, ob auf dieser Plattform überhaupt ein Geheimnis verwahrt werden kann.
///
/// Getrennt von [`pruefe_verfuegbarkeit`]: fragen können und verwahren können
/// sind zwei Dinge. Wer nur das erste prüft, bietet einen Schnelleinstieg an,
/// der beim Einrichten scheitert — genau das tat der Tresor auf Android bis
/// 09/2026.
pub fn geheimnisspeicher_verfuegbar(app: &AppHandle) -> bool {
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Entry::new(DIENST, FACH_MESSENGER_GERAET).is_ok()
    }
    #[cfg(target_os = "android")]
    {
        app.try_state::<SchluesselfachState<tauri::Wry>>().is_some()
    }
}

pub fn speichere_biometrie_geheimnis(
    app: &AppHandle,
    fach: &str,
    geheimnis: &str,
) -> Result<(), String> {
    pruefe_fach(fach)?;
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        let eintrag = Entry::new(DIENST, fach)
            .map_err(|e| format!("Fehler beim Zugriff auf Windows Credential Store: {e}"))?;
        eintrag
            .set_password(geheimnis)
            .map_err(|e| format!("Fehler beim Speichern im Windows Credential Store: {e}"))?;
        Ok(())
    }
    #[cfg(target_os = "android")]
    {
        android_ruf::<()>(
            app,
            "speichern",
            serde_json::json!({ "fach": fach, "geheimnis": geheimnis }),
        )
    }
}

pub fn loesche_biometrie_geheimnis(app: &AppHandle, fach: &str) -> Result<(), String> {
    pruefe_fach(fach)?;
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        if let Ok(eintrag) = Entry::new(DIENST, fach) {
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
        android_ruf::<()>(app, "loeschen", serde_json::json!({ "fach": fach }))
    }
}

/// Liest ein Fach **ohne** Abfrage. Nur für [`FACH_MESSENGER_GERAET`].
///
/// Die Einschränkung steht hier und nicht beim Aufrufer: ein Geheimnis, das
/// allein entsperrt, darf diesen Weg nie nehmen.
pub fn lies_geraetegeheimnis(app: &AppHandle) -> Result<Option<String>, String> {
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        let eintrag = Entry::new(DIENST, FACH_MESSENGER_GERAET)
            .map_err(|e| format!("Schlüssel-Tresor nicht erreichbar: {e}"))?;
        match eintrag.get_password() {
            Ok(geheim) => Ok(Some(geheim)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(format!("Gerätegeheimnis nicht lesbar: {e}")),
        }
    }
    #[cfg(target_os = "android")]
    {
        android_ruf::<WertAntwort>(
            app,
            "lesen",
            serde_json::json!({ "fach": FACH_MESSENGER_GERAET }),
        )
        .map(|a| a.wert)
    }
}

/// Fragt nach der Bestätigung und gibt das Geheimnis erst danach heraus.
pub async fn entsperre_mit_biometrie(
    app: &AppHandle,
    fach: &str,
    nachricht: &str,
) -> Result<String, String> {
    pruefe_fach(fach)?;
    if fach == FACH_MESSENGER_GERAET {
        return Err("Das Gerätegeheimnis entsperrt nichts allein.".to_string());
    }

    #[cfg(windows)]
    {
        let _ = app;
        // 1. ZUERST Windows Hello Biometrie / PIN abfragen
        let msg = windows::core::HSTRING::from(nachricht);
        let op = UserConsentVerifier::RequestVerificationAsync(&msg)
            .map_err(|e| format!("Windows Hello konnte nicht initialisiert werden: {e}"))?;
        let verif_result = op.await.map_err(|e| format!("Windows Hello Fehler: {e}"))?;

        if verif_result != UserConsentVerificationResult::Verified {
            return Err("Biometrische Authentifizierung verweigert oder abgebrochen.".to_string());
        }

        // 2. ERST NACH erfolgreicher Verifikation aus dem Windows Credential Store lesen!
        let eintrag = Entry::new(DIENST, fach)
            .map_err(|e| format!("Schlüssel-Tresor nicht erreichbar: {e}"))?;
        let secret = eintrag
            .get_password()
            .map_err(|e| format!("Kein biometrischer Schlüssel hinterlegt: {e}"))?;

        Ok(secret)
    }
    #[cfg(target_os = "android")]
    {
        // Hier fallen die beiden Schritte zusammen: der Keystore rückt den
        // Schlüssel erst nach der Bestätigung heraus. Das ist die härtere
        // Variante — auf Windows trennt die Anwendung Abfrage und Lesen, hier
        // trennt sie die Hardware.
        android_ruf::<WertAntwort>(
            app,
            "lesen",
            serde_json::json!({ "fach": fach, "nachricht": nachricht }),
        )?
        .wert
        .ok_or_else(|| "Kein biometrischer Schlüssel hinterlegt.".to_string())
    }
    #[cfg(not(any(windows, target_os = "android")))]
    {
        let _ = (app, fach, nachricht);
        Err("Biometrische Entsperrung wird auf dieser Plattform nicht unterstützt.".to_string())
    }
}
