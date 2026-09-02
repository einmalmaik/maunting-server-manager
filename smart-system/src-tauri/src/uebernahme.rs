//! Maus, Tastatur, Bildschirm — und die Freigabe, ohne die nichts davon geht.
//!
//! **Die Freigabe liegt hier und nirgendwo sonst.** Nicht im Panel, nicht im
//! Modell, nicht in der Weboberflaeche: eine Frist im Speicher dieses
//! Prozesses. Die KI kann darum bitten (`desktop_steuern` mit
//! `aktion="freigabe"`), und der
//! Mensch am Rechner erteilt sie — jeder Aufruf von `desktop_steuern` davor
//! oder danach wird abgewiesen, ohne dass irgendwer etwas dagegen tun kann.
//!
//! Zwei Dinge, die Windows uns schenkt und die man kennen muss:
//!
//! * **UIPI** verhindert, dass ein nicht-erhoehter Prozess erhoehte Fenster
//!   bedient — Task-Manager, UAC-Abfragen, ein als Administrator gestarteter
//!   Editor. Die App wird deshalb bewusst nie erhoeht: das ist zugleich die
//!   natuerliche Schranke dagegen, dass die KI eine UAC-Abfrage wegklickt.
//!   Der Fehlschlag ist dem Modell als eigener Zustand zu melden, nie als
//!   Erfolg — sonst haelt es den Klick fuer erledigt.
//! * **Der sichere Desktop** (UAC, Strg+Alt+Entf, Sperrbildschirm) ist
//!   unerreichbar, und ein Bildschirmfoto davon ist schwarz.
//!
//! Nur der Hauptbildschirm: enigo rechnet absolute Koordinaten gegen
//! `SM_CXSCREEN`/`SM_CYSCREEN` und setzt kein `MOUSEEVENTF_VIRTUALDESK` — auf
//! einem Zweitmonitor landete ein Klick woanders als gedacht. Lieber eine
//! ehrliche Grenze als ein Klick an der falschen Stelle.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// Bis wann die Freigabe gilt. `None` heisst: keine.
static FREIGABE: Mutex<Option<Instant>> = Mutex::new(None);

/// Das Aeusserste, worum die KI bitten darf. Der Mensch darf weniger geben,
/// nie mehr — eine Freigabe „bis auf Widerruf" gibt es nicht.
pub const MAX_MINUTEN: u64 = 30;

pub fn freigeben(minuten: u64) -> Result<(), String> {
    let minuten = minuten.clamp(1, MAX_MINUTEN);
    let mut stand = FREIGABE.lock().map_err(|_| "Freigabe nicht lesbar")?;
    *stand = Some(Instant::now() + Duration::from_secs(minuten * 60));
    Ok(())
}

pub fn widerrufen() -> Result<(), String> {
    let mut stand = FREIGABE.lock().map_err(|_| "Freigabe nicht lesbar")?;
    *stand = None;
    Ok(())
}

/// Wie viele Sekunden die Freigabe noch laeuft — 0 heisst: keine.
pub fn restsekunden() -> u64 {
    let Ok(stand) = FREIGABE.lock() else {
        return 0;
    };
    match *stand {
        Some(bis) => bis
            .checked_duration_since(Instant::now())
            .map(|rest| rest.as_secs())
            .unwrap_or(0),
        None => 0,
    }
}

/// Der Punkt, den das Modell genannt hat — als Bildkoordinate.
///
/// Hier stand `as i32`, und das schnitt still ab: aus 4294967396 wurde 100.
/// Der Klick landete dann auf einem Punkt, den niemand genannt hat, und ging
/// als Erfolg zurueck.
#[cfg_attr(not(windows), allow(dead_code))]
fn bildpunkt(wert: i64, name: &str) -> Result<i32, String> {
    i32::try_from(wert).map_err(|_| {
        format!(
            "'{name}' liegt mit {wert} weit ausserhalb jedes Bildschirms. \
             Nenne Punkte im Raster des letzten Bildschirmfotos (dessen \
             Felder 'breite' und 'hoehe'), Ursprung links oben."
        )
    })
}

