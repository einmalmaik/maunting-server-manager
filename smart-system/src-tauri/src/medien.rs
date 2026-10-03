//! Kameraaufnahmen dieses Geräts für die Kamera-Sicherung des Tresors.
//!
//! Nur Android hat das, die Arbeit macht `MedienPlugin.kt`. Hier wird nur
//! durchgereicht, beschränkt aufs Hauptfenster. Von drüben kommen nur Kennung
//! und Art einer Aufnahme, nie eine Adresse.

use serde_json::{json, Value};
use tauri::{AppHandle, WebviewWindow};

fn nur_hauptfenster(fenster: &WebviewWindow) -> Result<(), String> {
    if fenster.label() == "main" {
        Ok(())
    } else {
        Err("Nur das Hauptfenster liest Aufnahmen.".to_string())
    }
}

fn pruefe_art(art: &str) -> Result<(), String> {
    match art {
        "bild" | "video" => Ok(()),
        _ => Err("Unbekannte Art einer Aufnahme".to_string()),
    }
}

#[cfg(target_os = "android")]
pub struct MedienState<R: tauri::Runtime>(pub tauri::plugin::PluginHandle<R>);

/// Meldet `MedienPlugin` bei Tauri an. Wird in `lib.rs` eingehängt.
#[cfg(target_os = "android")]
pub fn init_android_medien<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    use tauri::Manager;
    tauri::plugin::Builder::<R>::new("medien")
        .setup(|app, api| {
            let handle = api.register_android_plugin("com.mauntingstudios.smart_system", "MedienPlugin")?;
            app.manage(MedienState(handle));
            Ok(())
        })
        .build()
}

/// Ruft das Kotlin-Plugin und wartet auf die Antwort. Nur aus
/// `#[tauri::command(async)]`, sonst steht der Hauptthread.
#[cfg(target_os = "android")]
fn ruf(app: &AppHandle, befehl: &str, daten: Value) -> Result<Value, String> {
    use tauri::Manager;
    let medien = app
        .try_state::<MedienState<tauri::Wry>>()
        .ok_or_else(|| "Die Aufnahmen dieses Geräts sind nicht erreichbar.".to_string())?;
    medien.0.run_mobile_plugin::<Value>(befehl, daten).map_err(|e| e.to_string())
}

#[cfg(not(target_os = "android"))]
fn ruf(_app: &AppHandle, _befehl: &str, _daten: Value) -> Result<Value, String> {
    Err("Die Kamera-Sicherung gibt es nur unter Android.".to_string())
}

#[tauri::command(async)]
pub async fn medien_zugriff(app: AppHandle, fenster: WebviewWindow, anfragen: bool) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, if anfragen { "zugriffAnfragen" } else { "zugriff" }, json!({}))
}

/// Kennung einer Installation: eine UUID, sonst nichts (Alias im Keystore).
fn pruefe_geraet(geraet: &str) -> Result<(), String> {
    if ist_uuid(geraet) { Ok(()) } else { Err("Ungültige Gerätekennung".into()) }
}

fn ist_uuid(wert: &str) -> bool {
    let teile: Vec<&str> = wert.split('-').collect();
    teile.len() == 5
        && teile
            .iter()
            .zip([8, 4, 4, 4, 12])
            .all(|(t, l)| t.len() == l && t.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()))
}

#[tauri::command(async)]
pub async fn medien_sicherung_schluessel(app: AppHandle, fenster: WebviewWindow, geraet: String) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    pruefe_geraet(&geraet)?;
    ruf(&app, "sicherungSchluessel", json!({ "geraet": geraet }))
}

/// Das Panel, mit dem der Hintergrund-Job spricht. Nur https; http nur im
/// Debug-Bau (Emulator gegen den Dev-Stack). Keine Anmeldedaten, keine Abfrage.
fn pruefe_server(server: &str) -> Result<(), String> {
    let rest = server
        .strip_prefix("https://")
        .or_else(|| if cfg!(debug_assertions) { server.strip_prefix("http://") } else { None })
        .ok_or("Das Panel muss über https erreichbar sein.")?;
    let gut = !rest.is_empty()
        && server.len() <= 300
        && !rest.starts_with('/')
        && rest.chars().all(|c| c.is_ascii_graphic() && !matches!(c, '@' | '?' | '#' | '\\'));
    if gut { Ok(()) } else { Err("Ungültige Adresse des Panels".into()) }
}

