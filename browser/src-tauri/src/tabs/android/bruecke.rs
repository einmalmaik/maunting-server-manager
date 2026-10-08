//! JNI für `TabsBruecke.kt`: was Kotlin aus den Tab-WebViews an Rust gibt.
//!
//! Entschieden wird mit denselben Funktionen wie unter Windows: `tabs::weg`
//! für Navigationen, `schild::pruefen` für Anfragen, `formular::lesen` für
//! Nachrichten von `seite.js`. Adressen nimmt Kotlin aus der WebView, nie aus
//! einer Nachricht der Seite.
//!
//! `anfrage` läuft auf einem Faden der WebView, alles andere auf dem
//! UI-Faden; keine Funktion hier wartet auf Kotlin.

use std::sync::OnceLock;

use jni::objects::{JClass, JString};
use jni::sys::{jboolean, jstring, JNI_FALSE, JNI_TRUE};
use jni::JNIEnv;
use tauri::{AppHandle, Emitter};

use crate::schild::{self, AnfrageArt};
use crate::tabs::formular::{gleiche_herkunft, lesen};
use crate::tabs::{id_pruefen, melden, weg, TabEreignis, Weg, EREIGNIS};

static APP: OnceLock<AppHandle> = OnceLock::new();

pub(super) fn anmelden(app: AppHandle) {
    let _ = APP.set(app);
}

fn text(env: &mut JNIEnv, wert: &JString) -> String {
    env.get_string(wert).map(String::from).unwrap_or_default()
}

fn zurueck(env: &mut JNIEnv, wert: &str) -> jstring {
    env.new_string(wert).map(|s| s.into_raw()).unwrap_or(std::ptr::null_mut())
}

fn ja(wert: bool) -> jboolean {
    if wert { JNI_TRUE } else { JNI_FALSE }
}

/// Was Kotlin selbst melden darf. Schild, Gesperrt und Formular meldet nur
/// Rust. Vollbild zeigt Kotlin selbst, Rechte lehnt es ab, und Protokoll,
/// Status, Ton und Schlaf gibt es auf Android nicht.
const ARTEN: [&str; 13] = [
    "laedt", "geladen", "adresse", "titel", "favicon", "neuer_tab", "absturz", "kontextmenue", "dialog", "anmeldung",
    "fehlerseite", "treffer", "download",
];

/// Ein Ereignis eines Tabs, als JSON in der Form von [`TabEreignis`].
fn ereignis_pruefen(json: &str) -> Option<serde_json::Value> {
    let wert: serde_json::Value = serde_json::from_str(json).ok()?;
    let art = wert.get("art")?.as_str()?;
    let id = wert.get("id")?.as_str()?;
    (ARTEN.contains(&art) && id_pruefen(id).is_ok()).then_some(wert)
}

#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_melden<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    json: JString<'l>,
) {
    let json = text(&mut env, &json);
    if let (Some(app), Some(wert)) = (APP.get(), ereignis_pruefen(&json)) {
        let _ = app.emit_to("main", EREIGNIS, wert);
    }
}

/// `""`: laden. `"-"`: nicht laden (verboten oder gesperrt; gesperrt meldet
/// Rust selbst). Sonst die Adresse, die stattdessen lädt (sichere Suche).
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_weg<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    tab: JString<'l>,
    url: JString<'l>,
) -> jstring {
    let (tab, url) = (text(&mut env, &tab), text(&mut env, &url));
    let antwort = match weg(&url) {
        Weg::Laden => String::new(),
        Weg::Verboten => "-".into(),
        Weg::Gesperrt(grund) => {
            if let (Some(app), Ok(())) = (APP.get(), id_pruefen(&tab)) {
                melden(app, TabEreignis::Gesperrt { id: tab, url, grund: grund.into() });
            }
            "-".into()
        }
        Weg::Umleiten(sicher) => sicher,
    };
    zurueck(&mut env, &antwort)
}

