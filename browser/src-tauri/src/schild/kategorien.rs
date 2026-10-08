//! Kategorielisten für den Jugend- und Suchtschutz (`schutz.rs`).
//!
//! Erwachsene, Glücksspiel und soziale Netzwerke kommen aus den Erweiterungen
//! von StevenBlack/hosts (Sammlung unter MIT, Einzelquellen laut deren
//! Readme). Geladen wird eine Liste erst, wenn ihre Kategorie an ist, und
//! danach höchstens alle vier Tage neu; der Server erfährt dabei nur den Abruf.
//! Für Spiele und Shopping gibt es keine gepflegte offene Liste, dort sperrt
//! eine eingebaute Liste die großen Seiten. Bis eine Liste da ist, gilt ihre
//! eingebaute.

use std::collections::HashSet;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

use super::listen::{alter, holen, ordner, ERNEUERN_NACH};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Kategorie {
    Erwachsene,
    Gluecksspiel,
    Sozial,
    Spiele,
    Shopping,
}

const STEVENBLACK: &str = "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates";

impl Kategorie {
    pub const ALLE: [Kategorie; 5] =
        [Kategorie::Erwachsene, Kategorie::Gluecksspiel, Kategorie::Sozial, Kategorie::Spiele, Kategorie::Shopping];

    pub fn name(self) -> &'static str {
        match self {
            Kategorie::Erwachsene => "erwachsene",
            Kategorie::Gluecksspiel => "gluecksspiel",
            Kategorie::Sozial => "sozial",
            Kategorie::Spiele => "spiele",
            Kategorie::Shopping => "shopping",
        }
    }

    fn quelle(self) -> Option<String> {
        let teil = match self {
            Kategorie::Erwachsene => "porn",
            Kategorie::Gluecksspiel => "gambling",
            Kategorie::Sozial => "social",
            Kategorie::Spiele | Kategorie::Shopping => return None,
        };
        Some(format!("{STEVENBLACK}/{teil}-only/hosts"))
    }

    fn eingebaut(self) -> &'static [&'static str] {
        match self {
            Kategorie::Erwachsene => &[
                "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "youporn.com", "redtube.com",
                "onlyfans.com", "chaturbate.com", "stripchat.com", "spankbang.com", "eporner.com",
                "brazzers.com", "tube8.com", "beeg.com",
            ],
            Kategorie::Gluecksspiel => &[
                "bet365.com", "bwin.com", "bwin.de", "tipico.de", "tipico.com", "pokerstars.com",
                "pokerstars.de", "888.com", "unibet.com", "betway.com", "interwetten.com", "lottoland.com",
                "stake.com", "williamhill.com", "mrgreen.com", "leovegas.com",
            ],
            Kategorie::Sozial => &[
                "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com", "reddit.com",
                "snapchat.com", "pinterest.com", "linkedin.com", "tumblr.com", "threads.net",
                "bsky.app", "vk.com", "twitch.tv",
            ],
            Kategorie::Spiele => &[
                "steampowered.com", "steamcommunity.com", "epicgames.com", "roblox.com", "battle.net",
                "blizzard.com", "riotgames.com", "leagueoflegends.com", "ea.com", "gog.com", "itch.io",
                "poki.com", "poki.de", "crazygames.com", "miniclip.com", "y8.com", "kongregate.com",
                "spielaffe.de", "jetztspielen.de", "agame.com", "armorgames.com", "friv.com",
                "fortnite.com", "minecraft.net", "playstation.com", "xbox.com", "ubisoft.com",
            ],
            Kategorie::Shopping => &[
                "amazon.de", "amazon.com", "amazon.co.uk", "amazon.fr", "amazon.it", "amazon.es",
                "amazon.nl", "amazon.at", "ebay.de", "ebay.com", "ebay.at", "zalando.de", "zalando.at",
                "zalando.ch", "otto.de", "temu.com", "shein.com", "aliexpress.com", "wish.com", "etsy.com",
                "kaufland.de", "mediamarkt.de", "saturn.de", "idealo.de", "galaxus.de", "galaxus.ch",
                "asos.com", "aboutyou.de", "hm.com", "bonprix.de", "lidl.de", "tchibo.de",
            ],
        }
    }
}

