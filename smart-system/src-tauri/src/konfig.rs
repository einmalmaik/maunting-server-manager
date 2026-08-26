//! Unkritische App-Einstellungen als JSON im App-Datenverzeichnis.
//!
//! Hier liegt **nie** ein Geheimnis: Tokens gehören in den OS-Tresor
//! (geheimnisse.rs), hier stehen nur Backend-URL, Sandbox-Pfad und der
//! Einrichtungsstand. Die Datei ist bewusst trivial — eine Struktur, eine
//! Datei, kein Migrationssystem: neue Felder kommen mit `#[serde(default)]`
//! dazu und fehlen in alten Dateien einfach.

use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

const DATEI: &str = "konfig.json";

/// Vorgaben der beiden globalen Hotkeys — Konstanten, damit `Default`,
/// Registrierung und Doku dieselbe Wahrheit tragen.
pub const HOTKEY_FENSTER_VORGABE: &str = "Alt+Space";
pub const HOTKEY_SPRACHE_VORGABE: &str = "Alt+Shift+Space";
/// Vorgabe der Wake-Word-Empfindlichkeit (rustpotter-Schwelle, Median-Score).
pub const WAKEWORD_SCHWELLE_VORGABE: f32 = 0.45;

pub const STANDARD_DOWNLOAD_LIMIT_BYTES: u64 = 10 * 1024 * 1024 * 1024; // 10 GiB
pub const MAX_DOWNLOAD_LIMIT_BYTES: u64 = 100 * 1024 * 1024 * 1024; // 100 GiB

#[derive(Serialize, Deserialize, Clone)]
#[serde(default)]
pub struct AppKonfig {
    /// Basis-URL des MSM-Backends (z. B. https://panel.example.com).
    pub backend_url: Option<String>,
    /// Arbeitsverzeichnis der Sandbox (Phase 5: einziger Schreibbereich der KI).
    pub sandbox_pfad: Option<String>,
    /// Der Einrichtungs-Assistent wurde vollständig durchlaufen.
    pub eingerichtet: bool,
    /// Globaler Hotkey: Hauptfenster zeigen/verstecken. `None` heißt bewusst
    /// deaktiviert. Fehlt das Feld in einer alten Datei, gilt die Vorgabe —
    /// deshalb der eigene `Default` unten statt `#[derive(Default)]`, dessen
    /// `None` hieße „abgeschaltet" statt „wie bisher".
    pub hotkey_fenster: Option<String>,
    /// Globaler Hotkey: Sprachsitzung im Overlay starten/beenden.
    pub hotkey_sprache: Option<String>,
    /// Ob das Wake-Word-Lauschen laufen soll — der **eine** Schalter, an dem
    /// alles hängt: der App-Start liest ihn, die Einstellungen stellen ihn.
    /// `false` heißt physisch aus — kein Pfad startet das Mikrofon, solange
    /// der Benutzer den Schalter nicht selbst umlegt. Vorgabe ist aus.
    pub wakeword_aktiv: bool,
    /// Auf welches Wort das Modell trainiert wurde (der Assistenten-Name zum
    /// Zeitpunkt der Kalibrierung). Weicht er vom heutigen Namen ab, schlägt
    /// die App eine Neukalibrierung vor — mehr nicht.
    pub wakeword_wort: Option<String>,
    /// Bevorzugtes Eingabegerät (Name, wie Windows ihn führt). `None` heißt:
    /// dem Windows-Standard folgen. Gilt für Wake-Word und Sprachsitzung.
    pub audio_eingabe: Option<String>,
    /// Bevorzugtes Ausgabegerät für die Stimme der KI. `None` = Windows-Standard.
    pub audio_ausgabe: Option<String>,
    /// Empfindlichkeit des Wake-Words: die rustpotter-Erkennungsschwelle.
    /// Kleiner = empfindlicher (mehr Treffer, mehr Fehlgriffe). Der Verbraucher
    /// klemmt auf 0,30–0,60 (`wakeword::schwelle_klemmen`).
    pub wakeword_schwelle: f32,
    /// Echounterdrückung der Sprachsitzung (Chromium-Verarbeitung, lokal).
    pub audio_echo: bool,
    /// Rauschunterdrückung der Sprachsitzung (Chromium-Verarbeitung, lokal).
    pub audio_rauschen: bool,
    /// Automatische Pegelanpassung der Sprachsitzung (Chromium, lokal).
    pub audio_autogain: bool,
    /// Software-Eingangsverstärkung der Sprachsitzung (1,0 = neutral).
    pub audio_verstaerkung: f32,
    /// Ob Computer-Use (Maus, Tastatur, Bildschirmsteuerung) durch die KI
    /// erlaubt ist. Vorgabe ist `false` (Datenschutz & Sicherheit).
    pub computer_use_aktiv: bool,
    /// Ob Artefakt-Installationen (Software, Mods, Installer) durch die KI
    /// erlaubt sind. Vorgabe ist `false` (Sicherheit & Privatsphäre).
    pub artifact_install_aktiv: bool,
    /// Maximales Download-Limit pro Artefakt in Bytes (Standard 10 GiB, max 100 GiB).
    pub max_download_bytes: u64,
    /// Vom Benutzer freigegebene Suchwurzeln für Spiele und Software.
    pub search_roots: Vec<String>,
    /// Ob Discord Rich Presence (RPC) aktiviert ist. Vorgabe ist `true`.
    pub discord_rpc_aktiv: bool,
    /// Optionale benutzerdefinierte Discord Application Client ID für Self-Hosting.
    pub discord_client_id: Option<String>,
    /// Optionaler benutzerdefinierter Details-Text (Zeile 1 in Discord).
    pub discord_details: Option<String>,
    /// Optionaler benutzerdefinierter State-Text (Zeile 2 in Discord).
    pub discord_state: Option<String>,
    /// Ob der Splashscreen beim Erststart bereits gesehen wurde.
    pub splash_gesehen: bool,
}

