//! Legt Bytes dort ab, wo der Mensch es im Speichern-Dialog sagt: entschlüsselte
//! Tresor-Dateien und den Datenexport.
//!
//! Den Pfad bestimmt nur der Dialog, nie das Fenster: ein Befehl, der Bytes an
//! einen Pfad aus JS schreibt, wäre ein Schreibzugriff auf die ganze Platte für
//! jedes Skript im WebView. Dateien können Gigabyte groß sein, deshalb kommen
//! die Bytes in Teilen: `start` öffnet Dialog und Datei, `teil` hängt einen
//! Chunk an, `ende` schließt oder bricht ab.
//!
//! Ein Abbruch (Tresor gesperrt, Fehler) darf keine halbe Klartextdatei am Ziel
//! lassen. Am Desktop wird deshalb in eine Datei daneben geschrieben
//! (`<name>.<zufall>.part`) und erst am Ende umbenannt; ein Abbruch löscht sie.
//!
//! Endet die App mitten im Speichern, bleibt die Teildatei liegen. Für jede
//! liegt deshalb eine Marke mit ihrem Pfad im App-Verzeichnis
//! (`speichern-offen/`), und der nächste Start löscht, worauf eine Marke zeigt.
//! Gelöscht wird nur, was die App selbst angelegt hat, nie eine Datei, die nur
//! so aussieht. Lädt das Fenster mitten im Speichern neu, kommt für den Vorgang
//! nie ein `ende`; nach `LIEGEN_GELASSEN` ohne Teil gilt er als verlassen.
//!
//! Android kennt keinen rohen IPC-Körper (Tauri liefert dort immer JSON) und
//! statt eines Pfads eine `content://`-Adresse. Die Teile reisen deshalb als
//! Base64, und geöffnet wird über das fs-Plugin. Dem Fenster gibt das Plugin
//! keine Rechte: es steht in keiner Capability. Eine Adresse lässt sich weder
//! umbenennen noch über das Plugin löschen: ein Abbruch leert sie auf null Byte.

use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use base64::Engine;
use tauri::{Manager, WebviewWindow};
use tauri_plugin_dialog::{DialogExt, FilePath};
use tauri_plugin_fs::{FsExt, OpenOptions};

/// Mehr gleichzeitige Speichervorgänge braucht niemand.
const HOECHSTENS_OFFEN: usize = 4;
/// Ein Teil als Base64; die Aufrufer schicken 1 MiB.
const HOECHSTENS_TEIL: usize = 12 * 1024 * 1024;
/// So lange ohne neuen Teil, und ein Vorgang gilt als verlassen. Ein Teil ist
/// 1 MiB; so lange braucht keiner, auch nicht über ein langsames Netz.
const LIEGEN_GELASSEN: Duration = Duration::from_secs(10 * 60);
/// Längster vorgeschlagener Name in Zeichen, die Endung zählt mit.
const HOECHSTENS_NAME: usize = 120;
/// Was hinter dem letzten Punkt länger ist, gilt nicht als Endung.
const HOECHSTENS_ENDUNG: usize = 16;
/// Unterordner im App-Verzeichnis, eine Marke je offener Teildatei.
const MARKEN: &str = "speichern-offen";

struct Vorgang {
    datei: File,
    ablage: Ablage,
    /// Wann zuletzt ein Teil kam (oder der Vorgang begann).
    zuletzt: Instant,
}

enum Ablage {
    /// Desktop: geschrieben wird in `teil` neben dem Ziel, umbenannt am Ende.
    /// `marke` nennt `teil`, bis es umbenannt oder gelöscht ist.
    Pfad { teil: PathBuf, ziel: PathBuf, marke: PathBuf },
    /// Android (`content://`): geschrieben wird direkt ins Ziel.
    Adresse(FilePath),
}

#[derive(Default)]
pub struct Offen {
    naechste: Mutex<u64>,
    vorgaenge: Mutex<HashMap<u64, Vorgang>>,
}

/// Unsichtbare Zeichen, die die Leserichtung umkehren oder Text verstecken.
/// Mit ihnen sähe `rechnung\u{202E}fdp.exe` im Dialog aus wie `rechnungexe.pdf`.
fn ist_unsichtbar(z: char) -> bool {
    matches!(
        z,
        '\u{00AD}'
            | '\u{034F}'
            | '\u{061C}'
            | '\u{115F}'..='\u{1160}'
            | '\u{17B4}'..='\u{17B5}'
            | '\u{180E}'
            | '\u{200B}'..='\u{200F}'
            | '\u{2028}'..='\u{202E}'
            | '\u{2060}'..='\u{206F}'
            | '\u{3164}'
            | '\u{FEFF}'
            | '\u{FFA0}'
            | '\u{FFF9}'..='\u{FFFB}'
            | '\u{1BCA0}'..='\u{1BCA3}'
            | '\u{1D173}'..='\u{1D17A}'
            | '\u{E0000}'..='\u{E007F}'
    )
}

/// Windows-Gerätenamen gelten mit jeder Endung (`aux.txt`).
fn ist_geraetename(name: &str) -> bool {
    let stamm = name.split('.').next().unwrap_or("").trim_end().to_ascii_uppercase();
    matches!(stamm.as_str(), "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$")
        || ((stamm.starts_with("COM") || stamm.starts_with("LPT"))
            && stamm.chars().count() == 4
            && stamm.chars().nth(3).is_some_and(|z| z.is_ascii_digit() || "¹²³".contains(z)))
}

