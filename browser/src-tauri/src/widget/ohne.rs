//! Auf dem Desktop gibt es kein Widget: nichts angestoßen, nichts anzuheften.

use serde_json::Value;
use tauri::AppHandle;

use crate::tabs::Tabs;

pub fn start(_: &AppHandle) -> Result<Option<Value>, String> {
    Ok(None)
}

pub fn stand(_: &AppHandle, _: bool) -> Result<(), String> {
    Ok(())
}

pub fn lage(_: &AppHandle) -> Result<String, String> {
    Ok("nein".into())
}

pub fn anheften(_: &AppHandle) -> Result<bool, String> {
    Ok(false)
}

/// Unter Windows wählt man den Standardbrowser in den Windows-Einstellungen.
pub fn standardbrowser(_: &AppHandle) -> Result<bool, String> {
    Ok(false)
}

pub fn standardbrowser_werden(_: &AppHandle) -> Result<bool, String> {
    Err("Nur unter Android.".into())
}

pub fn standardbrowser_einstellungen(_: &AppHandle) -> Result<bool, String> {
    Err("Nur unter Android.".into())
}

pub fn tastatur_zeigen(_: &AppHandle) -> Result<(), String> {
    Ok(())
}

pub fn bildsuche(_: &AppHandle, _: &Tabs, _: &str, _: bool, _: url::Url, _: &str, _: bool) -> Result<(), String> {
    Err("Die Bildsuche gibt es nur auf Android.".into())
}
