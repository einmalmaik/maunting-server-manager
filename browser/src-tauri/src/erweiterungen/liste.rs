//! Welche Erweiterungen installiert sind und laufen sollen. Die Liste ist die
//! Wahrheit; WebView2 wird ihr angeglichen (`webview.rs`). Was WebView2 kennt
//! und die Liste nicht, fällt beim nächsten Abgleich heraus.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::crx::ist_id;
use super::paket::Angaben;

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Herkunft {
    /// Aus dem Chrome Web Store, Signatur geprüft.
    Store,
    /// Entwicklermodus: ein Ordner, ungeprüft.
    Entpackt,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Eintrag {
    pub id: String,
    /// Name des Ordners unter `erweiterungen/`.
    pub ordner: String,
    pub herkunft: Herkunft,
    /// Woher ein entpackter Ordner kam, für „Neu laden“.
    #[serde(default)]
    pub quelle: Option<String>,
    pub an: bool,
    #[serde(default)]
    pub angeheftet: bool,
    pub angaben: Angaben,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Liste {
    #[serde(default)]
    pub entwicklermodus: bool,
    #[serde(default)]
    pub eintraege: Vec<Eintrag>,
}

/// Was in WebView2 stehen soll: Kennung, Ordner, läuft.
#[derive(Debug, PartialEq)]
pub struct Soll {
    pub id: String,
    pub ordner: PathBuf,
    pub an: bool,
}

/// Der Ordner einer Erweiterung, wenn ihr Name einer ist, den wir anlegen.
pub fn ordner_ok(name: &str) -> bool {
    let ok = |rest: &str, laenge: std::ops::RangeInclusive<usize>| {
        laenge.contains(&rest.len()) && rest.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
    };
    name.strip_prefix("store-").is_some_and(|r| ok(r, 34..=80)) || name.strip_prefix("entpackt-").is_some_and(|r| ok(r, 16..=16))
}

impl Liste {
    /// Liest die Datei. Was nicht passt, fällt weg (Punkt 137); eine Datei,
    /// die gar nicht zu lesen ist, heißt „keine Erweiterungen“, und der
    /// Abgleich nimmt sie aus WebView2.
    pub fn lesen(text: &str) -> Liste {
        let mut liste: Liste = serde_json::from_str(text).unwrap_or_default();
        let mut gesehen = Vec::new();
        liste.eintraege.retain(|e| {
            let gut = ist_id(&e.id) && ordner_ok(&e.ordner) && !gesehen.contains(&e.id);
            gesehen.push(e.id.clone());
            gut
        });
        liste
    }

    pub fn eintrag(&mut self, id: &str) -> Option<&mut Eintrag> {
        self.eintraege.iter_mut().find(|e| e.id == id)
    }

    /// Läuft die Erweiterung? Nie bei aktivem Jugendschutz, entpackte nur im
    /// Entwicklermodus.
    pub fn laeuft(&self, e: &Eintrag, schutz: bool) -> bool {
        e.an && !schutz && (e.herkunft == Herkunft::Store || self.entwicklermodus)
    }

    pub fn soll(&self, wurzel: &Path, schutz: bool) -> Vec<Soll> {
        self.eintraege.iter().map(|e| Soll { id: e.id.clone(), ordner: wurzel.join(&e.ordner), an: self.laeuft(e, schutz) }).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn angaben() -> Angaben {
        Angaben {
            name: "X".into(),
            version: "1".into(),
            beschreibung: String::new(),
            rechte: vec![],
            seiten: vec![],
            optional: vec![],
            symbol: None,
            popup: None,
            optionen: None,
        }
    }

    fn eintrag(id: &str, ordner: &str, herkunft: Herkunft, an: bool) -> Eintrag {
        Eintrag { id: id.into(), ordner: ordner.into(), herkunft, quelle: None, an, angeheftet: false, angaben: angaben() }
    }

    const A: &str = "ddkjiahejlhfcafbddmgiahcphecmpfh";
    const B: &str = "eimadpbcbfnmbkopoojfekhnkhdbieeh";

    #[test]
    fn jugendschutz_und_entwicklermodus_halten_erweiterungen_an() {
        let mut liste = Liste {
            entwicklermodus: false,
            eintraege: vec![
                eintrag(A, &format!("store-{A}-1.0"), Herkunft::Store, true),
                eintrag(B, "entpackt-0123456789abcdef", Herkunft::Entpackt, true),
            ],
        };
        let an = |l: &Liste, schutz| l.soll(Path::new("w"), schutz).iter().map(|s| s.an).collect::<Vec<_>>();
        assert_eq!(an(&liste, false), [true, false]);
        liste.entwicklermodus = true;
        assert_eq!(an(&liste, false), [true, true]);
        assert_eq!(an(&liste, true), [false, false]);
        assert_eq!(liste.soll(Path::new("w"), false)[1].ordner, Path::new("w").join("entpackt-0123456789abcdef"));
    }

    #[test]
    fn eine_kaputte_oder_fremde_liste_laesst_nichts_laufen() {
        assert_eq!(Liste::lesen("{ kaputt"), Liste::default());
        assert_eq!(Liste::lesen(r#"{ "eintraege": 5 }"#), Liste::default());
        let mut gut = Liste {
            entwicklermodus: true,
            eintraege: vec![
                eintrag(A, &format!("store-{A}-1.0"), Herkunft::Store, true),
                eintrag("nicht-id", "store-x", Herkunft::Store, true),
                eintrag(B, "../../Windows", Herkunft::Entpackt, true),
                eintrag(A, &format!("store-{A}-2.0"), Herkunft::Store, true),
            ],
        };
        let gelesen = Liste::lesen(&serde_json::to_string(&gut).unwrap());
        gut.eintraege.truncate(1);
        assert_eq!(gelesen, gut);
    }

    #[test]
    fn nur_eigene_ordnernamen() {
        assert!(ordner_ok(&format!("store-{A}-1.2.3")));
        assert!(ordner_ok("entpackt-0123456789abcdef"));
        for boese in ["store-..", "store-a/b", "entpackt-0123", "seiten", "", &format!("store-{A}\\..")] {
            assert!(!ordner_ok(boese), "{boese}");
        }
    }
}