/// Sieht der Text aus wie eine Hosts-Datei von StevenBlack?
pub fn ist_hostliste(text: &str) -> bool {
    let kopf: String = text.chars().take(4000).collect();
    kopf.contains("# Title:") && text.lines().any(|z| z.starts_with("0.0.0.0 "))
}

/// Die Hosts einer Hosts-Datei (`0.0.0.0 host`); alles andere fällt weg.
pub fn hosts_aus(text: &str) -> HashSet<Box<str>> {
    text.lines()
        .filter_map(|zeile| {
            let mut teile = zeile.split('#').next()?.split_whitespace();
            let ziel = teile.next()?;
            let host = teile.next()?.to_ascii_lowercase();
            let gueltig = matches!(ziel, "0.0.0.0" | "127.0.0.1")
                && host.contains('.')
                && host != "0.0.0.0"
                && host.len() <= 253
                && host.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'));
            gueltig.then(|| host.into_boxed_str())
        })
        .collect()
}

fn datei(app: &AppHandle, k: Kategorie) -> Option<PathBuf> {
    Some(ordner(app)?.join(format!("schutz-{}.txt", k.name())))
}

/// Eingebaute Liste plus die zuletzt geladene, falls es eine gibt.
pub fn laden(app: &AppHandle, k: Kategorie) -> HashSet<Box<str>> {
    let mut hosts: HashSet<Box<str>> = k.eingebaut().iter().map(|h| Box::from(*h)).collect();
    if let Some(text) = datei(app, k).and_then(|p| std::fs::read_to_string(p).ok()) {
        if ist_hostliste(&text) {
            hosts.extend(hosts_aus(&text));
        }
    }
    hosts
}

/// Holt die Listen der Kategorien, die fehlen oder älter als vier Tage sind.
/// `true`, wenn eine neu da ist.
pub async fn erneuern(app: &AppHandle, kategorien: &[Kategorie]) -> bool {
    let mut neu = false;
    for &k in kategorien {
        let (Some(url), Some(pfad)) = (k.quelle(), datei(app, k)) else { continue };
        if alter(&pfad).is_some_and(|a| a < ERNEUERN_NACH) {
            continue;
        }
        match holen(&url, ist_hostliste).await {
            Ok(text) => {
                let teil = pfad.with_extension("txt.part");
                if std::fs::write(&teil, text).is_ok() && std::fs::rename(&teil, &pfad).is_ok() {
                    neu = true;
                }
            }
            Err(fehler) => eprintln!("[MSB] Schutzliste {} nicht geladen: {fehler}", k.name()),
        }
    }
    neu
}

#[derive(Serialize)]
pub struct ListeStand {
    pub kategorie: Kategorie,
    /// Lädt die Kategorie eine Liste nach? Spiele und Shopping nicht.
    pub nachgeladen: bool,
    /// Sekunden seit dem letzten Abruf, `None` = noch nie geladen.
    pub alter_sekunden: Option<u64>,
}

pub fn stand(app: &AppHandle) -> Vec<ListeStand> {
    Kategorie::ALLE
        .iter()
        .map(|&k| ListeStand {
            kategorie: k,
            nachgeladen: k.quelle().is_some(),
            alter_sekunden: k.quelle().and(datei(app, k)).and_then(|p| alter(&p)).map(|a| a.as_secs()),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn liest_nur_hosts_aus_der_hosts_datei() {
        let text = "# Title: StevenBlack/hosts extension gambling\n127.0.0.1 localhost\n0.0.0.0 0.0.0.0\n\
                    0.0.0.0 Casino.Example # Kommentar\n0.0.0.0 bad host\n1.2.3.4 fremd.example\n0.0.0.0 a/b.example\n";
        assert!(ist_hostliste(text));
        let hosts = hosts_aus(text);
        assert_eq!(hosts.len(), 1, "{hosts:?}");
        assert!(hosts.contains("casino.example"));
        assert!(!ist_hostliste("<!doctype html><title>Fehler</title>"));
    }

    #[test]
    fn spiele_und_shopping_laden_nichts_nach() {
        assert!(Kategorie::Spiele.quelle().is_none());
        assert!(Kategorie::Shopping.quelle().is_none());
        assert!(Kategorie::Erwachsene.quelle().unwrap().ends_with("/porn-only/hosts"));
        for k in Kategorie::ALLE {
            assert!(k.eingebaut().iter().all(|h| h.contains('.') && *h == h.to_ascii_lowercase()));
        }
    }
}
