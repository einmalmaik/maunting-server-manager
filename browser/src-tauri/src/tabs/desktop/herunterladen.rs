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
use std::collections::{HashMap, HashSet, VecDeque};
use std::rc::Rc;
use std::time::Instant;
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
    /// Laufende WebView2-Vorgänge je Download-`nr`, für `abbrechen`. Sie
    /// gehören der Umgebung, nicht dem Tab: ein geschlossener Tab bricht
    /// seinen Download nicht ab. Nur auf dem UI-Faden.
    static VORGAENGE: RefCell<HashMap<u64, ICoreWebView2DownloadOperation>> = RefCell::new(HashMap::new());
    /// Downloads, deren Abbruch der Nutzer angestoßen hat (`abbrechen`).
    static NUTZER_ABBRUCH: RefCell<HashSet<u64>> = RefCell::new(HashSet::new());
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

/// `TotalBytesToReceive == -1` heißt unbekannte Länge; 0 ist eine leere
/// Datei mit bekannter Größe — beides nicht verwechseln.
fn gesamt_optisch(gesamt: i64) -> Option<u64> {
    u64::try_from(gesamt).ok()
}

/// Geglättete Empfangsrate aus (Sekunden, Bytes)-Proben. `None`, solange sie
/// nicht brauchbar ist: weniger als zwei Proben oder kein Zeitabstand. Ohne
/// brauchbare Rate gibt es keine ETA.
fn rate_pro_sekunde(proben: &[(f64, i64)]) -> Option<f64> {
    let (erste, letzte) = (proben.first()?, proben.last()?);
    let dauer = letzte.0 - erste.0;
    if dauer < 0.2 {
        return None;
    }
    let rate = (letzte.1 - erste.1) as f64 / dauer;
    (rate > 0.0).then_some(rate)
}

/// Ungefähre Restzeit in Sekunden; nur mit Restmenge und brauchbarer Rate.
fn rest_sekunden(noch: u64, rate: Option<f64>) -> Option<f64> {
    Some(noch as f64 / rate.filter(|r| *r > 0.0)?)
}

/// Höchstens vier Fortschrittsmeldungen je Sekunde und Download; die erste
/// Meldung geht immer sofort raus.
fn drossel_frei(letzte: Option<Instant>, jetzt: Instant) -> bool {
    match letzte {
        None => true,
        Some(l) => jetzt.duration_since(l).as_millis() >= MELDEN_ALLE_MS,
    }
}

/// Ordnet eine Unterbrechung verständlich ein: kein Platz, Abbruch durch den
/// Nutzer, Sicherheitsblockierung des Virenschutzes oder sonst ein Fehler
/// (Netz, Server, Berechtigung).
fn interrupt_stand(voll: bool, nutzerabbruch: bool, grund: COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON) -> DownloadStand {
    if voll || grund == COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_FILE_NO_SPACE {
        DownloadStand::Speicher
    } else if nutzerabbruch || grund == COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_USER_CANCELED {
        DownloadStand::Abgebrochen
    } else if blockiert(grund) {
        DownloadStand::Blockiert
    } else {
        DownloadStand::Fehler
    }
}

/// Höchstens so viele Millisekunden zwischen zwei Fortschrittsmeldungen.
const MELDEN_ALLE_MS: u128 = 250;
/// Probenfenster für die Raten-Glättung.
const PROBEN_FENSTER_S: f64 = 3.0;
const PROBEN_MAX: usize = 24;

/// Glättung und Takt eines laufenden Downloads. Nur auf dem UI-Faden.
struct Fort {
    app: AppHandle,
    tab: String,
    nr: u64,
    letzte_meldung: Cell<Option<Instant>>,
    letzte_bytes: Cell<i64>,
    letzte_bytes_zeit: Cell<Instant>,
    proben: RefCell<VecDeque<(Instant, i64)>>,
}

impl Fort {
    /// Automatischer Neustart mit sinkender Bytezahl: alte Proben wären eine
    /// falsche Rate und eine falsche ETA.
    fn neustart(&self, jetzt: Instant) {
        self.proben.borrow_mut().clear();
        self.letzte_bytes_zeit.set(jetzt);
    }

