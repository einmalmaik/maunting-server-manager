//! Standardbrowser unter Windows. Der Installer meldet den Browser als
//! `StartMenuInternet`-Client mit der ProgID [`PROGID`] für http und https an
//! (`windows/hooks.nsh`); welche ProgID gilt, entscheidet nur der Nutzer in den
//! Windows-Einstellungen. Hier wird nur gelesen.

use windows::core::{w, HSTRING};
use windows::Win32::System::Registry::{RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_SZ};
use windows::Win32::UI::Shell::ShellExecuteW;
use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

/// Muss mit `MSB_PROGID` in `windows/hooks.nsh` übereinstimmen.
pub const PROGID: &str = "MSBURL";
/// Der Name unter `RegisteredApplications`, ebenfalls aus `hooks.nsh`.
const NAME: &str = "Maunting Pontis";

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
                RegGetValueW(
                    HKEY_CURRENT_USER,
                    &pfad,
                    w!("ProgId"),
                    RRF_RT_REG_SZ,
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
    /// Installer und Prüfung nennen dieselbe ProgID und denselben Namen.
    #[test]
    fn installer_und_pruefung_passen_zusammen() {
        let hooks = include_str!("../../../windows/hooks.nsh");
        assert!(hooks.contains(&format!("!define MSB_PROGID \"{}\"", super::PROGID)));
        assert!(hooks.contains(&format!("!define MSB_NAME \"{}\"", super::NAME)));
    }

    #[test]
    fn gewaehlt_liefert_aktuellen_browser() {
        let g = super::gewaehlt();
        println!("gewaehlt: {:?}", g);
        assert!(g.is_some(), "Ein Standardbrowser sollte unter Windows gewählt sein");
    }
}
