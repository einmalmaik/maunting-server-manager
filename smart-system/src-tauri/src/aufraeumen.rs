//! Aufraeumen ausserhalb der Sandbox — was wirklich verschwindet.
//!
//! Die Regeln stammen woertlich vom Betreiber (23.08.2026):
//!
//! > "Beim Loeschen ja Papierkorb ist die Regel. Loeschen nur in bekannten
//! > Muellordnern ja, aber wenn ich sage, dass sie den Muelleimer leeren
//! > soll, kann sie das dann auch. Und wenn ich sage, bitte loesch das
//! > endgueltig, dann packt sie es nicht erst in den Papierkorb, sondern
//! > macht das direkt. [...] Wenn das aber nicht gesagt wird, dann wird es
//! > immer sicherheitshalber im Papierkorb gemacht, dann wird es aber auch
//! > gesagt."
//!
//! Daraus folgen drei Dinge, die dieser Code haelt:
//!
//! 1. **Der Papierkorb ist der Normalfall**, und das Ergebnis sagt es
//!    (`papierkorb: true` je Posten). Dass die KI es dem Benutzer weitersagt,
//!    steht im Systemprompt — hier steht die Tatsache, auf die sie sich
//!    berufen kann.
//! 2. **Endgueltig nur auf Ansage.** Diese Entscheidung faellt oben: das
//!    Panel schickt `aktion="endgueltig"`, wenn der Mensch es verlangt hat.
//!    Dieses Modul fuehrt aus, es raet nicht.
//! 3. **In Muellordnern ist der Papierkorb sinnlos** und wird uebersprungen —
//!    aber nur dort, und das Ergebnis sagt auch das (`papierkorb: false` mit
//!    Zone `muell`). Wer `%TEMP%` in den Papierkorb legt, hat keinen Platz
//!    gewonnen, sondern ihn nur verschoben.
//!
//! **Die Zone entscheidet, nicht der Name.** Jeder Pfad geht durch
//! [`crate::zonen::zone`], und die arbeitet auf der kanonisierten Form —
//! sonst fuehrte eine Junction aus `Downloads` nach `C:\Windows` und die
//! Pruefung waere gelogen.
//!
//! **Ein Fehlschlag stoppt nicht den Rest.** Jeder Pfad wird einzeln
//! verbucht. Ein halbes Aufraeumen, das ehrlich sagt, was es geschafft hat,
//! ist besser als ein Abbruch bei Posten drei — der Benutzer sieht sonst
//! "fehlgeschlagen" und weiss nicht, dass zwei Dateien schon weg sind.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::zonen::{self, Zone};

/// Wie viele Pfade ein Auftrag tragen darf. Dieselbe Zahl steht im
/// Werkzeugschema des Panels (`MAX_AUFRAEUM_PFADE`) und gilt hier noch
/// einmal: die App nimmt auch Auftraege entgegen, die nie durch dieses
/// Schema gegangen sind.
pub const MAX_PFADE: usize = 500;

/// Was mit einem einzelnen Pfad geschehen ist.
struct Posten {
    pfad: PathBuf,
    zone: Zone,
    bytes: u64,
}

/// Wie tief in Ordner hineingerechnet wird. Reicht fuer alles, was ein
/// Mensch bewusst zum Aufraeumen benennt.
pub const MESSTIEFE: u32 = 12;
/// Wie lange **eine** Groessenmessung hoechstens laufen darf.
///
/// Fuenf Sekunden sind lang genug fuer jeden normalen Ordner und kurz genug,
/// dass ein Mensch davor nicht denkt, das Programm sei tot. Der Wert gilt je
/// Pfad; eine Karte mit zwanzig Posten misst also im schlimmsten Fall
/// zwanzigmal fuenf Sekunden — dafuer laeuft sie seit demselben Tag nicht
/// mehr auf dem Hauptthread (`#[tauri::command(async)]`).
pub const MESSFRIST: Duration = Duration::from_secs(5);

