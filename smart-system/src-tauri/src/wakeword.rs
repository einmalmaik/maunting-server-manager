//! Lokales Wake-Word: Kalibrierung, Training und Erkennung — 100 % auf dem
//! Rechner, ohne Cloud und ohne permanentes Streaming.
//!
//! Engine ist rustpotter (Apache-2.0): der Nutzer spricht seinen
//! Agenten-Namen bis zu zehnmal ein, daraus entsteht ein Referenzmodell
//! (MFCC + Dynamic Time Warping) — genau der Weg, den rustpotter für
//! persönliche Wake-Words vorsieht. Kein vortrainiertes Netz nötig, der
//! Name ist frei wählbar.
//!
//! Ablauf:
//! 1. `aufnehmen(n)` — wartet auf Sprachenergie (bis ~5 s), schneidet ab
//!    Einsatz ~2,2 s plus 300 ms Vorlauf und legt sie als Mono-WAV unter
//!    `<app-local-data>/wakeword/aufnahme-NN.wav` ab. Stille ist ein
//!    Fehler, keine Aufnahme.
//! 2. `trainieren(wort)` — baut aus allen Aufnahmen ein Referenzmodell
//!    und speichert es als `wakeword.rpw`.
//! 3. `lauschen_starten` — ein einzelner Thread liest das Mikrofon und
//!    füttert rustpotter Frame für Frame; bei einem Treffer geht das
//!    Tauri-Event `wakeword-erkannt` an die Fenster. Erst **danach**
//!    öffnet die App den Audiokanal zur KI — vorher verlässt kein Sample
//!    den Prozess.
//!
//! Zero-Resource: der Lausch-Thread blockiert auf einem Channel (kein
//! Polling); Stoppen setzt ein Flag, der Thread endet binnen 500 ms und
//! gibt das Mikrofon frei.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::time::Duration;

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use rustpotter::{
    Rustpotter, RustpotterConfig, WakewordRef, WakewordRefBuildFromFiles, WakewordSave,
};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

/// Ziel-Anzahl der Kalibrierungs-Aufnahmen (Onboarding: „sprich den Namen 10x“).
pub const AUFNAHMEN_SOLL: u8 = 10;
/// rustpotter braucht mindestens drei Aufnahmen für eine brauchbare Referenz.
const AUFNAHMEN_MINDESTENS: usize = 3;
const AUFNAHME_SEKUNDEN: f32 = 2.2;
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
/// MFCC-Auflösung; 16 ist der rustpotter-Standard für Referenzmodelle.
const MFCC_GROESSE: u16 = 16;

/// Ob der Lausch-Thread laufen soll. Ein Flag statt Thread-Handles: der
/// Thread gehört sich selbst, Stoppen ist nur ein Wunsch, den er binnen
/// eines Channel-Timeouts erfüllt.
static LAUSCHT: AtomicBool = AtomicBool::new(false);

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

