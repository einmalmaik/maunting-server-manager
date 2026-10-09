//! Seitenübersetzung: Text der Seite hin, Übersetzung zurück.
//!
//! Übersetzt wird in der Oberfläche (`frontend/src/browser/uebersetzung/`),
//! auf dem Gerät. Die Seite (`seite.js`) gibt ihren Text in Stücken heraus,
//! aber nur auf Anfrage: Rust merkt sich je Tab die eine offene Anfrage und
//! lässt nur die Antwort mit dieser Nummer und von dieser Herkunft durch.
//! Eine Seite kann so nichts ungefragt an die Oberfläche schicken.

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

use super::{id_pruefen, melden, plattform, TabEreignis};

/// So viel nimmt Rust aus einer Seite an. Ein Stück hat in `seite.js`
/// höchstens 32 KiB Zeichen; als JSON mit Umlauten und Maskierung mehr.
pub const ROH_MAX: usize = 256 * 1024;
pub const TEXTE_MAX: usize = 100;
/// Längere Texte schickt `seite.js` gar nicht erst.
pub const TEXT_MAX: usize = 2000;
/// Eine Übersetzung darf länger sein als das Original.
pub const UEBERSETZUNG_MAX: usize = 4000;
const SPRACHE_MAX: usize = 35;

/// Ein Stück Text der Seite. `sprache` ist `lang` des Dokuments, wie die
/// Seite es angibt; leer, wenn sie keines nennt. Leere `texte`: fertig.
#[derive(Clone, Debug, PartialEq)]
pub struct Stueck {
    pub nr: u32,
    pub sprache: String,
    pub texte: Vec<String>,
}

/// Die Form auf dem Draht: `{"t":"texte", …}`, nichts sonst.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Roh {
    t: String,
    nr: u32,
    sprache: String,
    texte: Vec<String>,
}

/// Liest ein Stück; alles Unbekannte, zu Lange oder schlecht Geformte fällt weg.
pub fn lesen(roh: &str) -> Option<Stueck> {
    if roh.len() > ROH_MAX || !roh.starts_with(r#"{"t":"texte""#) {
        return None;
    }
    let r: Roh = serde_json::from_str(roh).ok()?;
    if r.t != "texte" {
        return None;
    }
    let s = Stueck { nr: r.nr, sprache: r.sprache, texte: r.texte };
    let sprache_passt = s.sprache.len() <= SPRACHE_MAX && s.sprache.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
    let texte_passen = s.texte.len() <= TEXTE_MAX && s.texte.iter().all(|t| t.chars().count() <= TEXT_MAX);
    (sprache_passt && texte_passen).then_some(s)
}

/// Je Tab die offene Anfrage: Nummer und Herkunft der Seite.
static OFFEN: LazyLock<Mutex<HashMap<String, (u32, String)>>> = LazyLock::new(Default::default);

fn herkunft(url: &str) -> Option<String> {
    let u = url::Url::parse(url).ok()?;
    matches!(u.scheme(), "http" | "https").then(|| u.origin().ascii_serialization())
}

/// Ein Stück der Seite; `url` ist die Adresse aus der Webview.
pub fn stueck(app: &AppHandle, id: &str, url: &str, s: Stueck) {
    if annehmen(id, url, s.nr) {
        let (id, nr, sprache, texte) = (id.to_string(), s.nr, s.sprache, s.texte);
        melden(app, TabEreignis::Texte { id, nr, sprache, texte });
    }
}

/// Gilt das Stück als Antwort auf die offene Anfrage? Danach ist keine mehr offen.
fn annehmen(id: &str, url: &str, nr: u32) -> bool {
    let mut offen = OFFEN.lock().unwrap();
    let passt = offen.get(id).is_some_and(|(n, h)| *n == nr && herkunft(url).as_ref() == Some(h));
    if passt {
        offen.remove(id);
    }
    passt
}

/// Die Seite wechselt oder der Tab ist zu.
pub fn vergessen(id: &str) {
    OFFEN.lock().unwrap().remove(id);
}

#[derive(Clone, Copy, Debug, PartialEq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Schritt {
    /// Text sammeln und das erste Stück schicken (auch neu, nach „Original“).
    Start,
    /// Die Übersetzungen des letzten Stücks einsetzen und das nächste schicken.
    Weiter,
    /// Alles zurück, keine Antwort.
    Original,
}

