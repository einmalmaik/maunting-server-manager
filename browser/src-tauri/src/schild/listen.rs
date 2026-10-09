//! Filterlisten: eingebaut, zwischengespeichert, im Hintergrund erneuert.
//!
//! Die Listen kommen von easylist.to, die Liste gegen Cookie-Hinweise von
//! secure.fanboy.co.nz (EasyList Cookie List, dieselben Betreuer). Der
//! Browser fragt dort höchstens alle vier Tage nach; bis dahin gilt die
//! Fassung im App-Ordner. Mehr als die IP-Adresse und den Abruf selbst
//! erfährt der Server nicht: kein Cookie, keine Kennung, kein Referer.

use std::path::PathBuf;
use std::time::{Duration, SystemTime};

use adblock::lists::{FilterSet, ParseOptions};
use adblock::Engine;
use serde::Serialize;
use tauri::{AppHandle, Manager};

use super::{engine_setzen, Treffer};

pub struct Liste {
    pub name: &'static str,
    pub url: &'static str,
    pub art: Treffer,
}

pub const LISTEN: [Liste; 4] = [
    Liste { name: "easylist", url: "https://easylist.to/easylist/easylist.txt", art: Treffer::Werbung },
    Liste {
        name: "easylistgermany",
        url: "https://easylist.to/easylistgermany/easylistgermany.txt",
        art: Treffer::Werbung,
    },
    Liste { name: "easyprivacy", url: "https://easylist.to/easylist/easyprivacy.txt", art: Treffer::Tracker },
    // Blendet Cookie-Hinweise aus und blockt die Skripte, die sie zeigen.
    // Wer den Hinweis nicht beantwortet, hat nichts zugestimmt.
    Liste {
        name: "easylist-cookie",
        url: "https://secure.fanboy.co.nz/fanboy-cookiemonster.txt",
        art: Treffer::Werbung,
    },
];

pub(super) const ERNEUERN_NACH: Duration = Duration::from_secs(4 * 24 * 3600);
/// Größer ist keine der Listen; was darüber liegt, ist keine Filterliste.
const HOECHSTENS_BYTES: usize = 16 * 1024 * 1024;

/// Die großen Werbenetze, bis die Listen geladen sind.
pub const EINGEBAUT_WERBUNG: &str = "\
||doubleclick.net^
||googlesyndication.com^
||googleadservices.com^
||adservice.google.com^
||amazon-adsystem.com^
||adnxs.com^
||criteo.com^
||criteo.net^
||taboola.com^
||outbrain.com^
||rubiconproject.com^
||pubmatic.com^
||openx.net^
||casalemedia.com^
||smartadserver.com^
||media.net^
||yieldmo.com^
||moatads.com^
||adroll.com^
||bidswitch.net^
||popads.net^
||propellerads.com^
||adsterra.com^
##.adsbygoogle
##[data-ad-client]
";

pub const EINGEBAUT_TRACKER: &str = "\
||google-analytics.com^
||googletagmanager.com^
||connect.facebook.net^$third-party
||facebook.com/tr^
||analytics.twitter.com^
||analytics.tiktok.com^
||scorecardresearch.com^
||hotjar.com^
||clarity.ms^
||mouseflow.com^
||fullstory.com^
||quantserve.com^
||mc.yandex.ru^
||mixpanel.com^
||segment.io^
||amplitude.com^
||chartbeat.com^
||statcounter.com^
";

pub fn engine_aus(texte: &[&str]) -> Engine {
    let mut satz = FilterSet::new(false);
    for text in texte {
        satz.add_filter_list(text.to_string(), ParseOptions::default());
    }
    Engine::new_with_filter_set(satz)
}

pub(super) fn ordner(app: &AppHandle) -> Option<PathBuf> {
    let o = app.path().app_local_data_dir().ok()?.join("filterlisten");
    std::fs::create_dir_all(&o).ok()?;
    Some(o)
}

fn datei(app: &AppHandle, liste: &Liste) -> Option<PathBuf> {
    Some(ordner(app)?.join(format!("{}.txt", liste.name)))
}

pub(super) fn alter(pfad: &PathBuf) -> Option<Duration> {
    let geaendert = std::fs::metadata(pfad).ok()?.modified().ok()?;
    SystemTime::now().duration_since(geaendert).ok()
}

/// Sieht der Text aus wie eine Filterliste im Adblock-Format?
pub fn ist_filterliste(text: &str) -> bool {
    let kopf: String = text.chars().take(2000).collect();
    kopf.starts_with("[Adblock") || kopf.contains("! Title:")
}

