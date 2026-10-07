pub mod adblock;
pub mod autofill;
pub mod konfig;
pub mod webview_manager;

use adblock::{adblock_status, adblock_zaehler_erhoehen};
use autofill::generiere_einfuege_script;
use konfig::{konfig_laden, konfig_speichern};
use webview_manager::{
    fenster_maximieren_umschalten, fenster_minimieren, fenster_schliessen, hauptfenster_fokussieren,
    tab_aktivieren, tab_aktualisieren, tab_bounds_anpassen, tab_erstellen, tab_navigieren,
    tab_neu_laden, tab_schliessen, tab_sichtbarkeit_setzen, tab_vorwaerts, tab_zustand_holen,
    tab_zurueck, TabManager,
};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[allow(unused_mut)]
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init());

    #[cfg(target_os = "android")]
    {
        builder = builder.plugin(tauri_plugin_biometric::init());
    }

    builder
        .manage(TabManager::default())
        .invoke_handler(tauri::generate_handler![
            konfig_laden,
            konfig_speichern,
            adblock_status,
            adblock_zaehler_erhoehen,
            generiere_einfuege_script,
            tab_zustand_holen,
            tab_erstellen,
            tab_aktivieren,
            tab_schliessen,
            tab_aktualisieren,
            tab_navigieren,
            tab_zurueck,
            tab_vorwaerts,
            tab_neu_laden,
            tab_bounds_anpassen,
            tab_sichtbarkeit_setzen,
            hauptfenster_fokussieren,
            fenster_schliessen,
            fenster_minimieren,
            fenster_maximieren_umschalten,
        ])
        .run(tauri::generate_context!())
        .expect("Fehler beim Starten des Maunting Secure Browsers");
}