/// Ermittelt den isolierten Standard-Sandbox-Pfad im Benutzerprofil (`%USERPROFILE%\MSS-Sandbox` bzw. `$HOME/MSS-Sandbox`).
pub fn standard_sandbox_pfad() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .filter(|w| !w.is_empty())
        .map(PathBuf::from)
        .map(|p| p.join("MSS-Sandbox"))
        .or_else(|| {
            std::env::var_os("HOME")
                .filter(|w| !w.is_empty())
                .map(PathBuf::from)
                .map(|p| p.join("MSS-Sandbox"))
        })
}

/// Legt den isolierten Standard-Sandbox-Ordner physisch an, falls er nicht existiert, und liefert den Pfad.
pub fn standard_sandbox_anlegen() -> Result<PathBuf, String> {
    let pfad = standard_sandbox_pfad()
        .ok_or_else(|| "Benutzerverzeichnis nicht ermittelbar".to_string())?;
    std::fs::create_dir_all(&pfad)
        .map_err(|e| format!("Konnte Sandbox-Ordner nicht anlegen: {e}"))?;
    Ok(pfad)
}

impl Default for AppKonfig {
    fn default() -> Self {
        Self {
            backend_url: None,
            sandbox_pfad: standard_sandbox_pfad().map(|p| p.to_string_lossy().to_string()),
            eingerichtet: false,
            hotkey_fenster: Some(HOTKEY_FENSTER_VORGABE.into()),
            hotkey_sprache: Some(HOTKEY_SPRACHE_VORGABE.into()),
            wakeword_aktiv: false,
            wakeword_wort: None,
            audio_eingabe: None,
            audio_ausgabe: None,
            wakeword_schwelle: WAKEWORD_SCHWELLE_VORGABE,
            audio_echo: true,
            audio_rauschen: true,
            audio_autogain: true,
            audio_verstaerkung: 1.0,
            computer_use_aktiv: false,
            artifact_install_aktiv: false,
            max_download_bytes: STANDARD_DOWNLOAD_LIMIT_BYTES,
            search_roots: Vec::new(),
            discord_rpc_aktiv: true,
            discord_client_id: None,
            discord_details: None,
            discord_state: None,
            splash_gesehen: false,
        }
    }
}