/// Liegt der umgerechnete Punkt ueberhaupt auf dem Hauptbildschirm?
///
/// Windows klemmt einen absoluten Zeiger stillschweigend an die Kante: aus
/// einem Punkt weit rechts wird der rechte Rand, und dort sitzen die
/// Taskleiste und das Schliessen-Kreuz eines maximierten Fensters. Genau so
/// endet ein Modell, das in der vollen Aufloesung antwortet statt im
/// gemeldeten Raster (bei 3840x2160 ein Faktor 3) — und gemeldet wurde
/// trotzdem "geklickt". Der Modulkopf verlangt das Gegenteil: ein Fehlschlag
/// ist als eigener Zustand zu melden, nie als Erfolg.
#[cfg_attr(not(windows), allow(dead_code))]
fn auf_dem_schirm((px, py): (i32, i32), (breite, hoehe): (i32, i32)) -> bool {
    px >= 0 && py >= 0 && px < breite && py < hoehe
}

fn pruefen() -> Result<(), String> {
    if restsekunden() == 0 {
        return Err(
            "Keine gueltige Freigabe fuer Maus und Tastatur. Bitte zuerst mit \
             desktop_steuern (aktion=\"freigabe\") darum, und warte die \
             Antwort des Benutzers ab — abgelaufene Freigaben leben nicht \
             wieder auf. Ansehen geht auch ohne: desktop_system mit \
             aktion=\"bildschirm\"."
                .into(),
        );
    }
    Ok(())
}

#[cfg(windows)]
mod windows_impl {
    use super::*;
    use enigo::{
        Axis, Button, Coordinate, Direction, Enigo, Key, Keyboard, Mouse, Settings,
    };

    fn maschine() -> Result<Enigo, String> {
        // `release_keys_when_dropped` ist Vorgabe und bleibt an: sonst bliebe
        // eine Modifiertaste haengen, wenn ein Aufruf mittendrin scheitert —
        // und der Benutzer saesse vor einem Rechner mit gedruecktem Strg.
        Enigo::new(&Settings::default()).map_err(|e| format!("Eingabe nicht moeglich: {e}"))
    }

    fn taste(name: &str) -> Option<Key> {
        match name.to_ascii_lowercase().as_str() {
            "ctrl" | "control" | "strg" => Some(Key::Control),
            "alt" => Some(Key::Alt),
            "altgr" => Some(Key::Alt),
            "shift" | "umschalt" => Some(Key::Shift),
            "super" | "win" | "meta" => Some(Key::Meta),
            "return" | "enter" | "eingabe" => Some(Key::Return),
            "tab" => Some(Key::Tab),
            "escape" | "esc" => Some(Key::Escape),
            "space" | "leertaste" => Some(Key::Space),
            "backspace" | "rueck" => Some(Key::Backspace),
            "delete" | "entf" => Some(Key::Delete),
            "up" | "hoch" => Some(Key::UpArrow),
            "down" | "runter" => Some(Key::DownArrow),
            "left" | "links" => Some(Key::LeftArrow),
            "right" | "rechts" => Some(Key::RightArrow),
            "home" | "pos1" => Some(Key::Home),
            "end" | "ende" => Some(Key::End),
            "pageup" | "bildauf" => Some(Key::PageUp),
            "pagedown" | "bildab" => Some(Key::PageDown),
            "f1" => Some(Key::F1),
            "f2" => Some(Key::F2),
            "f3" => Some(Key::F3),
            "f4" => Some(Key::F4),
            "f5" => Some(Key::F5),
            "f6" => Some(Key::F6),
            "f7" => Some(Key::F7),
            "f8" => Some(Key::F8),
            "f9" => Some(Key::F9),
            "f10" => Some(Key::F10),
            "f11" => Some(Key::F11),
            "f12" => Some(Key::F12),
            einzeln => {
                let mut zeichen = einzeln.chars();
                match (zeichen.next(), zeichen.next()) {
                    (Some(c), None) => Some(Key::Unicode(c)),
                    _ => None,
                }
            }
        }
    }

