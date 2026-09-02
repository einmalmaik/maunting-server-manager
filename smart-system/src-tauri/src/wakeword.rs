//! Lokales Wake-Word: Kalibrierung, Training und Erkennung — 100 % auf dem
//! Rechner, ohne Cloud und ohne permanentes Streaming.
//!
//! Engine ist rustpotter (Apache-2.0): der Nutzer spricht seinen
//! Agenten-Namen sechsmal ein, daraus entsteht ein Referenzmodell
//! (MFCC + Dynamic Time Warping) — genau der Weg, den rustpotter für
//! persönliche Wake-Words vorsieht. Kein vortrainiertes Netz nötig, der
//! Name ist frei wählbar.
//!
//! Ablauf:
//! 1. `aufnehmen(n)` — wartet auf Sprachenergie (bis ~5 s), schneidet vom
//!    Einsatz bis zum Sprachende (300 ms Vorlauf, 150 ms Nachlauf, höchstens
//!    2,2 s) und legt sie als Mono-WAV unter
//!    `<app-local-data>/wakeword/aufnahme-NN.wav` ab. Stille ist ein
//!    Fehler, keine Aufnahme. **Die Länge ist hier kein Nebending:** aus
//!    der längsten Schablone leitet rustpotter Suchfenster und Nachlaufuhr
//!    ab — zu lange Aufnahmen machen die Erkennung träge, nicht ungenau.
//! 2. `trainieren(wort)` — baut aus allen Aufnahmen ein Referenzmodell
//!    und speichert es als `wakeword.rpw`.
//! 3. `lauschen_starten` — ein einzelner Thread liest das Mikrofon und
//!    füttert rustpotter Frame für Frame; bei einem Treffer geht das
//!    Tauri-Event `wakeword-erkannt` an die Fenster. Erst **danach**
//!    öffnet die App den Audiokanal zur KI — vorher verlässt kein Sample
//!    den Prozess.
//!
//! Der Lausch-Thread blockiert auf einem Channel (kein Polling); Stoppen
//! setzt ein Flag, der Thread endet binnen 500 ms und gibt das Mikrofon
//! frei. Hier stand „Zero-Resource" — das stimmt für den Leerlauf **ohne**
//! Wake-Word. Lauscht die App, rechnet dieser Thread dauerhaft: gemessen am
//! 23.08.2026 rund ein Sechstel eines Kerns. Das ist der Preis des
//! Zuhörens; kürzere Schablonen (siehe `AUFNAHME_SEKUNDEN`) senken ihn
//! deutlich, weil DTW quadratisch in der Schablonenlänge ist.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::time::Duration;

use cpal::traits::{DeviceTrait, StreamTrait};
use rustpotter::{
    Rustpotter, RustpotterConfig, ScoreMode, VADMode, WakewordRef, WakewordRefBuildFromFiles,
    WakewordSave,
};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

/// Ziel-Anzahl der Kalibrierungs-Aufnahmen (Onboarding: „sprich den Namen 6x“).
///
/// Waren zehn. rustpotters README nennt für Referenzmodelle ausdrücklich
/// „3 to 8 wav records" — zehn lag ausserhalb des Bereichs, für den das
/// Verfahren gedacht ist, und jede weitere Aufnahme kostet Rechenzeit in
/// jedem einzelnen Vergleich. Sechs liegt in der Mitte des empfohlenen
/// Bereichs und lässt dem Median (siehe `score_mode`) noch genug Aufnahmen,
/// um einen Ausreisser wegzustecken.
pub const AUFNAHMEN_SOLL: u8 = 6;
/// rustpotter braucht mindestens drei Aufnahmen für eine brauchbare Referenz.
const AUFNAHMEN_MINDESTENS: usize = 3;
/// Notbremse für die Länge einer Kalibrierungsaufnahme.
///
/// **Nicht mehr die Ziellänge.** Bis zum 23.08.2026 wurde ab dem
/// Spracheinsatz stur so lange mitgeschnitten, egal wann der Sprecher
/// aufhörte — jede Aufnahme war exakt 2,5 s lang, ein zwei- bis
/// dreisilbiger Name dauert aber 0,5–0,8 s. Der Rest war Raumton, und
/// rustpotter trimmt ihn nicht: es baut die DTW-Schablone aus **allen**
/// Frames der Datei. Aus dieser Länge leitet es dann sein Suchfenster
/// (`max_mfcc_frames`) und seine Nachlaufuhr (`max_mfcc_frames / 2`) ab.
/// Geschnitten wird jetzt am Sprachende; dieser Wert greift nur noch, wenn
/// jemand ohne Pause weiterredet.
const AUFNAHME_SEKUNDEN: f32 = 2.2;
/// So viel zusammenhängende Stille beendet die Aufnahme. Kürzer wäre
/// gefährlich: zwischen zwei Silben liegt gut und gern eine Zehntelsekunde
/// unter der Schwelle, und ein Schnitt mitten im Namen wäre schlimmer als
/// etwas Raumton am Ende.
const STILLE_SEKUNDEN: f32 = 0.25;
/// Was von der gezählten Stille stehen bleibt. Der Auslaut eines Wortes ist
/// leise und liegt oft schon unter `SPRACHE_RMS` — wer bündig am letzten
/// lauten Fenster schneidet, verliert ihn.
const NACHLAUF_SEKUNDEN: f32 = 0.15;
/// So lange wird auf Sprachenergie gewartet, bevor die Runde als still gilt.
const SPRACHE_WARTEN_SEKUNDEN: f32 = 5.0;
/// Vorlauf vor dem erkannten Einsatz — Wortanfänge sind leise, und ein
/// Schnitt genau am Schwellendurchgang verlöre die erste Silbe.
const VORLAUF_SEKUNDEN: f32 = 0.3;
/// RMS-Schwelle für „hier spricht jemand" (~-40 dBFS). Fest statt adaptiv:
/// normale Sprache liegt eine Größenordnung darüber, Raumrauschen darunter,
/// und ein fester Wert ist testbar. Ein sehr lautes Umfeld öffnet das Tor
/// früher — das ist dann nicht schlechter als die alte Blindaufnahme.
const SPRACHE_RMS: f32 = 0.01;
/// Die eine Meldung für „niemand hat gesprochen" — die UI wiederholt die
/// Runde daraufhin, statt sie zu zählen.
const NICHTS_GEHOERT: &str = "Nichts gehört — bitte sprich das Wort deutlich ins Mikrofon";
const MODELL_DATEI: &str = "wakeword.rpw";
/// Neben dem Modell liegt eine Marke mit der Nummer des Schnittverfahrens.
///
/// Sie beantwortet genau eine Frage: **stammt diese Kalibrierung noch aus
/// der Zeit der festen 2,5-s-Aufnahmen?** Am 23.08.2026 wurde der Schnitt
/// vom festen Zeitmass auf das Sprachende umgestellt; alte Schablonen
/// bestehen zu drei Vierteln aus Raumton und machen die Erkennung genauso
/// traege wie vorher. Ein Modell weiterzubenutzen, das gerade repariert
/// wurde, ist die stille Art, eine Reparatur wirkungslos zu machen.
///
/// Warum eine Datei und keine Ableitung aus der Aufnahmezahl: die Zahl ist
/// mehrdeutig (wer damals abgebrochen hat, hat auch sechs), eine Marke ist
/// es nicht.
const VERFAHREN_DATEI: &str = "verfahren";
/// Die aktuelle Nummer. Wird sie erhoeht, gilt jede aeltere Kalibrierung als
/// veraltet und die App schlaegt eine neue vor.
const VERFAHREN: u32 = 2;
/// MFCC-Auflösung; 16 ist der rustpotter-Standard für Referenzmodelle.
const MFCC_GROESSE: u16 = 16;

