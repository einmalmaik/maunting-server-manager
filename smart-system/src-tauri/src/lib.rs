//! MSS — Maunting Smart System, Tauri-v2-Einstieg.
//!
//! Zwei Fenster (Hauptfenster + frameless Overlay), Tray mit Statusfarben,
//! zwei konfigurierbare globale Hotkeys (Fenster umschalten, Sprachsitzung
//! im Overlay — je einzeln abschaltbar), Autostart. Zero-Resource-Prinzip:
//! hier läuft keine einzige Schleife — alles ist ereignisgetrieben
//! (Tray-Events, Hotkey-Events, Commands aus dem Frontend), im Leerlauf
//! schläft der Prozess.
//!
//! Harte Grenze: dieses Backend kennt keine Server-Werkzeuge und wird nie
//! welche bekommen — Server-Verwaltung bleibt exklusiv dem Web-Panel.

mod audio;
mod aufraeumen;
mod auftrag;
mod bildschirm;
mod deinstallation;
#[cfg(windows)]
mod ducking;
mod geheimnisse;
mod konfig;
mod sandbox;
mod sichtfeld;
mod system;
mod tray;
mod uebernahme;
mod virenscan;
mod wakeword;
mod zonen;

use tauri::{Emitter, Manager};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

#[tauri::command]
fn setze_status(app: tauri::AppHandle, status: String) -> Result<(), String> {
    // Nur das Tray. Das Schaufenster-Ereignis (`mss:overlay-zustand-test`)
    // schicken die Diagnose-Knöpfe selbst — hier stand es einmal mit, und
    // dann folgte die Schaufenster-Blase jeder echten Sitzung des
    // Hauptfensters, weil auch die Zustandsverdrahtung diesen Befehl ruft.
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

/// Fuehrt einen Auftrag des Panels aus (Dateien, Programme, Uebernahme).
///
/// Der Sandbox-Ordner kommt aus der Konfiguration und nicht aus dem Auftrag:
/// welcher Ordner freigegeben ist, entscheidet der Benutzer in der App, nie
/// das Panel und schon gar nicht das Modell.
///
/// `Ok(None)` heisst: das Ergebnis kommt spaeter. Das ist genau ein Fall — die
/// Bitte um die Uebernahme, ueber die ein Mensch entscheidet.
#[tauri::command]
fn auftrag_ausfuehren(
    app: tauri::AppHandle,
    werkzeug: String,
    argumente: serde_json::Value,
) -> Result<Option<serde_json::Value>, String> {
    let pfad = konfig::laden(&app)
        .ok()
        .and_then(|k| k.sandbox_pfad)
        .map(std::path::PathBuf::from);
    auftrag::ausfuehren(&app, pfad, &werkzeug, &argumente)
}

/// Erteilt die Freigabe fuer Maus und Tastatur — nur nach einem Klick des
/// Menschen auf der Bestaetigungskarte. Die Frist selbst liegt in `uebernahme`
/// und damit im Prozess, nicht im Panel.
#[tauri::command]
fn uebernahme_freigeben(minuten: u64) -> Result<(), String> {
    uebernahme::freigeben(minuten)
}

#[tauri::command]
fn uebernahme_widerrufen() -> Result<(), String> {
    uebernahme::widerrufen()
}

/// Fuehrt den wartenden Aufraeumauftrag aus — der Klick auf "Ja".
///
/// Nimmt bewusst **keine** Pfadliste entgegen: der Plan liegt in Rust
/// (`auftrag::WARTEND`), und bestaetigt wird er, nicht das, was ein Fenster
/// gerade anzeigt. Ein Command mit Pfaden waere ein Loeschbefehl, den jeder
/// Aufrufer dieses IPC-Kanals frei fuellen koennte.
#[tauri::command]
fn aufraeumen_bestaetigen() -> Result<serde_json::Value, String> {
    auftrag::aufraeumen_bestaetigen()
}

/// Verwirft den wartenden Aufraeumauftrag. Es wird nichts angefasst.
#[tauri::command]
fn aufraeumen_ablehnen() {
    auftrag::aufraeumen_ablehnen()
}

/// Wie lange die Freigabe noch laeuft. Die Oberflaeche zeigt es an — eine
/// laufende Uebernahme, die man nicht sieht, waere die schlechteste Fassung.
#[tauri::command]
fn uebernahme_rest() -> Result<u64, String> {
    Ok(uebernahme::restsekunden())
}

/// Entfernt alle lokalen Spuren (Konfiguration, Sprachaufnahmen, Tresor,
/// Autostart) und berichtet einzeln, was geklappt hat. Startet den
/// Uninstaller **nicht** — das ist ein eigener Schritt, damit die Oberflaeche
/// den Bericht vorher zeigen kann.
#[tauri::command]
fn deinstallation_aufraeumen(app: tauri::AppHandle) -> deinstallation::Aufraeumbericht {
    deinstallation::aufraeumen(&app)
}

#[tauri::command]
fn deinstallation_starten(app: tauri::AppHandle) -> Result<(), String> {
    deinstallation::uninstaller_starten(&app)
}

#[tauri::command]
fn wakeword_stand(app: tauri::AppHandle) -> Result<wakeword::WakewordStand, String> {
    wakeword::stand(&app)
}

/// Blockiert, bis gesprochen wurde — höchstens ~7,5 s (Energie-Tor in
/// `wakeword::aufnehmen`). Tauri-Commands laufen auf eigenen Threads,
/// das Fenster bleibt bedienbar.
#[tauri::command]
fn wakeword_aufnehmen(app: tauri::AppHandle, nummer: u8) -> Result<String, String> {
    wakeword::aufnehmen(&app, nummer)
}

/// Trainiert das Modell und merkt sich das Wort in der Konfiguration —
/// daran erkennt die App später, dass der Assistent inzwischen anders heißt,
/// und schlägt die Neukalibrierung vor.
#[tauri::command]
fn wakeword_trainieren(app: tauri::AppHandle, wort: String) -> Result<(), String> {
    wakeword::trainieren(&app, &wort)?;
    let mut konfig = konfig::laden(&app)?;
    konfig.wakeword_wort = Some(wort.trim().to_string());
    konfig::speichern(&app, &konfig)
}

/// Der **eine** Schalter fürs Wake-Word: startet/stoppt den Lausch-Thread und
/// speichert den Wunsch. „Aus" heißt physisch aus — der App-Start liest nur
/// diesen Wert, nichts anderes schaltet das Mikrofon wieder ein. Gespeichert
/// wird „an" erst nach erfolgreichem Start: ein Schalter, der an zeigt,
/// während nichts lauscht, wäre gelogen.
#[tauri::command]
fn wakeword_lauschen(app: tauri::AppHandle, an: bool) -> Result<(), String> {
    if an {
        wakeword::lauschen_starten(app.clone())?;
    } else {
        wakeword::lauschen_stoppen();
    }
    let mut konfig = konfig::laden(&app)?;
    konfig.wakeword_aktiv = an;
    konfig::speichern(&app, &konfig)
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
        // X, ESC und die Selbstschließung laufen hierher — ein verstecktes
        // Fenster ist nie mehr ein Schaufenster.
        SCHAUFENSTER_OFFEN.store(false, std::sync::atomic::Ordering::SeqCst);
        fenster.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Fenster-Hotkey: Fenster nach vorn, wenn es nicht fokussiert ist — sonst
/// weg. So ist derselbe Griff „öffnen“ und „aus dem Weg“, wie man es von
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

/// Ob das sichtbare Overlay nur das **Schaufenster** ist (Testknopf) und
/// keine Sitzung trägt. Die Sichtbarkeit allein war einmal der Zustand
/// („sichtbar = Sitzung läuft"); das Schaufenster bricht diese Gleichung,
/// und ohne dieses Flag blieb das Wake-Word stumm, solange das Testfenster
/// stand — der Frühabbruch in `sprachsitzung_starten` hielt jede echte
/// Sitzung auf, und die 20-s-Selbstschließung ist im Schaufenster bewusst aus.
static SCHAUFENSTER_OFFEN: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Startet die Sprachsitzung im Overlay, falls dort keine läuft. Rust zeigt
/// nur das Fenster und schickt Ereignisse — die Sitzung selbst (Mikrofon,
/// Leitung) gehört dem Frontend. Ein offenes Schaufenster wird abgelöst:
/// das Frontend hört auf denselben Start und schaltet um.
/// Aufrufer: der Sprach-Hotkey und das erkannte Wake-Word.
pub(crate) fn sprachsitzung_starten(app: &tauri::AppHandle) {
    let Some(overlay) = app.get_webview_window("overlay") else {
        return;
    };
    let sichtbar = overlay.is_visible().unwrap_or(false);
    let schaufenster = SCHAUFENSTER_OFFEN.swap(false, std::sync::atomic::Ordering::SeqCst);
    if sichtbar && !schaufenster {
        return;
    }
    let _ = overlay.show();
    let _ = app.emit("mss:overlay-sprache-start", ());
}

/// Sprach-Hotkey: Sitzung im Overlay starten — oder beenden, wenn dort schon
/// eine läuft. Ein offenes Schaufenster zählt nicht als laufende Sitzung:
/// wer den Sprach-Hotkey drückt, will reden, nicht das Testfenster schließen.
fn sprachsitzung_umschalten(app: &tauri::AppHandle) {
    let Some(overlay) = app.get_webview_window("overlay") else {
        return;
    };
    let sichtbar = overlay.is_visible().unwrap_or(false);
    if sichtbar && !SCHAUFENSTER_OFFEN.load(std::sync::atomic::Ordering::SeqCst) {
        let _ = app.emit("mss:overlay-sprache-ende", ());
    } else {
        sprachsitzung_starten(app);
    }
}

/// Der Testknopf in den Einstellungen — ein **Schaufenster**, keine Sitzung:
/// das Overlay zeigt sich mit der Sprachblase, aber ohne Mikrofon und ohne
/// Leitung. Hier stand einmal der echte Sitzungsstart (derselbe Weg wie
/// Hotkey und Wake-Word) — wer nur sehen wollte, wie das Overlay aussieht,
/// sprach plötzlich mit der KI. Die vier Diagnose-Knöpfe (`setze_status`)
/// färben zusätzlich die Blase. Zweiter Druck, X oder ESC schließen.
#[tauri::command]
fn overlay_testen(app: tauri::AppHandle) {
    let Some(overlay) = app.get_webview_window("overlay") else {
        return;
    };
    if overlay.is_visible().unwrap_or(false) {
        SCHAUFENSTER_OFFEN.store(false, std::sync::atomic::Ordering::SeqCst);
        let _ = app.emit("mss:overlay-sprache-ende", ());
    } else {
        SCHAUFENSTER_OFFEN.store(true, std::sync::atomic::Ordering::SeqCst);
        let _ = overlay.show();
        let _ = app.emit("mss:overlay-schaufenster", ());
    }
}

/// Alle Audiogeräte mit Namen — für die Auswahl in den Einstellungen.
#[tauri::command]
fn audio_geraete() -> audio::AudioGeraete {
    audio::geraete()
}

/// Nur die eigene Oberfläche: die gebaute App (`tauri.localhost`) und der
/// Dev-Server (`localhost:1430`). Präfix mit Schrägstrich statt blossem
/// `starts_with` — „tauri.localhost.evil.example" wäre sonst auch „eigen".
#[cfg_attr(not(windows), allow(dead_code))]
fn ist_eigene_oberflaeche(uri: &str) -> bool {
    const EIGENE: [&str; 3] = [
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:1430",
    ];
    EIGENE
        .iter()
        .any(|basis| uri == *basis || uri.starts_with(&format!("{basis}/")))
}

/// Beantwortet die Mikrofonfrage des WebViews — für genau die eigene
/// Oberfläche.
///
/// WebView2 zeigt für `getUserMedia` auf `tauri.localhost` keinen eigenen
/// Freigabedialog: die Anfrage blieb unbeantwortet, der Sprachmodus meldete
/// „Mikrofon verweigert", und nie war eine Frage zu sehen. Der Handler
/// erlaubt **nur** das Mikrofon und **nur** der eigenen Herkunft — Kamera,
/// Standort und fremde Adressen behalten das Standardverhalten.
#[cfg(windows)]
fn mikrofon_freigeben(app: &tauri::AppHandle) {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
        COREWEBVIEW2_PERMISSION_STATE_ALLOW,
    };
    use webview2_com::{take_pwstr, PermissionRequestedEventHandler};
    // Bewusst nicht `windows::core::PWSTR`: unser `windows` (0.62, WASAPI)
    // und das von webview2-com (0.61) tragen gleichnamige, inkompatible Typen.
    use windows_strings::PWSTR;

    for kennung in ["main", "overlay"] {
        let Some(fenster) = app.get_webview_window(kennung) else {
            continue;
        };
        let _ = fenster.with_webview(|webview| unsafe {
            let Ok(kern) = webview.controller().CoreWebView2() else {
                return;
            };
            let mut merkzettel = 0_i64;
            let _ = kern.add_PermissionRequested(
                &PermissionRequestedEventHandler::create(Box::new(|_, args| {
                    let Some(args) = args else { return Ok(()) };
                    let mut art = COREWEBVIEW2_PERMISSION_KIND::default();
                    args.PermissionKind(&mut art)?;
                    if art != COREWEBVIEW2_PERMISSION_KIND_MICROPHONE {
                        return Ok(());
                    }
                    let mut uri = PWSTR::null();
                    args.Uri(&mut uri)?;
                    if ist_eigene_oberflaeche(&take_pwstr(uri)) {
                        args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                    }
                    Ok(())
                })),
                &mut merkzettel,
            );
        });
    }
}

/// Prüft eine Tastenkombination, bevor irgendetwas umgestellt wird — die
/// Fehlermeldung nennt die Kombination, nicht nur den Parserfehler.
fn hotkey_pruefen(kombi: &str) -> Result<Shortcut, String> {
    kombi
        .parse::<Shortcut>()
        .map_err(|e| format!("Ungültige Tastenkombination „{kombi}“: {e}"))
}

/// Meldet die konfigurierten Hotkeys an: erst alles abmelden, dann neu.
/// `None` heißt bewusst deaktiviert. Eine belegte oder ungültige Kombination
/// ist ein Fehler an den Aufrufer — beim Start wird er nur protokolliert
/// (die App bleibt über das Tray erreichbar), beim Umstellen rollt
/// `hotkeys_setzen` auf den alten Stand zurück.
fn hotkeys_registrieren(
    app: &tauri::AppHandle,
    fenster: Option<&str>,
    sprache: Option<&str>,
) -> Result<(), String> {
    let kuerzel = app.global_shortcut();
    kuerzel.unregister_all().map_err(|e| e.to_string())?;
    if let Some(kombi) = fenster {
        kuerzel
            .on_shortcut(hotkey_pruefen(kombi)?, |app, _s, ereignis| {
                if ereignis.state() == ShortcutState::Pressed {
                    hauptfenster_umschalten(app);
                }
            })
            .map_err(|e| format!("Hotkey „{kombi}“ nicht verfügbar: {e}"))?;
    }
    if let Some(kombi) = sprache {
        kuerzel
            .on_shortcut(hotkey_pruefen(kombi)?, |app, _s, ereignis| {
                if ereignis.state() == ShortcutState::Pressed {
                    sprachsitzung_umschalten(app);
                }
            })
            .map_err(|e| format!("Hotkey „{kombi}“ nicht verfügbar: {e}"))?;
    }
    Ok(())
}

/// Stellt beide Hotkeys um und speichert sie. Scheitert die Registrierung
/// (belegt, ungültig, doppelt), kommt der alte Stand zurück — halb
/// umgestellte Hotkeys wären schlimmer als die Fehlermeldung.
#[tauri::command]
fn hotkeys_setzen(
    app: tauri::AppHandle,
    fenster: Option<String>,
    sprache: Option<String>,
) -> Result<(), String> {
    let mut konfig = konfig::laden(&app)?;
    if let Err(fehler) = hotkeys_registrieren(&app, fenster.as_deref(), sprache.as_deref()) {
        let _ = hotkeys_registrieren(
            &app,
            konfig.hotkey_fenster.as_deref(),
            konfig.hotkey_sprache.as_deref(),
        );
        return Err(fehler);
    }
    konfig.hotkey_fenster = fenster;
    konfig.hotkey_sprache = sprache;
    konfig::speichern(&app, &konfig)
}

/// Beendet die App wirklich — der eine Ausgang des Schließen-Dialogs.
#[tauri::command]
fn app_beenden(app: tauri::AppHandle) {
    app.exit(0);
}

/// Versteckt das Hauptfenster im Tray — der andere Ausgang.
#[tauri::command]
fn hauptfenster_verstecken(app: tauri::AppHandle) -> Result<(), String> {
    let fenster = app.get_webview_window("main").ok_or("Hauptfenster fehlt")?;
    fenster.hide().map_err(|e| e.to_string())
}

pub fn run() {
    tauri::Builder::default()
        // "--autostart" markiert Boot-Starts: Crash-Guard und sanfter Start
        // (spaetere Ausbaustufe) muessen wissen, ob ein Mensch gestartet hat.
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--autostart"]),
        ))
        // Die Handler hängen an den einzelnen Hotkeys (`on_shortcut` in
        // `hotkeys_registrieren`) — ein globaler Handler müsste selbst
        // auseinanderhalten, welcher der beiden gedrückt wurde.
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        // Programme, Dateien und Adressen oeffnen — der offizielle Weg in
        // Tauri v2 (tauri-plugin-shell::open ist deprecated).
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            setze_status,
            overlay_sichtbar,
            overlay_testen,
            audio_geraete,
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
            wakeword_zuruecksetzen,
            hotkeys_setzen,
            app_beenden,
            hauptfenster_verstecken,
            auftrag_ausfuehren,
            uebernahme_freigeben,
            uebernahme_widerrufen,
            uebernahme_rest,
            aufraeumen_bestaetigen,
            aufraeumen_ablehnen,
            deinstallation_aufraeumen,
            deinstallation_starten
        ])
        .setup(|app| {
            tray::erstellen(app.handle())?;
            // Ohne diesen Handler bleibt getUserMedia in WebView2 stumm —
            // der Sprachmodus wäre in der App unbenutzbar.
            #[cfg(windows)]
            mikrofon_freigeben(app.handle());
            // Ein belegter Hotkey (anderes Tool nutzt Alt+Space) darf den
            // Start nicht verhindern — die App bleibt ueber Tray erreichbar.
            let konfig = konfig::laden(app.handle()).unwrap_or_default();
            if let Err(fehler) = hotkeys_registrieren(
                app.handle(),
                konfig.hotkey_fenster.as_deref(),
                konfig.hotkey_sprache.as_deref(),
            ) {
                eprintln!("Globale Hotkeys nicht verfuegbar: {fehler}");
            }
            // Wake-Word: nur wenn der Benutzer den Schalter selbst auf „an"
            // gestellt hat (konfig.wakeword_aktiv) — das ist der einzige Pfad,
            // der das Lauschen beim Start weckt. Ein Fehler (Modell weg,
            // Mikrofon weg) darf den App-Start nicht verhindern.
            if konfig.wakeword_aktiv {
                if let Err(fehler) = wakeword::lauschen_starten(app.handle().clone()) {
                    eprintln!("Wake-Word-Lauschen nicht gestartet: {fehler}");
                }
            }
            // Sanfter Start: beim Boot-Autostart bleibt das Fenster im Tray —
            // niemand will nach dem Hochfahren eine Boot-Sequenz vor der Nase.
            if std::env::args().any(|arg| arg == "--autostart") {
                if let Some(fenster) = app.get_webview_window("main") {
                    let _ = fenster.hide();
                }
            }
            Ok(())
        })
        // Das X entscheidet nicht selbst: das Fenster wird angehalten und
        // das Frontend fragt den Menschen — in den Hintergrund (Standard,
        // ein Companion, der beim Wegklicken stirbt, waere keiner) oder
        // wirklich beenden (`app_beenden`).
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.emit("mss:schliessen-angefragt", ());
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Fehler beim Start des Maunting Smart Systems");
}

