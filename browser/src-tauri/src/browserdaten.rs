//! Browserdaten der Seiten löschen: Cookies, Cache, Website-Speicher,
//! Berechtigungen. Getroffen wird nur das Seitenprofil (`tabs::seitenprofil`),
//! nie die Oberfläche mit Tresor und Messenger.
//!
//! Gelöscht wird über die WebView2 selbst (`ClearBrowsingDataAll`): solange
//! ihr Browserprozess läuft, ist der Ordner gesperrt. Gibt es gerade keinen
//! Tab, nimmt der Befehl einen unsichtbaren Helfer im Profil (`tabs::im_profil`).
//!
//! Mit „Beim Schließen vergessen“ läuft dasselbe beim Schließen des Fensters,
//! und beim Start fällt der Ordner, falls das Schließen nicht mehr dazu kam.
//!
//! Was eine Seite einmal erlaubt oder verboten bekam (Kamera, Standort, …),
//! merkt sich das Profil. `seitenrechte` listet es, `seitenrecht_zuruecksetzen`
//! lässt die Seite wieder fragen.

#[cfg(windows)]
use std::time::Duration;

use serde::Serialize;
use tauri::AppHandle;

#[cfg(windows)]
const FRIST: Duration = Duration::from_secs(15);

/// `seit`: nur Daten ab diesem Zeitpunkt (Millisekunden seit 1970), sonst alles.
#[tauri::command(async)]
pub fn seitendaten_loeschen(app: AppHandle, seit: Option<f64>) -> Result<(), String> {
    match seit {
        Some(ms) if ms.is_finite() && ms > 0.0 => plattform::leeren_seit(&app, ms / 1000.0),
        _ => loeschen(&app),
    }
}

pub fn loeschen(app: &AppHandle) -> Result<(), String> {
    plattform::leeren(app)
}