/// Ob der Lausch-Thread laufen soll. Stoppen ist nur ein Wunsch, den der
/// Thread binnen eines Channel-Timeouts erfüllt.
static LAUSCHT: AtomicBool = AtomicBool::new(false);
/// Das Handle des laufenden Threads. Es existiert für genau einen Fall: den
/// Durchstart (stoppen, sofort wieder starten — Gerätewechsel, neue
/// Schwelle). `stoppen` kehrt sofort zurück, der Thread prüft das Flag aber
/// nur je Audioblock; ohne das Join hier setzte `starten` das Flag wieder,
/// bevor der Alte es je gesehen hätte — und zwei Threads hingen an einem
/// Mikrofon, der alte mit der alten Konfiguration.
static FADEN: std::sync::Mutex<Option<std::thread::JoinHandle<()>>> =
    std::sync::Mutex::new(None);

#[derive(Serialize, Clone)]
pub struct WakewordStand {
    pub aufnahmen: u8,
    pub trainiert: bool,
    pub lauscht: bool,
    /// Ob das Lauschen laufen **soll** (konfig.json). `lauscht` sagt, ob der
    /// Thread wirklich läuft — nach einem Mikrofonfehler gehen beide auseinander.
    pub aktiv: bool,
    /// Auf welches Wort trainiert wurde. Weicht es vom heutigen
    /// Assistenten-Namen ab, schlägt die UI eine Neukalibrierung vor.
    pub wort: Option<String>,
    /// Ob die vorhandene Kalibrierung aus einem älteren Schnittverfahren
    /// stammt (siehe `VERFAHREN`). Dann hilft nur neu einsprechen — an
    /// überlangen Schablonen ändert keine Einstellung etwas.
    pub veraltet: bool,
    /// Name des Eingabegeräts, das benutzt würde — `None` heißt: kein Mikrofon
    /// da. Die UI zeigt dann eine Warnung statt Knöpfen, die nur scheitern können.
    pub geraet: Option<String>,
}

fn wakeword_verzeichnis(app: &AppHandle) -> Result<PathBuf, String> {
    let basis = app
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("App-Datenverzeichnis unbekannt: {e}"))?;
    let verzeichnis = basis.join("wakeword");
    std::fs::create_dir_all(&verzeichnis).map_err(|e| e.to_string())?;
    Ok(verzeichnis)
}

fn modell_pfad(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(wakeword_verzeichnis(app)?.join(MODELL_DATEI))
}

/// Die Verfahrensnummer der vorhandenen Kalibrierung. Fehlt oder taugt die
/// Marke nichts, ist es die Zeit vor der Marke — also 1.
fn verfahren_lesen(verzeichnis: &Path) -> u32 {
    std::fs::read_to_string(verzeichnis.join(VERFAHREN_DATEI))
        .ok()
        .and_then(|inhalt| inhalt.trim().parse().ok())
        .unwrap_or(1)
}

fn aufnahme_dateien(verzeichnis: &Path) -> Vec<String> {
    let Ok(eintraege) = std::fs::read_dir(verzeichnis) else {
        return Vec::new();
    };
    let mut dateien: Vec<String> = eintraege
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            (name.starts_with("aufnahme-") && name.ends_with(".wav"))
                .then(|| e.path().to_string_lossy().into_owned())
        })
        .collect();
    dateien.sort();
    dateien
}

