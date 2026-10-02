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
        '\u{061C}'
            | '\u{180E}'
            | '\u{200B}'..='\u{200F}'
            | '\u{202A}'..='\u{202E}'
            | '\u{2060}'..='\u{2069}'
            | '\u{FEFF}'
    )
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
        let erledigt = match std::fs::read_to_string(&marke) {
            Ok(teil) if ist_teildatei(Path::new(&teil)) => match std::fs::symlink_metadata(&teil) {
                Ok(art) if art.is_file() => entfernt(std::fs::remove_file(&teil)),
                // Ordner oder Verknüpfung: nicht von der App.
                Ok(_) => true,
                Err(fehler) => fehler.kind() == io::ErrorKind::NotFound,
            },
            Ok(_) => true,
            Err(_) => false,
        };
        if erledigt {
            let _ = std::fs::remove_file(&marke);
        }
    }
}

/// Sieht der Pfad aus wie eine Teildatei, die `daneben_oeffnen` anlegt?
fn ist_teildatei(pfad: &Path) -> bool {
    let Some(name) = pfad.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    pfad.is_absolute()
        && name
            .strip_suffix(".part")
            .and_then(|rest| rest.rsplit_once('.'))
            .is_some_and(|(_, zufall)| !zufall.is_empty() && zufall.chars().all(|z| z.is_ascii_hexdigit()))
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
    let mut teilname = name.to_os_string();
    teilname.push(format!(".{zufall}.part"));
    let teil = ziel.with_file_name(teilname);
    // Die Marke hält den Pfad als Text; ohne absoluten UTF-8-Pfad ginge das nicht.
    let pfad = teil
        .to_str()
        .filter(|_| teil.is_absolute())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Ziel ohne lesbaren Pfad"))?
        .to_string();
    let mut optionen = std::fs::OpenOptions::new();
    optionen.write(true).create_new(true);
    // Windows: solange die Datei offen ist, löscht sie kein anderer Prozess,
    // auch nicht der Start einer zweiten App (nur FILE_SHARE_READ).
    #[cfg(windows)]
    std::os::windows::fs::OpenOptionsExt::share_mode(&mut optionen, 0x1);
    let datei = optionen.open(&teil)?;
    let marke = marken.join(&zufall);
    let gemerkt = std::fs::create_dir_all(marken).and_then(|_| {
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
