//! Was der Jugend- und Suchtschutz gerade sperrt, gefragt bei jeder Anfrage
//! und jeder Navigation eines Tabs. Die Frage läuft auf dem UI-Faden der
//! WebView2 (Regel 122): nur Nachschlagen in Mengen, kein Netz, keine Datei.
//!
//! Gesperrt ist ein Host, wenn er oder eine seiner Elterndomains in einer
//! aktiven Kategorie oder in der eigenen Liste steht und keine Ausnahme ihn
//! freigibt; die genaueste Angabe gilt. Mit „Erwachsene“ kommt die sichere
//! Suche dazu: Google, Bing, DuckDuckGo und Brave bekommen ihren Parameter,
//! YouTube den eingeschränkten Modus per Kopfzeile.
//!
//! Solange eine Kategorie aktiv ist, öffnet der Browser öffentliche Adressen
//! nur über ihren Namen: eine Seite unter ihrer IP-Adresse stand in keiner
//! Liste und lud (bis 09.10.2026). Geräte im eigenen Netz (Router, NAS) und
//! IP-Adressen in den Ausnahmen bleiben erreichbar.

use std::collections::HashSet;
use std::sync::{Arc, LazyLock, RwLock};

use super::kategorien::Kategorie;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Grund {
    Kategorie(Kategorie),
    Eigene,
    /// Eine öffentliche IP-Adresse statt eines Namens.
    Adresse,
    /// Eine Suche, an die der Browser die sichere Suche nicht hängen kann.
    Suche,
}

impl Grund {
    pub fn name(self) -> &'static str {
        match self {
            Grund::Kategorie(k) => k.name(),
            Grund::Eigene => "eigene",
            Grund::Adresse => "adresse",
            Grund::Suche => "suche",
        }
    }
}

#[derive(Default)]
pub struct Sperre {
    pub listen: Vec<(Kategorie, Arc<HashSet<Box<str>>>)>,
    pub eigene: HashSet<String>,
    pub ausnahmen: HashSet<String>,
    pub sichere_suche: bool,
}

static SPERRE: LazyLock<RwLock<Sperre>> = LazyLock::new(Default::default);

pub fn setzen(sperre: Sperre) {
    *SPERRE.write().unwrap() = sperre;
}

/// Ein Host, wie ihn Listen und Ausnahmen führen: klein, ohne `*.`, Schema
/// und Pfad, mit mindestens einem Punkt. Dieselbe Regel gilt für „Immer wach“.
pub fn host_normal(eingabe: &str) -> Option<String> {
    let host = eingabe.trim().trim_start_matches("*.").trim_end_matches('.').to_ascii_lowercase();
    let gueltig = host.contains('.')
        && host.len() <= 253
        && !host.starts_with('.')
        && host.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-');
    gueltig.then_some(host)
}

fn host_von(url: &str) -> Option<String> {
    let u = url::Url::parse(url).ok()?;
    if !matches!(u.scheme(), "http" | "https" | "ws" | "wss") {
        return None;
    }
    Some(u.host_str()?.trim_end_matches('.').to_ascii_lowercase())
}

/// `Some(true)` für eine öffentliche IP-Adresse, `Some(false)` für eine im
/// eigenen Netz oder auf dem Gerät, `None` für einen Namen.
fn ip_oeffentlich(url: &str) -> Option<bool> {
    let ip = match url::Url::parse(url).ok()?.host()? {
        url::Host::Domain(_) => return None,
        url::Host::Ipv4(v4) => v4,
        url::Host::Ipv6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => v4,
            None => {
                let erstes = v6.segments()[0];
                let lokal = v6.is_loopback() || v6.is_unspecified() || erstes & 0xfe00 == 0xfc00 || erstes & 0xffc0 == 0xfe80;
                return Some(!lokal);
            }
        },
    };
    let [a, b, ..] = ip.octets();
    let cgnat = a == 100 && (64..128).contains(&b);
    Some(!(ip.is_private() || ip.is_loopback() || ip.is_link_local() || ip.is_unspecified() || cgnat))
}

