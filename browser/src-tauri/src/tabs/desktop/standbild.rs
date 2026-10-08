//! Ein Standbild des vorderen Tabs.
//!
//! Legt die Oberfläche etwas über den Inhalt (Schild, Vorschläge, Dialog),
//! muss die native Webview weg, sonst läge sie darüber. Damit die Seite dabei
//! nicht verschwindet, zeigt die Oberfläche so lange dieses Bild an ihrer
//! Stelle (bis 10/2026 stand dort eine leere Fläche).

use std::sync::mpsc;
use std::time::Duration;

use tauri::AppHandle;
use webview2_com::CapturePreviewCompletedHandler;
use webview2_com::Microsoft::Web::WebView2::Win32::COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_JPEG;
use windows::Win32::Foundation::HGLOBAL;
use windows::Win32::System::Com::StructuredStorage::CreateStreamOnHGlobal;
use windows::Win32::System::Com::{IStream, STREAM_SEEK_SET};

/// Länger wartet die Oberfläche nicht: dann geht der Tab ohne Bild weg.
const FRIST: Duration = Duration::from_millis(800);

pub fn aufnehmen(app: &AppHandle, id: &str) -> Result<Vec<u8>, String> {
    let rx = super::mit_tab(app, id, |tab| {
        let (tx, rx) = mpsc::channel::<Result<Vec<u8>, String>>();
        unsafe {
            let strom = CreateStreamOnHGlobal(HGLOBAL::default(), true).map_err(|e| e.to_string())?;
            let lesen = strom.clone();
            tab.core
                .CapturePreview(
                    COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_JPEG,
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
    rx.recv_timeout(FRIST).map_err(|_| "Standbild kam nicht rechtzeitig".to_string())?
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
