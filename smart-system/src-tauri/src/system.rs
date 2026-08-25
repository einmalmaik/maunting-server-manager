//! Lesender Blick auf den Rechner — Laufwerke, Ordner, Platzfresser,
//! Bildschirm, Virenscan.
//!
//! Dieses Modul liest nur. Es enthaelt bewusst keinen einzigen schreibenden
//! Dateisystemaufruf, und weil keine Typgrenze das erzwingen kann, haelt der
//! Test `lesend_bleibt_lesend` es als Quelltext-Zusage fest.
//!
//! Geschrieben wird an genau zwei Stellen des Programms, jede mit ihrer
//! eigenen Grenze: in der Sandbox (`sandbox.rs`, ein vom Benutzer
//! freigegebener Ordner) und beim Aufraeumen (`aufraeumen.rs` mit `zonen.rs`,
//! seit dem 23.08.2026, mit Papierkorb als Normalfall und einer Karte, wenn
//! der autonome Modus aus ist).
//!
//! Warum es das Modul gibt: "wie voll ist meine C-Platte" und "was frisst
//! den Platz" sind die haeufigsten Systemfragen — und die Antwort lag bisher
//! hinter der Sandbox-Grenze, wo sie nie liegen konnte. Was der KI dabei
//! nicht gehoert (Systemdateien, fremde Profile), steht als Fuehrung im
//! Prompt, nicht als Verbot hier: unlesbare Ordner werden schlicht
//! uebersprungen, wie es jedem Programm ohne Adminrechte ginge.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// Wie viele Eintraege eine Auflistung nennt — dieselbe Grenze wie in der
/// Sandbox: ein Ordner mit 20.000 Dateien gehoert nicht in einen Prompt.
const MAX_EINTRAEGE: usize = 300;
/// Zeitbudget der Groessenanalyse. Das Panel wartet hoechstens 180 s auf den
/// ganzen Auftrag und holt das Ergebnis 90 s lang ab — wer laenger rechnet,
/// liefert ins Leere. 60 s lassen Luft fuer Abholung und Netz; eine volle
/// C-Platte ist in der Zeit nicht fertig, aber die groessten Posten stehen.
const ZEITBUDGET: Duration = Duration::from_secs(60);
/// Wie viele Platzfresser (Dateien wie Unterordner) genannt werden.
const TOP_ANZAHL: usize = 25;

/// Alles, was der Rechner **ansehen** laesst.
///
/// Der `AppHandle` wird nur fuer `bildschirm` gebraucht (dort haengt der
/// Indikator daran) und sonst nirgends — er steht trotzdem in der Signatur
/// und nicht in einem Sonderweg, damit es genau einen Eingang gibt.
///
/// `bildschirm` und `virenscan` stehen hier und nicht in eigenen Werkzeugen,
/// weil beides Ansehen ist: das eine schaut auf den Monitor, das andere
/// laesst Defender auf eine Datei schauen. Beide veraendern nichts — der
/// Virenscan laeuft ausdruecklich mit `-DisableRemediation`, sonst raeumte
/// Defender den Fund selbst weg und die KI haette einen Loeschweg an jeder
/// Bestaetigung vorbei.
pub fn ausfuehren(app: &tauri::AppHandle, argumente: &Value) -> Result<Value, String> {
    // Die eine Aktion, die den Griff auf die App braucht — dort haengt der
    // Indikator dran. Sie steht vor dem uebrigen Dispatch und nicht darin,
    // damit der Rest ohne laufende App pruefbar bleibt: einen `AppHandle`
    // kann ein Test nicht bauen, und ein Zweig ohne Test ist der Zweig, der
    // spaeter still falsch antwortet.
    if argumente["aktion"].as_str() == Some("bildschirm") {
        return crate::bildschirm::aufnehmen(app);
    }
    ohne_app(argumente)
}

fn ohne_app(argumente: &Value) -> Result<Value, String> {
    let pfad = argumente["pfad"].as_str().unwrap_or("");
    let bereich = argumente["systembereich"].as_str();
    match argumente["aktion"].as_str().unwrap_or("") {
        "laufwerke" => laufwerke_impl::alle(),
        // `verzeichnis` und `virenscan` sehen alles an, was unter dem Pfad
        // liegt, und koennen nichts davon auslassen — sie brauchen deshalb
        // auch die Frage nach dem, was *darunter* liegt. `groesste` kann
        // auslassen und tut es (siehe dort).
        "verzeichnis" => nur_freie_orte(pfad, bereich).and_then(|()| verzeichnis(pfad)),
        "groesste" => {
            zugelassen(pfad, bereich).and_then(|()| groesste(pfad, bereich == Some("aus")))
        }
        "virenscan" => {
            nur_freie_orte(pfad, bereich).and_then(|()| crate::virenscan::pruefen(pfad))
        }
        andere => Err(format!(
            "Unbekannte Aktion: '{andere}'. Erlaubt sind laufwerke, \
             verzeichnis, groesste, bildschirm, virenscan."
        )),
    }
}

