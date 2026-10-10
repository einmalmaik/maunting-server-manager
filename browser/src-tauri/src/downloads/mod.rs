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
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{LazyLock, Mutex};

use tauri::{AppHandle, Manager};

use crate::konfig::KonfigZustand;

#[cfg(windows)]
pub mod pruefung;
pub mod quarantaene;

/// Dateien, die in dieser Sitzung fertig wurden; nur sie und der
/// Download-Ordner lassen sich im Explorer zeigen.
static FERTIG: LazyLock<Mutex<HashSet<PathBuf>>> = LazyLock::new(Default::default);

/// Die Nummer, unter der ein Download in allen Meldungen steht (`TabEreignis::Download`).
pub fn naechste_nr() -> u64 {
    static NR: AtomicU64 = AtomicU64::new(1);
    NR.fetch_add(1, Ordering::Relaxed)
}

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

/// Höchstens so viele Zeichen; die Endung bleibt beim Kürzen erhalten.
const HOECHSTENS_NAME: usize = 180;
const HOECHSTENS_ENDUNG: usize = 16;

/// Zeichen, die nichts zeigen oder die Leserichtung umkehren. Mit ihnen sähe
/// `rechnung\u{202E}fdp.exe` aus wie `rechnungexe.pdf`. Dieselbe Liste wie im
/// Smart System (`datei_speichern.rs`).
pub fn ist_unsichtbar(z: char) -> bool {
    matches!(
        z,
        '\u{00AD}'
            | '\u{034F}'
            | '\u{061C}'
            | '\u{115F}'..='\u{1160}'
            | '\u{17B4}'..='\u{17B5}'
            | '\u{180E}'
            | '\u{200B}'..='\u{200F}'
            | '\u{2028}'..='\u{202E}'
            | '\u{2060}'..='\u{206F}'
            | '\u{3164}'
            | '\u{FEFF}'
            | '\u{FFA0}'
            | '\u{FFF9}'..='\u{FFFB}'
            | '\u{1BCA0}'..='\u{1BCA3}'
            | '\u{1D173}'..='\u{1D17A}'
            | '\u{E0000}'..='\u{E007F}'
    )
}

/// Windows-Gerätenamen gelten mit jeder Endung (`aux.txt`) und mit
/// Leerzeichen vor dem Punkt (`CON .txt`).
fn ist_geraetename(name: &str) -> bool {
    let stamm = name.split('.').next().unwrap_or("").trim_end().to_ascii_uppercase();
    matches!(stamm.as_str(), "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$")
        || ((stamm.starts_with("COM") || stamm.starts_with("LPT"))
            && stamm.chars().count() == 4
            && stamm.chars().nth(3).is_some_and(|z| z.is_ascii_digit() || "¹²³".contains(z)))
}

/// Kürzt auf `HOECHSTENS_NAME` Zeichen und behält die Endung. Am Ende bleibt
/// kein Punkt und kein Leerzeichen: Windows wirft beides beim Anlegen weg, und
/// aus `….exe.` würde `….exe`.
fn kuerzen(name: &str) -> String {
    if name.chars().count() <= HOECHSTENS_NAME {
        return name.to_string();
    }
    if let Some((stamm, endung)) = name.rsplit_once('.') {
        let laenge = endung.chars().count();
        if (1..=HOECHSTENS_ENDUNG).contains(&laenge) {
            let stamm: String = stamm.chars().take(HOECHSTENS_NAME - 1 - laenge).collect();
            let stamm = stamm.trim_end_matches(['.', ' ']);
            if !stamm.is_empty() {
                return format!("{stamm}.{endung}");
            }
        }
    }
    let name: String = name.chars().take(HOECHSTENS_NAME).collect();
    name.trim_end_matches(['.', ' ']).to_string()
}