/// Was `/api/vault/sicherung/zugang` ausgibt: `msz1.<konto>.<familie>.<geheim>`.
fn pruefe_zugang(zugang: &str) -> Result<(), String> {
    let teile: Vec<&str> = zugang.split('.').collect();
    let gut = teile.len() == 4
        && teile[0] == "msz1"
        && zugang.len() <= 256
        && teile[1..].iter().all(|t| !t.is_empty() && t.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
    if gut { Ok(()) } else { Err("Ungültiger Sicherungszugang".into()) }
}

/// Der öffentliche Schlüssel des Posteingangs muss sich in Rust nutzen lassen,
/// bevor der Job ihn bekommt: sonst scheiterte jede Aufnahme erst im Hintergrund.
fn pruefe_posteingang(pq: &str, rsa: &str) -> Result<(), String> {
    use base64::Engine;
    let b64 = base64::engine::general_purpose::STANDARD;
    let pq = b64.decode(pq).map_err(|_| "Ungültiger Posteingang")?;
    let rsa = b64.decode(rsa).map_err(|_| "Ungültiger Posteingang")?;
    crate::kamera_krypto::hybrid_verschluesseln(b"probe", &pq, &rsa, b"probe")
        .map(|_| ())
        .map_err(|_| "Ungültiger Posteingang".into())
}

fn pruefe_bucket(bucket: &str) -> Result<(), String> {
    if bucket.len() == 64 && bucket.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()) {
        Ok(())
    } else {
        Err("Ungültiger Tresor".into())
    }
}

/// Schaltet die Sicherung ein oder erneuert den Zugang. Der Zugang geht nur
/// an `MedienPlugin.kt`, das ihn mit einem Keystore-Schlüssel ablegt.
#[allow(clippy::too_many_arguments)]
#[tauri::command(async)]
pub async fn medien_einrichten(
    app: AppHandle,
    fenster: WebviewWindow,
    konto: i64,
    server: String,
    bucket: String,
    geraet: String,
    zugang: String,
    eingang_id: String,
    pq: String,
    rsa: String,
    nur_wlan: bool,
) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    if konto <= 0 {
        return Err("Ungültiges Konto".into());
    }
    pruefe_server(&server)?;
    pruefe_bucket(&bucket)?;
    pruefe_geraet(&geraet)?;
    pruefe_zugang(&zugang)?;
    if !ist_uuid(&eingang_id) {
        return Err("Ungültiger Posteingang".into());
    }
    pruefe_posteingang(&pq, &rsa)?;
    ruf(
        &app,
        "einrichten",
        json!({
            "konto": konto, "server": server, "bucket": bucket, "geraet": geraet, "zugang": zugang,
            "eingangId": eingang_id, "pq": pq, "rsa": rsa, "nurWlan": nur_wlan,
        }),
    )
}

#[tauri::command(async)]
pub async fn medien_sicherung_stand(app: AppHandle, fenster: WebviewWindow) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, "stand", json!({}))
}

/// Höchstens so viele schon gesicherte Aufnahmen je Aufruf.
const BEKANNT_HOECHSTENS: usize = 100_000;

fn pruefe_bekannt(bekannt: &[String]) -> Result<(), String> {
    let gut = bekannt.len() <= BEKANNT_HOECHSTENS
        && bekannt.iter().all(|b| match b.split_once(':') {
            Some((id, sha)) => {
                !id.is_empty()
                    && id.len() <= 19
                    && !id.starts_with('0')
                    && id.chars().all(|c| c.is_ascii_digit())
                    && sha.len() == 64
                    && sha.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            }
            None => false,
        });
    if gut { Ok(()) } else { Err("Ungültige Liste gesicherter Aufnahmen".into()) }
}

#[tauri::command(async)]
pub async fn medien_sicherung_aendern(
    app: AppHandle,
    fenster: WebviewWindow,
    nur_wlan: Option<bool>,
    screenshots: Option<bool>,
    vorhandene: bool,
    bekannt: Vec<String>,
) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    pruefe_bekannt(&bekannt)?;
    let mut daten = json!({ "vorhandene": vorhandene, "bekannt": bekannt });
    if let Some(w) = nur_wlan {
        daten["nurWlan"] = json!(w);
    }
    if let Some(s) = screenshots {
        daten["screenshots"] = json!(s);
    }
    ruf(&app, "aendern", daten)
}