#[cfg(test)]
mod tests {
    use super::hotkey_pruefen;

    #[test]
    fn gaengige_kombinationen_gehen_durch() {
        // Die Vorgaben und das, was die Aufnahme in den Einstellungen baut.
        assert!(hotkey_pruefen("Alt+Space").is_ok());
        assert!(hotkey_pruefen("Alt+Shift+Space").is_ok());
        assert!(hotkey_pruefen("Ctrl+Shift+K").is_ok());
        assert!(hotkey_pruefen("F5").is_ok());
    }

    #[test]
    fn unsinn_wird_mit_der_kombination_im_text_abgelehnt() {
        // Die Meldung landet unverändert in der Oberfläche — sie muss sagen,
        // **was** abgelehnt wurde, nicht nur dass.
        let fehler = hotkey_pruefen("Foo+Bar").unwrap_err();
        assert!(fehler.contains("Foo+Bar"), "{fehler}");
    }

    #[test]
    fn mikrofon_bekommt_nur_die_eigene_oberflaeche() {
        use super::ist_eigene_oberflaeche;
        assert!(ist_eigene_oberflaeche("http://tauri.localhost/desktop.html"));
        assert!(ist_eigene_oberflaeche("https://tauri.localhost"));
        assert!(ist_eigene_oberflaeche(
            "http://localhost:1430/desktop.html?fenster=overlay"
        ));
        // Ein Präfix ist keine Herkunft: der Schrägstrich entscheidet.
        assert!(!ist_eigene_oberflaeche("http://tauri.localhost.evil.example/"));
        assert!(!ist_eigene_oberflaeche("http://localhost:14300/"));
        assert!(!ist_eigene_oberflaeche("https://panel.example.com/"));
    }
}
