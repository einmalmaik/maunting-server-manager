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
//! Die Bitte um die Uebernahme ist der einzige Auftrag, der hier gar nichts
//! tut: sie geht als Ereignis an die Oberflaeche, der Mensch entscheidet, und
//! erst seine Antwort meldet das Ergebnis. Deshalb liefert sie `None` statt
//! eines Ergebnisses.

use std::path::PathBuf;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};

use crate::sandbox;
use crate::system;
use crate::uebernahme;

/// Das Ereignis, mit dem die Oberflaeche die Bestaetigungskarte zeigt.
pub const EREIGNIS_UEBERNAHME: &str = "mss:uebernahme-anfrage";

/// Was ein ausgefuehrter Auftrag zurueckgibt. `None` heisst: das Ergebnis
/// kommt spaeter (Uebernahme-Anfrage), die Oberflaeche meldet dann selbst.
pub type Ergebnis = Option<Value>;

pub fn ausfuehren(
    app: &AppHandle,
    sandbox_pfad: Option<PathBuf>,
    werkzeug: &str,
    argumente: &Value,
) -> Result<Ergebnis, String> {
    match werkzeug {
        "desktop_dateien" => dateien(sandbox_pfad, argumente).map(Some),
        "desktop_launch_app" => starten(app, argumente).map(Some),
        "desktop_system" => system::ausfuehren(argumente).map(Some),
        "desktop_steuern" => uebernahme::steuern(argumente).map(Some),
        "desktop_takeover_control" => {
            let minuten = argumente["minuten"]
                .as_u64()
                .unwrap_or(5)
                .clamp(1, uebernahme::MAX_MINUTEN);
            let anliegen = argumente["anliegen"].as_str().unwrap_or("").to_string();
            app.emit(
                EREIGNIS_UEBERNAHME,
                json!({ "anliegen": anliegen, "minuten": minuten }),
            )
            .map_err(|e| format!("Karte nicht anzeigbar: {e}"))?;
            Ok(None)
        }
        andere => Err(format!("Unbekanntes Werkzeug: '{andere}'")),
    }
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
