//! MSS — Maunting Smart System, Tauri-v2-Einstieg.
//!
//! Zwei Fenster (Hauptfenster + frameless Overlay), Tray mit Statusfarben,
//! zwei konfigurierbare globale Hotkeys (Fenster umschalten, Sprachsitzung
//! im Overlay — je einzeln abschaltbar), Autostart. In dieser Datei läuft
//! keine Schleife: alles ist ereignisgetrieben (Tray-Events, Hotkey-Events,
//! Commands aus dem Frontend).
//!
//! Hier stand „Zero-Resource-Prinzip … im Leerlauf schläft der Prozess".
//! Das gilt für dieses Modul, aber nicht für die App: ist das Wake-Word
//! eingeschaltet, liest ein Faden **dauerhaft** das Mikrofon und rechnet
//! (gemessen am 23.08.2026: rund ein Sechstel eines Kerns). Das ist der
//! Preis des Zuhörens und kein Fehler — aber eine Zusage, die der Prozess
//! nicht hält, gehört nicht in einen Modulkopf.
//!
//! **Regel für Commands:** alles, was mehr tut als ein Feld zu lesen,
//! braucht `#[tauri::command(async)]`. Ohne das läuft es auf dem
//! Hauptthread und friert die ganze Oberfläche ein, solange es dauert.
//! Umgekehrt gehören Fenster-Operationen (`overlay_*`, `hauptfenster_*`,
//! `setze_status`, `hotkeys_setzen`) **ohne** `async` hierher: sie sind
//! kurz, und auf dem Hauptthread blockiert kein Getter.
//!
//! Harte Grenze: dieses Backend kennt keine Server-Werkzeuge und wird nie
//! welche bekommen — Server-Verwaltung bleibt exklusiv dem Web-Panel.

pub mod artefakt;
mod audio;
mod aufraeumen;
mod auftrag;
mod bildschirm;
mod deinstallation;
pub mod discord;
#[cfg(windows)]
mod ducking;
mod geheimnisse;
mod konfig;
mod sandbox;
pub mod sandbox_container;
mod sichtfeld;
mod system;
mod tray;
mod uebernahme;
pub mod updater;
mod virenscan;
mod wakeword;
mod zonen;

use tauri::{Emitter, Manager};
use tauri_plugin_notification::NotificationExt;
#[cfg(not(target_os = "android"))]
use tauri_plugin_autostart::MacosLauncher;
#[cfg(not(target_os = "android"))]
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

#[tauri::command]
async fn benachrichtigung_senden(
    app: tauri::AppHandle,
    titel: String,
    text: String,
) -> Result<(), String> {
    let notif = app.notification();
    if let Ok(perm) = notif.permission_state() {
        if perm != tauri_plugin_notification::PermissionState::Granted {
            let _ = notif.request_permission();
        }
    }

    notif
        .builder()
        .title(titel)
        .body(text)
        .icon("ic_launcher")
        .large_icon("ic_launcher")
        .channel_id("mss_alerts")
        .show()
        .map_err(|e| e.to_string())
}

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
#[tauri::command(async)]
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

#[tauri::command(async)]
fn konfig_laden(app: tauri::AppHandle) -> Result<konfig::AppKonfig, String> {
    konfig::laden(&app)
}

#[tauri::command(async)]
fn konfig_speichern(app: tauri::AppHandle, konfig: konfig::AppKonfig) -> Result<(), String> {
    konfig::speichern(&app, &konfig)
}

/// Refresh-Token in den OS-Tresor bzw. Sandbox-Speicher. Das Access-Token wird
/// bewusst nie gespeichert — es lebt nur im flüchtigen Speicher des Frontends.
#[tauri::command(async)]
fn refresh_token_speichern(app: tauri::AppHandle, token: String) -> Result<(), String> {
    geheimnisse::speichern(&app, &token)
}

