//! Der Jugend- und Suchtschutz zur Laufzeit: Datei, Befehle der Oberfläche,
//! Ablauf offener Anträge und die Serie. Das Modell steht in `schutz.rs`.
//!
//! Die Regeln liegen in einer eigenen Datei, an die `konfig_aendern` nicht
//! herankommt. Fehlt sie, gilt kein Schutz (frische Installation). Ist sie da,
//! aber nicht zu lesen, gilt alles gesperrt: sonst wäre eine kaputt gemachte
//! Datei ein schnellerer Weg als jeder Antrag.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

use super::kategorien;
use super::netzzeit;
use super::schutz::{lockert, Regeln, Schutz, FENSTER};
use super::sperre::{self, Sperre};

const DATEI: &str = "msb_schutz.json";
const EREIGNIS: &str = "msb:schutz";

/// Die Datei war beim Start nicht zu lesen; die Oberfläche sagt es.
static BESCHAEDIGT: AtomicBool = AtomicBool::new(false);

pub struct SchutzZustand(pub Mutex<Schutz>);

fn jetzt() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

fn pfad(app: &AppHandle) -> Option<PathBuf> {
    let ordner = app.path().app_config_dir().ok()?;
    std::fs::create_dir_all(&ordner).ok()?;
    Some(ordner.join(DATEI))
}

/// `None`, wenn es keine Datei gibt; ein strenger Schutz, wenn sie nicht zu lesen ist.
pub fn lesen(text: Option<&str>) -> (Schutz, bool) {
    let Some(text) = text else { return (Schutz::default(), false) };
    let gelesen = serde_json::from_str::<Schutz>(text)
        .ok()
        .and_then(|s| Some(Schutz { regeln: s.regeln.clone().pruefen().ok()?, ..s }));
    match gelesen {
        Some(s) => (s, false),
        None => (Schutz { regeln: Regeln::streng(), ..Schutz::default() }, true),
    }
}

