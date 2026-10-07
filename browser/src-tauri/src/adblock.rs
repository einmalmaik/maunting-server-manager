use std::sync::atomic::{AtomicUsize, Ordering};
use serde::Serialize;
use tauri::command;

static GEBLOCKTE_ANZEIGEN: AtomicUsize = AtomicUsize::new(0);
static GEBLOCKTE_TRACKER: AtomicUsize = AtomicUsize::new(0);

const WERBE_LISTE: &[&str] = &[
    "doubleclick.net",
    "adservice.google.",
    "googleads.",
    "googlesyndication.com",
    "criteo.com",
    "criteo.net",
    "outbrain.com",
    "taboola.com",
    "adnxs.com",
    "rubiconproject.com",
    "amazon-adsystem.com",
    "advertising.amazon.",
    "media.net",
    "openx.net",
    "pubmatic.com",
    "casalemedia.com",
    "smartadserver.com",
    "popads.net",
    "propellerads.com",
    "adsterra.com",
    "infolinks.com",
    "buysellads.com",
    "chitika.com",
    "revcontent.com",
    "yieldmo.com",
    "moatads.com",
    "adroll.com",
    "bidswitch.net",
    "serving-sys.com",
    "adtech.de",
    "adcolony.com",
    "applovin.com",
    "unityads.unity3d.com",
    "adsystem",
    "/ads.js",
    "/ads?",
    "/pagead/",
    "/adserve",
];

const TRACKER_LISTE: &[&str] = &[
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.net/tr",
    "connect.facebook.net",
    "analytics.twitter.com",
    "analytics.tiktok.com",
    "scorecardresearch.com",
    "hotjar.com",
    "clarity.ms",
    "mouseflow.com",
    "fullstory.com",
    "logrocket.com",
    "luckyorange.com",
    "crazyegg.com",
    "quantserve.com",
    "quantcount.com",
    "yandex.ru/metrika",
    "mc.yandex.ru",
    "branch.io",
    "appsflyer.com",
    "mixpanel.com",
    "segment.io",
    "segment.com",
    "amplitude.com",
    "adjust.com",
    "chartbeat.com",
    "statcounter.com",
    "/telemetry",
    "/pixel.gif",
    "/tracking",
    "/collector",
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

pub enum BlockArt {
    Werbung,
    Tracker,
    Erlaubt,
}

pub fn pruefe_url_block(url: &str) -> BlockArt {
    let lower = url.to_lowercase();
    for muster in WERBE_LISTE {
        if lower.contains(muster) {
            return BlockArt::Werbung;
        }
    }
    for muster in TRACKER_LISTE {
        if lower.contains(muster) {
            return BlockArt::Tracker;
        }
    }
    BlockArt::Erlaubt
}

pub fn ist_werbung_oder_tracker(url: &str) -> bool {
    matches!(pruefe_url_block(url), BlockArt::Werbung | BlockArt::Tracker)
}

/// Liefert das Content-Script für kosmetisches Adblocking, In-Flight Script/XHR Blocking und Tracker-Stripping
pub fn kosmetisches_adblock_script() -> &'static str {
    r#"
    (function() {
      if (window.__MSB_SHIELD_ACTIVE__) return;
      window.__MSB_SHIELD_ACTIVE__ = true;

      // 1. Kosmetisches CSS-Hiding gängiger Werbeelemente
      const cssRules = `
        [id*="google_ads"], [class*="adsbygoogle"], [id*="banner-ad"],
        [class*="banner-ad"], [class*="sponsored-post"], [data-ad-client],
        [id*="taboola-"], [id*="outbrain-"], .ad-container, .ads-wrapper,
        .ad_unit, .ad-box, .commercial-unit, [data-google-query-id],
        aside[class*="banner"], div[id*="crt-banner"], .cookie-banner,
        .didomi-popup, #onetrust-consent-sdk, .cc-banner {
          display: none !important;
          visibility: hidden !important;
          height: 0 !important;
          width: 0 !important;
          opacity: 0 !important;
          pointer-events: none !important;
          overflow: hidden !important;
        }
      `;
      function injectShieldStyles() {
        if (!document.head && !document.documentElement) return;
        const style = document.createElement('style');
        style.id = 'msb-adblock-shield';
        style.textContent = cssRules;
        (document.head || document.documentElement).appendChild(style);
      }
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectShieldStyles);
      } else {
        injectShieldStyles();
      }

      // 2. Bekannte Werbe- und Tracking-Domains für In-Flight-Checks
      const blockHostPatterns = [
        'doubleclick.net', 'google-analytics.com', 'googletagmanager.com',
        'criteo.com', 'outbrain.com', 'taboola.com', 'adnxs.com',
        'scorecardresearch.com', 'hotjar.com', 'clarity.ms', 'amazon-adsystem.com',
        'facebook.net/tr', 'analytics.twitter.com', 'pagead2.googlesyndication.com'
      ];

      function shouldBlock(url) {
        if (!url || typeof url !== 'string') return false;
        const low = url.toLowerCase();
        for (let i = 0; i < blockHostPatterns.length; i++) {
          if (low.includes(blockHostPatterns[i])) return true;
        }
        return false;
      }

      // 3. Network Interception (Fetch & XMLHttpRequest)
      const origFetch = window.fetch;
      if (origFetch) {
        window.fetch = function(...args) {
          const url = (typeof args[0] === 'string') ? args[0] : (args[0] && args[0].url ? args[0].url : '');
          if (shouldBlock(url)) {
            return Promise.reject(new Error('MSB Shield: Request blocked by privacy filter'));
          }
          return origFetch.apply(this, args);
        };
      }

      const origOpen = XMLHttpRequest.prototype.open;
      if (origOpen) {
        XMLHttpRequest.prototype.open = function(method, url, ...rest) {
          if (shouldBlock(url)) {
            this.abort();
            return;
          }
          return origOpen.call(this, method, url, ...rest);
        };
      }

      // 4. MutationObserver zum sofortigen Entfernen nachgeladener Werbe-Elemente
      const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
          for (const node of m.addedNodes) {
            if (node.nodeType === 1) { // ELEMENT_NODE
              const src = node.getAttribute && (node.getAttribute('src') || node.getAttribute('data-src'));
              if (src && shouldBlock(src)) {
                node.remove();
                continue;
              }
              const cls = node.className || '';
              const id = node.id || '';
              if (typeof cls === 'string' && (cls.includes('adsbygoogle') || cls.includes('ad-container'))) {
                node.remove();
              }
            }
          }
        }
      });

      if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true });
      } else {
        document.addEventListener('DOMContentLoaded', () => {
          if (document.body) {
            observer.observe(document.body, { childList: true, subtree: true });
          }
        });
      }

      // 5. Anti-Tracking: Bereinigung bekannter Tracking-Parameter aus Links
      const trackingParams = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'fbclid', 'gclid', 'msclkid', 'twclid', '_ga'];
      document.addEventListener('click', (e) => {
        const a = e.target && e.target.closest ? e.target.closest('a') : null;
        if (!a || !a.href) return;
        try {
          const u = new URL(a.href);
          let changed = false;
          for (const p of trackingParams) {
            if (u.searchParams.has(p)) {
              u.searchParams.delete(p);
              changed = true;
            }
          }
          if (changed) {
            a.href = u.toString();
          }
        } catch (_) {}
      }, true);
    })();
    "#
}
