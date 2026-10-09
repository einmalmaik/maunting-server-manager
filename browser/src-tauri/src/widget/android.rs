//! Das Widget unter Android: das Kotlin-Plugin `WidgetPlugin`. Das Foto
//! schickt ein Tab ab, deshalb geht die Bildsuche über `TabsPlugin`.

use serde::de::DeserializeOwned;
use serde_json::{json, Value};
use tauri::plugin::{Builder, PluginHandle, TauriPlugin};
use tauri::{AppHandle, Manager, Wry};

use crate::tabs::Tabs;

struct Kotlin(PluginHandle<Wry>);

pub fn init() -> TauriPlugin<Wry> {
    Builder::new("msb-widget")
        .setup(|app, api| {
            app.manage(Kotlin(api.register_android_plugin(crate::tabs::android::PAKET, "WidgetPlugin")?));
            Ok(())
        })
        .build()
}

fn rufen<T: DeserializeOwned>(app: &AppHandle, befehl: &str, daten: Value) -> Result<T, String> {
    let kotlin = app.try_state::<Kotlin>().ok_or("Das Widget ist auf diesem Gerät nicht erreichbar")?;
    kotlin.0.run_mobile_plugin::<T>(befehl, daten).map_err(|e| e.to_string())
}

type Leer = serde::de::IgnoredAny;

/// Was das Widget angestoßen hat, einmal (`WidgetActivity.kt`).
pub fn start(app: &AppHandle) -> Result<Option<Value>, String> {
    #[derive(serde::Deserialize)]
    struct Start {
        start: Option<Value>,
    }
    rufen::<Start>(app, "startAbholen", json!({})).map(|s| s.start)
}

/// Ob das Widget die Kamera zeigt.
pub fn stand(app: &AppHandle, bildsuche: bool) -> Result<(), String> {
    rufen::<Leer>(app, "stand", json!({ "bildsuche": bildsuche })).map(|_| ())
}

/// `liegt`, `anheftbar` oder `nein` (`SuchWidget.lage`).
pub fn lage(app: &AppHandle) -> Result<String, String> {
    #[derive(serde::Deserialize)]
    struct Lage {
        lage: String,
    }
    rufen::<Lage>(app, "lage", json!({})).map(|l| l.lage)
}

pub fn anheften(app: &AppHandle) -> Result<bool, String> {
    #[derive(serde::Deserialize)]
    struct Ok {
        ok: bool,
    }
    rufen::<Ok>(app, "anheften", json!({})).map(|o| o.ok)
}

pub fn tastatur_zeigen(app: &AppHandle) -> Result<(), String> {
    rufen::<Leer>(app, "tastaturZeigen", json!({})).map(|_| ())
}

pub fn bildsuche(app: &AppHandle, tabs: &Tabs, id: &str, privat: bool, ziel: url::Url, feld: &str, base64: bool) -> Result<(), String> {
    crate::tabs::android::bildsuche(app, tabs, id, privat, ziel, feld, base64)
}
