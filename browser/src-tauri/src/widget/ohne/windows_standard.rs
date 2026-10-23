//! Standardbrowser unter Windows. Die App meldet sich als
//! `StartMenuInternet`-Client mit der ProgID [`PROGID`] für http und https an
//! ([`registrieren`], beim Start und aus den Velopack-Installations-Hooks in
//! `main.rs`); welche ProgID gilt, entscheidet nur der Nutzer in den
//! Windows-Einstellungen.
//!
//! Bis 10/2026 schrieb das der NSIS-Installer (`windows/hooks.nsh`). Seit
//! Velopack der einzige Windows-Installer ist, gibt es keine NSIS-Hooks mehr;
//! wer eine Anmeldung nur im Installer lässt, verliert sie beim Wechsel.

use std::path::Path;

use windows::core::{w, HSTRING, PCWSTR};
use windows::Win32::Foundation::WIN32_ERROR;
use windows::Win32::System::Registry::{
    RegCloseKey, RegCreateKeyExW, RegDeleteTreeW, RegDeleteValueW, RegOpenKeyExW, RegSetValueExW,
    HKEY, HKEY_CURRENT_USER, KEY_WRITE, REG_OPTION_NON_VOLATILE, REG_SZ,
};
use windows::Win32::UI::Shell::{SHChangeNotify, SHCNE_ASSOCCHANGED, SHCNF_IDLIST, ShellExecuteW};
use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

/// Muss mit der ProgID in den Anmeldewerten übereinstimmen.
pub const PROGID: &str = "MSBURL";
/// Der Name unter `RegisteredApplications`.
const NAME: &str = "Maunting Pontis";
/// Der Zweig des `StartMenuInternet`-Clients unter HKCU.
const CLIENT: &str = r"Software\Clients\StartMenuInternet\MauntingSecureBrowser";
/// Ein früherer Name desselben Clients. Er bleibt nach einem Wechsel stehen und
/// zeigt dann auf einen Programmpfad, den es nicht mehr gibt.
const CLIENT_ALT: &str = r"Software\Clients\StartMenuInternet\MauntingPontis";
/// Der Zweig der eigenen ProgID unter HKCU.
const KLASSEN: &str = r"Software\Classes\MSBURL";
/// Der Zweig, in dem Windows die anmeldenden Anwendungen sammelt.
const ANGEMELDET: &str = r"Software\RegisteredApplications";
/// Der Zweig, unter dem Windows Programme für „Öffnen mit“ und die
/// Standard-Apps führt.
const ANWENDUNGEN: &str = r"Software\Classes\Applications";

/// Ein Wert, den die Anmeldung schreibt. Der Standardwert eines Schlüssels
/// heißt `None`; ein leerer Wert (`Some("")`) ist bei `URL Protocol` und
/// `SupportedTypes` Absicht.
struct Wert {
    zweig: String,
    name: Option<&'static str>,
    wert: String,
}

/// Alle Werte der Anmeldung — dieselben, die bis 10/2026 in `hooks.nsh`
/// standen. Reine Berechnung, damit sie ohne Registry prüfbar ist.
fn anmeldung(exe: &str) -> Vec<Wert> {
    let befehl = format!("\"{exe}\"");
    let symbol = format!("\"{exe}\",0");
    let datei = exe.rsplit(['\\', '/']).next().unwrap_or(exe);
    let anwendung = format!(r"{ANWENDUNGEN}\{datei}");
    let eintrag = |zweig: String, name: Option<&'static str>, wert: String| Wert { zweig, name, wert };

    vec![
        eintrag(CLIENT.into(), None, NAME.into()),
        eintrag(format!(r"{CLIENT}\DefaultIcon"), None, symbol.clone()),
        eintrag(format!(r"{CLIENT}\shell\open\command"), None, befehl.clone()),
        eintrag(format!(r"{CLIENT}\Capabilities"), Some("ApplicationName"), NAME.into()),
        eintrag(format!(r"{CLIENT}\Capabilities"), Some("ApplicationIcon"), symbol.clone()),
        eintrag(
            format!(r"{CLIENT}\Capabilities"),
            Some("ApplicationDescription"),
            "Browser von Maunting Studios ohne Werbung und Tracker".into(),
        ),
        eintrag(
            format!(r"{CLIENT}\Capabilities\StartMenu"),
            Some("StartMenuInternet"),
            "MauntingSecureBrowser".into(),
        ),
        eintrag(format!(r"{CLIENT}\Capabilities\URLAssociations"), Some("http"), PROGID.into()),
        eintrag(format!(r"{CLIENT}\Capabilities\URLAssociations"), Some("https"), PROGID.into()),
        eintrag(ANGEMELDET.into(), Some(NAME), format!(r"{CLIENT}\Capabilities")),
        eintrag(KLASSEN.into(), None, format!("{NAME} URL")),
        eintrag(format!(r"{KLASSEN}\FriendlyTypeName"), None, format!("{NAME} URL")),
        eintrag(format!(r"{KLASSEN}\URL Protocol"), None, String::new()),
        eintrag(format!(r"{KLASSEN}\Application"), None, datei.into()),
        eintrag(format!(r"{KLASSEN}\DefaultIcon"), None, symbol.clone()),
        eintrag(
            format!(r"{KLASSEN}\shell\open\command"),
            None,
            format!("\"{exe}\" \"%1\""),
        ),
        // Auch als Anwendung anmelden: sonst kennt Windows die exe weder für
        // „Öffnen mit“ noch für die Liste der Standard-Apps.
        eintrag(anwendung.clone(), None, NAME.into()),
        eintrag(format!(r"{anwendung}\FriendlyAppName"), None, NAME.into()),
        eintrag(format!(r"{anwendung}\DefaultIcon"), None, symbol),
        eintrag(format!(r"{anwendung}\shell\open\command"), None, format!("\"{exe}\" \"%1\"")),
        eintrag(format!(r"{anwendung}\SupportedTypes"), Some("http"), String::new()),
        eintrag(format!(r"{anwendung}\SupportedTypes"), Some("https"), String::new()),
    ]
}