/// Darf hier ueberhaupt hingesehen werden?
///
/// Die Kontoeinstellung des Benutzers kennt drei Stufen: `aus`, `lesen`,
/// `schreiben`. Nur `aus` verschliesst auch den **Blick** in den
/// Systembereich; `lesen` und `schreiben` lassen ihn zu, und `lesen` ist der
/// Standard und damit der Zustand, den es vor dem 23.08.2026 immer gab.
///
/// Fehlt das Feld ganz — ein aelteres Panel, das es noch nicht mitschickt —,
/// wird gelesen. Das ist hier die richtige Seite und beim Loeschen die
/// falsche: ein fehlendes Feld darf nichts vernichten, aber es soll auch
/// nicht heimlich Auskuenfte abschneiden, die gestern noch kamen. Die enge
/// Seite gewinnt dort, wo etwas kaputtgehen kann.
fn zugelassen(pfad: &str, systembereich: Option<&str>) -> Result<(), String> {
    if systembereich != Some("aus") {
        return Ok(());
    }
    if crate::zonen::zone(std::path::Path::new(pfad.trim())) != crate::zonen::Zone::System {
        return Ok(());
    }
    Err(format!(
        "'{pfad}' gehoert zum Systembereich (Windows, Programmordner, \
         Bootdateien oder ein fremdes Benutzerprofil). Der Benutzer hat ihn \
         in den Einstellungen der App auf 'aus' gestellt — auch das Ansehen. \
         Das aendert nur er selbst."
    ))
}

/// Wie `zugelassen`, aber auch fuer das, was **unter** dem Pfad liegt.
///
/// Der Grund ist ein einziger naheliegender Pfad: `C:\` gehoert selbst zu
/// keinem Systemort — Windows, die Programmordner, `Boot` und die fremden
/// Profile liegen alle *darunter*. Wer die Wurzel nannte, sah damit trotz
/// "aus" den ganzen Systembereich, und die Zusage der Einstellung ("auch das
/// Ansehen") war mit einem Buchstaben umgangen.
fn nur_freie_orte(pfad: &str, systembereich: Option<&str>) -> Result<(), String> {
    zugelassen(pfad, systembereich)?;
    if systembereich != Some("aus") {
        return Ok(());
    }
    // Ueber `absolut`, weil "C:" allein laufwerksrelativ ist: ein `read_dir`
    // darauf laese das Arbeitsverzeichnis der App auf C: und nicht die
    // Wurzel — der Riegel griffe fuer genau diese Schreibweise nicht. Ein
    // leerer oder relativer Pfad scheitert gleich danach mit seiner eigenen,
    // benannten Meldung.
    let Ok(ort) = absolut(pfad) else {
        return Ok(());
    };
    // Hier wird aufgeloest, einmal je Auftrag: `C:\Windows\..` zeigt auf die
    // Laufwerkswurzel, und gezaehlt werden muss der Ort, der gelesen wird.
    if !birgt_systemorte(&crate::zonen::aufloesen(&ort)) {
        return Ok(());
    }
    Err(format!(
        "Unter '{pfad}' liegt der Systembereich (Windows, Programmordner, \
         Bootdateien oder fremde Benutzerprofile). Der Benutzer hat ihn in \
         den Einstellungen der App auf 'aus' gestellt — auch das Ansehen. \
         Nenne einen Ordner darunter, der nicht dazugehoert; die Platzfrage \
         beantwortet ausserdem 'laufwerke' und, fuer eine ganze Platte, \
         'groesste' (das laesst den Systembereich einzeln aus)."
    ))
}

/// Liegt ein Systemort unmittelbar unter diesem Pfad?
///
/// Geprueft wird genau eine Ebene, und keine Datei: ein Systemort ist immer
/// ein Ordner, und jeder von ihnen liegt direkt unter einer Laufwerkswurzel
/// (`C:\Windows`, `C:\Users`) oder direkt unter `C:\ProgramData` — beides
/// erreicht diese eine Ebene. Wie tief das reicht und was es kostet, steht
/// bei [`SYSTEM_TIEFE`].
///
/// Erwartet einen **aufgeloesten** Pfad; der Aufrufer hat ihn schon durch
/// `zonen::aufloesen` geschickt. `C:\Windows\..` hat roh vier Bestandteile
/// und fiele damit durch den Deckel unten, waehrend `read_dir` darauf die
/// Laufwerkswurzel zeigt: genau den Ort, den dieser Riegel verschliesst.
/// Gezaehlt werden muss der Ort, der wirklich gelesen wird, sonst sind zwei
/// Punkte der Schluessel — und eine tief liegende Junction auf `C:\` waere
/// der zweite.
fn birgt_systemorte(pfad: &Path) -> bool {
    // Liegen die Kinder dieses Pfades schon tiefer, als ein Systemort je
    // anfaengt, ist nichts zu fragen — und das ist der Normalfall. Ohne
    // diese Zeile kostete die Auflistung eines Ordners mit tausend
    // Unterordnern tausend Zonenfragen.
    if pfad.components().count() + 1 > SYSTEM_TIEFE {
        return false;
    }
    let Ok(eintraege) = fs::read_dir(pfad) else {
        return false;
    };
    eintraege.flatten().any(|kind| {
        // Verknuepfungen mitfragen: eine Junction unter der Wurzel zeigt
        // vielleicht genau dorthin, wohin gerade nicht gesehen werden soll.
        kind.file_type().map(|art| !art.is_file()).unwrap_or(false)
            && crate::zonen::zone(&kind.path()) == crate::zonen::Zone::System
    })
}

