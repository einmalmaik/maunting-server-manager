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
use std::time::{Duration, Instant};

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
    /// Bis wann der Klick zaehlt — siehe [`BESTAETIGUNGSFRIST`].
    pub bis: Instant,
}

/// Wie lange ein wartender Plan gueltig bleibt.
///
/// Dieselben zehn Minuten, die das Panel dem Auftrag gibt
/// (`FRIST_BESTAETIGUNG_SEKUNDEN`). Danach ist der Auftrag dort verfallen und
/// der Lauf laengst mit `DESKTOP_JOB_EXPIRED` geweckt — er *weiss* also, dass
/// nichts geschehen ist. Ein Klick, der die Dateien dann doch noch loescht,
/// erzeugt genau die Lage, die es nie geben darf: die Dateien sind weg, und
/// niemand erfaehrt davon. Ohne diese Frist hielt der Plan unbegrenzt, und
/// die Fehlermeldung unten behauptete einen Verfall, den es gar nicht gab.
const BESTAETIGUNGSFRIST: Duration = Duration::from_secs(600);

/// Wie lange die Karte insgesamt messen darf, bevor sie gezeigt wird.
///
/// `aufraeumen::MESSFRIST` gilt je Pfad (5 s). Bei den erlaubten 500 Pfaden
/// waeren das im schlimmsten Fall vierzig Minuten auf einem langsamen Netz-
/// oder OneDrive-Pfad — der Auftrag ist nach zehn Minuten verfallen, und der
/// Mensch haette die Karte nie gesehen. Zwanzig Sekunden sind genug fuer
/// jede normale Liste; was danach kommt, steht ohne Zahl auf der Karte.
const KARTENBUDGET: Duration = Duration::from_secs(20);

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
    if Instant::now() > plan.bis {
        return Err(format!(
            "Der Aufraeumauftrag ist verfallen: zwischen der Frage und dem \
             Klick lagen mehr als {} Minuten. Es wurde nichts angefasst. \
             Frag erneut, wenn es noch aktuell ist.",
            BESTAETIGUNGSFRIST.as_secs() / 60
        ));
    }
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