    fn tastenfolge(enigo: &mut Enigo, folge: &str) -> Result<(), String> {
        let teile: Vec<&str> = folge.split('+').filter(|t| !t.is_empty()).collect();
        if teile.is_empty() {
            return Err("Leere Tastenfolge".into());
        }
        let (letzte, modifier) = teile.split_last().unwrap();
        let mut gedrueckt = Vec::new();
        for name in modifier {
            let key = taste(name).ok_or_else(|| format!("Unbekannte Taste: '{name}'"))?;
            enigo
                .key(key, Direction::Press)
                .map_err(|e| format!("Taste nicht druckbar: {e}"))?;
            gedrueckt.push(key);
        }
        let key = taste(letzte).ok_or_else(|| format!("Unbekannte Taste: '{letzte}'"))?;
        let ergebnis = enigo
            .key(key, Direction::Click)
            .map_err(|e| format!("Taste nicht druckbar: {e}"));
        // Die Modifier in jedem Fall wieder loslassen, auch nach einem
        // Fehlschlag. Sonst bleibt Strg gedrueckt, und der naechste
        // Tastendruck des *Benutzers* tut etwas anderes als gedacht.
        for key in gedrueckt.into_iter().rev() {
            let _ = enigo.key(key, Direction::Release);
        }
        ergebnis
    }

    /// Hält eine Tastenkombination (z. B. "W", "Shift+W", "Ctrl+Space") für die angegebene Zeitspanne gedrückt.
    fn tasten_halten(enigo: &mut Enigo, folge: &str, dauer_ms: u64) -> Result<(), String> {
        let teile: Vec<&str> = folge.split('+').filter(|t| !t.is_empty()).collect();
        if teile.is_empty() {
            return Err("Leere Tastenfolge".into());
        }
        let dauer = Duration::from_millis(dauer_ms.clamp(10, 10000));
        let mut gedrueckt = Vec::new();
        let mut fehler = None;
        for name in &teile {
            match taste(name) {
                Some(key) => {
                    if let Err(e) = enigo.key(key, Direction::Press) {
                        fehler = Some(format!("Taste '{name}' nicht drueckbar: {e}"));
                        break;
                    }
                    gedrueckt.push(key);
                }
                None => {
                    fehler = Some(format!("Unbekannte Taste: '{name}'"));
                    break;
                }
            }
        }
        if fehler.is_none() {
            std::thread::sleep(dauer);
        }
        // Alle Tasten in umgekehrter Reihenfolge sicher wieder loslassen
        for key in gedrueckt.into_iter().rev() {
            let _ = enigo.key(key, Direction::Release);
        }
        if let Some(err) = fehler {
            return Err(err);
        }
        Ok(())
    }

    /// Hält eine Maustaste für eine bestimmte Zeit gedrückt (z. B. Dauerfeuer, Aufladen, Ziehen).
    fn maus_halten(enigo: &mut Enigo, knopf: Button, dauer_ms: u64) -> Result<(), String> {
        let dauer = Duration::from_millis(dauer_ms.clamp(10, 10000));
        enigo
            .button(knopf, Direction::Press)
            .map_err(|e| format!("Maustaste nicht drueckbar: {e}"))?;
        std::thread::sleep(dauer);
        let _ = enigo.button(knopf, Direction::Release);
        Ok(())
    }

    /// Führt relative Mausbewegungen aus (z. B. 3D-Kamerasteuerung in Spielen).
    fn maus_relativ(enigo: &mut Enigo, dx: i64, dy: i64) -> Result<(), String> {
        let dx = i32::try_from(dx).unwrap_or(0).clamp(-2000, 2000);
        let dy = i32::try_from(dy).unwrap_or(0).clamp(-2000, 2000);
        enigo
            .move_mouse(dx, dy, Coordinate::Rel)
            .map_err(|e| format!("Relative Mausbewegung nicht moeglich: {e}"))?;
        Ok(())
    }

