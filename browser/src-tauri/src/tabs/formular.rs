//! Anmeldefelder einer Seite: was `seite.js` meldet, und das Füllen.
//!
//! Die Meldungen kommen aus der Seite und sind deshalb nur Angaben: die
//! Adresse, zu der sie gehören, nimmt Rust aus der Webview selbst, nie aus der
//! Meldung. Gefüllt wird nur, wenn der Tab noch auf derselben Herkunft steht,
//! für die die Oberfläche den Eintrag ausgesucht hat.

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

use super::{id_pruefen, plattform};

/// Länger wird nichts aus einer Seite angenommen.
pub const ROH_MAX: usize = 4096;
pub const BENUTZER_MAX: usize = 512;
pub const PASSWORT_MAX: usize = 1024;

#[derive(Clone, Serialize, Deserialize)]
#[serde(tag = "t", rename_all = "snake_case", deny_unknown_fields)]
pub enum Meldung {
    /// Ein Benutzer- oder Passwortfeld hat den Fokus. `neu`: ein neues
    /// Passwort (Registrierung, Wechsel). `sicher`: eindeutig ein neues
    /// Passwort (`new-password` oder Passwort samt Wiederholung); nur dann
    /// erzeugt der Browser eines von selbst.
    Feld {
        passwort: bool,
        neu: bool,
        #[serde(default)]
        sicher: bool,
    },
    /// Abgeschickt mit Passwort.
    Absenden { benutzer: String, passwort: String, neu: bool },
    /// Abgeschickt ohne Passwort: erster Schritt einer mehrstufigen Anmeldung.
    Benutzer { wert: String },
    /// Ein Feld für eine Zahlungskarte oder ein Bankkonto hat den Fokus.
    Zahlung { art: ZahlArt },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ZahlArt {
    Karte,
    Konto,
}

// Kein abgeleitetes Debug: ein Passwort landet in keinem Log.
impl std::fmt::Debug for Meldung {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Meldung::Feld { passwort, neu, sicher } => write!(f, "Feld {{ passwort: {passwort}, neu: {neu}, sicher: {sicher} }}"),
            Meldung::Absenden { neu, .. } => write!(f, "Absenden {{ neu: {neu}, .. }}"),
            Meldung::Benutzer { .. } => write!(f, "Benutzer {{ .. }}"),
            Meldung::Zahlung { art } => write!(f, "Zahlung {{ art: {art:?} }}"),
        }
    }
}

/// Liest eine Meldung der Seite; alles Unbekannte oder zu Lange fällt weg.
pub fn lesen(roh: &str) -> Option<Meldung> {
    if roh.len() > ROH_MAX {
        return None;
    }
    let meldung: Meldung = serde_json::from_str(roh).ok()?;
    let passt = match &meldung {
        Meldung::Feld { .. } | Meldung::Zahlung { .. } => true,
        Meldung::Absenden { benutzer, passwort, .. } => {
            !passwort.is_empty() && passwort.chars().count() <= PASSWORT_MAX && benutzer.chars().count() <= BENUTZER_MAX
        }
        Meldung::Benutzer { wert } => !wert.is_empty() && wert.chars().count() <= BENUTZER_MAX,
    };
    passt.then_some(meldung)
}

/// Was die Oberfläche einfüllen lässt. `neu` füllt alle Felder für ein neues
/// Passwort (Passwort und Wiederholung). `karte` und `konto` kommen erst nach
/// einer Bestätigung in der Oberfläche (Windows Hello oder Master-Passwort).
#[derive(Deserialize, Serialize, Default)]
#[serde(deny_unknown_fields)]
pub struct Fuellen {
    pub benutzer: Option<String>,
    pub passwort: Option<String>,
    pub neu: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub karte: Option<Karte>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub konto: Option<Konto>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Karte {
    pub nummer: String,
    pub inhaber: Option<String>,
    pub monat: Option<u8>,
    pub jahr: Option<u16>,
    pub pruefnummer: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Konto {
    pub iban: String,
    pub inhaber: Option<String>,
    pub bic: Option<String>,
}

const NAME_MAX: usize = 100;

fn ziffern(text: &str, von: usize, bis: usize) -> bool {
    (von..=bis).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_digit())
}

fn gross_oder_ziffer(text: &str, von: usize, bis: usize) -> bool {
    (von..=bis).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
}

/// Ein Name ohne Steuer- und Richtungszeichen.
fn name_passt(name: &Option<String>) -> bool {
    let unsichtbar = |z: char| z.is_control() || matches!(z, '\u{200B}'..='\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}' | '\u{FEFF}');
    name.as_ref().is_none_or(|n| n.chars().count() <= NAME_MAX && !n.chars().any(unsichtbar))
}

impl Fuellen {
    /// Nur eine Art zugleich, und jedes Feld in seiner Form.
    fn pruefen(&self) -> Result<(), String> {
        let lang = |w: &Option<String>, max: usize| w.as_ref().is_some_and(|w| w.chars().count() > max);
        if lang(&self.benutzer, BENUTZER_MAX) || lang(&self.passwort, PASSWORT_MAX) || lang(&self.neu, PASSWORT_MAX) {
            return Err("Zu lang".into());
        }
        let anmeldung = self.benutzer.is_some() || self.passwort.is_some() || self.neu.is_some();
        if [anmeldung, self.karte.is_some(), self.konto.is_some()].iter().filter(|&&x| x).count() > 1 {
            return Err("Nur eine Art zugleich".into());
        }
        if let Some(k) = &self.karte {
            let gueltig = ziffern(&k.nummer, 12, 19)
                && name_passt(&k.inhaber)
                && k.monat.is_none_or(|m| (1..=12).contains(&m))
                && k.jahr.is_none_or(|j| (2000..=2199).contains(&j))
                && k.pruefnummer.as_ref().is_none_or(|p| ziffern(p, 3, 4));
            if !gueltig {
                return Err("Ungültige Karte".into());
            }
        }
        if let Some(k) = &self.konto {
            let gueltig = gross_oder_ziffer(&k.iban, 15, 34) && name_passt(&k.inhaber) && k.bic.as_ref().is_none_or(|b| gross_oder_ziffer(b, 8, 11));
            if !gueltig {
                return Err("Ungültiges Konto".into());
            }
        }
        Ok(())
    }