/// Wie lange **ein ganzer Auftrag** hoechstens messen darf.
///
/// `MESSFRIST` gilt je Pfad. Beim Ausfuehren wird jeder der bis zu
/// [`MAX_PFADE`] Posten noch einmal gemessen (`pruefen`), und fuenfhundertmal
/// fuenf Sekunden waeren vierzig Minuten — waehrend der Desktop-Auftrag nach
/// zehn Minuten verfaellt. Der Lauf erfuehre dann `DESKTOP_JOB_EXPIRED` und
/// sagte dem Benutzer, es sei nichts geschehen, obwohl geloescht wird.
///
/// Zwanzig Sekunden reichen fuer jede normale Liste. Danach wird nicht mehr
/// gemessen, sondern nur noch geloescht: die Zahl in `freigegebene_bytes` ist
/// dann eine Untergrenze, und das ist allemal besser als ein Auftrag, der
/// waehrend seiner eigenen Arbeit verfaellt.
const MESSBUDGET: Duration = Duration::from_secs(20);

/// Die Groesse eines Pfades — bei Ordnern die Summe darunter, und dazu die
/// Auskunft, ob die Rechnung fertig geworden ist (`false` heisst: die Frist
/// lief ab, die Zahl ist eine Untergrenze).
///
/// Auch die Bestaetigungskarte rechnet damit (`auftrag::raeumen`): ein
/// blosses `metadata().len()` meldet fuer Ordner den Verzeichniseintrag und
/// damit fast immer 0 — eine Karte, die "Downloads-Ordner, 0 B" anbietet,
/// beantwortet die einzige Frage nicht, die der Mensch daran hat.
///
/// Ein Fehler beim Messen darf das Loeschen nicht verhindern (dann steht
/// eben 0 da). Die Rekursion ist begrenzt, weil ein zyklischer
/// Verknuepfungsbaum sonst nie fertig wuerde.
///
/// **Und sie ist seit dem 23.08.2026 auf die Uhr begrenzt.** Die Tiefe
/// allein reichte nicht: ein breiter Baum, ein OneDrive-Ordner voller
/// Platzhalter oder ein getrenntes Netzlaufwerk (wo schon jedes
/// `symlink_metadata` bis zum SMB-Zeitablauf steht) laesst diese Rechnung
/// beliebig lange laufen. Das war die einzige Stelle im Programm, deren
/// Blockade der Laenge nach unbegrenzt war — und sie lief auf dem
/// Hauptthread, also mit eingefrorener Oberflaeche.
///
/// Eine Karte, die nach fuenf Sekunden "mindestens 2,4 GB" sagt, ist besser
/// als eine, die nie kommt.
pub fn groesse_gemessen(pfad: &Path, tiefe: u32) -> (u64, bool) {
    groesse_mit_frist(pfad, tiefe, Instant::now() + MESSFRIST)
}

fn groesse_mit_frist(pfad: &Path, tiefe: u32, bis: Instant) -> (u64, bool) {
    if Instant::now() >= bis {
        return (0, false);
    }
    let Ok(daten) = std::fs::symlink_metadata(pfad) else {
        return (0, true);
    };
    if daten.is_file() {
        return (daten.len(), true);
    }
    // Einer Verknuepfung folgen wir beim Messen nicht: sonst zaehlte ein Link
    // nach C:\Windows das halbe System zur Groesse eines Downloads-Ordners.
    if daten.is_symlink() || tiefe == 0 {
        return (0, true);
    }
    let Ok(eintraege) = std::fs::read_dir(pfad) else {
        return (0, true);
    };
    let mut summe = 0u64;
    let mut vollstaendig = true;
    for eintrag in eintraege.flatten() {
        if Instant::now() >= bis {
            // Nicht weiterzaehlen, aber das Bisherige behalten: eine
            // Untergrenze ist eine Aussage, eine 0 waere eine falsche.
            return (summe, false);
        }
        let (teil, fertig) = groesse_mit_frist(&eintrag.path(), tiefe - 1, bis);
        summe = summe.saturating_add(teil);
        vollstaendig &= fertig;
    }
    (summe, vollstaendig)
}

