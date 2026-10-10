//! Die Symbole der Kacheln auf der Startseite, von der Website selbst.
//!
//! Wie Brave holt der Browser für jede Kachel die Startseite ihrer Herkunft,
//! sucht dort `apple-touch-icon` oder `icon` und nimmt sonst `/favicon.ico`.
//! Die Oberfläche lädt nie eine Adresse der Seite (Punkt 136) und dekodiert
//! kein Bild aus dem Netz: das tut hier `image` (PNG, ICO), mit Grenzen für
//! Bytes und Pixel, und gibt ein neu kodiertes PNG von höchstens 64 px heraus.
//!
//! Jeder Schritt, auch jede Weiterleitung, geht durch dieselbe Prüfung wie ein
//! Tab (`tabs::vorab`, Jugendschutz und Schild), ohne Cookies. Ergebnisse liegen
//! in `kachelsymbole/` und gehen mit ihrer Kachel.

use std::collections::HashSet;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use base64::Engine as _;
use tauri::{AppHandle, Manager};

use crate::schild::AnfrageArt;
use crate::tabs::https::im_eigenen_netz;
use crate::tabs::{vorab, Vorab};

const FRIST: Duration = Duration::from_secs(10);
const SEITE_MAX: usize = 512 * 1024;
const BILD_MAX: usize = 256 * 1024;
const WEITERLEITUNGEN_MAX: usize = 5;
/// Seitenlänge, die ein Bild höchstens haben darf, bevor es dekodiert wird.
const PIXEL_MAX: u32 = 1024;
const KLEINSTE: u32 = 16;
const GROESSE: u32 = 64;
/// Ein gefundenes Symbol gilt so lange, danach wird neu geholt.
const GILT: Duration = Duration::from_secs(30 * 24 * 3600);
/// Nach einem Fehlschlag wird so lange nicht neu gefragt.
const LEER_GILT: Duration = Duration::from_secs(7 * 24 * 3600);

/// Herkunft (Schema, Host, Port) einer Kachel, so wie sie geladen würde.
fn herkunft(url: &str) -> Option<url::Url> {
    let u = url::Url::parse(&crate::downloads::netzadresse(url)?).ok()?;
    u.host_str()?;
    url::Url::parse(&u.origin().ascii_serialization()).ok()
}

/// Dateiname im Cache: nur Kleinbuchstaben, Ziffern, Punkt und Bindestrich.
fn schluessel(herkunft: &url::Url) -> String {
    herkunft
        .origin()
        .ascii_serialization()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '.' || c == '-' { c.to_ascii_lowercase() } else { '_' })
        .collect()
}

/// Darf der Browser diese Adresse für eine Kachel holen? Wie ein Tab: das
/// Hauptdokument nach `vorab`, alles andere zusätzlich nach dem Schild. Ins
/// eigene Netz nur, wenn die Kachel selbst dort liegt.
fn erlaubt(u: &url::Url, start: &url::Url, seite: bool) -> bool {
    let netz_ok = crate::downloads::netzadresse(u.as_str()).as_deref() == Some(u.as_str());
    if !netz_ok || (im_eigenen_netz(u) && !im_eigenen_netz(start)) {
        return false;
    }
    match vorab(u.as_str(), seite) {
        Vorab::Blocken(_) => false,
        Vorab::Laden { .. } => true,
        Vorab::Schild { .. } => crate::schild::einstufen(u.as_str(), start.as_str(), AnfrageArt::Bild).is_none(),
    }
}

/// Holt eine Adresse und folgt Weiterleitungen selbst, jede geprüft.
/// Gibt die letzte Adresse und den Körper zurück.
async fn holen(client: &reqwest::Client, ziel: url::Url, start: &url::Url, seite: bool, max: usize) -> Option<(url::Url, Vec<u8>, String)> {
    let mut ziel = ziel;
    for _ in 0..=WEITERLEITUNGEN_MAX {
        if !erlaubt(&ziel, start, seite) {
            return None;
        }
        let mut antwort = client.get(ziel.clone()).send().await.ok()?;
        if antwort.status().is_redirection() {
            let ort = antwort.headers().get(reqwest::header::LOCATION)?.to_str().ok()?;
            ziel = ziel.join(ort).ok()?;
            continue;
        }
        if !antwort.status().is_success() || antwort.content_length().is_some_and(|l| l as usize > max) {
            return None;
        }
        let art = antwort
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|a| a.to_str().ok())
            .unwrap_or_default()
            .to_ascii_lowercase();
        let mut bytes = Vec::new();
        while let Some(teil) = antwort.chunk().await.ok()? {
            if bytes.len() + teil.len() > max {
                // Eine Seite darf länger sein; die Symbole stehen im Kopf.
                if seite {
                    bytes.extend_from_slice(&teil[..max - bytes.len()]);
                    break;
                }
                return None;
            }
            bytes.extend_from_slice(&teil);
        }
        return Some((ziel, bytes, art));
    }
    None
}