    /// Eine neue Messung; meldet gedrosselt `DownloadFortschritt` auf nativen
    /// Byte-Samples und monotoner Zeit. Ohne neue Bytes sinkt die Rate auf
    /// `None`, statt eine alte positive Geschwindigkeit weiter anzuzeigen.
    fn probe(&self, empfangen: i64, gesamt_roh: i64) {
        let jetzt = Instant::now();
        let davor = self.letzte_bytes.get();
        if empfangen < davor {
            self.neustart(jetzt);
        } else if empfangen > davor {
            self.letzte_bytes_zeit.set(jetzt);
        }
        self.letzte_bytes.set(empfangen);
        {
            let mut proben = self.proben.borrow_mut();
            proben.push_back((jetzt, empfangen));
            while proben.len() > PROBEN_MAX {
                proben.pop_front();
            }
            while proben.len() >= 2
                && jetzt.duration_since(proben[0].0).as_secs_f64() > PROBEN_FENSTER_S
            {
                proben.pop_front();
            }
        }
        if !drossel_frei(self.letzte_meldung.get(), jetzt) {
            return;
        }
        self.letzte_meldung.set(Some(jetzt));
        let gesamt = gesamt_optisch(gesamt_roh);
        let fenster: Vec<(f64, i64)> = {
            let proben = self.proben.borrow();
            let null = proben.front().map(|p| p.0).unwrap_or(jetzt);
            proben.iter().map(|(t, b)| (t.duration_since(null).as_secs_f64(), *b)).collect()
        };
        let rate = rate_pro_sekunde(&fenster);
        let rest = gesamt.and_then(|g| rest_sekunden(g.saturating_sub(empfangen.max(0) as u64), rate));
        melden(
            &self.app,
            TabEreignis::DownloadFortschritt {
                id: self.tab.clone(),
                nr: self.nr,
                empfangen,
                gesamt: gesamt.map(|g| g as i64),
                bytes_pro_sekunde: rate,
                rest_sekunden: rest,
                ohne_daten_sekunden: jetzt.duration_since(self.letzte_bytes_zeit.get()).as_secs(),
            },
        );
    }
}

/// Bricht einen laufenden Download ab. Nur auf dem UI-Faden. Gibt an, ob ein
/// Vorgang gefunden und `Cancel` angestoßen wurde; das Ende meldet danach
/// der `StateChanged`-Rückruf als `Abgebrochen`, Teilreste räumt er weg und
/// Quotas gibt er frei.
pub(super) fn abbrechen(nr: u64) -> bool {
    let vorgang = VORGAENGE.with(|v| v.borrow().get(&nr).cloned());
    let Some(vorgang) = vorgang else { return false };
    NUTZER_ABBRUCH.with(|a| a.borrow_mut().insert(nr));
    // UI-Faden: der einzige Faden, der den Vorgang anfassen darf.
    unsafe { vorgang.Cancel().is_ok() }
}