/// Prueft einen einzelnen Pfad, bevor irgendetwas passiert.
///
/// Gibt bei Ablehnung den Grund zurueck, den das Modell zu lesen bekommt —
/// benannt und nicht "geht nicht", damit es nicht dreimal dasselbe versucht.
fn pruefen(roh: &str, system_erlaubt: bool, messen_bis: Instant) -> Result<Posten, String> {
    let pfad = Path::new(roh.trim());
    if roh.trim().is_empty() {
        return Err("Leerer Pfad.".into());
    }
    if !pfad.is_absolute() {
        return Err(format!(
            "'{roh}' ist kein absoluter Pfad. Ausserhalb des Sandbox-Ordners \
             braucht es den vollen Pfad, z. B. 'C:\\Users\\Name\\Downloads\\alt.iso'."
        ));
    }
    if std::fs::symlink_metadata(pfad).is_err() {
        return Err(format!("'{roh}' gibt es nicht (mehr)."));
    }
    let zone = zonen::zone(pfad);
    if zone == Zone::System && !system_erlaubt {
        return Err(format!(
            "'{roh}' gehoert zum Systembereich (Windows, Programmordner, \
             Bootdateien oder ein fremdes Benutzerprofil). Der Benutzer hat \
             ihn fuer die KI nicht freigegeben; das aendert nur er selbst in \
             den Einstellungen der App. Such einen anderen Weg, den Platz zu \
             gewinnen."
        ));
    }
    // Die Messung ist eine Auskunft, kein Teil des Loeschens: ist das Budget
    // des Auftrags aufgebraucht, wird der Posten trotzdem entfernt — nur ohne
    // seine Zahl.
    let bytes = if Instant::now() < messen_bis {
        let (bytes, _) = groesse_mit_frist(pfad, MESSTIEFE, messen_bis);
        bytes
    } else {
        0
    };
    Ok(Posten { pfad: pfad.to_path_buf(), zone, bytes })
}

/// Loescht einen Pfad — hart, egal was er ist.
///
/// Drei Faelle, nicht zwei. Eine Junction oder ein Ordner-Symlink meldet
/// `is_symlink() == true` und `is_dir() == false`, faellt also auf den
/// Datei-Zweig — und `DeleteFileW` scheitert an einem Verzeichnis-Reparsepunkt
/// mit "Zugriff verweigert". Ausgerechnet in Muellordnern hinterlassen
/// Installer solche Verweise. Fuer sie ist `remove_dir` richtig: es entfernt
/// die **Verknuepfung** und nicht ihr Ziel — sonst raeumte ein Verweis nach
/// `C:\Windows` genau dorthin ab.
fn hart_weg(pfad: &Path) -> Result<(), String> {
    let daten = std::fs::symlink_metadata(pfad).map_err(|e| e.to_string())?;
    if daten.is_symlink() {
        // Wohin er zeigt, entscheidet die Loeschart — `metadata` folgt ihm
        // dafuer. Ein Verweis ins Leere ist eine Datei.
        let zeigt_auf_ordner = std::fs::metadata(pfad).map(|z| z.is_dir()).unwrap_or(false);
        return if zeigt_auf_ordner {
            std::fs::remove_dir(pfad).map_err(|e| e.to_string())
        } else {
            std::fs::remove_file(pfad).map_err(|e| e.to_string())
        };
    }
    if daten.is_dir() {
        std::fs::remove_dir_all(pfad).map_err(|e| e.to_string())
    } else {
        std::fs::remove_file(pfad).map_err(|e| e.to_string())
    }
}