/// Aus wie vielen Bestandteilen ein Systemort hoechstens besteht.
///
/// `C:\Windows` sind drei, `C:\ProgramData\Microsoft` und `C:\Users\Fremd`
/// vier (unter Windows zaehlen Laufwerksbuchstabe und Wurzel getrennt).
/// Tiefer faengt keiner der Orte aus `zonen::systemorte` an, und deshalb
/// braucht ein Ordner darunter die Frage gar nicht erst.
///
/// Die Grenze steht hier aus Kostengruenden: `zonen::zone` loest je Aufruf
/// ein gutes Dutzend Orte einzeln auf (jedes mit einem Dateisystemaufruf).
/// Je Ordner eines Millionenbaums gestellt waere die Frage teurer als die
/// ganze Rechnung — und die Auskunft bliebe im Zeitbudget stecken.
///
/// Was sie nicht erfasst: den eigenen Programmordner. Der zaehlt in `zonen`
/// auch als Systemort, liegt aber tief unter `AppData` — ihn in einer
/// Groessenrechnung zu sehen ist harmlos, gesperrt ist er dort gegen das
/// Loeschen, nicht gegen das Ansehen.
///
/// Gezaehlt wird ein aufgeloester Pfad. Ein roher kann mit `..` beliebig tief
/// aussehen und trotzdem auf `C:\` zeigen — eine Zahl, die den Ort nicht
/// trifft, ist keine Grenze. Aufgeloest wird deshalb die **Wurzel**, einmal je
/// Auftrag (`nur_freie_orte`, `groesste`); was darunter aus `read_dir` waechst,
/// erbt sie und wird roh gezaehlt. Je Ordner aufzuloesen kostete genau den
/// Dateisystemaufruf, den dieser Deckel gerade spart.
const SYSTEM_TIEFE: usize = 4;

/// Laesst dieser Lauf den Ordner aus, weil er zum Systembereich gehoert?
///
/// Nur bei `systembereich: "aus"` — sonst aendert sich an der Rechnung nichts,
/// und die Frage kostet dann auch nichts: der Zustand steht vor allem anderen.
/// Ausgelassen und nicht abgewiesen: eine Zahl ohne Windows ist mehr wert als
/// gar keine, und die Antwort sagt dazu, dass etwas fehlt.
///
/// Gezaehlt wird **roh**, und das ist keine Nachlaessigkeit: `groesste` hat
/// die Wurzel einmal aufgeloest, jeder Pfad hier ist daraus und aus
/// `read_dir`-Namen gewachsen und traegt deshalb weder `..` noch einen
/// unaufgeloesten Praefix. Was unterwegs doch woandershin zeigt — Junction,
/// Symlink — ist in beiden Schleifen schon an `typ.is_symlink()`
/// haengengeblieben, bevor diese Frage gestellt wird. Hier selbst
/// aufzuloesen kostete einen Dateisystemaufruf je Ordner des Laufs, also
/// millionenfach das, was der Deckel darunter gerade vermeidet.
fn ausgelassen(pfad: &Path, system_gesperrt: bool) -> bool {
    if !system_gesperrt {
        return false;
    }
    pfad.components().count() <= SYSTEM_TIEFE
        && crate::zonen::zone(pfad) == crate::zonen::Zone::System
        && !birgt_eigene_dateien(pfad)
}

/// Birgt dieser Ordner die eigenen Dateien des Benutzers?
///
/// `zonen` ordnet die Profilwurzel (`C:\Users`) und das eigene Profil darin
/// als `Zone::System` ein, und dort ist das richtig: gemeint ist der Ordner
/// **selbst** — wer `C:\Users\Name` loescht, nimmt das ganze Profil mit.
///
/// Fuer die Groessenrechnung waere dieselbe Antwort eine Luege. Der Lauf ueber
/// `C:\` trifft `C:\Users` auf der obersten Ebene; als Ganzes ausgelassen
/// fehlte das eigene Profil — auf den meisten Rechnern der groesste Posten
/// ueberhaupt —, waehrend `uebersprungen` darunter behauptet, es fehlten nur
/// Windows, Programmordner, Bootdateien und *fremde* Profile. Die Einstellung
/// im Panel sagt dazu ausdruecklich: "Deine eigenen Dateien sind davon nicht
/// betroffen."
///
/// Also wird hineingestiegen statt ausgelassen. Eine Ebene tiefer erledigt
/// `zonen::zone` den Rest von selbst: fremde Profile sind dort weiter
/// `System` und fallen weg, alles unter dem eigenen Profil ist `Frei`.
fn birgt_eigene_dateien(ordner: &Path) -> bool {
    let Some(profil) = std::env::var_os("USERPROFILE") else {
        return false;
    };
    let profil = bestandteile(&crate::zonen::aufloesen(Path::new(&profil)));
    let ordner = bestandteile(ordner);
    !ordner.is_empty() && profil.len() >= ordner.len() && profil[..ordner.len()] == ordner[..]
}

