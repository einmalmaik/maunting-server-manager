//! Der Indikator: ein kleines rotes Schild, solange die KI auf den Bildschirm
//! sieht.
//!
//! Warum es ihn gibt. Seit dem 23.08.2026 darf die KI jederzeit ein
//! Bildschirmfoto machen — ohne Freigabe, ohne Klick, ohne Frist
//! (`bildschirm.rs`). Damit ist die Frage nicht mehr „darf sie", sondern
//! „weiss ich es". Genau ein Ding beantwortet sie: ein sichtbares Zeichen,
//! das der Rechner selbst setzt. Die Kamera-Leuchte am Laptop ist das
//! Vorbild, und sie ist genau deshalb ein gutes Vorbild, weil sie nicht an
//! der Software haengt.
//!
//! **Drei Zusagen, die dieser Code haelt.**
//!
//! 1. *Rust zeigt ihn, unmittelbar bevor die Aufnahme laeuft.* Nicht das
//!    Frontend, nicht das Panel, nicht das Modell — nichts davon koennte man
//!    unterwegs verlieren. Danach bleibt er den `NACHLAUF` lang stehen: wer
//!    zweimal kurz hintereinander schaut, sieht ein durchgehendes Licht und
//!    kein Flackern, und ein einzelner Blick verschwindet nicht so schnell,
//!    dass man ihn uebersehen kann.
//! 2. *Es gibt keinen Schalter.* Kein Command, kein Werkzeug und kein Wert in
//!    der Konfiguration schaltet ihn ab; er haengt an nichts, was sich
//!    umstellen laesst. Der Test `es_gibt_keinen_schalter` haelt das am
//!    Quelltext fest, weil keine Typgrenze es erzwingen kann. Wer hier
//!    spaeter einen Tauri-Command, einen Blick in die Konfiguration oder eine
//!    Umgebungsvariable einbaut, baut die Umgehung ein, nicht ein Feature.
//! 3. *Er steht in keinem Prompt und in keiner Werkzeugbeschreibung.* Das ist
//!    Absicht und der eigentliche Kern: die KI soll ueber ihn nicht nachdenken
//!    koennen. Was sie nicht kennt, kann sie weder erwaehnen noch
//!    wegargumentieren noch den Benutzer bitten, es abzustellen. Wer ihn
//!    spaeter „der Vollstaendigkeit halber" in eine Werkzeugbeschreibung, in
//!    den Systemprompt oder in eine Zusagenliste aufnimmt, nimmt ihm genau
//!    die Eigenschaft, wegen der er existiert.
//!
//! **Und eine Regel fuers Scheitern.** Laesst sich das Fenster nicht bauen —
//! kein Monitor, WebView2 kaputt, Fensterlimit erreicht —, dann wird das
//! protokolliert und die Aufnahme laeuft trotzdem. Ein Indikator, der die
//! Funktion blockiert, wird beim ersten Bericht („die KI sieht nichts mehr")
//! wieder ausgebaut; einer, der still weiterlaeuft, bleibt drin. Ein
//! Indikator, den es nicht mehr gibt, schuetzt niemanden.
//!
//! Aufgerufen wird `zeigen` dort, wo der Griff auf die App liegt (das
//! Werkzeug in `auftrag.rs`), direkt vor `bildschirm::aufnehmen`. Der
//! `AppHandle` reicht nicht in die Aufnahme hinein, deshalb steht die Zeile
//! beim Werkzeug — nicht, weil sie optional waere.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};
#[cfg(not(target_os = "android"))]
use tauri::{LogicalPosition, WebviewUrl, WebviewWindowBuilder};

/// Kennung des Fensters. Bewusst kein Eintrag in `tauri.conf.json`: das
/// Fenster entsteht erst beim ersten Blick und lebt nur, solange geschaut
/// wird — ein dauerhaft vorhandenes, nur verstecktes Fenster waere ein
/// Fenster, das jemand versehentlich zeigen kann.
const KENNUNG: &str = "sichtfeld";

