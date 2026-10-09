//! Cookie-Hinweise ablehnen. autoconsent von DuckDuckGo (MPL-2.0) läuft in
//! jedem Rahmen jeder Seite, vor den Skripten der Seite, und klickt
//! „Ablehnen“, nie „Akzeptieren“ (`cookies/eingang.js`). Die Regeln liegen im
//! Browser; es geht keine Anfrage hinaus.
//!
//! autoconsent liegt unverändert in `cookies/autoconsent/`, geholt mit fester
//! Fassung und Prüfsumme von `browser/scripts/autoconsent-holen.mjs`.
//!
//! Es gehört zum Schild: aus heißt aus, eine pausierte Seite bleibt
//! unberührt. Ein Rahmen kann Rust nicht fragen, deshalb trägt das Skript
//! Schalter und Ausnahmen. Ändern sie sich, setzen die Tabs es neu
//! (`tabs::cookies_erneuern`); es gilt ab dem nächsten Dokument.

use std::sync::LazyLock;

const AUTOCONSENT: &str = include_str!("cookies/autoconsent/autoconsent.esm.js");
const REGELN: &str = include_str!("cookies/autoconsent/compact-rules.json");
const EINGANG: &str = include_str!("cookies/eingang.js");

/// Ohne den `export`-Block am Ende, damit es als Teil eines Skripts läuft:
/// alles bleibt in dessen Closure, die Seite sieht keinen Namen davon.
static RUMPF: LazyLock<&'static str> =
    LazyLock::new(|| AUTOCONSENT.rfind("\nexport {").map_or("", |ende| &AUTOCONSENT[..ende]));

/// Die Regeln als Text: `JSON.parse` liest das schneller als ein Objekt im
/// Quelltext, und nur, wenn das Schild für die Seite an ist.
static REGELN_TEXT: LazyLock<String> = LazyLock::new(|| serde_json::to_string(REGELN).unwrap_or_default());

/// Das Skript für einen Tab, mit dem heutigen Stand des Schilds.
pub fn skript() -> String {
    let (aktiv, ausnahmen) = crate::schild::stand_fuer_seiten();
    let schild = serde_json::json!({ "aktiv": aktiv, "ausnahmen": ausnahmen });
    format!(
        "(() => {{\n{rumpf}\nconst SCHILD = {schild};\nconst REGELN = {regeln};\n{EINGANG}\n}})();\n",
        rumpf = *RUMPF,
        regeln = *REGELN_TEXT,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn autoconsent_laeuft_als_teil_eines_skripts() {
        assert!(!RUMPF.is_empty(), "export-Block nicht gefunden: autoconsent hat sein Format geändert");
        let ende = &AUTOCONSENT[RUMPF.len()..];
        assert!(ende.contains("AutoConsent as default") && ende.contains("filterCompactRules"));
        assert!(!RUMPF.contains("\nimport ") && !RUMPF.contains("import.meta"));
        let skript = skript();
        assert!(skript.starts_with("(() => {") && skript.contains("const SCHILD = {\"aktiv\":"));
        assert_eq!(serde_json::from_str::<serde_json::Value>(REGELN).unwrap()["v"], 1);
    }

    /// Lizenz und Herkunft liegen neben dem Code; das Skript holt dieselbe Fassung.
    #[test]
    fn die_lizenz_liegt_bei_und_die_fassung_ist_fest() {
        let lizenz = include_str!("cookies/autoconsent/LICENSE");
        assert!(lizenz.contains("Mozilla Public License Version 2.0"));
        let holen = include_str!("../../scripts/autoconsent-holen.mjs");
        assert!(holen.contains("const VERSION = '") && holen.contains("const INTEGRITAET = 'sha512-"));
    }

    /// Weder Zustimmen noch Melden: `optIn` und die Heuristik ab `tier1`
    /// klicken „Akzeptieren“.
    #[test]
    fn lehnt_nur_ab() {
        assert!(EINGANG.contains("autoAction: 'optOut'") && EINGANG.contains("heuristicMode: 'reject'"));
        assert!(!EINGANG.contains("'optIn'") && !EINGANG.contains("'tier"));
        assert!(EINGANG.contains("new AutoConsent(() => Promise.resolve())"));
    }
}
