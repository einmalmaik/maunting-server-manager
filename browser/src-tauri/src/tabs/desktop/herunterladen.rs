//! Downloads eines Tabs: die Datei lädt in die Quarantäne, auf Wunsch kommt
//! vorher der Speicherdialog, nach dem Laden prüft der Virenschutz, und erst
//! dann liegt sie am Ziel (`crate::downloads`). Edge zeigt keine eigene Leiste.
//!
//! Wie unter Android (`Herunterladen.kt`) laufen je Tab höchstens [`JE_TAB`]
//! Downloads zugleich, und auf dem Laufwerk der Quarantäne bleibt [`RESERVE`]
//! frei; bis 10.10.2026 füllte ein endloser Download die Platte.
//!
//! Der Speicherdialog läuft außerhalb des UI-Fadens. Bis er zurückkommt, hält
//! ein Deferral den Download an; Ereignisargumente und Deferral warten in
//! `OFFEN`, weil WebView2-Objekte den UI-Faden nicht verlassen dürfen.

use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::path::PathBuf;

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{BytesReceivedChangedEventHandler, DownloadStartingEventHandler, StateChangedEventHandler};
use windows::core::{Interface, HSTRING};

use super::webview2::text;
use crate::downloads::{self, pruefung, quarantaene};
use crate::tabs::{melden, DownloadStand, TabEreignis};

/// Höchstens so viele Downloads je Tab zugleich, wartende Speicherdialoge mitgezählt.
const JE_TAB: usize = 3;
/// So viel bleibt auf dem Laufwerk der Quarantäne mindestens frei.
const RESERVE: u64 = 512 << 20;
/// Der freie Platz wird alle so viele Bytes neu gemessen, nicht bei jedem Ereignis.
const MESSEN_ALLE: i64 = 8 << 20;

/// Bleibt nach `noch` weiteren Bytes die Reserve frei?
fn passt(frei: u64, noch: u64) -> bool {
    frei.saturating_sub(noch) >= RESERVE
}

#[derive(Clone)]
struct Lauf {
    tab: String,
    /// Aus einem privaten Tab: die Datei nennt ihre Adresse nicht (`pruefung`).
    privat: bool,
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
    /// Laufende Downloads je Tab.
    static LAUFEND: RefCell<HashMap<String, usize>> = RefCell::new(HashMap::new());
}

fn zu_viele(tab: &str) -> bool {
    let laufend = LAUFEND.with(|l| l.borrow().get(tab).copied().unwrap_or(0));
    let wartend = OFFEN.with(|o| o.borrow().values().filter(|x| x.lauf.tab == tab).count());
    laufend + wartend >= JE_TAB
}

fn zaehlen(tab: &str, plus: bool) {
    LAUFEND.with(|l| {
        let mut l = l.borrow_mut();
        let n = l.entry(tab.to_string()).or_insert(0);
        *n = if plus { *n + 1 } else { n.saturating_sub(1) };
        if *n == 0 {
            l.remove(tab);
        }
    });
}

pub(super) unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2, privat: bool) -> windows::core::Result<()> {
    let Ok(core4) = core.cast::<ICoreWebView2_4>() else { return Ok(()) };
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
                privat,
                nr: downloads::naechste_nr(),
                name: downloads::name_fuer(&url, &vorschlag),
                url,
                gewaehlt: None,
            };
            if zu_viele(&lauf.tab) {
                lauf.melden(&app, DownloadStand::Fehler, Some(lauf.name.clone()));
                return args.SetCancel(true);
            }
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
    let vorgang = args.DownloadOperation()?;
    let mut gesamt = 0i64;
    vorgang.TotalBytesToReceive(&mut gesamt)?;
    // Die angekündigte Größe zuerst; ohne Angabe misst erst das Laden.
    if !quarantaene::frei(&platz).is_some_and(|f| passt(f, gesamt.max(0) as u64)) {
        quarantaene::wegraeumen(&platz);
        lauf.melden(app, DownloadStand::Speicher, Some(lauf.name.clone()));
        return args.SetCancel(true);
    }
    args.SetResultFilePath(&HSTRING::from(platz.as_os_str()))?;
    lauf.melden(app, DownloadStand::Start, Some(lauf.name.clone()));
    zaehlen(&lauf.tab, true);
    let voll = std::rc::Rc::new(Cell::new(false));
    let mut token = 0i64;
    {
        let (platz, voll) = (platz.clone(), voll.clone());
        let gemessen = Cell::new(0i64);
        vorgang.add_BytesReceivedChanged(
            &BytesReceivedChangedEventHandler::create(Box::new(move |sender, _| {
                let Some(vorgang) = sender else { return Ok(()) };
                let mut bisher = 0i64;
                vorgang.BytesReceived(&mut bisher)?;
                if bisher - gemessen.get() < MESSEN_ALLE {
                    return Ok(());
                }
                gemessen.set(bisher);
                let mut gesamt = 0i64;
                vorgang.TotalBytesToReceive(&mut gesamt)?;
                let noch = (gesamt - bisher).max(0) as u64;
                if !quarantaene::frei(&platz).is_some_and(|f| passt(f, noch)) {
                    voll.set(true);
                    vorgang.Cancel()?;
                }
                Ok(())
            })),
            &mut token,
        )?;
    }
    let app = app.clone();
    vorgang.add_StateChanged(
        &StateChangedEventHandler::create(Box::new(move |sender, _| {
            let Some(vorgang) = sender else { return Ok(()) };
            let mut stand = COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
            vorgang.State(&mut stand)?;
            if stand == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED {
                zaehlen(&lauf.tab, false);
                lauf.melden(&app, DownloadStand::Pruefung, Some(lauf.name.clone()));
                let (app, lauf, platz) = (app.clone(), lauf.clone(), platz.clone());
                std::thread::spawn(move || nach_dem_laden(&app, lauf, platz));
            } else if stand == COREWEBVIEW2_DOWNLOAD_STATE_INTERRUPTED {
                zaehlen(&lauf.tab, false);
                let mut grund = COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE;
                vorgang.InterruptReason(&mut grund)?;
                quarantaene::wegraeumen(&platz);
                let stand = if voll.get() {
                    DownloadStand::Speicher
                } else if blockiert(grund) {
                    DownloadStand::Blockiert
                } else {
                    DownloadStand::Fehler
                };
                lauf.melden(&app, stand, Some(lauf.name.clone()));
            }
            Ok(())
        })),
        &mut token,
    )
}

/// Eigener Faden: prüfen, dann ablegen. Was der Virenschutz ablehnt, wird gelöscht.
fn nach_dem_laden(app: &AppHandle, lauf: Lauf, platz: PathBuf) {
    if !pruefung::pruefen(&platz, &lauf.url, lauf.privat) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn die_reserve_bleibt_frei() {
        assert!(passt(RESERVE + 10, 10));
        assert!(!passt(RESERVE + 10, 11));
        assert!(!passt(RESERVE - 1, 0), "schon vorher zu wenig");
        assert!(!passt(0, u64::MAX));
    }

    #[test]
    fn je_tab_hoechstens_drei() {
        let tab = "tab-grenze";
        for _ in 0..JE_TAB {
            assert!(!zu_viele(tab));
            zaehlen(tab, true);
        }
        assert!(zu_viele(tab));
        assert!(!zu_viele("tab-anderer"));
        zaehlen(tab, false);
        assert!(!zu_viele(tab));
        for _ in 0..5 {
            zaehlen(tab, false);
        }
        assert!(LAUFEND.with(|l| !l.borrow().contains_key(tab)));
    }
}
