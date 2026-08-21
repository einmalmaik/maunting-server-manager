//! Audio-Ducking über WASAPI: Hintergrundton sanft absenken, solange die KI
//! spricht oder zuhört — und danach genauso sanft wiederherstellen.
//!
//! Arbeitsweise: alle Audio-Sessions des Standard-Wiedergabegeräts werden
//! enumeriert (IAudioSessionManager2), die eigene Session ausgenommen, und
//! jede fremde Session über ISimpleAudioVolume auf `DUCK_FAKTOR` ihrer
//! aktuellen Lautstärke gerampt. Die Originalwerte merkt sich ein Mutex,
//! Schlüssel ist die Session-Instanz-Kennung — beim Stoppen wird neu
//! enumeriert und nur wiederhergestellt, was noch existiert. COM-Objekte
//! werden nie über den Aufruf hinaus gehalten (Apartment-Regeln).
//!
//! Kein Polling, kein Thread: beide Funktionen laufen nur, wenn sie gerufen
//! werden, und blockieren für die Dauer der Rampe (~200 ms).

use std::collections::HashMap;
use std::sync::Mutex;

use windows::core::Interface;
use windows::Win32::Media::Audio::{
    eMultimedia, eRender, IAudioSessionControl2, IAudioSessionManager2, IMMDeviceEnumerator,
    ISimpleAudioVolume, MMDeviceEnumerator,
};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize, CLSCTX_ALL,
    COINIT_MULTITHREADED,
};

/// Zielanteil der Originallautstärke: Absenkung um 60 %.
const DUCK_FAKTOR: f32 = 0.4;
/// Rampe: Schritte und Pause dazwischen — weich statt Sprung.
const RAMPEN_SCHRITTE: u32 = 8;
const RAMPEN_PAUSE_MS: u64 = 25;

/// Originallautstärken der geduckten Sessions (Instanz-Kennung → Wert).
/// `None` heißt: gerade nichts geduckt.
static ORIGINALE: Mutex<Option<HashMap<String, f32>>> = Mutex::new(None);

/// COM-Initialisierung für die Dauer eines Aufrufs. `S_FALSE` (schon
/// initialisiert) zählt als Erfolg; nur was wir initialisiert haben,
/// geben wir auch wieder frei.
struct ComGast {
    freigeben: bool,
}

impl ComGast {
    fn betreten() -> Result<Self, String> {
        let hr = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
        if hr.is_ok() {
            return Ok(Self { freigeben: true });
        }
        // RPC_E_CHANGED_MODE: der Thread ist bereits im anderen Apartment
        // initialisiert — nutzbar, aber nicht von uns zu beenden.
        if hr.0 == 0x8001_0106_u32 as i32 {
            return Ok(Self { freigeben: false });
        }
        Err(format!("COM-Initialisierung fehlgeschlagen: {hr}"))
    }
}

impl Drop for ComGast {
    fn drop(&mut self) {
        if self.freigeben {
            unsafe { CoUninitialize() };
        }
    }
}

/// Eine fremde Audio-Session: Lautstärkeregler, stabile Kennung, Ist-Wert.
struct FremdeSession {
    kennung: String,
    regler: ISimpleAudioVolume,
    lautstaerke: f32,
}

/// Ein geplanter Lautstärkeverlauf für eine Session.
struct Rampe {
    regler: ISimpleAudioVolume,
    von: f32,
    nach: f32,
}

