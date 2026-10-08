//! Ton, Schlaf und Speicher der Tabs unter WebView2.
//!
//! - Ton: `IsDocumentPlayingAudio` und `IsMuted` (`ICoreWebView2_8`) gehen als
//!   `Ton` an die Oberfläche; der Lautsprecher am Tab schaltet stumm. Spielt ein
//!   Tab Ton, bekommt der Mixer von Windows unseren Namen (`crate::mixer`).
//! - Schlaf: `TrySuspend` (`ICoreWebView2_3`) nur für verborgene Tabs ohne
//!   Ton und ohne Ausnahme; beim Zeigen weckt [`zeigen`] sie wieder.
//! - Speicher: verborgene Tabs bekommen `MemoryUsageTargetLevel = LOW`
//!   (`ICoreWebView2_19`), der vordere `NORMAL`.
//!
//! Wer dran ist, entscheidet `tabs/ruhe.rs`; hier läuft alles auf dem UI-Faden.

use tauri::{AppHandle, Manager};
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{IsDocumentPlayingAudioChangedEventHandler, IsMutedChangedEventHandler, TrySuspendCompletedHandler};
use windows::core::{Interface, BOOL};

use super::{fehler, mit_tab, Nativ, NATIV};
use crate::tabs::ruhe::{ausgenommen, Ruhe};
use crate::tabs::{melden, TabEreignis, Tabs};

unsafe fn ton(core: &ICoreWebView2) -> Option<(bool, bool)> {
    let core8 = core.cast::<ICoreWebView2_8>().ok()?;
    let (mut spielt, mut stumm) = (BOOL::default(), BOOL::default());
    core8.IsDocumentPlayingAudio(&mut spielt).ok()?;
    core8.IsMuted(&mut stumm).ok()?;
    Some((spielt.as_bool(), stumm.as_bool()))
}

pub(super) unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let Ok(core8) = core.cast::<ICoreWebView2_8>() else { return Ok(()) };
    let mut token = 0i64;
    let melde = {
        let (app, id) = (app.clone(), id.to_string());
        move |sender: Option<ICoreWebView2>| {
            if let Some((spielt, stumm)) = sender.as_ref().and_then(|c| ton(c)) {
                if spielt {
                    crate::mixer::benennen(&app);
                }
                melden(&app, TabEreignis::Ton { id: id.clone(), spielt, stumm });
            }
            Ok(())
        }
    };
    let zweite = melde.clone();
    core8.add_IsDocumentPlayingAudioChanged(
        &IsDocumentPlayingAudioChangedEventHandler::create(Box::new(move |sender, _| melde(sender))),
        &mut token,
    )?;
    core8.add_IsMutedChanged(&IsMutedChangedEventHandler::create(Box::new(move |sender, _| zweite(sender))), &mut token)?;
    Ok(())
}

pub fn stumm(app: &AppHandle, id: &str, stumm: bool) -> Result<(), String> {
    mit_tab(app, id, move |tab| unsafe { tab.core.cast::<ICoreWebView2_8>().map_err(fehler)?.SetIsMuted(stumm).map_err(fehler) })
}

unsafe fn schlaeft(core: &ICoreWebView2) -> bool {
    let mut ja = BOOL::default();
    core.cast::<ICoreWebView2_3>().is_ok_and(|c| c.IsSuspended(&mut ja).is_ok() && ja.as_bool())
}

/// Beim Zeigen und Verbergen: der vordere Tab wacht auf und bekommt normalen
/// Speicher, alle anderen wenig. UI-Faden.
pub(super) unsafe fn zeigen(app: &AppHandle, id: &str, tab: &Nativ, vorne: bool) {
    if vorne && schlaeft(&tab.core) {
        if let Ok(c3) = tab.core.cast::<ICoreWebView2_3>() {
            let _ = c3.Resume();
        }
        melden(app, TabEreignis::Schlaf { id: id.to_string(), schlaeft: false });
    }
    if let Ok(c19) = tab.core.cast::<ICoreWebView2_19>() {
        let stufe = if vorne { COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_NORMAL } else { COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW };
        let _ = c19.SetMemoryUsageTargetLevel(stufe);
    }
}

/// Legt die fälligen Tabs schlafen oder verwirft sie. Was gerade Ton spielt
/// oder unter eine Ausnahme fällt, bleibt wach.
pub fn ruhen(app: &AppHandle, faellig: Vec<(String, Ruhe)>, ausnahmen: Vec<String>) {
    let app2 = app.clone();
    let _ = app.run_on_main_thread(move || {
        let mut verworfen = Vec::new();
        // Seit der Auswahl kann ein Tab nach vorne gekommen sein.
        let tabs = app2.state::<Tabs>();
        let (aktiv, vollbild) = {
            let z = tabs.0.lock().unwrap();
            (z.aktiv.clone(), z.vollbild.clone())
        };
        NATIV.with(|n| {
            for (id, ruhe) in &faellig {
                if aktiv.as_deref() == Some(id.as_str()) || vollbild.as_deref() == Some(id.as_str()) {
                    continue;
                }
                let n = n.borrow();
                let Some(tab) = n.get(id) else { continue };
                unsafe {
                    if ton(&tab.core).is_some_and(|(spielt, _)| spielt) || ausgenommen(&super::webview2::quelle(&tab.core), &ausnahmen) {
                        continue;
                    }
                    match ruhe {
                        Ruhe::Verwerfen => verworfen.push(id.clone()),
                        Ruhe::Schlafen if !schlaeft(&tab.core) => einschlafen(&app2, id, &tab.core),
                        Ruhe::Schlafen => {}
                    }
                }
            }
        });
        for id in verworfen {
            verwerfen(&app2, &id);
        }
    });
}

unsafe fn einschlafen(app: &AppHandle, id: &str, core: &ICoreWebView2) {
    let Ok(c3) = core.cast::<ICoreWebView2_3>() else { return };
    let (app, id) = (app.clone(), id.to_string());
    let _ = c3.TrySuspend(&TrySuspendCompletedHandler::create(Box::new(move |ergebnis, geschafft| {
        if ergebnis.is_ok() && geschafft {
            melden(&app, TabEreignis::Schlaf { id, schlaeft: true });
        }
        Ok(())
    })));
}

/// Wie Schließen, nur bleibt der Tab in der Oberfläche. UI-Faden.
fn verwerfen(app: &AppHandle, id: &str) {
    app.state::<Tabs>().0.lock().unwrap().tabs.remove(id);
    crate::schild::tab_vergessen(app, id);
    super::rueckfragen::vergessen(id);
    if let Some(tab) = NATIV.with(|n| n.borrow_mut().remove(id)) {
        unsafe {
            let _ = tab.controller.Close();
        }
    }
    melden(app, TabEreignis::Verworfen { id: id.to_string() });
}
