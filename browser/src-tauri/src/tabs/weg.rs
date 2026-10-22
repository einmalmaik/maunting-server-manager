//! Wohin ein Tab darf: Kennungen, Navigation und jede Anfrage, die eine
//! Seite stellt. Rein und ohne Webview, damit Windows (`desktop/`) und
//! Android (`android/bruecke.rs`) dieselbe Entscheidung treffen.

/// Kennungen kommen aus der Oberfläche und werden zum Label einer Webview.
/// Erlaubt ist nur `tab-` mit Buchstaben, Ziffern und Strich: so kann eine
/// Kennung nie `main` heißen und nie die Rechte der Oberfläche erben
/// (`capabilities/oberflaeche.json` gilt nur für die Webview `main`).
pub fn id_pruefen(id: &str) -> Result<(), String> {
    let rest = id.strip_prefix("tab-").ok_or("Ungültige Tab-Kennung")?;
    if rest.is_empty() || rest.len() > 40 || !rest.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("Ungültige Tab-Kennung".into());
    }
    Ok(())
}

/// Hosts, unter denen Tauri die eigene Oberfläche und ihre IPC ausliefert.
/// Ein Tab darf sie nie laden: dort liefe fremder Inhalt mit dem Ursprung der
/// Oberfläche.
const APP_HOSTS: [&str; 3] = ["tauri.localhost", "ipc.localhost", "asset.localhost"];

/// Kennungen der Erweiterungen, die gerade laufen (nur Windows,
/// `crate::erweiterungen`). Ihre Seiten (Optionen) darf ein Tab öffnen.
static ERWEITERUNGEN: std::sync::RwLock<Vec<String>> = std::sync::RwLock::new(Vec::new());

pub fn erweiterungen_setzen(laufen: Vec<String>) {
    *ERWEITERUNGEN.write().unwrap() = laufen;
}

/// Darf ein Tab diese Adresse als Seite öffnen?
pub fn navigation_erlaubt(url: &url::Url) -> bool {
    match url.scheme() {
        "http" | "https" => !url.host_str().is_some_and(|h| APP_HOSTS.contains(&h.trim_end_matches('.'))),
        // Leere Seite, Blob- und Data-Adressen, die eine Seite selbst erzeugt.
        "about" | "blob" | "data" => true,
        // Nie die einer Erweiterung, die aus ist oder fehlt: sonst öffnete ein
        // Link eine abgeschaltete Erweiterung, auch bei aktivem Jugendschutz.
        "chrome-extension" => url.host_str().is_some_and(|h| ERWEITERUNGEN.read().unwrap().iter().any(|e| e == h)),
        _ => false,
    }
}

/// Schemes, die ein Tab nie lädt und die trotzdem nie ans System gehen: die
/// eigenen und die von Tauri, Quelltext statt Ziele, lokale Dateien (der
/// Browser startet keine Programme von außen) sowie bekannte Angriffswege
/// unter Windows (Follina über `ms-msdt`, Suchfenster-Phishing über `search-ms`).
const NIE_EXTERN: &[&str] = &[
    "http", "https", "about", "blob", "data", "chrome-extension", "msb", "pontis", "tauri", "ipc", "asset",
    "javascript", "vbscript", "view-source", "file", "intent", "ms-msdt", "search-ms", "ms-search",
];

/// Ein Fremdprotokoll (OAuth-Rücksprung an eine Desktop-App wie `vscode://`,
/// `discord://`, `x-github-client://`): die normalisierte Adresse mit allem,
/// was der Token-Tausch braucht (`code`, `state`). `None`, wenn ein Tab die
/// Adresse selbst lädt, sie verboten ist oder nie ans System darf.
/// Dieselbe Entscheidung für WebView2 und Android.
pub fn externes_protokoll(url: &str) -> Option<String> {
    let geparst = url::Url::parse(url.trim()).ok()?;
    if NIE_EXTERN.contains(&geparst.scheme()) {
        return None;
    }
    Some(geparst.to_string())
}

/// Gibt eine Fremdadresse ans System: die zugehörige App öffnet sich
/// (OAuth-Rücksprung nach Anmeldung im Browser). Der Tab bleibt stehen, weil
/// seine Navigation abgebrochen wurde. Ohne registrierte App scheitert das:
/// dann steht der Fehler im Protokoll, ohne Abfrage (sie trägt das Geheimnis).
pub fn extern_oeffnen(app: &tauri::AppHandle, url: &str) {
    use tauri_plugin_opener::OpenerExt;
    let gekuerzt = url.split(['?', '#']).next().unwrap_or(url);
    if let Err(fehler) = app.opener().open_url(url, None::<&str>) {
        eprintln!("[MSB] Externe Adresse nicht geöffnet ({gekuerzt}): {fehler}");
    }
}