fn pfad(app: &AppHandle) -> Result<PathBuf, String> {
    let basis = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("App-Datenverzeichnis unbekannt: {e}"))?;
    std::fs::create_dir_all(&basis).map_err(|e| e.to_string())?;
    Ok(basis.join(DATEI))
}

pub fn laden(app: &AppHandle) -> Result<AppKonfig, String> {
    let pfad = pfad(app)?;
    let mut konfig = if !pfad.exists() {
        AppKonfig::default()
    } else {
        let text = std::fs::read_to_string(&pfad).map_err(|e| e.to_string())?;
        aus_text(&text)?
    };
    if konfig.sandbox_pfad.is_none() {
        if let Ok(auto_pfad) = standard_sandbox_anlegen() {
            konfig.sandbox_pfad = Some(auto_pfad.to_string_lossy().to_string());
        }
    } else if let Some(vorhanden) = konfig.sandbox_pfad.as_deref() {
        let _ = std::fs::create_dir_all(vorhanden);
    }
    Ok(konfig)
}

/// Der Inhalt der Datei als Konfiguration — samt der einen Begradigung, die
/// eine ältere Datei braucht.
///
/// **Eine Regel für beide Felder: was `speichern` heute ablehnt, überlebt das
/// Laden nicht.** Sonst ist ein einziger alter Wert ein Riegel vor *allen*
/// Einstellungen — `speichern` prüft bei jedem Aufruf das ganze Objekt, und
/// über `speichern` läuft auch, was mit dem Wert nichts zu tun hat:
/// `hotkeys_setzen`, `wakeword_trainieren`, `wakeword_lauschen`,
/// `wakeword::zuruecksetzen`. Bei `wakeword_lauschen` liefe der Lauschfaden
/// dabei sogar schon, während der Schalter aus bliebe — genau die Anzeige,
/// die dort ausgeschlossen ist.
///
/// Der Rückweg ist für beide Felder harmlos und vorhanden: fehlt der
/// Sandbox-Pfad, meldet die KI „Kein Sandbox-Ordner eingerichtet"; fehlt die
/// Adresse, zeigt die App wieder den Einrichtungs-Assistenten. Vergessen wird
/// jeweils nur der eine Wert, und beim Neueintragen gilt dieselbe Prüfung wie
/// beim Speichern.
///
/// Bei der Adresse ist das Vergessen sogar die sichere Richtung: bliebe ein
/// `http://<LAN-Adresse>` stehen, gingen Kopplungscode und Refresh-Token
/// weiter im Klartext durchs Netz — genau das, was `backend_url_verboten`
/// verhindert. Beim Sandbox-Pfad hing der Dateizugriff ohnehin nie an diesem
/// Wert allein: `sandbox::aufloesen` misst jedes Ziel noch einmal an
/// denselben Zonen.
///
/// Die Gegenprobe wäre, in `speichern` nur den *geänderten* Wert zu prüfen;
/// das bräuchte dort ein zweites Lesen der Datei und ließe den verbotenen
/// Wert trotzdem für immer stehen.
fn aus_text(text: &str) -> Result<AppKonfig, String> {
    let mut konfig: AppKonfig =
        serde_json::from_str(text).map_err(|e| format!("konfig.json unlesbar: {e}"))?;
    if konfig.sandbox_pfad.as_deref().is_some_and(sandbox_pfad_verboten) {
        konfig.sandbox_pfad = None;
    }
    if konfig.backend_url.as_deref().is_some_and(backend_url_verboten) {
        konfig.backend_url = None;
    }
    Ok(konfig)
}