fn fehler(schritt: &str, status: WIN32_ERROR) -> String {
    format!("{schritt} fehlgeschlagen: {status:?}")
}

/// Öffnet (oder legt an) einen Schlüssel unter HKCU zum Schreiben.
fn oeffnen(zweig: &str) -> Result<HKEY, String> {
    let pfad = HSTRING::from(zweig);
    let mut schluessel = HKEY::default();
    // SAFETY: Pfad und Ausgabeschlüssel sind gültig; Klasse und Sicherheit sind leer.
    let status = unsafe {
        RegCreateKeyExW(
            HKEY_CURRENT_USER,
            &pfad,
            None,
            PCWSTR::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_WRITE,
            None,
            &mut schluessel,
            None,
        )
    };
    if status.is_ok() {
        Ok(schluessel)
    } else {
        Err(fehler(zweig, status))
    }
}

/// Setzt einen `REG_SZ`-Wert. `None` ist der Standardwert des Schlüssels.
fn schreiben(schluessel: HKEY, name: Option<&str>, wert: &str) -> Result<(), String> {
    let name = name.map(HSTRING::from);
    let daten: Vec<u8> = wert
        .encode_utf16()
        .chain(std::iter::once(0))
        .flat_map(u16::to_le_bytes)
        .collect();
    // SAFETY: `daten` lebt bis zum Ende des Aufrufs; Länge und Zeiger gehören zusammen.
    let status = unsafe {
        RegSetValueExW(
            schluessel,
            name.as_ref()
                .map_or(PCWSTR::null(), |n| PCWSTR(n.as_ptr())),
            None,
            REG_SZ,
            Some(daten.as_slice()),
        )
    };
    if status.is_ok() {
        Ok(())
    } else {
        Err(fehler("Registry-Wert", status))
    }
}

/// Windows liest die URL-Zuordnungen neu (`SHCNE_ASSOCCHANGED`).
fn zuordnungen_neu_lesen() {
    // SAFETY: ein Ereignis ohne Zeiger.
    unsafe { SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None) };
}

/// Meldet den Browser unter HKCU als `StartMenuInternet`-Client an. Windows
/// zeigt ihn dann unter „Standard-Apps“ zur Wahl; festlegen kann ihn nur der
/// Nutzer selbst.
pub fn registrieren(exe: &Path) -> Result<(), String> {
    registrieren_fuer(&exe.display().to_string())
}

fn registrieren_fuer(exe: &str) -> Result<(), String> {
    for wert in anmeldung(exe) {
        let schluessel = oeffnen(&wert.zweig)?;
        let ergebnis = schreiben(schluessel, wert.name, &wert.wert);
        // SAFETY: `schluessel` stammt aus `oeffnen` und wird genau einmal geschlossen.
        unsafe {
            let _ = RegCloseKey(schluessel);
        };
        ergebnis?;
    }
    // Der frühere Clientname (vor der Umbenennung) zeigt auf den NSIS-Pfad und
    // bliebe sonst als toter Eintrag in den Standard-Apps stehen.
    zweig_entfernen(CLIENT_ALT)?;
    zuordnungen_neu_lesen();
    Ok(())
}