/// Die Bestandteile eines Pfades in Kleinschreibung. Windows unterscheidet
/// keine Gross-/Kleinschreibung, und `c:\users` ist dieselbe Profilwurzel wie
/// `C:\Users` — ein byteweiser Vergleich saehe zwei verschiedene Orte.
fn bestandteile(pfad: &Path) -> Vec<String> {
    pfad.components()
        .map(|teil| teil.as_os_str().to_string_lossy().to_lowercase())
        .collect()
}

/// Prueft und normalisiert den Pfad. Anders als in der Sandbox ist hier ein
/// **absoluter** Pfad Pflicht — es gibt keine Wurzel, zu der ein relativer
/// gehoeren koennte, und ein stilles Aufloesen gegen das Arbeitsverzeichnis
/// der App zeigte dem Modell einen Ort, den es nicht gemeint hat.
fn absolut(pfad: &str) -> Result<PathBuf, String> {
    let mut pfad = pfad.trim().to_string();
    if pfad.is_empty() {
        return Err("Es fehlt 'pfad' — ein absoluter Pfad wie 'C:\\Users'.".into());
    }
    // "C:" allein ist unter Windows laufwerksrelativ; gemeint ist die Wurzel.
    if pfad.ends_with(':') {
        pfad.push(std::path::MAIN_SEPARATOR);
    }
    let pfad = PathBuf::from(pfad);
    if !pfad.is_absolute() {
        return Err(format!(
            "'{}' ist kein absoluter Pfad. Ausserhalb des Sandbox-Ordners \
             braucht es den vollen Pfad, z. B. 'C:\\Users\\Name\\Downloads'.",
            pfad.display()
        ));
    }
    Ok(pfad)
}

fn verzeichnis(pfad: &str) -> Result<Value, String> {
    let ordner = absolut(pfad)?;
    let lesen = fs::read_dir(&ordner).map_err(|e| format!("Ordner nicht lesbar: {e}"))?;

    let start = Instant::now();
    let mut eintraege: Vec<Value> = Vec::new();
    let mut gekappt = false;
    for element in lesen.flatten() {
        // Nach der Kappung sofort aufhören, nicht weiterzählen: einen
        // Netzordner mit Millionen Einträgen ganz durchzulaufen, nur um die
        // exakte Restzahl zu nennen, kostete Minuten — und die ohnehin
        // gekappte Antwort verfiele beim Panel (Abholfrist).
        if eintraege.len() >= MAX_EINTRAEGE || start.elapsed() > ZEITBUDGET {
            gekappt = true;
            break;
        }
        let Ok(daten) = element.metadata() else { continue };
        eintraege.push(json!({
            "name": element.file_name().to_string_lossy(),
            "ordner": daten.is_dir(),
            "groesse": if daten.is_dir() { 0 } else { daten.len() },
        }));
    }
    eintraege.sort_by_key(|e| {
        (
            !e["ordner"].as_bool().unwrap_or(false),
            e["name"].as_str().unwrap_or("").to_lowercase(),
        )
    });

    let genannt = eintraege.len();
    let mut ausgabe = json!({ "pfad": ordner.display().to_string(), "eintraege": eintraege });
    if gekappt {
        ausgabe["gekuerzt"] =
            json!(format!("Der Ordner hat mehr Eintraege — genannt sind die ersten {genannt}."));
    }
    Ok(ausgabe)
}

