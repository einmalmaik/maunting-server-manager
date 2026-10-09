/// Alle Befehle des Browsers. Weil sie hier im App-Manifest stehen, prüft
/// Tauri jeden Aufruf gegen die Rechte in `capabilities/` — auch von lokalen
/// Seiten. Ohne Manifest liefe ein App-Befehl aus jeder Webview mit lokaler
/// Herkunft ungeprüft durch, also auch aus einem Tab.
///
/// Ein neuer Befehl kommt hierher, in `generate_handler!` (`src/lib.rs`) und
/// in `permissions/oberflaeche.toml`; `tests::rechte_decken_jeden_befehl`
/// prüft das.
const BEFEHLE: &[&str] = &[
    "konfig_laden",
    "konfig_aendern",
    "refresh_token_speichern",
    "refresh_token_laden",
    "refresh_token_loeschen",
    "biometrie_verfuegbar",
    "biometrie_verifizieren",
    "biometrie_speichern",
    "biometrie_entsperren",
    "biometrie_loeschen",
    "biometrie_speicher_verfuegbar",
    "biometrie_speicher_fragt_selbst",
    "messenger_geraetegeheimnis",
    "tab_laden",
    "tab_aktivieren",
    "tab_schliessen",
    "tab_aktion",
    "tabs_rahmen",
    "tabs_verdecken",
    "tab_standbild",
    "tab_antworten",
    "tab_protokoll",
    "tab_suchen",
    "tab_drucken",
    "tab_fuellen",
    "tabs_zuruecksetzen",
    "oberflaeche_fokussieren",
    "tabs_leistung",
    "tab_stumm",
    "schild_stand",
    "schutz_stand",
    "schutz_aendern",
    "schutz_binden",
    "schutz_abbrechen",
    "schutz_bestaetigen",
    "download_zeigen",
    "download_ordner",
    "fenster_aktion",
    "kurzinfo",
    "seitendaten_loeschen",
    "seitenrechte",
    "seitenrecht_zuruecksetzen",
    "widget_start",
    "widget_stand",
    "widget_lage",
    "widget_anheften",
    "bildsuche",
    "tastatur_zeigen",
    "standardbrowser",
    "standardbrowser_werden",
    "standardbrowser_einstellungen",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(BEFEHLE)),
    )
    .expect("tauri-build fehlgeschlagen");
}
