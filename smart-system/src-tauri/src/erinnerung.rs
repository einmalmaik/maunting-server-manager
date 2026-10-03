//! Terminerinnerungen, die Android zur geplanten Zeit selbst zeigt.
//!
//! Die offene App rechnet sie aus (`lib/erinnerungsplan.ts`), denn nur sie hat
//! den Schlüssel zu den Titeln. Hier wird geprüft und an `ErinnerungPlugin.kt`
//! durchgereicht, beschränkt aufs Hauptfenster. Kein Netz, kein Token: der
//! frühere Dienst, der dafür beim Panel nachfragte, ist entfallen.

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, WebviewWindow};

/// Android hält je App höchstens 500 Wecker; die App plant höchstens 100.
const HOECHSTENS: usize = 100;
const TEXT_HOECHSTENS: usize = 500;

#[derive(Debug, Deserialize, Serialize)]
pub struct Erinnerung {
    pub schluessel: String,
    pub zeit: i64,
    pub titel: String,
    pub text: String,
    pub oeffentlich: String,
}

fn pruefe(liste: &[Erinnerung]) -> Result<(), String> {
    if liste.len() > HOECHSTENS {
        return Err("Zu viele Erinnerungen".into());
    }
    for e in liste {
        let schluessel_gut = !e.schluessel.is_empty()
            && e.schluessel.len() <= 200
            && e.schluessel.chars().all(|c| c.is_ascii_alphanumeric() || "_-:.".contains(c));
        let texte_gut = [&e.titel, &e.text, &e.oeffentlich]
            .iter()
            .all(|t| !t.is_empty() && t.chars().count() <= TEXT_HOECHSTENS);
        if !schluessel_gut || !texte_gut || e.zeit <= 0 {
            return Err("Ungültige Erinnerung".into());
        }
    }
    Ok(())
}

#[cfg(target_os = "android")]
pub struct ErinnerungState<R: tauri::Runtime>(pub tauri::plugin::PluginHandle<R>);

/// Meldet `ErinnerungPlugin` bei Tauri an. Wird in `lib.rs` eingehängt.
#[cfg(target_os = "android")]
pub fn init_android_erinnerung<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    use tauri::Manager;
    tauri::plugin::Builder::<R>::new("erinnerung")
        .setup(|app, api| {
            let handle = api.register_android_plugin("com.mauntingstudios.smart_system", "ErinnerungPlugin")?;
            app.manage(ErinnerungState(handle));
            Ok(())
        })
        .build()
}

/// Ersetzt alle geplanten Erinnerungen; eine leere Liste nimmt alle weg.
#[tauri::command(async)]
pub async fn erinnerungen_planen(app: AppHandle, fenster: WebviewWindow, liste: Vec<Erinnerung>) -> Result<(), String> {
    if fenster.label() != "main" {
        return Err("Nur das Hauptfenster plant Erinnerungen.".into());
    }
    pruefe(&liste)?;
    weiterreichen(&app, liste)
}

#[cfg(target_os = "android")]
fn weiterreichen(app: &AppHandle, liste: Vec<Erinnerung>) -> Result<(), String> {
    use tauri::Manager;
    let plugin = app
        .try_state::<ErinnerungState<tauri::Wry>>()
        .ok_or_else(|| "Erinnerungen sind auf diesem Gerät nicht erreichbar.".to_string())?;
    plugin
        .0
        .run_mobile_plugin::<serde_json::Value>("planen", serde_json::json!({ "erinnerungen": liste }))
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Am Desktop meldet die offene App selbst; geplant wird nichts.
#[cfg(not(target_os = "android"))]
fn weiterreichen(_app: &AppHandle, _liste: Vec<Erinnerung>) -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn erinnerung(schluessel: &str) -> Erinnerung {
        Erinnerung {
            schluessel: schluessel.into(),
            zeit: 1_790_000_000_000,
            titel: "Terminerinnerung".into(),
            text: "Zahnarzt am 05.10.26, 14:00".into(),
            oeffentlich: "Ein Termin steht an".into(),
        }
    }

    #[test]
    fn nimmt_eine_gute_liste_an() {
        assert!(pruefe(&[erinnerung("5_abc-1_2026-10-05_24h")]).is_ok());
        assert!(pruefe(&[]).is_ok());
    }

    #[test]
    fn lehnt_zu_viele_und_seltsame_ab() {
        let viele: Vec<Erinnerung> = (0..=HOECHSTENS).map(|i| erinnerung(&format!("k{i}"))).collect();
        assert!(pruefe(&viele).is_err());
        assert!(pruefe(&[erinnerung("a/../b")]).is_err());
        assert!(pruefe(&[erinnerung("")]).is_err());
        let mut lang = erinnerung("k");
        lang.text = "x".repeat(TEXT_HOECHSTENS + 1);
        assert!(pruefe(&[lang]).is_err());
        let mut ohne_zeit = erinnerung("k");
        ohne_zeit.zeit = 0;
        assert!(pruefe(&[ohne_zeit]).is_err());
    }
}
