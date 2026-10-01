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
//! Android kennt keinen rohen IPC-Körper (Tauri liefert dort immer JSON) und
//! statt eines Pfads eine `content://`-Adresse. Die Teile reisen deshalb als
//! Base64, und geöffnet wird über das fs-Plugin. Dem Fenster gibt das Plugin
//! keine Rechte: es steht in keiner Capability. Eine Adresse lässt sich weder
//! umbenennen noch über das Plugin löschen: ein Abbruch leert sie auf null Byte.

use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::Engine;
use tauri::{Manager, WebviewWindow};
use tauri_plugin_dialog::{DialogExt, FilePath};
use tauri_plugin_fs::{FsExt, OpenOptions};

/// Mehr gleichzeitige Speichervorgänge braucht niemand.
const HOECHSTENS_OFFEN: usize = 4;
/// Ein Teil als Base64; die Aufrufer schicken 1 MiB.
const HOECHSTENS_TEIL: usize = 12 * 1024 * 1024;

struct Vorgang {
    datei: File,
    ablage: Ablage,
}

enum Ablage {
    /// Desktop: geschrieben wird in `teil` neben dem Ziel, umbenannt am Ende.
    Pfad { teil: PathBuf, ziel: PathBuf },
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
pub fn sicherer_dateiname(vorschlag: &str) -> String {
    let name: String = vorschlag
        .chars()
        .filter(|z| {
            !z.is_control()
                && !ist_unsichtbar(*z)
                && !matches!(z, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|')
        })
        .take(120)
        .collect();
    let name = name.trim().trim_start_matches('.').trim_end_matches(['.', ' ']);
    if name.is_empty() {
        "datei".to_string()
    } else {
        name.to_string()
    }
}

fn nur_hauptfenster(fenster: &WebviewWindow) -> Result<(), String> {
    if fenster.label() == "main" {
        Ok(())
    } else {
        Err("Nur das Hauptfenster darf speichern.".to_string())
    }
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
        FilePath::Path(pfad) => daneben_oeffnen(&pfad),
        adresse @ FilePath::Url(_) => {
            let mut optionen = OpenOptions::new();
            optionen.write(true).create(true).truncate(true);
            app.fs().open(adresse.clone(), optionen).map(|datei| Vorgang {
                datei,
                ablage: Ablage::Adresse(adresse),
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
    let (laufend, ergebnis) = tauri::async_runtime::spawn_blocking(move || {
        let ergebnis = laufend.datei.write_all(&inhalt);
        (laufend, ergebnis)
    })
    .await
    .map_err(|fehler| fehler.to_string())?;
    if let Err(fehler) = ergebnis {
        verwerfen_mit_app(&app, laufend);
        return Err(format!("Speichern fehlgeschlagen: {fehler}"));
    }
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

/// Legt die Teildatei neben `ziel` an. `create_new`, damit nie eine fremde
/// Datei überschrieben wird, die zufällig so heißt.
fn daneben_oeffnen(ziel: &Path) -> io::Result<Vorgang> {
    let name = ziel
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Ziel ohne Dateinamen"))?;
    let zufall = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let mut teilname = name.to_os_string();
    teilname.push(format!(".{zufall:x}.part"));
    let teil = ziel.with_file_name(teilname);
    let datei = std::fs::OpenOptions::new().write(true).create_new(true).open(&teil)?;
    Ok(Vorgang {
        datei,
        ablage: Ablage::Pfad { teil, ziel: ziel.to_path_buf() },
    })
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
        Ablage::Pfad { teil, ziel } => {
            drop(vorgang.datei);
            std::fs::rename(&teil, &ziel).map_err(|fehler| {
                let _ = std::fs::remove_file(&teil);
                (fehler, None)
            })
        }
    }
}

/// Lässt nichts Halbes liegen. Desktop: Teildatei löschen, das Ziel wurde nie
/// angefasst. Adresse: auf null Byte kürzen. `Err` heißt, die Adresse ließ
/// sich über die offene Datei nicht kürzen.
fn verwerfen(vorgang: Vorgang) -> Result<(), FilePath> {
    match vorgang.ablage {
        Ablage::Pfad { teil, .. } => {
            drop(vorgang.datei);
            let _ = std::fs::remove_file(teil);
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

    /// Eigener leerer Ordner je Test; kein geteilter Zustand, deshalb ohne Sperre.
    fn ordner(zweck: &str) -> PathBuf {
        let zufall = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let ordner = std::env::temp_dir().join(format!("msm-speichern-{zweck}-{zufall:x}"));
        std::fs::create_dir_all(&ordner).unwrap();
        ordner
    }

    fn inhalt(ordner: &Path) -> Vec<String> {
        let mut namen: Vec<String> = std::fs::read_dir(ordner)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        namen.sort();
        namen
    }

    #[test]
    fn abbruch_am_desktop_laesst_nichts_liegen() {
        let ordner = ordner("abbruch");
        let ziel = ordner.join("Mietvertrag.pdf");
        let mut vorgang = daneben_oeffnen(&ziel).unwrap();
        vorgang.datei.write_all(b"halber Klartext").unwrap();
        // Während des Schreibens steht am Ziel nichts.
        assert!(!ziel.exists());

        verwerfen(vorgang).unwrap();

        assert!(inhalt(&ordner).is_empty(), "liegt noch: {:?}", inhalt(&ordner));
        std::fs::remove_dir_all(ordner).unwrap();
    }

    #[test]
    fn abbruch_laesst_eine_vorhandene_zieldatei_stehen() {
        let ordner = ordner("vorhanden");
        let ziel = ordner.join("bericht.txt");
        std::fs::write(&ziel, b"alt").unwrap();
        let mut vorgang = daneben_oeffnen(&ziel).unwrap();
        vorgang.datei.write_all(b"neu, aber halb").unwrap();

        verwerfen(vorgang).unwrap();

        assert_eq!(std::fs::read(&ziel).unwrap(), b"alt");
        assert_eq!(inhalt(&ordner), vec!["bericht.txt".to_string()]);
        std::fs::remove_dir_all(ordner).unwrap();
    }

    #[test]
    fn ende_benennt_die_teildatei_aufs_ziel_um() {
        let ordner = ordner("ende");
        let ziel = ordner.join("foto.jpg");
        std::fs::write(&ziel, b"alt").unwrap();
        let mut vorgang = daneben_oeffnen(&ziel).unwrap();
        vorgang.datei.write_all(b"ganz").unwrap();

        abschliessen(vorgang).map_err(|(fehler, _)| fehler).unwrap();

        assert_eq!(std::fs::read(&ziel).unwrap(), b"ganz");
        assert_eq!(inhalt(&ordner), vec!["foto.jpg".to_string()]);
        std::fs::remove_dir_all(ordner).unwrap();
    }

    #[test]
    fn abbruch_an_einer_adresse_leert_das_ziel() {
        // Android: das fs-Plugin gibt für `content://` eine offene Datei; eine
        // gewöhnliche Datei steht hier dafür ein.
        let ordner = ordner("adresse");
        let ziel = ordner.join("export.zip");
        let datei = std::fs::OpenOptions::new().write(true).create(true).truncate(true).open(&ziel).unwrap();
        let adresse = FilePath::Url("content://com.android.providers.downloads.documents/document/7".parse().unwrap());
        let mut vorgang = Vorgang { datei, ablage: Ablage::Adresse(adresse) };
        vorgang.datei.write_all(b"halber Klartext").unwrap();

        assert!(verwerfen(vorgang).is_ok());

        assert_eq!(std::fs::metadata(&ziel).unwrap().len(), 0);
        std::fs::remove_dir_all(ordner).unwrap();
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
}
