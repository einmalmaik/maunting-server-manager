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
use crate::konfig;
use crate::sandbox;
use crate::system;
use crate::uebernahme;
use crate::zonen;

/// Das Ereignis, mit dem die Oberflaeche die Bestaetigungskarte fuer Maus/Tastatur zeigt.
pub const EREIGNIS_UEBERNAHME: &str = "mss:uebernahme-anfrage";
/// Dasselbe fuer das Aufraeumen — mit der vollstaendigen Liste im Gepaeck.
pub const EREIGNIS_AUFRAEUMEN: &str = "mss:aufraeumen-anfrage";
/// Das Ereignis fuer allgemeine Werkzeugaktionen bei inaktivem autonomem Modus.
pub const EREIGNIS_AKTION: &str = "mss:aktion-anfrage";

/// Was ein ausgefuehrter Auftrag zurueckgibt. `None` heisst: das Ergebnis
/// kommt spaeter (eine Karte fragt gerade), die Oberflaeche meldet dann selbst.
pub type Ergebnis = Option<Value>;

/// Ein Aufraeumauftrag, der auf einen Klick wartet.
pub struct Wartend {
    pub aktion: String,
    pub pfade: Vec<String>,
    pub system_erlaubt: bool,
    /// Bis wann der Klick zaehlt — siehe [`BESTAETIGUNGSFRIST`].
    pub bis: Instant,
}

/// Eine allgemeine Desktop-Aktion, die auf eine Benutzerbestätigung wartet.
pub struct WartendeAktion {
    pub auftrag_id: String,
    pub werkzeug: String,
    pub argumente: Value,
    pub sandbox_pfad: Option<PathBuf>,
    pub bis: Instant,
}

/// Wie lange ein wartender Plan gueltig bleibt (10 Minuten).
const BESTAETIGUNGSFRIST: Duration = Duration::from_secs(600);

/// Wie lange die Karte insgesamt messen darf, bevor sie gezeigt wird.
const KARTENBUDGET: Duration = Duration::from_secs(20);

static WARTEND: Mutex<Option<Wartend>> = Mutex::new(None);
static WARTENDE_AKTION: Mutex<Option<WartendeAktion>> = Mutex::new(None);

fn wartend_setzen(plan: Option<Wartend>) -> Option<Wartend> {
    let mut stand = WARTEND
        .lock()
        .unwrap_or_else(|vergiftet| vergiftet.into_inner());
    std::mem::replace(&mut *stand, plan)
}

