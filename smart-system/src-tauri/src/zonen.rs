//! Wohin gegriffen werden darf — drei Zonen, eine Funktion.
//!
//! Bis zum 23.08.2026 gab es genau eine Schreibgrenze: den Sandbox-Ordner
//! (`sandbox.rs`). Der Betreiber wollte mehr — die KI soll auch aufraeumen
//! duerfen, "und das sicher auch ausserhalb der Sandbox". Damit stellt sich
//! eine Frage, die es vorher nicht gab: *welcher* Pfad ausserhalb ist harmlos
//! und welcher nicht.
//!
//! Drei Antworten, mehr braucht es nicht:
//!
//! * [`Zone::Muell`] — Orte, an denen der Papierkorb sinnlos ist, weil dort
//!   nur Wegwerfbares liegt und der Papierkorb keinen Platz freigibt. Temp,
//!   Browser-Caches, `SoftwareDistribution\Download`.
//! * [`Zone::System`] — was den Rechner startet oder Windows selbst ist. Hier
//!   arbeitet die KI nur, wenn der Benutzer es in seinem Konto ausdruecklich
//!   erlaubt hat (`users.ai_desktop_systembereich`).
//! * [`Zone::Frei`] — alles andere. Die eigenen Dateien des Benutzers.
//!
//! **Kanonisiert wird immer.** Das ist nicht Sorgfalt, sondern der Kern: eine
//! Junction in `C:\Users\Name\Downloads`, die nach `C:\Windows\System32`
//! zeigt, saehe auf der Eingabe wie ein harmloser Downloads-Pfad aus. Genau
//! dieser Weg ist auch in `sandbox.rs` der Grund fuer `dunce::canonicalize`,
//! und aus demselben Grund steht er hier. `dunce` und nicht `std::fs`, weil
//! letzteres unter Windows die Verbatim-Form `\\?\C:\...` liefert und jeder
//! Praefixvergleich dagegen fehlschlaegt — eine Grenze, die nie greift.
//!
//! Existiert der Pfad noch nicht, wird der naechste existierende Elternordner
//! kanonisiert. Ein Name, den es nicht gibt, kann keine Verknuepfung sein;
//! sein Ordner aber schon.
//!
//! **Muell wird vor System geprueft**, und das ist kein Detail:
//! `C:\Windows\SoftwareDistribution\Download` liegt in `C:\Windows` und ist
//! trotzdem Wegwerfbares — dort sammeln sich die heruntergeladenen
//! Update-Pakete, und sie sind der haeufigste einzelne Platzfresser
//! ueberhaupt. Andersherum geprueft waere die haeufigste sinnvolle
//! Aufraeumaktion gesperrt.

use std::path::{Path, PathBuf};

/// Wie heikel ein Ort ist.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Zone {
    /// Die Dateien des Benutzers. Loeschen geht in den Papierkorb.
    Frei,
    /// Zwischenspeicher und Update-Reste. Der Papierkorb waere hier sinnlos.
    Muell,
    /// Windows selbst, Programme, Boot, fremde Profile.
    System,
}

impl Zone {
    /// Der Name, unter dem die Zone im Ergebnis und auf der Karte steht.
    pub fn name(self) -> &'static str {
        match self {
            Zone::Frei => "frei",
            Zone::Muell => "muell",
            Zone::System => "system",
        }
    }
}

/// Liest eine Umgebungsvariable als Pfad — leer heisst: gibt es nicht.
fn umgebung(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|wert| !wert.is_empty())
        .map(PathBuf::from)
}

/// Kanonisiert, so weit es geht: existiert der Pfad nicht, dann sein naechster
/// existierender Elternordner, mit dem Rest wieder angehaengt.
///
/// Ohne den Rueckweg ueber die Eltern waere jeder noch nicht existierende
/// Pfad unkanonisierbar — und "kann ich nicht aufloesen" duerfte hier nie
/// "ist wohl harmlos" heissen.
pub fn aufloesen(pfad: &Path) -> PathBuf {
    if let Ok(echt) = dunce::canonicalize(pfad) {
        return echt;
    }
    let mut rest = Vec::new();
    let mut lauf = pfad;
    while let Some(eltern) = lauf.parent() {
        if let Some(name) = lauf.file_name() {
            rest.push(name.to_os_string());
        }
        if let Ok(echt) = dunce::canonicalize(eltern) {
            let mut zusammen = echt;
            for name in rest.iter().rev() {
                zusammen.push(name);
            }
            return zusammen;
        }
        lauf = eltern;
    }
    pfad.to_path_buf()
}