/// Beim Start, bevor eine Webview das Profil öffnet.
pub fn beim_start(app: &AppHandle, vergessen: bool) {
    if !vergessen {
        return;
    }
    // Die Profile der WebViews liegen dort, wo Android sie anlegt; das Plugin
    // ist schon geladen (Plugins vor `setup`).
    #[cfg(target_os = "android")]
    if let Err(fehler) = plattform::leeren(app) {
        eprintln!("[MSB] Seitendaten beim Start nicht gelöscht: {fehler}");
    }
    #[cfg(windows)]
    if let Ok(ordner) = crate::tabs::seitenprofil(app) {
        if let Err(fehler) = std::fs::remove_dir_all(&ordner) {
            if fehler.kind() != std::io::ErrorKind::NotFound {
                eprintln!("[MSB] Seitendaten beim Start nicht gelöscht: {fehler}");
            }
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Seitenrecht {
    /// Name wie in `rueckfragen.rs` (`kamera`, `standort`, …).
    pub art: String,
    pub herkunft: String,
    pub erlaubt: bool,
}

#[tauri::command(async)]
pub fn seitenrechte(app: AppHandle) -> Result<Vec<Seitenrecht>, String> {
    plattform::rechte(&app)
}

#[tauri::command(async)]
pub fn seitenrecht_zuruecksetzen(app: AppHandle, art: String, herkunft: String) -> Result<(), String> {
    if herkunft.is_empty() || herkunft.len() > 2048 {
        return Err("Ungültige Herkunft".into());
    }
    plattform::zuruecksetzen(&app, &art, &herkunft)
}

#[cfg(windows)]
mod plattform {
    use std::sync::mpsc;
    use std::time::{SystemTime, UNIX_EPOCH};

    use tauri::AppHandle;
    use webview2_com::Microsoft::Web::WebView2::Win32::*;
    use webview2_com::{
        take_pwstr, ClearBrowsingDataCompletedHandler, GetNonDefaultPermissionSettingsCompletedHandler,
        SetPermissionStateCompletedHandler,
    };
    use windows::core::{Interface, HSTRING, PWSTR};

    use super::{Seitenrecht, FRIST};

    unsafe fn profil<T: Interface>(core: &ICoreWebView2) -> windows::core::Result<T> {
        core.cast::<ICoreWebView2_13>()?.Profile()?.cast::<T>()
    }

    fn warten<T>(rx: mpsc::Receiver<Result<T, String>>) -> Result<T, String> {
        rx.recv_timeout(FRIST).map_err(|_| "Das Profil hat nicht geantwortet".to_string())?
    }

    pub fn leeren(app: &AppHandle) -> Result<(), String> {
        let (tx, rx) = mpsc::channel::<Result<(), String>>();
        crate::tabs::im_profil(app, move |core| unsafe {
            profil::<ICoreWebView2Profile2>(core)
                .and_then(|p| {
                    p.ClearBrowsingDataAll(&ClearBrowsingDataCompletedHandler::create(Box::new(move |ergebnis| {
                        let _ = tx.send(ergebnis.map_err(|e| e.to_string()));
                        Ok(())
                    })))
                })
                .map_err(|e| e.to_string())
        })?;
        warten(rx)
    }

    pub fn leeren_seit(app: &AppHandle, seit_sekunden: f64) -> Result<(), String> {
        let bis = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|e| e.to_string())?.as_secs_f64();
        let (tx, rx) = mpsc::channel::<Result<(), String>>();
        crate::tabs::im_profil(app, move |core| unsafe {
            profil::<ICoreWebView2Profile2>(core)
                .and_then(|p| {
                    p.ClearBrowsingDataInTimeRange(
                        COREWEBVIEW2_BROWSING_DATA_KINDS_ALL_PROFILE,
                        seit_sekunden,
                        bis,
                        &ClearBrowsingDataCompletedHandler::create(Box::new(move |ergebnis| {
                            let _ = tx.send(ergebnis.map_err(|e| e.to_string()));
                            Ok(())
                        })),
                    )
                })
                .map_err(|e| e.to_string())
        })?;
        warten(rx)
    }

    pub fn rechte(app: &AppHandle) -> Result<Vec<Seitenrecht>, String> {
        let (tx, rx) = mpsc::channel::<Result<Vec<Seitenrecht>, String>>();
        crate::tabs::im_profil(app, move |core| unsafe {
            profil::<ICoreWebView2Profile4>(core)
                .and_then(|p| {
                    p.GetNonDefaultPermissionSettings(&GetNonDefaultPermissionSettingsCompletedHandler::create(Box::new(
                        move |ergebnis, liste| {
                            let rechte = liste.map(|l| lesen(&l)).unwrap_or_default();
                            let _ = tx.send(ergebnis.map_err(|e| e.to_string()).map(|_| rechte));
                            Ok(())
                        },
                    )))
                })
                .map_err(|e| e.to_string())
        })?;
        warten(rx)
    }

    unsafe fn lesen(liste: &ICoreWebView2PermissionSettingCollectionView) -> Vec<Seitenrecht> {
        let mut anzahl = 0u32;
        if liste.Count(&mut anzahl).is_err() {
            return Vec::new();
        }
        let mut rechte = Vec::new();
        for i in 0..anzahl {
            let Ok(eintrag) = liste.GetValueAtIndex(i) else { continue };
            let mut art = COREWEBVIEW2_PERMISSION_KIND::default();
            let mut stand = COREWEBVIEW2_PERMISSION_STATE::default();
            let mut herkunft = PWSTR::null();
            if eintrag.PermissionKind(&mut art).is_err()
                || eintrag.PermissionState(&mut stand).is_err()
                || eintrag.PermissionOrigin(&mut herkunft).is_err()
            {
                continue;
            }
            let herkunft = take_pwstr(herkunft);
            // Nur, wozu die Oberfläche einen Namen hat; den Rest kann sie nicht zurücksetzen.
            let name = crate::tabs::recht_name(art);
            if name == "sonstiges" || stand == COREWEBVIEW2_PERMISSION_STATE_DEFAULT {
                continue;
            }
            rechte.push(Seitenrecht { art: name.into(), herkunft, erlaubt: stand == COREWEBVIEW2_PERMISSION_STATE_ALLOW });
        }
        rechte
    }

    pub fn zuruecksetzen(app: &AppHandle, art: &str, herkunft: &str) -> Result<(), String> {
        let art = crate::tabs::recht_art(art).ok_or("Unbekanntes Recht")?;
        let herkunft = HSTRING::from(herkunft);
        let (tx, rx) = mpsc::channel::<Result<(), String>>();
        crate::tabs::im_profil(app, move |core| unsafe {
            profil::<ICoreWebView2Profile4>(core)
                .and_then(|p| {
                    p.SetPermissionState(
                        art,
                        &herkunft,
                        COREWEBVIEW2_PERMISSION_STATE_DEFAULT,
                        &SetPermissionStateCompletedHandler::create(Box::new(move |ergebnis| {
                            let _ = tx.send(ergebnis.map_err(|e| e.to_string()));
                            Ok(())
                        })),
                    )
                })
                .map_err(|e| e.to_string())
        })?;
        warten(rx)
    }
}

/// Android löscht über die Profile der WebViews (`TabsPlugin.datenLeeren`):
/// alles im Profil `seiten`, ohne Zeitraum. Rechte merkt sich dort niemand,
/// weil Seiten auf Android keine bekommen.
#[cfg(target_os = "android")]
mod plattform {
    use tauri::AppHandle;

    use super::Seitenrecht;

    pub fn leeren(app: &AppHandle) -> Result<(), String> {
        crate::tabs::android::daten_leeren(app)
    }

    pub fn leeren_seit(_app: &AppHandle, _seit: f64) -> Result<(), String> {
        Err("Android löscht Browserdaten nur ganz".into())
    }

    pub fn rechte(_app: &AppHandle) -> Result<Vec<Seitenrecht>, String> {
        Ok(Vec::new())
    }

    pub fn zuruecksetzen(_app: &AppHandle, _art: &str, _herkunft: &str) -> Result<(), String> {
        Ok(())
    }
}