/// Wie lange das Schild nach der Aufnahme noch stehen bleibt. Kurz genug,
/// dass es nicht im Weg ist, lang genug, dass eine Folge schneller Blicke
/// (schauen, klicken, wieder schauen) als ein durchgehendes Licht erscheint —
/// die Runde zwischen zwei Werkzeugaufrufen liegt bei Sekunden, die Aufnahme
/// selbst bei Millisekunden.
const NACHLAUF: Duration = Duration::from_millis(1500);

/// Groesse und Abstand zur Bildschirmkante, in logischen Punkten. Klein genug,
/// dass niemand es wegmachen will, gross genug, dass man den Satz liest.
#[allow(dead_code)]
const BREITE: f64 = 180.0;
#[allow(dead_code)]
const HOEHE: f64 = 44.0;
#[allow(dead_code)]
const RAND: f64 = 16.0;

/// Bis wann das Schild stehen bleibt. `None` heisst: es ist aus.
static FRIST: Mutex<Option<Instant>> = Mutex::new(None);

/// Zeigt den Indikator und verlaengert seine Frist. Der Aufrufer fotografiert
/// unmittelbar danach.
///
/// Ob das Schild im Foto selbst schon zu sehen ist, haengt am WebView: das
/// Anzeigen ist beim ersten Blick ein Fensterbau (der wartet), danach nur noch
/// eine Nachricht an die Ereignisschleife (die wartet nicht). Absichtlich wird
/// hier **nicht** auf das erste Bild gewartet — die Zusage gilt dem Menschen
/// am Rechner ueber die naechsten anderthalb Sekunden, und jeder Aufruf um
/// einen gemalten Rahmen langsamer zu machen waere ein Preis, den niemand
/// verlangt hat.
///
/// Scheitert nie nach aussen: was hier schiefgeht, steht im Protokoll und
/// haelt die Aufnahme nicht auf (siehe Modulkopf).
pub fn zeigen(app: &AppHandle) {
    // Die Reihenfolge traegt die ganze Mechanik: **erst** die Frist, dann das
    // Fenster, dann der Waechter. Erst die Frist, weil ein Waechter, der beim
    // Ausblenden noch eine leere Frist saehe, genau in diesem Moment
    // ausblenden wuerde; und weil ein noch laufender Waechter die
    // Verlaengerung sonst verpassen koennte.
    let braucht_waechter = {
        // Ein vergiftetes Schloss (Panik in einem frueheren Waechter) darf den
        // Indikator nicht abschalten: dahinter liegt ein einzelner Zeitpunkt,
        // den keine halbe Aenderung kaputtmachen kann. `into_inner` ist hier
        // die sichere Variante, nicht die bequeme.
        let mut stand = FRIST
            .lock()
            .unwrap_or_else(|vergiftet| vergiftet.into_inner());
        frist_setzen(&mut stand, Instant::now(), NACHLAUF)
    };

    if let Err(fehler) = fenster_zeigen(app) {
        eprintln!("Sichtfeld nicht anzeigbar: {fehler}");
    }

    if braucht_waechter {
        let app = app.clone();
        std::thread::spawn(move || waechter(app));
    }
}

/// Setzt die neue Frist und sagt, ob dafuer ein Waechter gebraucht wird.
///
/// `true` heisst: bisher lief keiner, der Aufrufer startet einen. `false`
/// heisst: es laeuft schon einer, und er sieht die verlaengerte Frist beim
/// naechsten Aufwachen von selbst. Genau hier liegt der Unterschied zwischen
/// „der zweite Blick verlaengert" und „der zweite Blick baut neu": ohne diese
/// Unterscheidung haette jeder Blick seinen eigenen Waechter, und der erste
/// haette das Fenster mitten im zweiten wieder versteckt.
///
/// Rein und ohne Zustand, damit die Fristlogik ohne laufende App pruefbar
/// bleibt — an einem Tauri-Fenster kann ein Test hier nichts festhalten.
fn frist_setzen(stand: &mut Option<Instant>, jetzt: Instant, nachlauf: Duration) -> bool {
    let laeuft_schon = matches!(*stand, Some(bis) if bis > jetzt);
    *stand = Some(jetzt + nachlauf);
    !laeuft_schon
}