#[tauri::command(async)]
pub async fn medien_sicherung_vergessen(app: AppHandle, fenster: WebviewWindow) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, "vergessen", json!({}))
}

#[tauri::command(async)]
pub async fn medien_sicherung_jetzt(app: AppHandle, fenster: WebviewWindow) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, "jetzt", json!({}))
}

#[tauri::command(async)]
pub async fn medien_pruefsumme(app: AppHandle, fenster: WebviewWindow, id: i64, art: String) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    pruefe_art(&art)?;
    ruf(&app, "pruefsumme", json!({ "id": id, "art": art }))
}

#[tauri::command(async)]
pub async fn medien_papierkorb(
    app: AppHandle,
    fenster: WebviewWindow,
    bilder: Vec<i64>,
    videos: Vec<i64>,
) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    if bilder.iter().chain(&videos).any(|id| *id <= 0) {
        return Err("Ungültige Kennung".to_string());
    }
    ruf(&app, "inPapierkorb", json!({ "bilder": bilder, "videos": videos }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nur_bild_und_video() {
        assert!(pruefe_art("bild").is_ok());
        assert!(pruefe_art("video").is_ok());
        assert!(pruefe_art("content://media/external/file/1").is_err());
        assert!(pruefe_art("").is_err());
    }

    #[test]
    fn geraet_nur_als_uuid() {
        assert!(pruefe_geraet("0f8fad5b-d9cb-469f-a165-70867728950e").is_ok());
        assert!(pruefe_geraet("0F8FAD5B-D9CB-469F-A165-70867728950E").is_err());
        assert!(pruefe_geraet("../msm-kamera").is_err());
        assert!(pruefe_geraet("0f8fad5b-d9cb-469f-a165-70867728950").is_err());
        assert!(pruefe_geraet("").is_err());
    }

    #[test]
    fn server_nur_als_https_ohne_anmeldedaten() {
        assert!(pruefe_server("https://panel.example.com").is_ok());
        assert!(pruefe_server("https://panel.example.com:8443/msm").is_ok());
        assert!(pruefe_server("https://nutzer:pw@panel.example.com").is_err());
        assert!(pruefe_server("https://panel.example.com/?x=1").is_err());
        assert!(pruefe_server("https:///panel").is_err());
        assert!(pruefe_server("ftp://panel.example.com").is_err());
        assert!(pruefe_server("https://panel example.com").is_err());
        // Im Debug-Bau (Tests) auch http, für den Emulator gegen den Dev-Stack.
        assert_eq!(pruefe_server("http://10.0.2.2:8000").is_ok(), cfg!(debug_assertions));
    }

    #[test]
    fn zugang_nur_in_seiner_form() {
        assert!(pruefe_zugang("msz1.12.Ab-c_d.XyZ09").is_ok());
        assert!(pruefe_zugang("msz1.12.fam").is_err());
        assert!(pruefe_zugang("msz2.12.fam.geheim").is_err());
        assert!(pruefe_zugang("msz1.12.fa m.geheim").is_err());
        assert!(pruefe_zugang("msz1.12.fam.ge\nheim").is_err());
    }

    #[test]
    fn bekannt_nur_als_kennung_und_pruefsumme() {
        let sha = "a".repeat(64);
        assert!(pruefe_bekannt(&[format!("42:{sha}")]).is_ok());
        assert!(pruefe_bekannt(&[format!("0:{sha}")]).is_err());
        assert!(pruefe_bekannt(&[format!("-1:{sha}")]).is_err());
        assert!(pruefe_bekannt(&[format!("42:{}", "A".repeat(64))]).is_err());
        assert!(pruefe_bekannt(&["42".into()]).is_err());
    }

    #[test]
    fn bucket_nur_als_hex() {
        assert!(pruefe_bucket(&"0".repeat(64)).is_ok());
        assert!(pruefe_bucket(&"0".repeat(63)).is_err());
        assert!(pruefe_bucket(&"g".repeat(64)).is_err());
    }
}