#[tauri::command(async)]
fn refresh_token_laden(app: tauri::AppHandle) -> Result<Option<String>, String> {
    geheimnisse::laden(&app)
}

#[tauri::command(async)]
fn refresh_token_loeschen(app: tauri::AppHandle) -> Result<(), String> {
    geheimnisse::loeschen(&app)
}

/// Prueft, ob Windows Sandbox auf diesem System verfuegbar ist.
#[tauri::command(async)]
fn sandbox_verfuegbar() -> bool {
    sandbox_container::ist_verfuegbar()
}

/// Fuehrt einen Auftrag des Panels aus (Dateien, Programme, Uebernahme).
///
/// Der Sandbox-Ordner kommt aus der Konfiguration und nicht aus dem Auftrag:
/// welcher Ordner freigegeben ist, entscheidet der Benutzer in der App, nie
/// das Panel und schon gar nicht das Modell.
///
/// `Ok(None)` heisst: das Ergebnis kommt spaeter. Das ist genau ein Fall — die
/// Bitte um die Uebernahme, ueber die ein Mensch entscheidet.
///
/// `auftrag_id` ist freiwillig und wird nur durchgereicht: Rust legt sie in
/// die Nutzlast der Bestaetigungskarten, damit die Antwort des Menschen zu
/// dem Auftrag gehoert, der gefragt hat — und nicht zu dem, den die
/// Oberflaeche sich gerade gemerkt hat.
#[tauri::command(async)]
fn auftrag_ausfuehren(
    app: tauri::AppHandle,
    werkzeug: String,
    argumente: serde_json::Value,
    auftrag_id: Option<String>,
) -> Result<Option<serde_json::Value>, String> {
    let pfad = konfig::laden(&app)
        .ok()
        .and_then(|k| k.sandbox_pfad)
        .map(std::path::PathBuf::from);
    auftrag::ausfuehren(&app, pfad, &werkzeug, &argumente, auftrag_id.as_deref())
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
#[tauri::command(async)]
fn aufraeumen_bestaetigen() -> Result<serde_json::Value, String> {
    auftrag::aufraeumen_bestaetigen()
}

/// Verwirft den wartenden Aufraeumauftrag. Es wird nichts angefasst.
#[tauri::command]
fn aufraeumen_ablehnen() {
    auftrag::aufraeumen_ablehnen()
}

/// Bestätigt eine wartende allgemeine Desktop-Aktion.
#[tauri::command(async)]
fn desktop_aktion_bestaetigen(
    app: tauri::AppHandle,
    auftrag_id: String,
) -> Result<serde_json::Value, String> {
    auftrag::desktop_aktion_bestaetigen(&app, &auftrag_id)
}

/// Lehnt eine wartende allgemeine Desktop-Aktion ab.
#[tauri::command]
fn desktop_aktion_ablehnen(auftrag_id: String) -> Result<(), String> {
    auftrag::desktop_aktion_ablehnen(&auftrag_id)
}

/// Öffnet eine externe URL sicher im Standard-Browser des Betriebssystems.
#[tauri::command(async)]
fn oeffne_browser(app: tauri::AppHandle, url: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let getrimmt = url.trim();
    if !(getrimmt.starts_with("https://") || getrimmt.starts_with("http://")) {
        return Err(format!("Nur http- und https-Adressen erlaubt: '{getrimmt}'"));
    }
    app.opener()
        .open_url(getrimmt, None::<&str>)
        .map_err(|e| format!("Browser konnte nicht geöffnet werden: {e}"))
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
#[tauri::command(async)]
fn deinstallation_aufraeumen(app: tauri::AppHandle) -> deinstallation::Aufraeumbericht {
    deinstallation::aufraeumen(&app)
}

#[tauri::command(async)]
fn deinstallation_starten(app: tauri::AppHandle) -> Result<(), String> {
    deinstallation::uninstaller_starten(&app)
}

#[tauri::command(async)]
fn wakeword_stand(app: tauri::AppHandle) -> Result<wakeword::WakewordStand, String> {
    wakeword::stand(&app)
}

/// Blockiert, bis gesprochen wurde — höchstens ~7,5 s (Energie-Tor in
/// `wakeword::aufnehmen`).
///
/// Hier stand „Tauri-Commands laufen auf eigenen Threads, das Fenster bleibt
/// bedienbar". **Das war falsch, und es war teuer.** Ein Command ohne
/// `(async)` läuft auf dem Hauptthread und blockiert damit die
/// tao-Ereignisschleife: solange er läuft, verarbeitet Windows für diesen
/// Prozess keine Fensternachrichten — kein Klick, kein X, kein Tray-Menü,
/// kein Hotkey. Der Satz stand hier, und in der Folge wanderte immer mehr
/// langlaufende Arbeit in Commands (Virenscan 120 s, Größenanalyse 60 s,
/// Papierkorb leeren). Seit dem 23.08.2026 trägt jeder dieser Commands
/// `(async)` — dann legt Tauri ihn auf den Blocking-Pool, und der Satz oben
/// stimmt endlich.
#[tauri::command(async)]
fn wakeword_aufnehmen(app: tauri::AppHandle, nummer: u8) -> Result<String, String> {
    wakeword::aufnehmen(&app, nummer)
}

/// Trainiert das Modell und merkt sich das Wort in der Konfiguration —
/// daran erkennt die App später, dass der Assistent inzwischen anders heißt,
/// und schlägt die Neukalibrierung vor.
#[tauri::command(async)]
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
#[tauri::command(async)]
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

#[tauri::command(async)]
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

/// Reicht eine Fensterarbeit an den Hauptthread weiter, statt sie im
/// aufrufenden Faden zu tun.
///
/// **Die Regel dahinter:** Fenster-*Getter* (`is_visible`, `is_focused`,
/// `title`, …) blockieren aus einem Nebenfaden so lange, bis die
/// Ereignisschleife antwortet. Läuft die Schleife gerade nicht, wartet der
/// Faden für immer — und wenn er dabei ein Schloss hält, wartet der Rest
/// mit. Am 23.08.2026 waren so zwei echte Deadlocks belegbar: der
/// Wake-Word-Faden gegen `wakeword_lauschen`, und der Hotkey-Faden (der das
/// Mutex des Kürzel-Plugins hält) gegen `hotkeys_setzen`.
///
/// `run_on_main_thread` reiht die Arbeit nur ein und kehrt sofort zurück.
/// Auf dem Hauptthread selbst blockiert ein Getter nie — dort *ist* er ja
/// die Schleife. Möglich ist das, weil keiner dieser drei Aufrufer ein
/// Ergebnis braucht: sie zeigen, verstecken und melden.
pub(crate) fn am_hauptthread(app: &tauri::AppHandle, arbeit: impl FnOnce(&tauri::AppHandle) + Send + 'static) {
    let kopie = app.clone();
    // Schlägt das Einreihen fehl, ist die Schleife bereits fort — dann gibt
    // es kein Fenster mehr, das man zeigen könnte, und Schweigen ist richtig.
    let _ = app.run_on_main_thread(move || arbeit(&kopie));
}

/// Fenster-Hotkey: Fenster nach vorn, wenn es nicht fokussiert ist — sonst
/// weg. So ist derselbe Griff „öffnen“ und „aus dem Weg“, wie man es von
/// Spotlight-artigen Overlays kennt.
///
/// Läuft **auf dem Hauptthread** (siehe `am_hauptthread`): der Hotkey-Faden
/// des Plugins darf hier nicht selbst fragen.
#[allow(dead_code)]
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
#[allow(dead_code)]
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
#[tauri::command(async)]
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
#[cfg(not(target_os = "android"))]
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
#[cfg(not(target_os = "android"))]
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
                    // Nicht direkt: dieser Rückruf läuft im Faden des
                    // Kürzel-Plugins und hält dessen Mutex.
                    am_hauptthread(app, hauptfenster_umschalten);
                }
            })
            .map_err(|e| format!("Hotkey „{kombi}“ nicht verfügbar: {e}"))?;
    }
    if let Some(kombi) = sprache {
        kuerzel
            .on_shortcut(hotkey_pruefen(kombi)?, |app, _s, ereignis| {
                if ereignis.state() == ShortcutState::Pressed {
                    am_hauptthread(app, sprachsitzung_umschalten);
                }
            })
            .map_err(|e| format!("Hotkey „{kombi}“ nicht verfügbar: {e}"))?;
    }
    Ok(())
}

