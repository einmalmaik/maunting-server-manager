//! Was ein Tab der Oberfläche meldet ([`TabEreignis`]) und was sie auf eine
//! Rückfrage antwortet ([`Antwort`]). Dieselben Typen für Windows und
//! Android: was Kotlin meldet, liest Rust in [`TabEreignis`] ein, bevor es
//! an die Oberfläche geht (`android/bruecke.rs`).

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

use super::formular;

/// Name des Ereignisses, über das Rust Neuigkeiten aus den Tabs meldet.
pub const EREIGNIS: &str = "msb:tab";

/// Was die Oberfläche aus einem Tab erfährt.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "art", rename_all = "snake_case")]
pub enum TabEreignis {
    /// Eine neue Seite beginnt zu laden.
    Laedt { id: String, url: String },
    /// Die Seite ist geladen.
    Geladen { id: String, url: String },
    /// Die angezeigte Adresse hat sich geändert (auch ohne Neuladen, etwa
    /// durch `history.pushState`), mit dem Stand von Zurück und Vor.
    Adresse { id: String, url: String, zurueck: bool, vor: bool },
    Titel { id: String, titel: String },
    Favicon { id: String, url: Option<String> },
    /// Die Seite will ein neues Fenster öffnen (Link mit `target=_blank`,
    /// `window.open`). Der Browser öffnet stattdessen einen Tab.
    NeuerTab { id: String, url: String },
    /// Ein Tastenkürzel, das im Tab gedrückt wurde und dem Browser gehört
    /// (Strg+T, Strg+L, …).
    Taste { id: String, taste: String },
    /// Ein Download; `nr` kennzeichnet ihn über alle Meldungen hinweg.
    Download {
        id: String,
        nr: u64,
        stand: DownloadStand,
        url: String,
        datei: Option<String>,
    },
    /// Zahl der in diesem Tab seit dem letzten Seitenwechsel geblockten
    /// Anfragen.
    Schild { id: String, werbung: u32, tracker: u32 },
    /// Der Prozess der Seite ist abgestürzt.
    Absturz { id: String },
    /// Ein Video oder eine Seite will den ganzen Bildschirm.
    Vollbild { id: String, an: bool },
    /// Ein Anmeldefeld der Seite (`formular.rs`). `url` ist die Adresse
    /// des Tabs, nicht eine Angabe der Seite. `rahmen`: das Zahlungsfeld
    /// liegt in einem Rahmen dieser Herkunft (`formular::Zahlrahmen`).
    Formular {
        id: String,
        url: String,
        meldung: formular::Meldung,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        rahmen: Option<String>,
    },
    /// Rechtsklick. `x`/`y` in Pixeln der Webview; die Einträge sind die
    /// Befehle der WebView2, die gerade gehen.
    Kontextmenue {
        id: String,
        nr: u64,
        x: i32,
        y: i32,
        eintraege: Vec<MenueEintrag>,
        link: Option<String>,
        bild: Option<String>,
        auswahl: Option<String>,
        bearbeitbar: bool,
    },
    /// `alert`, `confirm`, `prompt` oder `beforeunload`.
    Dialog { id: String, nr: u64, dialog: String, herkunft: String, text: String, vorgabe: String },
    /// Die Seite will ein Recht (Kamera, Standort, …).
    Recht { id: String, nr: u64, recht: String, herkunft: String },
    /// HTTP-Anmeldung (Basic/Digest).
    Anmeldung { id: String, nr: u64, herkunft: String, bereich: String },
    /// Die Seite ließ sich nicht laden.
    Fehlerseite { id: String, url: String, grund: String },
    /// Der Jugend- und Suchtschutz sperrt die Seite; `grund` ist die
    /// Kategorie oder `eigene`.
    Gesperrt { id: String, url: String, grund: String },
    /// Ziel des Links unter dem Zeiger, leer, wenn keiner.
    Status { id: String, text: String },
    /// Stand der Suche in der Seite (`aktuell` ab 1, 0 ohne aktiven Treffer).
    Treffer { id: String, aktuell: i32, anzahl: i32 },
    /// Die Seite spielt Ton oder hört damit auf; `stumm`, wenn der Tab
    /// stummgeschaltet ist.
    Ton { id: String, spielt: bool, stumm: bool },
    /// Der Tab schläft (`ruhe.rs`) oder ist wieder wach.
    Schlaf { id: String, schlaeft: bool },
    /// Die Webview des Tabs ist weg, um Speicher zu sparen. Die Adresse
    /// behält die Oberfläche; beim nächsten Zeigen lädt sie neu.
    Verworfen { id: String },
    /// Ein Ereignis des DevTools-Protokolls für die Entwicklerwerkzeuge,
    /// unverändert bis auf Kürzungen (`desktop/entwickler.rs`).
    Protokoll { id: String, methode: String, daten: serde_json::Value },
    /// Ein Stück Text der Seite zum Übersetzen, als Antwort auf eine Anfrage
    /// der Oberfläche (`uebersetzung.rs`). Leere `texte`: fertig.
    Texte { id: String, nr: u32, sprache: String, texte: Vec<String> },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MenueEintrag {
    pub befehl: i32,
    /// Name der WebView2 (`copy`, `paste`, `saveImageAs`, …).
    pub name: String,
}

/// Die Antwort der Oberfläche auf eine Rückfrage der Seite.
#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "art", rename_all = "snake_case")]
pub enum Antwort {
    /// `None`: die Oberfläche hat selbst gehandelt oder nichts gewählt.
    Menue { befehl: Option<i32> },
    Dialog { ok: bool, text: Option<String> },
    Recht { erlauben: bool },
    /// Ohne Benutzer: abgebrochen.
    Anmeldung { benutzer: Option<String>, passwort: Option<String> },
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DownloadStand {
    Start,
    /// Geladen, der Virenschutz prüft.
    Pruefung,
    Fertig,
    /// Der Virenschutz hat die Datei abgelehnt; sie ist gelöscht.
    Blockiert,
    Fehler,
    /// Auf dem Gerät war kein Platz mehr (Android).
    Speicher,
}

impl TabEreignis {
    /// Der Tab, von dem das Ereignis kommt.
    pub fn tab(&self) -> &str {
        use TabEreignis::*;
        match self {
            Laedt { id, .. } | Geladen { id, .. } | Adresse { id, .. } | Titel { id, .. } | Favicon { id, .. }
            | NeuerTab { id, .. } | Taste { id, .. } | Download { id, .. } | Schild { id, .. } | Absturz { id }
            | Vollbild { id, .. } | Formular { id, .. } | Kontextmenue { id, .. } | Dialog { id, .. }
            | Recht { id, .. } | Anmeldung { id, .. } | Fehlerseite { id, .. } | Gesperrt { id, .. }
            | Status { id, .. } | Treffer { id, .. } | Ton { id, .. } | Schlaf { id, .. } | Verworfen { id }
            | Protokoll { id, .. } | Texte { id, .. } => id,
        }
    }
}

/// Ein Ereignis, das Kotlin als JSON meldet (`Tab.melden`, `Herunterladen`,
/// `TabChrome`). Es wird in [`TabEreignis`] gelesen, also mit jedem Feld und
/// Typ geprüft; bis 09.10.2026 prüfte Rust nur `art` und `id` und gab den
/// Rest roh weiter. Schild, Gesperrt, Formular und Texte meldet nur Rust; Vollbild
/// zeigt Kotlin selbst, Rechte lehnt es ab, und Protokoll, Status, Ton und
/// Schlaf gibt es auf Android nicht.
pub fn von_kotlin(json: &str) -> Option<TabEreignis> {
    use TabEreignis::*;
    let ereignis: TabEreignis = serde_json::from_str(json).ok()?;
    let erlaubt = matches!(
        ereignis,
        Laedt { .. } | Geladen { .. } | Adresse { .. } | Titel { .. } | Favicon { .. } | NeuerTab { .. }
            | Absturz { .. } | Kontextmenue { .. } | Dialog { .. } | Anmeldung { .. } | Fehlerseite { .. }
            | Treffer { .. } | Download { .. }
    );
    (erlaubt && super::id_pruefen(ereignis.tab()).is_ok()).then_some(ereignis)
}

pub fn melden(app: &AppHandle, ereignis: TabEreignis) {
    if matches!(ereignis, TabEreignis::Gesperrt { .. }) {
        crate::schild::schutz_dienst::treffer(app);
    }
    let _ = app.emit_to("main", EREIGNIS, ereignis);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn was_kotlin_meldet_wird_mit_jedem_feld_geprueft() {
        let adresse = r#"{"art":"adresse","id":"tab-a1","url":"https://example.com/","zurueck":true,"vor":false}"#;
        assert!(matches!(von_kotlin(adresse), Some(TabEreignis::Adresse { zurueck: true, .. })));
        let download = r#"{"art":"download","id":"tab-a1","nr":7,"stand":"speicher","url":"https://x.example/a","datei":null}"#;
        assert!(von_kotlin(download).is_some());
        // Falscher Typ, fehlendes Feld, unbekannter Stand: bis 09.10.2026 ging das an die Oberfläche.
        assert!(von_kotlin(r#"{"art":"adresse","id":"tab-a1","url":"https://example.com/","zurueck":"ja","vor":false}"#).is_none());
        assert!(von_kotlin(r#"{"art":"titel","id":"tab-a1"}"#).is_none());
        assert!(von_kotlin(r#"{"art":"download","id":"tab-a1","nr":7,"stand":"irgendwas","url":"u","datei":null}"#).is_none());
        // Was nur Rust meldet, und fremde Kennungen.
        assert!(von_kotlin(r#"{"art":"gesperrt","id":"tab-a1","url":"https://x.example/","grund":"eigene"}"#).is_none());
        assert!(von_kotlin(r#"{"art":"schild","id":"tab-a1","werbung":1,"tracker":1}"#).is_none());
        assert!(von_kotlin(r#"{"art":"absturz","id":"main"}"#).is_none());
    }
}