/// Die Platzfresser unter einem Pfad.
///
/// `system_gesperrt` kommt aus der Kontoeinstellung (`systembereich: "aus"`)
/// und wirkt **im Lauf**, nicht nur am Startpunkt: von `C:\` aus liegt der
/// ganze Systembereich einen Schritt weiter, und `zugelassen` sieht ihn dort
/// nicht. Ausgelassene Ordner stehen als `uebersprungen` in der Antwort —
/// eine unvollstaendige Zahl, die sich als vollstaendig ausgibt, waere hier
/// die schlechtere Auskunft.
fn groesste(pfad: &str, system_gesperrt: bool) -> Result<Value, String> {
    // Einmal aufgeloest, bevor der erste Ordner gelesen wird — danach zaehlt
    // `ausgelassen` roh (siehe dort). Mit `C:\Windows\..` als Eingabe hiessen
    // die Kinder sonst `C:\Windows\..\Windows`: roh zu tief fuer den Deckel,
    // also nie ausgelassen — der Lauf stieg mitten in Windows ab und gab sich
    // trotzdem als vollstaendig aus. Nebenbei nennt die Antwort damit den
    // Ort, an dem wirklich gerechnet wurde.
    let wurzel = crate::zonen::aufloesen(&absolut(pfad)?);
    let start = Instant::now();

    let mut toepfe: Vec<(String, u64)> = Vec::new();
    let mut dateien: Vec<(String, u64)> = Vec::new();
    let mut gesamt = 0u64;
    let mut abgebrochen = false;
    let mut uebersprungen = false;

    let kinder = fs::read_dir(&wurzel).map_err(|e| format!("Ordner nicht lesbar: {e}"))?;
    for kind in kinder.flatten() {
        // Auch hier je Eintrag prüfen: ein Wurzelordner, der nur Dateien
        // enthält, erreicht `ordner_summe` nie — ohne diese Zeile sähe so
        // ein Lauf das Budget überhaupt nicht (flacher Riesenordner auf
        // einem Netzpfad).
        if start.elapsed() > ZEITBUDGET {
            abgebrochen = true;
            break;
        }
        let Ok(typ) = kind.file_type() else { continue };
        // Verknuepfungen (Symlinks, Junctions) zeigen woandershin — ihnen zu
        // folgen zaehlte fremde Orte mit und kann Schleifen bauen.
        if typ.is_symlink() {
            continue;
        }
        if typ.is_dir() {
            if ausgelassen(&kind.path(), system_gesperrt) {
                uebersprungen = true;
                continue;
            }
            let (summe, system_dabei) = ordner_summe(
                &kind.path(),
                &wurzel,
                &start,
                &mut dateien,
                &mut abgebrochen,
                system_gesperrt,
            );
            uebersprungen |= system_dabei;
            gesamt += summe;
            toepfe.push((kind.file_name().to_string_lossy().to_string(), summe));
        } else if let Ok(daten) = kind.metadata() {
            gesamt += daten.len();
            merken(&mut dateien, &kind.path(), &wurzel, daten.len());
        }
        if abgebrochen {
            break;
        }
    }

    toepfe.sort_by(|a, b| b.1.cmp(&a.1));
    toepfe.truncate(TOP_ANZAHL);

    let mut ausgabe = json!({
        "pfad": wurzel.display().to_string(),
        "gesamt_bytes": gesamt,
        "unterordner": toepfe
            .iter()
            .map(|(name, bytes)| json!({ "name": name, "bytes": bytes }))
            .collect::<Vec<_>>(),
        "dateien": dateien
            .iter()
            .map(|(pfad, bytes)| json!({ "pfad": pfad, "bytes": bytes }))
            .collect::<Vec<_>>(),
    });
    if abgebrochen {
        ausgabe["gekuerzt"] = json!(format!(
            "Zeitbudget von {} s erreicht — die Zahlen sind eine Untergrenze; \
             die groessten Posten stimmen in der Regel trotzdem.",
            ZEITBUDGET.as_secs()
        ));
    }
    if uebersprungen {
        ausgabe["uebersprungen"] = json!(
            "systembereich — Windows, Programmordner, Bootdateien und fremde \
             Profile sind in diesen Zahlen nicht enthalten. Der Benutzer hat \
             den Systembereich in den Einstellungen der App auf 'aus' \
             gestellt; das aendert nur er selbst."
        );
    }
    Ok(ausgabe)
}

/// Summiert einen Ordner rekursiv und sammelt die groessten Dateien ein.
/// Unlesbares wird uebersprungen (Systemordner ohne Rechte), Verknuepfungen
/// werden nicht verfolgt.
///
/// Zweiter Rueckgabewert: ob dabei ein Systemort ausgelassen wurde — die
/// Zahlen sind dann unvollstaendig, und die Antwort sagt das.
fn ordner_summe(
    ordner: &Path,
    wurzel: &Path,
    start: &Instant,
    dateien: &mut Vec<(String, u64)>,
    abgebrochen: &mut bool,
    system_gesperrt: bool,
) -> (u64, bool) {
    let mut summe = 0u64;
    let mut uebersprungen = false;
    let mut stapel = vec![ordner.to_path_buf()];
    while let Some(aktuell) = stapel.pop() {
        if start.elapsed() > ZEITBUDGET {
            *abgebrochen = true;
            return (summe, uebersprungen);
        }
        let Ok(eintraege) = fs::read_dir(&aktuell) else { continue };
        for element in eintraege.flatten() {
            // Je Eintrag, nicht nur je Ordner: ein einzelnes Verzeichnis mit
            // Millionen Einträgen liefe sonst am Budget vorbei, so lange die
            // eine Schleife eben braucht.
            if start.elapsed() > ZEITBUDGET {
                *abgebrochen = true;
                return (summe, uebersprungen);
            }
            let Ok(typ) = element.file_type() else { continue };
            if typ.is_symlink() {
                continue;
            }
            if typ.is_dir() {
                if ausgelassen(&element.path(), system_gesperrt) {
                    uebersprungen = true;
                    continue;
                }
                stapel.push(element.path());
            } else if let Ok(daten) = element.metadata() {
                summe += daten.len();
                merken(dateien, &element.path(), wurzel, daten.len());
            }
        }
    }
    (summe, uebersprungen)
}

/// Haelt die Liste der groessten Dateien sortiert und bei `TOP_ANZAHL` — der
/// Pfad wird erst gebaut, wenn die Datei es in die Liste schafft; bei einem
/// Lauf ueber Millionen Dateien ist das der Unterschied.
fn merken(dateien: &mut Vec<(String, u64)>, pfad: &Path, wurzel: &Path, groesse: u64) {
    if dateien.len() >= TOP_ANZAHL {
        if groesse <= dateien.last().map(|(_, g)| *g).unwrap_or(0) {
            return;
        }
        dateien.pop();
    }
    let relativ = pfad
        .strip_prefix(wurzel)
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| pfad.to_string_lossy().to_string());
    let stelle = dateien.partition_point(|(_, g)| *g >= groesse);
    dateien.insert(stelle, (relativ, groesse));
}

#[cfg(windows)]
mod laufwerke_impl {
    use serde_json::{json, Value};
    use windows::core::PCWSTR;
    use windows::Win32::Storage::FileSystem::{
        GetDiskFreeSpaceExW, GetDriveTypeW, GetLogicalDrives,
    };

