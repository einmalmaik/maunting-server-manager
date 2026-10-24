//! Der Dauerdienst auf Android: hört zu, wenn die App zu ist.
//!
//! Die Arbeit macht `Hintergrunddienst.kt`. Hier wird nur durchgereicht und
//! geprüft, was ein Tippfehler still kaputtmachen würde: ein Ausweis, der kein
//! Ausweis ist, eine Adresse ohne `https`, eine Mailboxliste, die keine Liste
//! ist. Der Dienst selbst merkte das erst beim Verbinden — und dann stünde er
//! still, ohne dass jemand erfährt, warum.
//!
//! Dieselbe Datei liegt in MSS und Pontis (`browser/src-tauri/src/dienst.rs`);
//! nur `PAKET` unterscheidet sie. `tests::der_dienst_ist_in_beiden_apps_gleich`
//! hält das fest.

use serde_json::{json, Value};
use tauri::{AppHandle, WebviewWindow};

/// Das Android-Paket dieser App — für `register_android_plugin`.
#[cfg_attr(not(target_os = "android"), allow(dead_code))]
const PAKET: &str = "com.mauntingstudios.secure_browser";

/// So viele Mailboxen nimmt auch das Panel entgegen (`StreamMailboxAbos`).
const HOECHSTENS_MAILBOXEN: usize = 200;

fn nur_hauptfenster(fenster: &WebviewWindow) -> Result<(), String> {
    if fenster.label() == "main" {
        Ok(())
    } else {
        Err("Nur das Hauptfenster steuert den Hintergrunddienst.".to_string())
    }
}

/// 64 Hexzeichen, sonst nichts. Der Server prüft den Wert selbst; hier geht es
/// nur darum, dass gar nicht erst ein Dienst mit einem unbrauchbaren Wert startet.
fn pruefe_ausweis(ausweis: &str) -> Result<(), String> {
    let gut = ausweis.len() == 64 && ausweis.chars().all(|c| c.is_ascii_hexdigit());
    if gut {
        Ok(())
    } else {
        Err("Ungültiger Kanal-Ausweis".into())
    }
}

/// Nur `https`; `http` allein im Debug-Bau (Emulator gegen den Dev-Stack).
///
/// Ohne Anmeldedaten in der Adresse, ohne Pfad: der Dienst hängt `/api/events/live`
/// an, und ein Pfad davor ginge verloren.
fn pruefe_basis(basis: &str) -> Result<(), String> {
    let basis = basis.trim_end_matches('/');
    let rest = basis
        .strip_prefix("https://")
        .or_else(|| if cfg!(debug_assertions) { basis.strip_prefix("http://") } else { None })
        .ok_or("Das Panel muss über https erreichbar sein.")?;
    let gut = !rest.is_empty()
        && basis.len() <= 300
        && !rest.contains('/')
        && rest.chars().all(|c| c.is_ascii_graphic() && !matches!(c, '@' | '?' | '#' | '\\'));
    if gut {
        Ok(())
    } else {
        Err("Ungültige Adresse des Panels".into())
    }
}

/// Baut die Mailboxliste neu: nur Kennung und Token, nichts sonst.
///
/// Was hier durchfällt, käme sonst in die Einstellungen der App und von dort in
/// den Ereignisstrom — als Zeichenkette, die niemand mehr prüft.
fn pruefe_mailboxen(mailboxen: &str) -> Result<String, String> {
    let wert: Value =
        serde_json::from_str(mailboxen).map_err(|_| "Mailboxliste ist kein JSON".to_string())?;
    let liste = wert.as_array().ok_or("Mailboxliste ist keine Liste")?;
    if liste.len() > HOECHSTENS_MAILBOXEN {
        return Err("Zu viele Mailboxen".into());
    }
    let mut sauber = Vec::with_capacity(liste.len());
    for eintrag in liste {
        let objekt = eintrag.as_object().ok_or("Mailbox ohne Kennung")?;
        let id = objekt
            .get("mailbox_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_lowercase();
        if id.is_empty() || id.len() > 200 {
            return Err("Ungültige Mailbox-Kennung".into());
        }
        let mut neu = serde_json::Map::new();
        neu.insert("mailbox_id".into(), json!(id));
        if let Some(token) = objekt.get("mailbox_token").and_then(Value::as_str) {
            if token.len() > 300 {
                return Err("Ungültiges Mailbox-Token".into());
            }
            neu.insert("mailbox_token".into(), json!(token));
        }
        sauber.push(Value::Object(neu));
    }
    Ok(Value::Array(sauber).to_string())
}

#[cfg(target_os = "android")]
pub struct DienstState<R: tauri::Runtime>(pub tauri::plugin::PluginHandle<R>);

/// Meldet `DienstPlugin` bei Tauri an. Wird in `lib.rs` eingehängt.
#[cfg(target_os = "android")]
pub fn init_android_dienst<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    use tauri::Manager;
    tauri::plugin::Builder::<R>::new("dienst")
        .setup(|app, api| {
            let handle = api.register_android_plugin(PAKET, "DienstPlugin")?;
            app.manage(DienstState(handle));
            Ok(())
        })
        .build()
}

/// Ruft das Kotlin-Plugin und wartet auf die Antwort. Nur aus
/// `#[tauri::command(async)]`, sonst steht der Hauptthread.
#[cfg(target_os = "android")]
fn ruf(app: &AppHandle, befehl: &str, daten: Value) -> Result<Value, String> {
    use tauri::Manager;
    let dienst = app
        .try_state::<DienstState<tauri::Wry>>()
        .ok_or_else(|| "Der Hintergrunddienst ist nicht erreichbar.".to_string())?;
    dienst
        .0
        .run_mobile_plugin::<Value>(befehl, daten)
        .map_err(|e| e.to_string())
}

