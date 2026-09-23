//! Das Overlay lässt Klicks durch — bis auf das, was man anklicken soll.
//!
//! Seit der Schwarm frei über dem Desktop schwebt (09/2026), sieht man von
//! den 480 × 380 Pixeln des Overlays nur den Schwarm und die Untertitel.
//! Windows gibt einem durchsichtigen Fenster trotzdem jeden Klick in seinem
//! Rechteck: Mitten auf dem Bildschirm lag so ein unsichtbarer Riegel über
//! dem, was darunter liegt. Früher sah man ihn wenigstens — eine dunkle Karte.
//!
//! Klickdurchlässig ist ein Fenster aber nur als Ganzes
//! (`set_ignore_cursor_events`). Deshalb verfolgt eine Wache den Zeiger,
//! solange das Overlay zu sehen ist: Über einer Trefferfläche, die das
//! Frontend meldet (heute nur das X), nimmt das Fenster Klicks an, überall
//! sonst gehen sie hindurch. Die Wache schaut 30-mal in der Sekunde nach —
//! auf dem Hauptthread, wo kein Fenster-Getter blockiert (`am_hauptthread`).
//!
//! Ziehen ließ sich das Overlay übrigens nie: `data-tauri-drag-region` ruft
//! `start_dragging`, und das erlaubt keine Capability (am gebauten Overlay
//! geprüft: „not allowed by ACL").

use std::sync::Mutex;

/// Trefferflächen in CSS-Pixeln des Overlays: `[links, oben, breite, höhe]`.
static FLAECHEN: Mutex<Vec<[f64; 4]>> = Mutex::new(Vec::new());

/// Das Frontend meldet, wo das Overlay Klicks annehmen soll — jedes Mal,
/// wenn es sich zeigt oder seine Größe ändert. Erst damit beginnt die Wache:
/// Bis dahin bleibt das Fenster, wie es war, ganz anklickbar. Ein X, das
/// niemand treffen kann, wäre schlimmer als der Riegel.
#[tauri::command]
pub fn overlay_trefferflaechen(app: tauri::AppHandle, flaechen: Vec<[f64; 4]>) {
    if let Ok(mut jetzt) = FLAECHEN.lock() {
        *jetzt = gueltige(flaechen);
    }
    #[cfg(not(target_os = "android"))]
    wache::starten(&app);
    // In der Android-App liegt das Overlay im Hauptfenster und lässt
    // Berührungen über `pointer-events` durch.
    #[cfg(target_os = "android")]
    let _ = app;
}

#[cfg_attr(target_os = "android", allow(dead_code))]
fn gueltige(flaechen: Vec<[f64; 4]>) -> Vec<[f64; 4]> {
    flaechen
        .into_iter()
        .filter(|f| f.iter().all(|wert| wert.is_finite()) && f[2] > 0.0 && f[3] > 0.0)
        .collect()
}

/// Liegt der Punkt (CSS-Pixel des Fensters) auf einer der Flächen?
#[cfg_attr(target_os = "android", allow(dead_code))]
fn trifft(flaechen: &[[f64; 4]], x: f64, y: f64) -> bool {
    flaechen
        .iter()
        .any(|&[links, oben, breite, hoehe]| x >= links && x < links + breite && y >= oben && y < oben + hoehe)
}

/// Der Zeiger in CSS-Pixeln des Fensters: Bildschirmpixel ab der Ecke der
/// Fensterfläche, geteilt durch die Skalierung ihres Monitors.
#[cfg_attr(target_os = "android", allow(dead_code))]
fn im_fenster(zeiger: (f64, f64), ecke: (i32, i32), faktor: f64) -> (f64, f64) {
    ((zeiger.0 - f64::from(ecke.0)) / faktor, (zeiger.1 - f64::from(ecke.1)) / faktor)
}

#[cfg(not(target_os = "android"))]
mod wache {
    use std::sync::atomic::{AtomicU64, AtomicU8, Ordering};
    use std::time::Duration;

    use tauri::Manager;

    use super::{im_fenster, trifft, FLAECHEN};

    /// Nummer der laufenden Wache; 0 heißt: keine.
    static WACHE: AtomicU64 = AtomicU64::new(0);
    static NAECHSTE: AtomicU64 = AtomicU64::new(1);
    /// Was zuletzt gesetzt wurde: 0 unbekannt, 1 durchlässig, 2 fest.
    static ZULETZT: AtomicU8 = AtomicU8::new(0);

