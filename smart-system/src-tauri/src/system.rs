//! Lesender Blick auf das Betriebssystem — Laufwerke, Ordner, Platzfresser.
//!
//! Die Sandbox (`sandbox.rs`) bleibt die einzige **Schreib**grenze des
//! Programms; dieses Modul liest nur. Es enthaelt bewusst keinen einzigen
//! schreibenden Dateisystemaufruf, und weil keine Typgrenze das erzwingen
//! kann, haelt der Test `lesend_bleibt_lesend` es als Quelltext-Zusage fest.
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

pub fn ausfuehren(argumente: &Value) -> Result<Value, String> {
    match argumente["aktion"].as_str().unwrap_or("") {
        "laufwerke" => laufwerke_impl::alle(),
        "verzeichnis" => verzeichnis(argumente["pfad"].as_str().unwrap_or("")),
        "groesste" => groesste(argumente["pfad"].as_str().unwrap_or("")),
        andere => Err(format!(
            "Unbekannte Aktion: '{andere}'. Erlaubt sind laufwerke, verzeichnis, groesste."
        )),
    }
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

fn groesste(pfad: &str) -> Result<Value, String> {
    let wurzel = absolut(pfad)?;
    let start = Instant::now();

    let mut toepfe: Vec<(String, u64)> = Vec::new();
    let mut dateien: Vec<(String, u64)> = Vec::new();
    let mut gesamt = 0u64;
    let mut abgebrochen = false;

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
            let summe = ordner_summe(&kind.path(), &wurzel, &start, &mut dateien, &mut abgebrochen);
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
    Ok(ausgabe)
}

/// Summiert einen Ordner rekursiv und sammelt die groessten Dateien ein.
/// Unlesbares wird uebersprungen (Systemordner ohne Rechte), Verknuepfungen
/// werden nicht verfolgt.
fn ordner_summe(
    ordner: &Path,
    wurzel: &Path,
    start: &Instant,
    dateien: &mut Vec<(String, u64)>,
    abgebrochen: &mut bool,
) -> u64 {
    let mut summe = 0u64;
    let mut stapel = vec![ordner.to_path_buf()];
    while let Some(aktuell) = stapel.pop() {
        if start.elapsed() > ZEITBUDGET {
            *abgebrochen = true;
            return summe;
        }
        let Ok(eintraege) = fs::read_dir(&aktuell) else { continue };
        for element in eintraege.flatten() {
            // Je Eintrag, nicht nur je Ordner: ein einzelnes Verzeichnis mit
            // Millionen Einträgen liefe sonst am Budget vorbei, so lange die
            // eine Schleife eben braucht.
            if start.elapsed() > ZEITBUDGET {
                *abgebrochen = true;
                return summe;
            }
            let Ok(typ) = element.file_type() else { continue };
            if typ.is_symlink() {
                continue;
            }
            if typ.is_dir() {
                stapel.push(element.path());
            } else if let Ok(daten) = element.metadata() {
                summe += daten.len();
                merken(dateien, &element.path(), wurzel, daten.len());
            }
        }
    }
    summe
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

    fn baum() -> PathBuf {
        let pfad = std::env::temp_dir().join(format!("mss-system-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&pfad);
        fs::create_dir_all(pfad.join("gross")).unwrap();
        fs::create_dir_all(pfad.join("klein")).unwrap();
        fs::write(pfad.join("gross/riese.bin"), vec![0u8; 4096]).unwrap();
        fs::write(pfad.join("gross/mittel.bin"), vec![0u8; 1024]).unwrap();
        fs::write(pfad.join("klein/zwerg.txt"), b"winzig").unwrap();
        fs::write(pfad.join("wurzel.txt"), b"direkt hier").unwrap();
        pfad
    }

    #[test]
    fn die_groessenanalyse_findet_die_platzfresser() {
        let wurzel = baum();
        let ergebnis = groesste(&wurzel.to_string_lossy()).unwrap();

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
    }

    #[test]
    fn die_auflistung_nennt_ordner_zuerst() {
        let wurzel = baum();
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
        let fehler = ausfuehren(&serde_json::json!({ "aktion": "loeschen" })).unwrap_err();
        assert!(fehler.contains("laufwerke, verzeichnis, groesste"), "{fehler}");
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