/// Löscht einen eigenen Zweig; ein fehlender Zweig gilt als entfernt.
fn zweig_entfernen(zweig: &str) -> Result<(), String> {
    let pfad = HSTRING::from(zweig);
    // SAFETY: nur der Pfad, kein Ausgabewert.
    let status = unsafe { RegDeleteTreeW(HKEY_CURRENT_USER, &pfad) };
    if !status.is_ok() && status != windows::Win32::Foundation::ERROR_FILE_NOT_FOUND {
        return Err(fehler(zweig, status));
    }
    Ok(())
}

/// Nimmt die Anmeldung vollständig zurück (Deinstallation). Fehlende Werte
/// gelten als entfernt, damit ein zweiter Lauf nicht scheitert.
pub fn entfernen() -> Result<(), String> {
    for zweig in [CLIENT, CLIENT_ALT, KLASSEN] {
        zweig_entfernen(zweig)?;
    }
    // Der Anwendungseintrag trägt den Dateinamen der laufenden exe.
    if let Some(datei) = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.file_name().map(|name| name.to_string_lossy().into_owned()))
    {
        zweig_entfernen(&format!(r"{ANWENDUNGEN}\{datei}"))?;
    }
    let pfad = HSTRING::from(ANGEMELDET);
    let mut schluessel = HKEY::default();
    // SAFETY: Pfad und Ausgabeschlüssel sind gültig.
    let status = unsafe { RegOpenKeyExW(HKEY_CURRENT_USER, &pfad, None, KEY_WRITE, &mut schluessel) };
    if status.is_ok() {
        let name = HSTRING::from(NAME);
        // SAFETY: `schluessel` ist geöffnet; danach genau einmal geschlossen.
        let geloescht = unsafe { RegDeleteValueW(schluessel, &name) };
        // SAFETY: siehe oben.
        unsafe {
            let _ = RegCloseKey(schluessel);
        };
        if !geloescht.is_ok() && geloescht != windows::Win32::Foundation::ERROR_FILE_NOT_FOUND {
            return Err(fehler("RegisteredApplications", geloescht));
        }
    } else if status != windows::Win32::Foundation::ERROR_FILE_NOT_FOUND {
        return Err(fehler(ANGEMELDET, status));
    }
    zuordnungen_neu_lesen();
    Ok(())
}

/// Die ProgID, die Windows für https oder http gewählt hat. Neuere Windows-11-Fassungen
/// schreiben `UserChoiceLatest` und lassen `UserChoice` veraltet stehen.
fn gewaehlt() -> Option<String> {
    for protokoll in ["https", "http"] {
        for zweig in ["UserChoiceLatest", "UserChoice"] {
            let pfad = HSTRING::from(format!(
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{protokoll}\{zweig}"
            ));
            let mut puffer = [0u16; 256];
            let mut groesse = std::mem::size_of_val(&puffer) as u32;
            // SAFETY: Puffer und Größe gehören zusammen; RegGetValueW schreibt höchstens `groesse` Bytes.
            let status = unsafe {
                windows::Win32::System::Registry::RegGetValueW(
                    HKEY_CURRENT_USER,
                    &pfad,
                    w!("ProgId"),
                    windows::Win32::System::Registry::RRF_RT_REG_SZ,
                    None,
                    Some(puffer.as_mut_ptr().cast()),
                    Some(&mut groesse),
                )
            };
            if status.is_ok() && groesse > 2 {
                let zeichen = (groesse as usize / 2).saturating_sub(1).min(puffer.len());
                let progid = String::from_utf16_lossy(&puffer[..zeichen]);
                let bereinigt = progid.trim();
                if !bereinigt.is_empty() {
                    return Some(bereinigt.to_string());
                }
            }
        }
    }
    None
}

pub fn ist_standard() -> bool {
    gewaehlt()
        .map(|g| g.eq_ignore_ascii_case(PROGID))
        .unwrap_or(false)
}

