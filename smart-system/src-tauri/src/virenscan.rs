//! Eine Datei auf Schadsoftware pruefen — mit dem Scanner, der schon da ist.
//!
//! Der typische Fall: die KI hat etwas in den Sandbox-Ordner geladen oder
//! geschrieben und soll sagen, ob das sauber ist. Einen eigenen Scanner dafuer
//! zu bauen waere absurd (Signaturen, Updates, Heuristik), und eine
//! Cloud-Abfrage schickte die Datei des Benutzers ausser Haus. Windows bringt
//! Microsoft Defender mit, und der hat mit `MpCmdRun.exe` eine
//! Kommandozeilenfassung — kein neues Paket, kein Netzwerk, kein Konto.
//!
//! Das Modul **meldet**, es **handelt nicht**. Deshalb laeuft jeder Scan mit
//! `-DisableRemediation`: ohne dieses Flag loescht oder verschiebt Defender
//! einen Fund selbst in die Quarantaene. Die KI haette damit einen Loeschweg,
//! der an jeder Bestaetigung vorbeigeht — „pruef das mal" waere in Wahrheit
//! „raeum das weg", und beim Fehlalarm auf einer eigenen Datei des Benutzers
//! waere die Datei fort, bevor irgendjemand gefragt wurde. Was mit einem Fund
//! geschieht, entscheidet der Mensch.
//!
//! Eine Ordnergrenze zieht diese Datei bewusst nicht. Der Aufrufer bestimmt,
//! was geprueft werden darf (heute die Sandbox); ein Scanner, der nur seinen
//! eigenen Ordner ansehen darf, waere spaeter nicht mehr zu oeffnen, ohne die
//! Grenze an zwei Stellen zu pflegen.

use std::fs;
use std::io::Read;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// Wie viel von Defenders Ausgabetext zurueckgeht. Der Text landet in einem
/// Prompt, und ein Scan ueber einen Ordner listet im Zweifel jede Datei
/// einzeln auf — dieselbe Ueberlegung wie bei den Leseobergrenzen in
/// `sandbox.rs`.
const MAX_AUSGABE: usize = 4_000;

/// Zeitgrenze eines Scans. Das Panel wartet hoechstens 180 s auf den ganzen
/// Auftrag; ein haengender Scan (Netzlaufwerk, riesiges Archiv, Defender
/// mitten im eigenen Signatur-Update) frisst diese Frist restlos auf, und der
/// Auftrag verfaellt ohne jede Antwort. Lieber nach 120 s ehrlich sagen, dass
/// nicht geprueft wurde, als gar nichts zu sagen.
const ZEITGRENZE: Duration = Duration::from_secs(120);

/// Wie oft nachgesehen wird, ob der Scanner fertig ist. `std` kennt kein
/// `wait_timeout` — dafuer gaebe es eine Kiste, aber ein Blick alle 200 ms
/// kostet nichts und spart eine Abhaengigkeit.
const TAKT: Duration = Duration::from_millis(200);

/// Konsolenfenster unterdruecken (CREATE_NO_WINDOW). MpCmdRun ist ein
/// Konsolenprogramm; aus einer Fensteranwendung heraus blitzte sonst bei jedem
/// Scan ein schwarzes Fenster ueber den Bildschirm des Benutzers.
#[cfg(windows)]
const KEIN_FENSTER: u32 = 0x0800_0000;