    /// Rechnet Bildkoordinaten in echte Bildschirmpunkte zurueck.
    ///
    /// Kommt seit dem 23.08.2026 aus `bildschirm.rs`. Zwei Fassungen
    /// derselben Skalierung waeren der Fehler, der einen Klick an die falsche
    /// Stelle setzt: das Modell antwortet in den Koordinaten des
    /// verkleinerten Bildes, und nur wer denselben Faktor benutzt, findet den
    /// echten Punkt wieder.
    fn echte_punkte(x: i32, y: i32) -> Result<(i32, i32), String> {
        crate::bildschirm::echte_punkte(x, y)
    }

    fn zeigen(enigo: &mut Enigo, x: Option<i64>, y: Option<i64>) -> Result<(), String> {
        let (Some(x), Some(y)) = (x, y) else {
            return Ok(());
        };
        let (x, y) = (bildpunkt(x, "x")?, bildpunkt(y, "y")?);
        let (px, py) = echte_punkte(x, y)?;
        // Gegen dieselben Masse, gegen die enigo gleich rechnet
        // (`SM_CXSCREEN`/`SM_CYSCREEN`) — was daran vorbeizeigt, klemmt
        // Windows an die Kante, statt es abzulehnen.
        let schirm = enigo
            .main_display()
            .map_err(|e| format!("Bildschirmmasse nicht lesbar: {e}"))?;
        if !auf_dem_schirm((px, py), schirm) {
            return Err(format!(
                "Der Punkt ({x}|{y}) liegt nicht auf dem Bildschirm: \
                 umgerechnet waere das ({px}|{py}), der Hauptbildschirm misst \
                 aber {}x{} Punkte. Die Koordinaten gehoeren in das Raster \
                 des Bildschirmfotos (Felder 'breite' und 'hoehe' der \
                 Aufnahme), nicht in die volle Aufloesung. Es wurde nichts \
                 angefasst — mach ein neues Foto und nenne den Punkt darin.",
                schirm.0, schirm.1
            ));
        }
        enigo
            .move_mouse(px, py, Coordinate::Abs)
            .map_err(|e| format!("Maus nicht bewegbar: {e}"))
    }

