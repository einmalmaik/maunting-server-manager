//! Das Skript, das in jedem Rahmen jeder Seite läuft, vor den Skripten der Seite.
//!
//! Es nimmt der Seite `chrome.webview` weg und behält die Brücke für sich.
//! Im obersten Rahmen meldet es Anmeldefelder und abgeschickte Anmeldungen
//! und füllt, wenn der Browser es nach einem Klick anweist
//! (`tabs::formular`). Es füllt nie von selbst.
//!
//! Was es meldet, ist eine Angabe der Seite: die Seite kann dieselben
//! Felder bauen und dasselbe abschicken. Die Adresse nimmt Rust deshalb aus
//! der Webview, und gefüllt wird nur auf derselben Herkunft.

pub const SKRIPT: &str = include_str!("seite.js");
