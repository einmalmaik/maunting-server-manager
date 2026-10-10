//! Der eine Weg, auf dem Rust selbst ins Netz geht (Filter- und Sperrlisten,
//! Netzzeit des Jugendschutzes, Symbole der Kacheln). Alles andere lädt die
//! WebView.
//!
//! Unter Android prüft rustls gegen die Wurzeln von Mozilla (`webpki-roots`),
//! wie im Smart System: der Plattform-Prüfer von reqwest braucht dort eine
//! Initialisierung über JNI und warf ohne sie bei jeder Anfrage. Die Listen
//! kamen so unter Android nie an (bis 09.10.2026).

use std::time::Duration;

pub fn client(frist: Duration) -> Result<reqwest::Client, String> {
    bauen(reqwest::Client::builder().timeout(frist))
}

/// Für Adressen, die eine Seite nennt (`kachelsymbol`): ohne Cookies, und
/// Weiterleitungen folgt der Aufrufer selbst, damit er jede prüfen kann.
/// Mit Namen, denn manche Seiten (Wikimedia) weisen Anfragen ohne ab.
pub fn client_ohne_weiterleitung(frist: Duration) -> Result<reqwest::Client, String> {
    bauen(
        reqwest::Client::builder()
            .timeout(frist)
            .redirect(reqwest::redirect::Policy::none())
            .user_agent(concat!("Mozilla/5.0 (compatible; MauntingSecureBrowser/", env!("CARGO_PKG_VERSION"), ")")),
    )
}

fn bauen(builder: reqwest::ClientBuilder) -> Result<reqwest::Client, String> {
    #[cfg(target_os = "android")]
    let builder = {
        let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
        let mut wurzeln = rustls::RootCertStore::empty();
        wurzeln.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
        builder.use_preconfigured_tls(rustls::ClientConfig::builder().with_root_certificates(wurzeln).with_no_client_auth())
    };
    builder.build().map_err(|e| e.to_string())
}