fn laden(app: &AppHandle) -> Schutz {
    let text = pfad(app).and_then(|p| match std::fs::read(p) {
        Ok(bytes) => Some(String::from_utf8_lossy(&bytes).into_owned()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        // Da, aber nicht lesbar: wie kaputt.
        Err(_) => Some(String::new()),
    });
    let (schutz, beschaedigt) = lesen(text.as_deref());
    BESCHAEDIGT.store(beschaedigt, Ordering::Relaxed);
    schutz
}

fn speichern(app: &AppHandle, schutz: &Schutz) -> Result<(), String> {
    let ziel = pfad(app).ok_or("Konfigurationsordner unbekannt")?;
    let json = serde_json::to_string_pretty(schutz).map_err(|e| e.to_string())?;
    crate::datei::ersetzen(&ziel, json).map_err(|e| format!("Schutz nicht gespeichert: {e}"))
}

fn sperre_bauen(app: &AppHandle, r: &Regeln) -> Sperre {
    if !r.aktiv {
        return Sperre::default();
    }
    Sperre {
        listen: r.kategorien.iter().map(|&k| (k, Arc::new(kategorien::laden(app, k)))).collect(),
        eigene: r.eigene.iter().cloned().collect(),
        ausnahmen: r.ausnahmen.iter().cloned().collect(),
        sichere_suche: r.sichere_suche(),
    }
}

/// Die Sperre steht nach dem Start; bis dahin wartet `tab_laden` ([`bereit`]).
static BEREIT: AtomicBool = AtomicBool::new(false);

/// Baut die Sperre aus den Regeln, die gerade gelten. Einer zur Zeit, und die
/// Regeln werden erst unter dem Schloss gelesen: zwei Läufe nebeneinander
/// (ein Befehl, der Faden für fällige Anträge, nachgeladene Listen) setzten
/// sonst in beliebiger Reihenfolge, und ein älterer Stand konnte zuletzt gelten.
fn neu_bauen(app: &AppHandle) -> Regeln {
    static BAUEN: Mutex<()> = Mutex::new(());
    let _schloss = BAUEN.lock().unwrap();
    let regeln = app.state::<SchutzZustand>().0.lock().unwrap().regeln.clone();
    sperre::setzen(sperre_bauen(app, &regeln));
    BEREIT.store(true, Ordering::Release);
    regeln
}

/// Wartet beim Start höchstens zehn Sekunden auf die Sperre. Wiederhergestellte
/// Tabs luden sonst, bevor die Listen gelesen waren, auch gesperrte Seiten.
/// Nie auf dem UI-Faden (Regel 122): nur aus `async`-Befehlen.
pub fn bereit() {
    for _ in 0..200 {
        if BEREIT.load(Ordering::Acquire) {
            return;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// Baut die Sperre aus den geltenden Regeln und holt fehlende Listen nach.
fn anwenden(app: &AppHandle) {
    let regeln = neu_bauen(app);
    let _ = app.emit_to("main", EREIGNIS, ());
    // Erweiterungen könnten Sperre und Schild umgehen (Proxy, VPN): bei
    // aktivem Schutz laufen keine.
    #[cfg(windows)]
    crate::erweiterungen::schutz_geaendert(app);
    if !regeln.aktiv {
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        if kategorien::erneuern(&app, &regeln.kategorien).await {
            let _ = tauri::async_runtime::spawn_blocking(move || neu_bauen(&app)).await;
        }
    });
}

/// Beim Start: Datei lesen, Sperre bauen, jede Sekunde nach verfallenen Anträgen sehen.
pub fn starten(app: &AppHandle) {
    let mut schutz = laden(app);
    // Ein Schutz von vor der Serie (oder aus einer kaputten Datei) zählt ab jetzt.
    if schutz.regeln.aktiv && schutz.serie.seit == 0 {
        schutz.serie.seit = jetzt();
        let _ = speichern(app, &schutz);
    }
    app.manage(SchutzZustand(Mutex::new(schutz)));
    let app = app.clone();
    std::thread::spawn(move || {
        anwenden(&app);
        let zustand = app.state::<SchutzZustand>();
        loop {
            let geaendert = {
                let mut s = zustand.0.lock().unwrap();
                s.ablaufen(jetzt()) && speichern(&app, &s).is_ok()
            };
            if geaendert {
                anwenden(&app);
            }
            std::thread::sleep(Duration::from_secs(1));
        }
    });
}

/// Eine Seite wurde gesperrt: die Serie beginnt von vorn. Läuft auf dem
/// UI-Faden der Tabs (Regel 122), deshalb schreibt ein eigener Faden.
pub fn treffer(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let Some(zustand) = app.try_state::<SchutzZustand>() else { return };
        let mut s = zustand.0.lock().unwrap();
        if s.serie.treffer(jetzt()) {
            let _ = speichern(&app, &s);
        }
    });
}

#[derive(Serialize)]
pub struct AntragStand {
    pub ziel: Regeln,
    /// Bis sich der Antrag bestätigen lässt; 0, wenn es jetzt geht.
    pub rest_sekunden: u64,
    /// Wie lange er sich dann noch bestätigen lässt.
    pub fenster_sekunden: u64,
}

#[derive(Serialize)]
pub struct SerieStand {
    pub tage: u64,
    pub rekord: u64,
}

#[derive(Serialize)]
pub struct Stand {
    pub regeln: Regeln,
    pub antrag: Option<AntragStand>,
    pub gebunden_sekunden: u64,
    pub abkuehlen_sekunden: u64,
    pub serie: SerieStand,
    pub beschaedigt: bool,
    pub listen: Vec<kategorien::ListeStand>,
}

pub fn stand_von(s: &Schutz, lokal: u64, zeit: u64) -> Stand {
    Stand {
        regeln: s.regeln.clone(),
        antrag: s.antrag.as_ref().map(|a| AntragStand {
            ziel: a.ziel.clone(),
            rest_sekunden: a.faellig.saturating_sub(zeit),
            fenster_sekunden: (a.faellig + FENSTER).saturating_sub(zeit.max(a.faellig)),
        }),
        gebunden_sekunden: s.gebunden_bis.saturating_sub(zeit),
        abkuehlen_sekunden: s.naechster_antrag_ab.saturating_sub(zeit),
        serie: SerieStand { tage: s.serie.tage(lokal), rekord: s.serie.rekord(lokal) },
        beschaedigt: BESCHAEDIGT.load(Ordering::Relaxed),
        listen: Vec::new(),
    }
}

fn stand(app: &AppHandle, s: &Schutz) -> Stand {
    let lokal = jetzt();
    Stand { listen: kategorien::stand(app), ..stand_von(s, lokal, netzzeit::angeglichen(lokal)) }
}

/// Wendet `f` auf eine Kopie an. Was sich geändert hat, wird gespeichert und
/// gilt, auch wenn `f` danach einen Fehler meldet (ein verfallener Antrag).
fn aendern_mit(app: &AppHandle, f: impl FnOnce(&mut Schutz) -> Result<(), String>) -> Result<Stand, String> {
    let zustand = app.state::<SchutzZustand>();
    let (s, ergebnis, geaendert) = {
        let mut s = zustand.0.lock().unwrap();
        let mut neu = s.clone();
        let ergebnis = f(&mut neu);
        let geaendert = neu != *s;
        if geaendert {
            speichern(app, &neu)?;
            *s = neu.clone();
        }
        (neu, ergebnis, geaendert)
    };
    if geaendert {
        anwenden(app);
    }
    ergebnis.map(|_| stand(app, &s))
}

#[tauri::command]
pub async fn schutz_stand(app: AppHandle) -> Result<Stand, String> {
    let schutz = app.state::<SchutzZustand>().0.lock().unwrap().clone();
    Ok(stand(&app, &schutz))
}

#[tauri::command]
pub async fn schutz_aendern(app: AppHandle, regeln: Regeln) -> Result<Stand, String> {
    let regeln = regeln.pruefen()?;
    let braucht_netz = lockert(&app.state::<SchutzZustand>().0.lock().unwrap().regeln, &regeln);
    let netz = if braucht_netz { netzzeit::holen(jetzt()).await } else { None };
    aendern_mit(&app, |s| s.aendern(regeln, jetzt(), netz))
}

/// Ohne Netz zählt die Uhr des Rechners; Binden ist strenger, nie lockerer.
#[tauri::command]
pub async fn schutz_binden(app: AppHandle, tage: u32) -> Result<Stand, String> {
    let zeit = netzzeit::holen(jetzt()).await.unwrap_or_else(jetzt);
    aendern_mit(&app, |s| s.binden(tage, zeit))
}

/// Nimmt einen offenen Antrag zurück. Das lockert nichts und gilt sofort.
#[tauri::command]
pub async fn schutz_abbrechen(app: AppHandle) -> Result<Stand, String> {
    let zeit = netzzeit::holen(jetzt()).await.unwrap_or_else(jetzt);
    aendern_mit(&app, |s| {
        s.abbrechen(zeit);
        Ok(())
    })
}

#[tauri::command]
pub async fn schutz_bestaetigen(app: AppHandle) -> Result<Stand, String> {
    let netz = netzzeit::holen(jetzt()).await;
    aendern_mit(&app, |s| s.bestaetigen(netz))
}
