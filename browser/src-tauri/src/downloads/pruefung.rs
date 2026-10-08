//! Prüft eine heruntergeladene Datei mit dem Virenschutz, den Windows kennt
//! (`IAttachmentExecute::Save`): Microsoft Defender oder einen anderen
//! eingetragenen. Dabei schreibt Windows die Herkunftsmarke (Mark of the Web),
//! damit SmartScreen und Office die Datei beim Öffnen wie jeden Download
//! behandeln. Die Prüfung kann Sekunden dauern und läuft deshalb nie auf dem
//! UI-Faden (Regel 122).

use std::path::Path;

use windows::core::{GUID, HSTRING};
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED};
use windows::Win32::UI::Shell::{AttachmentServices, IAttachmentExecute};

/// Woran Windows den Browser bei der Prüfung erkennt (eigene Einstellungen je Anwendung).
const MSB: GUID = GUID::from_u128(0x6d1c2a4e_8f3b_4c71_9a52_3e0b7d94f1c6);

/// Die Adresse für die Herkunftsmarke: nur http(s), ohne Zugangsdaten.
fn quelle(url: &str) -> Option<String> {
    let mut u = url::Url::parse(url).ok()?;
    if !matches!(u.scheme(), "http" | "https") {
        return None;
    }
    let _ = u.set_username("");
    let _ = u.set_password(None);
    Some(u.into())
}

/// `true`, wenn der Virenschutz die Datei durchlässt und sie danach noch da ist.
pub fn pruefen(datei: &Path, url: &str) -> bool {
    unsafe {
        let com = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let ergebnis = (|| -> windows::core::Result<()> {
            let pruefer: IAttachmentExecute = CoCreateInstance(&AttachmentServices, None, CLSCTX_INPROC_SERVER)?;
            pruefer.SetClientGuid(&MSB)?;
            pruefer.SetLocalPath(&HSTRING::from(datei.as_os_str()))?;
            if let Some(q) = quelle(url) {
                pruefer.SetSource(&HSTRING::from(q))?;
            }
            pruefer.Save()
        })();
        if com.is_ok() {
            CoUninitialize();
        }
        if let Err(fehler) = &ergebnis {
            eprintln!("[MSB] Virenschutz hat einen Download abgelehnt: {fehler}");
        }
        ergebnis.is_ok() && datei.is_file()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn herkunft_ohne_zugangsdaten() {
        assert_eq!(quelle("https://nutzer:geheim@example.com/a.zip").as_deref(), Some("https://example.com/a.zip"));
        assert_eq!(quelle("blob:https://example.com/123"), None);
        assert_eq!(quelle("data:text/plain,x"), None);
    }

    /// Legt die EICAR-Prüfdatei an; Defender meldet dabei einen Fund. Nur von
    /// Hand: `cargo test -- --ignored eicar`, und `TMP` darf nicht vom
    /// Virenschutz ausgenommen sein.
    #[test]
    #[ignore]
    fn eicar_wird_abgelehnt_und_ist_weg() {
        let teile = ["X5O!P%@AP[4", "\\PZX54(P^)7CC)7}$", "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$", "H+H*"];
        let datei = std::env::temp_dir().join(format!("msb-eicar-{}.com", std::process::id()));
        if std::fs::write(&datei, teile.concat()).is_err() {
            return; // Schon beim Schreiben verhindert: auch das ist ein Fund.
        }
        assert!(!pruefen(&datei, "https://example.com/eicar.com"));
        assert!(!datei.exists());
    }

    #[test]
    fn eine_harmlose_datei_kommt_durch() {
        let datei = std::env::temp_dir().join(format!("msb-pruefung-{}.txt", std::process::id()));
        std::fs::write(&datei, b"harmlos").unwrap();
        assert!(pruefen(&datei, "https://example.com/harmlos.txt"));
        std::fs::remove_file(&datei).unwrap();
    }
}
