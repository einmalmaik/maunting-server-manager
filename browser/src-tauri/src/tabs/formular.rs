//! Anmeldefelder einer Seite: was `seite.js` meldet, und das Füllen.
//!
//! Die Meldungen kommen aus der Seite und sind deshalb nur Angaben: die
//! Adresse, zu der sie gehören, nimmt Rust aus der Webview selbst, nie aus der
//! Meldung. Gefüllt wird nur, wenn der Tab noch auf derselben Herkunft steht,
//! für die die Oberfläche den Eintrag ausgesucht hat.
//!
//! Zahlungsfelder dürfen auch in einem Rahmen liegen ([`Zahlrahmen`]).

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

use super::{id_pruefen, melden, plattform, TabEreignis};

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
    /// erzeugt der Browser eines von selbst. `aktiv`: der Fokus kam von einem
    /// Klick oder einer Taste des Nutzers (`navigator.userActivation`), nicht
    /// von einem Skript der Seite.
    Feld {
        passwort: bool,
        neu: bool,
        #[serde(default)]
        sicher: bool,
        #[serde(default)]
        aktiv: bool,
    },
    /// Abgeschickt mit Passwort; `aktiv` wie bei [`Meldung::Feld`].
    Absenden {
        benutzer: String,
        passwort: String,
        neu: bool,
        #[serde(default)]
        aktiv: bool,
    },
    /// Abgeschickt ohne Passwort: erster Schritt einer mehrstufigen Anmeldung.
    Benutzer { wert: String },
    /// Ein Feld für eine Zahlungskarte oder ein Bankkonto hat den Fokus.
    Zahlung { art: ZahlArt },
    /// Ein Rahmen der Seite hat den Fokus, sichtbar und über HTTPS; `None`:
    /// keiner (mehr). Geht nicht an die Oberfläche, nur an [`Zahlrahmen`].
    Rahmen { herkunft: Option<String> },
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
            Meldung::Feld { passwort, neu, sicher, aktiv } => {
                write!(f, "Feld {{ passwort: {passwort}, neu: {neu}, sicher: {sicher}, aktiv: {aktiv} }}")
            }
            Meldung::Absenden { neu, aktiv, .. } => write!(f, "Absenden {{ neu: {neu}, aktiv: {aktiv}, .. }}"),
            Meldung::Benutzer { .. } => write!(f, "Benutzer {{ .. }}"),
            Meldung::Zahlung { art } => write!(f, "Zahlung {{ art: {art:?} }}"),
            Meldung::Rahmen { herkunft } => write!(f, "Rahmen {{ herkunft: {herkunft:?} }}"),
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
        Meldung::Rahmen { herkunft } => herkunft.as_deref().is_none_or(|h| https_herkunft(h).as_deref() == Some(h)),
    };
    passt.then_some(meldung)
}

/// Was ein Unterrahmen sagen darf: dass er da ist, und dass ein Zahlungsfeld
/// den Fokus hat. Anmeldungen gibt es nur im obersten Rahmen.
#[derive(Deserialize)]
#[serde(tag = "t", rename_all = "snake_case", deny_unknown_fields)]
enum RahmenMeldung {
    Da,
    Zahlung { art: ZahlArt },
}

/// Die Herkunft einer HTTPS-Adresse (`https://host[:port]`), sonst `None`.
fn https_herkunft(url: &str) -> Option<String> {
    let url = url::Url::parse(url).ok()?;
    (url.scheme() == "https").then(|| url.origin().ascii_serialization())
}

fn herkunft(url: &str) -> String {
    url::Url::parse(url).map(|u| u.origin().ascii_serialization()).unwrap_or_default()
}

