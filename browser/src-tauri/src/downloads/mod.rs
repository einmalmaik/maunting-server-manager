//! Downloads: wohin eine Datei geht und wie man sie wiederfindet.
//!
//! Die WebView lädt in die Quarantäne (`quarantaene.rs`). Erst wenn der
//! Virenschutz von Windows die Datei durchgelassen hat (`pruefung.rs`), kommt
//! sie in den Download-Ordner oder an den Ort, den man im Speicherdialog
//! gewählt hat. Der Browser öffnet heruntergeladene Dateien nie von sich aus,
//! er zeigt sie nur im Ordner.
//!
//! Unter Android lädt der Download-Dienst des Systems in `Download/`
//! (`Herunterladen.kt`); von hier kommt dort nur der Dateiname.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

use tauri::{AppHandle, Manager};

use crate::konfig::KonfigZustand;

#[cfg(windows)]
pub mod pruefung;
pub mod quarantaene;

/// Dateien, die in dieser Sitzung fertig wurden; nur sie und der
/// Download-Ordner lassen sich im Explorer zeigen.
static FERTIG: LazyLock<Mutex<HashSet<PathBuf>>> = LazyLock::new(Default::default);

/// Soll vor jedem Download der Speicherdialog kommen?
pub fn fragen(app: &AppHandle) -> bool {
    app.state::<KonfigZustand>().0.lock().unwrap().download_fragen
}

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

/// Der Dateiname eines Downloads. `vorschlag` ist, was die WebView vorhat;
/// von dort zählt nur der Name, sonst das Ende der Adresse.
pub fn name_fuer(url: &str, vorschlag: &Path) -> String {
    let name = vorschlag
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .filter(|n| !n.is_empty())
        .or_else(|| url::Url::parse(url).ok()?.path_segments()?.next_back().map(str::to_string))
        .unwrap_or_default();
    sicherer_name(&name)
}

/// Legt eine geprüfte Datei an ihr Ziel: den gewählten Ort, sonst den nächsten
/// freien Namen im Download-Ordner. Unter einem Schloss, damit zwei Downloads
/// mit gleichem Namen nicht beide denselben freien Platz nehmen.
pub fn ablegen(app: &AppHandle, datei: &Path, name: &str, gewaehlt: Option<PathBuf>) -> std::io::Result<PathBuf> {
    static SCHLOSS: Mutex<()> = Mutex::new(());
    let _schloss = SCHLOSS.lock().unwrap();
    let ziel = gewaehlt.unwrap_or_else(|| freier_pfad(&ordner(app), name));
    quarantaene::verschieben(datei, &ziel)?;
    FERTIG.lock().unwrap().insert(ziel.canonicalize().unwrap_or_else(|_| ziel.clone()));
    Ok(ziel)
}

/// Zeigt eine heruntergeladene Datei im Explorer. Nur Dateien im
/// Download-Ordner und solche, die diese Sitzung woanders abgelegt hat: der
/// Befehl ist kein allgemeiner Dateiöffner. Unter Android legt der
/// Download-Dienst des Systems ab (`Herunterladen.kt`), gezeigt wird seine Liste.
#[tauri::command(async)]
pub fn download_zeigen(app: AppHandle, pfad: String) -> Result<(), String> {
    #[cfg(target_os = "android")]
    {
        let _ = pfad;
        crate::tabs::android::downloads_zeigen(&app)
    }
    #[cfg(not(target_os = "android"))]
    {
        let datei = PathBuf::from(&pfad).canonicalize().map_err(|_| "Die Datei gibt es nicht mehr.".to_string())?;
        let erlaubt = ordner(&app).canonicalize().map_err(|e| e.to_string())?;
        if !datei.starts_with(&erlaubt) && !FERTIG.lock().unwrap().contains(&datei) {
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
    fn der_name_kommt_aus_dem_vorschlag_sonst_aus_der_adresse() {
        // Android schlägt vor, was `URLUtil.guessFileName` aus Kopfzeile und Adresse macht.
        assert_eq!(name_fuer("https://x.example/a.pdf", Path::new("rechnung\u{202E}fdp.exe")), "rechnung_fdp.exe");
        assert_eq!(name_fuer("https://x.example/dir/bericht.pdf", Path::new("")), "bericht.pdf");
        assert_eq!(name_fuer("", Path::new("../geheim/aux.txt")), "_aux.txt");
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