/// Der gemeinsame Weg von `papierkorb` und `endgueltig`.
///
/// `hart` sagt, was der Mensch verlangt hat. In `Zone::Muell` wird auch ohne
/// diese Ansage hart geloescht, denn dort ist der Papierkorb kein Schutz,
/// sondern nur eine zweite Kopie desselben Muells.
fn ausfuehren(pfade: &[String], system_erlaubt: bool, hart: bool) -> Result<Value, String> {
    if pfade.is_empty() {
        return Err("Keine Pfade angegeben.".into());
    }
    if pfade.len() > MAX_PFADE {
        return Err(format!(
            "{} Pfade sind zu viele; hoechstens {MAX_PFADE} auf einmal. \
             Teil es auf und fang mit den groessten an.",
            pfade.len()
        ));
    }

    // Eine Uhr fuer den ganzen Auftrag, nicht je Pfad — siehe `MESSBUDGET`.
    let messen_bis = Instant::now() + MESSBUDGET;
    let mut ergebnisse = Vec::with_capacity(pfade.len());
    let mut freigegeben: u64 = 0;
    let mut erledigt = 0usize;

    for roh in pfade {
        let posten = match pruefen(roh, system_erlaubt, messen_bis) {
            Ok(posten) => posten,
            Err(grund) => {
                ergebnisse.push(json!({ "pfad": roh, "erledigt": false, "grund": grund }));
                continue;
            }
        };
        let in_den_papierkorb = !hart && posten.zone != Zone::Muell;
        #[cfg(not(target_os = "android"))]
        let versuch = if in_den_papierkorb {
            trash::delete(&posten.pfad).map_err(|e| e.to_string())
        } else {
            hart_weg(&posten.pfad)
        };
        #[cfg(target_os = "android")]
        let versuch = hart_weg(&posten.pfad);
        match versuch {
            Ok(()) => {
                freigegeben += posten.bytes;
                erledigt += 1;
                ergebnisse.push(json!({
                    "pfad": roh,
                    "erledigt": true,
                    "papierkorb": in_den_papierkorb,
                    "zone": posten.zone.name(),
                    "bytes": posten.bytes,
                }));
            }
            Err(grund) => ergebnisse.push(json!({
                "pfad": roh, "erledigt": false, "grund": grund,
            })),
        }
    }

    Ok(json!({
        "erledigt": erledigt,
        "gesamt": pfade.len(),
        "freigegebene_bytes": freigegeben,
        "posten": ergebnisse,
        // Der Satz, den die KI weitersagen soll. Er steht hier und nicht nur
        // im Prompt, damit er auch dann dabeisteht, wenn das Modell den
        // Prompt gerade nicht beachtet.
        "hinweis": if hart {
            "Endgueltig geloescht. Das laesst sich nicht rueckgaengig machen."
        } else {
            "In den Papierkorb verschoben, ausser in Muellordnern (dort waere \
             er sinnlos). Sag dem Benutzer, dass er es von dort zurueckholen \
             kann — und dass 'papierkorb_leeren' den Platz erst wirklich \
             freigibt."
        },
    }))
}

/// Verschiebt in den Papierkorb (in Muellordnern: loescht direkt).
pub fn papierkorb(pfade: &[String], system_erlaubt: bool) -> Result<Value, String> {
    ausfuehren(pfade, system_erlaubt, false)
}

/// Loescht endgueltig. Nur, wenn der Mensch genau das verlangt hat.
pub fn endgueltig(pfade: &[String], system_erlaubt: bool) -> Result<Value, String> {
    ausfuehren(pfade, system_erlaubt, true)
}

/// Leert den Papierkorb.
///
/// Eigener Schritt und keine Nebenwirkung des Loeschens: der Papierkorb ist
/// die Rueckholmoeglichkeit fuer **alles**, was der Benutzer je geloescht hat,
/// nicht nur fuer das aus dieser Runde. Ihn beilaeufig mitzuleeren waere die
/// unangenehmste Ueberraschung, die dieses Programm bieten koennte.
pub fn papierkorb_leeren() -> Result<Value, String> {
    leeren_impl::leeren()
}

