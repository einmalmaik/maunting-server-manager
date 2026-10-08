//! Der eine Weg, auf dem Rust selbst ins Netz geht (Filter- und Sperrlisten,
//! Netzzeit des Jugendschutzes). Alles andere lädt die WebView.
//!
//! Unter Android prüft rustls gegen die Wurzeln von Mozilla (`webpki-roots`),
//! wie im Smart System: der Plattform-Prüfer von reqwest braucht dort eine
//! Initialisierung über JNI und warf ohne sie bei jeder Anfrage. Die Listen
//! kamen so unter Android nie an (bis 09.10.2026).

use std::time::Duration;

pub fn client(frist: Duration) -> Result<reqwest::Client, String> {
    let builder = reqwest::Client::builder().timeout(frist);
    #[cfg(target_os = "android")]
    let builder = {
        let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
        let mut wurzeln = rustls::RootCertStore::empty();
        wurzeln.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
        builder.use_preconfigured_tls(rustls::ClientConfig::builder().with_root_certificates(wurzeln).with_no_client_auth())
    };
    builder.build().map_err(|e| e.to_string())
}
