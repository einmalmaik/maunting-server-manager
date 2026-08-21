//! Restlos verschwinden — in der Reihenfolge, in der es sicher ist.
//!
//! Der Windows-Uninstaller entfernt das Programm. Er entfernt **nicht**, was
//! die App im Betrieb angelegt hat: die Konfiguration, die Stimmaufnahmen des
//! Wake-Words und den Eintrag im Anmeldeinformations-Manager. Genau das ist
//! das Heikle daran — Sprachaufnahmen und ein Refresh-Token sollen nicht auf
//! einem Rechner zurueckbleiben, von dem jemand die App entfernt hat.
//!
//! Deshalb raeumt diese Datei zuerst auf und startet den Uninstaller erst
//! danach. Andersherum waere der Prozess mitten im Aufraeumen beendet worden.
//!
//! Was **nicht** angefasst wird: der Sandbox-Ordner. Er gehoert dem Benutzer,
//! nicht der App — dort liegen seine Dateien, und die verschwinden nicht,
//! weil er ein Programm deinstalliert. Genannt wird er trotzdem, damit er
//! nicht vergessen wird.

use tauri::{AppHandle, Manager};

/// Was das Aufraeumen erledigt hat. Die Oberflaeche zeigt es an, bevor der
/// Uninstaller startet — ein „alles weg" ohne Aufzaehlung waere eine
/// Behauptung.
#[derive(serde::Serialize)]
pub struct Aufraeumbericht {
    pub konfiguration_entfernt: bool,
    pub sprachdaten_entfernt: bool,
    pub tresor_geleert: bool,
    pub autostart_entfernt: bool,
    /// Der Ordner des Benutzers, den wir bewusst stehen lassen.
    pub sandbox_bleibt: Option<String>,
    /// Was nicht geklappt hat, im Klartext. Ein stiller Fehlschlag waere hier
    /// der schlimmste Ausgang: der Benutzer glaubte, seine Aufnahmen seien weg.
    pub fehler: Vec<String>,
}

/// Entfernt alle lokalen Spuren. Ruft **nicht** den Uninstaller — das ist ein
/// eigener Schritt, damit die Oberflaeche den Bericht vorher zeigen kann.
pub fn aufraeumen(app: &AppHandle) -> Aufraeumbericht {
    let mut fehler = Vec::new();
    let sandbox = crate::konfig::laden(app).ok().and_then(|k| k.sandbox_pfad);

    // Zuerst das Mikrofon: ein laufender Lauschthread haelt sonst Dateien
    // offen, die gleich geloescht werden sollen.
    let sprachdaten_entfernt = match crate::wakeword::zuruecksetzen(app) {
        Ok(()) => true,
        Err(meldung) => {
            // "nicht gefunden" ist kein Fehlschlag, sondern der Normalfall bei
            // jemandem, der das Wake-Word nie eingerichtet hat.
            if meldung.contains("os error 2") || meldung.contains("not found") {
                true
            } else {
                fehler.push(format!("Sprachdaten: {meldung}"));
                false
            }
        }
    };

    let tresor_geleert = match crate::geheimnisse::loeschen() {
        Ok(()) => true,
        Err(meldung) => {
            fehler.push(format!("Tresor: {meldung}"));
            false
        }
    };

    let autostart_entfernt = match autostart_aus(app) {
        Ok(()) => true,
        Err(meldung) => {
            fehler.push(format!("Autostart: {meldung}"));
            false
        }
    };

    // Die Konfiguration zuletzt: bis hierhin wurde der Sandbox-Pfad daraus
    // gelesen, und ein Fehlschlag oben soll noch nachvollziehbar sein.
    let konfiguration_entfernt = match app.path().app_local_data_dir() {
        Ok(verzeichnis) => match std::fs::remove_dir_all(&verzeichnis) {
            Ok(()) => true,
            Err(problem) if problem.kind() == std::io::ErrorKind::NotFound => true,
            Err(problem) => {
                fehler.push(format!("Konfiguration: {problem}"));
                false
            }
        },
        Err(problem) => {
            fehler.push(format!("Konfiguration: {problem}"));
            false
        }
    };

    Aufraeumbericht {
        konfiguration_entfernt,
        sprachdaten_entfernt,
        tresor_geleert,
        autostart_entfernt,
        sandbox_bleibt: sandbox,
        fehler,
    }
}

/// Startet den Windows-Uninstaller und beendet die App.
///
/// Gesucht wird `uninstall.exe` **neben der eigenen Programmdatei** — dort
/// legt der NSIS-Installer sie ab. Bewusst nicht ueber die Registry: die ist
/// in diesem Projekt tabu, und der Pfad daneben ist die einfachere Wahrheit.
/// Findet sich nichts, sagt die Meldung, wo es von Hand geht, statt still zu
/// scheitern.
pub fn uninstaller_starten(app: &AppHandle) -> Result<(), String> {
    let programm = std::env::current_exe()
        .map_err(|e| format!("Eigener Pfad unbekannt: {e}"))?;
    let ordner = programm
        .parent()
        .ok_or("Eigener Ordner unbekannt")?;
    let uninstaller = ordner.join("uninstall.exe");
    if !uninstaller.is_file() {
        return Err(format!(
            "Der Windows-Uninstaller liegt nicht neben der App ({}). Die \
             lokalen Daten sind entfernt; das Programm selbst deinstallierst \
             du ueber Einstellungen → Apps.",
            uninstaller.display()
        ));
    }
    std::process::Command::new(&uninstaller)
        .spawn()
        .map_err(|e| format!("Uninstaller nicht startbar: {e}"))?;
    // Beenden, nicht verstecken: der Uninstaller kann eine laufende Datei
    // nicht ersetzen, und `exit` geht am „Schliessen heisst Tray"-Verhalten
    // vorbei.
    app.exit(0);
    Ok(())
}

fn autostart_aus(app: &AppHandle) -> Result<(), String> {
    use tauri_plugin_autostart::ManagerExt;

    let autostart = app.autolaunch();
    match autostart.is_enabled() {
        Ok(true) => autostart.disable().map_err(|e| e.to_string()),
        // Nicht eingerichtet ist kein Fehler — es ist der Zielzustand.
        Ok(false) => Ok(()),
        Err(problem) => Err(problem.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn der_bericht_nennt_jeden_teil_einzeln() {
        // Ein `bool` fuer alles zusammen waere die Fassung, in der ein
        // gescheiterter Tresor unter einem gruenen Haken verschwindet.
        let bericht = Aufraeumbericht {
            konfiguration_entfernt: true,
            sprachdaten_entfernt: false,
            tresor_geleert: true,
            autostart_entfernt: true,
            sandbox_bleibt: Some("C:\\Users\\test\\Sandbox".into()),
            fehler: vec!["Sprachdaten: Zugriff verweigert".into()],
        };
        let json = serde_json::to_value(&bericht).unwrap();
        assert_eq!(json["sprachdaten_entfernt"], false);
        assert_eq!(json["tresor_geleert"], true);
        assert_eq!(json["fehler"][0], "Sprachdaten: Zugriff verweigert");
        // Der Ordner des Benutzers bleibt und wird benannt.
        assert_eq!(json["sandbox_bleibt"], "C:\\Users\\test\\Sandbox");
    }
}
