//! Der Sandbox-Ordner — die eine Grenze, die niemand sonst zieht.
//!
//! Das Panel kann sie nicht pruefen: es kennt weder den Pfad noch das
//! Dateisystem des Benutzers. Die KI kann sie nicht pruefen, sie schlaegt nur
//! Pfade vor. Also steht sie hier, in genau einer Funktion, und alles andere
//! in dieser Datei geht durch sie hindurch.
//!
//! Der Ausbruchsweg ist nicht `..` — das faengt jede Normalisierung. Es sind
//! **Verknuepfungen**: ein Symlink oder eine Junction im Sandbox-Ordner zeigt
//! nach `C:\Windows`, und ein Vergleich auf der Eingabe saehe nur einen
//! harmlosen relativen Pfad. Deshalb wird ausschliesslich die *kanonisierte*
//! Form verglichen, bei der das Betriebssystem alle Verknuepfungen bereits
//! aufgeloest hat.
//!
//! `dunce::canonicalize` statt `std::fs::canonicalize`: letzteres liefert unter
//! Windows die Verbatim-Form `\\?\C:\...`, und ein `starts_with` gegen einen
//! normalen Pfad ist dann immer falsch — die Grenze waere eine, die nie greift.

use std::fs;
use std::path::{Component, Path, PathBuf};

use serde::Serialize;

/// Was eine gelesene Datei hoechstens an das Modell zurueckgibt. Darueber
/// wird gekuerzt und das ausdruecklich gemeldet — ein stilles Abschneiden
/// waere eine Datei, die das Modell fuer vollstaendig haelt.
const MAX_LESEZEICHEN: usize = 60_000;
/// Wie viele Eintraege eine Auflistung nennt. Ein Ordner mit 20.000 Dateien
/// gehoert nicht in einen Prompt.
const MAX_EINTRAEGE: usize = 300;

#[derive(Debug, Serialize)]
pub struct Eintrag {
    pub name: String,
    pub ordner: bool,
    pub groesse: u64,
}

/// Warum ein Pfad nicht benutzt werden darf. Die Meldung geht als
/// Werkzeugergebnis an das Modell, also nennt sie den Grund und nicht nur
/// „geht nicht" — sonst probiert es dieselbe Sache dreimal.
pub fn grenzfehler(pfad: &str) -> String {
    format!(
        "Ausserhalb des Sandbox-Ordners: '{pfad}'. Der Benutzer hat genau \
         einen Ordner freigegeben; alles ausserhalb ist gesperrt, auch ueber \
         Verknuepfungen. Bleib bei relativen Pfaden innerhalb des Ordners."
    )
}

/// Loest einen relativen Pfad im Sandbox-Ordner auf — oder lehnt ihn ab.
///
/// `muss_existieren = false` ist der Fall „Datei anlegen": dann wird das
/// **Elternverzeichnis** kanonisiert und geprueft, denn ein noch nicht
/// existierender Pfad laesst sich nicht aufloesen. Der Dateiname selbst darf
/// dabei keine eigenen Pfadanteile mehr tragen.
pub fn aufloesen(wurzel: &Path, relativ: &str, muss_existieren: bool) -> Result<PathBuf, String> {
    let wurzel = dunce::canonicalize(wurzel)
        .map_err(|e| format!("Sandbox-Ordner nicht lesbar: {e}"))?;

    // Absolute Pfade und Laufwerksangaben gar nicht erst zusammensetzen:
    // `wurzel.join("C:/Windows")` ergaebe unter Windows `C:/Windows`.
    let angefragt = Path::new(relativ);
    if angefragt.is_absolute()
        || angefragt
            .components()
            .any(|teil| matches!(teil, Component::Prefix(_) | Component::RootDir))
    {
        return Err(grenzfehler(relativ));
    }

    let ziel = wurzel.join(angefragt);
    if muss_existieren {
        let echt = dunce::canonicalize(&ziel)
            .map_err(|_| format!("Nicht gefunden: '{relativ}'"))?;
        if !echt.starts_with(&wurzel) {
            return Err(grenzfehler(relativ));
        }
        return Ok(echt);
    }

    let eltern = ziel.parent().ok_or_else(|| grenzfehler(relativ))?;
    let dateiname = ziel
        .file_name()
        .ok_or_else(|| format!("Kein Dateiname in '{relativ}'"))?;
    // Das Elternverzeichnis muss es geben — sonst legt der Aufrufer es an,
    // und zwar ueber denselben Weg (`aktion = "schreiben"` legt nur die Datei
    // an, nicht den halben Baum).
    let echte_eltern = dunce::canonicalize(eltern)
        .map_err(|_| format!("Der Ordner zu '{relativ}' existiert nicht"))?;
    if !echte_eltern.starts_with(&wurzel) {
        return Err(grenzfehler(relativ));
    }
    Ok(echte_eltern.join(dateiname))
}