/// Kassen wie Stripe oder Adyen legen ihre Felder in Rahmen einer fremden
/// Herkunft, Adyen jedes Feld in einen eigenen. Angeboten wird eine Karte
/// dort nur, wenn die oberste Seite einen sichtbaren Rahmen mit Fokus meldet
/// (`oben`) und ein Rahmen derselben Herkunft ein Zahlungsfeld mit Fokus
/// (`unten`). Beides kommt über verschiedene Wege, in beliebiger Reihenfolge.
/// Ein Werberahmen kann sich so nicht als Kasse ausgeben: die oberste Seite
/// nennt ihn nur, wenn er den Fokus hat, und füllen lässt die Oberfläche nur
/// die Herkunft, die sie dem Nutzer gezeigt hat.
#[derive(Default, Debug, PartialEq)]
struct Zahlrahmen {
    /// Herkunft der obersten Seite; wechselt sie, fängt alles von vorn an.
    seite: String,
    oben: Option<String>,
    unten: Option<(String, ZahlArt)>,
    gemeldet: bool,
}

enum Aenderung {
    Oben(Option<String>),
    Unten(String, ZahlArt),
}

impl Zahlrahmen {
    /// Nimmt eine Meldung auf; `Some`, wenn die Oberfläche jetzt das
    /// Einfügen in diesen Rahmen anbieten soll.
    fn aendern(&mut self, seite: &str, aenderung: Aenderung) -> Option<(String, ZahlArt)> {
        if self.seite != seite {
            *self = Zahlrahmen { seite: seite.to_string(), ..Default::default() };
        }
        match aenderung {
            Aenderung::Oben(h) => {
                if h != self.oben {
                    self.gemeldet = false;
                }
                self.oben = h;
            }
            Aenderung::Unten(h, art) => {
                if self.unten.as_ref() != Some(&(h.clone(), art)) {
                    self.gemeldet = false;
                }
                self.unten = Some((h, art));
            }
        }
        match (&self.oben, &self.unten) {
            (Some(o), Some((u, art))) if o == u && !self.gemeldet => {
                self.gemeldet = true;
                Some((o.clone(), *art))
            }
            _ => None,
        }
    }

    fn gilt(&self, seite: &str, rahmen: &str) -> bool {
        !seite.is_empty()
            && self.seite == seite
            && self.oben.as_deref() == Some(rahmen)
            && self.unten.as_ref().is_some_and(|(u, _)| u == rahmen)
    }
}

static ZAHLRAHMEN: LazyLock<Mutex<HashMap<String, Zahlrahmen>>> = LazyLock::new(Default::default);

fn zahlrahmen(app: &AppHandle, id: &str, url: &str, aenderung: Aenderung) {
    let anbieten = ZAHLRAHMEN.lock().unwrap().entry(id.to_string()).or_default().aendern(&herkunft(url), aenderung);
    if let Some((rahmen, art)) = anbieten {
        let (id, url) = (id.to_string(), url.to_string());
        melden(app, TabEreignis::Formular { id, url, meldung: Meldung::Zahlung { art }, rahmen: Some(rahmen) });
    }
}

/// Eine Meldung des obersten Rahmens; `url` ist die Adresse aus der Webview.
pub fn oben(app: &AppHandle, id: &str, url: &str, meldung: Meldung) {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return;
    }
    match meldung {
        Meldung::Rahmen { herkunft } => zahlrahmen(app, id, url, Aenderung::Oben(herkunft)),
        meldung => melden(app, TabEreignis::Formular { id: id.to_string(), url: url.to_string(), meldung, rahmen: None }),
    }
}

/// Eine Nachricht aus einem Unterrahmen. `url` ist die Adresse der obersten
/// Seite, `absender` die des Rahmens, beide aus der Webview. Gibt die
/// Herkunft des Rahmens zurück, wenn er als Ziel fürs Füllen in Frage kommt.
pub fn unten(app: &AppHandle, id: &str, url: &str, absender: &str, roh: &str) -> Option<String> {
    if roh.len() > ROH_MAX || !url.starts_with("https://") {
        return None;
    }
    let rahmen = https_herkunft(absender)?;
    match serde_json::from_str::<RahmenMeldung>(roh).ok()? {
        RahmenMeldung::Da => {}
        RahmenMeldung::Zahlung { art } => zahlrahmen(app, id, url, Aenderung::Unten(rahmen.clone(), art)),
    }
    Some(rahmen)
}

