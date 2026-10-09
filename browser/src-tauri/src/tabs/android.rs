//! Tabs unter Android: je Tab eine `android.webkit.WebView`, die das
//! Kotlin-Plugin `TabsPlugin` über die Oberfläche legt.
//!
//! - Rust → Kotlin über das Plugin (`rufen`). Jeder Aufruf hält den Faden,
//!   bis Kotlin auf dem UI-Faden geantwortet hat; die Befehle in `mod.rs`
//!   sind deshalb alle `async`, und hier wird nie das Schloss von `Tabs`
//!   gehalten, während Kotlin arbeitet.
//! - Kotlin → Rust über JNI (`bruecke.rs`): Navigation, Anfragen,
//!   Seitennachrichten und Ereignisse. Entschieden wird dort mit denselben
//!   Funktionen wie unter Windows.
//!
//! Seiten liegen im Profil `seiten`, private Tabs gemeinsam in einem eigenen
//! (`TabsPlugin.kt`). Die Oberfläche bleibt im Standardprofil und teilt mit keiner
//! Seite Cookies oder Speicher.

mod bruecke;

use base64::Engine;
use serde::de::DeserializeOwned;
use serde_json::{json, Value};
use tauri::plugin::{Builder, PluginHandle, TauriPlugin};
use tauri::{AppHandle, Manager, Wry};

use super::{Antwort, Tabs};

pub(crate) const PAKET: &str = "com.mauntingstudios.secure_browser";

struct Kotlin(PluginHandle<Wry>);

pub fn init() -> TauriPlugin<Wry> {
    Builder::new("msb-tabs")
        .setup(|app, api| {
            let handle = api.register_android_plugin(PAKET, "TabsPlugin")?;
            app.manage(Kotlin(handle));
            bruecke::anmelden(app.clone());
            Ok(())
        })
        .build()
}

/// Befehle ohne Rückgabe antworten mit `null`.
type Leer = serde::de::IgnoredAny;

fn rufen<T: DeserializeOwned>(app: &AppHandle, befehl: &str, daten: Value) -> Result<T, String> {
    let kotlin = app.try_state::<Kotlin>().ok_or("Die Tabs sind auf diesem Gerät nicht erreichbar")?;
    kotlin.0.run_mobile_plugin::<T>(befehl, daten).map_err(|e| e.to_string())
}

/// Für Aufrufe ohne Rückgabe an die Oberfläche: ein Fehler kommt ins Log.
fn senden(app: &AppHandle, befehl: &str, daten: Value) {
    if let Err(fehler) = rufen::<Leer>(app, befehl, daten) {
        eprintln!("[MSB] Tabs: {befehl} gescheitert: {fehler}");
    }
}

pub fn laden(app: &AppHandle, tabs: &Tabs, id: &str, ziel: url::Url, privat: bool) -> Result<(), String> {
    rufen::<Leer>(app, "laden", json!({ "id": id, "url": ziel.as_str(), "privat": privat }))?;
    rahmen_anwenden(app, tabs);
    sichtbarkeit(app, tabs);
    Ok(())
}

/// Vorne liegt der aktive Tab, außer die Oberfläche legt gerade etwas darüber.
pub fn sichtbarkeit(app: &AppHandle, tabs: &Tabs) {
    let vorne = {
        let z = tabs.0.lock().unwrap();
        if z.verdeckt { None } else { z.aktiv.clone() }
    };
    senden(app, "sichtbarkeit", json!({ "vorne": vorne }));
}

/// Der Inhaltsbereich in CSS-Pixeln der Oberfläche; Kotlin rechnet um.
pub fn rahmen_anwenden(app: &AppHandle, tabs: &Tabs) {
    let r = tabs.0.lock().unwrap().rahmen;
    senden(app, "rahmen", json!({ "x": r.x, "y": r.y, "breite": r.breite, "hoehe": r.hoehe }));
}

pub fn schliessen(app: &AppHandle, id: &str) {
    senden(app, "schliessen", json!({ "id": id }));
}

/// Kotlin holt das Skript je Tab über `TabsBruecke.cookiesskript`.
pub fn cookies_erneuern(app: &AppHandle) {
    senden(app, "cookies", json!({}));
}

/// Zurück, Vor, Neu laden, Anhalten (`tab_aktion`).
pub fn aktion(app: &AppHandle, id: &str, aktion: &str) -> Result<(), String> {
    senden(app, "aktion", json!({ "id": id, "aktion": aktion }));
    Ok(())
}