/// Prueft eine Datei oder einen Ordner mit Microsoft Defender.
///
/// Ergebnis: `{ "sauber": bool, "befund": <Text oder null>, "geprueft": <Pfad> }`.
/// Jeder Fehlschlag hat eine eigene, benannte Meldung — ein dreimal gleiches
/// „geht nicht" laesst das Modell dreimal dasselbe versuchen.
pub fn pruefen(pfad: &str) -> Result<Value, String> {
    let ziel = absolut(pfad)?;
    // Zuerst der Pfad, dann der Scanner: einen Prozess zu starten, um ihm eine
    // Datei zu nennen, die es nicht gibt, kostet Sekunden und liefert eine
    // Defender-Meldung, aus der niemand den eigentlichen Grund liest.
    if !ziel.exists() {
        return Err(format!(
            "Nicht gefunden: '{}'. Es gibt dort weder eine Datei noch einen \
             Ordner — geprüft wurde nichts.",
            ziel.display()
        ));
    }

    let werkzeug = mpcmdrun_finden().ok_or(
        "Microsoft Defender ist auf diesem Rechner nicht erreichbar \
         (MpCmdRun.exe nicht gefunden). Das ist normal, wenn ein \
         Virenschutz eines Drittanbieters installiert ist oder Defender \
         abgeschaltet wurde. Eine Prüfung ist hier nicht möglich; ein zweiter \
         Versuch ändert daran nichts.",
    )?;

    let mut befehl = Command::new(&werkzeug);
    befehl
        .arg("-Scan")
        // ScanType 3 = benutzerdefiniert, also genau der eine Pfad statt
        // Schnell- oder Vollscan.
        .arg("-ScanType")
        .arg("3")
        .arg("-File")
        .arg(&ziel)
        // Siehe Modulkopf: melden, nicht handeln.
        .arg("-DisableRemediation")
        // Keine Shell dazwischen — die Argumente gehen einzeln an den Prozess.
        // Ein Dateiname wie `a.txt & del *` ist damit ein Dateiname und kein
        // zweiter Befehl.
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        // Fehlerkanal ins Leere: er wird nicht mitgelesen, und eine volle,
        // ungeleerte Leitung liesse den Scanner blockieren, statt zu enden.
        // Was schiefging, sagt der Rueckgabewert.
        .stderr(Stdio::null());
    #[cfg(windows)]
    befehl.creation_flags(KEIN_FENSTER);

    let mut kind = befehl
        .spawn()
        .map_err(|e| format!("Microsoft Defender nicht startbar: {e}"))?;

    // Die Ausgabe muss nebenher geleert werden. Wuerde sie erst nach dem Ende
    // gelesen, koennte der Scanner an einer vollen Leitung stehenbleiben — und
    // genau dann greift die Zeitgrenze bei etwas, das gar nicht scannt.
    let (sender, empfaenger) = mpsc::channel::<String>();
    if let Some(mut leitung) = kind.stdout.take() {
        std::thread::spawn(move || {
            let mut roh = Vec::new();
            let _ = leitung.read_to_end(&mut roh);
            // Defender schreibt in der Codepage der Konsole, nicht in UTF-8;
            // verlustbehaftet lesen ist hier richtig, sonst faellt die ganze
            // Meldung wegen eines einzelnen Umlauts aus.
            let _ = sender.send(String::from_utf8_lossy(&roh).into_owned());
        });
    }

    let beginn = Instant::now();
    let status = loop {
        match kind.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if beginn.elapsed() > ZEITGRENZE {
                    // Erst abbrechen, dann einsammeln: ohne `wait` bliebe ein
                    // Zombie-Prozess zurueck, und der naechste Scan traefe auf
                    // einen Defender, der noch mit dem alten beschaeftigt ist.
                    let _ = kind.kill();
                    let _ = kind.wait();
                    return Err(format!(
                        "Zeitgrenze: Die Prüfung von '{}' wurde nach {} Sekunden \
                         abgebrochen und hat kein Ergebnis geliefert. Das \
                         passiert bei sehr großen Ordnern, Netzlaufwerken oder \
                         während Defender selbst aktualisiert. Prüfe einzelne \
                         Dateien statt ganzer Ordner.",
                        ziel.display(),
                        ZEITGRENZE.as_secs()
                    ));
                }
                std::thread::sleep(TAKT);
            }
            Err(e) => return Err(format!("Microsoft Defender nicht abfragbar: {e}")),
        }
    };

    // Der Lesethread endet mit dem Prozess (die Leitung schliesst); die kurze
    // Frist ist nur die Bremse, damit ein haengender Thread nicht doch noch
    // alles aufhaelt.
    let ausgabe = empfaenger
        .recv_timeout(Duration::from_secs(2))
        .unwrap_or_default();

    // Die Rueckgabewerte von MpCmdRun sind dokumentiert: 0 sauber, 2 Fund.
    match status.code() {
        Some(0) => Ok(json!({
            "sauber": true,
            "befund": Value::Null,
            "geprueft": ziel.display().to_string(),
        })),
        Some(2) => Ok(json!({
            "sauber": false,
            // Nichts wurde entfernt (`-DisableRemediation`) — die Datei liegt
            // noch da, wo sie lag. Das gehoert in die Antwort, sonst haelt das
            // Modell den Fall fuer erledigt.
            "befund": kuerzen(&ausgabe),
            "geprueft": ziel.display().to_string(),
            "hinweis": "Der Fund wurde gemeldet, nicht entfernt oder verschoben. \
                        Die Datei liegt unverändert an ihrem Platz.",
        })),
        andere => Err(format!(
            "Microsoft Defender endete unerwartet (Rückgabewert {}). Weder \
             sauber noch Fund — das Ergebnis ist unbekannt. Ausgabe: {}",
            andere.map(|c| c.to_string()).unwrap_or_else(|| "keiner".into()),
            kuerzen(&ausgabe)
        )),
    }
}

