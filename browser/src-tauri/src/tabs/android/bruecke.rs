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
use crate::tabs::ereignis::von_kotlin;
use crate::tabs::formular::{gleiche_herkunft, lesen};
use crate::tabs::{id_pruefen, melden, vorab, weg, TabEreignis, Vorab, Weg};

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

#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_melden<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    json: JString<'l>,
) {
    let json = text(&mut env, &json);
    if let (Some(app), Some(ereignis)) = (APP.get(), von_kotlin(&json)) {
        melden(app, ereignis);
    }
}

/// `""`: laden. `"-"`: nicht laden (verboten oder gesperrt; gesperrt meldet
/// Rust selbst). Sonst die Adresse, die stattdessen lädt (HTTPS, ohne
/// Tracking-Parameter, sichere Suche).
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_weg<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    tab: JString<'l>,
    url: JString<'l>,
    weiterleitung: jboolean,
) -> jstring {
    let (tab, url) = (text(&mut env, &tab), text(&mut env, &url));
    let antwort = match weg(&url, weiterleitung != JNI_FALSE) {
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

/// Die Seite ließ sich nicht laden: die Adresse mit `http://`, wenn der
/// Browser sie eben erst hochgestuft hat, sonst `""` (Fehlerseite).
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_rueckfall<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
    url: JString<'l>,
) -> jstring {
    let url = text(&mut env, &url);
    let antwort = crate::tabs::https::rueckfall(&url).unwrap_or_default();
    zurueck(&mut env, &antwort)
}

/// `true`: blocken. Dieselbe Entscheidung wie unter Windows ([`vorab`]).
/// Die Seite selbst prüft `weg`; ein Formular, das per POST abschickt, kommt
/// aber nur hier vorbei, deshalb gilt sie auch für den Hauptrahmen, und eine
/// Sperre dort wird gemeldet.
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
    let hauptrahmen = hauptframe == JNI_TRUE;
    match vorab(&url, hauptrahmen) {
        Vorab::Blocken(grund) => {
            if let (Some(grund), true, Ok(())) = (grund, hauptrahmen, id_pruefen(&tab)) {
                melden(app, TabEreignis::Gesperrt { id: tab, url, grund: grund.into() });
            }
            return JNI_TRUE;
        }
        Vorab::Laden { .. } => return JNI_FALSE,
        // Den YouTube-Kopf kann Android nicht setzen (Datenschutzerklärung, Jugendschutz).
        Vorab::Schild { .. } => {}
    }
    let art = AnfrageArt::raten(&url, &accept);
    // Ein Service Worker gehört keinem Tab (`TabsPlugin.workerPruefen`): blocken ja, zählen nicht.
    if id_pruefen(&tab).is_err() {
        return ja(schild::einstufen(&url, &seite, art).is_some());
    }
    ja(schild::pruefen(app, &tab, &url, &seite, art))
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
/// die Sperre die Adresse nicht erlaubt. `Herunterladen.kt` fragt das vor
/// jeder Weiterleitung eines Downloads; die gehen an `anfrage` vorbei.
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

/// Das Cookie-Skript mit dem heutigen Stand des Schilds (`crate::cookies`).
#[no_mangle]
pub extern "system" fn Java_com_mauntingstudios_secure_1browser_TabsBruecke_cookiesskript<'l>(
    mut env: JNIEnv<'l>,
    _: JClass<'l>,
) -> jstring {
    zurueck(&mut env, &crate::cookies::skript())
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
