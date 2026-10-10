//! Installieren, schalten, entfernen. Die Liste liegt in
//! `app_local_data/erweiterungen/liste.json`, jede Erweiterung in einem
//! eigenen Ordner daneben. WebView2 lädt sie von dort und braucht den Ordner,
//! solange sie installiert ist.
//!
//! Ein Schloss (`aendern`) hält Änderungen nacheinander. Die Liste selbst ist
//! nur kurz gesperrt und nie, während auf den UI-Faden gewartet wird: der
//! liest sie in `tabs::desktop::anlegen`.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};

use super::liste::{ordner_ok, Eintrag, Herkunft, Liste, Soll};
use super::{crx, laden, paket};

/// Die Oberfläche lädt Liste und Leiste neu.
pub const EREIGNIS: &str = "msb:erweiterungen";

struct Vorgang {
    /// Unbekannt bei entpackten Ordnern, bis WebView2 sie lädt.
    id: Option<String>,
    ordner: PathBuf,
    herkunft: Herkunft,
    quelle: Option<String>,
    angaben: paket::Angaben,
}

#[derive(Default)]
pub struct Zustand {
    liste: Mutex<Liste>,
    vorgaenge: Mutex<HashMap<String, Vorgang>>,
    aendern: Mutex<()>,
}

fn wurzel(app: &AppHandle) -> Result<PathBuf, String> {
    app.path().app_local_data_dir().map(|d| d.join("erweiterungen")).map_err(|e| e.to_string())
}

fn zufall(bytes: usize) -> String {
    use ring::rand::SecureRandom;
    let mut b = vec![0u8; bytes];
    ring::rand::SystemRandom::new().fill(&mut b).expect("Zufall");
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Die App-Sprache für `__MSG_…__`; sie wird zum Ordnernamen unter `_locales`.
fn sprache(text: &str) -> String {
    let gut = (2..=5).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_alphabetic() || b == b'_');
    if gut { text.into() } else { "en".into() }
}

/// Ohne gelesenen Jugendschutz gilt er als aktiv.
fn schutz_aktiv(app: &AppHandle) -> bool {
    app.try_state::<crate::schild::schutz_dienst::SchutzZustand>().is_none_or(|z| z.0.lock().unwrap().regeln.aktiv)
}

fn liste(app: &AppHandle) -> Liste {
    app.try_state::<Zustand>().map(|z| z.liste.lock().unwrap().clone()).unwrap_or_default()
}

/// Was in WebView2 stehen soll. Läuft auch auf dem UI-Faden.
pub fn soll(app: &AppHandle) -> Vec<Soll> {
    match wurzel(app) {
        Ok(w) => liste(app).soll(&w, schutz_aktiv(app)),
        Err(_) => Vec::new(),
    }
}

fn speichern(app: &AppHandle, neu: Liste) -> Result<(), String> {
    let w = wurzel(app)?;
    fs::create_dir_all(&w).map_err(|e| e.to_string())?;
    crate::datei::ersetzen(&w.join("liste.json"), serde_json::to_vec_pretty(&neu).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    *app.state::<Zustand>().liste.lock().unwrap() = neu;
    Ok(())
}

fn abgleichen(app: &AppHandle, nur_offen: bool) -> Result<(), String> {
    let soll = soll(app);
    let erwartet: Vec<String> = soll.iter().filter(|s| s.an).map(|s| s.id.clone()).collect();
    // Schon vor dem Abgleich: was nicht laufen soll, hat ab jetzt keine Seiten.
    crate::tabs::erweiterungen_setzen(erwartet.clone());
    let ergebnis = crate::tabs::erweiterungen_abgleichen(app, soll, nur_offen);
    if let Ok(Some(laufen)) = &ergebnis {
        crate::tabs::erweiterungen_setzen(laufen.iter().filter(|id| erwartet.contains(id)).cloned().collect());
    }
    let _ = app.emit_to("main", EREIGNIS, ());
    ergebnis.map(|_| ())
}

/// Beim Start: Liste lesen und alles löschen, was keiner Erweiterung gehört
/// (abgebrochene Installationen, Ordner entfernter Erweiterungen).
pub fn beim_start(app: &AppHandle) {
    let mut liste = Liste::default();
    if let Ok(w) = wurzel(app) {
        if let Ok(text) = fs::read_to_string(w.join("liste.json")) {
            liste = Liste::lesen(&text);
        }
        if let Ok(eintraege) = fs::read_dir(&w) {
            for e in eintraege.flatten() {
                let name = e.file_name().to_string_lossy().into_owned();
                if e.path().is_dir() && !liste.eintraege.iter().any(|x| x.ordner == name) {
                    let _ = fs::remove_dir_all(e.path());
                }
            }
        }
    }
    app.manage(Zustand { liste: Mutex::new(liste), ..Default::default() });
}

/// Der Jugendschutz hat sich geändert: alle Erweiterungen aus oder wieder an.
/// Ohne offenen Tab holt `anlegen` es nach.
pub fn schutz_geaendert(app: &AppHandle) {
    if liste(app).eintraege.is_empty() {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        if let Err(e) = abgleichen(&app, true) {
            eprintln!("[MSB] Erweiterungen nach dem Jugendschutz nicht abgeglichen: {e}");
        }
    });
}