/// Der vorgeschlagene Name. Pfadteile, Steuer- und Richtungszeichen und die
/// unter Windows verbotenen Zeichen fallen weg; Umlaute und Leerzeichen bleiben.
/// Ein zu langer Name wird am Stamm gekürzt, die Endung bleibt.
pub fn sicherer_dateiname(vorschlag: &str) -> String {
    let name: String = vorschlag
        .chars()
        .filter(|z| {
            !z.is_control()
                && !ist_unsichtbar(*z)
                && !matches!(z, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|')
        })
        .collect();
    let name = kuerzen(name.trim().trim_start_matches('.').trim_end_matches(['.', ' ']));
    if name.is_empty() {
        "datei".to_string()
    } else if ist_geraetename(&name) {
        kuerzen(&format!("_{name}"))
    } else {
        name
    }
}

/// Kürzt auf `HOECHSTENS_NAME` Zeichen und behält dabei die Endung.
fn kuerzen(name: &str) -> String {
    if name.chars().count() <= HOECHSTENS_NAME {
        return name.to_string();
    }
    if let Some((stamm, endung)) = name.rsplit_once('.') {
        let laenge = endung.chars().count();
        if (1..=HOECHSTENS_ENDUNG).contains(&laenge) {
            let stamm: String = stamm.chars().take(HOECHSTENS_NAME - 1 - laenge).collect();
            let stamm = stamm.trim_end_matches(['.', ' ']);
            if !stamm.is_empty() {
                return format!("{stamm}.{endung}");
            }
        }
    }
    let name: String = name.chars().take(HOECHSTENS_NAME).collect();
    name.trim_end_matches(['.', ' ']).to_string()
}

fn nur_hauptfenster(fenster: &WebviewWindow) -> Result<(), String> {
    if fenster.label() == "main" {
        Ok(())
    } else {
        Err("Nur das Hauptfenster darf speichern.".to_string())
    }
}

fn marken_ordner(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path().app_local_data_dir().ok().map(|basis| basis.join(MARKEN))
}

/// Beim Start der App: Teildateien eines früheren Laufs löschen. Läuft neben
/// dem Start her, ein Fehler hält ihn nicht auf.
pub fn waisen_beim_start(app: &tauri::AppHandle) {
    if let Some(marken) = marken_ordner(app) {
        std::thread::spawn(move || waisen_aufraeumen(&marken));
    }
}

/// Weg ist weg, auch wenn es schon vorher weg war.
fn entfernt(ergebnis: io::Result<()>) -> bool {
    match ergebnis {
        Ok(()) => true,
        Err(fehler) => fehler.kind() == io::ErrorKind::NotFound,
    }
}

/// Löscht jede Teildatei, auf die eine Marke zeigt, und danach die Marke.
/// Zeigt eine Marke auf etwas, das nicht wie eine eigene Teildatei aussieht
/// (absolut, `<name>.<hex>.part`, gewöhnliche Datei), fällt nur die Marke weg.
/// Lässt sich eine Teildatei nicht löschen, bleibt die Marke für den nächsten
/// Start.
fn waisen_aufraeumen(marken: &Path) {
    let Ok(eintraege) = std::fs::read_dir(marken) else {
        return;
    };
    for eintrag in eintraege.flatten() {
        let marke = eintrag.path();
        let zufall = eintrag.file_name().to_string_lossy().into_owned();
        let erledigt = match std::fs::read_to_string(&marke) {
            Ok(teil) if ist_teildatei(Path::new(&teil), &zufall) => {
                // Zeigt der Pfad heute noch dorthin, wo die App die Datei anlegte?
                // Ein Ordner im Pfad, der seitdem gegen eine Junction getauscht
                // wurde, führt woandershin.
                match std::fs::canonicalize(&teil) {
                    Ok(echt) if echt == Path::new(&teil) => match std::fs::symlink_metadata(&teil) {
                        Ok(art) if art.is_file() => entfernt(std::fs::remove_file(&teil)),
                        // Ordner oder Verknüpfung: nicht von der App.
                        Ok(_) => true,
                        Err(fehler) => fehler.kind() == io::ErrorKind::NotFound,
                    },
                    Ok(_) => true,
                    Err(fehler) => fehler.kind() == io::ErrorKind::NotFound,
                }
            }
            Ok(_) => true,
            // Kein Text: keine Marke dieser App.
            Err(fehler) => fehler.kind() == io::ErrorKind::InvalidData,
        };
        if erledigt {
            let _ = std::fs::remove_file(&marke);
        }
    }
}

/// Sieht der Pfad aus wie eine Teildatei, die `daneben_oeffnen` anlegt?
fn ist_teildatei(pfad: &Path, marke: &str) -> bool {
    let Some(name) = pfad.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    pfad.is_absolute()
        && pfad
            .components()
            .all(|teil| !matches!(teil, std::path::Component::ParentDir | std::path::Component::CurDir))
        && name
            .strip_suffix(".part")
            .and_then(|rest| rest.rsplit_once('.'))
            .is_some_and(|(_, zufall)| {
                zufall == marke && zufall.len() >= 24 && zufall.chars().all(|z| z.is_ascii_hexdigit())
            })
}

/// Nimmt Vorgänge heraus, für die seit `LIEGEN_GELASSEN` kein Teil kam. Für sie
/// gibt es kein Fenster mehr, das sie beendet (neu geladen oder abgestürzt).
/// Sie belegten bis zum Ende der App einen Platz in `HOECHSTENS_OFFEN`, und ihr
/// halber Klartext blieb so lange liegen.
fn liegengebliebene(vorgaenge: &mut HashMap<u64, Vorgang>, jetzt: Instant) -> Vec<Vorgang> {
    let alt: Vec<u64> = vorgaenge
        .iter()
        .filter(|(_, vorgang)| jetzt.saturating_duration_since(vorgang.zuletzt) > LIEGEN_GELASSEN)
        .map(|(id, _)| *id)
        .collect();
    alt.into_iter().filter_map(|id| vorgaenge.remove(&id)).collect()
}

/// Fragt nach dem Ziel und öffnet es. `None` heißt: abgebrochen.
#[tauri::command(async)]
pub async fn datei_speichern_start(
    app: tauri::AppHandle,
    window: WebviewWindow,
    name: String,
) -> Result<Option<u64>, String> {
    nur_hauptfenster(&window)?;
    let offen = app.state::<Offen>();
    let verlassen = {
        let mut vorgaenge = offen.vorgaenge.lock().map_err(|_| "gesperrt")?;
        liegengebliebene(&mut vorgaenge, Instant::now())
    };
    for vorgang in verlassen {
        verwerfen_mit_app(&app, vorgang);
    }
    if offen.vorgaenge.lock().map_err(|_| "gesperrt")?.len() >= HOECHSTENS_OFFEN {
        return Err("Es laufen schon zu viele Speichervorgänge.".to_string());
    }
    let dateiname = sicherer_dateiname(&name);
    let dialog = app.clone();
    // Der Dialog blockiert bis zur Antwort, deshalb nicht auf dem Hauptthread.
    let ziel = tauri::async_runtime::spawn_blocking(move || {
        dialog.dialog().file().set_file_name(dateiname).blocking_save_file()
    })
    .await
    .map_err(|fehler| fehler.to_string())?;
    let Some(ziel) = ziel else {
        return Ok(None);
    };
    let geoeffnet = match ziel {
        FilePath::Path(pfad) => match marken_ordner(&app) {
            Some(marken) => daneben_oeffnen(&pfad, &marken),
            None => Err(io::Error::other("App-Verzeichnis unbekannt")),
        },
        adresse @ FilePath::Url(_) => {
            let mut optionen = OpenOptions::new();
            optionen.write(true).create(true).truncate(true);
            app.fs().open(adresse.clone(), optionen).map(|datei| Vorgang {
                datei,
                ablage: Ablage::Adresse(adresse),
                zuletzt: Instant::now(),
            })
        }
    };
    let neu = geoeffnet.map_err(|_| "An diesem Ort kann die App nicht speichern.".to_string())?;

    let mut naechste = offen.naechste.lock().map_err(|_| "gesperrt")?;
    *naechste += 1;
    let id = *naechste;
    offen
        .vorgaenge
        .lock()
        .map_err(|_| "gesperrt")?
        .insert(id, neu);
    Ok(Some(id))
}

/// Hängt einen Chunk an.
#[tauri::command(async)]
pub async fn datei_speichern_teil(
    app: tauri::AppHandle,
    window: WebviewWindow,
    vorgang: u64,
    teil: String,
) -> Result<(), String> {
    nur_hauptfenster(&window)?;
    if teil.len() > HOECHSTENS_TEIL {
        return Err("Der Teil ist zu groß.".to_string());
    }
    let inhalt = base64::engine::general_purpose::STANDARD
        .decode(teil)
        .map_err(|_| "Der Teil kam nicht als Base64 an.".to_string())?;
    let offen = app.state::<Offen>();
    // Herausnehmen, schreiben, zurücklegen: die Sperre hält nie während des Schreibens.
    let mut laufend = offen
        .vorgaenge
        .lock()
        .map_err(|_| "gesperrt")?
        .remove(&vorgang)
        .ok_or("Dieser Speichervorgang ist nicht offen.")?;
    let (mut laufend, ergebnis) = tauri::async_runtime::spawn_blocking(move || {
        let ergebnis = laufend.datei.write_all(&inhalt);
        (laufend, ergebnis)
    })
    .await
    .map_err(|fehler| fehler.to_string())?;
    if let Err(fehler) = ergebnis {
        verwerfen_mit_app(&app, laufend);
        return Err(format!("Speichern fehlgeschlagen: {fehler}"));
    }
    laufend.zuletzt = Instant::now();
    offen.vorgaenge.lock().map_err(|_| "gesperrt")?.insert(vorgang, laufend);
    Ok(())
}

/// Schließt die Datei ab. Mit `abbrechen` (oder wenn das Abschließen scheitert)
/// bleibt am Ziel nichts Halbes: am Desktop fällt die Teildatei weg, auf
/// Android wird die Adresse geleert.
#[tauri::command(async)]
pub async fn datei_speichern_ende(
    app: tauri::AppHandle,
    window: WebviewWindow,
    vorgang: u64,
    abbrechen: bool,
) -> Result<(), String> {
    nur_hauptfenster(&window)?;
    let offen = app.state::<Offen>();
    let Some(vorgang) = offen.vorgaenge.lock().map_err(|_| "gesperrt")?.remove(&vorgang) else {
        return Ok(());
    };
    if abbrechen {
        verwerfen_mit_app(&app, vorgang);
        return Ok(());
    }
    let fs = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        abschliessen(vorgang).map_err(|(fehler, rest)| {
            if let Some(rest) = rest {
                verwerfen_mit_app(&fs, rest);
            }
            format!("Speichern fehlgeschlagen: {fehler}")
        })
    })
    .await
    .map_err(|fehler| fehler.to_string())?
}

