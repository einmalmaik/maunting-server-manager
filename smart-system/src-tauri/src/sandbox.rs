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

/// Warum ein Pfad *innerhalb* der Wurzel trotzdem gesperrt ist.
///
/// Der Sandbox-Ordner kommt aus `konfig.json`, und die Datei kann aelter sein
/// als die Pruefung, die dort heute keinen Systembereich mehr als Wurzel
/// zulaesst. Stand `C:\` einmal drin, laege `Windows\System32` innerhalb der
/// Grenze — deshalb entscheidet zusaetzlich `zonen::zone` ueber das Ziel und
/// nicht nur seine Lage zur Wurzel.
fn systemfehler(pfad: &str) -> String {
    format!(
        "Systembereich: '{pfad}' gehoert zu Windows und bleibt auch dann \
         gesperrt, wenn der eingerichtete Sandbox-Ordner ihn umfasst. Der \
         Ordner ist zu weit gefasst; das aendert der Benutzer in den \
         Einstellungen der App, nicht ein zweiter Versuch."
    )
}

/// Loest einen relativen Pfad im Sandbox-Ordner auf — oder lehnt ihn ab.
///
/// `muss_existieren = false` ist der Fall „Datei anlegen": dann wird das
/// **Elternverzeichnis** kanonisiert und geprueft, denn ein noch nicht
/// existierender Pfad laesst sich nicht aufloesen. Der Dateiname selbst darf
/// dabei keine eigenen Pfadanteile mehr tragen — und wenn es ihn schon gibt,
/// wird auch er kanonisiert, sonst fuehrte eine Verknuepfung am Blatt am
/// Elternvergleich vorbei.
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
        if crate::zonen::zone(&echt) == crate::zonen::Zone::System {
            return Err(systemfehler(relativ));
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
    let ergebnis = echte_eltern.join(dateiname);
    // Der gepruefte Elternordner reicht nicht: das **Blatt** kann selbst eine
    // Verknuepfung sein. `fs::write` oeffnet unter Windows mit CREATE_ALWAYS
    // und folgt dem Reparse-Point, der Inhalt laege also ausserhalb — genau
    // der Ausbruchsweg aus dem Modulkopf, nur eine Ebene tiefer.
    //
    // `symlink_metadata` und nicht `exists`: letzteres folgt der Verknuepfung
    // und saehe eine ins Leere zeigende gar nicht — dabei legt ein Schreiben
    // darauf die Zieldatei draussen erst an.
    if ergebnis.symlink_metadata().is_ok() {
        // Kanonisiert, nicht abgelehnt: eine Verknuepfung *innerhalb* des
        // Sandbox-Ordners bleibt damit benutzbar, nur eine hinaus nicht.
        let echt = dunce::canonicalize(&ergebnis).map_err(|_| grenzfehler(relativ))?;
        if !echt.starts_with(&wurzel) {
            return Err(grenzfehler(relativ));
        }
    }
    if crate::zonen::zone(&ergebnis) == crate::zonen::Zone::System {
        return Err(systemfehler(relativ));
    }
    Ok(ergebnis)
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
    #[cfg(not(target_os = "android"))]
    trash::delete(&ziel).map_err(|e| format!("Nicht in den Papierkorb verschiebbar: {e}"))?;
    #[cfg(target_os = "android")]
    {
        if ziel.is_dir() {
            fs::remove_dir_all(&ziel).map_err(|e| format!("Nicht loeschbar: {e}"))?;
        } else {
            fs::remove_file(&ziel).map_err(|e| format!("Nicht loeschbar: {e}"))?;
        }
    }
    Ok(serde_json::json!({ "geloescht": relativ, "papierkorb": cfg!(not(target_os = "android")) }))
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

    /// Eine eigene, leere Wurzel fuer die Verknuepfungstests. Nicht die
    /// gemeinsame `sandbox()`: die Tests laufen nebenlaeufig im selben
    /// Prozess, und ein Link, den ein anderer Test gerade sieht, waere ein
    /// Ergebnis, das mal so und mal so ausfaellt.
    fn eigene_wurzel(name: &str) -> PathBuf {
        let pfad = std::env::temp_dir().join(format!("mss-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&pfad);
        fs::create_dir_all(&pfad).unwrap();
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

    #[test]
    fn eine_junction_traegt_die_grenze_nicht_weiter() {
        // Der Fall aus dem Modulkopf, den bisher kein Test abdeckte: ein
        // Ordnerlink im Sandbox-Ordner, der nach C:\Windows zeigt. Auf der
        // Eingabe sieht das aus wie ein harmloser Unterordner.
        let wurzel = eigene_wurzel("junction");
        let link = wurzel.join("harmlos");
        let gebaut = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&link)
            .arg("C:\\Windows")
            .output()
            .map(|a| a.status.success())
            .unwrap_or(false);
        if !gebaut {
            // Ohne Windows oder ohne Rechte fuer Junctions ist hier nichts zu
            // pruefen — dann still aufhoeren, statt eine fremde Umgebung
            // rotzufaerben.
            let _ = fs::remove_dir_all(&wurzel);
            return;
        }

        assert!(auflisten(&wurzel, "harmlos").is_err());
        assert!(lesen(&wurzel, "harmlos\\System32\\drivers\\etc\\hosts").is_err());
        let fehler = schreiben(&wurzel, "harmlos\\mss.txt", "nein").unwrap_err();
        assert!(fehler.contains("Ausserhalb"), "{fehler}");

        // `remove_dir` und nicht `remove_dir_all`: es loescht den Reparse-Point
        // und scheitert an jedem echten, nicht leeren Ordner. Ein Aufraeumen,
        // das im Fehlerfall C:\Windows leeren koennte, waere schlimmer als
        // ein liegengebliebener Testordner.
        let _ = fs::remove_dir(&link);
        let _ = fs::remove_dir_all(&wurzel);
    }

    #[test]
    fn eine_zu_weite_wurzel_oeffnet_windows_trotzdem_nicht() {
        // Die Wurzel kommt aus `konfig.json`. Eine Datei, die eine aeltere
        // Fassung geschrieben hat, kann `C:\` enthalten — dann laege Windows
        // *innerhalb* der Grenze, und der Wurzelvergleich allein saehe nichts
        // Falsches. Geprueft wird die Meldung und nicht nur „irgendein
        // Fehler": ein verweigerter Schreibzugriff waere sonst dasselbe
        // Ergebnis aus dem falschen Grund.
        let wurzel = Path::new("C:\\");
        if !wurzel.exists() {
            return;
        }
        let fehler = lesen(wurzel, "Windows\\System32\\drivers\\etc\\hosts").unwrap_err();
        assert!(fehler.contains("Systembereich"), "{fehler}");
        let fehler = schreiben(wurzel, "Windows\\mss-test.txt", "nein").unwrap_err();
        assert!(fehler.contains("Systembereich"), "{fehler}");
        assert!(!Path::new("C:\\Windows\\mss-test.txt").exists());
    }

    #[cfg(windows)]
    #[test]
    fn ein_dateisymlink_am_blatt_schreibt_nicht_nach_draussen() {
        // Die Luecke, wegen der diese Tests entstanden sind: beim Anlegen
        // wurde nur der Elternordner kanonisiert. Zeigte der Dateiname selbst
        // schon als Verknuepfung nach draussen, folgte `fs::write` ihr.
        let wurzel = eigene_wurzel("symlink");
        let draussen = std::env::temp_dir().join(format!("mss-opfer-{}.txt", std::process::id()));
        fs::write(&draussen, "unberuehrt").unwrap();

        let link = wurzel.join("harmlos.txt");
        if std::os::windows::fs::symlink_file(&draussen, &link).is_err() {
            // Dateisymlinks braucht der Entwicklermodus. Fehlt er, ist hier
            // nichts zu pruefen.
            let _ = fs::remove_dir_all(&wurzel);
            let _ = fs::remove_file(&draussen);
            return;
        }

        let fehler = schreiben(&wurzel, "harmlos.txt", "uebernommen").unwrap_err();
        assert!(fehler.contains("Ausserhalb"), "{fehler}");
        assert_eq!(fs::read_to_string(&draussen).unwrap(), "unberuehrt");
        assert!(loeschen(&wurzel, "harmlos.txt").is_err());

        // Und die Gegenprobe, damit die Grenze keine Faehigkeit kostet: eine
        // Verknuepfung, die *innerhalb* des Ordners bleibt, ist weiter
        // beschreibbar.
        fs::write(wurzel.join("echt.txt"), "alt").unwrap();
        if std::os::windows::fs::symlink_file(wurzel.join("echt.txt"), wurzel.join("zeiger.txt"))
            .is_ok()
        {
            schreiben(&wurzel, "zeiger.txt", "neu").unwrap();
            assert_eq!(fs::read_to_string(wurzel.join("echt.txt")).unwrap(), "neu");
        }

        let _ = fs::remove_dir_all(&wurzel);
        let _ = fs::remove_file(&draussen);
    }
}
