//! HTTPS zuerst: eine Adresse mit `http://` lädt der Tab zuerst verschlüsselt.
//! Klappt das nicht (kein HTTPS, Zertifikatsfehler) oder leitet der Server
//! zurück auf `http://`, lädt er die Adresse wie angegeben und merkt sich den
//! Host bis zum Neustart. Eine Warnseite gibt es nicht.
//!
//! Was offensichtlich nicht im Internet liegt, bleibt, wie es ist:
//! `localhost`, IP-Adressen, Namen ohne Punkt (`router`, `nas`), Namen im
//! eigenen Netz (`.local`, `.lan`, `.internal`, `.test` …) und jede Adresse
//! mit eigenem Port (`:3000`). Dort hat ein Entwicklungsserver selten ein
//! Zertifikat, und HTTPS zu versuchen kostet nur Zeit.

use std::collections::{HashMap, HashSet};
use std::sync::{LazyLock, Mutex};
use std::time::{Duration, Instant};

/// So lange gilt ein Fehler oder ein Rücksprung auf `http://` als Folge
/// der Hochstufung.
const FRIST: Duration = Duration::from_secs(30);
/// Mehr Hosts merkt sich der Browser nicht; danach fängt die Liste von vorn an.
const HOECHSTENS: usize = 1000;

/// `.box` für `fritz.box`, den Router vieler Haushalte.
const LOKAL: [&str; 13] = [
    ".localhost", ".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet", ".corp", ".test",
    ".example", ".invalid", ".onion", ".box",
];

#[derive(Default)]
struct Stand {
    /// Hosts, die gerade hochgestuft wurden, und wann.
    versucht: HashMap<String, Instant>,
    /// Hosts, die nur `http://` können.
    nur_http: HashSet<String>,
}

static STAND: LazyLock<Mutex<Stand>> = LazyLock::new(Default::default);

/// Bleibt diese Adresse, wie sie ist?
fn ausgenommen(url: &url::Url) -> bool {
    if url.port().is_some() {
        return true;
    }
    match url.host() {
        Some(url::Host::Domain(name)) => {
            let name = name.trim_end_matches('.').to_ascii_lowercase();
            name == "localhost" || !name.contains('.') || LOKAL.iter().any(|e| name.ends_with(e))
        }
        // IP-Adressen und Adressen ohne Host.
        _ => true,
    }
}

fn host(url: &url::Url) -> Option<String> {
    url.host_str().map(|h| h.trim_end_matches('.').to_ascii_lowercase())
}

/// Die verschlüsselte Fassung einer `http://`-Adresse, oder `None`, wenn
/// sie bleibt. `weiterleitung`: der Server hat hierher umgeleitet; kommt das
/// kurz nach einer Hochstufung desselben Hosts, kann er nur `http://`.
pub fn hochstufen(url: &url::Url, weiterleitung: bool) -> Option<url::Url> {
    if url.scheme() != "http" || ausgenommen(url) {
        return None;
    }
    let host = host(url)?;
    let mut stand = STAND.lock().unwrap();
    if stand.nur_http.contains(&host) {
        return None;
    }
    let jetzt = Instant::now();
    stand.versucht.retain(|_, wann| jetzt.duration_since(*wann) < FRIST);
    if weiterleitung && stand.versucht.contains_key(&host) {
        stand.versucht.remove(&host);
        merken(&mut stand, host);
        return None;
    }
    let mut sicher = url.clone();
    sicher.set_scheme("https").ok()?;
    stand.versucht.insert(host, jetzt);
    Some(sicher)
}

fn merken(stand: &mut Stand, host: String) {
    if stand.nur_http.len() >= HOECHSTENS {
        stand.nur_http.clear();
    }
    stand.nur_http.insert(host);
}

/// Eine Seite ließ sich nicht laden. War sie eben erst hochgestuft, die
/// Adresse mit `http://`, die stattdessen lädt; sonst `None` (Fehlerseite).
pub fn rueckfall(url: &str) -> Option<String> {
    let geparst = url::Url::parse(url).ok()?;
    if geparst.scheme() != "https" {
        return None;
    }
    let host = host(&geparst)?;
    let mut stand = STAND.lock().unwrap();
    let wann = stand.versucht.remove(&host)?;
    if wann.elapsed() >= FRIST {
        return None;
    }
    merken(&mut stand, host);
    let mut unverschluesselt = geparst;
    unverschluesselt.set_scheme("http").ok()?;
    Some(unverschluesselt.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hoch(url: &str, weiterleitung: bool) -> Option<String> {
        hochstufen(&url::Url::parse(url).unwrap(), weiterleitung).map(String::from)
    }

    #[test]
    fn stuft_oeffentliche_adressen_hoch_und_laesst_entwickler_in_ruhe() {
        assert_eq!(hoch("http://hoch.example.com/a?b=1", false).as_deref(), Some("https://hoch.example.com/a?b=1"));
        for bleibt in [
            "http://localhost:5173/",
            "http://localhost/",
            "http://app.localhost/",
            "http://127.0.0.1/",
            "http://192.168.178.1/",
            "http://[::1]/",
            "http://router/",
            "http://nas.local/",
            "http://fritz.box/",
            "http://dienst.internal/",
            "http://seite.test/",
            "http://mein-server.de:8080/",
            "https://schon.example.com/",
        ] {
            assert_eq!(hoch(bleibt, false), None, "{bleibt}");
        }
    }

    #[test]
    fn faellt_nach_einem_fehler_auf_http_zurueck_und_merkt_es_sich() {
        assert!(hoch("http://rueck.example.com/x", false).is_some());
        assert_eq!(rueckfall("https://rueck.example.com/x").as_deref(), Some("http://rueck.example.com/x"));
        assert_eq!(hoch("http://rueck.example.com/y", false), None);
        // Nur, was der Browser selbst hochgestuft hat: wer `https://` meint, bekommt die Fehlerseite.
        assert_eq!(rueckfall("https://selbst.example.com/"), None);
    }

    #[test]
    fn eine_weiterleitung_zurueck_auf_http_kreist_nicht() {
        assert!(hoch("http://kreis.example.com/", false).is_some());
        assert_eq!(hoch("http://kreis.example.com/", true), None);
        assert_eq!(hoch("http://kreis.example.com/", false), None);
        // Eine Weiterleitung ohne vorherige Hochstufung wird selbst hochgestuft.
        assert!(hoch("http://weiter.example.com/", true).is_some());
    }
}
