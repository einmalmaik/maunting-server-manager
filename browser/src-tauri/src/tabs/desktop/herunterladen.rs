//! Downloads eines Tabs: die Datei lädt in die Quarantäne, auf Wunsch kommt
//! vorher der Speicherdialog, nach dem Laden prüft der Virenschutz, und erst
//! dann liegt sie am Ziel (`crate::downloads`). Edge zeigt keine eigene Leiste.
//!
//! Der Speicherdialog läuft außerhalb des UI-Fadens. Bis er zurückkommt, hält
//! ein Deferral den Download an; Ereignisargumente und Deferral warten in
//! `OFFEN`, weil WebView2-Objekte den UI-Faden nicht verlassen dürfen.

use std::cell::RefCell;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{DownloadStartingEventHandler, StateChangedEventHandler};
use windows::core::{Interface, HSTRING};

use super::webview2::text;
use crate::downloads::{self, pruefung, quarantaene};
use crate::tabs::{melden, DownloadStand, TabEreignis};

#[derive(Clone)]
struct Lauf {
    tab: String,
    nr: u64,
    url: String,
    name: String,
    /// Im Speicherdialog gewählt; sonst der Download-Ordner.
    gewaehlt: Option<PathBuf>,
}

impl Lauf {
    fn melden(&self, app: &AppHandle, stand: DownloadStand, datei: Option<String>) {
        melden(app, TabEreignis::Download { id: self.tab.clone(), nr: self.nr, stand, url: self.url.clone(), datei });
    }
}

struct Offen {
    args: ICoreWebView2DownloadStartingEventArgs,
    deferral: ICoreWebView2Deferral,
    lauf: Lauf,
}

thread_local! {
    static OFFEN: RefCell<HashMap<u64, Offen>> = RefCell::new(HashMap::new());
}

pub(super) unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let Ok(core4) = core.cast::<ICoreWebView2_4>() else { return Ok(()) };
    static NR: AtomicU64 = AtomicU64::new(1);
    let (app, id) = (app.clone(), id.to_string());
    let mut token = 0i64;
    core4.add_DownloadStarting(
        &DownloadStartingEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else { return Ok(()) };
            args.SetHandled(true)?;
            let vorgang = args.DownloadOperation()?;
            let url = text(|p| vorgang.Uri(p));
            let vorschlag = PathBuf::from(text(|p| args.ResultFilePath(p)));
            let lauf = Lauf {
                tab: id.clone(),
                nr: NR.fetch_add(1, Ordering::Relaxed),
                name: downloads::name_fuer(&url, &vorschlag),
                url,
                gewaehlt: None,
            };
            if !downloads::fragen(&app) {
                return starten(&app, &args, lauf);
            }
            let deferral = args.GetDeferral()?;
            let (nr, name) = (lauf.nr, lauf.name.clone());
            OFFEN.with(|o| o.borrow_mut().insert(nr, Offen { args, deferral, lauf }));
            fragen(&app, nr, name);
            Ok(())
        })),
        &mut token,
    )
}

fn fragen(app: &AppHandle, nr: u64, name: String) {
    let mut dialog = app.dialog().file().set_file_name(name).set_directory(downloads::ordner(app));
    if let Some(fenster) = app.get_webview_window("main") {
        dialog = dialog.set_parent(&fenster);
    }
    let app = app.clone();
    dialog.save_file(move |wahl| {
        let ziel = wahl.and_then(|p| p.into_path().ok());
        let app2 = app.clone();
        let _ = app.run_on_main_thread(move || entschieden(&app2, nr, ziel));
    });
}

/// UI-Faden: der Speicherdialog ist zu. Ohne Ziel fällt der Download weg.
fn entschieden(app: &AppHandle, nr: u64, ziel: Option<PathBuf>) {
    let Some(offen) = OFFEN.with(|o| o.borrow_mut().remove(&nr)) else { return };
    unsafe {
        let _ = match ziel {
            Some(ziel) => starten(app, &offen.args, Lauf { gewaehlt: Some(ziel), ..offen.lauf }),
            None => offen.args.SetCancel(true),
        };
        let _ = offen.deferral.Complete();
    }
}

fn blockiert(grund: COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON) -> bool {
    grund == COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_FILE_MALICIOUS
        || grund == COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_FILE_BLOCKED_BY_POLICY
        || grund == COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_FILE_SECURITY_CHECK_FAILED
}

unsafe fn starten(app: &AppHandle, args: &ICoreWebView2DownloadStartingEventArgs, lauf: Lauf) -> windows::core::Result<()> {
    let Some(platz) = quarantaene::neuer_platz(app, &lauf.name) else {
        lauf.melden(app, DownloadStand::Fehler, Some(lauf.name.clone()));
        return args.SetCancel(true);
    };
    args.SetResultFilePath(&HSTRING::from(platz.as_os_str()))?;
    lauf.melden(app, DownloadStand::Start, Some(lauf.name.clone()));
    let app = app.clone();
    let mut token = 0i64;
    args.DownloadOperation()?.add_StateChanged(
        &StateChangedEventHandler::create(Box::new(move |sender, _| {
            let Some(vorgang) = sender else { return Ok(()) };
            let mut stand = COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
            vorgang.State(&mut stand)?;
            if stand == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED {
                lauf.melden(&app, DownloadStand::Pruefung, Some(lauf.name.clone()));
                let (app, lauf, platz) = (app.clone(), lauf.clone(), platz.clone());
                std::thread::spawn(move || nach_dem_laden(&app, lauf, platz));
            } else if stand == COREWEBVIEW2_DOWNLOAD_STATE_INTERRUPTED {
                let mut grund = COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE;
                vorgang.InterruptReason(&mut grund)?;
                quarantaene::wegraeumen(&platz);
                let stand = if blockiert(grund) { DownloadStand::Blockiert } else { DownloadStand::Fehler };
                lauf.melden(&app, stand, Some(lauf.name.clone()));
            }
            Ok(())
        })),
        &mut token,
    )
}

/// Eigener Faden: prüfen, dann ablegen. Was der Virenschutz ablehnt, wird gelöscht.
fn nach_dem_laden(app: &AppHandle, lauf: Lauf, platz: PathBuf) {
    if !pruefung::pruefen(&platz, &lauf.url) {
        quarantaene::wegraeumen(&platz);
        lauf.melden(app, DownloadStand::Blockiert, Some(lauf.name.clone()));
        return;
    }
    let ergebnis = downloads::ablegen(app, &platz, &lauf.name, lauf.gewaehlt.clone());
    quarantaene::wegraeumen(&platz);
    match ergebnis {
        Ok(ziel) => lauf.melden(app, DownloadStand::Fertig, Some(ziel.to_string_lossy().into_owned())),
        Err(fehler) => {
            eprintln!("[MSB] Download nicht abgelegt: {fehler}");
            lauf.melden(app, DownloadStand::Fehler, Some(lauf.name.clone()));
        }
    }
}