/// Der Host und seine Elterndomains, ohne die nackte Endung.
fn mit_eltern(host: &str) -> impl Iterator<Item = &str> {
    std::iter::successors(Some(host), |h| h.split_once('.').map(|(_, rest)| rest)).filter(|h| h.contains('.'))
}

impl Sperre {
    pub fn einstufen(&self, url: &str) -> Option<Grund> {
        if self.sichere_suche && suche_ohne_parameter(url) {
            return Some(Grund::Suche);
        }
        if self.listen.is_empty() && self.eigene.is_empty() {
            return None;
        }
        let host = host_von(url)?;
        if ip_oeffentlich(url) == Some(true) && !self.listen.is_empty() && !self.ausnahmen.contains(host.as_str()) {
            return Some(Grund::Adresse);
        }
        // Die genaueste Angabe gilt: eine eigene Sperre von `mail.google.com`
        // schlägt die Ausnahme `google.com` und umgekehrt. Auf derselben
        // Ebene gewinnt die Sperre.
        for h in mit_eltern(&host) {
            if self.eigene.contains(h) {
                return Some(Grund::Eigene);
            }
            if self.ausnahmen.contains(h) {
                return None;
            }
        }
        self.listen
            .iter()
            .find(|(_, liste)| mit_eltern(&host).any(|h| liste.contains(h)))
            .map(|(k, _)| Grund::Kategorie(*k))
    }
}

/// Warum der Schutz diese Adresse sperrt, `None` = durchlassen.
pub fn gesperrt(url: &str) -> Option<Grund> {
    SPERRE.read().unwrap().einstufen(url)
}

/// Die Adresse mit erzwungener sicherer Suche, wenn sie eine Suche ist, der
/// der Parameter fehlt oder die ihn anders setzt.
pub fn sicher_umschreiben(url: &str) -> Option<String> {
    let mut u = url::Url::parse(url).ok()?;
    if u.scheme() != "https" && u.scheme() != "http" {
        return None;
    }
    let host = u.host_str()?.trim_end_matches('.').to_ascii_lowercase();
    let host = host.trim_start_matches("www.");
    let pfad = u.path();
    let suche = u.query_pairs().any(|(k, _)| k == "q");
    let (name, wert) = if (host.starts_with("google.") || host.contains(".google.")) && (pfad == "/search" || pfad.starts_with("/images")) {
        ("safe", "active")
    } else if (host == "bing.com" || host.ends_with(".bing.com")) && pfad.ends_with("/search") {
        ("adlt", "strict")
    } else if matches!(host, "duckduckgo.com" | "html.duckduckgo.com" | "lite.duckduckgo.com") && suche {
        ("kp", "1")
    } else if host == "search.brave.com" && suche {
        ("safesearch", "strict")
    } else {
        return None;
    };
    if u.query_pairs().filter(|(k, _)| k == name).map(|(_, v)| v.into_owned()).eq([wert.to_string()]) {
        return None;
    }
    let rest: Vec<(String, String)> = u.query_pairs().filter(|(k, _)| k != name).map(|(k, v)| (k.into_owned(), v.into_owned())).collect();
    u.query_pairs_mut().clear().extend_pairs(rest).append_pair(name, wert);
    Some(u.into())
}

/// DuckDuckGo HTML und Lite schicken die Suche per POST, `q` steht im Körper.
/// An die Adresse lässt sich `kp` dort nicht hängen; solange die sichere
/// Suche gilt, sind diese Seiten ohne `q` in der Adresse gesperrt. Eine
/// Suche per GET schreibt [`sicher_umschreiben`] um.
fn suche_ohne_parameter(url: &str) -> bool {
    let Ok(u) = url::Url::parse(url) else { return false };
    let Some(host) = u.host_str().map(|h| h.trim_end_matches('.').to_ascii_lowercase()) else { return false };
    let ddg = matches!(host.trim_start_matches("www."), "duckduckgo.com" | "html.duckduckgo.com" | "lite.duckduckgo.com");
    ddg && (u.path().starts_with("/html") || u.path().starts_with("/lite")) && !u.query_pairs().any(|(k, _)| k == "q")
}

/// Navigation zu einer Suche ohne sichere Suche: hierhin stattdessen.
pub fn sichere_suche(url: &str) -> Option<String> {
    if !SPERRE.read().unwrap().sichere_suche {
        return None;
    }
    sicher_umschreiben(url)
}