#[derive(serde::Deserialize)]
struct Bild {
    jpeg: String,
}

/// Bild des vorderen Tabs für die Oberfläche, solange sie etwas darüberlegt.
pub fn standbild(app: &AppHandle, tabs: &Tabs) -> Result<Vec<u8>, String> {
    let id = tabs.0.lock().unwrap().aktiv.clone().ok_or("Kein Tab vorne")?;
    let bild: Bild = rufen(app, "standbild", json!({ "id": id }))?;
    base64::engine::general_purpose::STANDARD.decode(bild.jpeg).map_err(|e| e.to_string())
}

pub fn antworten(app: &AppHandle, nr: u64, antwort: Antwort) -> Result<(), String> {
    let antwort = serde_json::to_string(&antwort).map_err(|e| e.to_string())?;
    rufen::<Leer>(app, "antworten", json!({ "nr": nr, "antwort": antwort })).map(|_| ())
}

pub fn protokoll(_: &AppHandle, _: &str, _: String, _: Value) -> Result<Value, String> {
    Err("Entwicklerwerkzeuge gibt es auf Android nicht".into())
}

pub fn suchen(app: &AppHandle, id: &str, richtung: String, begriff: String) -> Result<(), String> {
    rufen::<Leer>(app, "suchen", json!({ "id": id, "richtung": richtung, "begriff": begriff })).map(|_| ())
}

/// Kotlin prüft auf dem UI-Faden, ob der Tab noch auf der Herkunft von `fuer`
/// steht, und schickt erst dann; dazwischen kann keine Navigation liegen.
pub fn fuellen(app: &AppHandle, id: &str, fuer: &str, nachricht: String) -> Result<(), String> {
    rufen::<Leer>(app, "fuellen", json!({ "id": id, "fuer": fuer, "nachricht": nachricht })).map(|_| ())
}

/// Wie [`fuellen`], an jeden Unterrahmen der Herkunft `rahmen`, den Kotlin kennt.
pub fn fuellen_rahmen(app: &AppHandle, id: &str, fuer: &str, rahmen: &str, nachricht: String) -> Result<(), String> {
    rufen::<Leer>(app, "fuellen", json!({ "id": id, "fuer": fuer, "rahmen": rahmen, "nachricht": nachricht })).map(|_| ())
}

/// Cookies, Speicher und Cache im Profil `seiten`; private Profile fallen
/// ohnehin mit ihrem Tab.
pub fn daten_leeren(app: &AppHandle) -> Result<(), String> {
    rufen::<Leer>(app, "datenLeeren", json!({})).map(|_| ())
}

pub fn drucken(app: &AppHandle, id: &str) -> Result<(), String> {
    rufen::<Leer>(app, "drucken", json!({ "id": id })).map(|_| ())
}

/// Die Download-Liste des Systems; die Dateien liegen in `Download/`.
pub fn downloads_zeigen(app: &AppHandle) -> Result<(), String> {
    rufen::<Leer>(app, "downloadsZeigen", json!({})).map(|_| ())
}

/// Ton je Tab gibt es unter Android nicht; die Oberfläche bietet ihn nicht an.
pub fn stumm(_: &AppHandle, _: &str, _: bool) -> Result<(), String> {
    Ok(())
}

/// Die Oberfläche bekommt den Fokus unter Android mit dem Tippen selbst.
pub fn oberflaeche_fokussieren(_: &AppHandle) {}

/// Schlafen und Verwerfen gibt es unter Android nicht; das System beendet
/// Renderer selbst, wenn der Speicher knapp wird (`Tab.kt`, `absturz`).
pub fn ruhen(_: &AppHandle, _: Vec<(String, super::ruhe::Ruhe)>, _: Vec<String>) {}

/// Lädt im Tab die Seite, die das Foto des Widgets an `ziel` schickt.
pub fn bildsuche(app: &AppHandle, tabs: &Tabs, id: &str, privat: bool, ziel: url::Url, feld: &str, base64: bool) -> Result<(), String> {
    let daten = json!({ "id": id, "privat": privat, "url": ziel.as_str(), "feld": feld, "base64": base64 });
    rufen::<Leer>(app, "bildsuche", daten)?;
    rahmen_anwenden(app, tabs);
    sichtbarkeit(app, tabs);
    Ok(())
}