/// Prueft und normalisiert den Pfad. Absolut ist Pflicht — aus demselben Grund
/// wie in `system.rs`: es gibt hier keine Wurzel, zu der ein relativer Pfad
/// gehoeren koennte, und ein stilles Aufloesen gegen das Arbeitsverzeichnis
/// der App pruefte einen Ort, den niemand gemeint hat. Bei einem Virenscan
/// waere das die schlechteste aller Verwechslungen: „sauber" ueber die falsche
/// Datei.
fn absolut(pfad: &str) -> Result<PathBuf, String> {
    let pfad = pfad.trim();
    if pfad.is_empty() {
        return Err("Es fehlt 'pfad' — ein absoluter Pfad wie 'C:\\Users\\Name\\Downloads\\setup.exe'.".into());
    }
    let pfad = PathBuf::from(pfad);
    if !pfad.is_absolute() {
        return Err(format!(
            "'{}' ist kein absoluter Pfad. Zum Prüfen braucht es den vollen \
             Pfad, z. B. 'C:\\Users\\Name\\Downloads\\setup.exe'.",
            pfad.display()
        ));
    }
    Ok(pfad)
}

/// Sucht `MpCmdRun.exe`.
///
/// Defender legt bei jedem Plattform-Update einen neuen Versionsordner an und
/// laesst den alten liegen — dort steht nach einem halben Jahr ein gutes
/// Dutzend. Der alte Ordner enthaelt eine alte Fassung, die gegen die aktuelle
/// Signaturdatenbank nicht mehr sauber arbeitet, also gilt immer die hoechste
/// Version. Der Rueckfall unter `Program Files` ist die Fassung, die Windows
/// mitliefert, bevor je ein Plattform-Update lief.
fn mpcmdrun_finden() -> Option<PathBuf> {
    // Ueber die Umgebungsvariablen statt fest „C:\": auf Rechnern, deren
    // Windows nicht auf C: liegt, gibt es die fest verdrahteten Pfade nicht.
    let plattform = PathBuf::from(
        std::env::var("ProgramData").unwrap_or_else(|_| r"C:\ProgramData".into()),
    )
    .join(r"Microsoft\Windows Defender\Platform");

    if let Ok(eintraege) = fs::read_dir(&plattform) {
        let namen: Vec<String> = eintraege
            .flatten()
            .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
            .map(|e| e.file_name().to_string_lossy().to_string())
            .collect();
        if let Some(neueste) = hoechste_version(&namen) {
            let werkzeug = plattform.join(neueste).join("MpCmdRun.exe");
            if werkzeug.is_file() {
                return Some(werkzeug);
            }
        }
    }

    let rueckfall = PathBuf::from(
        std::env::var("ProgramFiles").unwrap_or_else(|_| r"C:\Program Files".into()),
    )
    .join(r"Windows Defender\MpCmdRun.exe");
    rueckfall.is_file().then_some(rueckfall)
}

/// Waehlt aus Ordnernamen wie `4.18.24090.11-0` die hoechste Version.
///
/// Bewusst ohne Dateisystem, damit die Auswahl pruefbar bleibt: der Vergleich
/// muss zahlenweise laufen, ein Textvergleich stellte `4.18.9.0-0` ueber
/// `4.18.24090.11-0` — und die App benutzte monatelang eine veraltete Fassung,
/// ohne dass es jemandem auffiele.
fn hoechste_version(namen: &[String]) -> Option<&str> {
    namen
        .iter()
        // Neben den Versionsordnern liegt gelegentlich anderes (etwa
        // Sicherungen eines abgebrochenen Updates); eine fuehrende Ziffer ist
        // die einfachste verlaessliche Unterscheidung.
        .filter(|name| name.starts_with(|z: char| z.is_ascii_digit()))
        .max_by_key(|name| versionsteile(name))
        .map(String::as_str)
}

