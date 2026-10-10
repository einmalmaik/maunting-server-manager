//! Bilder eines Tabs: das Standbild und der Screenshot.
//!
//! Legt die Oberfläche etwas über den Inhalt (Schild, Vorschläge, Dialog),
//! muss die native Webview weg, sonst läge sie darüber. Damit die Seite dabei
//! nicht verschwindet, zeigt die Oberfläche so lange ein Standbild an ihrer
//! Stelle (bis 10/2026 stand dort eine leere Fläche).
//!
//! Ein Screenshot ist dasselbe Bild als PNG (`tabs/aufnahme.rs`). Für die
//! ganze Seite zieht das DevTools-Protokoll die Ansicht kurz auf die Höhe der
//! Seite; `captureBeyondViewport` antwortet in WebView2 nie (10.10.2026).

use std::sync::mpsc;
use std::time::Duration;

use tauri::AppHandle;
use webview2_com::CapturePreviewCompletedHandler;
use webview2_com::Microsoft::Web::WebView2::Win32::{
    COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT, COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_JPEG, COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_PNG,
};
use windows::Win32::Foundation::HGLOBAL;
use windows::Win32::System::Com::StructuredStorage::CreateStreamOnHGlobal;
use windows::Win32::System::Com::{IStream, STREAM_SEEK_SET};

/// Länger wartet die Oberfläche nicht: dann geht der Tab ohne Bild weg.
const FRIST: Duration = Duration::from_millis(800);
/// Ein Screenshot darf dauern; der Nutzer wartet darauf.
const FRIST_SCREENSHOT: Duration = Duration::from_secs(10);
/// Chromium zeichnet höchstens so viele Pixel je Seite; eine längere Seite wird abgeschnitten.
const HOECHSTENS_PX: f64 = 16_384.0;

/// Das Standbild (JPEG).
pub fn aufnehmen(app: &AppHandle, id: &str) -> Result<Vec<u8>, String> {
    bild(app, id, COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_JPEG, FRIST)
}

/// Der sichtbare Teil als PNG.
pub fn sichtbar(app: &AppHandle, id: &str) -> Result<Vec<u8>, String> {
    bild(app, id, COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_PNG, FRIST_SCREENSHOT)
}

/// Die ganze Seite als PNG (Base64, wie das Protokoll sie liefert) und ob sie abgeschnitten ist.
pub fn ganz(app: &AppHandle, id: &str) -> Result<(String, bool), String> {
    let rufen = |methode: &str, parameter: serde_json::Value| -> Result<serde_json::Value, String> {
        serde_json::from_str(&super::entwickler::aufrufen(app, id, methode.into(), parameter.to_string())?).map_err(|e| e.to_string())
    };
    let metrik = rufen("Page.getLayoutMetrics", serde_json::json!({}))?;
    let zahl = |pfad: &str| metrik.pointer(pfad).and_then(serde_json::Value::as_f64);
    let (breite, hoehe) = zahl("/cssContentSize/width").zip(zahl("/cssContentSize/height")).ok_or("Die Seite nennt ihre Größe nicht")?;
    // `contentSize` zählt Gerätepixel, `cssContentSize` CSS-Pixel.
    let faktor = zahl("/contentSize/width").map_or(1.0, |w| (w / breite).max(1.0));
    let (breite, abgeschnitten) = (breite.ceil(), hoehe > HOECHSTENS_PX / faktor);
    let hoehe = hoehe.min(HOECHSTENS_PX / faktor).floor().max(1.0);
    // `deviceScaleFactor: 0` behält den Faktor des Bildschirms.
    rufen("Emulation.setDeviceMetricsOverride", serde_json::json!({ "width": breite, "height": hoehe, "deviceScaleFactor": 0, "mobile": false }))?;
    let bild = rufen("Page.captureScreenshot", serde_json::json!({ "format": "png", "clip": { "x": 0, "y": 0, "width": breite, "height": hoehe, "scale": 1 } }));
    // Immer zurück, auch wenn die Aufnahme scheiterte.
    let zurueck = rufen("Emulation.clearDeviceMetricsOverride", serde_json::json!({}));
    let png = bild?["data"].as_str().ok_or("Kein Bild von der Seite")?.to_string();
    zurueck?;
    Ok((png, abgeschnitten))
}

fn bild(app: &AppHandle, id: &str, format: COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT, frist: Duration) -> Result<Vec<u8>, String> {
    let rx = super::mit_tab(app, id, move |tab| {
        let (tx, rx) = mpsc::channel::<Result<Vec<u8>, String>>();
        unsafe {
            let strom = CreateStreamOnHGlobal(HGLOBAL::default(), true).map_err(|e| e.to_string())?;
            let lesen = strom.clone();
            tab.core
                .CapturePreview(
                    format,
                    &strom,
                    &CapturePreviewCompletedHandler::create(Box::new(move |ergebnis| {
                        let _ = tx.send(ergebnis.and_then(|()| auslesen(&lesen)).map_err(|e| e.to_string()));
                        Ok(())
                    })),
                )
                .map_err(|e| e.to_string())?;
        }
        Ok(rx)
    })?;
    rx.recv_timeout(frist).map_err(|_| "Standbild kam nicht rechtzeitig".to_string())?
}

unsafe fn auslesen(strom: &IStream) -> windows::core::Result<Vec<u8>> {
    strom.Seek(0, STREAM_SEEK_SET, None)?;
    let mut alles = Vec::new();
    let mut puffer = vec![0u8; 64 * 1024];
    loop {
        let mut gelesen = 0u32;
        strom.Read(puffer.as_mut_ptr().cast(), puffer.len() as u32, Some(&mut gelesen)).ok()?;
        if gelesen == 0 {
            return Ok(alles);
        }
        alles.extend_from_slice(&puffer[..gelesen as usize]);
    }
}