fn wartende_aktion_setzen(aktion: Option<WartendeAktion>) -> Option<WartendeAktion> {
    let mut stand = WARTENDE_AKTION
        .lock()
        .unwrap_or_else(|vergiftet| vergiftet.into_inner());
    std::mem::replace(&mut *stand, aktion)
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

/// Bestätigt eine wartende allgemeine Desktop-Aktion und führt sie aus.
pub fn desktop_aktion_bestaetigen(app: &AppHandle, auftrag_id: &str) -> Result<Value, String> {
    let mut stand = WARTENDE_AKTION
        .lock()
        .unwrap_or_else(|vergiftet| vergiftet.into_inner());
    let wartend = stand.take().ok_or(
        "Es wartet gerade keine Aktion zur Bestätigung. Vermutlich ist sie verfallen \
         oder wurde bereits beantwortet.",
    )?;
    if wartend.auftrag_id != auftrag_id {
        return Err("Die Auftrags-ID stimmt nicht mit der wartenden Aktion überein.".into());
    }
    if Instant::now() > wartend.bis {
        return Err("Die Bestätigungsfrist ist abgelaufen. Es wurde nichts ausgeführt.".into());
    }
    match wartend.werkzeug.as_str() {
        "desktop_dateien" => dateien(wartend.sandbox_pfad, &wartend.argumente),
        "desktop_launch_app" => starten(app, &wartend.argumente),
        "desktop_system" => system::ausfuehren(app, &wartend.argumente),
        "desktop_steuern" => uebernahme::steuern(&wartend.argumente),
        "desktop_artifact" => crate::artefakt::ausfuehren(app, &wartend.argumente),
        andere => Err(format!("Unbekanntes Werkzeug: '{andere}'")),
    }
}

/// Lehnt eine wartende allgemeine Desktop-Aktion ab.
pub fn desktop_aktion_ablehnen(auftrag_id: &str) -> Result<(), String> {
    let mut stand = WARTENDE_AKTION
        .lock()
        .unwrap_or_else(|vergiftet| vergiftet.into_inner());
    if let Some(ref wartend) = *stand {
        if wartend.auftrag_id == auftrag_id {
            stand.take();
        }
    }
    Ok(())
}

fn aktion_beschreibung(werkzeug: &str, argumente: &Value) -> (String, String) {
    match werkzeug {
        "desktop_system" => {
            let aktion = argumente["aktion"].as_str().unwrap_or("");
            let pfad = argumente["pfad"].as_str().unwrap_or("");
            match aktion {
                "bildschirm" => (
                    "Bildschirmaufnahme (Screenshot)".to_string(),
                    "Die KI möchte ein Bild deines Hauptbildschirms aufnehmen, um zu analysieren, was gerade angezeigt wird.".to_string(),
                ),
                "laufwerke" => (
                    "Laufwerke einsehen".to_string(),
                    "Die KI möchte die Liste deiner Laufwerke und Speicherkapazitäten einsehen.".to_string(),
                ),
                "verzeichnis" => (
                    "Ordnerinhalt ansehen".to_string(),
                    format!("Die KI möchte den Ordner '{pfad}' auflisten."),
                ),
                "groesste" => (
                    "Speicherplatz analysieren".to_string(),
                    format!("Die KI möchte nach großen Dateien unter '{pfad}' suchen."),
                ),
                "virenscan" => (
                    "Virenprüfung starten".to_string(),
                    format!("Die KI möchte den Pfad '{pfad}' mit Windows Defender auf Schädlinge prüfen."),
                ),
                _ => (
                    "Systemdaten lesen".to_string(),
                    "Die KI möchte Systeminformationen deines Rechners abfragen.".to_string(),
                ),
            }
        }
        "desktop_dateien" => {
            let aktion = argumente["aktion"].as_str().unwrap_or("");
            let pfad = argumente["pfad"].as_str().unwrap_or("");
            let ziel = argumente["ziel"].as_str().unwrap_or("");
            match aktion {
                "lesen" => (
                    "Datei lesen".to_string(),
                    format!("Die KI möchte die Datei '{pfad}' im Sandbox-Ordner lesen."),
                ),
                "schreiben" => (
                    "Datei schreiben".to_string(),
                    format!("Die KI möchte in die Datei '{pfad}' im Sandbox-Ordner schreiben."),
                ),
                "auflisten" => (
                    "Sandbox auflisten".to_string(),
                    format!("Die KI möchte Dateien unter '{pfad}' in der Sandbox auflisten."),
                ),
                "loeschen" => (
                    "Datei löschen".to_string(),
                    format!("Die KI möchte die Datei '{pfad}' in der Sandbox löschen."),
                ),
                "verschieben" => (
                    "Datei verschieben".to_string(),
                    format!("Die KI möchte '{pfad}' nach '{ziel}' in der Sandbox verschieben."),
                ),
                _ => (
                    "Dateioperation".to_string(),
                    format!("Die KI möchte eine Dateioperation ('{aktion}') in der Sandbox ausführen."),
                ),
            }
        }
        "desktop_launch_app" => {
            if let Some(url) = argumente["url"].as_str() {
                (
                    "Webadresse im Browser öffnen".to_string(),
                    format!("Die KI möchte die Webadresse '{url}' in deinem Standardbrowser öffnen."),
                )
            } else if let Some(programm) = argumente["programm"].as_str() {
                (
                    "Programm starten".to_string(),
                    format!("Die KI möchte das Programm '{programm}' auf deinem Rechner starten."),
                )
            } else {
                (
                    "Programm oder Adresse öffnen".to_string(),
                    "Die KI möchte eine Anwendung oder Webadresse öffnen.".to_string(),
                )
            }
        }
        "desktop_steuern" => (
            "Maus- oder Tastatureingabe".to_string(),
            "Die KI möchte Maus- oder Tastaturaktionen auf deinem Computer ausführen.".to_string(),
        ),
        "desktop_artifact" => {
            let aktion = argumente["aktion"].as_str().unwrap_or("");
            let url = argumente["url"].as_str().unwrap_or("");
            let art_id = argumente["artifact_id"].as_str().unwrap_or("");
            match aktion {
                "download" => (
                    "Artefakt herunterladen".to_string(),
                    format!("Die KI möchte eine Datei via HTTPS in die lokale Quarantäne herunterladen: '{url}'."),
                ),
                "pruefen" | "sandbox" => (
                    "Artefakt prüfen & Sandbox starten".to_string(),
                    format!("Die KI möchte das Quarantäne-Artefakt '{art_id}' mit Microsoft Defender prüfen und in einer flüchtigen Windows Sandbox isoliert öffnen."),
                ),
                "locator" => (
                    "Software & Spiele lokalisieren".to_string(),
                    "Die KI möchte installierte Spiele und Software in freigegebenen Suchbereichen ermitteln.".to_string(),
                ),
                "deploy" => (
                    "Artefakt installieren (Snapshot-Deployment)".to_string(),
                    format!("Die KI möchte das geprüfte Artefakt '{art_id}' mit automatischem Rollback-Snapshot installieren."),
                ),
                "rollback" => (
                    "Rollback ausführen".to_string(),
                    format!("Die KI möchte den vorherigen Zustand für Artefakt '{art_id}' aus dem Snapshot wiederherstellen."),
                ),
                "installer" => (
                    "Setup-Programm ausführen".to_string(),
                    format!("Die KI möchte das Setup-Programm '{art_id}' auf deinem Rechner starten."),
                ),
                _ => (
                    "Artefakt-Aktion".to_string(),
                    format!("Die KI möchte eine Artefakt-Aktion ('{aktion}') ausführen."),
                ),
            }
        }
        _ => (
            "Aktion ausführen".to_string(),
            format!("Die KI möchte das Werkzeug '{werkzeug}' ausführen."),
        ),
    }
}

/// `auftrag_id` ist die Kennung des Auftrags, aus dem dieser Aufruf stammt.
pub fn ausfuehren(
    app: &AppHandle,
    sandbox_pfad: Option<PathBuf>,
    werkzeug: &str,
    argumente: &Value,
    auftrag_id: Option<&str>,
) -> Result<Ergebnis, String> {
    #[cfg(target_os = "android")]
    {
        let _ = (app, sandbox_pfad, werkzeug, argumente, auftrag_id);
        return Err(
            "Auf Android sind lokale System-, Datei- und Steuerungs-Werkzeuge \
             aus Sicherheitsgründen physikalisch deaktiviert."
                .into(),
        );
    }

    #[cfg(not(target_os = "android"))]
    {
    let app_konfig = konfig::laden(app).unwrap_or_default();

    // 1. Computer-Use Sicherheitsgrenze:
    // Ist Computer-Use in den Einstellungen deaktiviert, werden Steuerungen und
    // Bildschirmaufnahmen ausnahmslos blockiert.
    if !app_konfig.computer_use_aktiv
        && (werkzeug == "desktop_steuern"
            || (werkzeug == "desktop_system"
                && argumente["aktion"].as_str() == Some("bildschirm")))
    {
        return Err(
            "Computer-Use ist in den Desktop-Einstellungen deaktiviert. Der \
             Benutzer kann es in den Einstellungen aktivieren."
                .into(),
        );
    }

    // 1b. Artefakt-Installationen Sicherheitsgrenze:
    if werkzeug == "desktop_artifact" && !app_konfig.artifact_install_aktiv {
        return Err(
            "Artefakt-Installationen sind in den Desktop-Einstellungen deaktiviert. Der \
             Benutzer kann sie in den Einstellungen aktivieren."
                .into(),
        );
    }

    // 2. Spezialfälle mit eigenen Karten/Abläufen:
    if werkzeug == "desktop_steuern" && argumente["aktion"].as_str() == Some("freigabe") {
        return steuern(app, argumente, auftrag_id);
    }
    if werkzeug == "desktop_aufraeumen" {
        return raeumen(app, argumente, auftrag_id);
    }

    // 3. Autonomie-Prüfung:
    // Wenn autonomer Modus aktiv ist, sofort ausführen.
    if argumente["autonom"].as_bool() == Some(true) {
        return match werkzeug {
            "desktop_dateien" => dateien(sandbox_pfad, argumente).map(Some),
            "desktop_launch_app" => starten(app, argumente).map(Some),
            "desktop_system" => system::ausfuehren(app, argumente).map(Some),
            "desktop_steuern" => uebernahme::steuern(argumente).map(Some),
            "desktop_artifact" => crate::artefakt::ausfuehren(app, argumente).map(Some),
            andere => Err(format!("Unbekanntes Werkzeug: '{andere}'")),
        };
    }

    // 4. Nicht autonom (autonom == false): Menschliche Bestätigung („Ja“/„Nein“) erzwingen.
    let (titel, beschreibung) = aktion_beschreibung(werkzeug, argumente);
    let id = auftrag_id.unwrap_or("").to_string();
    let wartend = WartendeAktion {
        auftrag_id: id.clone(),
        werkzeug: werkzeug.to_string(),
        argumente: argumente.clone(),
        sandbox_pfad,
        bis: Instant::now() + BESTAETIGUNGSFRIST,
    };
    wartende_aktion_setzen(Some(wartend));

    if let Err(fehler) = app.emit(
        EREIGNIS_AKTION,
        json!({
            "auftrag_id": id,
            "werkzeug": werkzeug,
            "titel": titel,
            "beschreibung": beschreibung,
            "argumente": argumente,
        }),
    ) {
        wartende_aktion_setzen(None);
        return Err(format!("Bestätigungskarte nicht anzeigbar: {fehler}"));
    }
    Ok(None)
    }
}

/// Maus und Tastatur — samt der Bitte um die Freigabe dafuer.
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

    #[test]
    fn wartende_desktop_aktion_laesst_sich_ablehnen() {
        let wartend = WartendeAktion {
            auftrag_id: "test-auftrag-123".to_string(),
            werkzeug: "desktop_system".to_string(),
            argumente: json!({ "aktion": "laufwerke" }),
            sandbox_pfad: None,
            bis: Instant::now() + Duration::from_secs(60),
        };
        wartende_aktion_setzen(Some(wartend));

        assert!(desktop_aktion_ablehnen("test-auftrag-123").is_ok());
        assert!(wartende_aktion_setzen(None).is_none());
    }

    #[test]
    fn verfallene_desktop_aktion_wird_abgewiesen() {
        let wartend = WartendeAktion {
            auftrag_id: "test-auftrag-expired".to_string(),
            werkzeug: "desktop_system".to_string(),
            argumente: json!({ "aktion": "laufwerke" }),
            sandbox_pfad: None,
            bis: Instant::now() - Duration::from_secs(1),
        };
        wartende_aktion_setzen(Some(wartend));

        // Ohne laufende AppHandle schlägt die Ausführung fehl, aber hier schlägt die Frist vorher zu:
        let lock = WARTENDE_AKTION.lock().unwrap();
        assert!(lock.as_ref().unwrap().bis < Instant::now());
        drop(lock);
        wartende_aktion_setzen(None);
    }
}