/// Schlaeft bis zur Frist, verlaengert sich mit ihr und blendet am Ende aus.
fn waechter(app: AppHandle) {
    loop {
        let rest = {
            let mut stand = FRIST
                .lock()
                .unwrap_or_else(|vergiftet| vergiftet.into_inner());
            match (*stand).and_then(|bis| bis.checked_duration_since(Instant::now())) {
                Some(rest) => rest,
                None => {
                    // Abgelaufen: Frist raeumen und ausblenden unter
                    // demselben Schloss. Getrennt waere dazwischen Platz fuer
                    // einen neuen Blick — das Schild ginge mitten in einer
                    // Aufnahme aus, und niemand haette es gemerkt.
                    //
                    // Das Schloss ueber `hide` zu halten kostet nichts und
                    // klemmt nichts fest: `hide` schickt aus einem
                    // Nebenfaden nur eine Nachricht an die Ereignisschleife
                    // und wartet nicht auf die Antwort (tauri-runtime-wry,
                    // `send_user_message`). Wer das hier spaeter
                    // auseinanderzieht, um „nicht unter dem Schloss zu
                    // blockieren", holt sich das Loch zurueck.
                    *stand = None;
                    if let Some(fenster) = app.get_webview_window(KENNUNG) {
                        let _ = fenster.hide();
                    }
                    return;
                }
            }
        };
        std::thread::sleep(rest);
    }
}

/// Baut das Fenster beim ersten Blick und zeigt es bei jedem weiteren.
///
/// Kein Neubau, solange es steht: das waere sichtbares Flackern, und der
/// WebView muesste jedes Mal neu laden — mitten in einer Folge von Blicken
/// gaebe es genau dann kein Schild, wenn geschaut wird.
#[cfg(not(target_os = "android"))]
fn fenster_zeigen(app: &AppHandle) -> Result<(), String> {
    if let Some(fenster) = app.get_webview_window(KENNUNG) {
        return fenster.show().map_err(|e| e.to_string());
    }

    let fenster = WebviewWindowBuilder::new(app, KENNUNG, WebviewUrl::External(inhalt()?))
        // Der Titel ist nicht zu sehen (rahmenlos), steht aber in jeder
        // Fensterliste des Betriebssystems. Ein namenloses Fenster, das immer
        // oben liegt und Klicks durchlaesst, ist genau das, was man sonst bei
        // Schadsoftware sucht — dieses sagt, was es ist.
        .title("Singra sieht den Bildschirm")
        .inner_size(BREITE, HOEHE)
        .resizable(false)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        // Nicht fokussierbar: der Indikator darf niemandem den Cursor aus dem
        // Textfeld nehmen. Wer beim Tippen unterbrochen wird, macht das Ding
        // weg — und dann schuetzt es niemanden mehr.
        .focused(false)
        .focusable(false)
        // Erst unsichtbar bauen, positionieren, dann zeigen: sonst blitzt es
        // einen Moment in der Bildschirmmitte auf.
        .visible(false)
        .build()
        .map_err(|e| e.to_string())?;

    // Klicks gehen hindurch. Ein Schild, das im Weg ist, wird zu dem Ding,
    // das man loswerden will.
    if let Err(fehler) = fenster.set_ignore_cursor_events(true) {
        eprintln!("Sichtfeld nicht klickdurchlaessig: {fehler}");
    }
    if let Some((x, y)) = ecke(app) {
        let _ = fenster.set_position(LogicalPosition::new(x, y));
    }
    fenster.show().map_err(|e| e.to_string())
}

#[cfg(target_os = "android")]
fn fenster_zeigen(_app: &AppHandle) -> Result<(), String> {
    Ok(())
}