#[cfg(target_os = "android")]
fn hotkeys_registrieren(
    _app: &tauri::AppHandle,
    _fenster: Option<&str>,
    _sprache: Option<&str>,
) -> Result<(), String> {
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
    #[cfg(not(target_os = "android"))]
    {
        if let Err(fehler) = hotkeys_registrieren(&app, fenster.as_deref(), sprache.as_deref()) {
            let _ = hotkeys_registrieren(
                &app,
                konfig.hotkey_fenster.as_deref(),
                konfig.hotkey_sprache.as_deref(),
            );
            return Err(fehler);
        }
    }
    konfig.hotkey_fenster = fenster;
    konfig.hotkey_sprache = sprache;
    konfig::speichern(&app, &konfig)
}

/// Beendet die App wirklich — der eine Ausgang des Schließen-Dialogs.
///
/// **Hart und nicht über die Ereignisschleife.** Hier stand `app.exit(0)`,
/// und das war der Grund, warum sich eine hängende App nicht mehr schließen
/// liess: `exit` legt die Bitte nur in die Warteschlange der Schleife
/// (`tauri-runtime-wry::request_exit` — ausdrücklich **ohne** die harte
/// Notbremse, weil `send_event` ja erfolgreich war). Steht die Schleife,
/// wird die Bitte nie abgeholt. Der Betreiber sah am 23.08.2026 genau das:
/// „ich kann es nicht schliessen, es läuft noch im Hintergrund".
///
/// Der eine Ausgang eines Programms darf nicht davon abhängen, dass das
/// Programm noch gesund ist. `cleanup_before_exit` räumt auf, was sich
/// aufräumen lässt (Tray-Icon aus der Leiste, Fenster zu); danach geht der
/// Prozess so oder so.
#[tauri::command]
fn app_beenden(app: tauri::AppHandle) {
    beenden_erzwingen(&app);
}