/// Enumeriert alle fremden Sessions des Standard-Wiedergabegeräts.
/// Fehler einzelner Sessions überspringen statt alles abzubrechen —
/// eine halb geduckte Musik ist besser als gar kein Ducking.
fn fremde_sessions() -> Result<Vec<FremdeSession>, String> {
    let eigene_pid = std::process::id();
    let mut ergebnis = Vec::new();
    unsafe {
        let geraete: IMMDeviceEnumerator = CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)
            .map_err(|e| format!("Audiogeräte nicht erreichbar: {e}"))?;
        let standard = geraete
            .GetDefaultAudioEndpoint(eRender, eMultimedia)
            .map_err(|e| format!("Kein Standard-Wiedergabegerät: {e}"))?;
        let manager: IAudioSessionManager2 = standard
            .Activate(CLSCTX_ALL, None)
            .map_err(|e| format!("Session-Manager nicht aktivierbar: {e}"))?;
        let sessions = manager
            .GetSessionEnumerator()
            .map_err(|e| format!("Sessions nicht auflistbar: {e}"))?;
        let anzahl = sessions.GetCount().map_err(|e| e.to_string())?;

        for i in 0..anzahl {
            let Ok(session) = sessions.GetSession(i) else { continue };
            let Ok(details) = session.cast::<IAudioSessionControl2>() else { continue };
            let Ok(pid) = details.GetProcessId() else { continue };
            if pid == eigene_pid {
                continue;
            }
            let Ok(regler) = session.cast::<ISimpleAudioVolume>() else { continue };
            let Ok(lautstaerke) = regler.GetMasterVolume() else { continue };
            let Ok(roh) = details.GetSessionInstanceIdentifier() else { continue };
            let kennung = roh.to_string().unwrap_or_default();
            CoTaskMemFree(Some(roh.as_ptr() as *const _));
            if kennung.is_empty() {
                continue;
            }
            ergebnis.push(FremdeSession { kennung, regler, lautstaerke });
        }
    }
    Ok(ergebnis)
}

/// Fährt alle Rampen gleichzeitig und weich von `von` nach `nach`.
fn rampen_fahren(rampen: &[Rampe]) {
    if rampen.is_empty() {
        return;
    }
    for schritt in 1..=RAMPEN_SCHRITTE {
        let anteil = schritt as f32 / RAMPEN_SCHRITTE as f32;
        for r in rampen {
            let wert = r.von + (r.nach - r.von) * anteil;
            unsafe {
                let _ = r.regler.SetMasterVolume(wert, std::ptr::null());
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(RAMPEN_PAUSE_MS));
    }
}

/// Senkt alle fremden Sessions auf `DUCK_FAKTOR` ab. Idempotent: wer schon
/// duckt, duckt nicht tiefer (sonst fräße jeder weitere Aufruf 60 % vom Rest).
pub fn starten() -> Result<(), String> {
    let mut originale = ORIGINALE.lock().map_err(|_| "Ducking-Zustand vergiftet")?;
    if originale.is_some() {
        return Ok(());
    }
    let _com = ComGast::betreten()?;
    let sessions = fremde_sessions()?;
    let mut gemerkt = HashMap::new();
    let mut plaene = Vec::new();
    for s in sessions {
        gemerkt.insert(s.kennung, s.lautstaerke);
        plaene.push(Rampe {
            regler: s.regler,
            von: s.lautstaerke,
            nach: s.lautstaerke * DUCK_FAKTOR,
        });
    }
    rampen_fahren(&plaene);
    *originale = Some(gemerkt);
    Ok(())
}

/// Stellt die gemerkten Lautstärken wieder her. Sessions, die inzwischen
/// verschwunden sind, werden still übersprungen; neue bleiben unberührt.
pub fn stoppen() -> Result<(), String> {
    let mut originale = ORIGINALE.lock().map_err(|_| "Ducking-Zustand vergiftet")?;
    let Some(gemerkt) = originale.take() else {
        return Ok(());
    };
    let _com = ComGast::betreten()?;
    let plaene: Vec<Rampe> = fremde_sessions()?
        .into_iter()
        .filter_map(|s| {
            let original = *gemerkt.get(&s.kennung)?;
            Some(Rampe {
                regler: s.regler,
                von: s.lautstaerke,
                nach: original,
            })
        })
        .collect();
    rampen_fahren(&plaene);
    Ok(())
}
