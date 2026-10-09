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
const NAME: &str = "Maunting Secure Browser";

/// Die ProgID, die Windows für https gewählt hat. Neuere Windows-11-Fassungen
/// schreiben `UserChoiceLatest` und lassen `UserChoice` veraltet stehen.
fn gewaehlt() -> Option<String> {
    ["UserChoiceLatest", "UserChoice"].iter().find_map(|zweig| {
        let pfad = HSTRING::from(format!(
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\{zweig}"
        ));
        let mut puffer = [0u16; 256];
        let mut groesse = std::mem::size_of_val(&puffer) as u32;
        // SAFETY: Puffer und Größe gehören zusammen; RegGetValueW schreibt höchstens `groesse` Bytes.
        unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                &pfad,
                w!("ProgId"),
                RRF_RT_REG_SZ,
                None,
                Some(puffer.as_mut_ptr().cast()),
                Some(&mut groesse),
            )
        }
        .ok()
        .ok()?;
        let zeichen = (groesse as usize / 2).saturating_sub(1).min(puffer.len());
        Some(String::from_utf16_lossy(&puffer[..zeichen]))
    })
}

pub fn ist_standard() -> bool {
    gewaehlt().as_deref() == Some(PROGID)
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
}