/// Der harte Ausgang, geteilt von Schließen-Dialog und Tray-Menü.
///
/// Steht bewusst hier und nicht in `tray.rs`: es gibt genau **einen** Weg
/// hinaus, und zwei Fassungen davon wären zwei Gelegenheiten, die falsche
/// zu erwischen.
pub(crate) fn beenden_erzwingen(app: &tauri::AppHandle) {
    // Das Mikrofon zuerst: der Lausch-Faden hält ein Gerät, und ein Prozess,
    // der stirbt, während cpal noch liest, hinterlässt unter Windows
    // gelegentlich eine belegte Aufnahmequelle. Das hier ist **bestenfalls**
    // ein Hinweis an den Faden: `lauschen_stoppen` setzt nur ein Flag, und
    // der Faden sieht es erst beim nächsten Audioblock — der Prozess ist
    // Mikrosekunden später fort. Garantiert wird davon nichts, und
    // garantiert werden darf es auch nicht: auf einen Faden zu warten hiesse,
    // den einen Ausgang von der Gesundheit der App abhängig zu machen.
    wakeword::lauschen_stoppen();
    // Und den Ton der anderen zurückdrehen. Die Originallautstärken leben
    // nur in diesem Prozess (`ducking::ORIGINALE`); wer hier ohne sie
    // hinausgeht, lässt Musik, Spiel und Discord dauerhaft auf 40 % zurück,
    // und niemand kann das danach noch auflösen — der Benutzer regelt jede
    // App von Hand hoch und errät den Zusammenhang nicht.
    //
    // In einem eigenen Faden mit kurzer Frist: `stoppen()` nimmt dasselbe
    // Schloss, das ein laufendes `starten()` über die COM-Enumeration und
    // die 200-ms-Rampe hält. Hängt WASAPI, wartet dieser Aufruf sonst
    // endlos — und der Fehler, wegen dem `app.exit` hier verschwand, wäre
    // zurück. Nach einer Sekunde geht es so oder so weiter.
    #[cfg(windows)]
    {
        let (fertig, warten) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let _ = ducking::stoppen();
            let _ = fertig.send(());
        });
        let _ = warten.recv_timeout(std::time::Duration::from_secs(1));
    }
    discord::beenden();
    app.cleanup_before_exit();
    std::process::exit(0);
}