pub fn stand(app: &AppHandle) -> Result<WakewordStand, String> {
    let verzeichnis = wakeword_verzeichnis(app)?;
    let konfig = crate::konfig::laden(app).unwrap_or_default();
    Ok(WakewordStand {
        aufnahmen: aufnahme_dateien(&verzeichnis).len().min(u8::MAX as usize) as u8,
        trainiert: verzeichnis.join(MODELL_DATEI).exists(),
        lauscht: LAUSCHT.load(Ordering::SeqCst),
        aktiv: konfig.wakeword_aktiv,
        wort: konfig.wakeword_wort,
        veraltet: verzeichnis.join(MODELL_DATEI).exists()
            && verfahren_lesen(&verzeichnis) < VERFAHREN,
        geraet: mikrofon_name(konfig.audio_eingabe.as_deref()),
    })
}

/// Name des Eingabegeräts, das benutzt würde (Wunsch des Benutzers oder
/// Windows-Standard). Nur der Name verlässt das Modul — kein Audio,
/// keine Geräte-IDs.
fn mikrofon_name(bevorzugt: Option<&str>) -> Option<String> {
    crate::audio::eingang_finden(bevorzugt)
        .and_then(|geraet| geraet.description().ok())
        .map(|beschreibung| beschreibung.name().to_string())
}

/// „Wake-Word neu einrichten": Schalter aus, Aufnahmen und Modell weg.
///
/// **Erst der Schalter, dann die Dateien**, und beides ohne verschluckten
/// Fehler. Hier stand das Speichern als `let _ =` hinter dem Löschen: schlug
/// es fehl, war das Modell weg und `wakeword_aktiv` stand weiter auf „an" —
/// ein Schalter ohne Modell dahinter ist eine Anzeige, die lügt, und niemand
/// erfuhr davon.
///
/// **Aber kein Abbruch dazwischen.** Danach hing das Löschen an einem
/// erfolgreichen `speichern`, und `speichern` prüft das ganze Konfig-Objekt:
/// ein einziger Altwert an unbeteiligter Stelle machte „neu einrichten"
/// unmöglich. Beide Schritte laufen jetzt, und die Meldung nennt, was davon
/// schiefging.
///
/// Die verbleibende Fehlrichtung kostet nichts: bleibt der Schalter auf „an",
/// während das Modell weg ist, weist `lauschen_starten` den nächsten Start
/// mit „Kein Wake-Word trainiert" ab — kein Mikrofon geht heimlich an, und
/// der Fehler stand vorher im Klartext auf dem Bildschirm.
pub fn zuruecksetzen(app: &AppHandle) -> Result<(), String> {
    let schalter = schalter_aus(app);
    let dateien = aufnahmen_loeschen(app);
    beides_melden(schalter, dateien)
}

/// Legt den Aktiv-Schalter um und vergisst das trainierte Wort.
fn schalter_aus(app: &AppHandle) -> Result<(), String> {
    let mut konfig = crate::konfig::laden(app)?;
    konfig.wakeword_aktiv = false;
    konfig.wakeword_wort = None;
    crate::konfig::speichern(app, &konfig)
}

/// Stoppt das Lauschen und löscht Aufnahmen, Modell und Verfahrensmarke.
///
/// Getrennt von `zuruecksetzen`, weil der Uninstaller genau das braucht und
/// sonst nichts: dort wird das Konfig-Verzeichnis gleich danach ohnehin
/// gelöscht, und `deinstallation.rs` gibt es allein dafür, dass keine
/// Stimmaufnahmen zurückbleiben. Diese Zusage darf nicht an einem Schreibzugriff
/// hängen, der mit den Aufnahmen nichts zu tun hat.
///
/// Zuerst das Mikrofon: ein laufender Lauschthread hielte sonst Dateien offen,
/// die gleich verschwinden sollen.
pub fn aufnahmen_loeschen(app: &AppHandle) -> Result<(), String> {
    lauschen_stoppen();
    let verzeichnis = wakeword_verzeichnis(app)?;
    std::fs::remove_dir_all(&verzeichnis).map_err(|e| e.to_string())
}

/// Zwei unabhängige Teilschritte, eine Meldung — keiner darf den anderen
/// verschlucken. Ein `?` zwischen ihnen hätte den zweiten gar nicht erst
/// laufen lassen; ein `let _ =` hätte seinen Fehler verschwiegen.
fn beides_melden(erstes: Result<(), String>, zweites: Result<(), String>) -> Result<(), String> {
    let fehler: Vec<String> = [erstes.err(), zweites.err()].into_iter().flatten().collect();
    if fehler.is_empty() {
        return Ok(());
    }
    Err(fehler.join(" — "))
}

/// Startet einen cpal-Eingabestream und liefert f32-Samples (interleaved,
/// Geräteformat) über den Channel. F32- und I16-Geräte werden unterstützt —
/// das deckt praktisch jede Windows-Aufnahmekette ab.
fn stream_starten(
    geraet: &cpal::Device,
    konfig: &cpal::SupportedStreamConfig,
    sender: mpsc::Sender<Vec<f32>>,
) -> Result<cpal::Stream, String> {
    // Nicht-fangende Closure: Copy, darf in beide Arme wandern.
    let fehler_melden = |fehler| eprintln!("Mikrofon-Fehler: {fehler}");
    let stream = match konfig.sample_format() {
        cpal::SampleFormat::F32 => geraet
            .build_input_stream(
                konfig.config(),
                move |daten: &[f32], _: &cpal::InputCallbackInfo| {
                    let _ = sender.send(daten.to_vec());
                },
                fehler_melden,
                None,
            )
            .map_err(|e| e.to_string())?,
        cpal::SampleFormat::I16 => geraet
            .build_input_stream(
                konfig.config(),
                move |daten: &[i16], _: &cpal::InputCallbackInfo| {
                    let _ = sender.send(
                        daten.iter().map(|s| *s as f32 / i16::MAX as f32).collect(),
                    );
                },
                fehler_melden,
                None,
            )
            .map_err(|e| e.to_string())?,
        anderes => return Err(format!("Mikrofonformat nicht unterstützt: {anderes}")),
    };
    stream.play().map_err(|e| e.to_string())?;
    Ok(stream)
}