/// Öffnet „Standard-Apps“ direkt beim Browser.
pub fn einstellungen_oeffnen() -> Result<(), String> {
    let ziel = HSTRING::from(format!("ms-settings:defaultapps?registeredAppUser={}", NAME.replace(' ', "%20")));
    // SAFETY: alle Zeiger stammen aus lebenden HSTRINGs bzw. Konstanten.
    let ergebnis = unsafe { ShellExecuteW(None, w!("open"), &ziel, None, None, SW_SHOWNORMAL) };
    // Werte bis 32 sind Fehlercodes (Win32-Doku zu ShellExecute).
    if ergebnis.0 as isize > 32 {
        Ok(())
    } else {
        Err("Windows hat die Einstellungen nicht geöffnet.".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wert<'a>(liste: &'a [Wert], zweig: &str, name: Option<&str>) -> Option<&'a str> {
        liste
            .iter()
            .find(|w| w.zweig == zweig && w.name.map(str::to_string).as_deref() == name)
            .map(|w| w.wert.as_str())
    }

    /// Anmeldung und Prüfung nennen dieselbe ProgID und denselben Namen.
    #[test]
    fn anmeldung_und_pruefung_passen_zusammen() {
        let liste = anmeldung(r"C:\Users\tester\AppData\Local\Programs\Maunting Pontis\MauntingPontis.exe");
        assert_eq!(wert(&liste, CLIENT, None), Some(NAME));
        assert_eq!(
            wert(&liste, r"Software\Clients\StartMenuInternet\MauntingSecureBrowser\Capabilities\URLAssociations", Some("http")),
            Some(PROGID)
        );
        assert_eq!(
            wert(&liste, r"Software\Clients\StartMenuInternet\MauntingSecureBrowser\Capabilities\URLAssociations", Some("https")),
            Some(PROGID)
        );
        assert_eq!(wert(&liste, ANGEMELDET, Some(NAME)), Some(r"Software\Clients\StartMenuInternet\MauntingSecureBrowser\Capabilities"));
        assert_eq!(wert(&liste, KLASSEN, None), Some("Maunting Pontis URL"));
        // Ohne den Anwendungseintrag kennt Windows die exe weder für „Öffnen
        // mit“ noch als Ziel der Standard-Apps.
        assert_eq!(
            wert(&liste, r"Software\Classes\Applications\MauntingPontis.exe", None),
            Some(NAME)
        );
        assert_eq!(
            wert(&liste, r"Software\Classes\Applications\MauntingPontis.exe\SupportedTypes", Some("https")),
            Some("")
        );
    }

    /// Jeder Wert wird genau einmal geschrieben, und kein Wert zeigt mehr auf
    /// den früheren Clientnamen. Doppelte Einträge würden sich beim Anlegen
    /// gegenseitig überschreiben; der alte Zweig bliebe als Leiche stehen.
    #[test]
    fn die_anmeldung_ist_eindeutig_und_ohne_alten_client() {
        let liste = anmeldung(r"C:\Program Files\Maunting Pontis\MauntingPontis.exe");
        let mut gesehen = std::collections::HashSet::new();
        for wert in &liste {
            assert!(
                gesehen.insert((wert.zweig.clone(), wert.name)),
                "doppelter Wert: {} {:?}",
                wert.zweig,
                wert.name
            );
            assert!(!wert.zweig.starts_with(CLIENT_ALT), "alter Client: {}", wert.zweig);
        }
        // Der Anwendungszweig wird auch bei Schrägstrichen richtig benannt.
        let vorwaerts = anmeldung("C:/Program Files/Maunting Pontis/MauntingPontis.exe");
        assert_eq!(
            wert(&vorwaerts, r"Software\Classes\Applications\MauntingPontis.exe", None),
            Some(NAME)
        );
    }

    /// Die Befehle nennen den Pfad in Anführungszeichen (ein Leerzeichen im
    /// Programmpfad zerlegte ihn sonst) und die Dateizuordnung gibt `%1` weiter.
    #[test]
    fn befehle_sind_quotiert_und_geben_die_adresse_weiter() {
        let liste = anmeldung(r"C:\Program Files\Maunting Pontis\MauntingPontis.exe");
        assert_eq!(
            wert(&liste, r"Software\Clients\StartMenuInternet\MauntingSecureBrowser\shell\open\command", None),
            Some(r#""C:\Program Files\Maunting Pontis\MauntingPontis.exe""#)
        );
        assert_eq!(
            wert(&liste, r"Software\Classes\MSBURL\shell\open\command", None),
            Some(r#""C:\Program Files\Maunting Pontis\MauntingPontis.exe" "%1""#)
        );
        assert_eq!(
            wert(&liste, r"Software\Clients\StartMenuInternet\MauntingSecureBrowser\DefaultIcon", None),
            Some(r#""C:\Program Files\Maunting Pontis\MauntingPontis.exe",0"#)
        );
        assert_eq!(
            wert(&liste, r"Software\Classes\Applications\MauntingPontis.exe\shell\open\command", None),
            Some(r#""C:\Program Files\Maunting Pontis\MauntingPontis.exe" "%1""#)
        );
        assert_eq!(wert(&liste, r"Software\Classes\MSBURL\URL Protocol", None), Some(""));
        assert_eq!(wert(&liste, r"Software\Classes\MSBURL\Application", None), Some("MauntingPontis.exe"));
    }

    #[test]
    fn gewaehlt_liefert_aktuellen_browser() {
        let g = super::gewaehlt();
        println!("gewaehlt: {:?}", g);
        assert!(g.is_some(), "Ein Standardbrowser sollte unter Windows gewählt sein");
    }
}
