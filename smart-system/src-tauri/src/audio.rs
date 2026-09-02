//! Audiogeräte: auflisten und ein bevorzugtes Eingabegerät finden.
//!
//! Die Auswahl in den Einstellungen soll **unabhängig** vom Windows-Standard
//! funktionieren: der Benutzer wählt ein Gerät mit Namen, und Wake-Word wie
//! Sprachsitzung nehmen genau dieses — egal, was Windows gerade als Standard
//! führt. Gespeichert wird nur der Name (konfig.json), nie eine Geräte-ID
//! und nie Audio.
//!
//! Die Namen sind dieselben, die der Browserteil über `enumerateDevices`
//! als Label sieht (beide kommen aus dem WASAPI-Friendly-Name) — deshalb
//! reicht eine Auswahlliste für beide Welten.

use cpal::traits::{DeviceTrait, HostTrait};
use serde::Serialize;

#[derive(Serialize)]
pub struct AudioGeraete {
    pub eingaenge: Vec<String>,
    pub ausgaenge: Vec<String>,
    /// Der aktuelle Windows-Standard — die UI zeigt ihn hinter „Windows-Standard“.
    pub standard_eingang: Option<String>,
    pub standard_ausgang: Option<String>,
}

fn name(geraet: &cpal::Device) -> Option<String> {
    geraet
        .description()
        .ok()
        .map(|beschreibung| beschreibung.name().to_string())
}

/// Listet alle Ein- und Ausgabegeräte mit Namen. Ein Gerät ohne lesbaren
/// Namen fällt weg — auswählen könnte man es ohnehin nicht.
pub fn geraete() -> AudioGeraete {
    let host = cpal::default_host();
    let eingaenge = host
        .input_devices()
        .map(|iter| iter.filter_map(|g| name(&g)).collect())
        .unwrap_or_default();
    let ausgaenge = host
        .output_devices()
        .map(|iter| iter.filter_map(|g| name(&g)).collect())
        .unwrap_or_default();
    AudioGeraete {
        eingaenge,
        ausgaenge,
        standard_eingang: host.default_input_device().as_ref().and_then(name),
        standard_ausgang: host.default_output_device().as_ref().and_then(name),
    }
}

/// Das Eingabegerät zur Wahl des Benutzers — oder der Windows-Standard,
/// wenn keines gewählt oder das gewählte gerade nicht angesteckt ist.
/// Der stille Rückfall ist Absicht: ein abgezogenes USB-Mikrofon soll das
/// Wake-Word nicht dauerhaft lahmlegen.
pub fn eingang_finden(bevorzugt: Option<&str>) -> Option<cpal::Device> {
    let host = cpal::default_host();
    if let Some(gesucht) = bevorzugt {
        if let Ok(mut iter) = host.input_devices() {
            if let Some(geraet) = iter.find(|g| name(g).as_deref() == Some(gesucht)) {
                return Some(geraet);
            }
        }
    }
    host.default_input_device()
}
