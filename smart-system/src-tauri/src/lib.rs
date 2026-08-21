//! Singra Smart System — Tauri-v2-Einstieg.
//!
//! Zwei Fenster (Hauptfenster + frameless Overlay), Tray mit Statusfarben,
//! globaler Hotkey Alt+Space, Autostart. Zero-Resource-Prinzip: hier läuft
//! keine einzige Schleife — alles ist ereignisgetrieben (Tray-Events,
//! Hotkey-Events, Commands aus dem Frontend), im Leerlauf schläft der Prozess.
//!
//! Harte Grenze: dieses Backend kennt keine Server-Werkzeuge und wird nie
//! welche bekommen — Server-Verwaltung bleibt exklusiv dem Web-Panel.

#[cfg(windows)]
mod ducking;
mod geheimnisse;
mod konfig;
mod tray;
mod wakeword;

use tauri::Manager;
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

#[tauri::command]
fn setze_status(app: tauri::AppHandle, status: String) -> Result<(), String> {
    tray::set_status(&app, &status)
}

/// Senkt Hintergrundton um 60 % ab (WASAPI) bzw. stellt ihn wieder her.
/// Auf anderen Plattformen ein stiller No-Op — die App zielt auf Windows,
/// aber ein Linux-CI-Check soll daran nicht scheitern.
#[tauri::command]
fn ducking(an: bool) -> Result<(), String> {
    #[cfg(windows)]
    {
        if an {
            ducking::starten()
        } else {
            ducking::stoppen()
        }
    }
    #[cfg(not(windows))]
    {
        let _ = an;
        Ok(())
    }
}

#[tauri::command]
fn konfig_laden(app: tauri::AppHandle) -> Result<konfig::AppKonfig, String> {
    konfig::laden(&app)
}

#[tauri::command]
fn konfig_speichern(app: tauri::AppHandle, konfig: konfig::AppKonfig) -> Result<(), String> {
    konfig::speichern(&app, &konfig)
}

/// Refresh-Token in den OS-Tresor. Das Access-Token wird bewusst nie
/// gespeichert — es lebt nur im Speicher des Frontends.
#[tauri::command]
fn refresh_token_speichern(token: String) -> Result<(), String> {
    geheimnisse::speichern(&token)
}

#[tauri::command]
fn refresh_token_laden() -> Result<Option<String>, String> {
    geheimnisse::laden()
}

#[tauri::command]
fn refresh_token_loeschen() -> Result<(), String> {
    geheimnisse::loeschen()
}

#[tauri::command]
fn wakeword_stand(app: tauri::AppHandle) -> Result<wakeword::WakewordStand, String> {
    wakeword::stand(&app)
}

/// Blockiert für die Dauer der Aufnahme (~2,2 s) — Tauri-Commands laufen
/// auf eigenen Threads, das Fenster bleibt bedienbar.
#[tauri::command]
fn wakeword_aufnehmen(app: tauri::AppHandle, nummer: u8) -> Result<String, String> {
    wakeword::aufnehmen(&app, nummer)
}

#[tauri::command]
fn wakeword_trainieren(app: tauri::AppHandle, wort: String) -> Result<(), String> {
    wakeword::trainieren(&app, &wort)
}

#[tauri::command]
fn wakeword_lauschen(app: tauri::AppHandle, an: bool) -> Result<(), String> {
    if an {
        wakeword::lauschen_starten(app)
    } else {
        wakeword::lauschen_stoppen();
        Ok(())
    }
}

#[tauri::command]
fn wakeword_zuruecksetzen(app: tauri::AppHandle) -> Result<(), String> {
    wakeword::zuruecksetzen(&app)
}

#[tauri::command]
fn overlay_sichtbar(app: tauri::AppHandle, sichtbar: bool) -> Result<(), String> {
    let fenster = app
        .get_webview_window("overlay")
        .ok_or("Overlay-Fenster fehlt")?;
    if sichtbar {
        fenster.show().map_err(|e| e.to_string())?;
    } else {
        fenster.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Alt+Space: Fenster nach vorn, wenn es nicht fokussiert ist — sonst weg.
/// So ist derselbe Griff „öffnen“ und „aus dem Weg“, wie man es von
/// Spotlight-artigen Overlays kennt.
fn hauptfenster_umschalten(app: &tauri::AppHandle) {
    if let Some(fenster) = app.get_webview_window("main") {
        let sichtbar = fenster.is_visible().unwrap_or(false);
        let fokussiert = fenster.is_focused().unwrap_or(false);
        if sichtbar && fokussiert {
            let _ = fenster.hide();
        } else {
            tray::hauptfenster_zeigen(app);
        }
    }
}

pub fn run() {
    tauri::Builder::default()
        // "--autostart" markiert Boot-Starts: Crash-Guard und sanfter Start
        // (spaetere Ausbaustufe) muessen wissen, ob ein Mensch gestartet hat.
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--autostart"]),
        ))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        hauptfenster_umschalten(app);
                    }
                })
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            setze_status,
            overlay_sichtbar,
            ducking,
            konfig_laden,
            konfig_speichern,
            refresh_token_speichern,
            refresh_token_laden,
            refresh_token_loeschen,
            wakeword_stand,
            wakeword_aufnehmen,
            wakeword_trainieren,
            wakeword_lauschen,
            wakeword_zuruecksetzen
        ])
        .setup(|app| {
            tray::erstellen(app.handle())?;
            // Ein belegter Hotkey (anderes Tool nutzt Alt+Space) darf den
            // Start nicht verhindern — die App bleibt ueber Tray erreichbar.
            if let Err(fehler) = app.global_shortcut().register("Alt+Space") {
                eprintln!("Globaler Hotkey Alt+Space nicht verfuegbar: {fehler}");
            }
            Ok(())
        })
        // Schliessen heisst „in den Tray“, nicht „beenden“ — beenden geht
        // ausdruecklich ueber das Tray-Menue. Ein Companion, der beim
        // Wegklicken stirbt, waere keiner.
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Fehler beim Start des Singra Smart Systems");
}
