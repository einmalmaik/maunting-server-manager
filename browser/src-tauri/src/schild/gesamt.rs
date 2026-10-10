//! Was das Schild geschafft hat, seit der Browser zählt: geblockte Werbe- und
//! Trackeranfragen, abgelehnte Cookie-Hinweise und die Seitenaufrufe, auf
//! denen etwas geblockt wurde. Daraus schätzt die Startseite gesparte Daten
//! und Rechenzeit (`seite/start/schaetzung.ts`).
//!
//! Nur Zahlen und der Zeitpunkt, ab dem gezählt wird, nie Adressen oder
//! Seiten. Sie bleiben auf dem Gerät (`schild-gesamt.json`) und werden
//! höchstens alle 30 Sekunden und beim Schließen geschrieben.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

use super::Treffer;

static WERBUNG: AtomicU64 = AtomicU64::new(0);
static TRACKER: AtomicU64 = AtomicU64::new(0);
static COOKIES: AtomicU64 = AtomicU64::new(0);
static SEITEN: AtomicU64 = AtomicU64::new(0);
/// Sekunden seit 1970, ab denen gezählt wird; 0, bis `starten` gelaufen ist.
static SEIT: AtomicU64 = AtomicU64::new(0);
static GEAENDERT: AtomicBool = AtomicBool::new(false);

const SPEICHERN_ALLE: Duration = Duration::from_secs(30);

#[derive(Serialize, Deserialize, Default, Clone, Copy, PartialEq, Eq, Debug)]
pub struct Gesamt {
    #[serde(default)]
    pub werbung: u64,
    #[serde(default)]
    pub tracker: u64,
    /// Cookie-Hinweise, die autoconsent abgelehnt hat (nicht nur ausgeblendet).
    #[serde(default)]
    pub cookies: u64,
    /// Seitenaufrufe, auf denen das Schild mindestens eine Anfrage geblockt hat.
    #[serde(default)]
    pub seiten: u64,
    #[serde(default)]
    pub seit: u64,
}

pub(super) fn zaehlen(treffer: Treffer) {
    match treffer {
        Treffer::Werbung => WERBUNG.fetch_add(1, Ordering::Relaxed),
        Treffer::Tracker => TRACKER.fetch_add(1, Ordering::Relaxed),
    };
    GEAENDERT.store(true, Ordering::Relaxed);
}

/// Der erste Treffer eines Seitenaufrufs (`schild::pruefen`).
pub(super) fn seite_zaehlen() {
    SEITEN.fetch_add(1, Ordering::Relaxed);
    GEAENDERT.store(true, Ordering::Relaxed);
}

/// Eine Ablehnung je Seitenaufruf (`schild::cookies_abgelehnt`).
pub(super) fn cookie_zaehlen() {
    COOKIES.fetch_add(1, Ordering::Relaxed);
    GEAENDERT.store(true, Ordering::Relaxed);
}

fn stand() -> Gesamt {
    Gesamt {
        werbung: WERBUNG.load(Ordering::Relaxed),
        tracker: TRACKER.load(Ordering::Relaxed),
        cookies: COOKIES.load(Ordering::Relaxed),
        seiten: SEITEN.load(Ordering::Relaxed),
        seit: SEIT.load(Ordering::Relaxed),
    }
}

/// Eine kaputte Datei heißt null, nie ein Fehler beim Start.
fn lesen(text: &str) -> Gesamt {
    serde_json::from_str(text).unwrap_or_default()
}

fn sekunden(zeit: SystemTime) -> u64 {
    zeit.duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// Ab wann gezählt wird. Eine Datei von vor dem 10.10.2026 kennt den
/// Zeitpunkt nicht; dann gilt der erste Start, das Anlegen des App-Ordners.
/// Das Anlegen der Datei taugt nicht: `datei::ersetzen` legt sie bei jedem
/// Speichern neu an.
fn seit_von(gespeichert: Option<Gesamt>, erster_start: Option<SystemTime>, jetzt: SystemTime) -> u64 {
    match (gespeichert, erster_start) {
        (Some(g), _) if g.seit > 0 => g.seit,
        (Some(_), Some(angelegt)) => sekunden(angelegt.min(jetzt)),
        _ => sekunden(jetzt),
    }
}

fn pfad(app: &AppHandle) -> Option<PathBuf> {
    Some(app.path().app_local_data_dir().ok()?.join("schild-gesamt.json"))
}

/// Liest den gespeicherten Stand dazu und schreibt danach regelmäßig.
pub fn starten(app: &AppHandle) {
    let gespeichert = pfad(app).and_then(|p| std::fs::read_to_string(p).ok()).map(|t| lesen(&t));
    let erster_start = app.path().app_local_data_dir().ok().and_then(|o| std::fs::metadata(o).and_then(|m| m.created()).ok());
    if let Some(g) = gespeichert {
        // Dazuzählen statt setzen: was vor dem Lesen schon geblockt wurde, bleibt.
        WERBUNG.fetch_add(g.werbung, Ordering::Relaxed);
        TRACKER.fetch_add(g.tracker, Ordering::Relaxed);
        COOKIES.fetch_add(g.cookies, Ordering::Relaxed);
        SEITEN.fetch_add(g.seiten, Ordering::Relaxed);
    }
    let ohne_zeitpunkt = gespeichert.is_none_or(|g| g.seit == 0);
    SEIT.store(seit_von(gespeichert, erster_start, SystemTime::now()), Ordering::Relaxed);
    if ohne_zeitpunkt {
        GEAENDERT.store(true, Ordering::Relaxed);
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
        assert_eq!(lesen(r#"{"werbung":12,"tracker":3}"#), Gesamt { werbung: 12, tracker: 3, ..Default::default() });
        assert_eq!(
            lesen(r#"{"werbung":1,"tracker":2,"cookies":3,"seiten":4,"seit":1791000000}"#),
            Gesamt { werbung: 1, tracker: 2, cookies: 3, seiten: 4, seit: 1_791_000_000 }
        );
        assert_eq!(lesen(r#"{"werbung":-1,"tracker":"x"}"#), Gesamt::default());
        assert_eq!(lesen(r#"{"werbung":1,"seiten":-4}"#), Gesamt::default());
        assert_eq!(lesen("{kaputt"), Gesamt::default());
    }

    #[test]
    fn der_zeitpunkt_kommt_aus_der_datei_sonst_vom_ersten_start() {
        let jetzt = UNIX_EPOCH + Duration::from_secs(2_000_000_000);
        let angelegt = UNIX_EPOCH + Duration::from_secs(1_900_000_000);
        let mit = Gesamt { seit: 1_800_000_000, ..Default::default() };
        let alt = Some(Gesamt { werbung: 5, ..Default::default() });
        assert_eq!(seit_von(Some(mit), Some(angelegt), jetzt), 1_800_000_000);
        assert_eq!(seit_von(alt, Some(angelegt), jetzt), 1_900_000_000);
        // Ein Ordner aus der Zukunft (verstellte Uhr) gilt als jetzt.
        assert_eq!(seit_von(alt, Some(jetzt + Duration::from_secs(5)), jetzt), 2_000_000_000);
        assert_eq!(seit_von(alt, None, jetzt), 2_000_000_000);
        // Ohne Datei beginnt das Zählen jetzt, auch in einem alten Ordner.
        assert_eq!(seit_von(None, Some(angelegt), jetzt), 2_000_000_000);
    }
}