fn engines_bauen(app: &AppHandle) {
    for art in [Treffer::Werbung, Treffer::Tracker] {
        let eingebaut = match art {
            Treffer::Werbung => EINGEBAUT_WERBUNG,
            Treffer::Tracker => EINGEBAUT_TRACKER,
        };
        let mut texte = vec![eingebaut.to_string()];
        for liste in LISTEN.iter().filter(|l| l.art == art) {
            if let Some(text) = datei(app, liste).and_then(|p| std::fs::read_to_string(p).ok()) {
                if ist_filterliste(&text) {
                    texte.push(text);
                }
            }
        }
        let refs: Vec<&str> = texte.iter().map(String::as_str).collect();
        engine_setzen(art, engine_aus(&refs));
    }
}

/// Holt eine Liste und nimmt sie nur, wenn `passt` sie als Liste erkennt.
pub(super) async fn holen(url: &str, passt: fn(&str) -> bool) -> Result<String, String> {
    let mut antwort = crate::netz::client(Duration::from_secs(60))?
        .get(url)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !antwort.status().is_success() {
        return Err(format!("HTTP {}", antwort.status()));
    }
    if antwort.content_length().is_some_and(|l| l as usize > HOECHSTENS_BYTES) {
        return Err("Liste zu groß".into());
    }
    // Stückweise mit Grenze: ohne `Content-Length` las `bytes()` sonst alles
    // in den Speicher, bevor die Grenze griff.
    let mut bytes = Vec::new();
    while let Some(teil) = antwort.chunk().await.map_err(|e| e.to_string())? {
        if bytes.len() + teil.len() > HOECHSTENS_BYTES {
            return Err("Liste zu groß".into());
        }
        bytes.extend_from_slice(&teil);
    }
    let text = String::from_utf8(bytes).map_err(|_| "Liste ist kein UTF-8".to_string())?;
    if !passt(&text) {
        return Err("Antwort ist keine Filterliste".into());
    }
    Ok(text)
}

/// Lädt beim Start die gespeicherten Listen und erneuert veraltete.
pub fn starten(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let a = app.clone();
        let _ = tauri::async_runtime::spawn_blocking(move || engines_bauen(&a)).await;

        let mut erneuert = false;
        for liste in LISTEN.iter() {
            let Some(pfad) = datei(&app, liste) else { continue };
            if alter(&pfad).is_some_and(|a| a < ERNEUERN_NACH) {
                continue;
            }
            match holen(liste.url, ist_filterliste).await {
                Ok(text) => {
                    if crate::datei::ersetzen(&pfad, text).is_ok() {
                        erneuert = true;
                    }
                }
                Err(fehler) => eprintln!("[MSB] Filterliste {} nicht erneuert: {fehler}", liste.name),
            }
        }
        if erneuert {
            let a = app.clone();
            let _ = tauri::async_runtime::spawn_blocking(move || engines_bauen(&a)).await;
        }
    });
}

#[derive(Serialize)]
pub struct ListenStand {
    pub name: &'static str,
    /// Sekunden seit dem letzten Abruf, `None` = noch nie geladen.
    pub alter_sekunden: Option<u64>,
}

pub fn stand(app: &AppHandle) -> Vec<ListenStand> {
    LISTEN
        .iter()
        .map(|l| ListenStand {
            name: l.name,
            alter_sekunden: datei(app, l).and_then(|p| alter(&p)).map(|a| a.as_secs()),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn erkennt_filterlisten() {
        assert!(ist_filterliste("[Adblock Plus 2.0]\n! Title: EasyList\n||x^"));
        assert!(ist_filterliste("! Title: EasyPrivacy\n"));
        assert!(!ist_filterliste("<!doctype html><html>"));
    }

    #[test]
    fn eingebaute_listen_greifen() {
        let w = engine_aus(&[EINGEBAUT_WERBUNG]);
        let r = adblock::request::Request::new(
            "https://securepubads.g.doubleclick.net/tag/js/gpt.js",
            "https://news.example/",
            "script",
            "GET",
        )
        .unwrap();
        assert!(w.check_network_request(&r).should_block());
        let t = engine_aus(&[EINGEBAUT_TRACKER]);
        let r = adblock::request::Request::new(
            "https://www.google-analytics.com/g/collect",
            "https://news.example/",
            "ping",
            "GET",
        )
        .unwrap();
        assert!(t.check_network_request(&r).should_block());
    }
}