/// Versteckt das Hauptfenster im Tray — der andere Ausgang.
#[tauri::command]
fn hauptfenster_verstecken(app: tauri::AppHandle) -> Result<(), String> {
    let fenster = app.get_webview_window("main").ok_or("Hauptfenster fehlt")?;
    fenster.hide().map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[allow(unused_mut)]
    let mut builder = tauri::Builder::default();

    #[cfg(not(target_os = "android"))]
    {
        // "--autostart" markiert Boot-Starts: Crash-Guard und sanfter Start
        // (spaetere Ausbaustufe) muessen wissen, ob ein Mensch gestartet hat.
        //
        // **Der Dateiname darf kein Leerzeichen enthalten.** Dieses Plugin
        // schreibt den Run-Schluessel der Registry **ungequotet**:
        // `C:\...\MauntingSmartSystem.exe --autostart`. Hiesse die Datei
        // "Maunting Smart System.exe", probierte CreateProcess zuerst
        // `Maunting.exe` und dann `Maunting Smart.exe` im selben Ordner — und
        // der NSIS-Standardordner unter LOCALAPPDATA ist vom Benutzer
        // beschreibbar. Wer dort eine `Maunting.exe` ablegen kann, wird beim
        // naechsten Anmelden statt MSS gestartet. Deshalb steht in
        // tauri.conf.json `"mainBinaryName": "MauntingSmartSystem"` ohne
        // Leerzeichen; die lesbare Beschriftung im Task-Manager kommt
        // ohnehin aus `productName` und nicht aus dem Dateinamen.
        builder = builder
            .plugin(tauri_plugin_autostart::init(
                MacosLauncher::LaunchAgent,
                Some(vec!["--autostart"]),
            ))
            // Die Handler hängen an den einzelnen Hotkeys (`on_shortcut` in
            // `hotkeys_registrieren`) — ein globaler Handler müsste selbst
            // auseinanderhalten, welcher der beiden gedrückt wurde.
            .plugin(tauri_plugin_global_shortcut::Builder::new().build())
            .plugin(tauri_plugin_updater::Builder::new().build());
    }

    builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
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
            desktop_aktion_bestaetigen,
            desktop_aktion_ablehnen,
            oeffne_browser,
            deinstallation_aufraeumen,
            deinstallation_starten,
            sandbox_verfuegbar,
            benachrichtigung_senden
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
            // Discord Rich Presence: wenn in der Konfiguration aktiv (Standard),
            // verbindet sich die Desktop App mit der lokalen Discord Named Pipe.
            if konfig.discord_rpc_aktiv {
                discord::starten(
                    konfig.discord_client_id,
                    konfig.discord_details,
                    konfig.discord_state,
                );
            }
            // Automatischer Updater: prüft asynchron im Hintergrund auf neuere GitHub Releases
            updater::pruefe_und_installiere_update_hintergrund(app.handle().clone());
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

#[cfg(all(test, not(target_os = "android")))]
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
