//! Das Bildschirmfoto — die Aufnahme selbst, ohne Schranke davor.
//!
//! **Herkunft.** Dieser Code stand bis zum 23.08.2026 in `uebernahme.rs`
//! (`windows_impl::bildschirm` und `windows_impl::echte_punkte`) und lag damit
//! hinter der Freigabe fuer Maus und Tastatur: wer sehen wollte, musste erst
//! um Steuerung bitten und einen Menschen klicken lassen. Der Betreiber hat
//! das umgedreht — sehen darf die KI jederzeit, *gesteuert* wird weiterhin
//! nur nach Klick. Deshalb zieht die Aufnahme hierher, und `uebernahme.rs`
//! holt seine Aktion `bildschirm` und seine Koordinatenrechnung kuenftig von
//! hier, statt eine zweite Fassung zu pflegen. Zwei Fassungen derselben
//! Skalierung waeren genau der Fehler, der einen Klick an die falsche Stelle
//! setzt: das Modell antwortet in den Koordinaten des verkleinerten Bildes,
//! und nur wer weiss, wie stark verkleinert wurde, findet den echten Punkt
//! wieder. Aus demselben Grund liegt `echte_punkte` hier und nicht dort.
//!
//! Beim Umzug hat sich am Verhalten nichts geaendert — dieselbe xcap-Aufnahme,
//! dieselbe Verkleinerung auf `MAX_KANTE`, dieselben Rueckgabefelder. Nur das
//! Bildformat wurde noch am selben Tag getauscht (PNG → JPEG), weil das PNG
//! die Bruecke zum Panel nie passiert hat; die Begruendung steht bei der
//! Kodierung. Zwei Ausdruecke haben ausserdem einen Namen bekommen
//! (`verkleinerung`, `zurueckrechnen`): dieselbe Rechnung stand vorher zweimal
//! da, und ein Test kann sie jetzt festhalten, statt sie zu glauben.
//!
//! Nur der Hauptbildschirm — dieselbe ehrliche Grenze wie bei der Maus (enigo
//! rechnet absolute Koordinaten gegen `SM_CXSCREEN`/`SM_CYSCREEN`). Und der
//! sichere Desktop von Windows (UAC, Strg+Alt+Entf, Sperrbildschirm) bleibt
//! schwarz; das ist kein Fehler, sondern die Zusage des Betriebssystems.
//!
//! **Der Indikator gehoert zur Aufnahme, nicht zum Aufrufer.** `aufnehmen`
//! nimmt deshalb einen `AppHandle` entgegen, den es fuer das Foto selbst gar
//! nicht braucht: er ist nur da, um `sichtfeld::zeigen` von hier aus
//! aufzurufen. Stuende die Zeile stattdessen beim Werkzeug, koennte ein
//! zweiter Aufrufer sie vergessen — und genau das ist die Art, wie solche
//! Zusagen still verschwinden. Hier kann er es nicht.

use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::AppHandle;

/// Human Error Guard: Schutz vor unbeabsichtigter Erfassung des Passwort-Managers
/// bei Bildschirmaufnahmen für Computer-Use.
pub static TRESOR_SCHUTZ_AKTIV: AtomicBool = AtomicBool::new(false);

pub fn setze_tresor_schutz_zustand(aktiv: bool) {
    TRESOR_SCHUTZ_AKTIV.store(aktiv, Ordering::SeqCst);
}

pub fn ist_tresor_schutz_aktiv() -> bool {
    TRESOR_SCHUTZ_AKTIV.load(Ordering::SeqCst)
}

/// Laengste Kante eines Bildschirmfotos. Die Anbieter skalieren nicht
/// selbst — sie weisen zu grosse Bilder ab. Und ein Vollbild kostet Tokens,
/// ohne mehr zu zeigen: 1280 Punkte sind lesbar.
pub const MAX_KANTE: u32 = 1280;

/// JPEG-Qualitaet des Bildschirmfotos. 75 ist der uebliche Punkt, an dem
/// Text noch sauber lesbar ist und die Datei klein bleibt; darueber waechst
/// sie schnell, ohne dass ein Modell mehr erkennt.
#[cfg_attr(not(windows), allow(dead_code))]
const JPEG_QUALITAET: u8 = 75;

