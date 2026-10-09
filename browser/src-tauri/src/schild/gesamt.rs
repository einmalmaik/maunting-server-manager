//! Wie viel das Schild seit der Installation geblockt hat.
//!
//! Nur zwei Zahlen, nie Adressen oder Seiten. Sie bleiben auf dem Gerät
//! (`schild-gesamt.json`) und werden höchstens alle 30 Sekunden und beim
//! Schließen geschrieben.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

use super::Treffer;

static WERBUNG: AtomicU64 = AtomicU64::new(0);
static TRACKER: AtomicU64 = AtomicU64::new(0);
static GEAENDERT: AtomicBool = AtomicBool::new(false);

const SPEICHERN_ALLE: Duration = Duration::from_secs(30);

#[derive(Serialize, Deserialize, Default, Clone, Copy, PartialEq, Eq, Debug)]
pub struct Gesamt {
    #[serde(default)]
    pub werbung: u64,
    #[serde(default)]
    pub tracker: u64,
}

pub(super) fn zaehlen(treffer: Treffer) {
    match treffer {
        Treffer::Werbung => WERBUNG.fetch_add(1, Ordering::Relaxed),
        Treffer::Tracker => TRACKER.fetch_add(1, Ordering::Relaxed),
    };
    GEAENDERT.store(true, Ordering::Relaxed);
}

fn stand() -> Gesamt {
    Gesamt { werbung: WERBUNG.load(Ordering::Relaxed), tracker: TRACKER.load(Ordering::Relaxed) }
}

/// Eine kaputte Datei heißt null, nie ein Fehler beim Start.
fn lesen(text: &str) -> Gesamt {
    serde_json::from_str(text).unwrap_or_default()
}

fn pfad(app: &AppHandle) -> Option<PathBuf> {
    Some(app.path().app_local_data_dir().ok()?.join("schild-gesamt.json"))
}

/// Liest den gespeicherten Stand dazu und schreibt danach regelmäßig.
pub fn starten(app: &AppHandle) {
    if let Some(gespeichert) = pfad(app).and_then(|p| std::fs::read_to_string(p).ok()).map(|t| lesen(&t)) {
        // Dazuzählen statt setzen: was vor dem Lesen schon geblockt wurde, bleibt.
        WERBUNG.fetch_add(gespeichert.werbung, Ordering::Relaxed);
        TRACKER.fetch_add(gespeichert.tracker, Ordering::Relaxed);
    }
    let app = app.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(SPEICHERN_ALLE);
        speichern(&app);
    });
}

pub fn speichern(app: &AppHandle) {
    if !GEAENDERT.swap(false, Ordering::Relaxed) {
        return;
    }
    let Some(ziel) = pfad(app) else { return };
    let json = serde_json::to_string(&stand()).unwrap_or_default();
    let geschrieben = ziel.parent().map(std::fs::create_dir_all).transpose().and_then(|_| crate::datei::ersetzen(&ziel, json));
    if let Err(fehler) = geschrieben {
        GEAENDERT.store(true, Ordering::Relaxed);
        eprintln!("[MSB] Zähler des Schilds nicht gespeichert: {fehler}");
    }
}

#[tauri::command]
pub fn schild_gesamt() -> Gesamt {
    stand()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn liest_nur_zahlen_und_nimmt_kaputtes_als_null() {
        assert_eq!(lesen(r#"{"werbung":12,"tracker":3}"#), Gesamt { werbung: 12, tracker: 3 });
        assert_eq!(lesen(r#"{"werbung":12}"#), Gesamt { werbung: 12, tracker: 0 });
        assert_eq!(lesen(r#"{"werbung":-1,"tracker":"x"}"#), Gesamt::default());
        assert_eq!(lesen("{kaputt"), Gesamt::default());
    }
}