    const TAKT: Duration = Duration::from_millis(33);

    pub(super) fn starten(app: &tauri::AppHandle) {
        if WACHE.load(Ordering::SeqCst) != 0 {
            return;
        }
        let nummer = NAECHSTE.fetch_add(1, Ordering::SeqCst);
        WACHE.store(nummer, Ordering::SeqCst);
        let app = app.clone();
        std::thread::spawn(move || {
            while WACHE.load(Ordering::SeqCst) == nummer {
                std::thread::sleep(TAKT);
                let kopie = app.clone();
                // Schlägt das Einreihen fehl, ist die Schleife fort — und mit
                // ihr das Fenster.
                if app.run_on_main_thread(move || pruefen(&kopie, nummer)).is_err() {
                    break;
                }
            }
        });
    }

    /// Auf dem Hauptthread: Liegt der Zeiger über einer Trefferfläche?
    fn pruefen(app: &tauri::AppHandle, nummer: u64) {
        if WACHE.load(Ordering::SeqCst) != nummer {
            return;
        }
        let Some(fenster) = app.get_webview_window("overlay") else {
            beenden(nummer);
            return;
        };
        if !fenster.is_visible().unwrap_or(false) {
            // Versteckt ruht die Wache, und das Fenster zeigt sich beim
            // nächsten Mal ganz anklickbar, bis das Frontend wieder meldet.
            let _ = fenster.set_ignore_cursor_events(false);
            ZULETZT.store(0, Ordering::SeqCst);
            beenden(nummer);
            return;
        }
        let (Ok(zeiger), Ok(ecke), Ok(faktor)) = (
            app.cursor_position(),
            fenster.inner_position(),
            fenster.scale_factor(),
        ) else {
            return;
        };
        let (x, y) = im_fenster((zeiger.x, zeiger.y), (ecke.x, ecke.y), faktor);
        let drauf = FLAECHEN.lock().map(|flaechen| trifft(&flaechen, x, y)).unwrap_or(true);
        let soll = if drauf { 2 } else { 1 };
        if ZULETZT.swap(soll, Ordering::SeqCst) != soll && fenster.set_ignore_cursor_events(!drauf).is_err() {
            // Beim nächsten Takt noch einmal.
            ZULETZT.store(0, Ordering::SeqCst);
        }
    }

    fn beenden(nummer: u64) {
        let _ = WACHE.compare_exchange(nummer, 0, Ordering::SeqCst, Ordering::SeqCst);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Das X des Overlays: 32 × 32 oben rechts, mit 4 px Rand gemeldet.
    const X: [f64; 4] = [432.0, 8.0, 40.0, 40.0];

    #[test]
    fn nur_das_x_nimmt_klicks_an() {
        assert!(trifft(&[X], 450.0, 20.0));
        // Die Mitte, wo der Schwarm schwebt, und der leere Rand lassen durch.
        assert!(!trifft(&[X], 240.0, 170.0));
        assert!(!trifft(&[X], 5.0, 375.0));
        // Rechts und unten gehört die Kante nicht mehr dazu.
        assert!(!trifft(&[X], 472.0, 20.0));
        assert!(!trifft(&[], 450.0, 20.0));
    }

    #[test]
    fn rechnet_bildschirmpixel_in_css_pixel_des_fensters() {
        // 150 % Skalierung, Fenster bei (700, 400): der Zeiger bei (1375, 430)
        // liegt 450 × 20 CSS-Pixel in der Fläche — auf dem X.
        let (x, y) = im_fenster((1375.0, 430.0), (700, 400), 1.5);
        assert!((x - 450.0).abs() < 1e-9 && (y - 20.0).abs() < 1e-9);
        assert!(trifft(&[X], x, y));
        // Auf einem zweiten Monitor links vom ersten ist die Ecke negativ.
        let (x, y) = im_fenster((-1470.0, 30.0), (-1920, 10), 1.0);
        assert!(trifft(&[X], x, y));
    }

    #[test]
    fn verwirft_flaechen_ohne_ausdehnung_oder_mit_unsinn() {
        let flaechen = gueltige(vec![X, [0.0, 0.0, 0.0, 10.0], [f64::NAN, 0.0, 5.0, 5.0], [0.0, 0.0, 5.0, f64::INFINITY]]);
        assert_eq!(flaechen, vec![X]);
    }
}