    pub fn alle() -> Result<Value, String> {
        let maske = unsafe { GetLogicalDrives() };
        if maske == 0 {
            return Err("Laufwerke nicht abfragbar.".into());
        }
        let mut liste: Vec<Value> = Vec::new();
        for stelle in 0..26u32 {
            if maske & (1 << stelle) == 0 {
                continue;
            }
            let buchstabe = char::from(b'A' + stelle as u8);
            let wurzel: Vec<u16> = format!("{buchstabe}:\\")
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            // Die DRIVE_*-Werte aus der Win32-Doku (GetDriveTypeW). Die
            // benannten Konstanten laegen in Win32_System_WindowsProgramming
            // — ein weiteres Feature fuer fuenf dokumentierte Zahlen.
            let art = match unsafe { GetDriveTypeW(PCWSTR(wurzel.as_ptr())) } {
                2 => "wechseldatentraeger",
                3 => "festplatte",
                4 => "netzlaufwerk",
                5 => "cd",
                6 => "ramdisk",
                _ => "unbekannt",
            };
            let mut zeile = json!({ "laufwerk": format!("{buchstabe}:"), "art": art });
            let mut frei = 0u64;
            let mut gesamt = 0u64;
            // `frei` ist der Platz, den **dieser Benutzer** nutzen darf
            // (Kontingente eingerechnet) — genau die Zahl, nach der gefragt
            // wird. Ein leeres CD-Laufwerk scheitert hier; dann fehlen die
            // Zahlen einfach, das Laufwerk selbst wird trotzdem genannt.
            let platz = unsafe {
                GetDiskFreeSpaceExW(
                    PCWSTR(wurzel.as_ptr()),
                    Some(&mut frei as *mut u64),
                    Some(&mut gesamt as *mut u64),
                    None,
                )
            };
            if platz.is_ok() {
                zeile["gesamt_bytes"] = json!(gesamt);
                zeile["frei_bytes"] = json!(frei);
            }
            liste.push(zeile);
        }
        Ok(json!({ "laufwerke": liste }))
    }
}

#[cfg(not(windows))]
mod laufwerke_impl {
    use serde_json::Value;