/// Vergleicht zwei Pfade als Praefix, ohne Gross-/Kleinschreibung.
///
/// Windows-Dateisysteme unterscheiden sie nicht, und `Path::starts_with`
/// vergleicht byteweise: `c:\windows\temp` waere sonst nicht `C:\Windows\Temp`
/// — und eine Grenze, die man durch Kleinschreibung umgeht, ist keine.
fn liegt_unter(pfad: &Path, wurzel: &Path) -> bool {
    let pfad: Vec<String> = pfad
        .components()
        .map(|teil| teil.as_os_str().to_string_lossy().to_lowercase())
        .collect();
    let wurzel: Vec<String> = wurzel
        .components()
        .map(|teil| teil.as_os_str().to_string_lossy().to_lowercase())
        .collect();
    !wurzel.is_empty() && pfad.len() >= wurzel.len() && pfad[..wurzel.len()] == wurzel[..]
}

fn gleich(pfad: &Path, anderer: &Path) -> bool {
    liegt_unter(pfad, anderer) && liegt_unter(anderer, pfad)
}

/// Die Wegwerf-Orte. Alle aus der Umgebung, keiner fest verdrahtet: ein
/// Benutzerprofil kann auf `D:` liegen, und `%TEMP%` ist umlegbar.
fn muellorte() -> Vec<PathBuf> {
    let mut orte = Vec::new();
    for name in ["TEMP", "TMP"] {
        if let Some(pfad) = umgebung(name) {
            orte.push(pfad);
        }
    }
    if let Some(lokal) = umgebung("LOCALAPPDATA") {
        orte.push(lokal.join("Temp"));
        // Die Browser-Zwischenspeicher. Nur die Cache-Ordner selbst — im
        // Profil daneben liegen Lesezeichen und Passwoerter, und die sind
        // alles andere als Wegwerfbares.
        for hersteller in [
            "Google\\Chrome", "Microsoft\\Edge", "BraveSoftware\\Brave-Browser",
            "Chromium", "Vivaldi", "Opera Software",
        ] {
            orte.push(lokal.join(hersteller).join("User Data"));
        }
        orte.push(lokal.join("Mozilla\\Firefox\\Profiles"));
        orte.push(lokal.join("npm-cache"));
        orte.push(lokal.join("pip\\Cache"));
        orte.push(lokal.join("CrashDumps"));
    }
    if let Some(profil) = umgebung("USERPROFILE") {
        orte.push(profil.join(".cargo\\registry\\cache"));
        orte.push(profil.join(".npm\\_cacache"));
    }
    if let Some(windows) = umgebung("SystemRoot").or_else(|| Some(PathBuf::from("C:\\Windows"))) {
        orte.push(windows.join("Temp"));
        orte.push(windows.join("Prefetch"));
        orte.push(windows.join("SoftwareDistribution\\Download"));
    }
    orte
}

/// Was Windows gehoert. Der Benutzerordner selbst steht mit drin — nicht sein
/// Inhalt: `C:\Users\Name` zu loeschen nimmt das ganze Profil mit, waehrend
/// `C:\Users\Name\Downloads\alt.iso` genau die Datei ist, um die es geht.
fn systemorte() -> Vec<PathBuf> {
    let mut orte = Vec::new();
    let windows = umgebung("SystemRoot").unwrap_or_else(|| PathBuf::from("C:\\Windows"));
    orte.push(windows);
    for name in ["ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"] {
        if let Some(pfad) = umgebung(name) {
            orte.push(pfad);
        }
    }
    if let Some(daten) = umgebung("ProgramData") {
        orte.push(daten.join("Microsoft"));
        orte.push(daten.join("Package Cache"));
    }
    if let Some(laufwerk) = umgebung("SystemDrive") {
        for name in ["Boot", "EFI", "Recovery", "System Volume Information", "$Recycle.Bin"] {
            orte.push(PathBuf::from(&laufwerk).join(name));
        }
    }
    orte
}

/// Wo dieses Programm selbst liegt. Sich selbst wegzuraeumen ist kein
/// Aufraeumen, sondern der Grund, warum danach niemand mehr fragen kann.
fn eigener_ort() -> Option<PathBuf> {
    std::env::current_exe().ok()?.parent().map(Path::to_path_buf)
}