/// Einmaliges Ende eines Downloads: Zähler, Register und Teilreste werden
/// aufgeräumt; nur die erste Meldung zählt, verspätete kommen nicht mehr an.
fn beenden(
    app: &AppHandle,
    lauf: &Lauf,
    platz: &PathBuf,
    voll: &Cell<bool>,
    fertig: &Cell<bool>,
    geladen: bool,
    grund: COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON,
) {
    if fertig.replace(true) {
        return;
    }
    VORGAENGE.with(|v| v.borrow_mut().remove(&lauf.nr));
    zaehlen(&lauf.tab, false);
    if geladen {
        lauf.melden(app, DownloadStand::Pruefung, Some(lauf.name.clone()));
        let (app, lauf, platz) = (app.clone(), lauf.clone(), platz.clone());
        std::thread::spawn(move || nach_dem_laden(&app, lauf, platz));
        return;
    }
    let nutzerabbruch = NUTZER_ABBRUCH.with(|a| a.borrow_mut().remove(&lauf.nr));
    quarantaene::wegraeumen(platz);
    lauf.melden(app, interrupt_stand(voll.get(), nutzerabbruch, grund), Some(lauf.name.clone()));
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
    let voll = Rc::new(Cell::new(false));
    let fertig = Rc::new(Cell::new(false));
    let start = Instant::now();
    let fort = Rc::new(Fort {
        app: app.clone(),
        tab: lauf.tab.clone(),
        nr: lauf.nr,
        letzte_meldung: Cell::new(None),
        letzte_bytes: Cell::new(0),
        letzte_bytes_zeit: Cell::new(start),
        proben: RefCell::new(VecDeque::new()),
    });
    VORGAENGE.with(|v| v.borrow_mut().insert(lauf.nr, vorgang.clone()));
    let mut token = 0i64;
    {
        let (platz, voll, fertig, fort) = (platz.clone(), voll.clone(), fertig.clone(), fort.clone());
        let geprueft = Cell::new(0i64);
        vorgang.add_BytesReceivedChanged(
            &BytesReceivedChangedEventHandler::create(Box::new(move |sender, _| {
                if fertig.get() {
                    return Ok(()); // Verspätete Messung nach dem Ende.
                }
                let Some(vorgang) = sender else { return Ok(()) };
                let mut bisher = 0i64;
                vorgang.BytesReceived(&mut bisher)?;
                let mut gesamt = 0i64;
                vorgang.TotalBytesToReceive(&mut gesamt)?;
                if bisher - geprueft.get() >= MESSEN_ALLE {
                    geprueft.set(bisher);
                    let noch = (gesamt - bisher).max(0) as u64;
                    if !quarantaene::frei(&platz).is_some_and(|f| passt(f, noch)) {
                        voll.set(true);
                        vorgang.Cancel()?;
                    }
                }
                fort.probe(bisher, gesamt);
                Ok(())
            })),
            &mut token,
        )?;
    }
    {
        let (app, lauf, platz, voll, fertig) = (app.clone(), lauf.clone(), platz.clone(), voll.clone(), fertig.clone());
        vorgang.add_StateChanged(
            &StateChangedEventHandler::create(Box::new(move |sender, _| {
                let Some(vorgang) = sender else { return Ok(()) };
                let mut stand = COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
                vorgang.State(&mut stand)?;
                if stand == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED {
                    beenden(&app, &lauf, &platz, &voll, &fertig, true, COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE);
                } else if stand == COREWEBVIEW2_DOWNLOAD_STATE_INTERRUPTED {
                    let mut grund = COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE;
                    vorgang.InterruptReason(&mut grund)?;
                    beenden(&app, &lauf, &platz, &voll, &fertig, false, grund);
                }
                Ok(())
            })),
            &mut token,
        )?;
    }
    // Der Stand nach dem Anmelden: ein schneller Download ist schon fertig,
    // bevor seine erste Änderung ankommt — ohne dies bliebe der Eintrag
    // ewig auf „Wird geladen“ stehen.
    {
        let mut stand = COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
        vorgang.State(&mut stand)?;
        let mut bisher = 0i64;
        vorgang.BytesReceived(&mut bisher)?;
        let mut gesamt = 0i64;
        vorgang.TotalBytesToReceive(&mut gesamt)?;
        if stand == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED {
            beenden(app, &lauf, &platz, &voll, &fertig, true, COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE);
        } else if stand == COREWEBVIEW2_DOWNLOAD_STATE_INTERRUPTED {
            let mut grund = COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE;
            vorgang.InterruptReason(&mut grund)?;
            beenden(app, &lauf, &platz, &voll, &fertig, false, grund);
        } else {
            fort.probe(bisher, gesamt); // Der Anfang kommt sofort.
        }
        Ok(())
    }
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
    fn unbekannt_bleibt_null_leer_bleibt_null() {
        assert_eq!(gesamt_optisch(-1), None);
        assert_eq!(gesamt_optisch(0), Some(0));
        assert_eq!(gesamt_optisch(41), Some(41));
    }

    #[test]
    fn rate_braucht_zwei_proben_mit_abstand() {
        assert_eq!(rate_pro_sekunde(&[]), None);
        assert_eq!(rate_pro_sekunde(&[(0.0, 100)]), None);
        assert_eq!(rate_pro_sekunde(&[(0.0, 0), (0.1, 100)]), None);
        assert_eq!(rate_pro_sekunde(&[(0.0, 0), (2.0, 4000)]), Some(2000.0));
        assert_eq!(rate_pro_sekunde(&[(1.0, 5000), (3.0, 5000)]), None);
    }

    #[test]
    fn rest_nur_mit_brauchbarer_rate() {
        assert_eq!(rest_sekunden(8000, Some(2000.0)), Some(4.0));
        assert_eq!(rest_sekunden(0, Some(2000.0)), Some(0.0));
        assert_eq!(rest_sekunden(8000, None), None);
        assert_eq!(rest_sekunden(8000, Some(0.0)), None);
    }

    #[test]
    fn drossel_laesst_hoechstens_vier_je_sekunde() {
        use std::time::Duration;
        let jetzt = Instant::now();
        assert!(drossel_frei(None, jetzt));
        assert!(!drossel_frei(Some(jetzt), jetzt));
        assert!(!drossel_frei(Some(jetzt), jetzt + Duration::from_millis(100)));
        assert!(drossel_frei(Some(jetzt), jetzt + Duration::from_millis(250)));
    }

    #[test]
    fn unterbrechungen_treffen_die_richtige_kategorie() {
        assert!(matches!(
            interrupt_stand(true, false, COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE),
            DownloadStand::Speicher
        ));
        assert!(matches!(
            interrupt_stand(false, true, COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE),
            DownloadStand::Abgebrochen
        ));
        assert!(matches!(
            interrupt_stand(false, false, COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_USER_CANCELED),
            DownloadStand::Abgebrochen
        ));
        assert!(matches!(
            interrupt_stand(false, false, COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_FILE_MALICIOUS),
            DownloadStand::Blockiert
        ));
        assert!(matches!(
            interrupt_stand(false, false, COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NETWORK_FAILED),
            DownloadStand::Fehler
        ));
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
