//! Ein Auftrag des Panels, hier ausgefuehrt.
//!
//! Die Weboberflaeche holt die Auftraege ab (sie hat das Zugangstoken) und
//! reicht jeden einzelnen hier herein. Diese Datei entscheidet **nichts**
//! ueber Erlaubnisse — sie verteilt nur:
//!
//! * Dateien → `sandbox` (dort liegt die Ordnergrenze),
//! * Programme und Adressen → `tauri-plugin-opener`,
//! * Maus/Tastatur/Bildschirm → `uebernahme` (dort liegt die Freigabe).
//!
//! Zwei Auftraege tun hier gar nichts, sondern fragen einen Menschen: die
//! Bitte um die Uebernahme und — bei ausgeschaltetem autonomem Modus — das
//! Aufraeumen. Sie gehen als Ereignis an die Oberflaeche, der Mensch
//! entscheidet, und erst seine Antwort meldet das Ergebnis. Deshalb liefern
//! sie `None` statt eines Ergebnisses.

use std::path::PathBuf;
use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};

use crate::aufraeumen;
use crate::sandbox;
use crate::system;
use crate::uebernahme;
use crate::zonen;

/// Das Ereignis, mit dem die Oberflaeche die Bestaetigungskarte zeigt.
pub const EREIGNIS_UEBERNAHME: &str = "mss:uebernahme-anfrage";
/// Dasselbe fuer das Aufraeumen — mit der vollstaendigen Liste im Gepaeck.
pub const EREIGNIS_AUFRAEUMEN: &str = "mss:aufraeumen-anfrage";

/// Was ein ausgefuehrter Auftrag zurueckgibt. `None` heisst: das Ergebnis
/// kommt spaeter (eine Karte fragt gerade), die Oberflaeche meldet dann selbst.
pub type Ergebnis = Option<Value>;

/// Ein Aufraeumauftrag, der auf einen Klick wartet.
///
/// **Er liegt hier und nicht in der Oberflaeche**, und das ist der Grund,
/// warum die Karte beim Bestaetigen keine Pfadliste mitschickt: bestaetigt
/// wird, was hier steht, nicht was ein Fenster gerade anzeigt. Ein Renderer,
/// der eine harmlose Liste zeigt und eine andere loeschen laesst, ist damit
/// gar nicht erst moeglich.
///
/// Immer hoechstens einer: die Auftragsschleife arbeitet der Reihe nach, und
/// ein zweiter Plan wuerde den ersten ueberschreiben, statt sich anzustellen.
pub struct Wartend {
    pub aktion: String,
    pub pfade: Vec<String>,
    pub system_erlaubt: bool,
}

static WARTEND: Mutex<Option<Wartend>> = Mutex::new(None);

fn wartend_setzen(plan: Option<Wartend>) -> Option<Wartend> {
    let mut stand = WARTEND
        .lock()
        .unwrap_or_else(|vergiftet| vergiftet.into_inner());
    std::mem::replace(&mut *stand, plan)
}

/// Fuehrt den wartenden Plan aus. Ruft die Karte nach dem Klick auf "Ja".
pub fn aufraeumen_bestaetigen() -> Result<Value, String> {
    let plan = wartend_setzen(None).ok_or(
        "Es wartet gerade kein Aufraeumauftrag. Vermutlich ist er verfallen \
         oder wurde schon beantwortet.",
    )?;
    aufraeumen_ausfuehren(&plan)
}

/// Verwirft den wartenden Plan. Es wird nichts angefasst.
pub fn aufraeumen_ablehnen() {
    wartend_setzen(None);
}

fn aufraeumen_ausfuehren(plan: &Wartend) -> Result<Value, String> {
    match plan.aktion.as_str() {
        "papierkorb" => aufraeumen::papierkorb(&plan.pfade, plan.system_erlaubt),
        "endgueltig" => aufraeumen::endgueltig(&plan.pfade, plan.system_erlaubt),
        "papierkorb_leeren" => aufraeumen::papierkorb_leeren(),
        andere => Err(format!(
            "Unbekannte Aktion: '{andere}'. Moeglich sind papierkorb, \
             endgueltig, papierkorb_leeren."
        )),
    }
}

pub fn ausfuehren(
    app: &AppHandle,
    sandbox_pfad: Option<PathBuf>,
    werkzeug: &str,
    argumente: &Value,
) -> Result<Ergebnis, String> {
    match werkzeug {
        "desktop_dateien" => dateien(sandbox_pfad, argumente).map(Some),
        "desktop_launch_app" => starten(app, argumente).map(Some),
        "desktop_system" => system::ausfuehren(app, argumente).map(Some),
        "desktop_steuern" => steuern(app, argumente),
        "desktop_aufraeumen" => raeumen(app, argumente),
        andere => Err(format!("Unbekanntes Werkzeug: '{andere}'")),
    }
}