pub fn auflisten(wurzel: &Path, relativ: &str) -> Result<serde_json::Value, String> {
    let ordner = if relativ.trim().is_empty() {
        dunce::canonicalize(wurzel).map_err(|e| format!("Sandbox-Ordner nicht lesbar: {e}"))?
    } else {
        aufloesen(wurzel, relativ, true)?
    };
    let lesen = fs::read_dir(&ordner).map_err(|e| format!("Ordner nicht lesbar: {e}"))?;

    let mut eintraege: Vec<Eintrag> = Vec::new();
    let mut uebersprungen = 0usize;
    for element in lesen.flatten() {
        if eintraege.len() >= MAX_EINTRAEGE {
            uebersprungen += 1;
            continue;
        }
        let daten = match element.metadata() {
            Ok(daten) => daten,
            Err(_) => continue,
        };
        eintraege.push(Eintrag {
            name: element.file_name().to_string_lossy().to_string(),
            ordner: daten.is_dir(),
            groesse: if daten.is_dir() { 0 } else { daten.len() },
        });
    }
    eintraege.sort_by(|a, b| (b.ordner, a.name.to_lowercase()).cmp(&(a.ordner, b.name.to_lowercase())));

    let mut ausgabe = serde_json::json!({ "eintraege": eintraege });
    if uebersprungen > 0 {
        ausgabe["gekuerzt"] = serde_json::json!(format!(
            "{uebersprungen} weitere Eintraege nicht genannt (Grenze {MAX_EINTRAEGE})."
        ));
    }
    Ok(ausgabe)
}

pub fn lesen(wurzel: &Path, relativ: &str) -> Result<serde_json::Value, String> {
    let datei = aufloesen(wurzel, relativ, true)?;
    let roh = fs::read(&datei).map_err(|e| format!("Datei nicht lesbar: {e}"))?;
    // Verlustfrei lesen ist hier falsch: eine Binaerdatei als Text an ein
    // Modell zu schicken kostet ein Vermoegen und sagt nichts.
    let text = match String::from_utf8(roh) {
        Ok(text) => text,
        Err(_) => {
            return Ok(serde_json::json!({
                "binaer": true,
                "hinweis": "Keine Textdatei — Inhalt nicht gelesen.",
            }))
        }
    };
    if text.len() > MAX_LESEZEICHEN {
        // Auf einer Zeichengrenze schneiden, nicht auf einer Bytegrenze:
        // ein halbes Umlaut-Byte macht aus der Antwort Datenmuell.
        let ende = text
            .char_indices()
            .map(|(i, _)| i)
            .take_while(|i| *i <= MAX_LESEZEICHEN)
            .last()
            .unwrap_or(0);
        return Ok(serde_json::json!({
            "inhalt": &text[..ende],
            "gekuerzt": format!(
                "Datei ist {} Zeichen lang, gezeigt sind die ersten {ende}.",
                text.len()
            ),
        }));
    }
    Ok(serde_json::json!({ "inhalt": text }))
}