#[cfg(windows)]
mod leeren_impl {
    use serde_json::{json, Value};
    use windows::core::PCWSTR;
    use windows::Win32::UI::Shell::{
        SHEmptyRecycleBinW, SHQueryRecycleBinW, SHERB_NOCONFIRMATION, SHERB_NOPROGRESSUI,
        SHERB_NOSOUND, SHQUERYRBINFO,
    };

    pub fn leeren() -> Result<Value, String> {
        // Vorher messen: hinterher ist der Papierkorb leer und niemand kann
        // mehr sagen, wieviel Platz das gebracht hat.
        let mut info = SHQUERYRBINFO {
            cbSize: std::mem::size_of::<SHQUERYRBINFO>() as u32,
            ..Default::default()
        };
        let vorher = unsafe { SHQueryRecycleBinW(PCWSTR::null(), &mut info) }
            .ok()
            .map(|()| (info.i64Size.max(0) as u64, info.i64NumItems.max(0) as u64));

        // Kein Dialog, kein Ton, kein Fortschrittsfenster: der Benutzer hat
        // bereits entschieden (Karte oder autonomer Modus), und ein zweiter
        // Windows-Dialog haette niemanden, der ihn wegklickt.
        unsafe {
            SHEmptyRecycleBinW(
                None,
                PCWSTR::null(),
                SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND,
            )
        }
        .map_err(|e| format!("Papierkorb nicht zu leeren: {e}"))?;

        let (bytes, anzahl) = vorher.unwrap_or((0, 0));
        Ok(json!({
            "geleert": true,
            "freigegebene_bytes": bytes,
            "eintraege": anzahl,
            "hinweis": "Der Papierkorb ist leer. Das war nicht rueckgaengig zu machen.",
        }))
    }
}

#[cfg(not(windows))]
mod leeren_impl {
    use serde_json::Value;