const YOUTUBE: [&str; 6] = [
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtubei.googleapis.com",
    "youtube.googleapis.com",
    "youtube-nocookie.com",
];

/// Braucht diese Anfrage `YouTube-Restrict: Strict`?
pub fn youtube_einschraenken(url: &str) -> bool {
    if !SPERRE.read().unwrap().sichere_suche {
        return false;
    }
    host_von(url).is_some_and(|h| YOUTUBE.contains(&h.trim_start_matches("www.")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sperre() -> Sperre {
        let liste: HashSet<Box<str>> = ["casino.example".into(), "www.wetten.example".into()].into_iter().collect();
        Sperre {
            listen: vec![(Kategorie::Gluecksspiel, Arc::new(liste))],
            eigene: ["zeitfresser.example".to_string()].into_iter().collect(),
            ausnahmen: ["erlaubt.casino.example".to_string()].into_iter().collect(),
            sichere_suche: true,
        }
    }

    #[test]
    fn sperrt_host_und_unterdomains_ausser_ausnahmen() {
        let s = sperre();
        assert_eq!(s.einstufen("https://casino.example/"), Some(Grund::Kategorie(Kategorie::Gluecksspiel)));
        assert_eq!(s.einstufen("https://live.casino.example/x"), Some(Grund::Kategorie(Kategorie::Gluecksspiel)));
        assert_eq!(s.einstufen("wss://casino.example./ws"), Some(Grund::Kategorie(Kategorie::Gluecksspiel)));
        assert_eq!(s.einstufen("https://wetten.example/"), None, "nur www. steht in der Liste");
        assert_eq!(s.einstufen("https://notcasino.example/"), None);
        assert_eq!(s.einstufen("https://erlaubt.casino.example/"), None);
        assert_eq!(s.einstufen("https://a.zeitfresser.example/"), Some(Grund::Eigene));
        assert_eq!(s.einstufen("msb://einstellungen"), None);
        assert_eq!(Sperre::default().einstufen("https://casino.example/"), None);
    }

    #[test]
    fn oeffentliche_ip_adressen_nur_mit_kategorien() {
        let mut s = sperre();
        s.ausnahmen.insert("203.0.113.7".into());
        assert_eq!(s.einstufen("https://93.184.215.14/"), Some(Grund::Adresse));
        assert_eq!(s.einstufen("http://1572395790/"), Some(Grund::Adresse), "Dezimalform derselben Adresse");
        assert_eq!(s.einstufen("https://[2606:4700::1111]/"), Some(Grund::Adresse));
        assert_eq!(s.einstufen("https://[::ffff:5db8:d70e]/"), Some(Grund::Adresse));
        for frei in [
            "http://192.168.178.1/",
            "http://10.0.2.2:8791/",
            "http://127.0.0.1/",
            "http://100.64.1.2/",
            "http://169.254.1.1/",
            "http://[::1]/",
            "http://[fd00::1]/",
            "http://[fe80::1]/",
            "https://203.0.113.7/",
        ] {
            assert_eq!(s.einstufen(frei), None, "{frei}");
        }
        s.listen.clear();
        assert_eq!(s.einstufen("https://93.184.215.14/"), None, "ohne Kategorie bleibt die Adresse offen");
        s.eigene.insert("93.184.215.14".into());
        assert_eq!(s.einstufen("https://93.184.215.14/"), Some(Grund::Eigene));
    }

    #[test]
    fn hosts_werden_einheitlich() {
        assert_eq!(host_normal(" *.Casino.Example. ").as_deref(), Some("casino.example"));
        assert_eq!(host_normal("localhost"), None);
        assert_eq!(host_normal("https://x.de/"), None);
        assert_eq!(host_normal(".de"), None);
    }

    #[test]
    fn suche_bekommt_ihren_parameter() {
        let google = sicher_umschreiben("https://www.google.de/search?q=test&safe=off").unwrap();
        assert_eq!(google, "https://www.google.de/search?q=test&safe=active");
        assert_eq!(sicher_umschreiben(&google), None, "kein zweites Umschreiben");
        assert_eq!(sicher_umschreiben("https://www.google.com/maps?q=x"), None);
        assert_eq!(sicher_umschreiben("https://images.google.com/images?q=x").unwrap(), "https://images.google.com/images?q=x&safe=active");
        assert_eq!(sicher_umschreiben("https://www.bing.com/images/search?q=x").unwrap(), "https://www.bing.com/images/search?q=x&adlt=strict");
        assert_eq!(sicher_umschreiben("https://duckduckgo.com/?q=x&kp=-2").unwrap(), "https://duckduckgo.com/?q=x&kp=1");
        assert_eq!(sicher_umschreiben("https://duckduckgo.com/about"), None);
        assert_eq!(sicher_umschreiben("https://search.brave.com/search?q=x&safesearch=off").unwrap(), "https://search.brave.com/search?q=x&safesearch=strict");
    }

    /// Bugjagd 08.10.2026: ein Punkt am Host und DuckDuckGo ohne JavaScript
    /// umgingen die sichere Suche.
    #[test]
    fn sichere_suche_auch_auf_anderen_bing_hosts() {
        assert_eq!(sicher_umschreiben("https://cn.bing.com/images/search?q=x&adlt=off").unwrap(), "https://cn.bing.com/images/search?q=x&adlt=strict");
        assert_eq!(sicher_umschreiben("https://bing.com/search?q=x").unwrap(), "https://bing.com/search?q=x&adlt=strict");
        assert_eq!(sicher_umschreiben("https://nichtbing.com/search?q=x"), None);
    }

    // DuckDuckGo HTML und Lite schicken `q` per POST im Körper (bis 10.10.2026 ungeprüft).
    #[test]
    fn duckduckgo_per_post_ist_bei_sicherer_suche_gesperrt() {
        let s = sperre();
        for url in ["https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/", "https://duckduckgo.com/html", "https://duckduckgo.com./lite/?kp=-2"] {
            assert_eq!(s.einstufen(url), Some(Grund::Suche), "{url}");
        }
        assert_eq!(s.einstufen("https://html.duckduckgo.com/html/?q=x&kp=1"), None, "per GET schreibt sicher_umschreiben um");
        assert_eq!(s.einstufen("https://duckduckgo.com/?q=x"), None);
        assert_eq!(Sperre { sichere_suche: false, ..sperre() }.einstufen("https://html.duckduckgo.com/html/"), None);
    }

    #[test]
    fn sichere_suche_auch_mit_punkt_am_host_und_ohne_javascript() {
        for url in [
            "https://www.bing.com./search?q=x&adlt=off",
            "https://duckduckgo.com./?q=x&kp=-2",
            "https://html.duckduckgo.com/html/?q=x&kp=-2",
            "https://lite.duckduckgo.com/lite/?q=x&kp=-2",
            "https://search.brave.com./search?q=x",
        ] {
            assert!(sicher_umschreiben(url).is_some(), "nicht umgeschrieben: {url}");
        }
    }

    /// Bugjagd 08.10.2026: die Ausnahme der Elterndomain hob die eigene Sperre
    /// einer Unterdomain auf.
    #[test]
    fn die_genaueste_angabe_gilt() {
        let mut s = sperre();
        s.eigene = ["mail.google.com".to_string(), "google.de".to_string()].into_iter().collect();
        s.ausnahmen = ["google.com".to_string(), "maps.google.de".to_string(), "casino.example".to_string()].into_iter().collect();
        assert_eq!(s.einstufen("https://mail.google.com/"), Some(Grund::Eigene));
        assert_eq!(s.einstufen("https://x.mail.google.com/"), Some(Grund::Eigene));
        assert_eq!(s.einstufen("https://google.com/"), None);
        assert_eq!(s.einstufen("https://google.de/"), Some(Grund::Eigene));
        assert_eq!(s.einstufen("https://maps.google.de/"), None);
        assert_eq!(s.einstufen("https://live.casino.example/"), None, "die Ausnahme schlägt weiter die Kategorie");
        s.eigene.insert("casino.example".into());
        assert_eq!(s.einstufen("https://casino.example/"), Some(Grund::Eigene), "auf derselben Ebene gewinnt die Sperre");
    }
}