/// Um wie viel ein Bild dieser Groesse kleiner wird. Nie ueber 1.0: ein
/// kleiner Bildschirm wird nicht hochgerechnet — das kostete Tokens und
/// zeigte keinen Punkt mehr.
#[cfg_attr(not(windows), allow(dead_code))]
fn verkleinerung(breite: u32, hoehe: u32) -> f32 {
    (MAX_KANTE as f32 / breite.max(hoehe) as f32).min(1.0)
}

/// Der Rueckweg: aus einem Punkt im verkleinerten Bild wird ein Punkt auf dem
/// Bildschirm. Gerechnet wird hier, damit das Modell nicht rechnen muss —
/// eine Skalierung, die es selbst anwenden soll, wendet es irgendwann falsch
/// an, und ein Klick daneben ist teurer als jede eingesparte Zeile.
#[cfg_attr(not(windows), allow(dead_code))]
fn zurueckrechnen(x: i32, y: i32, faktor: f32) -> (i32, i32) {
    (
        (x as f32 / faktor).round() as i32,
        (y as f32 / faktor).round() as i32,
    )
}

#[cfg(windows)]
mod aufnahme_impl {
    use super::{verkleinerung, zurueckrechnen, JPEG_QUALITAET};
    use base64::Engine;
    use serde_json::{json, Value};

    /// Der eine Bildschirm, um den es geht. Aufnahme und Rueckrechnung muessen
    /// denselben meinen, sonst passt der Faktor nicht zum Bild.
    fn hauptbildschirm() -> Result<xcap::Monitor, String> {
        let monitore = xcap::Monitor::all().map_err(|e| format!("Kein Bildschirm: {e}"))?;
        monitore
            .into_iter()
            .find(|m| m.is_primary().unwrap_or(false))
            .ok_or_else(|| "Kein Hauptbildschirm gefunden".to_string())
    }

    pub fn aufnehmen(app: Option<&tauri::AppHandle>) -> Result<Value, String> {
        let monitor = hauptbildschirm()?;
        let mut bild = monitor
            .capture_image()
            .map_err(|e| format!("Bildschirmfoto fehlgeschlagen: {e}"))?;

        // Human Error Guard: Wenn der Passwort-Manager aktiv ist,
        // wird der Bereich des App-Fensters im Bildschirmfoto geschwärzt.
        if super::ist_tresor_schutz_aktiv() {
            if let Some(app) = app {
                use tauri::Manager;
                if let Some(main_window) = app.get_webview_window("main") {
                    if let (Ok(pos), Ok(size)) = (main_window.outer_position(), main_window.outer_size()) {
                    let win_x = pos.x.max(0) as u32;
                    let win_y = pos.y.max(0) as u32;
                    let win_w = size.width;
                    let win_h = size.height;

                    let img_w = bild.width();
                    let img_h = bild.height();

                    let end_x = (win_x + win_w).min(img_w);
                    let end_y = (win_y + win_h).min(img_h);

                        for y in win_y..end_y {
                            for x in win_x..end_x {
                                bild.put_pixel(x, y, image::Rgba([0, 0, 0, 255]));
                            }
                        }
                    }
                }
            }
        }

        let (breite, hoehe) = (bild.width(), bild.height());
        let faktor = verkleinerung(breite, hoehe);
        let klein = if faktor < 1.0 {
            image::imageops::resize(
                &bild,
                (breite as f32 * faktor) as u32,
                (hoehe as f32 * faktor) as u32,
                image::imageops::FilterType::Triangle,
            )
        } else {
            bild
        };

        // **JPEG und nicht PNG**, und das ist der Unterschied zwischen
        // "funktioniert" und "kommt nie an". Ein Desktop mit Text und Fenstern
        // wird als PNG (verlustfrei) 300 KB bis 1,5 MB gross; base64 macht ein
        // Drittel mehr daraus, und die Bruecke zum Panel liess bis zum
        // 23.08.2026 nur 200.000 Zeichen durch — jedes Bildschirmfoto wurde
        // abgewiesen, und die KI sah nie etwas. Dasselbe Bild als JPEG bei
        // Qualitaet 75 sind 40 bis 150 KB.
        //
        // Der Qualitaetsverlust kostet hier nichts: das Bild wird ohnehin auf
        // MAX_KANTE verkleinert, und ein Modell soll darauf Fenster, Knoepfe
        // und Text erkennen — nicht Pixel vergleichen.
        //
        // `to_rgb8`, weil JPEG keinen Alphakanal kennt. Ohne die Umwandlung
        // scheitert die Kodierung statt zu funktionieren.
        let mut jpeg = std::io::Cursor::new(Vec::new());
        image::DynamicImage::ImageRgba8(klein.clone())
            .to_rgb8()
            .write_with_encoder(image::codecs::jpeg::JpegEncoder::new_with_quality(
                &mut jpeg, JPEG_QUALITAET,
            ))
            .map_err(|e| format!("Bild nicht kodierbar: {e}"))?;

        Ok(json!({
            "bild_jpeg_base64": base64::engine::general_purpose::STANDARD.encode(jpeg.into_inner()),
            // Die Koordinaten, in denen das Modell antworten soll: die des
            // **verkleinerten** Bildes. Zurueckgerechnet wird beim Klicken,
            // damit das Modell nicht rechnen muss.
            "breite": klein.width(),
            "hoehe": klein.height(),
            "hinweis": "Koordinaten beziehen sich auf dieses Bild, Ursprung links oben.",
        }))
    }