    pub fn ausfuehren(aktion: &str, argumente: &Value) -> Result<Value, String> {
        if aktion == "warten" {
            let sekunden = argumente["menge"].as_u64().unwrap_or(1).clamp(1, 30);
            std::thread::sleep(Duration::from_secs(sekunden));
            return Ok(json!({ "gewartet": sekunden }));
        }

        let x = argumente["x"].as_i64();
        let y = argumente["y"].as_i64();
        let text = argumente["text"].as_str().unwrap_or("");
        let mut enigo = maschine()?;

        match aktion {
            "maus_bewegen" => {
                zeigen(&mut enigo, x, y)?;
                Ok(json!({ "bewegt": true }))
            }
            "maus_relativ" | "kamera_drehen" => {
                let dx = argumente["dx"].as_i64().unwrap_or(0);
                let dy = argumente["dy"].as_i64().unwrap_or(0);
                maus_relativ(&mut enigo, dx, dy)?;
                Ok(json!({ "relativ_bewegt": true, "dx": dx, "dy": dy }))
            }
            "klick" | "doppelklick" | "rechtsklick" => {
                zeigen(&mut enigo, x, y)?;
                let knopf = if aktion == "rechtsklick" {
                    Button::Right
                } else {
                    Button::Left
                };
                let male = if aktion == "doppelklick" { 2 } else { 1 };
                for _ in 0..male {
                    enigo
                        .button(knopf, Direction::Click)
                        .map_err(|e| format!("Klick nicht moeglich: {e}"))?;
                }
                Ok(json!({ "geklickt": aktion }))
            }
            "maus_halten" => {
                zeigen(&mut enigo, x, y)?;
                let knopf_str = argumente["knopf"].as_str().unwrap_or("links");
                let knopf = match knopf_str {
                    "rechts" | "right" => Button::Right,
                    "mitte" | "middle" => Button::Middle,
                    _ => Button::Left,
                };
                let dauer_ms = argumente["dauer_ms"].as_u64().unwrap_or(500);
                maus_halten(&mut enigo, knopf, dauer_ms)?;
                Ok(json!({ "maus_gehalten": knopf_str, "dauer_ms": dauer_ms }))
            }
            "tippen" => {
                if text.is_empty() {
                    return Err("Nichts zu tippen: 'text' fehlt".into());
                }
                // `text()` sendet Unicode-Ereignisse und umgeht das
                // Tastaturlayout. Anwendungen, die Scancodes lesen (Spiele),
                // sehen das nicht — fuer Tastenkuerzel gibt es 'taste'.
                enigo
                    .text(text)
                    .map_err(|e| format!("Text nicht tippbar: {e}"))?;
                Ok(json!({ "getippt": text.chars().count() }))
            }
            "taste" => {
                if text.is_empty() {
                    return Err("Keine Taste angegeben: 'text' fehlt".into());
                }
                tastenfolge(&mut enigo, text)?;
                Ok(json!({ "gedrueckt": text }))
            }
            "taste_halten" => {
                let taste_text = if !text.is_empty() {
                    text
                } else {
                    argumente["tasten"].as_str().unwrap_or("")
                };
                if taste_text.is_empty() {
                    return Err("Keine Tasten angegeben: 'text' oder 'tasten' fehlt".into());
                }
                let dauer_ms = argumente["dauer_ms"].as_u64().unwrap_or(500);
                tasten_halten(&mut enigo, taste_text, dauer_ms)?;
                Ok(json!({ "gehalten": taste_text, "dauer_ms": dauer_ms }))
            }
            "scrollen" => {
                zeigen(&mut enigo, x, y)?;
                let menge = argumente["menge"].as_i64().unwrap_or(3).clamp(-20, 20) as i32;
                enigo
                    .scroll(menge, Axis::Vertical)
                    .map_err(|e| format!("Nicht scrollbar: {e}"))?;
                Ok(json!({ "gescrollt": menge }))
            }
            andere => Err(format!("Unbekannte Aktion: '{andere}'")),
        }
    }
}

#[cfg(not(windows))]
mod windows_impl {
    use super::*;

    pub fn ausfuehren(_aktion: &str, _argumente: &Value) -> Result<Value, String> {
        Err("Die Uebernahme gibt es bisher nur unter Windows".into())
    }
}

