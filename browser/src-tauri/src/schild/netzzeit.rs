//! Die Uhrzeit aus dem Netz, für alles, was der Jugend- und Suchtschutz an
//! einer Frist misst. Die Uhr des Rechners lässt sich in Sekunden verstellen;
//! die Kopfzeile `Date` einer HTTPS-Antwort von GitHub nicht, ohne der App
//! ein eigenes Stammzertifikat unterzuschieben.
//!
//! Gefragt wird derselbe Host, von dem die Sperrlisten kommen
//! (raw.githubusercontent.com), mit HEAD: kein Inhalt, kein Cookie.

use std::sync::atomic::{AtomicI64, Ordering};
use std::time::Duration;

const QUELLE: &str = "https://raw.githubusercontent.com/StevenBlack/hosts/master/readme.md";

/// Netzzeit minus Rechnerzeit beim letzten Abruf; nur für die Anzeige.
static VERSATZ: AtomicI64 = AtomicI64::new(0);

/// Unix-Sekunden laut Netz, `None` ohne Netz.
pub async fn holen(lokal: u64) -> Option<u64> {
    let antwort = crate::netz::client(Duration::from_secs(10))
        .ok()?
        .head(QUELLE)
        .send()
        .await
        .ok()?;
    let zeit = datum_lesen(antwort.headers().get(reqwest::header::DATE)?.to_str().ok()?)?;
    VERSATZ.store(zeit as i64 - lokal as i64, Ordering::Relaxed);
    Some(zeit)
}

/// Die Rechnerzeit, so gut es geht an die Netzzeit angeglichen. Für Countdowns
/// in der Oberfläche; entschieden wird nur mit [`holen`].
pub fn angeglichen(lokal: u64) -> u64 {
    lokal.saturating_add_signed(VERSATZ.load(Ordering::Relaxed))
}

/// `Wed, 08 Oct 2026 21:31:07 GMT` (IMF-fixdate, RFC 9110) in Unix-Sekunden.
pub fn datum_lesen(text: &str) -> Option<u64> {
    let teile: Vec<&str> = text.split_whitespace().collect();
    let [_, tag, monat, jahr, uhr, "GMT"] = teile[..] else { return None };
    const MONATE: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let monat = MONATE.iter().position(|m| *m == monat)? as i64 + 1;
    let (tag, jahr): (i64, i64) = (tag.parse().ok()?, jahr.parse().ok()?);
    let mut hms = uhr.split(':').map(|z| z.parse::<i64>().ok());
    let (h, m, s) = (hms.next()??, hms.next()??, hms.next()??);
    if hms.next().is_some() || !(1..=31).contains(&tag) || h > 23 || m > 59 || s > 60 {
        return None;
    }
    // Tage seit 1970 nach Howard Hinnant, „days_from_civil“.
    let y = if monat <= 2 { jahr - 1 } else { jahr };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * ((monat + 9) % 12) + 2) / 5 + tag - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let tage = era * 146_097 + doe - 719_468;
    let zeit = tage * 86_400 + h * 3600 + m * 60 + s;
    // Vor 2024 stimmt keine Antwort, die wir bekommen können.
    (zeit >= 1_704_067_200).then_some(zeit as u64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn liest_das_datum_einer_antwort() {
        assert_eq!(datum_lesen("Thu, 08 Oct 2026 21:31:07 GMT"), Some(1_791_495_067));
        assert_eq!(datum_lesen("Thu, 29 Feb 2024 00:00:00 GMT"), Some(1_709_164_800));
        assert_eq!(datum_lesen("Fri, 31 Dec 2027 23:59:59 GMT"), Some(1_830_297_599));
    }

    /// Fragt GitHub wirklich: `cargo test netzzeit -- --ignored`.
    #[test]
    #[ignore]
    fn holt_die_zeit_aus_dem_netz() {
        let lokal = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
        let netz = tauri::async_runtime::block_on(holen(lokal)).expect("keine Antwort");
        assert!(netz.abs_diff(lokal) < 300, "Netz {netz}, Rechner {lokal}");
        assert!(angeglichen(lokal).abs_diff(netz) < 5);
    }

    #[test]
    fn nimmt_nichts_anderes_an() {
        for text in [
            "",
            "Thu, 08 Oct 2026 21:31:07 CET",
            "Thu, 08 Okt 2026 21:31:07 GMT",
            "Thu, 08 Oct 2026 24:00:00 GMT",
            "Thu, 08 Oct 2026 21:31 GMT",
            "Thu, 08 Oct 2026 21:31:07:01 GMT",
            "Thu, 32 Oct 2026 21:31:07 GMT",
            "Thu, 08 Oct 1999 21:31:07 GMT",
            "Thursday, 08-Oct-26 21:31:07 GMT",
        ] {
            assert_eq!(datum_lesen(text), None, "{text}");
        }
    }
}
