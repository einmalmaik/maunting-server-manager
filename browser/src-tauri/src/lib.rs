//! Maunting Secure Browser: Tabs, Schild, Downloads und die Anbindung an MSM.
//!
//! Die Oberfläche (`frontend/src/browser`) ist eine eigene Webview `main`.
//! Jede Seite läuft in einer eigenen Tab-Webview ohne Zugriff auf die Befehle
//! hier: die Rechte gelten nur für die Webview `main`
//! (`capabilities/oberflaeche.json`), und weil `build.rs` alle Befehle im
//! App-Manifest nennt, prüft Tauri jeden Aufruf gegen diese Rechte.

pub mod browserdaten;
pub mod downloads;
pub mod fenster;
pub mod konfig;
pub mod kurzinfo;
#[cfg(windows)]
pub mod mixer;
pub mod schild;
pub mod seite;
pub mod tabs;
pub mod widget;

use tauri::Manager;

#[cfg(all(not(target_os = "android"), not(debug_assertions)))]
const DIENST: &str = "MauntingSecureBrowser";
#[cfg(any(target_os = "android", debug_assertions))]
const DIENST: &str = "MauntingSecureBrowserDev";

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    schluesselfach::einrichten(schluesselfach::Dienst {
        name: DIENST,
        frueher: None,
        android_paket: "com.mauntingstudios.secure_browser",
    });

    // `tauri dev` bekommt eine eigene Kennung und damit eigene Konfiguration
    // und eigenen WebView-Speicher, getrennt vom installierten Browser.
    #[allow(unused_mut)]
    let mut kontext = tauri::generate_context!();
    #[cfg(all(debug_assertions, desktop))]
    {
        let name = "Maunting Secure Browser Dev";
        kontext.config_mut().identifier = "com.mauntingstudios.secure-browser.dev".into();
        kontext.config_mut().product_name = Some(name.into());
        kontext.package_info_mut().name = name.into();
    }

    #[allow(unused_mut)]
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init());

    #[cfg(target_os = "android")]
    {
        builder = builder
            .plugin(tauri_plugin_biometric::init())
            .plugin(schluesselfach::biometrie::init_android_schluesselfach())
            .plugin(tabs::android::init());
    }

    builder
        .manage(tabs::Tabs::default())
        .setup(|app| {
            let handle = app.handle();
            let konfig = konfig::laden(handle);
            browserdaten::beim_start(handle, konfig.vergessen_beim_schliessen);
            schild::konfig_uebernehmen(&konfig);
            app.manage(konfig::KonfigZustand(std::sync::Mutex::new(konfig)));
            schild::listen::starten(handle);
            downloads::quarantaene::beim_start(handle);
            schild::schutz::starten(handle);
            tabs::ruhe::starten(handle.clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            konfig::konfig_laden,
            konfig::konfig_aendern,
            schluesselfach::befehle::refresh_token_speichern,
            schluesselfach::befehle::refresh_token_laden,
            schluesselfach::befehle::refresh_token_loeschen,
            schluesselfach::befehle::biometrie_verfuegbar,
            schluesselfach::befehle::biometrie_verifizieren,
            schluesselfach::befehle::biometrie_speichern,
            schluesselfach::befehle::biometrie_entsperren,
            schluesselfach::befehle::biometrie_loeschen,
            schluesselfach::befehle::biometrie_speicher_verfuegbar,
            schluesselfach::befehle::biometrie_speicher_fragt_selbst,
            schluesselfach::befehle::messenger_geraetegeheimnis,
            tabs::tab_laden,
            tabs::tab_aktivieren,
            tabs::tab_schliessen,
            tabs::tab_aktion,
            tabs::tabs_rahmen,
            tabs::tabs_verdecken,
            tabs::tab_standbild,
            tabs::tab_antworten,
            tabs::tab_protokoll,
            tabs::tab_suchen,
            tabs::tab_drucken,
            tabs::formular::tab_fuellen,
            tabs::tabs_zuruecksetzen,
            tabs::oberflaeche_fokussieren,
            tabs::ruhe::tabs_leistung,
            tabs::tab_stumm,
            schild::schild_stand,
            schild::schutz::schutz_stand,
            schild::schutz::schutz_aendern,
            schild::schutz::schutz_abbrechen,
            schild::schutz::schutz_bestaetigen,
            downloads::download_zeigen,
            downloads::download_ordner,
            fenster::fenster_aktion,
            kurzinfo::kurzinfo,
            browserdaten::seitendaten_loeschen,
            browserdaten::seitenrechte,
            browserdaten::seitenrecht_zuruecksetzen,
            widget::widget_start,
            widget::widget_stand,
            widget::bildsuche,
            widget::tastatur_zeigen,
        ])
        .on_window_event(|fenster, ereignis| {
            // „Beim Schließen vergessen“: erst die Seitendaten löschen, dann
            // beenden. Das Fenster bleibt dafür einen Moment stehen.
            if let tauri::WindowEvent::CloseRequested { api, .. } = ereignis {
                let app = fenster.app_handle().clone();
                let vergessen = app
                    .try_state::<konfig::KonfigZustand>()
                    .is_some_and(|k| k.0.lock().unwrap().vergessen_beim_schliessen);
                if fenster.label() == "main" && vergessen {
                    api.prevent_close();
                    std::thread::spawn(move || {
                        if let Err(fehler) = browserdaten::loeschen(&app) {
                            eprintln!("[MSB] Seitendaten beim Schließen nicht gelöscht: {fehler}");
                        }
                        app.exit(0);
                    });
                }
            }
        })
        .run(kontext)
        .expect("Fehler beim Starten des Maunting Secure Browsers");
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    fn namen(text: &str, muster: &str) -> BTreeSet<String> {
        text.lines()
            .filter_map(|z| z.trim().strip_prefix(muster))
            .map(|r| r.trim_end_matches([',', '"']).trim_start_matches('"').to_string())
            .collect()
    }

    /// Jeder registrierte Befehl steht im Manifest und in den Rechten der
    /// Oberfläche, und keiner darüber hinaus.
    #[test]
    fn rechte_decken_jeden_befehl() {
        let build = include_str!("../build.rs");
        let rechte = include_str!("../permissions/oberflaeche.toml");
        let lib = include_str!("lib.rs");

        let manifest: BTreeSet<String> = namen(build, "\"");
        let erlaubt: BTreeSet<String> = namen(rechte, "\"allow-").into_iter().map(|n| n.replace('-', "_")).collect();
        let registriert: BTreeSet<String> = lib
            .split("generate_handler![")
            .nth(1)
            .unwrap()
            .split(']')
            .next()
            .unwrap()
            .split(',')
            .filter_map(|p| p.trim().rsplit("::").next().map(str::to_string))
            .filter(|n| !n.is_empty())
            .collect();

        assert_eq!(manifest, registriert);
        assert_eq!(erlaubt, registriert);
    }
}
