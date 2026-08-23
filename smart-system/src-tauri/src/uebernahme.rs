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
        let (px, py) = echte_punkte(x as i32, y as i32)?;
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

/// Der eine Eingang. Alles darin setzt eine gueltige Freigabe voraus — auch
/// das Bildschirmfoto: was auf dem Schirm steht, ist so privat wie das, was
/// man darauf tippt.
pub fn steuern(argumente: &Value) -> Result<Value, String> {
    pruefen()?;
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
    fn freigabe_laeuft_und_laesst_sich_widerrufen() {
        freigeben(5).unwrap();
        assert!(restsekunden() > 0);
        widerrufen().unwrap();
        assert_eq!(restsekunden(), 0);
    }

    #[test]
    fn die_bitte_kann_nicht_mehr_verlangen_als_erlaubt() {
        // Auch eine Bitte um 999 Minuten endet bei der Hausgrenze.
        freigeben(999).unwrap();
        assert!(restsekunden() <= MAX_MINUTEN * 60);
        widerrufen().unwrap();
    }
}