/// Was mit einer Navigation im Tab geschieht. Dieselbe Entscheidung für
/// WebView2 (`desktop/grundereignisse.rs`) und Android (`android/bruecke.rs`),
/// auch für Weiterleitungen, nicht nur für die erste Adresse.
#[derive(Debug, PartialEq)]
pub enum Weg {
    Laden,
    /// Keine Seite, die ein Tab öffnen darf (`navigation_erlaubt`).
    Verboten,
    /// Der Jugend- und Suchtschutz sperrt sie; die Kategorie oder `eigene`.
    Gesperrt(&'static str),
    /// Stattdessen diese Adresse: verschlüsselt (`https.rs`), ohne
    /// Tracking-Parameter (`schild/verfolgung.rs`) oder mit erzwungener
    /// sicherer Suche (`schild/sperre.rs`).
    Umleiten(String),
    /// Fremdprotokoll (OAuth-Rücksprung an eine Desktop-App): die Navigation
    /// wird abgebrochen, die Adresse geht ans System (`extern_oeffnen`).
    Extern(String),
}

/// `weiterleitung`: der Server hat hierher umgeleitet (`https::hochstufen`).
pub fn weg(url: &str, weiterleitung: bool) -> Weg {
    // Zuerst: ein Fremdprotokoll lädt nie, auch nicht verschlüsselt oder ohne
    // Tracking-Parameter — es geht im Ganzen ans System.
    if let Some(fremd) = externes_protokoll(url) {
        return Weg::Extern(fremd);
    }
    let Some(geparst) = url::Url::parse(url).ok().filter(navigation_erlaubt) else {
        return Weg::Verboten;
    };
    let mut ziel = super::https::hochstufen(&geparst, weiterleitung).unwrap_or_else(|| geparst.clone());
    if crate::schild::aktiv_fuer(ziel.as_str()) {
        if let Some(sauber) = crate::schild::verfolgung::ohne_verfolgung(&ziel) {
            ziel = sauber;
        }
    }
    if let Some(grund) = crate::schild::sperre::gesperrt(ziel.as_str()) {
        return Weg::Gesperrt(grund.name());
    }
    match crate::schild::sperre::sichere_suche(ziel.as_str()) {
        Some(sicher) => Weg::Umleiten(sicher),
        None if ziel != geparst => Weg::Umleiten(ziel.into()),
        None => Weg::Laden,
    }
}

/// Adressen, die die Oberfläche zum Öffnen schickt: Webseiten und die
/// Seiten laufender Erweiterungen (Optionen).
pub fn ziel_pruefen(url: &str) -> Result<url::Url, String> {
    let geparst = url::Url::parse(url).map_err(|_| "Ungültige Adresse".to_string())?;
    if !matches!(geparst.scheme(), "http" | "https" | "chrome-extension") || !navigation_erlaubt(&geparst) {
        return Err("Diese Adresse kann der Browser nicht öffnen.".into());
    }
    Ok(geparst)
}

/// Was mit einer Anfrage aus einem Tab geschieht, bevor das Schild zählt.
#[derive(Debug, PartialEq)]
pub enum Vorab {
    /// 403. `Some` nennt den Grund der Sperre; ist es das Hauptdokument,
    /// meldet der Tab `gesperrt`.
    Blocken(Option<&'static str>),
    /// Das Hauptdokument selbst: das Schild blockt keine Seite, die der
    /// Nutzer öffnet. `youtube`: mit `YouTube-Restrict: Strict` (Windows).
    Laden { youtube: bool },
    /// Was die Seite nachlädt: das Schild entscheidet.
    Schild { youtube: bool },
}

/// Eine Entscheidung für jede Anfrage eines Tabs, auf beiden Plattformen.
/// Bis 09.10.2026 baute jede Plattform ihre eigene, und unter Android
/// prüfte ein Formular per POST im Hauptrahmen nur die Sperre, nicht, ob
/// ein Tab die Adresse überhaupt laden darf.
pub fn vorab(url: &str, hauptdokument: bool) -> Vorab {
    let geparst = url::Url::parse(url).ok();
    let app_host = geparst
        .as_ref()
        .and_then(|u| u.host_str())
        .is_some_and(|h| APP_HOSTS.contains(&h.trim_end_matches('.')));
    let darf = geparst.as_ref().is_some_and(navigation_erlaubt);
    if app_host || (hauptdokument && !darf) {
        return Vorab::Blocken(None);
    }
    if let Some(grund) = crate::schild::sperre::gesperrt(url) {
        return Vorab::Blocken(Some(grund.name()));
    }
    let youtube = crate::schild::sperre::youtube_einschraenken(url);
    if hauptdokument {
        Vorab::Laden { youtube }
    } else {
        Vorab::Schild { youtube }
    }
}

/// Ein Rahmen, dessen Dokument auf einer gesperrten Herkunft steht. Unter
/// Android sehen `shouldOverrideUrlLoading` und `shouldInterceptRequest` die
/// Weiterleitung eines Rahmens nicht: ein iframe auf einen Kurzlink lud die
/// gesperrte Seite dahinter. `seite.js` meldet sich aber aus jedem Rahmen,
/// und `sourceOrigin` nennt die Herkunft, auf der er wirklich steht.
#[cfg(any(target_os = "android", test))]
pub fn rahmen_gesperrt(herkunft: &str) -> bool {
    matches!(vorab(herkunft, false), Vorab::Blocken(_))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn eine_anfrage_entscheidet_auf_beiden_plattformen_gleich() {
        assert_eq!(vorab("https://example.com/", true), Vorab::Laden { youtube: false });
        assert_eq!(vorab("https://example.com/bild.png", false), Vorab::Schild { youtube: false });
        // Ein Formular per POST an die Oberfläche: unter Android bis 09.10.2026 durchgelassen.
        assert_eq!(vorab("http://tauri.localhost/browser.html", true), Vorab::Blocken(None));
        assert_eq!(vorab("https://IPC.localhost./konfig_laden", false), Vorab::Blocken(None));
        assert_eq!(vorab("file:///C:/Windows/win.ini", true), Vorab::Blocken(None));
        // Nachgeladenes darf andere Schemata haben (WebSocket), die Seite selbst nicht.
        assert_eq!(vorab("wss://example.com/live", false), Vorab::Schild { youtube: false });
        assert_eq!(vorab("wss://example.com/live", true), Vorab::Blocken(None));
    }

    #[test]
    fn ein_rahmen_auf_einer_gesperrten_herkunft_faellt() {
        // Die Sperre ist global; kein anderer Test fragt nach diesem Host.
        use crate::schild::sperre::{setzen, Sperre};
        setzen(Sperre { eigene: ["gesperrt.example".to_string()].into_iter().collect(), ..Sperre::default() });
        let ergebnis = [
            rahmen_gesperrt("https://gesperrt.example"),
            rahmen_gesperrt("https://www.gesperrt.example:8443"),
            rahmen_gesperrt("https://example.com"),
            // Ein Rahmen ohne Herkunft (sandbox) hat nichts, woran er fallen könnte.
            rahmen_gesperrt("null"),
            rahmen_gesperrt("http://tauri.localhost"),
        ];
        setzen(Sperre::default());
        assert_eq!(ergebnis, [true, true, false, false, true]);
    }

    #[test]
    fn nur_laufende_erweiterungen_haben_seiten() {
        const AN: &str = "ddkjiahejlhfcafbddmgiahcphecmpfh";
        erweiterungen_setzen(vec![AN.into()]);
        assert!(ziel_pruefen(&format!("chrome-extension://{AN}/options.html")).is_ok());
        assert_eq!(weg(&format!("chrome-extension://{AN}/options.html"), false), Weg::Laden);
        let fremd = "chrome-extension://eimadpbcbfnmbkopoojfekhnkhdbieeh/options.html";
        assert!(ziel_pruefen(fremd).is_err());
        assert_eq!(weg(fremd, false), Weg::Verboten);
        assert_eq!(vorab(fremd, true), Vorab::Blocken(None));
        erweiterungen_setzen(vec![]);
        assert!(ziel_pruefen(&format!("chrome-extension://{AN}/options.html")).is_err());
    }

    #[test]
    fn kennung_kann_nie_die_oberflaeche_sein() {
        assert!(id_pruefen("tab-a1b2").is_ok());
        assert!(id_pruefen("main").is_err());
        assert!(id_pruefen("tab-").is_err());
        assert!(id_pruefen("tab-../main").is_err());
        assert!(id_pruefen(&format!("tab-{}", "a".repeat(41))).is_err());
    }

    #[test]
    fn tabs_laden_nie_die_oberflaeche() {
        let u = |s: &str| url::Url::parse(s).unwrap();
        assert!(navigation_erlaubt(&u("https://example.com/")));
        assert!(navigation_erlaubt(&u("about:blank")));
        assert!(!navigation_erlaubt(&u("http://tauri.localhost/browser.html")));
        assert!(!navigation_erlaubt(&u("https://ipc.localhost/konfig_laden")));
        assert!(!navigation_erlaubt(&u("tauri://localhost/")));
        assert!(!navigation_erlaubt(&u("file:///C:/Windows/win.ini")));
        assert!(!navigation_erlaubt(&u("javascript:alert(1)")));
    }

    #[test]
    fn navigation_entscheidet_ohne_webview() {
        assert_eq!(weg("https://example.com/", false), Weg::Laden);
        assert_eq!(weg("http://tauri.localhost/browser.html", false), Weg::Verboten);
        assert_eq!(weg("file:///C:/Windows/win.ini", false), Weg::Verboten);
        assert_eq!(weg("intent://scan/#Intent;scheme=zxing;end", false), Weg::Verboten);
        assert_eq!(weg("kaputt", false), Weg::Verboten);
        assert_eq!(weg("http://localhost:5173/", false), Weg::Laden);
    }

    #[test]
    fn eine_navigation_laedt_verschluesselt_und_ohne_tracking_parameter() {
        assert_eq!(
            weg("http://weg.example.com/a?utm_source=mail&id=3", false),
            Weg::Umleiten("https://weg.example.com/a?id=3".into())
        );
        assert_eq!(weg("https://weg2.example.com/?fbclid=x", false), Weg::Umleiten("https://weg2.example.com/".into()));
        // Was schon sauber ist, lädt; sonst kreiste die Umleitung.
        assert_eq!(weg("https://weg2.example.com/", false), Weg::Laden);
        // Der Entwicklungsserver bleibt bei http; Tracking-Parameter fallen auch dort.
        assert_eq!(weg("http://localhost:3000/?utm_source=x", false), Weg::Umleiten("http://localhost:3000/".into()));
    }

    #[test]
    fn oauth_rueckspruenge_gehen_ans_system_statt_ins_leere() {
        // GitHub Desktop, VS Code, Discord: der IdP leitet nach der Anmeldung auf ein
        // Fremdprotokoll um. Die Abfrage (state, code) muss unbeschädigt mitkommen.
        for ziel in [
            "x-github-client://oauth?code=abc123&state=xyz",
            "vscode://vscode.github-authentication/did-authenticate?code=abc123&state=xyz",
            "discord://callback?code=abc123&state=xyz",
        ] {
            let fremd = externes_protokoll(ziel);
            assert!(fremd.is_some(), "{ziel}");
            // Die Abfrage bleibt erhalten: ohne sie ist der Token-Tausch tot.
            assert!(fremd.unwrap().contains("code=abc123"), "{ziel}");
            assert!(matches!(weg(ziel, false), Weg::Extern(_)), "{ziel}");
        }
        // Ohne Abfrage, aber ebenfalls extern: E-Mail und Telefon.
        for ziel in ["mailto:hilfe@example.org", "tel:+4930123456"] {
            assert!(externes_protokoll(ziel).is_some(), "{ziel}");
            assert!(matches!(weg(ziel, false), Weg::Extern(_)), "{ziel}");
        }
        // Großschreibung ändert nichts am Scheme.
        assert!(externes_protokoll("VSCode://a/b?code=1").is_some());
    }

    #[test]
    fn gefaehrliche_und_eigene_schemata_gehen_nie_ans_system() {
        for behalten in [
            "https://example.org/",
            "http://127.0.0.1:5173/callback?code=1",
            "about:blank",
            "data:text/html,hallo",
            "blob:https://example.org/x",
            "chrome-extension://ddkjiahejlhfcafbddmgiahcphecmpfh/popup.html",
            "tauri://localhost/",
            "msb://einstellungen",
            "pontis://einstellungen",
            "file:///C:/Windows/win.ini",
            "javascript:alert(1)",
            "vbscript:msgbox(1)",
            "view-source:https://example.org/",
            "intent://scan/#Intent;scheme=zxing;end",
            // Follina und Suchfenster-Phishing: nie starten.
            "ms-msdt:/id%20PCWDiagnostic",
            "search-ms:query=Rechnung",
            "ms-search:query=x",
            "kaputt",
            "",
        ] {
            assert_eq!(externes_protokoll(behalten), None, "{behalten}");
        }
        // Was kein Fremdprotokoll ist, entscheidet wie bisher.
        assert_eq!(weg("intent://scan/#Intent;scheme=zxing;end", false), Weg::Verboten);
        assert_eq!(weg("http://127.0.0.1:5173/callback?code=1", false), Weg::Laden);
    }

    #[test]
    fn die_oberflaeche_oeffnet_nur_webseiten() {
        assert!(ziel_pruefen("https://www.google.com/search?q=x").is_ok());
        assert!(ziel_pruefen("about:blank").is_err());
        assert!(ziel_pruefen("data:text/html,hallo").is_err());
        assert!(ziel_pruefen("http://tauri.localhost/").is_err());
        // Mit Punkt am Ende derselbe Host; bis 09.10.2026 galt er als fremd.
        assert!(ziel_pruefen("https://ipc.localhost./konfig_laden").is_err());
        assert!(!navigation_erlaubt(&url::Url::parse("http://TAURI.localhost./browser.html").unwrap()));
    }
}