/// Der eine Eingang. Ohne autonomen Modus setzt alles darin eine gueltige
/// Freigabe voraus.
///
/// **Der autonome Modus ist die Freigabe.** Die Regel des Betreibers gilt
/// hier wie ueberall: autonomer Modus an, keine Bestaetigung; autonomer Modus
/// aus, immer eine. Eine Karte *je Klick* waere die Alternative gewesen, und
/// die waere unbenutzbar — ein Formular auszufuellen sind zwanzig Klicks.
///
/// Das Feld setzt das Panel (`_desktop_argumente`), nicht das Modell; fehlt
/// es, wird gefragt. Die Uhr in `FREIGABE` laeuft dabei bewusst **nicht** mit:
/// wird der autonome Modus mitten im Lauf ausgeschaltet, fragt der naechste
/// Klick — und nicht erst in fuenf Minuten.
pub fn steuern(argumente: &Value) -> Result<Value, String> {
    if argumente["autonom"].as_bool() != Some(true) {
        pruefen()?;
    }
    let aktion = argumente["aktion"].as_str().unwrap_or("");
    let ergebnis = windows_impl::ausfuehren(aktion, argumente)?;
    Ok(ergebnis)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ohne_freigabe_geht_nichts() {
        widerrufen().unwrap();
        // Bewusst `klick` und nicht mehr `bildschirm`: sehen darf die KI
        // seit dem 23.08.2026 ohne Freigabe (`desktop_system`), steuern nicht.
        let fehler = steuern(&json!({ "aktion": "klick" })).unwrap_err();
        assert!(fehler.contains("Keine gueltige Freigabe"), "{fehler}");
    }

    #[test]
    fn der_autonome_modus_ist_die_freigabe() {
        widerrufen().unwrap();
        // Nicht `is_ok()`: unter Windows klickt das hier wirklich, im
        // CI-Container gibt es gar keine Maus. Geprueft wird das eine, worum
        // es geht — die Schranke greift nicht mehr.
        let ergebnis = steuern(&json!({ "aktion": "klick", "autonom": true }));
        if let Err(fehler) = ergebnis {
            assert!(!fehler.contains("Keine gueltige Freigabe"), "{fehler}");
        }
        // Und die Uhr laeuft trotzdem nicht: sonst klickte es weiter, wenn der
        // autonome Modus gleich danach ausgeht.
        assert_eq!(restsekunden(), 0);
    }

    #[test]
    fn freigabe_laeuft_und_laesst_sich_widerrufen() {
        freigeben(5).unwrap();
        assert!(restsekunden() > 0);
        widerrufen().unwrap();
        assert_eq!(restsekunden(), 0);
    }

    #[test]
    fn ein_punkt_neben_dem_schirm_wird_nicht_geklickt() {
        // Der Fall aus der Praxis: das Modell antwortet in der vollen
        // Aufloesung (3840x2160) statt im gemeldeten Raster (1280x720). Der
        // Rueckweg rechnet den Punkt noch einmal um Faktor 3 hoch, Windows
        // klemmt ihn an die Kante — und dort sitzen Taskleiste und
        // Schliessen-Kreuz. Gemeldet wurde trotzdem "geklickt".
        let schirm = (3840, 2160);
        assert!(auf_dem_schirm((0, 0), schirm));
        assert!(auf_dem_schirm((3839, 2159), schirm));
        assert!(!auf_dem_schirm((3840, 100), schirm));
        assert!(!auf_dem_schirm((11517, 6477), schirm));
        assert!(!auf_dem_schirm((-1, 100), schirm));
        assert!(!auf_dem_schirm((100, -1), schirm));
    }

    #[test]
    fn ein_riesiger_wert_wird_nicht_stillschweigend_abgeschnitten() {
        // `as i32` machte aus 4294967396 die Zahl 100 — ein Klick auf einen
        // Punkt, den niemand genannt hat, und niemand haette es gemerkt.
        assert_eq!(bildpunkt(100, "x").unwrap(), 100);
        let fehler = bildpunkt(4_294_967_396, "x").unwrap_err();
        assert!(fehler.contains("ausserhalb"), "{fehler}");
        assert!(fehler.contains('x'), "{fehler}");
    }

    #[test]
    fn die_bitte_kann_nicht_mehr_verlangen_als_erlaubt() {
        // Auch eine Bitte um 999 Minuten endet bei der Hausgrenze.
        freigeben(999).unwrap();
        assert!(restsekunden() <= MAX_MINUTEN * 60);
        widerrufen().unwrap();
    }

    #[test]
    fn taste_halten_und_maus_relativ_verlangen_freigabe_oder_autonom() {
        widerrufen().unwrap();
        let fehler_taste = steuern(&json!({
            "aktion": "taste_halten",
            "text": "w",
            "dauer_ms": 100
        })).unwrap_err();
        assert!(fehler_taste.contains("Keine gueltige Freigabe"));

        let fehler_maus = steuern(&json!({
            "aktion": "maus_relativ",
            "dx": 50,
            "dy": -50
        })).unwrap_err();
        assert!(fehler_maus.contains("Keine gueltige Freigabe"));
    }
}
