//! Legt Bytes dort ab, wo der Mensch es im Speichern-Dialog sagt: entschlüsselte
//! Tresor-Dateien und den Datenexport.
//!
//! Den Pfad bestimmt nur der Dialog, nie das Fenster: ein Befehl, der Bytes an
//! einen Pfad aus JS schreibt, wäre ein Schreibzugriff auf die ganze Platte für
//! jedes Skript im WebView. Dateien können Gigabyte groß sein, deshalb kommen
//! die Bytes in Teilen: `start` öffnet Dialog und Datei, `teil` hängt einen
//! Chunk an, `ende` schließt oder bricht ab.
//!
//! Android kennt keinen rohen IPC-Körper (Tauri liefert dort immer JSON) und
//! statt eines Pfads eine `content://`-Adresse. Die Teile reisen deshalb als
//! Base64, und geöffnet wird über das fs-Plugin. Dem Fenster gibt das Plugin
//! keine Rechte: es steht in keiner Capability.

use std::collections::HashMap;
use std::fs::File;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

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
    /// Nur am Desktop: damit ein Abbruch keine halbe Datei liegen lässt.
    pfad: Option<PathBuf>,
}

#[derive(Default)]
pub struct Offen {
    naechste: Mutex<u64>,
    vorgaenge: Mutex<HashMap<u64, Vorgang>>,
}

/// Der vorgeschlagene Name. Pfadteile, Steuerzeichen und die
/// unter Windows verbotenen Zeichen fallen weg; Umlaute und Leerzeichen bleiben.
pub fn sicherer_dateiname(vorschlag: &str) -> String {
    let name: String = vorschlag
        .chars()
        .filter(|z| !z.is_control() && !matches!(z, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|'))
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
    let pfad = match &ziel {
        FilePath::Path(pfad) => Some(pfad.clone()),
        FilePath::Url(_) => None,
    };
    let mut optionen = OpenOptions::new();
    optionen.write(true).create(true).truncate(true);
    let datei = app
        .fs()
        .open(ziel, optionen)
        .map_err(|_| "An diesem Ort kann die App nicht speichern.".to_string())?;

    let mut naechste = offen.naechste.lock().map_err(|_| "gesperrt")?;
    *naechste += 1;
    let id = *naechste;
    offen
        .vorgaenge
        .lock()
        .map_err(|_| "gesperrt")?
        .insert(id, Vorgang { datei, pfad });
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
        verwerfen(laufend);
        return Err(format!("Speichern fehlgeschlagen: {fehler}"));
    }
    offen.vorgaenge.lock().map_err(|_| "gesperrt")?.insert(vorgang, laufend);
    Ok(())
}

/// Schließt die Datei. Mit `abbrechen` wird sie am Desktop wieder gelöscht.
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
        verwerfen(vorgang);
        return Ok(());
    }
    tauri::async_runtime::spawn_blocking(move || vorgang.datei.sync_all())
        .await
        .map_err(|fehler| fehler.to_string())?
        .map_err(|fehler| format!("Speichern fehlgeschlagen: {fehler}"))
}

fn verwerfen(vorgang: Vorgang) {
    drop(vorgang.datei);
    if let Some(pfad) = vorgang.pfad {
        let _ = std::fs::remove_file(pfad);
    }
}

#[cfg(test)]
mod tests {
    use super::sicherer_dateiname;

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