/// Das Eingabegerät samt Format — das gewählte aus konfig.json oder der
/// Windows-Standard (audio::eingang_finden erklärt den stillen Rückfall).
fn mikrofon(app: &AppHandle) -> Result<(cpal::Device, cpal::SupportedStreamConfig), String> {
    let bevorzugt = crate::konfig::laden(app).ok().and_then(|k| k.audio_eingabe);
    let geraet =
        crate::audio::eingang_finden(bevorzugt.as_deref()).ok_or("Kein Mikrofon gefunden")?;
    let konfig = geraet
        .default_input_config()
        .map_err(|e| format!("Mikrofon nicht lesbar: {e}"))?;
    Ok((geraet, konfig))
}

/// Mischt interleavte Samples auf Mono herunter.
fn nach_mono(daten: &[f32], kanaele: usize, ziel: &mut Vec<f32>) {
    for rahmen in daten.chunks(kanaele) {
        let summe: f32 = rahmen.iter().sum();
        ziel.push(summe / kanaele as f32);
    }
}

/// Klemmt die Benutzer-Empfindlichkeit auf den Bereich, in dem die Erkennung
/// noch etwas taugt: unter 0,30 feuert Median-Score auf Alltagsgeräusche,
/// über 0,60 hört sie den eigenen Namen nicht mehr.
pub fn schwelle_klemmen(wert: f32) -> f32 {
    if !wert.is_finite() {
        return crate::konfig::WAKEWORD_SCHWELLE_VORGABE;
    }
    wert.clamp(0.40, 0.75)
}

/// Quadratischer Mittelwert eines Fensters — das Maß für „hier ist Energie".
fn rms(fenster: &[f32]) -> f32 {
    if fenster.is_empty() {
        return 0.0;
    }
    (fenster.iter().map(|s| s * s).sum::<f32>() / fenster.len() as f32).sqrt()
}

/// Schneidet aus einem Mono-Strom die Aufnahme ab Spracheinsatz.
///
/// Vorher nahm `aufnehmen` blind die ersten 2,2 Sekunden: wer erst nach einem
/// Atemzug sprach, lieferte ein halbes Wort, und wer schwieg, lieferte Stille
/// — beides zählte als gültige Kalibrierung, und das Modell lernte Rauschen.
/// Jetzt öffnet Sprachenergie das Tor: gewartet wird bis zu
/// `SPRACHE_WARTEN_SEKUNDEN` auf ein 20-ms-Fenster über der Schwelle,
/// geschnitten ab Einsatz minus Vorlauf, und wer nichts sagt, bekommt einen
/// Fehler statt einer stillen Aufnahme.
///
/// Getrennt von cpal, damit es testbar ist: `naechster_block` liefert
/// Mono-Blöcke, `None` heißt „das Mikrofon liefert nichts mehr".
fn sprache_schneiden(
    mut naechster_block: impl FnMut() -> Option<Vec<f32>>,
    rate: u32,
) -> Result<Vec<f32>, String> {
    let fenster_len = (rate as usize / 50).max(1);
    let vorlauf_len = (rate as f32 * VORLAUF_SEKUNDEN) as usize;
    let warte_len = (rate as f32 * SPRACHE_WARTEN_SEKUNDEN) as usize;
    let stille_len = (rate as f32 * STILLE_SEKUNDEN) as usize;
    let nachlauf_len = (rate as f32 * NACHLAUF_SEKUNDEN) as usize;

    // Noch nicht in Fenster zerlegte Samples, der Vorlauf vor dem Einsatz
    // und die eigentliche Aufnahme. `ziel` ist die Notbremse (Vorlauf plus
    // AUFNAHME_SEKUNDEN ab Einsatz); der Normalfall ist der Abbruch an der
    // Stille, gezaehlt in `stille`.
    let mut rest: Vec<f32> = Vec::new();
    let mut vorlauf: Vec<f32> = Vec::new();
    let mut aufnahme: Vec<f32> = Vec::new();
    let mut ziel: Option<usize> = None;
    let mut gewartet = 0usize;
    let mut stille = 0usize;

    while ziel.is_none_or(|z| aufnahme.len() < z) {
        while rest.len() < fenster_len {
            match naechster_block() {
                Some(block) => rest.extend(block),
                None => {
                    // Eine angebrochene Aufnahme ab einer halben Sekunde ist
                    // brauchbar — dasselbe Maß wie vor dem Umbau. Alles
                    // darunter ist ein Fehler, keine Kalibrierung.
                    return match ziel {
                        Some(_) if aufnahme.len() >= (rate / 2) as usize => Ok(aufnahme),
                        Some(_) => Err("Aufnahme zu kurz — liefert das Mikrofon Daten?".into()),
                        None => Err(NICHTS_GEHOERT.into()),
                    };
                }
            }
        }
        let fenster: Vec<f32> = rest.drain(..fenster_len).collect();
        let laut = rms(&fenster) >= SPRACHE_RMS;
        if ziel.is_some() {
            // **Am Sprachende schneiden, nicht nach fester Länge.** Bis zum
            // 23.08.2026 lief hier stur bis `AUFNAHME_SEKUNDEN` weiter, egal
            // wann der Sprecher aufhörte. Ein Agentenname dauert 0,5–0,8 s;
            // der Rest der Aufnahme war Raumton. Das ist nicht bloss Ballast:
            // rustpotter leitet aus der längsten Vorlage sein Suchfenster
            // **und** seine Nachlaufuhr ab, und beides wurde damit doppelt
            // so gross wie nötig.
            aufnahme.extend(fenster);
            if laut {
                stille = 0;
            } else {
                stille += fenster_len;
                if stille >= stille_len {
                    break;
                }
            }
        } else if laut {
            // Der Einsatz: Vorlauf mitnehmen, ab hier sammeln. `ziel` ist
            // jetzt nur noch die Notbremse — normalerweise endet die
            // Aufnahme vorher an der Stille.
            aufnahme.append(&mut vorlauf);
            ziel = Some(aufnahme.len() + (rate as f32 * AUFNAHME_SEKUNDEN) as usize);
            aufnahme.extend(fenster);
        } else {
            vorlauf.extend(fenster);
            if vorlauf.len() > vorlauf_len {
                let ueberhang = vorlauf.len() - vorlauf_len;
                vorlauf.drain(..ueberhang);
            }
            gewartet += fenster_len;
            if gewartet >= warte_len {
                return Err(NICHTS_GEHOERT.into());
            }
        }
    }
    if let Some(z) = ziel {
        aufnahme.truncate(z);
    }
    // Die gezaehlte Stille am Ende wieder abschneiden, aber einen kurzen
    // Nachlauf stehen lassen: der Auslaut eines Wortes ist leise und faellt
    // sonst mit unter die Schwelle. Was hier zu viel bleibt, verlaengert
    // Suchfenster und Nachlaufuhr von rustpotter.
    if stille > nachlauf_len {
        let weg = (stille - nachlauf_len).min(aufnahme.len());
        aufnahme.truncate(aufnahme.len() - weg);
    }
    Ok(aufnahme)
}