#[cfg(not(target_os = "android"))]
fn ruf(_app: &AppHandle, _befehl: &str, _daten: Value) -> Result<Value, String> {
    Err("Den Hintergrunddienst gibt es nur unter Android.".to_string())
}

/// Einschalten: Ausweis, Adresse und Mailboxen an Android geben und starten.
#[cfg(target_os = "android")]
#[tauri::command(async)]
pub async fn dienst_starten(
    app: AppHandle,
    fenster: WebviewWindow,
    ausweis: String,
    basis: String,
    mailboxen: String,
) -> Result<(), String> {
    nur_hauptfenster(&fenster)?;
    pruefe_ausweis(&ausweis)?;
    pruefe_basis(&basis)?;
    let mailboxen = pruefe_mailboxen(&mailboxen)?;
    ruf(
        &app,
        "starten",
        json!({
            "ausweis": ausweis,
            "basis": basis.trim_end_matches('/'),
            "mailboxen": mailboxen,
        }),
    )
    .map(|_| ())
}

#[cfg(not(target_os = "android"))]
#[tauri::command(async)]
pub async fn dienst_starten(
    _app: AppHandle,
    fenster: WebviewWindow,
    ausweis: String,
    basis: String,
    mailboxen: String,
) -> Result<(), String> {
    nur_hauptfenster(&fenster)?;
    pruefe_ausweis(&ausweis)?;
    pruefe_basis(&basis)?;
    pruefe_mailboxen(&mailboxen)?;
    Err("Den Hintergrunddienst gibt es nur unter Android.".to_string())
}

#[tauri::command(async)]
pub async fn dienst_stoppen(app: AppHandle, fenster: WebviewWindow) -> Result<(), String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, "stoppen", json!({})).map(|_| ())
}

/// Die geöffneten Mailboxen nachreichen, ohne neu zu starten.
#[tauri::command(async)]
pub async fn dienst_mailboxen(
    app: AppHandle,
    fenster: WebviewWindow,
    mailboxen: String,
) -> Result<(), String> {
    nur_hauptfenster(&fenster)?;
    let mailboxen = pruefe_mailboxen(&mailboxen)?;
    ruf(&app, "mailboxen", json!({ "mailboxen": mailboxen })).map(|_| ())
}

/// Ob der Mensch ihn eingeschaltet hat — und ob Android ihn laufen lässt.
#[cfg(target_os = "android")]
#[tauri::command(async)]
pub async fn dienst_stand(app: AppHandle, fenster: WebviewWindow) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, "laeuft", json!({}))
}

#[cfg(not(target_os = "android"))]
#[tauri::command(async)]
pub async fn dienst_stand(_app: AppHandle, fenster: WebviewWindow) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    Ok(json!({ "aktiv": false, "akkuBefreit": false }))
}

/// Fragt die Akku-Ausnahme an. Ohne sie beenden Samsung und Xiaomi den Dienst
/// nach Stunden still — das ist der Unterschied zwischen „kommt an" und
/// „kommt manchmal an".
#[tauri::command(async)]
pub async fn dienst_akku_befreien(app: AppHandle, fenster: WebviewWindow) -> Result<(), String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, "akkuBefreien", json!({})).map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ausweis_ist_64_hexzeichen() {
        assert!(pruefe_ausweis(&"a".repeat(64)).is_ok());
        assert!(pruefe_ausweis(&"0123456789abcdef".repeat(4)).is_ok());
        assert!(pruefe_ausweis(&"a".repeat(63)).is_err());
        assert!(pruefe_ausweis(&"z".repeat(64)).is_err());
        assert!(pruefe_ausweis("").is_err());
    }

    #[test]
    fn basis_ist_eine_herkunft_ohne_pfad_und_ohne_anmeldung() {
        assert!(pruefe_basis("https://panel.example").is_ok());
        assert!(pruefe_basis("https://panel.example/").is_ok());
        assert!(pruefe_basis("https://user:pw@panel.example").is_err());
        assert!(pruefe_basis("https://panel.example/mit/pfad").is_err());
        assert!(pruefe_basis("ftp://panel.example").is_err());
        assert!(pruefe_basis("").is_err());
    }

    #[test]
    fn mailboxen_verlieren_alles_unbekannte() {
        let liste = pruefe_mailboxen(
            r#"[{"mailbox_id":"ABC","mailbox_token":"t","extra":"x"}]"#,
        )
        .unwrap();
        assert_eq!(liste, r#"[{"mailbox_id":"abc","mailbox_token":"t"}]"#);
    }

    #[test]
    fn mailboxen_ohne_kennung_und_zu_viele_sind_fehler() {
        assert!(pruefe_mailboxen(r#"[{"mailbox_id":""}]"#).is_err());
        assert!(pruefe_mailboxen("kein json").is_err());
        assert!(pruefe_mailboxen(r#"{"mailbox_id":"a"}"#).is_err());
        let zu_viele: Vec<String> = (0..=HOECHSTENS_MAILBOXEN)
            .map(|i| format!(r#"{{"mailbox_id":"mb-{i}"}}"#))
            .collect();
        assert!(pruefe_mailboxen(&format!("[{}]", zu_viele.join(","))).is_err());
    }

}
