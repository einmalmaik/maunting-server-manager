//! Das Skript, das in jedem Rahmen jeder Seite läuft, vor den Skripten der Seite.
//!
//! Es nimmt der Seite `chrome.webview` weg und behält die Brücke für sich.
//! Im obersten Rahmen meldet es Anmeldefelder, abgeschickte Anmeldungen und
//! den Rahmen mit Fokus; in einem Unterrahmen nur Zahlungsfelder. Es füllt,
//! wenn der Browser es nach einem Klick anweist (`tabs::formular`), nie von selbst.
//!
//! Was es meldet, ist eine Angabe der Seite: die Seite kann dieselben
//! Felder bauen und dasselbe abschicken. Die Adresse nimmt Rust deshalb aus
//! der Webview, und gefüllt wird nur auf derselben Herkunft.

//!
//! Das Cookie-Skript meldet eine Ablehnung über ein Ereignis, dessen Name je
//! Sitzung zufällig ist ([`ZEICHEN`]); `seite.js` gibt sie an den Browser
//! weiter. Ohne den Namen kann die Seite keine Ablehnung vortäuschen, und mit
//! ihm höchstens eine je Seitenaufruf (`schild::cookies_abgelehnt`).

use std::collections::hash_map::RandomState;
use std::hash::BuildHasher;
use std::sync::LazyLock;

/// Zufälliger Ereignisname dieser Sitzung, 128 Bit.
pub static ZEICHEN: LazyLock<String> = LazyLock::new(|| {
    let zufall = RandomState::new();
    format!("msb{:016x}{:016x}", zufall.hash_one(1u8), zufall.hash_one(2u8))
});

const PLATZHALTER: &str = "__MSB_ZEICHEN__";

pub static SKRIPT: LazyLock<String> = LazyLock::new(|| include_str!("seite.js").replace(PLATZHALTER, &ZEICHEN));

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn der_ereignisname_steht_eingesetzt_und_ist_zufaellig() {
        assert_eq!(include_str!("seite.js").matches(PLATZHALTER).count(), 1);
        assert!(SKRIPT.contains(&format!("'{}'", *ZEICHEN)) && !SKRIPT.contains(PLATZHALTER));
        assert!(ZEICHEN.len() == 35 && ZEICHEN[3..].bytes().all(|b| b.is_ascii_hexdigit()));
        let anders = RandomState::new();
        assert_ne!(*ZEICHEN, format!("msb{:016x}{:016x}", anders.hash_one(1u8), anders.hash_one(2u8)));
    }
}