/// Obere rechte Ecke des Hauptbildschirms, innerhalb des Arbeitsbereichs.
/// Oben, weil unten die Taskleiste sitzt; rechts, weil Windows dort selbst
/// seine Aufnahme- und Mikrofonhinweise zeigt — dort sucht das Auge.
/// `None` heisst: kein Monitor bekannt, dann bleibt die Vorgabe von Tauri.
#[allow(dead_code)]
fn ecke(app: &AppHandle) -> Option<(f64, f64)> {
    let monitor = app.primary_monitor().ok().flatten()?;
    let skalierung = monitor.scale_factor();
    let flaeche = monitor.work_area();
    let links = flaeche.position.x as f64 / skalierung;
    let oben = flaeche.position.y as f64 / skalierung;
    let breite = flaeche.size.width as f64 / skalierung;
    Some((links + breite - BREITE - RAND, oben + RAND))
}

/// Das Schild. Der Satz nennt den Namen, unter dem der Benutzer die KI kennt,
/// und sagt in der Gegenwartsform, was gerade passiert — nicht „Aufnahme
/// aktiv", was nach Videomitschnitt klingt, und nicht „Bildschirmfreigabe",
/// was nach etwas klingt, das der Benutzer selbst erteilt hat.
///
/// Farben und Masse aus der MauntingStudios Design-DNA: Flaeche `--el-3`
/// (202 26% 14%), Text `--foreground` (188 29% 95%), Punkt und Rand
/// `--destructive` (0 70% 55%), Schatten „Produktkarte", Radius „Pill".
/// Bewusst ohne `#`-Notation — jede Raute muesste in der Adresse unten
/// maskiert werden, und eine vergessene zerlegte die Seite in Pfad und
/// Fragment.
#[allow(dead_code)]
const HTML: &str = r#"<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title></title><style>
html,body{margin:0;height:100%;background:transparent;overflow:hidden}
.schild{box-sizing:border-box;display:flex;align-items:center;gap:10px;
height:100%;padding:0 14px;border-radius:999px;
background:hsl(202 26% 14% / 0.94);border:1px solid hsl(0 70% 55% / 0.55);
box-shadow:0 18px 60px hsl(0 0% 0% / 0.30);color:hsl(188 29% 95%);
font-family:Inter,"IBM Plex Sans","Segoe UI",system-ui,sans-serif;
font-size:13px;line-height:1}
.punkt{flex:none;width:10px;height:10px;border-radius:999px;
background:hsl(0 70% 55%);box-shadow:0 0 8px hsl(0 70% 55% / 0.9)}
</style></head><body><div class="schild"><span class="punkt"></span>
<span>Singra sieht den Bildschirm</span></div></body></html>"#;

/// Der Fensterinhalt als `data:`-URL — so braucht der Indikator keine eigene
/// HTML-Datei im Bundle, die ein Installer auslassen oder ein Build vergessen
/// koennte. Genau das waere die stille Art, ihn loszuwerden.
#[allow(dead_code)]
fn inhalt() -> Result<tauri::Url, String> {
    tauri::Url::parse(&format!("data:text/html;charset=utf-8,{}", prozent(HTML)))
        .map_err(|e| format!("Inhalt nicht baubar: {e}"))
}