pub fn stand(app: &AppHandle) -> Value {
    let liste = liste(app);
    let schutz = schutz_aktiv(app);
    let eintraege: Vec<Value> = liste
        .eintraege
        .iter()
        .map(|e| {
            json!({
                "id": e.id, "herkunft": e.herkunft, "an": e.an, "laeuft": liste.laeuft(e, schutz),
                "angeheftet": e.angeheftet, "angaben": e.angaben,
            })
        })
        .collect();
    json!({ "entwicklermodus": liste.entwicklermodus, "jugendschutz": schutz, "eintraege": eintraege })
}

fn gesperrt(app: &AppHandle) -> Result<(), String> {
    if schutz_aktiv(app) {
        return Err("jugendschutz".into());
    }
    Ok(())
}

fn vorschau(nr: &str, v: &Vorgang) -> Value {
    json!({ "vorgang": nr, "id": v.id, "herkunft": v.herkunft, "angaben": v.angaben })
}

/// Lädt und prüft ein Paket aus dem Store (oder kopiert einen Ordner im
/// Entwicklermodus) und legt es beiseite. Installiert wird erst nach der
/// Rückfrage mit den Rechten (`installieren`).
pub async fn pruefen(app: AppHandle, quelle: String, entpackt: bool, sprache: String) -> Result<Value, String> {
    gesperrt(&app)?;
    let w = wurzel(&app)?;
    fs::create_dir_all(&w).map_err(|e| e.to_string())?;
    let ziel = w.join(format!("neu-{}", zufall(8)));
    let sprache = self::sprache(&sprache);
    let vorgang = if entpackt {
        if !liste(&app).entwicklermodus {
            return Err("kein_entwicklermodus".into());
        }
        let quelle_pfad = PathBuf::from(&quelle);
        if !quelle_pfad.is_absolute() || !quelle_pfad.is_dir() {
            return Err("ordner_fehlt".into());
        }
        let z = ziel.clone();
        let angaben = tauri::async_runtime::spawn_blocking(move || {
            paket::kopieren(&quelle_pfad, &z)?;
            paket::angaben(&z, &sprache)
        })
        .await
        .map_err(|e| e.to_string())?;
        angaben.map(|angaben| Vorgang { id: None, ordner: ziel.clone(), herkunft: Herkunft::Entpackt, quelle: Some(quelle), angaben })
    } else {
        let id = laden::id_aus_eingabe(&quelle).ok_or("keine_id")?;
        let crx_bytes = laden::holen(&id).await?;
        let (z, id2) = (ziel.clone(), id.clone());
        let angaben = tauri::async_runtime::spawn_blocking(move || {
            let p = crx::pruefen(&crx_bytes, &id2, &crx::STORE_SCHLUESSEL).map_err(|f| f.code().to_string())?;
            paket::entpacken(p.zip, &z)?;
            paket::schluessel_setzen(&z, &p.schluessel)?;
            paket::angaben(&z, &sprache)
        })
        .await
        .map_err(|e| e.to_string())?;
        angaben.map(|angaben| Vorgang { id: Some(id), ordner: ziel.clone(), herkunft: Herkunft::Store, quelle: None, angaben })
    };
    let vorgang = match vorgang {
        Ok(v) => v,
        Err(e) => {
            let _ = fs::remove_dir_all(&ziel);
            return Err(e);
        }
    };
    let nr = zufall(8);
    let antwort = vorschau(&nr, &vorgang);
    app.state::<Zustand>().vorgaenge.lock().unwrap().insert(nr, vorgang);
    Ok(antwort)
}

pub fn verwerfen(app: &AppHandle, vorgang: &str) {
    if let Some(v) = app.state::<Zustand>().vorgaenge.lock().unwrap().remove(vorgang) {
        let _ = fs::remove_dir_all(&v.ordner);
    }
}