/// `true`: blocken. Hier zählen Sperre und Schild für alles, was die Seite
/// nachlädt. Die Seite selbst prüft `weg`; ein Formular, das per POST
/// abschickt, kommt aber nur hier vorbei, deshalb sperrt die Sperre auch
/// den Hauptrahmen und meldet es.
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_anfrage<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    tab: JString<'l>,
    url: JString<'l>,
    seite: JString<'l>,
    hauptframe: jboolean,
    accept: JString<'l>,
) -> jboolean {
    let (tab, url, seite, accept) =
        (text(&mut env, &tab), text(&mut env, &url), text(&mut env, &seite), text(&mut env, &accept));
    let Some(app) = APP.get() else { return JNI_FALSE };
    if let Some(grund) = schild::sperre::gesperrt(&url) {
        if hauptframe == JNI_TRUE && id_pruefen(&tab).is_ok() {
            melden(app, TabEreignis::Gesperrt { id: tab, url, grund: grund.name().into() });
        }
        return JNI_TRUE;
    }
    if hauptframe == JNI_TRUE {
        return JNI_FALSE;
    }
    ja(schild::pruefen(app, &tab, &url, &seite, AnfrageArt::raten(&url, &accept)))
}

/// Eine neue Seite im Tab: der Zähler des Schilds beginnt von vorn.
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_seitenwechsel<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    tab: JString<'l>,
) {
    let tab = text(&mut env, &tab);
    if let Some(app) = APP.get() {
        schild::seitenwechsel(app, &tab);
    }
}

/// Eine Nachricht von `seite.js` aus dem obersten Rahmen; `url` aus der WebView.
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_nachricht<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    tab: JString<'l>,
    url: JString<'l>,
    roh: JString<'l>,
) {
    let (id, url, roh) = (text(&mut env, &tab), text(&mut env, &url), text(&mut env, &roh));
    let (Some(app), Some(meldung)) = (APP.get(), lesen(&roh)) else { return };
    if id_pruefen(&id).is_ok() && (url.starts_with("https://") || url.starts_with("http://")) {
        melden(app, TabEreignis::Formular { id, url, meldung });
    }
}

#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_gleicheHerkunft<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    a: JString<'l>,
    b: JString<'l>,
) -> jboolean {
    let (a, b) = (text(&mut env, &a), text(&mut env, &b));
    ja(gleiche_herkunft(&a, &b))
}

/// Der Dateiname eines Downloads, bereinigt wie unter Windows; leer, wenn
/// die Sperre die Adresse nicht erlaubt (der Download-Dienst folgt
/// Weiterleitungen selbst, an `anfrage` vorbei).
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_dateiname<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    url: JString<'l>,
    vorschlag: JString<'l>,
) -> jstring {
    let (url, vorschlag) = (text(&mut env, &url), text(&mut env, &vorschlag));
    let name = if schild::sperre::gesperrt(&url).is_some() {
        String::new()
    } else {
        crate::downloads::name_fuer(&url, std::path::Path::new(&vorschlag))
    };
    zurueck(&mut env, &name)
}

/// Das Such-Widget hat etwas angestoßen; die Oberfläche holt es per `widget_start`.
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_widget<'l>(_: JNIEnv<'l>, _: JClass<'l>) {
    if let Some(app) = APP.get() {
        let _ = app.emit_to("main", crate::widget::EREIGNIS, ());
    }
}

/// `seite.js`, damit es nur eine Fassung davon gibt.
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_seitenskript<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
) -> jstring {
    zurueck(&mut env, crate::seite::SKRIPT)
}

/// Das Skript, das die seitenspezifische Kosmetik einsetzt; leer, wenn nichts.
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_kosmetik<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    url: JString<'l>,
) -> jstring {
    let url = text(&mut env, &url);
    let skript = match schild::kosmetik(&url) {
        Some(k) if !k.css.is_empty() => schild::stil_skript(&k.css),
        _ => String::new(),
    };
    zurueck(&mut env, &skript)
}

/// Ob danach die allgemeinen Regeln folgen: dann sammelt Kotlin Klassen und IDs.
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_klassenSammeln<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    url: JString<'l>,
) -> jstring {
    let url = text(&mut env, &url);
    let skript = match schild::kosmetik(&url) {
        Some(k) if !k.generichide => schild::KLASSEN_SAMMELN,
        _ => "",
    };
    zurueck(&mut env, skript)
}

/// Die allgemeinen Regeln für die gesammelten Klassen und IDs; leer, wenn nichts.
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_allgemein<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    url: JString<'l>,
    json: JString<'l>,
) -> jstring {
    let (url, json) = (text(&mut env, &url), text(&mut env, &json));
    let skript = schild::kosmetik(&url)
        .filter(|k| !k.generichide)
        .and_then(|k| schild::allgemeine_kosmetik(&url, &json, &k.ausnahmen))
        .map(|css| schild::stil_skript(&css))
        .unwrap_or_default();
    zurueck(&mut env, &skript)
}