/// Prozentkodierung fuer den Inhalt der Adresse. Ohne sie entscheidet der
/// Zufall: ein `?` im Text macht aus dem Rest eine Abfrage, ein `#` ein
/// Fragment, und die halbe Seite ist weg — ohne Fehlermeldung, das Fenster
/// bliebe einfach leer. Durchgelassen wird deshalb nur, was in einer Adresse
/// unstrittig ist; alles andere reist als Byte.
#[allow(dead_code)]
fn prozent(text: &str) -> String {
    let mut aus = String::with_capacity(text.len() * 2);
    for byte in text.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                aus.push(*byte as char)
            }
            anderes => aus.push_str(&format!("%{anderes:02X}")),
        }
    }
    aus
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn der_erste_blick_braucht_einen_waechter() {
        let mut stand = None;
        let jetzt = Instant::now();
        assert!(frist_setzen(&mut stand, jetzt, NACHLAUF));
        assert_eq!(stand, Some(jetzt + NACHLAUF));
    }

    #[test]
    fn der_zweite_blick_verlaengert_statt_neu_zu_bauen() {
        // Der Kern der Fristlogik: waehrend das Schild steht, kommt kein
        // zweiter Waechter dazu (sonst haette der erste es mitten im zweiten
        // Blick versteckt) — die Frist wandert nur nach hinten.
        let jetzt = Instant::now();
        let mut stand = None;
        frist_setzen(&mut stand, jetzt, NACHLAUF);

        let spaeter = jetzt + Duration::from_millis(500);
        assert!(!frist_setzen(&mut stand, spaeter, NACHLAUF));
        assert_eq!(stand, Some(spaeter + NACHLAUF));
    }

    #[test]
    fn nach_ablauf_beginnt_es_von_vorn() {
        // Der alte Waechter ist zu diesem Zeitpunkt fertig; ohne neuen bliebe
        // das Fenster stehen, bis jemand die App beendet.
        let jetzt = Instant::now();
        let mut stand = Some(jetzt);
        assert!(frist_setzen(&mut stand, jetzt + NACHLAUF, NACHLAUF));
    }

    #[test]
    fn genau_auf_der_frist_zaehlt_als_abgelaufen() {
        // Die Kante: `bis > jetzt`, nicht `>=`. Sonst haette ein Blick exakt
        // auf der Millisekunde keinen Waechter bekommen und das Schild waere
        // stehen geblieben.
        let jetzt = Instant::now();
        let mut stand = Some(jetzt);
        assert!(frist_setzen(&mut stand, jetzt, NACHLAUF));
    }

    #[test]
    fn die_adresse_bleibt_ganz() {
        // Ein `?` oder `#` im Inhalt wuerde die Adresse still abschneiden:
        // das Fenster bliebe leer, und niemand saehe je einen Fehler.
        let url = inhalt().unwrap();
        assert_eq!(url.scheme(), "data");
        assert!(url.query().is_none(), "Abfrage abgetrennt: {url}");
        assert!(url.fragment().is_none(), "Fragment abgetrennt: {url}");
        // Und nichts unterwegs verloren oder noch einmal umkodiert.
        assert!(url.path().ends_with(&prozent(HTML)), "Inhalt veraendert");
    }

    #[test]
    fn die_kodierung_laesst_keine_trennzeichen_durch() {
        assert_eq!(prozent("a#b?c d"), "a%23b%3Fc%20d");
        assert_eq!(prozent("<span>"), "%3Cspan%3E");
        // Das Prozentzeichen selbst zuerst — sonst waere jede CSS-Prozentzahl
        // der Anfang einer kaputten Kodierung.
        assert_eq!(prozent("26%"), "26%25");
        assert!(prozent(HTML)
            .chars()
            .all(|z| z.is_ascii_alphanumeric() || "-_.~%".contains(z)));
    }

    #[test]
    fn der_satz_steht_im_schild() {
        // Der eine sichtbare Produkttext dieses Moduls. Faellt er beim
        // Umbauen der Auszeichnung heraus, zeigt der Indikator einen roten
        // Punkt ohne Erklaerung.
        assert!(
            HTML.contains("Singra sieht den Bildschirm"),
            "Der Satz fehlt in der Auszeichnung"
        );
    }

    #[test]
    fn es_gibt_keinen_schalter() {
        // Zusage 2 aus dem Modulkopf, als Quelltextpruefung wie
        // `lesend_bleibt_lesend` in system.rs: mechanisch erzwingen laesst
        // sie sich nicht. Geprueft wird nur der Code vor dem Testmodul, sonst
        // faende der Test seine eigenen Suchbegriffe.
        let quelle = include_str!("sichtfeld.rs");
        let code = quelle.split("#[cfg(test)]").next().unwrap();
        for umgehung in [
            // Ein Command waere der Schalter fuer das Frontend …
            "tauri::command",
            // … ein Blick in die Konfiguration der fuer jeden, der die Datei
            // findet, und ein Blick in eine Umgebungsvariable der fuer jeden,
            // der die Verknuepfung bearbeitet.
            "konfig::",
            "env::var",
        ] {
            assert!(
                !code.contains(umgehung),
                "Ausschalter im Indikator: {umgehung}"
            );
        }
    }
}