/// `4.18.24090.11-0` → `[4, 18, 24090, 11, 0]`. Was sich nicht als Zahl liest,
/// zaehlt als 0 statt den ganzen Namen auszuschliessen — ein unerwarteter
/// Zusatz am Ende soll den Ordner nicht unsichtbar machen.
fn versionsteile(name: &str) -> Vec<u64> {
    name.split(['.', '-'])
        .map(|teil| teil.parse::<u64>().unwrap_or(0))
        .collect()
}

/// Kuerzt den Ausgabetext auf `MAX_AUSGABE` und sagt es dazu. Geschnitten wird
/// auf einer Zeichengrenze, nicht auf einer Bytegrenze — ein halbes
/// Umlaut-Byte macht aus der Antwort Datenmuell (wie in `sandbox.rs`).
fn kuerzen(text: &str) -> String {
    let text = text.trim();
    if text.len() <= MAX_AUSGABE {
        return text.to_string();
    }
    let ende = text
        .char_indices()
        .map(|(i, _)| i)
        .take_while(|i| *i <= MAX_AUSGABE)
        .last()
        .unwrap_or(0);
    format!(
        "{}\n[gekürzt: Defender hat {} Zeichen ausgegeben, hier stehen die ersten {ende}.]",
        &text[..ende],
        text.len()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ein_nicht_vorhandener_pfad_wird_benannt_abgewiesen() {
        // Wichtig ist nicht nur die Ablehnung, sondern dass sie kommt, **bevor**
        // Defender startet: sonst kostete jeder Tippfehler des Modells Sekunden
        // aus der 180-s-Frist des Auftrags.
        let weg = std::env::temp_dir()
            .join(format!("mss-virenscan-nie-da-{}.bin", std::process::id()));
        assert!(!weg.exists());

        let beginn = Instant::now();
        let fehler = pruefen(&weg.to_string_lossy()).unwrap_err();

        assert!(fehler.contains("Nicht gefunden"), "{fehler}");
        // Ein gestarteter Scanner braucht selbst im besten Fall laenger als
        // eine Sekunde; so bleibt die Zusage „ohne Defender zu starten"
        // ueberhaupt pruefbar.
        assert!(beginn.elapsed() < Duration::from_secs(1), "zu langsam");
    }

    #[test]
    fn ein_relativer_pfad_wird_benannt_abgewiesen() {
        // Still gegen das Arbeitsverzeichnis der App aufzuloesen hiesse, eine
        // andere Datei zu pruefen als die gemeinte — und sie „sauber" zu nennen.
        let fehler = pruefen("Downloads/setup.exe").unwrap_err();
        assert!(fehler.contains("kein absoluter Pfad"), "{fehler}");
    }

    #[test]
    fn ein_leerer_pfad_nennt_das_fehlende_feld() {
        let fehler = pruefen("   ").unwrap_err();
        assert!(fehler.contains("Es fehlt 'pfad'"), "{fehler}");
    }

    #[test]
    fn die_hoechste_version_gewinnt_auch_gegen_den_textvergleich() {
        let ordner: Vec<String> = [
            "4.18.9.0-0",       // textlich das groesste, zahlenmaessig das kleinste
            "4.18.24090.11-0",  // das gesuchte
            "4.18.23110.3-0",
            "Backup",           // kein Versionsordner
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();

        assert_eq!(hoechste_version(&ordner), Some("4.18.24090.11-0"));
    }

    #[test]
    fn ohne_versionsordner_gibt_es_keine_wahl() {
        // Dann greift der Rueckfall unter `Program Files` — hier darf nicht
        // versehentlich „Backup" als Version durchgehen.
        let ordner = vec!["Backup".to_string(), "Updates".to_string()];
        assert_eq!(hoechste_version(&ordner), None);
        assert_eq!(hoechste_version(&[]), None);
    }

    #[test]
    fn lange_ausgaben_werden_gekuerzt_und_sagen_es() {
        let kurz = kuerzen("  Scan finished.  ");
        assert_eq!(kurz, "Scan finished.");

        let lang = "x".repeat(MAX_AUSGABE * 2);
        let geschnitten = kuerzen(&lang);
        assert!(geschnitten.contains("gekürzt"), "{geschnitten}");
        // Ein stilles Abschneiden waere die Fassung, in der das Modell eine
        // halbe Fundliste fuer die ganze haelt.
        assert!(geschnitten.len() < lang.len());
    }
}