/// Nimmt eine Kalibrierungs-Aufnahme auf und schreibt sie als
/// 16-bit-Mono-WAV. Nummer 1..=10; eine vorhandene Aufnahme derselben Nummer
/// wird ersetzt. Blockiert, bis gesprochen wurde — höchstens ~7,5 s (5 s
/// warten, 2,5 s schneiden).
pub fn aufnehmen(app: &AppHandle, nummer: u8) -> Result<String, String> {
    if nummer == 0 || nummer > AUFNAHMEN_SOLL {
        return Err(format!("Aufnahmenummer 1..={AUFNAHMEN_SOLL} erwartet"));
    }
    if LAUSCHT.load(Ordering::SeqCst) {
        return Err("Erst das Lauschen stoppen, dann kalibrieren".into());
    }
    let (geraet, konfig) = mikrofon(app)?;
    let rate = konfig.sample_rate();
    let kanaele = konfig.channels() as usize;

    let (sender, empfaenger) = mpsc::channel::<Vec<f32>>();
    let stream = stream_starten(&geraet, &konfig, sender)?;

    let mono = sprache_schneiden(
        || {
            empfaenger
                .recv_timeout(Duration::from_secs(2))
                .ok()
                .map(|block| {
                    let mut mono = Vec::with_capacity(block.len() / kanaele.max(1));
                    nach_mono(&block, kanaele, &mut mono);
                    mono
                })
        },
        rate,
    );
    drop(stream);
    let mono = mono?;

    let pfad = wakeword_verzeichnis(app)?.join(format!("aufnahme-{nummer:02}.wav"));
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut schreiber = hound::WavWriter::create(&pfad, spec).map_err(|e| e.to_string())?;
    for s in &mono {
        let wert = (s.clamp(-1.0, 1.0) * i16::MAX as f32) as i16;
        schreiber.write_sample(wert).map_err(|e| e.to_string())?;
    }
    schreiber.finalize().map_err(|e| e.to_string())?;
    Ok(pfad.to_string_lossy().into_owned())
}

/// Baut aus allen vorhandenen Aufnahmen das Referenzmodell.
pub fn trainieren(app: &AppHandle, wort: &str) -> Result<(), String> {
    let wort = wort.trim();
    if wort.is_empty() || wort.len() > 64 || wort.lines().count() > 1 {
        return Err("Das Wake-Word muss eine kurze, einzeilige Bezeichnung sein".into());
    }
    let verzeichnis = wakeword_verzeichnis(app)?;
    let dateien = aufnahme_dateien(&verzeichnis);
    if dateien.len() < AUFNAHMEN_MINDESTENS {
        return Err(format!(
            "Mindestens {AUFNAHMEN_MINDESTENS} Aufnahmen nötig, vorhanden: {}",
            dateien.len()
        ));
    }
    // threshold/avg_threshold None: die Schwellen kommen zur Laufzeit aus der
    // Detector-Konfiguration — eine Stelle, nicht zwei.
    let referenz =
        WakewordRef::new_from_sample_files(wort.to_string(), None, None, dateien, MFCC_GROESSE)?;
    let pfad = verzeichnis.join(MODELL_DATEI);
    referenz.save_to_file(pfad.to_str().ok_or("Modellpfad nicht darstellbar")?)?;
    // Erst nach dem Modell und mit ignoriertem Fehler: die Marke ist ein
    // Hinweis, kein Teil des Modells. Laesst sie sich nicht schreiben, bietet
    // die App eine ueberfluessige Neukalibrierung an — das ist die harmlose
    // Richtung. Umgekehrt (Marke da, Modell nicht) waere es die schaedliche.
    let _ = std::fs::write(verzeichnis.join(VERFAHREN_DATEI), VERFAHREN.to_string());
    Ok(())
}

