use std::sync::atomic::{AtomicUsize, Ordering};
use serde::Serialize;
use tauri::command;

static GEBLOCKTE_ANZEIGEN: AtomicUsize = AtomicUsize::new(0);
static GEBLOCKTE_TRACKER: AtomicUsize = AtomicUsize::new(0);

const TRACKER_LISTE: &[&str] = &[
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net/tr",
    "connect.facebook.net",
    "analytics.twitter.com",
    "scorecardresearch.com",
    "criteo.com",
    "hotjar.com",
    "clarity.ms",
    "outbrain.com",
    "taboola.com",
    "adnxs.com",
    "rubiconproject.com",
    "amazon-adsystem.com",
    "adservice.google.",
    "/telemetry",
    "/pixel.gif",
];

#[derive(Debug, Clone, Serialize)]
pub struct AdblockStatistik {
    pub geblockte_anzeigen: usize,
    pub geblockte_tracker: usize,
    pub gesamt_geblockt: usize,
    pub adblock_aktiv: bool,
}

#[command]
pub fn adblock_status() -> AdblockStatistik {
    let ads = GEBLOCKTE_ANZEIGEN.load(Ordering::Relaxed);
    let trackers = GEBLOCKTE_TRACKER.load(Ordering::Relaxed);
    AdblockStatistik {
        geblockte_anzeigen: ads,
        geblockte_tracker: trackers,
        gesamt_geblockt: ads + trackers,
        adblock_aktiv: true,
    }
}

#[command]
pub fn adblock_zaehler_erhoehen(kategorie: String) -> AdblockStatistik {
    if kategorie == "tracker" {
        GEBLOCKTE_TRACKER.fetch_add(1, Ordering::Relaxed);
    } else {
        GEBLOCKTE_ANZEIGEN.fetch_add(1, Ordering::Relaxed);
    }
    adblock_status()
}

pub fn ist_werbung_oder_tracker(url: &str) -> bool {
    let lower = url.to_lowercase();
    for muster in TRACKER_LISTE {
        if lower.contains(muster) {
            return true;
        }
    }
    false
}

/// Liefert das Content-Script für kosmetisches Adblocking (Verstecken gängiger Werbeelemente)
pub fn kosmetisches_adblock_script() -> &'static str {
    r#"
    (function() {
      if (window.__MSB_ADBLOCK_INJECTED__) return;
      window.__MSB_ADBLOCK_INJECTED__ = true;

      const style = document.createElement('style');
      style.textContent = `
        [id*="google_ads"], [class*="adsbygoogle"], [id*="banner-ad"],
        [class*="banner-ad"], [class*="sponsored-post"], [data-ad-client],
        [id*="taboola-"], [id*="outbrain-"], .ad-container, .ads-wrapper {
          display: none !important;
          visibility: hidden !important;
          height: 0 !important;
          width: 0 !important;
          opacity: 0 !important;
          pointer-events: none !important;
        }
      `;
      (document.head || document.documentElement).appendChild(style);
    })();
    "#
}