    pub fn alle() -> Result<Value, String> {
        Err("Die Laufwerksuebersicht gibt es bisher nur unter Windows.".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Ein leerer, eigener Ordner — je Test ein anderer Name, damit sich
    /// gleichzeitig laufende Tests nicht gegenseitig den Boden wegziehen.
    fn freier_ordner(name: &str) -> PathBuf {
        let pfad = std::env::temp_dir().join(format!("mss-system-{name}-{}", std::process::id()));
        fs::create_dir_all(&pfad).unwrap();
        pfad
    }

    fn baum(name: &str) -> PathBuf {
        let pfad = std::env::temp_dir().join(format!("mss-system-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&pfad);
        fs::create_dir_all(pfad.join("gross")).unwrap();
        fs::create_dir_all(pfad.join("klein")).unwrap();
        fs::write(pfad.join("gross").join("riese.bin"), vec![0u8; 4096]).unwrap();
        fs::write(pfad.join("gross").join("mittel.bin"), vec![0u8; 1024]).unwrap();
        fs::write(pfad.join("klein").join("zwerg.txt"), b"winzig").unwrap();
        fs::write(pfad.join("wurzel.txt"), b"direkt hier").unwrap();
        pfad
    }

    #[test]
    fn die_groessenanalyse_findet_die_platzfresser() {
        let wurzel = baum("groesse");
        let ergebnis = groesste(&wurzel.to_string_lossy(), false).unwrap();

        // Der groesste Unterordner steht vorn, die groesste Datei auch.
        assert_eq!(ergebnis["unterordner"][0]["name"], "gross");
        assert_eq!(ergebnis["unterordner"][0]["bytes"], 4096 + 1024);
        assert!(ergebnis["dateien"][0]["pfad"]
            .as_str()
            .unwrap()
            .ends_with("riese.bin"));
        // Die Wurzeldatei zaehlt zur Gesamtsumme.
        assert_eq!(
            ergebnis["gesamt_bytes"].as_u64().unwrap(),
            4096 + 1024 + 6 + 11
        );
        // Nichts abgebrochen: kein Kuerzungshinweis.
        assert!(ergebnis.get("gekuerzt").is_none());
        // Und nichts ausgelassen: ohne die Einstellung "aus" bleibt jeder
        // Ordner in der Rechnung.
        assert!(ergebnis.get("uebersprungen").is_none());
    }

    #[test]
    fn die_auflistung_nennt_ordner_zuerst() {
        let wurzel = baum("auflistung");
        let ergebnis = verzeichnis(&wurzel.to_string_lossy()).unwrap();
        let namen: Vec<&str> = ergebnis["eintraege"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| e["name"].as_str().unwrap())
            .collect();
        assert_eq!(namen, vec!["gross", "klein", "wurzel.txt"]);
    }

    #[test]
    fn ein_relativer_pfad_wird_abgewiesen() {
        // Still gegen das Arbeitsverzeichnis aufzuloesen zeigte dem Modell
        // einen Ort, den es nicht gemeint hat.
        let fehler = verzeichnis("Users/irgendwer").unwrap_err();
        assert!(fehler.contains("kein absoluter Pfad"), "{fehler}");
    }

    #[test]
    fn eine_unbekannte_aktion_nennt_die_erlaubten() {
        let fehler = ohne_app(&serde_json::json!({ "aktion": "loeschen" })).unwrap_err();
        assert!(fehler.contains("laufwerke, verzeichnis, groesste"), "{fehler}");
        // Auch die zwei neuen Aktionen stehen in der Aufzaehlung — ein Modell,
        // das danebengreift, soll den ganzen Vorrat sehen und nicht die Haelfte.
        assert!(fehler.contains("bildschirm"), "{fehler}");
        assert!(fehler.contains("virenscan"), "{fehler}");
    }

    #[test]
    fn aus_verschliesst_auch_den_blick() {
        // Die dritte Stufe der Kontoeinstellung. Sie wirkte anfangs nur beim
        // Loeschen — "aus" haette dann bedeutet: sie darf dort alles sehen,
        // nur nichts wegnehmen. Das ist nicht, was der Betreiber bestellt hat.
        let fehler = ohne_app(&serde_json::json!({
            "aktion": "verzeichnis",
            "pfad": r"C:\Windows\System32",
            "systembereich": "aus",
        }))
        .unwrap_err();
        assert!(fehler.contains("Systembereich"), "{fehler}");
    }

    #[test]
    fn aus_haelt_auch_die_laufwerkswurzel_zu() {
        // Der Pfad, der die ganze Einstellung aushebelte: `C:\` gehoert
        // selbst zu keinem Systemort — Windows, die Programmordner, `Boot`
        // und die fremden Profile liegen alle *darunter*. Die Auflistung
        // zeigte sie damit trotz "aus", und `aus_verschliesst_auch_den_blick`
        // merkte davon nichts, weil es nur `C:\Windows\System32` fragt.
        let fehler = ohne_app(&serde_json::json!({
            "aktion": "verzeichnis",
            "pfad": "C:\\",
            "systembereich": "aus",
        }))
        .unwrap_err();
        assert!(fehler.contains("Systembereich"), "{fehler}");

        // Denselben Riegel bekommt der Virenscan: Defender kann keinen
        // Ordner auslassen, also wird gar nicht erst gescannt. Geprueft an
        // der Schranke selbst — ein Scan ueber `C:\` liefe zwei Minuten.
        assert!(nur_freie_orte("C:\\", Some("aus")).is_err());
        // Auch in der laufwerksrelativen Schreibweise: "C:" allein meint
        // dieselbe Wurzel, und `read_dir` darauf laese etwas anderes.
        assert!(nur_freie_orte("C:", Some("aus")).is_err());
        // Ohne die Einstellung bleibt alles, wie es war.
        assert!(nur_freie_orte("C:\\", Some("lesen")).is_ok());
        assert!(nur_freie_orte("C:\\", None).is_ok());
        // Und ein eigener Ordner bleibt offen: die Einstellung heisst
        // "Systembereich" und nicht "Rechner".
        let eigen = freier_ordner("wurzeltest");
        assert!(nur_freie_orte(&eigen.to_string_lossy(), Some("aus")).is_ok());
    }

    #[test]
    fn der_lauf_laesst_den_systembereich_aus() {
        // Die Groessenanalyse wird nicht abgewiesen, sondern laesst aus —
        // "was frisst meinen Platz auf C:" bleibt beantwortbar, nur ohne
        // Windows. Geprueft wird die Entscheidung selbst; ein echter Lauf
        // ueber `C:\` braeuchte das volle Zeitbudget.
        let windows = Path::new(r"C:\Windows");
        assert!(ausgelassen(windows, true));
        // Auch eine Ebene tiefer: `C:\ProgramData` ist frei, `Microsoft`
        // darunter nicht — ein Lauf ueber `C:\` kommt genau dort vorbei.
        if let Some(daten) = std::env::var_os("ProgramData") {
            let ort = PathBuf::from(daten).join("Microsoft");
            assert!(ausgelassen(&ort, true), "{}", ort.display());
        }
        // Ohne die Einstellung aendert sich an keiner Rechnung etwas.
        assert!(!ausgelassen(windows, false));
        // Und die eigenen Ordner bleiben drin — auch die, die flach genug
        // liegen, dass wirklich nach der Zone gefragt wird.
        assert!(!ausgelassen(Path::new(r"D:\Projekte"), true));
        assert!(!ausgelassen(&freier_ordner("lauftest"), true));
    }

    #[test]
    fn zwei_punkte_oeffnen_den_riegel_nicht() {
        // `C:\Windows\..` ist die Laufwerkswurzel — `read_dir` darauf listet
        // `C:\`. Roh gezaehlt hat der Pfad aber vier Bestandteile und lag
        // damit ueber dem Deckel von `birgt_systemorte`: der Riegel fiel aus,
        // und die Auflistung zeigte trotz "aus" genau das, was
        // `aus_haelt_auch_die_laufwerkswurzel_zu` verbietet — zwei Punkte
        // waren der Schluessel.
        let fehler = ohne_app(&serde_json::json!({
            "aktion": "verzeichnis",
            "pfad": r"C:\Windows\..",
            "systembereich": "aus",
        }))
        .unwrap_err();
        assert!(fehler.contains("Systembereich"), "{fehler}");
        assert!(nur_freie_orte(r"C:\Windows\..", Some("aus")).is_err());
        assert!(nur_freie_orte(r"C:\Users\..", Some("aus")).is_err());

        // Im Lauf haengt derselbe Riegel an der Wurzel: sie wird einmal
        // aufgeloest, bevor der erste Ordner gelesen wird, und alle Kindpfade
        // wachsen daraus. Ohne diese eine Aufloesung hiessen die Kinder von
        // `C:\Windows\..` eben `C:\Windows\..\Windows` — roh fuenf
        // Bestandteile, nie unter dem Deckel, nie ausgelassen: der Lauf stieg
        // in Windows und in fremde Profile ab und gab sich dabei als
        // vollstaendig aus. Gezeigt wird die Aufloesung am genannten Pfad;
        // ein echter Lauf ueber `C:\` braeuchte das ganze Zeitbudget.
        let wurzel = freier_ordner("zweipunkte");
        fs::create_dir_all(wurzel.join("unten")).unwrap();
        let umweg = wurzel.join("unten").join("..");
        let ergebnis = groesste(&umweg.to_string_lossy(), true).unwrap();
        assert_eq!(
            ergebnis["pfad"].as_str().unwrap(),
            crate::zonen::aufloesen(&wurzel).display().to_string()
        );

        // Und was aus so einer Wurzel waechst, ist genau das, was der Deckel
        // erwischen muss: die obersten Kinder eines Laufs ueber `C:\`.
        assert!(ausgelassen(Path::new(r"C:\Windows"), true));
        assert!(ausgelassen(Path::new(r"C:\Users\Public"), true));
    }

    #[test]
    fn der_lauf_laesst_das_eigene_profil_nicht_aus() {
        let Some(profil) = std::env::var_os("USERPROFILE") else {
            return;
        };
        let profil = PathBuf::from(profil);
        let alle = profil.parent().expect("das Profil hat eine Wurzel");
        // Die Profilwurzel und das eigene Profil sind `Zone::System` — als
        // Ordner, die man nicht loescht. Wer sie deshalb in der
        // Groessenrechnung auslaesst, wirft den groessten Posten der Platte
        // weg und schreibt darunter, es fehlten nur Windows, Programmordner,
        // Bootdateien und *fremde* Profile.
        assert!(!ausgelassen(alle, true), "{}", alle.display());
        assert!(!ausgelassen(&profil, true), "{}", profil.display());
        // Die Gegenprobe: das Profil nebenan bleibt draussen. Der Name ist
        // erfunden, weil kein Testrechner ein zweites Profil haben muss —
        // `zonen` ordnet ihn ueber den vorhandenen Elternordner ein.
        let fremd = alle.join("Nachbar-gibt-es-nicht-12345");
        assert!(ausgelassen(&fremd, true), "{}", fremd.display());
        // Und die eigenen Dateien bleiben in der Rechnung.
        assert!(!ausgelassen(&profil.join("Downloads"), true));
    }

    #[test]
    fn lesen_laesst_den_blick_zu() {
        // Der Standard und damit der Zustand vor dem 23.08.2026: hineinsehen
        // ja, anfassen nein. Ein Fehlschlag hier waere eine stille
        // Verschaerfung von etwas, das laeuft.
        let ergebnis = ohne_app(&serde_json::json!({
            "aktion": "verzeichnis",
            "pfad": r"C:\Windows",
            "systembereich": "lesen",
        }));
        assert!(ergebnis.is_ok(), "{ergebnis:?}");
    }

    #[test]
    fn ein_fehlendes_feld_schneidet_keine_auskunft_ab() {
        let ergebnis = ohne_app(&serde_json::json!({
            "aktion": "verzeichnis", "pfad": r"C:\Windows",
        }));
        assert!(ergebnis.is_ok(), "{ergebnis:?}");
    }

    #[test]
    fn aus_gilt_nur_fuer_den_systembereich() {
        // Die eigenen Dateien bleiben sichtbar — die Einstellung heisst
        // "Systembereich" und nicht "Rechner".
        let eigen = std::env::temp_dir();
        let ergebnis = ohne_app(&serde_json::json!({
            "aktion": "verzeichnis",
            "pfad": eigen.display().to_string(),
            "systembereich": "aus",
        }));
        assert!(ergebnis.is_ok(), "{ergebnis:?}");
    }

    #[test]
    fn lesend_bleibt_lesend() {
        // Die Zusage dieses Moduls ist mechanisch nicht erzwingbar (std::fs
        // kennt keine Lese-Ansicht), also haelt dieser Test sie am Quelltext
        // fest: vor dem Testmodul kommt kein schreibender Aufruf vor.
        let quelle = include_str!("system.rs");
        let code = quelle.split("#[cfg(test)]").next().unwrap();
        for verboten in [
            "fs::write",
            "File::create",
            "File::options",
            "OpenOptions",
            "remove_file",
            "remove_dir",
            "fs::rename",
            "create_dir",
            "fs::copy",
            "hard_link",
            "set_permissions",
        ] {
            assert!(
                !code.contains(verboten),
                "Schreibender Aufruf im Lesemodul: {verboten}"
            );
        }
    }
}