/// Ein `<link>` mit Symbol, wie die Seite es nennt.
#[derive(Debug, PartialEq)]
struct Kandidat {
    href: String,
    rang: u32,
}

/// Liest die Attribute eines Tags; Werte in `"`, `'` oder ohne Anführung.
fn attribute(tag: &str) -> Vec<(String, String)> {
    let mut aus = Vec::new();
    let b = tag.as_bytes();
    let mut i = 0;
    while i < b.len() {
        while i < b.len() && (b[i].is_ascii_whitespace() || b[i] == b'/') {
            i += 1;
        }
        let start = i;
        while i < b.len() && !b[i].is_ascii_whitespace() && b[i] != b'=' && b[i] != b'/' {
            i += 1;
        }
        let name = tag[start..i].to_ascii_lowercase();
        while i < b.len() && b[i].is_ascii_whitespace() {
            i += 1;
        }
        let mut wert = String::new();
        if i < b.len() && b[i] == b'=' {
            i += 1;
            while i < b.len() && b[i].is_ascii_whitespace() {
                i += 1;
            }
            if i < b.len() && (b[i] == b'"' || b[i] == b'\'') {
                let q = b[i];
                i += 1;
                let s = i;
                while i < b.len() && b[i] != q {
                    i += 1;
                }
                wert = tag[s..i].to_string();
                i += 1;
            } else {
                let s = i;
                while i < b.len() && !b[i].is_ascii_whitespace() {
                    i += 1;
                }
                wert = tag[s..i].to_string();
            }
        }
        if !name.is_empty() {
            aus.push((name, wert.replace("&amp;", "&")));
        }
    }
    aus
}

/// Die Symbole im HTML, das beste zuerst: große Touch-Symbole, dann `icon`
/// nach Größe. SVG und Maskensymbole fallen weg (kein SVG-Dekoder im Browser).
fn kandidaten(html: &str) -> Vec<Kandidat> {
    let klein = html.to_ascii_lowercase();
    let mut aus = Vec::new();
    let mut pos = 0;
    while let Some(i) = klein[pos..].find("<link") {
        let anfang = pos + i + 5;
        let Some(ende) = klein[anfang..].find('>').map(|e| anfang + e) else { break };
        pos = ende;
        let attr = attribute(&html[anfang..ende]);
        let wert = |n: &str| attr.iter().find(|(a, _)| a == n).map(|(_, w)| w.trim().to_string()).unwrap_or_default();
        let rel = wert("rel").to_ascii_lowercase();
        let href = wert("href");
        let svg = wert("type").to_ascii_lowercase().contains("svg") || href.to_ascii_lowercase().split(['?', '#']).next().unwrap_or("").ends_with(".svg");
        if href.is_empty() || svg {
            continue;
        }
        let groesse = wert("sizes")
            .split_whitespace()
            .filter_map(|s| s.to_ascii_lowercase().split_once('x').and_then(|(b, _)| b.parse::<u32>().ok()))
            .max()
            .unwrap_or(0)
            .min(PIXEL_MAX);
        let rel: Vec<&str> = rel.split_whitespace().collect();
        let rang = if rel.iter().any(|r| r.starts_with("apple-touch-icon")) {
            3000 + groesse.max(180)
        } else if rel.contains(&"fluid-icon") {
            2000 + groesse.max(512)
        } else if rel.contains(&"icon") {
            1000 + groesse
        } else {
            continue;
        };
        aus.push(Kandidat { href, rang });
    }
    aus.sort_by(|a, b| b.rang.cmp(&a.rang));
    aus
}

/// Dekodiert PNG oder ICO mit Grenzen und gibt ein PNG von höchstens 64 px
/// mit dem ursprünglichen Seitenverhältnis zurück.
fn umwandeln(bytes: &[u8]) -> Option<Vec<u8>> {
    let mut leser = image::ImageReader::new(Cursor::new(bytes)).with_guessed_format().ok()?;
    if !matches!(leser.format(), Some(image::ImageFormat::Png | image::ImageFormat::Ico)) {
        return None;
    }
    let mut grenzen = image::Limits::default();
    grenzen.max_image_width = Some(PIXEL_MAX);
    grenzen.max_image_height = Some(PIXEL_MAX);
    grenzen.max_alloc = Some(16 * 1024 * 1024);
    leser.limits(grenzen);
    let bild = leser.decode().ok()?;
    if bild.width().min(bild.height()) < KLEINSTE {
        return None;
    }
    let bild = if bild.width().max(bild.height()) > GROESSE {
        bild.resize(GROESSE, GROESSE, image::imageops::FilterType::Lanczos3)
    } else {
        bild
    };
    let mut png = Vec::new();
    bild.to_rgba8().write_to(&mut Cursor::new(&mut png), image::ImageFormat::Png).ok()?;
    Some(png)
}

