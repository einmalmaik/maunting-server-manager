//! Symbole aus den Browser-Datenbanken: als 32-px-Datenadresse für
//! Lesezeichen und als 64-px-Datei im Kachelcache für Top-Sites.
//!
//! Dekodiert werden nur PNG und ICO (`kachelsymbol::umwandeln_auf`), SVG und
//! kaputte Bilder fallen weg. Es wird nichts nachgeladen; ein fehlendes Symbol
//! bleibt einfach ohne Bild.

use std::collections::HashSet;
use std::fs;

use tauri::AppHandle;

use super::{herkunft, warnung, ImportBild, ImportWarnung, RohBild, BILD_LAENGE_MAX, BILDER_MAX};
use crate::datei;
use crate::kachelsymbol;

/// Mehr Symbole passen nicht sinnvoll in den localStorage neben dem Verlauf.
const BILD_GESAMT_MAX: usize = 1024 * 1024;

pub(crate) fn aufbereiten(
    app: &AppHandle,
    kacheln: &HashSet<String>,
    roh: &[RohBild],
) -> (Vec<ImportBild>, Vec<ImportWarnung>) {
    let mut aus = Vec::new();
    let mut warnungen = Vec::new();
    let mut gesehen = HashSet::new();
    let mut gesamt = 0usize;
    let mut gekuerzt = false;
    let ordner = kachelsymbol::ordner(app);

    for bild in roh {
        if aus.len() >= BILDER_MAX {
            gekuerzt = true;
            break;
        }
        let Some(herkunft) = herkunft(&bild.herkunft) else { continue };
        if !gesehen.insert(herkunft.clone()) {
            continue;
        }
        let Some(png32) = bild.kandidaten.iter().find_map(|b| kachelsymbol::umwandeln_auf(b, 32)) else {
            continue;
        };
        let daten = kachelsymbol::als_data(&png32);
        if daten.len() > BILD_LAENGE_MAX {
            continue;
        }
        if gesamt + daten.len() > BILD_GESAMT_MAX {
            gekuerzt = true;
            break;
        }
        gesamt += daten.len();

        if kacheln.contains(&herkunft) {
            if let (Some(ordner), Some(png64)) = (
                ordner.as_ref(),
                bild.kandidaten.iter().find_map(|b| kachelsymbol::umwandeln_auf(b, 64)),
            ) {
                let _ = fs::create_dir_all(ordner);
                for name in kachel_schluessel(&herkunft) {
                    let _ = datei::ersetzen(&ordner.join(format!("{name}.png")), &png64);
                    let _ = fs::remove_file(ordner.join(format!("{name}.leer")));
                }
            }
        }
        aus.push(ImportBild { herkunft, daten });
    }
    if gekuerzt {
        warnung(&mut warnungen, "bilder_gekuerzt");
    }
    (aus, warnungen)
}

/// Der Kachelcache sucht unter der verschlüsselten Herkunft (`http` wird für
/// fremde Netze zu `https`), der Bildspeicher der Oberfläche unter der
/// tatsächlichen. Beide Namen bekommen dasselbe Bild.
fn kachel_schluessel(herkunft: &str) -> Vec<String> {
    let mut aus = Vec::new();
    let mut gesehen = HashSet::new();
    if let Ok(url) = url::Url::parse(herkunft) {
        if matches!(url.scheme(), "http" | "https") {
            let name = kachelsymbol::schluessel(&url);
            gesehen.insert(name.clone());
            aus.push(name);
        }
    }
    if let Some(hoch) = kachelsymbol::herkunft(herkunft) {
        let name = kachelsymbol::schluessel(&hoch);
        if gesehen.insert(name.clone()) {
            aus.push(name);
        }
    }
    aus
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legt_fuer_http_auch_den_verschluesselten_kachelnamen_an() {
        let namen = kachel_schluessel("http://example.org");
        assert_eq!(namen, ["http___example.org", "https___example.org"]);
        assert_eq!(kachel_schluessel("https://example.org"), ["https___example.org"]);
        assert!(kachel_schluessel("file:///C:/a").is_empty());
    }
}