/// Legt die Teildatei neben `ziel` an und ihre Marke in `marken`. `create_new`,
/// damit nie eine fremde Datei überschrieben wird, die zufällig so heißt. Die
/// Marke entsteht erst nach der Teildatei und zeigt so nie auf etwas, das die
/// App nicht selbst angelegt hat. Lässt sie sich nicht schreiben, wird nicht
/// gespeichert: nach einem Absturz bliebe sonst Klartext ohne Marke liegen.
fn daneben_oeffnen(ziel: &Path, marken: &Path) -> io::Result<Vorgang> {
    static ZAEHLER: AtomicU64 = AtomicU64::new(0);
    let name = ziel
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Ziel ohne Dateinamen"))?;
    let zeit = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let zufall = format!("{zeit:x}{:08x}{:x}", std::process::id(), ZAEHLER.fetch_add(1, Ordering::Relaxed));
    let name = name
        .to_str()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Ziel ohne lesbaren Namen"))?;
    // Ein Name hat höchstens 255 UTF-16-Einheiten; die Endung `.<zufall>.part`
    // darf ein Ziel, das selbst passt, nicht darüber heben.
    let endung = format!(".{zufall}.part");
    let mut platz = 255usize.saturating_sub(endung.len());
    let mut teilname: String = name
        .chars()
        .take_while(|z| match platz.checked_sub(z.len_utf16()) {
            Some(rest) => {
                platz = rest;
                true
            }
            None => false,
        })
        .collect();
    teilname.push_str(&endung);
    let teil = ziel.with_file_name(teilname);
    if !teil.is_absolute() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "Ziel ohne absoluten Pfad"));
    }
    let mut optionen = std::fs::OpenOptions::new();
    optionen.write(true).create_new(true);
    // Windows: solange die Datei offen ist, löscht sie kein anderer Prozess,
    // auch nicht der Start einer zweiten App (nur FILE_SHARE_READ).
    #[cfg(windows)]
    std::os::windows::fs::OpenOptionsExt::share_mode(&mut optionen, 0x1);
    let datei = optionen.open(&teil)?;
    let marke = marken.join(&zufall);
    // Die Marke hält den aufgelösten Pfad; das Aufräumen löscht nur, wenn er
    // sich heute noch genauso auflöst.
    let pfad = std::fs::canonicalize(&teil).and_then(|echt| {
        echt.to_str()
            .map(str::to_string)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Ziel ohne lesbaren Pfad"))
    });
    let gemerkt = pfad.and_then(|pfad| std::fs::create_dir_all(marken).map(|_| pfad)).and_then(|pfad| {
        let mut notiz = std::fs::OpenOptions::new().write(true).create_new(true).open(&marke)?;
        notiz.write_all(pfad.as_bytes())?;
        notiz.sync_all()
    });
    if let Err(fehler) = gemerkt {
        drop(datei);
        let _ = std::fs::remove_file(&teil);
        let _ = std::fs::remove_file(&marke);
        return Err(fehler);
    }
    Ok(Vorgang {
        datei,
        ablage: Ablage::Pfad { teil, ziel: ziel.to_path_buf(), marke },
        zuletzt: Instant::now(),
    })
}

/// Löscht die Teildatei und danach ihre Marke. Scheitert das Löschen, bleibt
/// die Marke, und der nächste Start versucht es noch einmal.
fn teil_loeschen(teil: &Path, marke: &Path) {
    if entfernt(std::fs::remove_file(teil)) {
        let _ = std::fs::remove_file(marke);
    }
}

/// Schreibt fest und benennt am Desktop die Teildatei aufs Ziel um (ersetzt
/// eine vorhandene Datei, deren Überschreiben der Dialog schon bestätigt hat).
/// Scheitert es, kommt der Vorgang zum Verwerfen zurück, sofern noch etwas liegt.
fn abschliessen(vorgang: Vorgang) -> Result<(), (io::Error, Option<Vorgang>)> {
    if let Err(fehler) = vorgang.datei.sync_all() {
        return Err((fehler, Some(vorgang)));
    }
    match vorgang.ablage {
        Ablage::Adresse(_) => Ok(()),
        Ablage::Pfad { teil, ziel, marke } => {
            drop(vorgang.datei);
            match std::fs::rename(&teil, &ziel) {
                Ok(()) => {
                    let _ = std::fs::remove_file(&marke);
                    Ok(())
                }
                Err(fehler) => {
                    teil_loeschen(&teil, &marke);
                    Err((fehler, None))
                }
            }
        }
    }
}