/// Ein Symbol als `data:`-Adresse im HTML (`data:image/png;base64,…`).
fn aus_data(href: &str) -> Option<Vec<u8>> {
    let rest = href.strip_prefix("data:")?;
    let (kopf, daten) = rest.split_once(',')?;
    if !kopf.ends_with(";base64") || daten.len() > BILD_MAX * 4 / 3 + 4 {
        return None;
    }
    base64::engine::general_purpose::STANDARD.decode(daten.trim()).ok()
}

async fn suchen(start: url::Url) -> Option<Vec<u8>> {
    let client = crate::netz::client_ohne_weiterleitung(FRIST).ok()?;
    let mut versuche: Vec<String> = Vec::new();
    let mut basis = start.clone();
    if let Some((seite, html, art)) = holen(&client, start.clone(), &start, true, SEITE_MAX).await {
        if art.starts_with("text/html") || art.is_empty() {
            basis = seite;
            versuche.extend(kandidaten(&String::from_utf8_lossy(&html)).into_iter().map(|k| k.href));
        }
    }
    versuche.truncate(3);
    versuche.push("/favicon.ico".into());
    for href in versuche {
        let bytes = if href.starts_with("data:") {
            aus_data(&href)
        } else {
            let Ok(ziel) = basis.join(&href) else { continue };
            holen(&client, ziel, &start, false, BILD_MAX).await.map(|(_, b, _)| b)
        };
        if let Some(png) = bytes.as_deref().and_then(umwandeln) {
            return Some(png);
        }
    }
    None
}

fn ordner(app: &AppHandle) -> Option<PathBuf> {
    Some(app.path().app_local_data_dir().ok()?.join("kachelsymbole"))
}

fn alter(pfad: &Path) -> Option<Duration> {
    let geaendert = std::fs::metadata(pfad).ok()?.modified().ok()?;
    Some(SystemTime::now().duration_since(geaendert).unwrap_or_default())
}

fn als_data(png: &[u8]) -> String {
    format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(png))
}

/// Das Symbol einer Kachel als `data:`-Adresse, `None` ohne Symbol (die
/// Kachel zeigt dann den Anfangsbuchstaben).
#[tauri::command]
pub async fn kachel_symbol(app: AppHandle, url: String) -> Result<Option<String>, String> {
    let Some(start) = herkunft(&url) else { return Ok(None) };
    let ordner = ordner(&app).ok_or("pfad")?;
    let name = schluessel(&start);
    let (bild, leer) = (ordner.join(format!("{name}.png")), ordner.join(format!("{name}.leer")));
    let vorhanden = std::fs::read(&bild).ok();
    if vorhanden.is_some() && alter(&bild).is_some_and(|a| a < GILT) {
        return Ok(vorhanden.as_deref().map(als_data));
    }
    if vorhanden.is_none() && alter(&leer).is_some_and(|a| a < LEER_GILT) {
        return Ok(None);
    }
    // Wie ein Tab erst, wenn die Sperre des Jugendschutzes steht.
    tauri::async_runtime::spawn_blocking(crate::schild::schutz_dienst::bereit).await.map_err(|e| e.to_string())??;
    let neu = suchen(start).await;
    let _ = std::fs::create_dir_all(&ordner);
    match neu {
        Some(png) => {
            let _ = crate::datei::ersetzen(&bild, &png);
            let _ = std::fs::remove_file(&leer);
            Ok(Some(als_data(&png)))
        }
        // Ein altes Symbol bleibt, wenn die Seite gerade nicht antwortet.
        None if vorhanden.is_some() => Ok(vorhanden.as_deref().map(als_data)),
        None => {
            let _ = crate::datei::ersetzen(&leer, b"");
            Ok(None)
        }
    }
}