/// Der Tab ist zu.
pub fn vergessen(id: &str) {
    ZAHLRAHMEN.lock().unwrap().remove(id);
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
    let unsichtbar = |z: char| z.is_control() || crate::downloads::ist_unsichtbar(z);
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

/// Füllt Anmeldedaten in den Tab, wenn er noch auf der Herkunft von `fuer`
/// steht. Mit `rahmen` gehen Zahlungsdaten an die Rahmen dieser Herkunft,
/// wenn [`Zahlrahmen`] sie noch als Kasse kennt.
#[tauri::command(async)]
pub fn tab_fuellen(app: AppHandle, id: String, fuer: String, rahmen: Option<String>, werte: Fuellen) -> Result<(), String> {
    id_pruefen(&id)?;
    werte.pruefen()?;
    // Zahlungsdaten gehen nie über eine unverschlüsselte Verbindung.
    if werte.zahlung() && !fuer.starts_with("https://") {
        return Err("Zahlungsdaten nur über HTTPS".into());
    }
    let mut nachricht = serde_json::to_value(&werte).map_err(|e| e.to_string())?;
    nachricht["t"] = "fuellen".into();
    let Some(rahmen) = rahmen else {
        return plattform::fuellen(&app, &id, &fuer, nachricht.to_string());
    };
    let gilt = ZAHLRAHMEN.lock().unwrap().get(&id).is_some_and(|z| z.gilt(&herkunft(&fuer), &rahmen));
    if !werte.zahlung() || !gilt {
        return Err("Das Formular ist nicht mehr da".into());
    }
    // Der Rahmen prüft selbst noch einmal, ob er auf dieser Herkunft steht.
    nachricht["herkunft"] = rahmen.clone().into();
    plattform::fuellen_rahmen(&app, &id, &fuer, &rahmen, nachricht.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nimmt_nur_bekannte_und_knappe_meldungen() {
        assert!(matches!(lesen(r#"{"t":"feld","passwort":true,"neu":false}"#), Some(Meldung::Feld { passwort: true, neu: false, sicher: false, aktiv: false })));
        assert!(matches!(lesen(r#"{"t":"feld","passwort":true,"neu":true,"sicher":true,"aktiv":true}"#), Some(Meldung::Feld { aktiv: true, .. })));
        assert!(matches!(
            lesen(r#"{"t":"absenden","benutzer":"a","passwort":"b","neu":true,"aktiv":true}"#),
            Some(Meldung::Absenden { aktiv: true, .. })
        ));
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
        // Bis 09.10.2026 kannte `name_passt` nur vier Bereiche unsichtbarer Zeichen.
        assert!(mit(|k| k.inhaber = Some("Ada\u{061C}Lovelace".into())).is_err());
        assert!(mit(|k| k.inhaber = Some("Ada\u{00AD}".into())).is_err());
        let beides = Fuellen { karte: Some(karte()), passwort: Some("x".into()), ..Default::default() };
        assert!(beides.pruefen().is_err());
        let konto = |iban: &str| Fuellen { konto: Some(Konto { iban: iban.into(), inhaber: None, bic: Some("COBADEFFXXX".into()) }), ..Default::default() }.pruefen();
        assert!(konto("DE89370400440532013000").is_ok());
        assert!(konto("de89370400440532013000").is_err());
    }

    #[test]
    fn ein_rahmen_meldet_seine_herkunft_nur_als_https() {
        assert!(matches!(lesen(r#"{"t":"rahmen","herkunft":"https://js.stripe.com"}"#), Some(Meldung::Rahmen { herkunft: Some(_) })));
        assert!(matches!(lesen(r#"{"t":"rahmen","herkunft":null}"#), Some(Meldung::Rahmen { herkunft: None })));
        for falsch in ["http://js.stripe.com", "https://js.stripe.com/pfad", "https://JS.stripe.com", "javascript:1", ""] {
            let roh = serde_json::json!({ "t": "rahmen", "herkunft": falsch }).to_string();
            assert!(lesen(&roh).is_none(), "{falsch}");
        }
    }

    #[test]
    fn ein_unterrahmen_darf_nur_da_und_zahlung_sagen() {
        let lesen = |roh: &str| serde_json::from_str::<RahmenMeldung>(roh).ok();
        assert!(matches!(lesen(r#"{"t":"da"}"#), Some(RahmenMeldung::Da)));
        assert!(matches!(lesen(r#"{"t":"zahlung","art":"konto"}"#), Some(RahmenMeldung::Zahlung { art: ZahlArt::Konto })));
        assert!(lesen(r#"{"t":"feld","passwort":true,"neu":false}"#).is_none());
        assert!(lesen(r#"{"t":"absenden","benutzer":"a","passwort":"b","neu":false}"#).is_none());
        assert!(lesen(r#"{"t":"rahmen","herkunft":null}"#).is_none());
        assert_eq!(https_herkunft("https://js.stripe.com/v3/elements-inner-card.html").as_deref(), Some("https://js.stripe.com"));
        assert_eq!(https_herkunft("http://js.stripe.com/"), None);
        assert_eq!(https_herkunft("about:srcdoc"), None);
    }

    const KASSE: &str = "https://laden.example";
    const STRIPE: &str = "https://js.stripe.com";

    fn oben(z: &mut Zahlrahmen, h: Option<&str>) -> Option<(String, ZahlArt)> {
        z.aendern(KASSE, Aenderung::Oben(h.map(String::from)))
    }

    fn unten(z: &mut Zahlrahmen, h: &str) -> Option<(String, ZahlArt)> {
        z.aendern(KASSE, Aenderung::Unten(h.into(), ZahlArt::Karte))
    }

    /// Angeboten wird nur, wenn die oberste Seite und der Rahmen dieselbe
    /// Herkunft nennen, egal, wer zuerst ankommt.
    #[test]
    fn ein_rahmen_braucht_die_bestaetigung_der_obersten_seite() {
        let anbieten = Some((STRIPE.to_string(), ZahlArt::Karte));
        let mut z = Zahlrahmen::default();
        assert_eq!(unten(&mut z, STRIPE), None);
        assert_eq!(oben(&mut z, Some(STRIPE)), anbieten);
        assert!(z.gilt(KASSE, STRIPE));

        let mut z = Zahlrahmen::default();
        assert_eq!(oben(&mut z, Some(STRIPE)), None);
        assert_eq!(unten(&mut z, STRIPE), anbieten);
        // Einmal angeboten reicht, bis der Fokus wechselt.
        assert_eq!(unten(&mut z, STRIPE), None);
        assert_eq!(oben(&mut z, None), None);
        assert!(!z.gilt(KASSE, STRIPE));
        assert_eq!(oben(&mut z, Some(STRIPE)), anbieten);
    }

    #[test]
    fn ein_fremder_rahmen_bekommt_nichts() {
        let mut z = Zahlrahmen::default();
        oben(&mut z, Some(STRIPE));
        // Ein Werberahmen meldet ein Kartenfeld, hat aber nicht den Fokus der Seite.
        assert_eq!(unten(&mut z, "https://werbung.example"), None);
        assert!(!z.gilt(KASSE, "https://werbung.example"));
        assert!(!z.gilt(KASSE, STRIPE));
        // Die Seite wechselt: was vorher galt, gilt nicht mehr.
        let mut z = Zahlrahmen::default();
        oben(&mut z, Some(STRIPE));
        unten(&mut z, STRIPE);
        assert!(z.gilt(KASSE, STRIPE));
        assert!(!z.gilt("https://anderer.example", STRIPE));
        assert_eq!(z.aendern("https://anderer.example", Aenderung::Oben(Some(STRIPE.into()))), None);
        assert!(!z.gilt("https://anderer.example", STRIPE));
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