/// Startet den Lausch-Thread. Idempotent: läuft schon einer, passiert nichts.
/// Läuft ein Vorgänger gerade aus (Durchstart), wird auf ihn gewartet — das
/// dauert höchstens einen Channel-Timeout (~500 ms) und geschieht auf einem
/// Command-Thread, nie in der UI.
pub fn lauschen_starten(app: AppHandle) -> Result<(), String> {
    let modell = modell_pfad(&app)?;
    if !modell.exists() {
        return Err("Kein Wake-Word trainiert — erst kalibrieren".into());
    }
    let mut halter = FADEN.lock().map_err(|_| "Wake-Word-Zustand vergiftet")?;
    if let Some(faden) = halter.take() {
        if LAUSCHT.load(Ordering::SeqCst) && !faden.is_finished() {
            // Läuft und soll laufen — nichts zu tun.
            *halter = Some(faden);
            return Ok(());
        }
        // Ein Stopp ist unterwegs oder der Thread ist schon zu Ende: erst zu
        // Ende bringen, dann frisch starten. Ohne das Join sähe der Alte das
        // gleich wieder gesetzte Flag und liefe einfach weiter.
        LAUSCHT.store(false, Ordering::SeqCst);
        let _ = faden.join();
    }
    LAUSCHT.store(true, Ordering::SeqCst);
    let faden = std::thread::Builder::new()
        .name("mss-wakeword".into())
        .spawn(move || {
            if let Err(fehler) = lausch_schleife(&app, &modell) {
                // Ohne das Event stürbe der Thread still: die UI hätte einen
                // Schalter auf „an", hinter dem nichts mehr lauscht. Nur die
                // Meldung geht raus — nie Audio.
                eprintln!("Wake-Word-Lauschen beendet: {fehler}");
                let _ = app.emit("wakeword-fehler", serde_json::json!({ "meldung": fehler }));
            }
            LAUSCHT.store(false, Ordering::SeqCst);
        })
        .map_err(|e| {
            LAUSCHT.store(false, Ordering::SeqCst);
            e.to_string()
        })?;
    *halter = Some(faden);
    Ok(())
}

/// Bittet den Lausch-Thread zu enden (er tut es binnen ~500 ms).
pub fn lauschen_stoppen() {
    LAUSCHT.store(false, Ordering::SeqCst);
}

