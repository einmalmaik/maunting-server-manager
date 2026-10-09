//! Tracking-Parameter in Links: Kampagnen- und Klickkennungen, an denen eine
//! Seite sieht, woher jemand kommt (`utm_source`, `fbclid`, `gclid` …). Vor
//! dem Laden fallen sie weg; die Seite sieht gleich aus.
//!
//! Die Liste ist bewusst eng: nur Namen, die nichts anderes bedeuten.
//! Partnerkennungen wie `tag` bei Amazon bleiben, sie bezahlen jemanden.
//! Gilt nur, wo das Schild an ist; eine Seite, die ohne ihren Parameter
//! nicht geht, nimmt man in die Ausnahmen.

/// Kleingeschrieben verglichen. `utm_` gilt als Vorsilbe.
const NAMEN: [&str; 32] = [
    "fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "ysclid", "twclid", "ttclid",
    "igshid", "igsh", "li_fat_id", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "__hssc", "__hstc", "__hsfp",
    "hsctatracking", "mkt_tok", "oly_anon_id", "oly_enc_id", "vero_id", "vero_conv", "wickedid", "rb_clickid",
    "_openstat", "__s", "srsltid",
];

fn verfolgt(name: &str) -> bool {
    let name = name.to_ascii_lowercase();
    name.starts_with("utm_") || NAMEN.contains(&name.as_str())
}

/// Die Adresse ohne Tracking-Parameter, `None`, wenn keiner darin steht.
///
/// Gearbeitet wird am rohen Text der Abfrage: was bleibt, behält seine
/// Schreibweise (`+`, `%2F`). Neu kodiert wären signierte Adressen kaputt.
pub fn ohne_verfolgung(url: &url::Url) -> Option<url::Url> {
    let abfrage = url.query()?;
    let teile: Vec<&str> = abfrage.split('&').collect();
    let bleibt: Vec<&str> = teile
        .iter()
        .copied()
        .filter(|teil| {
            let name = teil.split('=').next().unwrap_or_default();
            let name = url::form_urlencoded::parse(name.as_bytes()).next().map(|(n, _)| n.into_owned());
            !name.is_some_and(|n| verfolgt(&n))
        })
        .collect();
    if bleibt.len() == teile.len() {
        return None;
    }
    let mut neu = url.clone();
    let rest = bleibt.join("&");
    neu.set_query((!rest.is_empty()).then_some(rest.as_str()));
    Some(neu)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sauber(url: &str) -> Option<String> {
        ohne_verfolgung(&url::Url::parse(url).unwrap()).map(String::from)
    }

    #[test]
    fn nimmt_kampagnen_und_klickkennungen_heraus() {
        assert_eq!(
            sauber("https://example.com/a?utm_source=x&id=7&fbclid=IwAR0&UTM_Medium=mail#teil").as_deref(),
            Some("https://example.com/a?id=7#teil")
        );
        assert_eq!(sauber("https://example.com/?gclid=1").as_deref(), Some("https://example.com/"));
        assert_eq!(sauber("https://example.com/?%75tm_source=x&q=1").as_deref(), Some("https://example.com/?q=1"));
    }

    #[test]
    fn laesst_alles_andere_wie_es_ist() {
        assert_eq!(sauber("https://example.com/?q=a+b&sig=x%2Fy%3D&tag=partner-21"), None);
        assert_eq!(sauber("https://example.com/"), None);
        assert_eq!(sauber("https://example.com/?utm=1&source=2"), None);
        // Was bleibt, behält seine Schreibweise: eine Signatur ginge sonst kaputt.
        assert_eq!(
            sauber("https://example.com/?sig=x%2Fy%3D&utm_id=1&q=a+b").as_deref(),
            Some("https://example.com/?sig=x%2Fy%3D&q=a+b")
        );
    }
}
