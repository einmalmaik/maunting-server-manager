//! Das Cookie-Skript (`crate::cookies`) je Tab. Es trägt den Stand des
//! Schilds; ändert der sich, kommt das alte Skript weg und das neue dazu.
//!
//! WebView2 nennt die Kennung zum Entfernen erst im Rückruf. Kommt der nach
//! einem Erneuern an, gehört er zu einem veralteten Skript und entfernt es
//! gleich wieder, sonst liefen zwei Fassungen in jeder Seite.

use std::cell::{Cell, RefCell};
use std::collections::HashMap;

use tauri::AppHandle;
use webview2_com::AddScriptToExecuteOnDocumentCreatedCompletedHandler;
use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2;
use windows::core::HSTRING;

thread_local! {
    static KENNUNGEN: RefCell<HashMap<String, String>> = RefCell::new(HashMap::new());
    static FASSUNG: Cell<u64> = const { Cell::new(0) };
}

/// Meldet das Skript am Tab an. UI-Faden.
pub(super) unsafe fn anmelden(id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let (id, fassung, core_spaeter) = (id.to_string(), FASSUNG.get(), core.clone());
    core.AddScriptToExecuteOnDocumentCreated(
        &HSTRING::from(crate::cookies::skript()),
        &AddScriptToExecuteOnDocumentCreatedCompletedHandler::create(Box::new(move |fehler, kennung| {
            fehler?;
            if fassung == FASSUNG.get() {
                KENNUNGEN.with(|k| k.borrow_mut().insert(id, kennung));
            } else {
                core_spaeter.RemoveScriptToExecuteOnDocumentCreated(&HSTRING::from(kennung))?;
            }
            Ok(())
        })),
    )
}

/// Ersetzt das Skript in allen Tabs; gilt ab dem nächsten Dokument.
pub fn erneuern(app: &AppHandle) {
    let _ = super::auf_ui(app, || {
        FASSUNG.set(FASSUNG.get() + 1);
        let alt = KENNUNGEN.with(|k| std::mem::take(&mut *k.borrow_mut()));
        super::NATIV.with(|n| {
            for (id, tab) in n.borrow().iter() {
                unsafe {
                    if let Some(kennung) = alt.get(id) {
                        let _ = tab.core.RemoveScriptToExecuteOnDocumentCreated(&HSTRING::from(kennung.as_str()));
                    }
                    let _ = anmelden(id, &tab.core);
                }
            }
        });
        Ok(())
    });
}

/// Ein geschlossener Tab braucht keine Kennung mehr. UI-Faden.
pub(super) fn vergessen(id: &str) {
    KENNUNGEN.with(|k| k.borrow_mut().remove(id));
}