fn lausch_schleife(app: &AppHandle, modell: &Path) -> Result<(), String> {
    let (geraet, konfig) = mikrofon(app)?;
    let rate = konfig.sample_rate();
    let kanaele = konfig.channels() as usize;

    // rustpotter bekommt Mono-f32 im Geräte-Takt und rechnet intern um.
    let mut rp_konfig = RustpotterConfig::default();
    rp_konfig.fmt.sample_rate = rate as usize;
    rp_konfig.fmt.channels = 1;
    // Der Gain-Normalizer ist der Hebel für schlechte und leise Mikrofone:
    // er zieht den Live-Pegel auf den **Median der Frame-RMS** der eigenen
    // Kalibrierungsaufnahmen (gain_ref None = Referenz aus dem Modell).
    // Hier stand „auf die RMS der Aufnahmen" — das klang nach Sprachpegel,
    // ist aber der Median über alle 30-ms-Blöcke der Datei. Solange jede
    // Aufnahme zu drei Vierteln aus Raumton bestand, war der Median-Block
    // ein Stille-Block, und der Filter zog live eher herunter als herauf.
    // Mit dem Schnitt am Sprachende (siehe `AUFNAHME_SEKUNDEN`) trifft die
    // Referenz von selbst einen Sprach-Block. Ab Werk ist er **aus**, und
    // max_gain 1.0 hieße „nur dämpfen, nie verstärken" — ein leises Mikrofon
    // käme nie auf den Pegel, mit dem trainiert wurde. Der Preis: er hebt
    // auch Rauschen an — deshalb die drei Tore darunter.
    rp_konfig.filters.gain_normalizer.enabled = true;
    rp_konfig.filters.gain_normalizer.max_gain = 1.8;
    rp_konfig.detector.vad_mode = Some(VADMode::Medium);
    rp_konfig.detector.score_mode = ScoreMode::Median;
    rp_konfig.detector.avg_threshold = 0.38;
    rp_konfig.detector.threshold = schwelle_klemmen(
        crate::konfig::laden(app)
            .map(|k| k.wakeword_schwelle)
            .unwrap_or(crate::konfig::WAKEWORD_SCHWELLE_VORGABE),
    );
    rp_konfig.detector.min_scores = 5;
    rp_konfig.detector.eager = true;
    let mut erkenner = Rustpotter::new(&rp_konfig)?;
    erkenner.add_wakeword_from_file(
        "wakeword",
        modell.to_str().ok_or("Modellpfad nicht darstellbar")?,
    )?;
    let je_frame = erkenner.get_samples_per_frame();

    let (sender, empfaenger) = mpsc::channel::<Vec<f32>>();
    let stream = stream_starten(&geraet, &konfig, sender)?;

    let mut puffer: Vec<f32> = Vec::with_capacity(je_frame * 4);
    let mut letzter_pegel = std::time::Instant::now();
    let mut letzter_treffer = std::time::Instant::now() - Duration::from_secs(5);
    while LAUSCHT.load(Ordering::SeqCst) {
        match empfaenger.recv_timeout(Duration::from_millis(500)) {
            Ok(block) => {
                nach_mono(&block, kanaele, &mut puffer);
                if letzter_pegel.elapsed() >= Duration::from_millis(250) {
                    letzter_pegel = std::time::Instant::now();
                    let _ = app.emit(
                        "wakeword-pegel",
                        serde_json::json!({
                            "rms": erkenner.get_rms_level(),
                            "gain": erkenner.get_gain(),
                        }),
                    );
                }
                while puffer.len() >= je_frame {
                    let frame: Vec<f32> = puffer.drain(..je_frame).collect();
                    if let Some(erkennung) = erkenner.process_samples(frame) {
                        if letzter_treffer.elapsed() >= Duration::from_millis(2500) {
                            letzter_treffer = std::time::Instant::now();
                            let _ = app.emit(
                                "wakeword-erkannt",
                                serde_json::json!({
                                    "name": erkennung.name,
                                    "score": erkennung.score,
                                }),
                            );
                            crate::am_hauptthread(app, crate::sprachsitzung_starten);
                        }
                    }
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    drop(stream);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const RATE: u32 = 16_000;

    /// Blockfolge als Closure — was sonst cpal liefert, hier von Hand.
    fn quelle(bloecke: Vec<Vec<f32>>) -> impl FnMut() -> Option<Vec<f32>> {
        let mut rest = bloecke.into_iter();
        move || rest.next()
    }

    #[test]
    fn stille_ist_keine_aufnahme() {
        // Fünf Sekunden unter der Schwelle: vorher wäre das eine gültige
        // Kalibrierung gewesen — das Modell hätte Stille gelernt und beim
        // Lauschen entweder nie oder dauernd ausgelöst.
        let bloecke = vec![vec![0.0_f32; 1_600]; 60];
        let fehler = sprache_schneiden(quelle(bloecke), RATE).unwrap_err();
        assert!(fehler.contains("Nichts gehört"), "{fehler}");
    }

    #[test]
    fn schnitt_beginnt_mit_dem_vorlauf_vor_dem_einsatz() {
        // Eine Sekunde Atemholen, dann Sprache: geschnitten wird ab Einsatz,
        // mit 300 ms Vorlauf davor — Wortanfänge sind leise, und ein Schnitt
        // genau am Schwellendurchgang verlöre die erste Silbe.
        let mut bloecke = vec![vec![0.0_f32; 1_600]; 10];
        bloecke.extend(vec![vec![0.5_f32; 1_600]; 30]);
        let aufnahme = sprache_schneiden(quelle(bloecke), RATE).unwrap();

        let vorlauf = (RATE as f32 * VORLAUF_SEKUNDEN) as usize;
        // Hier wird ohne Pause weitergesprochen (30 laute Bloecke = 3 s),
        // also greift die Notbremse `AUFNAHME_SEKUNDEN`.
        let deckel = vorlauf + (RATE as f32 * AUFNAHME_SEKUNDEN) as usize;
        assert_eq!(aufnahme.len(), deckel);
        assert_eq!(aufnahme[0], 0.0, "vor dem Einsatz steht der stille Vorlauf");
        assert!(aufnahme[vorlauf] > 0.0, "am Einsatz muss Sprache stehen");
    }

    #[test]
    fn der_schnitt_endet_am_sprachende_nicht_an_der_uhr() {
        // Der eigentliche Umbau vom 23.08.2026. Ein Name dauert 0,5-0,8 s;
        // vorher wurden daraus stur 2,5 s, und die restlichen 1,8 s
        // Raumton bestimmten anschliessend Suchfenster und Nachlaufuhr von
        // rustpotter. Gemessen kam die Erkennung dadurch 3,08 s nach dem
        // Wort - fuer einen Menschen ist das "reagiert nicht".
        let mut bloecke = vec![vec![0.0_f32; 1_600]; 5]; // 0,5 s Ruhe
        bloecke.extend(vec![vec![0.5_f32; 1_600]; 7]); // 0,7 s Wort
        bloecke.extend(vec![vec![0.0_f32; 1_600]; 30]); // danach still
        let aufnahme = sprache_schneiden(quelle(bloecke), RATE).unwrap();

        let vorlauf = (RATE as f32 * VORLAUF_SEKUNDEN) as usize;
        let wort = (RATE as f32 * 0.7) as usize;
        let nachlauf = (RATE as f32 * NACHLAUF_SEKUNDEN) as usize;
        let erwartet = vorlauf + wort + nachlauf;
        // Auf ein 20-ms-Fenster genau: die Stille wird blockweise gezaehlt.
        let fenster = RATE as usize / 50;
        assert!(
            aufnahme.len().abs_diff(erwartet) <= fenster,
            "erwartet ~{erwartet}, bekommen {}",
            aufnahme.len()
        );
        // Und ausdruecklich: deutlich kuerzer als die alte Festlaenge.
        let alte_festlaenge = vorlauf + (RATE as f32 * AUFNAHME_SEKUNDEN) as usize;
        assert!(aufnahme.len() < alte_festlaenge / 2);
    }

    #[test]
    fn eine_kalibrierung_ohne_marke_gilt_als_veraltet() {
        // Wer vor dem 23.08.2026 kalibriert hat, hat 2,5-s-Schablonen. Die
        // App muss das anbieten koennen, sonst laeuft die Reparatur ins
        // Leere: an ueberlangen Vorlagen aendert keine Einstellung etwas.
        let ordner = std::env::temp_dir().join(format!("mss-verfahren-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&ordner);
        assert_eq!(verfahren_lesen(&ordner), 1, "keine Marke heisst: von frueher");
        std::fs::write(ordner.join(VERFAHREN_DATEI), VERFAHREN.to_string()).unwrap();
        assert_eq!(verfahren_lesen(&ordner), VERFAHREN);
        // Und Unsinn in der Datei zaehlt wie keine Datei — nie wie "aktuell".
        std::fs::write(ordner.join(VERFAHREN_DATEI), "kaputt").unwrap();
        assert_eq!(verfahren_lesen(&ordner), 1);
        let _ = std::fs::remove_dir_all(&ordner);
    }

    #[test]
    fn eine_pause_zwischen_zwei_silben_schneidet_nicht() {
        // 200 ms Luftholen mitten im Wort liegen unter `STILLE_SEKUNDEN`.
        // Waere die Grenze knapper, schnitte sie Namen in der Mitte durch -
        // und die Schablone lernte eine halbe Silbe.
        let mut bloecke = vec![vec![0.5_f32; 1_600]; 4]; // erste Silbe
        bloecke.extend(vec![vec![0.0_f32; 1_600]; 2]); // 0,2 s Pause
        bloecke.extend(vec![vec![0.5_f32; 1_600]; 4]); // zweite Silbe
        bloecke.extend(vec![vec![0.0_f32; 1_600]; 30]);
        let aufnahme = sprache_schneiden(quelle(bloecke), RATE).unwrap();

        // Beide Silben plus die Pause dazwischen sind drin.
        let beide = (RATE as f32 * 1.0) as usize;
        assert!(aufnahme.len() >= beide, "die Pause hat das Wort zerschnitten");
    }

    #[test]
    fn wer_sofort_spricht_hat_eben_keinen_vorlauf() {
        // Der Vorlauf ist, was da war — nicht 300 ms, die es nie gab. Warten
        // oder Auffüllen verfälschte den Einsatz.
        let bloecke = vec![vec![0.5_f32; 1_600]; 30];
        let aufnahme = sprache_schneiden(quelle(bloecke), RATE).unwrap();

        assert_eq!(aufnahme.len(), (RATE as f32 * AUFNAHME_SEKUNDEN) as usize);
        assert!(aufnahme[0] > 0.0);
    }

    #[test]
    fn angebrochene_aufnahme_ab_halber_sekunde_zaehlt() {
        // Das Mikrofon stirbt nach dem Einsatz: ab einer halben Sekunde ist
        // die Aufnahme brauchbar — dasselbe Maß wie vor dem Umbau.
        let mut bloecke = vec![vec![0.0_f32; 1_600]];
        bloecke.extend(vec![vec![0.5_f32; 1_600]; 6]);
        let aufnahme = sprache_schneiden(quelle(bloecke), RATE).unwrap();

        // 1.600 Samples Vorlauf plus 9.600 gesprochene — mehr kam nicht.
        assert_eq!(aufnahme.len(), 11_200);
    }

    #[test]
    fn totes_mikrofon_kurz_nach_dem_einsatz_ist_zu_kurz() {
        // Einsatz erkannt, aber nur 0,2 s Sprache: daraus lernt niemand ein
        // Wort — der Fehler sagt „zu kurz", nicht „nichts gehört".
        let bloecke = vec![vec![0.0_f32; 1_600], vec![0.5_f32; 3_200]];
        let fehler = sprache_schneiden(quelle(bloecke), RATE).unwrap_err();
        assert!(fehler.contains("zu kurz"), "{fehler}");
    }

    #[test]
    fn keiner_der_beiden_teilfehler_verschwindet_in_der_meldung() {
        // Geprüft wird `beides_melden`, nicht `zuruecksetzen` — das braucht
        // einen `AppHandle` und ist hier nicht erreichbar. Dass beide Schritte
        // überhaupt laufen, hängt allein daran, dass zwischen ihnen kein `?`
        // steht; das sieht dieser Test nicht, das sieht nur ein Leser von
        // `zuruecksetzen`.
        //
        // Was er sieht, ist die andere Hälfte derselben Zusage: kommen zwei
        // Fehler an, verschweigt die Meldung keinen. Vorher stand hier ein
        // `let _ =` — der Uninstaller meldete den Fehler des Speicherns und
        // schwieg darüber, dass die Stimmaufnahmen liegengeblieben waren.
        assert!(beides_melden(Ok(()), Ok(())).is_ok());
        assert_eq!(beides_melden(Err("Schalter".into()), Ok(())).unwrap_err(), "Schalter");
        assert_eq!(beides_melden(Ok(()), Err("Aufnahmen".into())).unwrap_err(), "Aufnahmen");
        let beide = beides_melden(Err("Schalter".into()), Err("Aufnahmen".into())).unwrap_err();
        assert!(beide.contains("Schalter"), "{beide}");
        assert!(beide.contains("Aufnahmen"), "{beide}");
    }

    #[test]
    fn die_schwelle_bleibt_im_brauchbaren_bereich() {
        // Werte kommen aus konfig.json und damit potenziell von Hand: NaN,
        // 0 oder 3 dürfen die Erkennung weder dauerfeuern lassen noch taub
        // machen.
        assert_eq!(schwelle_klemmen(0.55), 0.55);
        assert_eq!(schwelle_klemmen(0.0), 0.40);
        assert_eq!(schwelle_klemmen(3.0), 0.75);
        assert_eq!(
            schwelle_klemmen(f32::NAN),
            crate::konfig::WAKEWORD_SCHWELLE_VORGABE
        );
    }

    #[test]
    fn totes_mikrofon_vor_dem_einsatz_ist_nichts_gehoert() {
        // Kein einziges Fenster über der Schwelle, dann Stromende: für den
        // Benutzer ist das dieselbe Auskunft wie Schweigen — sprich lauter,
        // prüf das Mikrofon.
        let bloecke = vec![vec![0.0_f32; 1_600]; 2];
        let fehler = sprache_schneiden(quelle(bloecke), RATE).unwrap_err();
        assert!(fehler.contains("Nichts gehört"), "{fehler}");
    }
}