/// Nimmt eine Erweiterung aus Liste und WebView2 und löscht ihren Ordner.
fn herausnehmen(app: &AppHandle, w: &Path, id: &str) -> Result<(), String> {
    let mut neu = liste(app);
    let Some(pos) = neu.eintraege.iter().position(|e| e.id == id) else { return Ok(()) };
    let alt = neu.eintraege.remove(pos);
    speichern(app, neu)?;
    abgleichen(app, false)?;
    // Bleibt er liegen (noch in Gebrauch), räumt ihn der nächste Start ab.
    let _ = fs::remove_dir_all(w.join(&alt.ordner));
    Ok(())
}

pub fn installieren(app: &AppHandle, vorgang: &str) -> Result<Value, String> {
    let zustand = app.state::<Zustand>();
    let _schloss = zustand.aendern.lock().unwrap();
    let v = zustand.vorgaenge.lock().unwrap().remove(vorgang).ok_or("vorgang_fehlt")?;
    let ergebnis = (|| {
        gesperrt(app)?;
        let w = wurzel(app)?;
        let ordner = match v.herkunft {
            Herkunft::Store => format!("store-{}-{}", v.id.as_deref().unwrap_or_default(), zufall(4)),
            Herkunft::Entpackt => format!("entpackt-{}", zufall(8)),
        };
        debug_assert!(ordner_ok(&ordner));
        fs::rename(&v.ordner, w.join(&ordner)).map_err(|e| e.to_string())?;
        let id = match &v.id {
            Some(id) => id.clone(),
            None => crate::tabs::erweiterung_laden(app, w.join(&ordner))?,
        };
        // Eine neue Fassung ersetzt die alte ganz: WebView2 lädt eine
        // Kennung, die es schon kennt, nicht aus einem neuen Ordner.
        if v.herkunft == Herkunft::Store {
            herausnehmen(app, &w, &id)?;
        }
        let mut neu = liste(app);
        neu.eintraege.push(Eintrag { id, ordner, herkunft: v.herkunft, quelle: v.quelle.clone(), an: true, angeheftet: false, angaben: v.angaben.clone() });
        speichern(app, neu)?;
        abgleichen(app, false)
    })();
    if ergebnis.is_err() {
        let _ = fs::remove_dir_all(&v.ordner);
    }
    ergebnis.map(|()| stand(app))
}

fn aendern(app: &AppHandle, f: impl FnOnce(&mut Liste) -> Result<(), String>) -> Result<Value, String> {
    let zustand = app.state::<Zustand>();
    let _schloss = zustand.aendern.lock().unwrap();
    let mut neu = liste(app);
    f(&mut neu)?;
    speichern(app, neu)?;
    abgleichen(app, false)?;
    Ok(stand(app))
}

pub fn schalten(app: &AppHandle, id: &str, an: bool) -> Result<Value, String> {
    if an {
        gesperrt(app)?;
    }
    aendern(app, |l| {
        l.eintrag(id).ok_or("unbekannt")?.an = an;
        Ok(())
    })
}

pub fn anheften(app: &AppHandle, id: &str, an: bool) -> Result<Value, String> {
    let zustand = app.state::<Zustand>();
    let _schloss = zustand.aendern.lock().unwrap();
    let mut neu = liste(app);
    neu.eintrag(id).ok_or("unbekannt")?.angeheftet = an;
    speichern(app, neu)?;
    let _ = app.emit_to("main", EREIGNIS, ());
    Ok(stand(app))
}

pub fn entwicklermodus(app: &AppHandle, an: bool) -> Result<Value, String> {
    if an {
        gesperrt(app)?;
    }
    aendern(app, |l| {
        l.entwicklermodus = an;
        Ok(())
    })
}

pub fn entfernen(app: &AppHandle, id: &str) -> Result<Value, String> {
    let zustand = app.state::<Zustand>();
    let _schloss = zustand.aendern.lock().unwrap();
    herausnehmen(app, &wurzel(app)?, id)?;
    Ok(stand(app))
}

/// Adresse einer Seite der Erweiterung (Popup, Optionen), nur wenn sie läuft.
pub fn seite(app: &AppHandle, id: &str, popup: bool) -> Result<String, String> {
    let liste = liste(app);
    let e = liste.eintraege.iter().find(|e| e.id == id).ok_or("unbekannt")?;
    if !liste.laeuft(e, schutz_aktiv(app)) {
        return Err("aus".into());
    }
    let pfad = if popup { &e.angaben.popup } else { &e.angaben.optionen };
    Ok(format!("chrome-extension://{id}/{}", pfad.as_deref().ok_or("keine_seite")?))
}