    fn zahlung(&self) -> bool {
        self.karte.is_some() || self.konto.is_some()
    }
}

/// Gleiche Herkunft: Schema, Host und Port.
pub fn gleiche_herkunft(a: &str, b: &str) -> bool {
    match (url::Url::parse(a), url::Url::parse(b)) {
        (Ok(a), Ok(b)) => {
            matches!(a.scheme(), "http" | "https") && a.origin() == b.origin()
        }
        _ => false,
    }
}

/// Füllt Anmeldedaten in den Tab, wenn er noch auf der Herkunft von `fuer` steht.
#[tauri::command(async)]
pub fn tab_fuellen(app: AppHandle, id: String, fuer: String, werte: Fuellen) -> Result<(), String> {
    id_pruefen(&id)?;
    werte.pruefen()?;
    // Zahlungsdaten gehen nie über eine unverschlüsselte Verbindung.
    if werte.zahlung() && !fuer.starts_with("https://") {
        return Err("Zahlungsdaten nur über HTTPS".into());
    }
    let mut nachricht = serde_json::to_value(&werte).map_err(|e| e.to_string())?;
    nachricht["t"] = "fuellen".into();
    plattform::fuellen(&app, &id, &fuer, nachricht.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nimmt_nur_bekannte_und_knappe_meldungen() {
        assert!(matches!(lesen(r#"{"t":"feld","passwort":true,"neu":false}"#), Some(Meldung::Feld { passwort: true, neu: false, sicher: false })));
        assert!(matches!(lesen(r#"{"t":"feld","passwort":true,"neu":true,"sicher":true}"#), Some(Meldung::Feld { sicher: true, .. })));
        assert!(lesen(r#"{"t":"absenden","benutzer":"a","passwort":"","neu":false}"#).is_none());
        assert!(lesen(r#"{"t":"feld","passwort":true,"neu":false,"url":"https://bank.example"}"#).is_none());
        assert!(lesen(r#"{"t":"fuellen","passwort":"x"}"#).is_none());
        let lang = format!(r#"{{"t":"benutzer","wert":"{}"}}"#, "x".repeat(BENUTZER_MAX + 1));
        assert!(lesen(&lang).is_none());
    }

    #[test]
    fn debug_zeigt_kein_passwort() {
        let m = lesen(r#"{"t":"absenden","benutzer":"ada","passwort":"geheim-123","neu":true}"#).unwrap();
        let text = format!("{m:?}");
        assert!(!text.contains("geheim") && !text.contains("ada"));
    }

    #[test]
    fn zahlungsfelder_melden_nur_ihre_art() {
        assert!(matches!(lesen(r#"{"t":"zahlung","art":"karte"}"#), Some(Meldung::Zahlung { art: ZahlArt::Karte })));
        assert!(lesen(r#"{"t":"zahlung","art":"bitcoin"}"#).is_none());
        assert!(lesen(r#"{"t":"zahlung","art":"konto","nummer":"4111"}"#).is_none());
    }

    #[test]
    fn karte_und_konto_nur_wohlgeformt_und_einzeln() {
        let karte = || Karte { nummer: "4111111111111111".into(), inhaber: Some("Ada".into()), monat: Some(12), jahr: Some(2030), pruefnummer: Some("123".into()) };
        let mit = |aendern: fn(&mut Karte)| {
            let mut k = karte();
            aendern(&mut k);
            Fuellen { karte: Some(k), ..Default::default() }.pruefen()
        };
        assert!(mit(|_| {}).is_ok());
        assert!(mit(|k| k.nummer = "4111 1111 1111".into()).is_err());
        assert!(mit(|k| k.monat = Some(13)).is_err());
        assert!(mit(|k| k.pruefnummer = Some("12a".into())).is_err());
        assert!(mit(|k| k.inhaber = Some("a\u{202E}b".into())).is_err());
        let beides = Fuellen { karte: Some(karte()), passwort: Some("x".into()), ..Default::default() };
        assert!(beides.pruefen().is_err());
        let konto = |iban: &str| Fuellen { konto: Some(Konto { iban: iban.into(), inhaber: None, bic: Some("COBADEFFXXX".into()) }), ..Default::default() }.pruefen();
        assert!(konto("DE89370400440532013000").is_ok());
        assert!(konto("de89370400440532013000").is_err());
    }

    #[test]
    fn herkunft_zaehlt_mit_schema_und_port() {
        assert!(gleiche_herkunft("https://example.com/login", "https://example.com/"));
        assert!(!gleiche_herkunft("https://example.com/", "https://login.example.com/"));
        assert!(!gleiche_herkunft("https://example.com/", "http://example.com/"));
        assert!(!gleiche_herkunft("https://example.com/", "https://example.com:8443/"));
        assert!(!gleiche_herkunft("about:blank", "about:blank"));
    }
}
