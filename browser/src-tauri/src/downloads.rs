//! Downloads: wohin eine Datei geht und wie man sie wiederfindet.
//!
//! Die WebView lädt selbst herunter und markiert die Datei unter Windows als
//! aus dem Internet stammend (Mark of the Web); SmartScreen und Office prüfen
//! sie deshalb wie jeden Browser-Download. Der Browser öffnet heruntergeladene
//! Dateien nie von sich aus, er zeigt sie nur im Ordner.

use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager};

use crate::konfig::KonfigZustand;

/// Der Zielordner: der eingestellte, sonst der Download-Ordner des Systems.
pub fn ordner(app: &AppHandle) -> PathBuf {
    let eingestellt = app.state::<KonfigZustand>().0.lock().unwrap().download_ordner.clone();
    eingestellt
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
        .or_else(|| app.path().download_dir().ok())
        .unwrap_or_else(std::env::temp_dir)
}

/// Bereinigt einen Dateinamen, den eine Webseite vorschlägt: keine Pfade,
/// keine Steuer- und Richtungszeichen (`rechnung\u{202E}fdp.exe`), keine
/// reservierten Windows-Namen.
pub fn sicherer_name(name: &str) -> String {
    let basis = name.rsplit(['/', '\\']).next().unwrap_or_default();
    let mut sauber: String = basis
        .chars()
        .map(|c| {
            if c.is_control()
                || matches!(c, '<' | '>' | ':' | '"' | '|' | '?' | '*')
                || matches!(c as u32, 0x200B..=0x200F | 0x202A..=0x202E | 0x2066..=0x2069 | 0xFEFF)
            {
                '_'
            } else {
                c
            }
        })
        .collect();
    sauber = sauber.trim().trim_matches('.').to_string();
    if sauber.is_empty() {
        sauber = "download".into();
    }
    let stamm = sauber.split('.').next().unwrap_or_default().to_ascii_uppercase();
    const RESERVIERT: [&str; 22] = [
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ];
    if RESERVIERT.contains(&stamm.as_str()) {
        sauber = format!("_{sauber}");
    }
    sauber.chars().take(180).collect()
}

/// Ein freier Pfad im Ordner: `datei.pdf`, sonst `datei (1).pdf` usw.
pub fn freier_pfad(ordner: &Path, name: &str) -> PathBuf {
    let erster = ordner.join(name);
    if !erster.exists() {
        return erster;
    }
    let (stamm, endung) = match name.rsplit_once('.') {
        Some((s, e)) if !s.is_empty() => (s.to_string(), format!(".{e}")),
        _ => (name.to_string(), String::new()),
    };
    (1..10_000)
        .map(|n| ordner.join(format!("{stamm} ({n}){endung}")))
        .find(|p| !p.exists())
        .unwrap_or(erster)
}

/// Wohin ein angeforderter Download geht. `vorschlag` ist, was die WebView
/// vorhat; von dort zählt nur der Dateiname.
pub fn ziel(app: &AppHandle, url: &url::Url, vorschlag: &Path) -> PathBuf {
    let name = vorschlag
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .filter(|n| !n.is_empty())
        .or_else(|| url.path_segments().and_then(|mut s| s.next_back()).map(str::to_string))
        .unwrap_or_default();
    freier_pfad(&ordner(app), &sicherer_name(&name))
}

/// Zeigt eine heruntergeladene Datei im Explorer. Nur Dateien im
/// Download-Ordner: der Befehl ist kein allgemeiner Dateiöffner.
#[tauri::command(async)]
pub fn download_zeigen(app: AppHandle, pfad: String) -> Result<(), String> {
    let datei = PathBuf::from(&pfad).canonicalize().map_err(|_| "Die Datei gibt es nicht mehr.".to_string())?;
    let erlaubt = ordner(&app).canonicalize().map_err(|e| e.to_string())?;
    if !datei.starts_with(&erlaubt) {
        return Err("Die Datei liegt nicht im Download-Ordner.".into());
    }
    #[cfg(windows)]
    {
        std::process::Command::new("explorer")
            .arg(format!("/select,{}", datei.display()))
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(not(windows))]
    {
        let _ = datei;
    }
    Ok(())
}

/// Der Ordner, in den Downloads gehen, zur Anzeige in den Einstellungen.
#[tauri::command(async)]
pub fn download_ordner(app: AppHandle) -> String {
    ordner(&app).to_string_lossy().into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn namen_zeigen_was_sie_sind() {
        assert_eq!(sicherer_name("rechnung\u{202E}fdp.exe"), "rechnung_fdp.exe");
        assert_eq!(sicherer_name("../../Windows/system32/evil.dll"), "evil.dll");
        assert_eq!(sicherer_name("C:\\a\\b.txt"), "b.txt");
        assert_eq!(sicherer_name("aux.txt"), "_aux.txt");
        assert_eq!(sicherer_name("..."), "download");
        assert_eq!(sicherer_name("a:b?.pdf"), "a_b_.pdf");
    }

    #[test]
    fn vorhandene_dateien_werden_nicht_ueberschrieben() {
        let ordner = std::env::temp_dir().join(format!("msb-dl-{}", std::process::id()));
        std::fs::create_dir_all(&ordner).unwrap();
        std::fs::write(ordner.join("bericht.pdf"), b"x").unwrap();
        std::fs::write(ordner.join("bericht (1).pdf"), b"x").unwrap();
        assert_eq!(freier_pfad(&ordner, "bericht.pdf"), ordner.join("bericht (2).pdf"));
        assert_eq!(freier_pfad(&ordner, "neu.pdf"), ordner.join("neu.pdf"));
        std::fs::remove_dir_all(&ordner).unwrap();
    }
}