    pub fn echte_punkte(x: i32, y: i32) -> Result<(i32, i32), String> {
        let monitor = hauptbildschirm()?;
        let breite = monitor
            .width()
            .map_err(|e| format!("Kein Bildschirm: {e}"))?;
        let hoehe = monitor
            .height()
            .map_err(|e| format!("Kein Bildschirm: {e}"))?;
        Ok(zurueckrechnen(x, y, verkleinerung(breite, hoehe)))
    }
}

#[cfg(not(windows))]
mod aufnahme_impl {
    use serde_json::Value;

    pub fn aufnehmen(_app: &tauri::AppHandle) -> Result<Value, String> {
        Err("Bildschirmfotos gibt es bisher nur unter Windows.".into())
    }

    pub fn echte_punkte(_x: i32, _y: i32) -> Result<(i32, i32), String> {
        Err("Bildschirmfotos gibt es bisher nur unter Windows.".into())
    }
}

/// Ein Foto des Hauptbildschirms, verkleinert und als JPEG in base64.
///
/// Felder: `bild_jpeg_base64`, `breite`, `hoehe`, `hinweis` — `breite` und
/// `hoehe` sind die des **verkleinerten** Bildes und damit das Raster, in dem
/// das Modell Punkte nennt.
///
/// **Der `AppHandle` ist nicht optional, und das ist der ganze Punkt.** Er
/// wird fuer die Aufnahme nicht gebraucht, sondern fuer den Indikator
/// (`sichtfeld::zeigen`) — und er steht hier in der Signatur, damit es keinen
/// Weg gibt, ein Bildschirmfoto zu machen, ohne ihn zu zeigen. Waere der
/// Aufruf eine Zeile weiter oben im Dispatch, koennte ein zweiter Aufrufer
/// sie vergessen; hier kann er es nicht, ohne den Compiler zu ueberzeugen.
/// Wer diesen Parameter spaeter entfernt, entfernt die Zusage.
pub fn aufnehmen(app: &AppHandle) -> Result<Value, String> {
    crate::sichtfeld::zeigen(app);
    aufnahme_impl::aufnehmen(Some(app))
}