/// Behält nur die Symbole dieser Kacheln; die übrigen gehen mit ihrer Kachel.
#[tauri::command(async)]
pub fn kachel_symbole_behalten(app: AppHandle, adressen: Vec<String>) -> Result<(), String> {
    let behalten: HashSet<String> = adressen.iter().take(256).filter_map(|a| herkunft(a)).map(|h| schluessel(&h)).collect();
    let Some(ordner) = ordner(&app) else { return Ok(()) };
    let Ok(eintraege) = std::fs::read_dir(&ordner) else { return Ok(()) };
    for eintrag in eintraege.flatten() {
        let pfad = eintrag.path();
        let stamm = pfad.file_stem().and_then(|s| s.to_str()).unwrap_or_default().to_string();
        if !behalten.contains(&stamm) {
            let _ = std::fs::remove_file(&pfad);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn die_herkunft_ist_verschluesselt_und_ohne_pfad() {
        let h = |u: &str| herkunft(u).map(|h| h.to_string());
        assert_eq!(h("https://de.wikipedia.org/wiki/Hauptseite?x=1").as_deref(), Some("https://de.wikipedia.org/"));
        assert_eq!(h("http://github.com/").as_deref(), Some("https://github.com/"));
        assert_eq!(h("http://192.168.1.10:8080/a").as_deref(), Some("http://192.168.1.10:8080/"));
        for schlecht in ["javascript:alert(1)", "file:///C:/a.png", "data:text/html,x", "msb://einstellungen", ""] {
            assert_eq!(h(schlecht), None, "{schlecht}");
        }
        assert_eq!(schluessel(&herkunft("https://Example.org:8443/x").unwrap()), "https___example.org_8443");
    }

    #[test]
    fn findet_das_groesste_symbol_und_laesst_svg_weg() {
        let html = r#"<html><head>
          <link rel="icon" href="/favicon-16.png" sizes="16x16">
          <LINK REL='icon' TYPE='image/svg+xml' HREF='/logo.svg'>
          <link rel="mask-icon" href="/mask.png">
          <link rel=icon href=/favicon-32.png sizes=32x32>
          <link href="/touch.png?v=2&amp;x=1" rel="apple-touch-icon">
          <link rel="stylesheet" href="/a.css">
          <link rel="shortcut icon" href="/favicon.ico">
        </head>"#;
        let k: Vec<String> = kandidaten(html).into_iter().map(|k| k.href).collect();
        assert_eq!(k, ["/touch.png?v=2&x=1", "/favicon-32.png", "/favicon-16.png", "/favicon.ico"]);
        assert!(kandidaten("<link rel=icon href='/kaputt").is_empty());
    }

    fn png(breite: u32, hoehe: u32) -> Vec<u8> {
        let bild = image::RgbaImage::from_pixel(breite, hoehe, image::Rgba([10, 20, 30, 255]));
        let mut aus = Vec::new();
        bild.write_to(&mut Cursor::new(&mut aus), image::ImageFormat::Png).unwrap();
        aus
    }

    #[test]
    fn wandelt_nur_png_und_ico_mit_grenzen() {
        let aus = umwandeln(&png(180, 90)).unwrap();
        let bild = image::load_from_memory(&aus).unwrap();
        assert_eq!((bild.width(), bild.height()), (64, 32));
        let klein = image::load_from_memory(&umwandeln(&png(32, 32)).unwrap()).unwrap();
        assert_eq!((klein.width(), klein.height()), (32, 32));
        let mut ico = Vec::new();
        image::RgbaImage::from_pixel(48, 48, image::Rgba([1, 2, 3, 255])).write_to(&mut Cursor::new(&mut ico), image::ImageFormat::Ico).unwrap();
        let aus_ico = image::load_from_memory(&umwandeln(&ico).unwrap()).unwrap();
        assert_eq!((aus_ico.width(), aus_ico.height()), (48, 48));
        assert_eq!(umwandeln(&png(8, 8)), None, "zu klein");
        assert_eq!(umwandeln(&png(1100, 20)), None, "zu groß zum Dekodieren");
        assert_eq!(umwandeln(b"<svg xmlns='http://www.w3.org/2000/svg'/>"), None);
        assert_eq!(umwandeln(b"GIF89a\x01\x00\x01\x00"), None);
        // Ein PNG, das mehr Pixel behauptet, als es Daten hat, wird nicht dekodiert.
        let mut luege = png(32, 32);
        luege[16..24].copy_from_slice(&[0, 0, 0x40, 0, 0, 0, 0x40, 0]);
        assert_eq!(umwandeln(&luege), None);
    }

    #[test]
    fn nimmt_data_adressen_nur_als_base64() {
        let b64 = base64::engine::general_purpose::STANDARD.encode(png(32, 32));
        assert!(aus_data(&format!("data:image/png;base64,{b64}")).and_then(|b| umwandeln(&b)).is_some());
        assert_eq!(aus_data("data:image/png,rohdaten"), None);
        assert_eq!(aus_data("https://x.example/a.png"), None);
    }
}