/// Anti-Zerstörungs-Invariante, erste Schranke: die Sandbox muss in den
/// eigenen Dateien des Benutzers liegen. Phase 5 prüft zusätzlich bei
/// **jedem** Dateizugriff — das hier verhindert nur, dass so ein Pfad
/// überhaupt gespeichert wird, und `aus_text` vergisst ihn, wenn er aus
/// einer älteren Datei stammt.
///
/// Was heikel ist, beantwortet `zonen::zone` und sonst nichts. Hier
/// stand vorher ein Textvergleich gegen `%WINDIR%`, also eine zweite,
/// schwächere Definition von „Systembereich": `C:\Program Files`, `C:\Boot`
/// und fremde Benutzerprofile gingen damit durch. `zone` kanonisiert
/// außerdem, weshalb auch eine Junction nicht mehr an der Prüfung vorbeiführt.
///
/// Erlaubt ist deshalb genau `Zone::Frei`: `Zone::System` wäre das, was die
/// Invariante verbietet, und `Zone::Muell` wäre ein Arbeitsordner, den die
/// KI beim Aufräumen selbst endgültig löschen dürfte.
///
/// Ein ganzes Laufwerk (`C:\`) fällt durch keine der beiden Fragen: der Pfad
/// ist selbst keine Systemstelle, er *enthält* nur alle. Ein Laufwerk ist
/// ohnehin kein Arbeitsordner.
fn sandbox_pfad_verboten(pfad: &str) -> bool {
    let pfad = Path::new(pfad.trim());
    // Relativ hieße: die Sandbox hängt am Arbeitsverzeichnis des Prozesses
    // und liegt nach einem Start aus einem anderen Ordner woanders — dann
    // wäre auch die Zonenantwort von eben nichts mehr wert.
    if !pfad.is_absolute() {
        return true;
    }
    let nur_laufwerk = pfad
        .components()
        .all(|teil| matches!(teil, Component::Prefix(_) | Component::RootDir));
    nur_laufwerk || crate::zonen::zone(pfad) != crate::zonen::Zone::Frei
}

/// Über diese Adresse laufen der Kopplungscode und das Refresh-Token, und an
/// einer Gerätesitzung hängt der volle Desktop-Werkzeugkatalog. Auf `http://`
/// liest jeder im selben Netz beides mit, deshalb bleibt Klartext auf den
/// eigenen Rechner beschränkt — dort verlässt nichts davon das Gerät, und die
/// Entwicklungsstrecke (Sidecar auf localhost) bleibt unverändert nutzbar.
fn backend_url_verboten(url: &str) -> bool {
    if url.starts_with("https://") {
        return false;
    }
    let Some(rest) = url.strip_prefix("http://") else {
        return true;
    };
    let host = rest
        .split(['/', '?', '#'])
        .next()
        .unwrap_or("")
        .to_ascii_lowercase();
    // `http://localhost@fremder.example` wäre sonst „lokal": alles vor dem
    // `@` ist Benutzername und nicht der Host, den der Browser anspricht.
    if host.contains('@') {
        return true;
    }
    !["localhost", "127.0.0.1", "[::1]"]
        .iter()
        .any(|lokal| host == *lokal || host.starts_with(&format!("{lokal}:")))
}