/// Rechnet Bildkoordinaten in echte Bildschirmpunkte zurueck — der Gegenweg
/// zu dem Raster, das `aufnehmen` meldet.
#[allow(dead_code)]
pub fn echte_punkte(x: i32, y: i32) -> Result<(i32, i32), String> {
    aufnahme_impl::echte_punkte(x, y)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Ein echtes Bildschirmfoto muss durch die Bruecke zum Panel passen.
    ///
    /// Diese Zahl ist der Grund, warum hier JPEG steht und nicht PNG. Bis zum
    /// 23.08.2026 wurde als PNG kodiert, und die Bruecke liess 200.000 Zeichen
    /// durch — jedes Foto war groesser, wurde abgewiesen, und die KI sah nie
    /// etwas. Der Test misst am echten Bildschirm, damit die Annahme nicht
    /// wieder ungeprueft im Kommentar steht.
    ///
    /// Ohne Monitor (Bauserver ohne Sitzung) faellt er still durch: dort gibt
    /// es nichts zu messen, und ein roter Test waere eine Aussage ueber die
    /// Umgebung statt ueber den Code.
    #[cfg(windows)]
    #[test]
    fn ein_echtes_foto_passt_durch_die_bruecke() {
        let Ok(ergebnis) = aufnahme_impl::aufnehmen(None) else {
            return;
        };
        let bild = ergebnis["bild_jpeg_base64"].as_str().unwrap();
        eprintln!(
            "Bildschirmfoto: {} Zeichen base64 ({}x{})",
            bild.len(),
            ergebnis["breite"],
            ergebnis["hoehe"]
        );
        assert!(
            bild.len() < 900_000,
            "Bildschirmfoto zu gross fuer die Bruecke: {} Zeichen",
            bild.len()
        );
        // Ein JPEG faengt mit /9j/ an (0xFF 0xD8 0xFF). Der Test haelt damit
        // auch fest, dass hier wirklich JPEG rauskommt und nicht PNG.
        assert!(bild.starts_with("/9j/"), "kein JPEG: {}", &bild[..8.min(bild.len())]);
    }

    #[test]
    fn kleine_bildschirme_werden_nicht_hochgerechnet() {
        // Ein 1024x768-Bild groesser zu rechnen kostete Tokens und zeigte
        // keinen Punkt mehr — der Faktor bleibt bei 1.0, der Rueckweg bei
        // der Identitaet.
        assert_eq!(verkleinerung(1024, 768), 1.0);
        assert_eq!(zurueckrechnen(300, 200, 1.0), (300, 200));
    }

    #[test]
    fn die_lange_kante_landet_auf_max_kante() {
        // Quer und hochkant: entscheidend ist die laengere Seite, nicht die
        // Breite. Ein 1440x2560-Schirm wurde sonst auf 1280 Breite gerechnet
        // und waere mit 2276 Punkten Hoehe weiter zu gross.
        let quer = verkleinerung(3840, 2160);
        assert_eq!((3840.0 * quer) as u32, MAX_KANTE);
        let hoch = verkleinerung(1440, 2560);
        assert_eq!((2560.0 * hoch) as u32, MAX_KANTE);
    }

    #[test]
    fn der_rueckweg_trifft_wieder_den_rand() {
        // Die Invariante hinter jedem Klick: was das Modell am rechten unteren
        // Rand des verkleinerten Bildes sieht, muss am rechten unteren Rand
        // des Bildschirms landen. Ein zweiter, abweichender Faktor irgendwo
        // im Code waere ein Klick daneben — und das faellt niemandem auf,
        // ausser dem Menschen, dessen Fenster sich schliesst.
        let faktor = verkleinerung(3840, 2160);
        let (x, y) = zurueckrechnen((3840.0 * faktor) as i32, (2160.0 * faktor) as i32, faktor);
        assert!((x - 3840).abs() <= 1, "x={x}");
        assert!((y - 2160).abs() <= 1, "y={y}");
    }

    #[cfg(not(windows))]
    #[test]
    fn ausserhalb_von_windows_gibt_es_kein_stilles_leerbild() {
        // Ein leeres Bild statt eines Fehlers waere die schlimmste Antwort:
        // das Modell haelt einen schwarzen Schirm fuer eine Tatsache.
        assert!(aufnehmen().is_err());
        assert!(echte_punkte(10, 10).is_err());
    }
}