/// Maus und Tastatur — samt der Bitte um die Freigabe dafuer.
///
/// `aktion="freigabe"` war bis zum 23.08.2026 ein eigenes Werkzeug
/// (`desktop_takeover_control`); zusammengelegt, weil der Werkzeugkatalog
/// seine 64.000 Zeichen erreichte. Am Ablauf hat sich nichts geaendert: die
/// Bitte zeigt eine Karte und liefert `None`, alles andere braucht eine
/// gueltige Freigabe und liefert sofort.
fn steuern(app: &AppHandle, argumente: &Value) -> Result<Ergebnis, String> {
    if argumente["aktion"].as_str() != Some("freigabe") {
        return uebernahme::steuern(argumente).map(Some);
    }
    let minuten = argumente["minuten"]
        .as_u64()
        .unwrap_or(5)
        .clamp(1, uebernahme::MAX_MINUTEN);
    // Im autonomen Modus gibt es nichts zu bestaetigen — und deshalb auch
    // keine Karte. Die Bitte trotzdem zu stellen ist der KI nicht vorzuwerfen
    // (die Werkzeugbeschreibung verlangt sie), sie kostet hier nur eine
    // sofortige Antwort statt eines Wartens auf einen Klick, der nie kaeme.
    if argumente["autonom"].as_bool() == Some(true) {
        return Ok(Some(json!({
            "freigegeben": true,
            "hinweis": "Du hast Maus und Tastatur bereits. Fang an."
        })));
    }
    let anliegen = argumente["anliegen"].as_str().unwrap_or("").to_string();
    app.emit(
        EREIGNIS_UEBERNAHME,
        json!({ "anliegen": anliegen, "minuten": minuten }),
    )
    .map_err(|e| format!("Karte nicht anzeigbar: {e}"))?;
    Ok(None)
}

/// Aufraeumen — sofort oder erst nach einem Klick.
///
/// **Die Entscheidung darueber trifft das Panel, nicht diese Datei.** Es
/// setzt `autonom` beim Anlegen des Auftrags (`_desktop_argumente`), und die
/// Regel dahinter ist die des Betreibers: autonomer Modus an, keine
/// Bestaetigung; autonomer Modus aus, immer eine. Fehlt das Feld — ein alter
/// Panelstand, ein manipulierter Auftrag —, wird gefragt. Die vorsichtige
/// Seite ist hier die richtige.
///
/// Gleiches gilt fuer `systembereich`: das steht im Konto des Benutzers, und
/// ohne den Wert `schreiben` bleibt Windows selbst gesperrt.
fn raeumen(app: &AppHandle, argumente: &Value) -> Result<Ergebnis, String> {
    let aktion = argumente["aktion"].as_str().unwrap_or("").to_string();
    let pfade: Vec<String> = argumente["pfade"]
        .as_array()
        .map(|liste| {
            liste
                .iter()
                .filter_map(|wert| wert.as_str())
                .map(|text| text.trim().to_string())
                .filter(|text| !text.is_empty())
                .collect()
        })
        .unwrap_or_default();
    let plan = Wartend {
        aktion,
        pfade,
        system_erlaubt: argumente["systembereich"].as_str() == Some("schreiben"),
    };

    if argumente["autonom"].as_bool() == Some(true) {
        return aufraeumen_ausfuehren(&plan).map(Some);
    }

    // Die Karte zeigt, was jeder Posten kostet und wo er liegt. Das kann nur
    // dieser Rechner beantworten — das Panel kennt weder Groessen noch Zonen.
    let posten: Vec<Value> = plan
        .pfade
        .iter()
        .map(|pfad| {
            let ort = std::path::Path::new(pfad);
            // Dieselbe Rechnung wie beim Ausfuehren — sonst zeigte die
            // Karte fuer Ordner 0 B und der Mensch entschiede blind. Seit
            // dem 23.08.2026 mit Zeitgrenze: lief sie ab, ist `bytes` eine
            // Untergrenze, und die Karte sagt das mit einem "mindestens".
            // Eine geschaetzte Zahl, die sich als genau ausgibt, ist bei
            // einer Loeschentscheidung die schlechtere Sorte Ungenauigkeit.
            let (bytes, vollstaendig) = aufraeumen::groesse_gemessen(ort, aufraeumen::MESSTIEFE);
            json!({
                "pfad": pfad,
                "bytes": bytes,
                "ungefaehr": !vollstaendig,
                "zone": zonen::zone(ort).name(),
            })
        })
        .collect();
    let karte = json!({
        "aktion": plan.aktion,
        "grund": argumente["grund"].as_str().unwrap_or(""),
        "posten": posten,
    });

    wartend_setzen(Some(plan));
    if let Err(fehler) = app.emit(EREIGNIS_AUFRAEUMEN, karte) {
        // Ohne Karte gibt es keinen Klick — und ein Plan, der dann liegen
        // bliebe, wuerde beim naechsten Bestaetigen eines *anderen* Auftrags
        // mit ausgefuehrt. Also sofort wieder wegraeumen.
        wartend_setzen(None);
        return Err(format!("Karte nicht anzeigbar: {fehler}"));
    }
    Ok(None)
}