/// `auftrag_id` ist die Kennung des Auftrags, aus dem dieser Aufruf stammt.
/// Sie wird nur gebraucht, wo eine Karte gezeigt wird: die Antwort des
/// Menschen gehoert zu **diesem** Auftrag, und die Karte soll das aus dem
/// Ereignis lesen koennen statt aus einem Zustand der Oberflaeche, der
/// vielleicht noch nicht angekommen ist.
pub fn ausfuehren(
    app: &AppHandle,
    sandbox_pfad: Option<PathBuf>,
    werkzeug: &str,
    argumente: &Value,
    auftrag_id: Option<&str>,
) -> Result<Ergebnis, String> {
    match werkzeug {
        "desktop_dateien" => dateien(sandbox_pfad, argumente).map(Some),
        "desktop_launch_app" => starten(app, argumente).map(Some),
        "desktop_system" => system::ausfuehren(app, argumente).map(Some),
        "desktop_steuern" => steuern(app, argumente, auftrag_id),
        "desktop_aufraeumen" => raeumen(app, argumente, auftrag_id),
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
fn steuern(
    app: &AppHandle,
    argumente: &Value,
    auftrag_id: Option<&str>,
) -> Result<Ergebnis, String> {
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
        json!({ "anliegen": anliegen, "minuten": minuten, "auftrag_id": auftrag_id }),
    )
    .map_err(|e| format!("Karte nicht anzeigbar: {e}"))?;
    Ok(None)
}

/// Die Pfadliste aus der Nutzlast — und die billige Pruefung davor.
///
/// `aufraeumen::ausfuehren` kennt dieselbe Obergrenze, kommt aber erst nach
/// der Karte an die Reihe. Eine Liste mit tausend Pfaden wurde deshalb erst
/// Stueck fuer Stueck vermessen (bis zu 5 s je Pfad) und danach als "zu
/// viele" abgewiesen — bis dahin war der Auftrag verfallen und die Karte nie
/// zu sehen. Die Zaehlung gehoert vor die Messung.
fn pfadliste(argumente: &Value) -> Result<Vec<String>, String> {
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
    if pfade.len() > aufraeumen::MAX_PFADE {
        return Err(format!(
            "{} Pfade sind zu viele; hoechstens {} auf einmal. Teil es auf \
             und fang mit den groessten an.",
            pfade.len(),
            aufraeumen::MAX_PFADE
        ));
    }
    Ok(pfade)
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
fn raeumen(
    app: &AppHandle,
    argumente: &Value,
    auftrag_id: Option<&str>,
) -> Result<Ergebnis, String> {
    let aktion = argumente["aktion"].as_str().unwrap_or("").to_string();
    let pfade = pfadliste(argumente)?;
    let plan = Wartend {
        aktion,
        pfade,
        system_erlaubt: argumente["systembereich"].as_str() == Some("schreiben"),
        bis: Instant::now() + BESTAETIGUNGSFRIST,
    };

    if argumente["autonom"].as_bool() == Some(true) {
        return aufraeumen_ausfuehren(&plan).map(Some);
    }

    // Die Karte zeigt, was jeder Posten kostet und wo er liegt. Das kann nur
    // dieser Rechner beantworten — das Panel kennt weder Groessen noch Zonen.
    let messung_bis = Instant::now() + KARTENBUDGET;
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
            //
            // Die Frist gilt je Pfad; ueber alle zusammen wacht
            // `KARTENBUDGET`. Danach steht bei den restlichen Posten keine
            // Zahl mehr (`bytes: null`) — die Karte kommt lieber
            // unvollstaendig als gar nicht.
            let (bytes, vollstaendig) = if Instant::now() < messung_bis {
                let (bytes, vollstaendig) =
                    aufraeumen::groesse_gemessen(ort, aufraeumen::MESSTIEFE);
                (Some(bytes), vollstaendig)
            } else {
                (None, true)
            };
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
        "auftrag_id": auftrag_id,
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

    #[test]
    fn ein_verfallener_plan_loescht_nichts() {
        // Der Plan lag unbegrenzt: ein Klick eine Stunde spaeter loeschte
        // wirklich, und das Ergebnis ging an einen Auftrag, den das Panel
        // laengst verworfen hatte. Der Lauf war da schon mit
        // DESKTOP_JOB_EXPIRED geweckt — er "wusste" also, dass nichts
        // passiert ist, waehrend die Dateien weg waren.
        let ziel = std::env::temp_dir().join(format!(
            "mss-verfallener-plan-{}.txt",
            std::process::id()
        ));
        std::fs::write(&ziel, b"bleibt").unwrap();
        wartend_setzen(Some(Wartend {
            aktion: "endgueltig".into(),
            pfade: vec![ziel.display().to_string()],
            system_erlaubt: false,
            bis: Instant::now() - Duration::from_secs(1),
        }));

        let fehler = aufraeumen_bestaetigen().unwrap_err();
        assert!(fehler.contains("verfallen"), "{fehler}");
        assert!(ziel.exists(), "Der verfallene Plan hat trotzdem geloescht");
        // Und er liegt nicht mehr herum: der naechste Klick faende ihn sonst
        // wieder vor.
        assert!(wartend_setzen(None).is_none());
        let _ = std::fs::remove_file(&ziel);
    }

    #[test]
    fn zu_viele_pfade_werden_vor_dem_messen_abgewiesen() {
        // Die Obergrenze stand nur in `aufraeumen::ausfuehren` und damit
        // hinter der Messung: eine zu lange Liste wurde erst Pfad fuer Pfad
        // vermessen (bis zu 5 s je Stueck) und danach abgewiesen.
        let viele: Vec<Value> = (0..aufraeumen::MAX_PFADE + 1)
            .map(|n| json!(format!("C:\\x\\{n}")))
            .collect();
        let fehler = pfadliste(&json!({ "pfade": viele })).unwrap_err();
        assert!(fehler.contains("zu viele"), "{fehler}");

        // Und was durchgeht, kommt getrimmt und ohne Leereintraege an.
        let liste = pfadliste(&json!({ "pfade": ["  C:\\a  ", "", "C:\\b"] })).unwrap();
        assert_eq!(liste, vec!["C:\\a".to_string(), "C:\\b".to_string()]);
    }
}