/// Schickt einen Schritt an die Seite, wenn der Tab noch auf der Herkunft von
/// `fuer` steht. `texte` nur bei `Weiter`.
#[tauri::command(async)]
pub fn tab_uebersetzen(app: AppHandle, id: String, fuer: String, nr: u32, schritt: Schritt, texte: Option<Vec<String>>) -> Result<(), String> {
    id_pruefen(&id)?;
    let h = herkunft(&fuer).ok_or("Keine Webseite")?;
    let texte = texte.unwrap_or_default();
    // Übersetzungen gibt es nur zu einem Stück, und jedes Stück hat Text.
    if (schritt == Schritt::Weiter) == texte.is_empty() {
        return Err("Übersetzungen nur mit „weiter“".into());
    }
    if texte.len() > TEXTE_MAX || texte.iter().any(|t| t.chars().count() > UEBERSETZUNG_MAX) {
        return Err("Zu lang".into());
    }
    let nachricht = serde_json::json!({ "t": "uebersetzen", "nr": nr, "schritt": schritt, "texte": texte });
    // Erst merken, dann schicken: die Antwort kann vor der Rückkehr da sein.
    if schritt != Schritt::Original {
        OFFEN.lock().unwrap().insert(id.clone(), (nr, h));
    }
    let ergebnis = plattform::fuellen(&app, &id, &fuer, nachricht.to_string());
    if ergebnis.is_err() {
        vergessen(&id);
    }
    ergebnis
}

#[cfg(test)]
mod tests {
    use super::*;

    fn roh(nr: u32, texte: &[&str]) -> String {
        let texte = texte.iter().map(|t| t.to_string()).collect();
        serde_json::to_string(&Roh { t: "texte".into(), nr, sprache: "de".into(), texte }).unwrap()
    }

    #[test]
    fn liest_nur_stuecke_in_ihren_grenzen() {
        assert_eq!(lesen(&roh(3, &["Hallo"])).unwrap().texte, vec!["Hallo"]);
        assert!(lesen(r#"{"t":"texte","nr":1,"sprache":"de","texte":[],"mehr":1}"#).is_none());
        assert!(lesen(r#"{"t":"feld","passwort":true,"neu":false}"#).is_none());
        assert!(lesen(r#"{"t":"texte","nr":1,"sprache":"de\"><x","texte":[]}"#).is_none());
        let viele: Vec<&str> = vec!["a"; TEXTE_MAX + 1];
        assert!(lesen(&roh(1, &viele)).is_none());
        let lang = "x".repeat(TEXT_MAX + 1);
        assert!(lesen(&roh(1, &[&lang])).is_none());
        assert!(lesen(&format!("{}{}", roh(1, &["a"]), " ".repeat(ROH_MAX))).is_none());
    }

    #[test]
    fn nimmt_nur_die_angefragte_antwort_von_derselben_herkunft() {
        let id = "tab-uebersetzen-test";
        // Ungefragt: nichts offen.
        assert!(!annehmen(id, "https://a.example/x", 1));
        OFFEN.lock().unwrap().insert(id.into(), (2, "https://a.example".into()));
        assert!(!annehmen(id, "https://a.example/x", 1), "falsche Nummer");
        assert!(!annehmen(id, "https://b.example/x", 2), "fremde Herkunft");
        assert!(!annehmen(id, "http://a.example/x", 2), "anderes Schema");
        assert!(annehmen(id, "https://a.example/y", 2));
        // Eine Anfrage, eine Antwort.
        assert!(!annehmen(id, "https://a.example/y", 2));
        OFFEN.lock().unwrap().insert(id.into(), (3, "https://a.example".into()));
        vergessen(id);
        assert!(!annehmen(id, "https://a.example/y", 3), "nach einem Seitenwechsel");
    }

    #[test]
    fn schritte_haben_ihre_form() {
        assert_eq!(serde_json::to_string(&Schritt::Weiter).unwrap(), r#""weiter""#);
        assert_eq!(herkunft("https://a.example:8443/x?y").as_deref(), Some("https://a.example:8443"));
        assert_eq!(herkunft("chrome-extension://abc/popup.html"), None);
    }
}