/// Bereinigt einen Dateinamen, den eine Webseite vorschlägt: keine Pfade,
/// keine Steuer-, Richtungs- und unsichtbaren Zeichen, keine reservierten
/// Windows-Namen, und ein langer Name behält seine Endung.
pub fn sicherer_name(name: &str) -> String {
    let basis = name.rsplit(['/', '\\']).next().unwrap_or_default();
    let sauber: String = basis
        .chars()
        .map(|c| {
            if c.is_control() || ist_unsichtbar(c) || matches!(c, '<' | '>' | ':' | '"' | '|' | '?' | '*') {
                '_'
            } else {
                c
            }
        })
        .collect();
    let sauber = kuerzen(sauber.trim().trim_start_matches('.').trim_end_matches(['.', ' ']));
    if sauber.is_empty() {
        "download".into()
    } else if ist_geraetename(&sauber) {
        kuerzen(&format!("_{sauber}"))
    } else {
        sauber
    }
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

/// Legt eine Datei ab, die der Browser selbst erzeugt hat (Screenshot): erst
/// in die Quarantäne, dann wie jeder Download an den nächsten freien Namen.
/// So liegt am Ziel nie eine halbe Datei (AGENTS.md Punkt 83).
pub fn erzeugt_ablegen(app: &AppHandle, name: &str, daten: &[u8]) -> std::io::Result<PathBuf> {
    let platz = quarantaene::neuer_platz(app, name).ok_or_else(|| std::io::Error::other("Kein Platz in der Quarantäne"))?;
    let ergebnis = std::fs::write(&platz, daten).and_then(|()| ablegen(app, &platz, name, None));
    quarantaene::wegraeumen(&platz);
    ergebnis
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
    #[cfg(windows)]
    {
        let datei = PathBuf::from(&pfad).canonicalize().map_err(|_| "Die Datei gibt es nicht mehr.".to_string())?;
        let erlaubt = ordner(&app).canonicalize().map_err(|e| e.to_string())?;
        if !datei.starts_with(&erlaubt) && !FERTIG.lock().unwrap().contains(&datei) {
            return Err("Die Datei liegt nicht im Download-Ordner.".into());
        }
        im_explorer_zeigen(&datei)
    }
}

/// Öffnet den Ordner der Datei im Explorer und wählt sie aus. Über die Shell,
/// nicht als `explorer /select,<pfad>`: Explorer liest Kommas im Namen, den
/// eine Seite vorschlägt, als Trenner seiner Argumente (bis 09.10.2026).
#[cfg(windows)]
fn im_explorer_zeigen(datei: &Path) -> Result<(), String> {
    use windows::core::HSTRING;
    use windows::Win32::System::Com::{CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED};
    use windows::Win32::UI::Shell::{ILCreateFromPathW, ILFree, SHOpenFolderAndSelectItems};
    unsafe {
        let com = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let eintrag = ILCreateFromPathW(&HSTRING::from(datei.as_os_str()));
        let ergebnis = if eintrag.is_null() {
            Err("Die Datei gibt es nicht mehr.".to_string())
        } else {
            let ergebnis = SHOpenFolderAndSelectItems(eintrag, None, 0).map_err(|e| e.to_string());
            ILFree(Some(eintrag));
            ergebnis
        };
        if com.is_ok() {
            CoUninitialize();
        }
        ergebnis
    }
}

/// Der Ordner, in den Downloads gehen, zur Anzeige in den Einstellungen.
#[tauri::command(async)]
pub fn download_ordner(app: AppHandle) -> String {
    ordner(&app).to_string_lossy().into_owned()
}

/// Die Adresse, unter der ein Download aus dem Netz lädt; `None` für alles
/// außer http(s). `http://` aus dem Internet wird `https://`, ohne Rückfall:
/// wer mitliest, könnte eine unverschlüsselte Datei tauschen (ein APK). Im
/// eigenen Netz bleibt sie, wie sie ist (`tabs::https::im_eigenen_netz`). Unter
/// Android lud ein Download bis 10.10.2026 auch von einer HTTPS-Seite
/// unverschlüsselt.
pub fn netzadresse(url: &str) -> Option<String> {
    let mut u = url::Url::parse(url).ok()?;
    match u.scheme() {
        "https" => {}
        "http" if crate::tabs::https::im_eigenen_netz(&u) => {}
        "http" => u.set_scheme("https").ok()?,
        _ => return None,
    }
    Some(u.into())
}

/// Gehört `a` zur selben Site wie `b` (Schema und registrierbare Domain, wie
/// `SameSite`)? Unter Android gehen Cookies nur dann an einen Download: der
/// CookieManager sagt nicht, welche `Strict` sind, und eine fremde Seite
/// konnte bis 10.10.2026 per Download eine Anfrage mit allen Cookies einer
/// anderen Site auslösen.
pub fn gleiche_site(a: &str, b: &str) -> bool {
    let (Ok(a), Ok(b)) = (url::Url::parse(a), url::Url::parse(b)) else { return false };
    if a.scheme() != b.scheme() || !matches!(a.scheme(), "http" | "https") {
        return false;
    }
    match (a.host(), b.host()) {
        (Some(url::Host::Domain(x)), Some(url::Host::Domain(y))) => {
            let (x, y) = (x.trim_end_matches('.').to_ascii_lowercase(), y.trim_end_matches('.').to_ascii_lowercase());
            let site = |h: &str| psl::domain_str(h).map(str::to_string).unwrap_or_else(|| h.to_string());
            site(&x) == site(&y)
        }
        (Some(x), Some(y)) => x == y,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn downloads_aus_dem_internet_laden_verschluesselt() {
        assert_eq!(netzadresse("http://dl.example.com/app.apk").as_deref(), Some("https://dl.example.com/app.apk"));
        assert_eq!(netzadresse("https://dl.example.com/a").as_deref(), Some("https://dl.example.com/a"));
        assert_eq!(netzadresse("http://dl.example.com:8080/a").as_deref(), Some("https://dl.example.com:8080/a"));
        assert_eq!(netzadresse("http://192.168.1.5/a.zip").as_deref(), Some("http://192.168.1.5/a.zip"));
        assert_eq!(netzadresse("http://fritz.box/a.zip").as_deref(), Some("http://fritz.box/a.zip"));
        assert_eq!(netzadresse("ftp://x.example/a"), None);
        assert_eq!(netzadresse("javascript:alert(1)"), None);
    }

    #[test]
    fn cookies_nur_fuer_dieselbe_site() {
        assert!(gleiche_site("https://dl.example.co.uk/a", "https://www.example.co.uk/"));
        assert!(!gleiche_site("https://a.co.uk/", "https://b.co.uk/"), "öffentliches Suffix ist keine Site");
        assert!(!gleiche_site("https://bank.example/konto", "https://boese.example/"));
        assert!(!gleiche_site("http://example.com/", "https://example.com/"), "anderes Schema");
        assert!(!gleiche_site("https://nutzer.github.io/", "https://anderer.github.io/"));
        assert!(gleiche_site("http://192.168.1.5/a", "http://192.168.1.5/"));
        assert!(!gleiche_site("https://example.com/", "about:blank"));
    }

    #[test]
    fn namen_zeigen_was_sie_sind() {
        assert_eq!(sicherer_name("rechnung\u{202E}fdp.exe"), "rechnung_fdp.exe");
        assert_eq!(sicherer_name("../../Windows/system32/evil.dll"), "evil.dll");
        assert_eq!(sicherer_name("C:\\a\\b.txt"), "b.txt");
        assert_eq!(sicherer_name("aux.txt"), "_aux.txt");
        assert_eq!(sicherer_name("..."), "download");
        assert_eq!(sicherer_name("a:b?.pdf"), "a_b_.pdf");
    }

    /// AGENTS.md 83. Bis 09.10.2026 schnitt `sicherer_name` nach 180 Zeichen
    /// ab: die Endung fiel weg, oder aus `….exe<Füllung>.txt` wurde `….exe`.
    #[test]
    fn ein_langer_name_behaelt_seine_endung() {
        assert!(sicherer_name(&format!("{}.pdf", "a".repeat(300))).ends_with(".pdf"));
        let vorschlag = format!("{}.exe{}.txt", "a".repeat(176), "b".repeat(20));
        assert!(sicherer_name(&vorschlag).ends_with(".txt"));
        assert!(name_fuer("https://x.example/a", Path::new(&vorschlag)).ends_with(".txt"));
        let ohne_endung = sicherer_name(&format!("{}.exe.{}", "x".repeat(175), "y".repeat(40)));
        assert!(ohne_endung.chars().count() <= 180 && !ohne_endung.ends_with('.'));
    }

    /// Windows wirft einen Punkt am Ende beim Anlegen weg; aus `….exe.` würde `….exe`.
    #[cfg(windows)]
    #[test]
    fn das_dateisystem_legt_an_was_der_name_sagt() {
        let name = sicherer_name(&format!("{}.exe.txt", "x".repeat(175)));
        let ordner = std::env::temp_dir().join(format!("msb-name-{}", std::process::id()));
        std::fs::create_dir_all(&ordner).unwrap();
        std::fs::write(ordner.join(&name), b"MZ").unwrap();
        let angelegt: Vec<String> =
            std::fs::read_dir(&ordner).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        std::fs::remove_dir_all(&ordner).unwrap();
        assert_eq!(angelegt, [name]);
    }

    #[test]
    fn alle_geraetenamen_bekommen_einen_strich() {
        for name in ["CONIN$.txt", "CONOUT$.log", "COM¹.txt", "LPT³.pdf", "COM0.txt", "LPT0.txt", "CON .txt", "nul"] {
            assert!(sicherer_name(name).starts_with('_'), "{name}");
        }
        assert_eq!(sicherer_name("COM10.txt"), "COM10.txt");
    }

    #[test]
    fn unsichtbare_zeichen_fallen_aus_dem_namen() {
        for z in ['\u{061C}', '\u{00AD}', '\u{2060}', '\u{2064}', '\u{180E}', '\u{3164}', '\u{E0041}', '\u{FFF9}'] {
            assert_eq!(sicherer_name(&format!("rechnung{z}.pdf")), "rechnung_.pdf", "U+{:04X}", z as u32);
        }
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
