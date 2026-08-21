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
//! 1. `aufnehmen(n)` — nimmt ~2,2 s vom Standardmikrofon auf und legt sie
//!    als Mono-WAV unter `<app-local-data>/wakeword/aufnahme-NN.wav` ab.
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
const MODELL_DATEI: &str = "wakeword.rpw";
/// MFCC-Auflösung; 16 ist der rustpotter-Standard für Referenzmodelle.
const MFCC_GROESSE: u16 = 16;

/// Ob der Lausch-Thread laufen soll. Ein Flag statt Thread-Handles: der
/// Thread gehört sich selbst, Stoppen ist nur ein Wunsch, den er binnen
/// eines Channel-Timeouts erfüllt.
static LAUSCHT: AtomicBool = AtomicBool::new(false);

#[derive(Serialize, Clone, Copy)]
pub struct WakewordStand {
    pub aufnahmen: u8,
    pub trainiert: bool,
    pub lauscht: bool,
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
    Ok(WakewordStand {
        aufnahmen: aufnahme_dateien(&verzeichnis).len().min(u8::MAX as usize) as u8,
        trainiert: verzeichnis.join(MODELL_DATEI).exists(),
        lauscht: LAUSCHT.load(Ordering::SeqCst),
    })
}

/// Löscht Aufnahmen und Modell — für „Wake-Word neu einrichten“ und für den
/// Uninstaller (temporäre Audiodaten restlos entfernen).
pub fn zuruecksetzen(app: &AppHandle) -> Result<(), String> {
    lauschen_stoppen();
    let verzeichnis = wakeword_verzeichnis(app)?;
    std::fs::remove_dir_all(&verzeichnis).map_err(|e| e.to_string())?;
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

fn mikrofon() -> Result<(cpal::Device, cpal::SupportedStreamConfig), String> {
    let geraet = cpal::default_host()
        .default_input_device()
        .ok_or("Kein Mikrofon gefunden")?;
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

/// Nimmt eine Kalibrierungs-Aufnahme auf (blockiert ~2,2 s) und schreibt sie
/// als 16-bit-Mono-WAV. Nummer 1..=10; eine vorhandene Aufnahme derselben
/// Nummer wird ersetzt.
pub fn aufnehmen(app: &AppHandle, nummer: u8) -> Result<String, String> {
    if nummer == 0 || nummer > AUFNAHMEN_SOLL {
        return Err(format!("Aufnahmenummer 1..={AUFNAHMEN_SOLL} erwartet"));
    }
    if LAUSCHT.load(Ordering::SeqCst) {
        return Err("Erst das Lauschen stoppen, dann kalibrieren".into());
    }
    let (geraet, konfig) = mikrofon()?;
    let rate = konfig.sample_rate();
    let kanaele = konfig.channels() as usize;

    let (sender, empfaenger) = mpsc::channel::<Vec<f32>>();
    let stream = stream_starten(&geraet, &konfig, sender)?;

    let ziel_samples = (rate as f32 * AUFNAHME_SEKUNDEN) as usize;
    let mut mono: Vec<f32> = Vec::with_capacity(ziel_samples);
    while mono.len() < ziel_samples {
        match empfaenger.recv_timeout(Duration::from_secs(2)) {
            Ok(block) => nach_mono(&block, kanaele, &mut mono),
            Err(_) => break,
        }
    }
    drop(stream);
    mono.truncate(ziel_samples);
    if mono.len() < (rate / 2) as usize {
        return Err("Aufnahme zu kurz — liefert das Mikrofon Daten?".into());
    }

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
        .name("singra-wakeword".into())
        .spawn(move || {
            if let Err(fehler) = lausch_schleife(&app, &modell) {
                eprintln!("Wake-Word-Lauschen beendet: {fehler}");
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
    let (geraet, konfig) = mikrofon()?;
    let rate = konfig.sample_rate();
    let kanaele = konfig.channels() as usize;

    // rustpotter bekommt Mono-f32 im Geräte-Takt und rechnet intern um.
    let mut rp_konfig = RustpotterConfig::default();
    rp_konfig.fmt.sample_rate = rate as usize;
    rp_konfig.fmt.channels = 1;
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