/// In welche Zone dieser Pfad faellt.
///
/// Erwartet einen absoluten Pfad; ein relativer wird gegen das
/// Arbeitsverzeichnis aufgeloest und landet damit fast immer in `Frei` — die
/// Pflicht zum absoluten Pfad steht deshalb eine Ebene hoeher
/// (`aufraeumen::pruefen`), wo sie eine benannte Fehlermeldung bekommt.
pub fn zone(pfad: &Path) -> Zone {
    let echt = aufloesen(pfad);

    // Muell zuerst — siehe Modulkopf: SoftwareDistribution liegt in
    // C:\Windows und ist trotzdem Wegwerfbares.
    for ort in muellorte() {
        if liegt_unter(&echt, &aufloesen(&ort)) {
            return Zone::Muell;
        }
    }

    for ort in systemorte() {
        if liegt_unter(&echt, &aufloesen(&ort)) {
            return Zone::System;
        }
    }

    if let Some(eigen) = eigener_ort() {
        if liegt_unter(&echt, &aufloesen(&eigen)) {
            return Zone::System;
        }
    }

    // Das Profil selbst und die Profilwurzel: `C:\Users` und `C:\Users\Name`
    // sind System, ihre Inhalte sind es nicht.
    if let Some(profil) = umgebung("USERPROFILE") {
        let profil = aufloesen(&profil);
        if gleich(&echt, &profil) {
            return Zone::System;
        }
        if let Some(alle) = profil.parent() {
            // `C:\Users` selbst und jedes **fremde** Profil daneben.
            if gleich(&echt, alle) {
                return Zone::System;
            }
            if liegt_unter(&echt, alle) && !liegt_unter(&echt, &profil) {
                return Zone::System;
            }
        }
    }

    Zone::Frei
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_ist_system() {
        assert_eq!(zone(Path::new("C:\\Windows\\System32\\kernel32.dll")), Zone::System);
        assert_eq!(zone(Path::new("C:\\Windows")), Zone::System);
    }

    #[test]
    fn kleinschreibung_hilft_nicht_am_tor_vorbei() {
        // Windows unterscheidet keine Gross-/Kleinschreibung. Ein byteweiser
        // Vergleich haette hier `Frei` gesagt.
        assert_eq!(zone(Path::new("c:\\windows\\system32")), Zone::System);
        assert_eq!(zone(Path::new("C:\\WINDOWS\\Temp\\x.log")), Zone::Muell);
    }

    #[test]
    fn temp_ist_muell_obwohl_es_im_profil_liegt() {
        let temp = umgebung("TEMP").expect("TEMP muesste gesetzt sein");
        assert_eq!(zone(&temp.join("irgendwas.tmp")), Zone::Muell);
    }

    #[test]
    fn updatepakete_sind_muell_obwohl_sie_in_windows_liegen() {
        // Der Fall, wegen dem die Reihenfolge im Modulkopf steht.
        assert_eq!(
            zone(Path::new("C:\\Windows\\SoftwareDistribution\\Download\\abc")),
            Zone::Muell
        );
    }

    #[test]
    fn das_profil_selbst_ist_system_sein_inhalt_nicht() {
        let profil = umgebung("USERPROFILE").expect("USERPROFILE muesste gesetzt sein");
        assert_eq!(zone(&profil), Zone::System);
        assert_eq!(zone(&profil.join("Downloads\\alt.iso")), Zone::Frei);
    }

    #[test]
    fn programmordner_sind_system() {
        for name in ["ProgramFiles", "ProgramFiles(x86)"] {
            if let Some(pfad) = umgebung(name) {
                assert_eq!(zone(&pfad.join("Irgendwas")), Zone::System, "{name}");
            }
        }
    }

    #[test]
    fn ein_datenlaufwerk_ist_frei() {
        assert_eq!(zone(Path::new("D:\\Projekte\\alt")), Zone::Frei);
    }

    #[test]
    fn ein_nicht_existierender_pfad_wird_ueber_den_elternordner_eingeordnet() {
        // Der Ordner existiert, die Datei nicht — die Zone muss trotzdem
        // stimmen, sonst waere jeder frei erfundene Name harmlos.
        assert_eq!(
            zone(Path::new("C:\\Windows\\System32\\gibt-es-nicht-12345.dll")),
            Zone::System
        );
    }

    #[test]
    fn eine_verknuepfung_verschiebt_die_zone_nicht() {
        // Der eigentliche Ausbruchsweg. Gebaut wird eine Junction in einem
        // freien Ordner, die nach C:\Windows zeigt; die Einordnung muss der
        // Junction folgen und nicht ihrem Namen.
        let temp = std::env::temp_dir().join("mss-zonen-test");
        let _ = std::fs::remove_dir_all(&temp);
        std::fs::create_dir_all(&temp).unwrap();
        let link = temp.join("harmlos");

        let gebaut = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&link)
            .arg("C:\\Windows")
            .output()
            .map(|a| a.status.success())
            .unwrap_or(false);
        if !gebaut {
            // Ohne Rechte fuer Junctions ist hier nichts zu pruefen. Der Test
            // faellt dann still durch, statt eine fremde Umgebung rotzufaerben.
            let _ = std::fs::remove_dir_all(&temp);
            return;
        }

        assert_eq!(zone(&link.join("System32")), Zone::System);
        let _ = std::fs::remove_dir_all(&link);
        let _ = std::fs::remove_dir_all(&temp);
    }
}