/// Lässt nichts Halbes liegen. Desktop: Teildatei löschen, das Ziel wurde nie
/// angefasst. Adresse: auf null Byte kürzen. `Err` heißt, die Adresse ließ
/// sich über die offene Datei nicht kürzen.
fn verwerfen(vorgang: Vorgang) -> Result<(), FilePath> {
    match vorgang.ablage {
        Ablage::Pfad { teil, marke, .. } => {
            drop(vorgang.datei);
            teil_loeschen(&teil, &marke);
            Ok(())
        }
        Ablage::Adresse(adresse) => {
            let gekuerzt = vorgang.datei.set_len(0).and_then(|_| vorgang.datei.sync_all());
            drop(vorgang.datei);
            gekuerzt.map_err(|_| adresse)
        }
    }
}

/// Wie `verwerfen`. Lässt sich eine Adresse nicht über die offene Datei kürzen
/// (Anbieter ohne echte Datei dahinter), öffnet das fs-Plugin sie noch einmal
/// mit `truncate`, dann kürzt der Anbieter selbst.
fn verwerfen_mit_app(app: &tauri::AppHandle, vorgang: Vorgang) {
    if let Err(adresse) = verwerfen(vorgang) {
        let mut optionen = OpenOptions::new();
        optionen.write(true).truncate(true);
        let _ = app.fs().open(adresse, optionen);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Eigener leerer Ordner je Test, darin `ziel` (wohin gespeichert wird)
    /// und `marken` (steht für das Marken-Verzeichnis der App). Kein geteilter
    /// Zustand, deshalb ohne Sperre.
    fn ordner(zweck: &str) -> (PathBuf, PathBuf, PathBuf) {
        let zufall = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let basis = std::env::temp_dir().join(format!("msm-speichern-{zweck}-{zufall:x}"));
        let ziel = basis.join("ziel");
        std::fs::create_dir_all(&ziel).unwrap();
        (basis.clone(), ziel, basis.join("marken"))
    }

    fn inhalt(ordner: &Path) -> Vec<String> {
        let Ok(eintraege) = std::fs::read_dir(ordner) else {
            return Vec::new();
        };
        let mut namen: Vec<String> =
            eintraege.map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        namen.sort();
        namen
    }

    #[test]
    fn abbruch_am_desktop_laesst_nichts_liegen() {
        let (basis, ordner, marken) = ordner("abbruch");
        let ziel = ordner.join("Mietvertrag.pdf");
        let mut vorgang = daneben_oeffnen(&ziel, &marken).unwrap();
        vorgang.datei.write_all(b"halber Klartext").unwrap();
        // Während des Schreibens steht am Ziel nichts.
        assert!(!ziel.exists());

        verwerfen(vorgang).unwrap();

        assert!(inhalt(&ordner).is_empty(), "liegt noch: {:?}", inhalt(&ordner));
        assert!(inhalt(&marken).is_empty(), "Marke liegt noch: {:?}", inhalt(&marken));
        std::fs::remove_dir_all(basis).unwrap();
    }

    #[test]
    fn abbruch_laesst_eine_vorhandene_zieldatei_stehen() {
        let (basis, ordner, marken) = ordner("vorhanden");
        let ziel = ordner.join("bericht.txt");
        std::fs::write(&ziel, b"alt").unwrap();
        let mut vorgang = daneben_oeffnen(&ziel, &marken).unwrap();
        vorgang.datei.write_all(b"neu, aber halb").unwrap();

        verwerfen(vorgang).unwrap();

        assert_eq!(std::fs::read(&ziel).unwrap(), b"alt");
        assert_eq!(inhalt(&ordner), vec!["bericht.txt".to_string()]);
        std::fs::remove_dir_all(basis).unwrap();
    }

    #[test]
    fn ende_benennt_die_teildatei_aufs_ziel_um() {
        let (basis, ordner, marken) = ordner("ende");
        let ziel = ordner.join("foto.jpg");
        std::fs::write(&ziel, b"alt").unwrap();
        let mut vorgang = daneben_oeffnen(&ziel, &marken).unwrap();
        vorgang.datei.write_all(b"ganz").unwrap();

        abschliessen(vorgang).map_err(|(fehler, _)| fehler).unwrap();

        assert_eq!(std::fs::read(&ziel).unwrap(), b"ganz");
        assert_eq!(inhalt(&ordner), vec!["foto.jpg".to_string()]);
        assert!(inhalt(&marken).is_empty(), "Marke liegt noch: {:?}", inhalt(&marken));
        std::fs::remove_dir_all(basis).unwrap();
    }

    #[test]
    fn nach_einem_absturz_raeumt_der_naechste_start_die_teildatei_weg() {
        let (basis, ordner, marken) = ordner("absturz");
        let ziel = ordner.join("Steuer 2025.pdf");
        // Fremde Dateien, die genauso aussehen: ein Browser-Download und eine
        // Teildatei nach demselben Muster, die nicht von dieser App stammt.
        std::fs::write(ordner.join("film.mkv.part"), b"fremd").unwrap();
        std::fs::write(ordner.join("Steuer 2025.pdf.1a2b.part"), b"fremd").unwrap();
        let mut vorgang = daneben_oeffnen(&ziel, &marken).unwrap();
        vorgang.datei.write_all(b"halber Klartext").unwrap();
        // Die App endet mitten im Speichern: weder `ende` noch `verwerfen`.
        drop(vorgang);

        waisen_aufraeumen(&marken);

        assert_eq!(
            inhalt(&ordner),
            vec!["Steuer 2025.pdf.1a2b.part".to_string(), "film.mkv.part".to_string()]
        );
        assert!(inhalt(&marken).is_empty(), "Marke liegt noch: {:?}", inhalt(&marken));
        // Ein zweiter Lauf findet nichts mehr und scheitert nicht.
        waisen_aufraeumen(&marken);
        std::fs::remove_dir_all(basis).unwrap();
    }

    #[test]
    fn eine_marke_auf_eine_fremde_datei_loescht_nichts() {
        let (basis, ordner, marken) = ordner("fremdmarke");
        let wichtig = ordner.join("wichtig.txt");
        let unterordner = ordner.join("ordner.1a2b.part");
        std::fs::write(&wichtig, b"bleibt").unwrap();
        std::fs::create_dir_all(&unterordner).unwrap();
        std::fs::create_dir_all(&marken).unwrap();
        std::fs::write(marken.join("a"), wichtig.to_str().unwrap()).unwrap();
        std::fs::write(marken.join("b"), unterordner.to_str().unwrap()).unwrap();
        std::fs::write(marken.join("c"), "relativ.1a2b.part").unwrap();

        waisen_aufraeumen(&marken);

        assert_eq!(std::fs::read(&wichtig).unwrap(), b"bleibt");
        assert!(unterordner.is_dir());
        std::fs::remove_dir_all(basis).unwrap();
    }

    #[test]
    fn liegengebliebene_vorgaenge_sperren_das_speichern_nicht() {
        // Lädt das Fenster mitten im Speichern neu, kommt für diese Vorgänge
        // nie ein `ende`. Nach vier davon blieb Speichern bis zum Neustart gesperrt.
        let (basis, ordner, marken) = ordner("liegen");
        let mut offen = HashMap::new();
        for nummer in 0..HOECHSTENS_OFFEN as u64 {
            let mut vorgang = daneben_oeffnen(&ordner.join(format!("{nummer}.txt")), &marken).unwrap();
            vorgang.datei.write_all(b"halber Klartext").unwrap();
            offen.insert(nummer, vorgang);
        }
        let jetzt = Instant::now() + LIEGEN_GELASSEN + Duration::from_secs(1);
        let mut laufend = daneben_oeffnen(&ordner.join("laeuft.txt"), &marken).unwrap();
        laufend.zuletzt = jetzt;
        offen.insert(99, laufend);

        let weg = liegengebliebene(&mut offen, jetzt);

        assert_eq!(weg.len(), HOECHSTENS_OFFEN);
        assert_eq!(offen.keys().copied().collect::<Vec<_>>(), vec![99]);
        for vorgang in weg {
            verwerfen(vorgang).unwrap();
        }
        let liegt = inhalt(&ordner);
        assert_eq!(liegt.len(), 1, "liegt noch: {liegt:?}");
        assert!(liegt[0].starts_with("laeuft.txt."), "{liegt:?}");
        drop(offen);
        std::fs::remove_dir_all(basis).unwrap();
    }

    #[test]
    fn abbruch_an_einer_adresse_leert_das_ziel() {
        // Android: das fs-Plugin gibt für `content://` eine offene Datei; eine
        // gewöhnliche Datei steht hier dafür ein.
        let (basis, ordner, _) = ordner("adresse");
        let ziel = ordner.join("export.zip");
        let datei = std::fs::OpenOptions::new().write(true).create(true).truncate(true).open(&ziel).unwrap();
        let adresse = FilePath::Url("content://com.android.providers.downloads.documents/document/7".parse().unwrap());
        let mut vorgang = Vorgang { datei, ablage: Ablage::Adresse(adresse), zuletzt: Instant::now() };
        vorgang.datei.write_all(b"halber Klartext").unwrap();

        assert!(verwerfen(vorgang).is_ok());

        assert_eq!(std::fs::metadata(&ziel).unwrap().len(), 0);
        std::fs::remove_dir_all(basis).unwrap();
    }

    #[test]
    fn richtungszeichen_fallen_weg() {
        // Sähe sonst aus wie „rechnungexe.pdf“ und wäre ein Programm.
        assert_eq!(sicherer_dateiname("rechnung\u{202E}fdp.exe"), "rechnungfdp.exe");
        for zeichen in [
            '\u{202A}', '\u{202B}', '\u{202C}', '\u{202D}', '\u{202E}', '\u{2066}', '\u{2067}', '\u{2068}',
            '\u{2069}', '\u{200E}', '\u{200F}', '\u{061C}', '\u{200B}', '\u{FEFF}',
        ] {
            assert_eq!(sicherer_dateiname(&format!("a{zeichen}b.txt")), "ab.txt", "{:?}", zeichen);
        }
        assert_eq!(sicherer_dateiname("\u{2067}\u{2069}"), "datei");
        assert_eq!(sicherer_dateiname("a\u{7F}\u{85}b.txt"), "ab.txt");
    }

    #[test]
    fn pfadteile_fallen_weg() {
        assert_eq!(sicherer_dateiname("../../Windows/system32/x"), "Windowssystem32x");
        assert_eq!(sicherer_dateiname("C:\\Users\\a.txt"), "CUsersa.txt");
        assert_eq!(sicherer_dateiname("a\u{0}b\nc.pdf"), "abc.pdf");
    }

    #[test]
    fn umlaute_bleiben_und_nie_leer() {
        assert_eq!(sicherer_dateiname("Mietvertrag Müller.pdf"), "Mietvertrag Müller.pdf");
        assert_eq!(sicherer_dateiname("urlaub.jpg. "), "urlaub.jpg");
        assert_eq!(sicherer_dateiname("..."), "datei");
        assert_eq!(sicherer_dateiname(""), "datei");
        assert_eq!(sicherer_dateiname("a?b*.txt"), "ab.txt");
    }

    #[test]
    fn ein_langer_name_behaelt_seine_endung() {
        // Bis 02.10.2026 schnitt das Kürzen die Endung ab: aus „….pdf“ wurde
        // eine Datei ohne Endung, die kein Programm mehr öffnet.
        let name = sicherer_dateiname(&format!("{}.pdf", "ä".repeat(200)));
        assert_eq!(name, format!("{}.pdf", "ä".repeat(116)));

        // Leerzeichen am Ende des gekürzten Stamms fallen weg.
        let name = sicherer_dateiname(&format!("{} {}.jpeg", "a".repeat(114), "b".repeat(50)));
        assert_eq!(name, format!("{}.jpeg", "a".repeat(114)));

        // Richtungszeichen fallen auch aus einem langen Namen, bevor gezählt wird.
        let name = sicherer_dateiname(&format!("{}\u{202E}{}.exe", "a".repeat(100), "b".repeat(100)));
        assert_eq!(name, format!("{}{}.exe", "a".repeat(100), "b".repeat(16)));

        // Eine „Endung“ über 16 Zeichen ist keine: gekürzt wird wie bisher.
        let ohne = format!("{}.{}", "a".repeat(100), "x".repeat(40));
        assert_eq!(sicherer_dateiname(&ohne), ohne.chars().take(120).collect::<String>());
        // Kurze Namen bleiben, wie sie sind.
        assert_eq!(sicherer_dateiname("bericht.tar.gz"), "bericht.tar.gz");
    }
}

#[cfg(test)]
mod angriff {
    //! Negativtests aus der Sicherheitsprüfung vom 02.10.2026. Jeder Test hat
    //! seinen eigenen Ordner; geteilter Zustand ist nur der Zähler in
    //! `daneben_oeffnen` (atomar), deshalb ohne Test-Sperre.
    use super::*;

    fn ordner(zweck: &str) -> (PathBuf, PathBuf, PathBuf) {
        let zufall = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let temp = std::env::temp_dir().to_string_lossy().replace('/', "\\");
        let basis = PathBuf::from(temp).join(format!("msm-angriff-{zweck}-{zufall:x}"));
        let ziel = basis.join("ziel");
        std::fs::create_dir_all(&ziel).unwrap();
        (basis.clone(), ziel, basis.join("marken"))
    }

    fn marke(marken: &Path, name: &str, inhalt: &[u8]) -> PathBuf {
        std::fs::create_dir_all(marken).unwrap();
        let pfad = marken.join(name);
        std::fs::write(&pfad, inhalt).unwrap();
        pfad
    }

    fn weg(basis: PathBuf) {
        let _ = std::fs::remove_dir_all(basis);
    }

    fn inhalt(ordner: &Path) -> Vec<String> {
        let Ok(eintraege) = std::fs::read_dir(ordner) else {
            return Vec::new();
        };
        let mut namen: Vec<String> =
            eintraege.map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        namen.sort();
        namen
    }

    fn junction(link: &Path, ziel: &Path) -> bool {
        std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(link)
            .arg(ziel)
            .output()
            .map(|a| a.status.success())
            .unwrap_or(false)
    }

    // ------------------------------------------------------------------
    // Befunde (am Stand 1493f41a rot)
    // ------------------------------------------------------------------

    /// Eine Marke, die nicht von `daneben_oeffnen` stammt, zeigt auf eine
    /// fremde Datei, die nur nach dem Muster `<name>.<hex>.part` heißt.
    #[test]
    fn b1_marke_auf_fremde_teilartige_datei_loescht_sie_nicht() {
        let (basis, ziel, marken) = ordner("b1");
        let fremd = ziel.join("Diplomarbeit.docx.1a2b.part");
        std::fs::write(&fremd, b"fremd").unwrap();
        marke(&marken, "beliebig", fremd.to_str().unwrap().as_bytes());

        waisen_aufraeumen(&marken);

        let noch_da = fremd.exists();
        weg(basis);
        assert!(noch_da, "fremde Datei per Marke geloescht: {fremd:?}");
    }

    /// Dieselbe fremde Datei, erreicht über `..` im Markenpfad.
    #[test]
    fn b1_marke_mit_punktpunkt_loescht_nichts_ausserhalb() {
        let (basis, ziel, marken) = ordner("b1pp");
        let fremd = basis.join("Diplomarbeit.docx.1a2b.part");
        std::fs::write(&fremd, b"fremd").unwrap();
        let ueber = format!("{}\\gibtsnicht\\..\\..\\Diplomarbeit.docx.1a2b.part", ziel.display());
        marke(&marken, "pp", ueber.as_bytes());

        waisen_aufraeumen(&marken);

        let noch_da = fremd.exists();
        weg(basis);
        assert!(noch_da, "fremde Datei ueber `..` geloescht");
    }

    /// Dieselbe fremde Datei als Verbatim-Pfad `\\?\D:\…`.
    #[cfg(windows)]
    #[test]
    fn b1_marke_als_verbatim_pfad_loescht_nichts() {
        let (basis, ziel, marken) = ordner("b1vb");
        let fremd = ziel.join("bild.png.abcdef.part");
        std::fs::write(&fremd, b"fremd").unwrap();
        marke(&marken, "vb", format!("\\\\?\\{}", fremd.display()).as_bytes());

        waisen_aufraeumen(&marken);

        let noch_da = fremd.exists();
        weg(basis);
        assert!(noch_da, "fremde Datei ueber verbatim-Pfad geloescht");
    }

    /// Dieselbe fremde Datei über eine UNC-Freigabe (`\\localhost\D$\…`). Ist
    /// die Admin-Freigabe nicht erreichbar, sagt der Test das und prüft nichts.
    #[cfg(windows)]
    #[test]
    fn b1_marke_als_unc_pfad_loescht_nichts() {
        let (basis, ziel, marken) = ordner("b1unc");
        let fremd = ziel.join("bild.png.abcdef.part");
        std::fs::write(&fremd, b"fremd").unwrap();
        let lokal = fremd.to_str().unwrap().to_string();
        let unc = format!("\\\\localhost\\{}${}", &lokal[..1], &lokal[2..]);
        if std::fs::metadata(&unc).is_err() {
            eprintln!("UNC nicht erreichbar, uebersprungen: {unc}");
            weg(basis);
            return;
        }
        marke(&marken, "unc", unc.as_bytes());

        waisen_aufraeumen(&marken);

        let noch_da = fremd.exists();
        weg(basis);
        assert!(noch_da, "fremde Datei ueber UNC geloescht: {unc}");
    }

    /// Echte Marke der App (Absturz mitten im Speichern). Danach wird der
    /// Zielordner gegen eine Junction auf einen fremden Ordner getauscht, in dem
    /// eine Datei gleichen Namens liegt.
    #[cfg(windows)]
    #[test]
    fn b1_echte_marke_nach_ordnertausch_gegen_junction_loescht_nichts() {
        let (basis, ziel, marken) = ordner("b1j");
        let abzweig = ziel.join("abzweig");
        std::fs::create_dir_all(&abzweig).unwrap();
        let vorgang = daneben_oeffnen(&abzweig.join("x.pdf"), &marken).unwrap();
        let teilname = match &vorgang.ablage {
            Ablage::Pfad { teil, .. } => teil.file_name().unwrap().to_owned(),
            Ablage::Adresse(_) => unreachable!(),
        };
        drop(vorgang); // Absturz: weder ende noch verwerfen.
        std::fs::remove_dir_all(&abzweig).unwrap();
        let anderswo = basis.join("anderswo");
        std::fs::create_dir_all(&anderswo).unwrap();
        let fremd = anderswo.join(&teilname);
        std::fs::write(&fremd, b"fremd").unwrap();
        assert!(junction(&abzweig, &anderswo), "Junction liess sich nicht anlegen");

        waisen_aufraeumen(&marken);

        let noch_da = fremd.exists();
        let _ = std::fs::remove_dir(&abzweig);
        weg(basis);
        assert!(noch_da, "Datei hinter einer Junction geloescht");
    }

    /// Unsichtbare Formatzeichen, die nicht in `ist_unsichtbar` stehen.
    #[test]
    fn b2_weitere_unsichtbare_zeichen_fallen_weg() {
        let mut bleiben = Vec::new();
        for z in [
            '\u{00AD}', '\u{034F}', '\u{2028}', '\u{2029}', '\u{206A}', '\u{206F}', '\u{FFF9}',
            '\u{FFFB}', '\u{E0001}', '\u{E0041}', '\u{1D173}',
        ] {
            let name = sicherer_dateiname(&format!("a{z}b.txt"));
            if name != "ab.txt" {
                bleiben.push(format!("U+{:04X}", z as u32));
            }
        }
        assert!(bleiben.is_empty(), "bleiben im Dateinamen: {bleiben:?}");
    }

    /// Reservierte Windows-Gerätenamen bleiben im Vorschlag stehen.
    #[test]
    fn b3_reservierte_windows_namen_werden_nicht_vorgeschlagen() {
        const RESERVIERT: [&str; 22] = [
            "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        ];
        let mut durch = Vec::new();
        for vorschlag in ["CON", "nul", "aux.txt", "COM1.pdf", "lpt9.tar.gz", "Prn . "] {
            let name = sicherer_dateiname(vorschlag);
            let stamm = name.split('.').next().unwrap_or("").trim_end().to_ascii_uppercase();
            if RESERVIERT.contains(&stamm.as_str()) {
                durch.push(format!("{vorschlag:?} -> {name:?}"));
            }
        }
        assert!(durch.is_empty(), "reservierte Namen vorgeschlagen: {durch:?}");
    }

    /// Was passiert, wenn der Dialog trotzdem `…\CON` liefert?
    #[cfg(windows)]
    #[test]
    fn b3_reservierter_name_als_ziel_schreibt_in_keine_geraetedatei() {
        let (basis, ziel, marken) = ordner("b3z");
        let ergebnis = daneben_oeffnen(&ziel.join("CON"), &marken);
        let angelegt = inhalt(&ziel);
        if let Ok(mut vorgang) = ergebnis {
            let geschrieben = vorgang.datei.write_all(b"Klartext");
            let fertig = abschliessen(vorgang).map_err(|(f, _)| f);
            eprintln!("CON: angelegt {angelegt:?}, schreiben {geschrieben:?}, ende {fertig:?}, danach {:?}", inhalt(&ziel));
        } else {
            eprintln!("CON: daneben_oeffnen scheitert: {:?}", ergebnis.err());
        }
        let m = inhalt(&marken);
        weg(basis);
        assert!(m.is_empty(), "Marke bleibt liegen: {m:?}");
    }

    /// Ein Name, den `sicherer_dateiname` selbst liefert (Emoji: 2 UTF-16-
    /// Einheiten je Zeichen), lässt sich am Ziel anlegen, die Teildatei
    /// daneben aber nicht: `.<zufall>.part` hebt sie über 255 Einheiten.
    #[test]
    fn b4_vorgeschlagener_langer_name_laesst_sich_speichern() {
        let (basis, ziel, marken) = ordner("b4");
        let name = sicherer_dateiname(&format!("{}.jpg", "\u{1F600}".repeat(200)));
        let pfad = ziel.join(&name);
        // Das Betriebssystem nimmt den Namen selbst an.
        std::fs::write(&pfad, b"x").unwrap();
        std::fs::remove_file(&pfad).unwrap();

        let ergebnis = daneben_oeffnen(&pfad, &marken).map(|_| ());

        weg(basis);
        assert!(ergebnis.is_ok(), "Speichern unter dem Vorschlag scheitert: {ergebnis:?}");
    }

    /// Dasselbe mit einem langen Namen, den der Mensch im Dialog tippt.
    #[test]
    fn b4_im_dialog_getippter_langer_name_laesst_sich_speichern() {
        let (basis, ziel, marken) = ordner("b4d");
        let pfad = ziel.join(format!("{}.pdf", "a".repeat(240)));
        std::fs::write(&pfad, b"x").unwrap();
        std::fs::remove_file(&pfad).unwrap();

        let ergebnis = daneben_oeffnen(&pfad, &marken).map(|_| ());

        weg(basis);
        assert!(ergebnis.is_ok(), "Speichern unter 244 Zeichen scheitert: {ergebnis:?}");
    }

    /// Eine Marke, die kein UTF-8 ist, bleibt bei jedem Start liegen.
    #[test]
    fn b5_kaputte_marke_bleibt_nicht_ewig_liegen() {
        let (basis, _ziel, marken) = ordner("b5");
        let kaputt = marke(&marken, "kaputt", &[0xff, 0xfe, 0x00, 0xc3]);

        waisen_aufraeumen(&marken);
        waisen_aufraeumen(&marken);

        let liegt = kaputt.exists();
        weg(basis);
        assert!(!liegt, "kaputte Marke liegt nach zwei Starts noch");
    }

    // ------------------------------------------------------------------
    // Geprüft, hält
    // ------------------------------------------------------------------

    #[test]
    fn h_leere_riesige_und_umbrochene_marken_loeschen_nichts() {
        let (basis, ziel, marken) = ordner("hm");
        let fremd = ziel.join("a.txt");
        std::fs::write(&fremd, b"bleibt").unwrap();
        marke(&marken, "leer", b"");
        marke(&marken, "riesig", &vec![b'a'; 8 * 1024 * 1024]);
        marke(&marken, "zeile", format!("{}\n", fremd.display()).as_bytes());
        marke(&marken, "relativ", b"..\\..\\x.1a2b.part");
        marke(&marken, "nur_part", format!("{}\\.part", ziel.display()).as_bytes());
        marke(&marken, "ohne_hex", format!("{}\\a.txt.xyz.part", ziel.display()).as_bytes());
        marke(&marken, "leerer_zufall", format!("{}\\a.txt..part", ziel.display()).as_bytes());
        std::fs::write(ziel.join("a.txt.xyz.part"), b"bleibt").unwrap();
        std::fs::write(ziel.join("a.txt..part"), b"bleibt").unwrap();

        waisen_aufraeumen(&marken);

        let liegt = inhalt(&ziel);
        let marken_rest = inhalt(&marken);
        weg(basis);
        assert_eq!(liegt, vec!["a.txt", "a.txt..part", "a.txt.xyz.part"]);
        assert!(marken_rest.is_empty(), "{marken_rest:?}");
    }

    #[test]
    fn h_marke_die_selbst_ein_ordner_ist_bleibt_und_loescht_nichts() {
        let (basis, ziel, marken) = ordner("hmo");
        std::fs::create_dir_all(marken.join("ordner")).unwrap();
        std::fs::write(ziel.join("a.txt"), b"x").unwrap();
        waisen_aufraeumen(&marken);
        let liegt = inhalt(&ziel);
        let m = inhalt(&marken);
        weg(basis);
        assert_eq!(liegt, vec!["a.txt"]);
        assert_eq!(m, vec!["ordner"]);
    }

    #[cfg(windows)]
    #[test]
    fn h_junction_die_wie_teildatei_heisst_wird_nicht_angefasst() {
        let (basis, ziel, marken) = ordner("hj");
        let anderswo = basis.join("anderswo");
        std::fs::create_dir_all(&anderswo).unwrap();
        std::fs::write(anderswo.join("wichtig.txt"), b"x").unwrap();
        let als_teil = ziel.join("x.pdf.1a2b.part");
        assert!(junction(&als_teil, &anderswo));
        marke(&marken, "j", als_teil.to_str().unwrap().as_bytes());
        waisen_aufraeumen(&marken);
        let junction_da = std::fs::symlink_metadata(&als_teil).is_ok();
        let wichtig = anderswo.join("wichtig.txt").exists();
        let _ = std::fs::remove_dir(&als_teil);
        weg(basis);
        assert!(junction_da && wichtig);
    }

    #[cfg(windows)]
    #[test]
    fn h_symlink_der_wie_teildatei_heisst_wird_nicht_angefasst() {
        let (basis, ziel, marken) = ordner("hs");
        let wichtig = basis.join("wichtig.txt");
        std::fs::write(&wichtig, b"x").unwrap();
        let als_teil = ziel.join("x.pdf.1a2b.part");
        if std::os::windows::fs::symlink_file(&wichtig, &als_teil).is_err() {
            eprintln!("Symlink braucht Rechte, uebersprungen");
            weg(basis);
            return;
        }
        marke(&marken, "s", als_teil.to_str().unwrap().as_bytes());
        waisen_aufraeumen(&marken);
        let link = std::fs::symlink_metadata(&als_teil).is_ok();
        let ziel_da = wichtig.exists();
        weg(basis);
        assert!(link && ziel_da);
    }

    /// Ein zweiter Start (zweite Instanz) räumt nicht weg, was die erste
    /// gerade schreibt.
    #[cfg(windows)]
    #[test]
    fn h_laufender_vorgang_ueberlebt_das_aufraeumen_einer_zweiten_instanz() {
        let (basis, ziel, marken) = ordner("hl");
        let mut vorgang = daneben_oeffnen(&ziel.join("laeuft.pdf"), &marken).unwrap();
        vorgang.datei.write_all(b"halb").unwrap();
        waisen_aufraeumen(&marken);
        vorgang.datei.write_all(b" ganz").unwrap();
        let m = inhalt(&marken).len();
        let ergebnis = abschliessen(vorgang).map_err(|(f, _)| f);
        let gelesen = std::fs::read(ziel.join("laeuft.pdf")).ok();
        let rest = inhalt(&marken);
        weg(basis);
        assert_eq!(m, 1);
        ergebnis.unwrap();
        assert_eq!(gelesen.as_deref(), Some(&b"halb ganz"[..]));
        assert!(rest.is_empty());
    }

    #[test]
    fn h_zwei_vorgaenge_auf_dasselbe_ziel() {
        let (basis, ziel, marken) = ordner("h2");
        let pfad = ziel.join("doppelt.txt");
        let mut a = daneben_oeffnen(&pfad, &marken).unwrap();
        let mut b = daneben_oeffnen(&pfad, &marken).unwrap();
        a.datei.write_all(b"A").unwrap();
        b.datei.write_all(b"B").unwrap();
        let ra = abschliessen(a).map_err(|(f, _)| f);
        let rb = abschliessen(b).map_err(|(f, _)| f);
        let liegt = inhalt(&ziel);
        let gelesen = std::fs::read(&pfad).unwrap();
        let m = inhalt(&marken);
        weg(basis);
        ra.unwrap();
        rb.unwrap();
        assert_eq!(liegt, vec!["doppelt.txt"]);
        assert_eq!(gelesen, b"B");
        assert!(m.is_empty());
    }

    #[test]
    fn h_verwerfen_und_teil_loeschen_doppelt() {
        let (basis, ziel, marken) = ordner("hd");
        let vorgang = daneben_oeffnen(&ziel.join("x.txt"), &marken).unwrap();
        let (teil, marke) = match &vorgang.ablage {
            Ablage::Pfad { teil, marke, .. } => (teil.clone(), marke.clone()),
            Ablage::Adresse(_) => unreachable!(),
        };
        verwerfen(vorgang).unwrap();
        teil_loeschen(&teil, &marke);
        waisen_aufraeumen(&marken);
        let liegt = inhalt(&ziel);
        let m = inhalt(&marken);
        weg(basis);
        assert!(liegt.is_empty() && m.is_empty());
    }

    #[test]
    fn h_relatives_oder_namenloses_ziel_legt_nichts_an() {
        let (basis, _ziel, marken) = ordner("hr");
        assert!(daneben_oeffnen(Path::new("relativ.txt"), &marken).is_err());
        assert!(daneben_oeffnen(Path::new("C:\\"), &marken).is_err());
        let relativ_angelegt = std::fs::read_dir(".")
            .unwrap()
            .flatten()
            .any(|e| e.file_name().to_string_lossy().starts_with("relativ.txt"));
        let m = inhalt(&marken);
        weg(basis);
        assert!(!relativ_angelegt);
        assert!(m.is_empty());
    }

    #[test]
    fn h_ohne_marke_kein_klartext() {
        let (basis, ziel, _) = ordner("hk");
        // Der Marken-Ordner ist eine Datei: die Marke lässt sich nicht anlegen.
        let marken = basis.join("blockiert");
        std::fs::write(&marken, b"x").unwrap();
        assert!(daneben_oeffnen(&ziel.join("x.txt"), &marken).is_err());
        let liegt = inhalt(&ziel);
        weg(basis);
        assert!(liegt.is_empty(), "{liegt:?}");
    }

    #[test]
    fn h_mehr_als_hoechstens_offen_liegengebliebene() {
        let (basis, ziel, marken) = ordner("hh");
        let mut offen = HashMap::new();
        for n in 0..(HOECHSTENS_OFFEN as u64 * 3) {
            offen.insert(n, daneben_oeffnen(&ziel.join(format!("{n}.txt")), &marken).unwrap());
        }
        // Zeitpunkt vor dem Anlegen: nichts fällt, keine Panik.
        let frueher = Instant::now().checked_sub(Duration::from_secs(1)).unwrap();
        assert!(liegengebliebene(&mut offen, frueher).is_empty());
        let spaeter = Instant::now() + LIEGEN_GELASSEN + Duration::from_secs(1);
        let alt = liegengebliebene(&mut offen, spaeter);
        assert_eq!(alt.len(), HOECHSTENS_OFFEN * 3);
        for v in alt {
            verwerfen(v).unwrap();
        }
        let liegt = inhalt(&ziel);
        let m = inhalt(&marken);
        weg(basis);
        assert!(liegt.is_empty() && m.is_empty());
    }

    #[test]
    fn h_dateinamen_invarianten() {
        let verboten = ['/', '\\', ':', '*', '?', '"', '<', '>', '|'];
        let mut faelle: Vec<String> = vec![
            String::new(),
            ".".into(),
            "....".into(),
            " . . ".into(),
            "\u{0}\u{1f}\u{7f}".into(),
            "CON".into(),
            "a.".into(),
            "a ".into(),
            "name.endungmitsiebzehn".into(),
            format!("{}.{}", "a".repeat(200), "b".repeat(10_000)),
            format!("{}.{}", "a".repeat(10_000), "pdf"),
            format!("{}.pdf", "e\u{0301}".repeat(200)),
            format!("{}.pdf", "\u{1F468}\u{200D}\u{1F469}".repeat(100)),
            format!("{}. .pdf", "a".repeat(300)),
            format!("{}{}", "a".repeat(119), "\u{0301}".repeat(10)),
            format!("{} .{}", "x".repeat(300), "y".repeat(16)),
            format!("{}..{}", ".".repeat(300), "pdf"),
            "..\\..\\..\\Windows\\win.ini".into(),
            "\\\\server\\share\\x".into(),
            "rechnung\u{202E}fdp.exe".into(),
        ];
        for n in 0..200u32 {
            faelle.push(format!("{}{}", "\u{00E4}".repeat(n as usize), ".x".repeat((n % 7) as usize)));
        }
        for vorschlag in faelle {
            let name = sicherer_dateiname(&vorschlag);
            assert!(!name.is_empty(), "{vorschlag:?}");
            assert!(name.chars().count() <= HOECHSTENS_NAME, "{vorschlag:?} -> {name:?}");
            assert!(!name.ends_with(['.', ' ']), "{vorschlag:?} -> {name:?}");
            assert!(!name.starts_with('.'), "{vorschlag:?} -> {name:?}");
            assert!(!name.contains(verboten), "{vorschlag:?} -> {name:?}");
            assert!(!name.chars().any(|z| z.is_control() || ist_unsichtbar(z)), "{vorschlag:?}");
        }
        // Kürzen am Kombinationszeichen: Zeichengrenze, keine Panik, Endung bleibt.
        let name = sicherer_dateiname(&format!("{}.pdf", "e\u{0301}".repeat(200)));
        assert!(name.ends_with(".pdf"));
        // Endung genau 16 bleibt, 17 nicht.
        let n16 = sicherer_dateiname(&format!("{}.{}", "a".repeat(200), "b".repeat(16)));
        assert!(n16.ends_with(&format!(".{}", "b".repeat(16))));
        let n17 = sicherer_dateiname(&format!("{}.{}", "a".repeat(200), "b".repeat(17)));
        assert_eq!(n17, "a".repeat(120));
    }
}
