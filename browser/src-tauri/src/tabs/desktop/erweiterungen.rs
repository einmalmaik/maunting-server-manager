//! Erweiterungen im Profil der Seiten (`ICoreWebView2Profile7`). Was laufen
//! soll, steht in `crate::erweiterungen::liste`; hier wird WebView2 dem
//! angeglichen. Private Tabs haben ein eigenes Profil ohne Erweiterungen.
//! Alles auf dem UI-Faden.

use std::path::Path;
use std::sync::mpsc;

use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    BrowserExtensionEnableCompletedHandler, BrowserExtensionRemoveCompletedHandler, ProfileAddBrowserExtensionCompletedHandler,
    ProfileGetBrowserExtensionsCompletedHandler,
};
use windows::core::{Interface, BOOL, HSTRING};

use super::{fehler, kein_zeiger};
use crate::erweiterungen::liste::Soll;

/// Bringt WebView2 selbst mit (Zwischenablage, PDF-Ansicht); nie anfassen.
const EINGEBAUT: [&str; 2] = ["dgiklkfkllikcanfonkcabmbdfmgleag", "mhjfbmdgcfjbbpaeojofohoefgiehjai"];

unsafe fn profil(core: &ICoreWebView2) -> Result<ICoreWebView2Profile7, String> {
    core.cast::<ICoreWebView2_13>()
        .and_then(|c| c.Profile())
        .and_then(|p| p.cast::<ICoreWebView2Profile7>())
        .map_err(fehler)
}

fn warten<T>(rx: mpsc::Receiver<windows::core::Result<T>>) -> Result<T, String> {
    webview2_com::wait_with_pump(rx).map_err(fehler)?.map_err(fehler)
}

unsafe fn id_von(x: &ICoreWebView2BrowserExtension) -> String {
    super::webview2::text(|p| x.Id(p))
}

unsafe fn alle(p: &ICoreWebView2Profile7) -> Result<Vec<ICoreWebView2BrowserExtension>, String> {
    let (tx, rx) = mpsc::channel();
    p.GetBrowserExtensions(&ProfileGetBrowserExtensionsCompletedHandler::create(Box::new(move |e, liste| {
        let _ = tx.send(e.and_then(|()| liste.ok_or_else(kein_zeiger)));
        Ok(())
    })))
    .map_err(fehler)?;
    let liste = warten(rx)?;
    let mut n = 0u32;
    liste.Count(&mut n).map_err(fehler)?;
    (0..n).map(|i| liste.GetValueAtIndex(i).map_err(fehler)).collect()
}

unsafe fn hinzu(p: &ICoreWebView2Profile7, ordner: &Path) -> Result<ICoreWebView2BrowserExtension, String> {
    let (tx, rx) = mpsc::channel();
    p.AddBrowserExtension(
        &HSTRING::from(ordner.as_os_str()),
        &ProfileAddBrowserExtensionCompletedHandler::create(Box::new(move |e, x| {
            let _ = tx.send(e.and_then(|()| x.ok_or_else(kein_zeiger)));
            Ok(())
        })),
    )
    .map_err(fehler)?;
    warten(rx)
}

unsafe fn schalten(x: &ICoreWebView2BrowserExtension, an: bool) -> Result<(), String> {
    let mut ist = BOOL::default();
    x.IsEnabled(&mut ist).map_err(fehler)?;
    if ist.as_bool() == an {
        return Ok(());
    }
    let (tx, rx) = mpsc::channel();
    x.Enable(
        an,
        &BrowserExtensionEnableCompletedHandler::create(Box::new(move |e| {
            let _ = tx.send(e);
            Ok(())
        })),
    )
    .map_err(fehler)?;
    warten(rx)
}

unsafe fn weg(x: &ICoreWebView2BrowserExtension) -> Result<(), String> {
    let (tx, rx) = mpsc::channel();
    x.Remove(&BrowserExtensionRemoveCompletedHandler::create(Box::new(move |e| {
        let _ = tx.send(e);
        Ok(())
    })))
    .map_err(fehler)?;
    warten(rx)
}

/// Gleicht das Profil der Liste an und gibt die Kennungen zurück, die jetzt
/// laufen. Fehlt eine Erweiterung im Profil (Seitendaten gelöscht), kommt sie
/// aus ihrem Ordner wieder; was die Liste nicht kennt, fällt heraus.
pub(super) unsafe fn abgleichen(core: &ICoreWebView2, soll: &[Soll]) -> Result<Vec<String>, String> {
    let p = profil(core)?;
    let mut ist = alle(&p)?;
    for s in soll {
        if ist.iter().any(|x| id_von(x) == s.id) {
            continue;
        }
        match hinzu(&p, &s.ordner) {
            // Die Kennung hängt am `key` im Manifest. Passt sie nicht, ist der
            // Ordner nicht mehr der, den wir geprüft haben.
            Ok(x) if id_von(&x) != s.id => {
                eprintln!("[MSB] Erweiterung {} kam mit anderer Kennung zurück", s.id);
                weg(&x)?;
            }
            Ok(x) => ist.push(x),
            Err(e) => eprintln!("[MSB] Erweiterung {} nicht geladen: {e}", s.id),
        }
    }
    let mut laufen = Vec::new();
    for x in &ist {
        let id = id_von(x);
        match soll.iter().find(|s| s.id == id) {
            Some(s) => {
                schalten(x, s.an)?;
                if s.an {
                    laufen.push(id);
                }
            }
            None if !EINGEBAUT.contains(&id.as_str()) => weg(x)?,
            None => {}
        }
    }
    Ok(laufen)
}

/// Lädt einen entpackten Ordner und gibt die Kennung zurück, die WebView2
/// ihm gibt (ohne `key` aus dem Pfad). Sie startet aus; an schaltet der
/// Abgleich, wenn sie in der Liste steht.
pub(super) unsafe fn laden(core: &ICoreWebView2, ordner: &Path) -> Result<String, String> {
    let p = profil(core)?;
    let x = hinzu(&p, ordner)?;
    schalten(&x, false)?;
    Ok(id_von(&x))
}