fn dateien(sandbox_pfad: Option<PathBuf>, argumente: &Value) -> Result<Value, String> {
    let wurzel = sandbox_pfad.ok_or(
        "Kein Sandbox-Ordner eingerichtet. Der Benutzer legt ihn in den \
         Einstellungen der App fest; ohne ihn gibt es keinen Ort, an dem \
         gearbeitet werden darf.",
    )?;
    let aktion = argumente["aktion"].as_str().unwrap_or("");
    // Nachsichtig lesen, streng arbeiten: ein fehlender Pfad ist der Ordner
    // selbst, ein Formfehler kostet eine Runde und nie die Antwort.
    let pfad = argumente["pfad"].as_str().unwrap_or("").trim();

    match aktion {
        "auflisten" => sandbox::auflisten(&wurzel, pfad),
        "lesen" => sandbox::lesen(&wurzel, pfad),
        "schreiben" => {
            let inhalt = argumente["inhalt"]
                .as_str()
                .ok_or("Zum Schreiben fehlt 'inhalt'")?;
            sandbox::schreiben(&wurzel, pfad, inhalt)
        }
        "loeschen" => sandbox::loeschen(&wurzel, pfad),
        "verschieben" => {
            let ziel = argumente["ziel"]
                .as_str()
                .ok_or("Zum Verschieben fehlt 'ziel'")?;
            sandbox::verschieben(&wurzel, pfad, ziel)
        }
        andere => Err(format!(
            "Unbekannte Aktion: '{andere}'. Moeglich sind auflisten, lesen, \
             schreiben, loeschen, verschieben."
        )),
    }
}

fn starten(app: &AppHandle, argumente: &Value) -> Result<Value, String> {
    use tauri_plugin_opener::OpenerExt;

    if let Some(url) = argumente["url"].as_str().map(str::trim).filter(|u| !u.is_empty()) {
        // Nur Web-Adressen. `file://` waere ein Weg, ueber den Umweg einer
        // Adresse eine beliebige Datei zu starten — genau der Fall, den die
        // Sandbox verhindern soll.
        if !(url.starts_with("https://") || url.starts_with("http://")) {
            return Err(format!(
                "Nur http- und https-Adressen: '{url}' wurde nicht geoeffnet."
            ));
        }
        app.opener()
            .open_url(url, None::<&str>)
            .map_err(|e| format!("Adresse nicht zu oeffnen: {e}"))?;
        return Ok(json!({ "geoeffnet": url }));
    }

    let programm = argumente["programm"]
        .as_str()
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .ok_or("Weder 'programm' noch 'url' angegeben")?;
    if verboten(programm) {
        return Err(format!(
            "'{programm}' ist ein Systemwerkzeug von Windows und wird nicht \
             gestartet. Systemverwaltung gehoert nicht zu dem, was hier \
             passiert."
        ));
    }
    app.opener()
        .open_url(programm, None::<&str>)
        .map_err(|e| format!("Programm nicht startbar: {e}"))?;
    Ok(json!({ "gestartet": programm }))
}

/// Systemwerkzeuge, die auch mit den Rechten des Benutzers nichts zu suchen
/// haben. Keine Sicherheitsschranke — wer den Rechner bedienen darf, kommt an
/// alles heran, was der Benutzer selbst kann. Aber eine klare Ansage, was
/// dieses Produkt nicht tut, und ein Schutz gegen das Missverstaendnis, die
/// Registry sei ein Werkzeug wie jedes andere.
fn verboten(programm: &str) -> bool {
    let klein = programm.to_ascii_lowercase();
    let name = klein
        .rsplit(['\\', '/'])
        .next()
        .unwrap_or(&klein)
        .trim_end_matches(".exe");
    matches!(
        name,
        "regedit"
            | "regedt32"
            | "cmd"
            | "powershell"
            | "pwsh"
            | "wt"
            | "diskpart"
            | "bcdedit"
            | "gpedit"
            | "secpol"
            | "services"
            | "taskmgr"
            | "mmc"
            | "sc"
            | "reg"
    ) || klein.contains("\\windows\\system32")
        || klein.contains("/windows/system32")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn systemwerkzeuge_sind_ausgeschlossen() {
        for name in [
            "regedit",
            "RegEdit.exe",
            "cmd",
            "powershell.exe",
            "C:\\Windows\\System32\\taskmgr.exe",
            "c:/windows/system32/diskpart.exe",
        ] {
            assert!(verboten(name), "{name} muesste gesperrt sein");
        }
    }

    #[test]
    fn gewoehnliche_programme_bleiben_erlaubt() {
        for name in ["discord", "Steam", "code", "spotify.exe"] {
            assert!(!verboten(name), "{name} muesste erlaubt sein");
        }
    }
}