pub fn speichern(app: &AppHandle, konfig: &AppKonfig) -> Result<(), String> {
    if let Some(sandbox) = konfig.sandbox_pfad.as_deref() {
        if sandbox_pfad_verboten(sandbox) {
            return Err("Die Sandbox muss ein eigener Ordner in den Dateien des \
                        Benutzers sein: kein ganzes Laufwerk, kein Windows-, \
                        Programm- oder Zwischenspeicherordner und kein fremdes \
                        Benutzerprofil."
                .into());
        }
    }
    if let Some(url) = konfig.backend_url.as_deref() {
        if backend_url_verboten(url) {
            return Err("Backend-URL muss mit https:// beginnen. http:// ist nur \
                        für localhost erlaubt, sonst gingen Kopplungscode und \
                        Token im Klartext über das Netz."
                .into());
        }
    }
    let text = serde_json::to_string_pretty(konfig).map_err(|e| e.to_string())?;
    std::fs::write(pfad(app)?, text).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::{
        aus_text, backend_url_verboten, sandbox_pfad_verboten, AppKonfig, HOTKEY_FENSTER_VORGABE,
        HOTKEY_SPRACHE_VORGABE,
    };

    #[test]
    fn alte_datei_ohne_hotkey_felder_bekommt_die_vorgaben() {
        // Die Felder kamen später dazu. Eine Datei aus der Zeit davor darf
        // die Hotkeys nicht verlieren — `Alt+Space` war immer an.
        let alt = r#"{"backend_url":"https://panel.example.com","sandbox_pfad":null,"eingerichtet":true}"#;
        let konfig: AppKonfig = serde_json::from_str(alt).unwrap();
        assert_eq!(konfig.hotkey_fenster.as_deref(), Some(HOTKEY_FENSTER_VORGABE));
        assert_eq!(konfig.hotkey_sprache.as_deref(), Some(HOTKEY_SPRACHE_VORGABE));
    }

    #[test]
    fn null_heisst_deaktiviert_und_ueberlebt_den_roundtrip() {
        // `null` ist eine Entscheidung, kein fehlendes Feld: der Benutzer hat
        // den Hotkey abgeschaltet, und Speichern + Laden darf daraus nicht
        // wieder die Vorgabe machen.
        let text = r#"{"hotkey_fenster":null,"hotkey_sprache":"Ctrl+Shift+K"}"#;
        let konfig: AppKonfig = serde_json::from_str(text).unwrap();
        assert_eq!(konfig.hotkey_fenster, None);
        assert_eq!(konfig.hotkey_sprache.as_deref(), Some("Ctrl+Shift+K"));

        let gespeichert = serde_json::to_string(&konfig).unwrap();
        let wieder: AppKonfig = serde_json::from_str(&gespeichert).unwrap();
        assert_eq!(wieder.hotkey_fenster, None);
        assert_eq!(wieder.hotkey_sprache.as_deref(), Some("Ctrl+Shift+K"));
    }

    #[test]
    fn wakeword_ist_in_alten_dateien_aus_und_ohne_geraetewahl() {
        // Anders als bei den Hotkeys ist die richtige Vorgabe hier **aus**:
        // ein Mikrofon, das nach einem Update von selbst zu lauschen beginnt,
        // wäre genau das falsche Verhalten für ein Werkzeug, das mithört.
        let alt = r#"{"eingerichtet":true}"#;
        let konfig: AppKonfig = serde_json::from_str(alt).unwrap();
        assert!(!konfig.wakeword_aktiv);
        assert_eq!(konfig.wakeword_wort, None);
        assert_eq!(konfig.audio_eingabe, None);
        assert_eq!(konfig.audio_ausgabe, None);
    }

    #[test]
    fn audio_vorgaben_sind_neutral_und_die_verarbeitung_an() {
        // Die Chromium-Verarbeitung (Echo, Rauschen, Auto-Pegel) ist der
        // Grund, warum die Sprachsitzung ohne Kopfhörer funktioniert — sie
        // muss in alten Dateien anbleiben. Verstärkung 1,0 heißt: nichts tun.
        let alt = r#"{"eingerichtet":true}"#;
        let konfig: AppKonfig = serde_json::from_str(alt).unwrap();
        assert!(konfig.audio_echo);
        assert!(konfig.audio_rauschen);
        assert!(konfig.audio_autogain);
        assert_eq!(konfig.audio_verstaerkung, 1.0);
        assert_eq!(konfig.wakeword_schwelle, super::WAKEWORD_SCHWELLE_VORGABE);
    }

    #[test]
    fn windows_verzeichnis_ist_tabu() {
        assert!(sandbox_pfad_verboten("C:\\Windows"));
        assert!(sandbox_pfad_verboten("C:\\Windows\\System32"));
        assert!(sandbox_pfad_verboten("c:/windows/temp"));
    }

    #[test]
    fn ein_ganzes_laufwerk_ist_keine_sandbox() {
        // Der Fall, der die alte Prüfung ausgehebelt hat: `C:\` ist selbst
        // kein Systemverzeichnis, es enthält nur alle. Als Sandbox-Wurzel
        // hätte damit jeder Dateizugriff der KI in `C:\Windows\System32`
        // gelegen — innerhalb der Grenze.
        assert!(sandbox_pfad_verboten("C:\\"));
        assert!(sandbox_pfad_verboten("C:/"));
        assert!(sandbox_pfad_verboten("D:\\"));
        // Ein relativer Pfad hinge am Arbeitsverzeichnis und wäre morgen ein
        // anderer Ordner.
        assert!(sandbox_pfad_verboten("Sandbox"));
        assert!(sandbox_pfad_verboten(""));
    }

    #[test]
    fn programme_boot_und_fremde_profile_sind_ebenfalls_tabu() {
        // Alles hier ging durch den alten Textvergleich gegen %WINDIR%.
        for name in ["ProgramFiles", "SystemRoot"] {
            if let Some(pfad) = std::env::var_os(name) {
                let pfad = pfad.to_string_lossy().to_string();
                assert!(sandbox_pfad_verboten(&pfad), "{pfad}");
            }
        }
        if let Some(laufwerk) = std::env::var_os("SystemDrive") {
            // Der Laufwerksstamm muss von Hand zusammengesetzt werden:
            // `%SystemDrive%` ist „C:" **ohne** Trennzeichen, und
            // `PathBuf::push` setzt hinter einem bloßen Laufwerkspräfix
            // bewusst keins. `from("C:").join("Boot")` ergäbe `C:Boot` — ein
            // laufwerksrelativer Pfad, der schon an der Relativ-Kante oben
            // durchfällt. Die Zusicherung hätte dann gehalten, ohne die
            // Zonenfrage je zu stellen.
            let stamm = std::path::PathBuf::from(format!("{}\\", laufwerk.to_string_lossy()));
            assert!(sandbox_pfad_verboten(&stamm.join("Boot").to_string_lossy()));
        }
        if let Some(temp) = std::env::var_os("TEMP") {
            // Zwischenspeicher: dort räumt die KI selbst endgültig auf.
            assert!(sandbox_pfad_verboten(&temp.to_string_lossy()));
        }
    }

    #[test]
    fn benutzerordner_sind_erlaubt() {
        let profil = std::path::PathBuf::from(
            std::env::var_os("USERPROFILE").expect("USERPROFILE müsste gesetzt sein"),
        );
        let verboten = |pfad: std::path::PathBuf| sandbox_pfad_verboten(&pfad.to_string_lossy());

        // Der Normalfall aus dem Assistenten.
        assert!(!verboten(profil.join("MSS-Sandbox")));
        assert!(!verboten(profil.join("Downloads\\Arbeit")));
        assert!(!sandbox_pfad_verboten("D:\\Projekte"));
        // "Windows" als Namensbestandteil ausserhalb des Systempfads ist ok.
        assert!(!sandbox_pfad_verboten("C:\\WindowsAppsBackup"));

        // Bewusst nicht erlaubt: das Profil selbst (dort liegen .ssh und der
        // Browser-Speicher) und die Profilwurzel daneben, in der die fremden
        // Profile stehen. Beides ist `Zone::System`.
        assert!(verboten(profil.clone()));
        if let Some(alle) = profil.parent() {
            assert!(verboten(alle.to_path_buf()));
            assert!(verboten(alle.join("fremder")));
        }
    }

    #[test]
    fn eine_inzwischen_verbotene_sandbox_faellt_beim_laden_weg() {
        // Der Bestandsnutzer: seine Sandbox war das ganze Laufwerk, die
        // Prüfung dagegen kam später. Bliebe der Wert stehen, lehnte
        // `speichern` von da an **jede** Einstellung ab — Hotkeys,
        // Audiogerät, Wake-Word —, denn geprüft wird immer das ganze Objekt.
        let alt = r#"{"backend_url":"https://panel.example.com","sandbox_pfad":"C:\\",
                      "eingerichtet":true,"hotkey_fenster":"Ctrl+K","wakeword_aktiv":true}"#;
        let konfig = aus_text(alt).unwrap();
        assert_eq!(konfig.sandbox_pfad, None);
        // Und nur dieser eine Wert fällt: alles andere ist unbeteiligt.
        assert_eq!(konfig.backend_url.as_deref(), Some("https://panel.example.com"));
        assert_eq!(konfig.hotkey_fenster.as_deref(), Some("Ctrl+K"));
        assert!(konfig.eingerichtet);
        assert!(konfig.wakeword_aktiv);

        // Die Gegenprobe: ein erlaubter Ordner überlebt das Laden. Sonst
        // wäre die Sandbox nach jedem Start wieder weg.
        let gut = std::path::PathBuf::from(
            std::env::var_os("USERPROFILE").expect("USERPROFILE müsste gesetzt sein"),
        )
        .join("MSS-Sandbox")
        .to_string_lossy()
        .into_owned();
        let datei = serde_json::json!({ "sandbox_pfad": &gut }).to_string();
        assert_eq!(aus_text(&datei).unwrap().sandbox_pfad.as_deref(), Some(gut.as_str()));
    }

    #[test]
    fn eine_inzwischen_verbotene_adresse_faellt_beim_laden_ebenfalls_weg() {
        // Der zweite Bestandsnutzer: sein Panel lief auf `http://<LAN-IP>`,
        // die Prüfung dagegen kam später. Blieb der Wert stehen, lehnte
        // `speichern` von da an **jede** Einstellung ab — und über `speichern`
        // laufen `hotkeys_setzen`, `wakeword_trainieren`, `wakeword_lauschen`
        // und `wakeword::zuruecksetzen`. Beim Lauschen liefe der Faden dabei
        // schon, während der Schalter aus bliebe; „Wake-Word neu einrichten"
        // kam gar nicht mehr bis zu den Aufnahmen.
        let alt = r#"{"backend_url":"http://192.168.1.50:8000","sandbox_pfad":null,
                      "eingerichtet":true,"hotkey_fenster":"Ctrl+K"}"#;
        let konfig = aus_text(alt).unwrap();
        assert_eq!(konfig.backend_url, None);
        // Nur dieser eine Wert fällt; der Rest ist unbeteiligt, und der
        // Assistent fragt die Adresse neu ab.
        assert_eq!(konfig.hotkey_fenster.as_deref(), Some("Ctrl+K"));
        assert!(konfig.eingerichtet);

        // Die Regel als Ganzes: aus einer Datei mit **beiden** Altwerten darf
        // nichts hervorgehen, was `speichern` gleich wieder abweist.
        let beides = r#"{"backend_url":"http://panel.example.com","sandbox_pfad":"C:\\"}"#;
        let konfig = aus_text(beides).unwrap();
        assert!(!konfig.backend_url.as_deref().is_some_and(backend_url_verboten));
        assert!(!konfig.sandbox_pfad.as_deref().is_some_and(sandbox_pfad_verboten));

        // Die Gegenprobe: eine gültige Adresse überlebt das Laden. Sonst wäre
        // sie nach jedem Start weg und der Assistent käme immer wieder.
        for gut in ["https://panel.example.com", "http://localhost:8000"] {
            let datei = serde_json::json!({ "backend_url": gut }).to_string();
            assert_eq!(aus_text(&datei).unwrap().backend_url.as_deref(), Some(gut));
        }
    }

    #[test]
    fn klartext_ist_nur_auf_dem_eigenen_rechner_erlaubt() {
        // Über die Adresse gehen Kopplungscode und Refresh-Token.
        assert!(!backend_url_verboten("https://panel.example.com"));
        assert!(!backend_url_verboten("http://localhost:1430"));
        assert!(!backend_url_verboten("http://127.0.0.1:8000/api"));
        assert!(!backend_url_verboten("http://[::1]:8000"));

        assert!(backend_url_verboten("http://panel.example.com"));
        assert!(backend_url_verboten("http://192.168.1.50:8000"));
        // Der Host ist `fremder.example`, nicht `localhost`: alles vor dem
        // `@` ist Benutzername.
        assert!(backend_url_verboten("http://localhost@fremder.example/api"));
        assert!(backend_url_verboten("http://localhost.fremder.example"));
        assert!(backend_url_verboten("ftp://panel.example.com"));
        assert!(backend_url_verboten("panel.example.com"));
    }

    #[test]
    fn computer_use_ist_standardmaessig_deaktiviert() {
        let standard = AppKonfig::default();
        assert!(!standard.computer_use_aktiv);

        let alt = r#"{"eingerichtet": true}"#;
        let geladen = aus_text(alt).unwrap();
        assert!(!geladen.computer_use_aktiv);
    }
}