pub fn schreiben(wurzel: &Path, relativ: &str, inhalt: &str) -> Result<serde_json::Value, String> {
    let datei = aufloesen(wurzel, relativ, false)?;
    fs::write(&datei, inhalt).map_err(|e| format!("Datei nicht schreibbar: {e}"))?;
    Ok(serde_json::json!({ "geschrieben": relativ, "zeichen": inhalt.len() }))
}

pub fn loeschen(wurzel: &Path, relativ: &str) -> Result<serde_json::Value, String> {
    let ziel = aufloesen(wurzel, relativ, true)?;
    // In den Papierkorb, nicht ins Nichts. Der autonome Modus erlaubt der KI,
    // in der Sandbox ohne Rueckfrage zu arbeiten — dann muss das Schlimmste,
    // was ein Missverstaendnis anrichtet, umkehrbar sein.
    trash::delete(&ziel).map_err(|e| format!("Nicht in den Papierkorb verschiebbar: {e}"))?;
    Ok(serde_json::json!({ "geloescht": relativ, "papierkorb": true }))
}

pub fn verschieben(wurzel: &Path, von: &str, nach: &str) -> Result<serde_json::Value, String> {
    let quelle = aufloesen(wurzel, von, true)?;
    let ziel = aufloesen(wurzel, nach, false)?;
    if ziel.exists() {
        return Err(format!("Ziel gibt es schon: '{nach}'"));
    }
    fs::rename(&quelle, &ziel).map_err(|e| format!("Nicht verschiebbar: {e}"))?;
    Ok(serde_json::json!({ "verschoben": von, "nach": nach }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn sandbox() -> PathBuf {
        let pfad = std::env::temp_dir().join(format!("mss-test-{}", std::process::id()));
        let _ = fs::create_dir_all(&pfad);
        pfad
    }

    #[test]
    fn relativer_pfad_bleibt_drinnen() {
        let wurzel = sandbox();
        fs::write(wurzel.join("notiz.txt"), "hallo").unwrap();
        let aufgeloest = aufloesen(&wurzel, "notiz.txt", true).unwrap();
        assert!(aufgeloest.ends_with("notiz.txt"));
    }

    #[test]
    fn punkt_punkt_kommt_nicht_hinaus() {
        let wurzel = sandbox();
        // Auch wenn das Ziel existiert (das Temp-Verzeichnis darueber gibt es
        // sicher): der kanonisierte Pfad liegt nicht unter der Wurzel.
        let fehler = aufloesen(&wurzel, "../", true).unwrap_err();
        assert!(fehler.contains("Ausserhalb"), "{fehler}");
    }

    #[test]
    fn absoluter_pfad_wird_abgewiesen() {
        let wurzel = sandbox();
        for versuch in ["C:/Windows/System32/drivers/etc/hosts", "/etc/passwd"] {
            let fehler = aufloesen(&wurzel, versuch, true).unwrap_err();
            assert!(fehler.contains("Ausserhalb"), "{versuch}: {fehler}");
        }
    }

    #[test]
    fn neue_datei_braucht_einen_vorhandenen_ordner_in_der_sandbox() {
        let wurzel = sandbox();
        // Innerhalb: erlaubt, obwohl es die Datei noch nicht gibt.
        assert!(aufloesen(&wurzel, "neu.txt", false).is_ok());
        // Ausserhalb: abgewiesen, obwohl es die Datei ebenfalls nicht gibt.
        assert!(aufloesen(&wurzel, "../neu.txt", false).is_err());
    }

    #[test]
    fn schreiben_und_lesen_gehen_durch_dieselbe_grenze() {
        let wurzel = sandbox();
        schreiben(&wurzel, "rund.txt", "Grüße mit Umlaut").unwrap();
        let gelesen = lesen(&wurzel, "rund.txt").unwrap();
        assert_eq!(gelesen["inhalt"], "Grüße mit Umlaut");
        assert!(schreiben(&wurzel, "../ausbruch.txt", "nein").is_err());
    }
}