/// Löscht Aufnahmen und Modell — für „Wake-Word neu einrichten“ und für den
/// Uninstaller (temporäre Audiodaten restlos entfernen). Der Aktiv-Schalter
/// und das trainierte Wort fallen mit: ein Schalter auf „an" ohne Modell
/// dahinter wäre eine Anzeige, die lügt.
pub fn zuruecksetzen(app: &AppHandle) -> Result<(), String> {
    lauschen_stoppen();
    let verzeichnis = wakeword_verzeichnis(app)?;
    std::fs::remove_dir_all(&verzeichnis).map_err(|e| e.to_string())?;
    if let Ok(mut konfig) = crate::konfig::laden(app) {
        konfig.wakeword_aktiv = false;
        konfig.wakeword_wort = None;
        let _ = crate::konfig::speichern(app, &konfig);
    }
    Ok(())
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

    // Noch nicht in Fenster zerlegte Samples, der Vorlauf vor dem Einsatz
    // und die eigentliche Aufnahme. `ziel` ist erst gesetzt, wenn gesprochen
    // wurde: Vorlauf plus 2,2 s ab Einsatz.
    let mut rest: Vec<f32> = Vec::new();
    let mut vorlauf: Vec<f32> = Vec::new();
    let mut aufnahme: Vec<f32> = Vec::new();
    let mut ziel: Option<usize> = None;
    let mut gewartet = 0usize;

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
        if ziel.is_some() {
            aufnahme.extend(fenster);
        } else if rms(&fenster) >= SPRACHE_RMS {
            // Der Einsatz: Vorlauf mitnehmen, ab hier bis zur Ziellänge sammeln.
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
    Ok(())
}

/// Startet den Lausch-Thread. Idempotent: läuft schon einer, passiert nichts.
pub fn lauschen_starten(app: AppHandle) -> Result<(), String> {
    let modell = modell_pfad(&app)?;
    if !modell.exists() {
        return Err("Kein Wake-Word trainiert — erst kalibrieren".into());
    }
    if LAUSCHT.swap(true, Ordering::SeqCst) {
        return Ok(());
    }
    std::thread::Builder::new()
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
    // er zieht den Live-Pegel auf die RMS der eigenen Kalibrierungsaufnahmen
    // (gain_ref None = Referenz aus dem Modell). Ab Werk ist er **aus**, und
    // max_gain 1.0 hieße „nur dämpfen, nie verstärken" — ein leises Mikrofon
    // käme nie auf den Pegel, mit dem trainiert wurde. 4.0 verstärkt kräftig,
    // ohne aus Raumrauschen Sprache zu machen.
    rp_konfig.filters.gain_normalizer.enabled = true;
    rp_konfig.filters.gain_normalizer.max_gain = 4.0;
    // Etwas unter der Werksschwelle (0,5): zehn eigene Aufnahmen streuen, und
    // ein verpasstes Wort ist hier teurer als ein seltener Fehlgriff — der
    // öffnet nur das Overlay, das ESC gleich wieder schließt.
    rp_konfig.detector.threshold = 0.45;
    // Das Durchschnitts-Vor-Tor (Werk 0,2) ist eine reine CPU-Sparmaßnahme:
    // es bricht Erkennungen ab, bevor die eigentlichen Vergleiche laufen.
    // Bei einem einzigen Stream ist die Ersparnis egal, die abgebrochenen
    // Treffer sind es nicht. 0 heißt aus.
    rp_konfig.detector.avg_threshold = 0.0;
    let mut erkenner = Rustpotter::new(&rp_konfig)?;
    erkenner.add_wakeword_from_file(
        "wakeword",
        modell.to_str().ok_or("Modellpfad nicht darstellbar")?,
    )?;
    let je_frame = erkenner.get_samples_per_frame();

    let (sender, empfaenger) = mpsc::channel::<Vec<f32>>();
    let stream = stream_starten(&geraet, &konfig, sender)?;

    let mut puffer: Vec<f32> = Vec::with_capacity(je_frame * 4);
    while LAUSCHT.load(Ordering::SeqCst) {
        match empfaenger.recv_timeout(Duration::from_millis(500)) {
            Ok(block) => {
                nach_mono(&block, kanaele, &mut puffer);
                while puffer.len() >= je_frame {
                    let frame: Vec<f32> = puffer.drain(..je_frame).collect();
                    if let Some(erkennung) = erkenner.process_samples(frame) {
                        // Nur Name und Score verlassen dieses Modul — nie Audio.
                        let _ = app.emit(
                            "wakeword-erkannt",
                            serde_json::json!({
                                "name": erkennung.name,
                                "score": erkennung.score,
                            }),
                        );
                        // Das Overlay öffnet **hier**, nicht im Hauptfenster-JS:
                        // ein verstecktes WebView darf gedrosselt sein, Rust
                        // nicht — das Wake-Word muss auch dann tragen, wenn
                        // kein Fenster offen ist. Läuft dort schon eine
                        // Sitzung, passiert nichts.
                        crate::sprachsitzung_starten(app);
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
        let ziel = vorlauf + (RATE as f32 * AUFNAHME_SEKUNDEN) as usize;
        assert_eq!(aufnahme.len(), ziel);
        assert_eq!(aufnahme[0], 0.0, "vor dem Einsatz steht der stille Vorlauf");
        assert!(aufnahme[vorlauf] > 0.0, "am Einsatz muss Sprache stehen");
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
    fn totes_mikrofon_vor_dem_einsatz_ist_nichts_gehoert() {
        // Kein einziges Fenster über der Schwelle, dann Stromende: für den
        // Benutzer ist das dieselbe Auskunft wie Schweigen — sprich lauter,
        // prüf das Mikrofon.
        let bloecke = vec![vec![0.0_f32; 1_600]; 2];
        let fehler = sprache_schneiden(quelle(bloecke), RATE).unwrap_err();
        assert!(fehler.contains("Nichts gehört"), "{fehler}");
    }
}
