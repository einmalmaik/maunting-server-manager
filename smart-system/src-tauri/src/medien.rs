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

#[tauri::command(async)]
pub async fn medien_stand(app: AppHandle, fenster: WebviewWindow) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, "medienStand", json!({}))
}

/// Kennung einer Installation: eine UUID, sonst nichts (Alias im Keystore).
fn pruefe_geraet(geraet: &str) -> Result<(), String> {
    let teile: Vec<&str> = geraet.split('-').collect();
    let laengen = [8, 4, 4, 4, 12];
    let gut = teile.len() == 5
        && teile.iter().zip(laengen).all(|(t, l)| t.len() == l && t.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    if gut { Ok(()) } else { Err("Ungültige Gerätekennung".into()) }
}

#[tauri::command(async)]
pub async fn medien_sicherung_schluessel(app: AppHandle, fenster: WebviewWindow, geraet: String) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    pruefe_geraet(&geraet)?;
    ruf(&app, "sicherungSchluessel", json!({ "geraet": geraet }))
}

#[tauri::command(async)]
pub async fn medien_sicherung_signieren(
    app: AppHandle,
    fenster: WebviewWindow,
    geraet: String,
    daten: String,
) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    pruefe_geraet(&geraet)?;
    // Base64 von höchstens 64 KiB.
    if daten.len() > 88_000 {
        return Err("Zu viele Daten".into());
    }
    ruf(&app, "sicherungSignieren", json!({ "geraet": geraet, "daten": daten }))
}

#[tauri::command(async)]
pub async fn medien_aufnahmen(
    app: AppHandle,
    fenster: WebviewWindow,
    nach: i64,
    hoechstens: u32,
) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    ruf(&app, "aufnahmen", json!({ "nach": nach.max(0), "hoechstens": hoechstens }))
}

#[tauri::command(async)]
pub async fn medien_lesen(
    app: AppHandle,
    fenster: WebviewWindow,
    id: i64,
    art: String,
    von: u64,
    laenge: u32,
) -> Result<Value, String> {
    nur_hauptfenster(&fenster)?;
    pruefe_art(&art)?;
    ruf(&app, "lesen", json!({ "id": id, "art": art, "von": von, "laenge": laenge }))
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
}