    pub fn leeren() -> Result<Value, String> {
        Err("Den Papierkorb leert diese App bisher nur unter Windows.".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wegwerfordner(name: &str) -> PathBuf {
        let ordner = std::env::temp_dir().join(format!("mss-aufraeumen-{name}"));
        let _ = std::fs::remove_dir_all(&ordner);
        std::fs::create_dir_all(&ordner).unwrap();
        ordner
    }

    #[test]
    fn ohne_freigabe_bleibt_der_systembereich_zu() {
        let ergebnis = papierkorb(&["C:\\Windows\\System32".to_string()], false).unwrap();
        let posten = &ergebnis["posten"][0];
        assert_eq!(posten["erledigt"], json!(false));
        let grund = posten["grund"].as_str().unwrap();
        assert!(grund.contains("Systembereich"), "{grund}");
        assert_eq!(ergebnis["erledigt"], json!(0));
    }

    #[test]
    fn ein_relativer_pfad_wird_benannt_abgelehnt() {
        let ergebnis = papierkorb(&["irgendwas.txt".to_string()], true).unwrap();
        let grund = ergebnis["posten"][0]["grund"].as_str().unwrap();
        assert!(grund.contains("absoluter Pfad"), "{grund}");
    }

    #[test]
    fn ein_fehlschlag_haelt_die_uebrigen_nicht_auf() {
        // Der Kern der Einzelverbuchung: Pfad 1 gibt es nicht, Pfad 2 schon.
        let ordner = wegwerfordner("reihe");
        let datei = ordner.join("weg.txt");
        std::fs::write(&datei, "x").unwrap();

        let ergebnis = papierkorb(
            &[
                ordner.join("gibt-es-nicht.txt").display().to_string(),
                datei.display().to_string(),
            ],
            false,
        )
        .unwrap();

        assert_eq!(ergebnis["erledigt"], json!(1));
        assert_eq!(ergebnis["gesamt"], json!(2));
        assert_eq!(ergebnis["posten"][0]["erledigt"], json!(false));
        assert_eq!(ergebnis["posten"][1]["erledigt"], json!(true));
        assert!(!datei.exists(), "die zweite Datei muesste weg sein");
        let _ = std::fs::remove_dir_all(&ordner);
    }

    #[test]
    fn in_muellordnern_wird_der_papierkorb_uebersprungen() {
        // %TEMP% ist Zone::Muell — dort ist der Papierkorb kein Schutz,
        // sondern nur eine zweite Kopie desselben Muells.
        let ordner = wegwerfordner("muell");
        let datei = ordner.join("cache.bin");
        std::fs::write(&datei, "x").unwrap();

        let ergebnis = papierkorb(&[datei.display().to_string()], false).unwrap();
        let posten = &ergebnis["posten"][0];
        assert_eq!(posten["erledigt"], json!(true));
        assert_eq!(posten["zone"], json!("muell"));
        assert_eq!(posten["papierkorb"], json!(false));
        let _ = std::fs::remove_dir_all(&ordner);
    }

    #[test]
    fn eine_junction_laesst_sich_wegraeumen_ohne_ihr_ziel_zu_treffen() {
        // Der Fall, an dem `remove_file` scheiterte: ein Verzeichnis-
        // Reparsepunkt. Geprueft wird beides — dass die Verknuepfung weg ist
        // **und** dass der Ordner dahinter noch steht.
        let ordner = wegwerfordner("junction");
        let ziel = ordner.join("echt");
        std::fs::create_dir_all(&ziel).unwrap();
        std::fs::write(ziel.join("wichtig.txt"), "bleibt").unwrap();
        let verweis = ordner.join("verweis");

        let gebaut = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&verweis)
            .arg(&ziel)
            .output()
            .map(|a| a.status.success())
            .unwrap_or(false);
        if !gebaut {
            // Ohne Rechte fuer Junctions ist hier nichts zu pruefen.
            let _ = std::fs::remove_dir_all(&ordner);
            return;
        }

        // %TEMP% ist Zone::Muell, also geht es durch `hart_weg`.
        let ergebnis = papierkorb(&[verweis.display().to_string()], false).unwrap();
        let posten = &ergebnis["posten"][0];
        assert_eq!(posten["erledigt"], json!(true), "{posten}");
        assert!(!verweis.exists(), "die Verknuepfung muesste weg sein");
        assert!(ziel.join("wichtig.txt").exists(), "das Ziel muesste stehen bleiben");
        let _ = std::fs::remove_dir_all(&ordner);
    }

    #[test]
    fn zu_viele_pfade_werden_benannt_abgewiesen() {
        let viele: Vec<String> = (0..MAX_PFADE + 1).map(|n| format!("C:\\x\\{n}")).collect();
        let fehler = papierkorb(&viele, false).unwrap_err();
        assert!(fehler.contains("zu viele"), "{fehler}");
    }

    #[test]
    fn ohne_pfade_passiert_nichts() {
        assert!(papierkorb(&[], true).is_err());
        assert!(endgueltig(&[], true).is_err());
    }

    #[test]
    fn der_hinweis_nennt_den_papierkorb() {
        // Die Zusage des Betreibers: "dann wird es aber auch gesagt".
        let ordner = wegwerfordner("hinweis");
        let datei = ordner.join("x.txt");
        std::fs::write(&datei, "x").unwrap();
        let ergebnis = papierkorb(&[datei.display().to_string()], false).unwrap();
        let hinweis = ergebnis["hinweis"].as_str().unwrap();
        assert!(hinweis.contains("Papierkorb"), "{hinweis}");
        assert!(hinweis.contains("zurueckholen"), "{hinweis}");
        let _ = std::fs::remove_dir_all(&ordner);
    }
}
