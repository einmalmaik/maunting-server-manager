//! Was eine Seite fragt, fragt die Oberfläche.
//!
//! Für Rechtsklick, `alert`/`confirm`/`prompt`, Rechte (Kamera, Standort, …)
//! und die HTTP-Anmeldung zeigte WebView2 eigene Fenster im Stil von Edge. Der
//! Browser nimmt jede dieser Anfragen an sich (`Handled`, Aufschub), meldet sie
//! als [`TabEreignis`] und hält sie hier, bis die Oberfläche mit
//! [`erledigen`] antwortet.
//!
//! Alles läuft auf dem UI-Faden der WebView2; die offenen Anfragen liegen
//! deshalb in einem `thread_local`.

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

use tauri::AppHandle;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{
    BasicAuthenticationRequestedEventHandler, ContextMenuRequestedEventHandler, PermissionRequestedEventHandler,
    ScriptDialogOpeningEventHandler,
};
use windows::core::{Interface, BOOL, HSTRING};
use windows::Win32::Foundation::POINT;

use super::webview2::text;
use crate::tabs::{melden, Antwort, MenueEintrag, TabEreignis};

enum Anfrage {
    Menue(ICoreWebView2ContextMenuRequestedEventArgs),
    Dialog(ICoreWebView2ScriptDialogOpeningEventArgs),
    Recht(ICoreWebView2PermissionRequestedEventArgs),
    Anmeldung(ICoreWebView2BasicAuthenticationRequestedEventArgs),
}

struct Offen {
    tab: String,
    anfrage: Anfrage,
    aufschub: ICoreWebView2Deferral,
}

thread_local! {
    static OFFEN: RefCell<HashMap<u64, Offen>> = RefCell::new(HashMap::new());
}
static NAECHSTE: AtomicU64 = AtomicU64::new(1);

fn ablegen(tab: &str, anfrage: Anfrage, aufschub: ICoreWebView2Deferral) -> u64 {
    let nr = NAECHSTE.fetch_add(1, Ordering::Relaxed);
    OFFEN.with(|o| o.borrow_mut().insert(nr, Offen { tab: tab.to_string(), anfrage, aufschub }));
    nr
}

/// Läuft auf dem UI-Faden (`run_on_main_thread`). Eine unbekannte Nummer ist
/// schon erledigt (Tab geschlossen) und wird übergangen.
pub fn erledigen(nr: u64, antwort: Antwort) {
    let Some(offen) = OFFEN.with(|o| o.borrow_mut().remove(&nr)) else { return };
    unsafe {
        let _ = match (offen.anfrage, antwort) {
            (Anfrage::Menue(args), Antwort::Menue { befehl: Some(befehl) }) => args.SetSelectedCommandId(befehl),
            (Anfrage::Dialog(args), Antwort::Dialog { ok: true, text }) => {
                if let Some(text) = text {
                    let _ = args.SetResultText(&HSTRING::from(text));
                }
                args.Accept()
            }
            (Anfrage::Recht(args), Antwort::Recht { erlauben }) => args.SetState(if erlauben {
                COREWEBVIEW2_PERMISSION_STATE_ALLOW
            } else {
                COREWEBVIEW2_PERMISSION_STATE_DENY
            }),
            (Anfrage::Anmeldung(args), Antwort::Anmeldung { benutzer: Some(benutzer), passwort }) => {
                args.Response().and_then(|r| {
                    r.SetUserName(&HSTRING::from(benutzer))?;
                    r.SetPassword(&HSTRING::from(passwort.unwrap_or_default()))
                })
            }
            (Anfrage::Recht(args), _) => args.SetState(COREWEBVIEW2_PERMISSION_STATE_DENY),
            (Anfrage::Anmeldung(args), _) => args.SetCancel(true),
            // Menü ohne Wahl, Dialog abgelehnt: nichts setzen heißt abbrechen.
            _ => Ok(()),
        };
        let _ = offen.aufschub.Complete();
    }
}

/// Ein Tab geht zu: was er noch fragt, wird abgelehnt.
pub fn vergessen(tab: &str) {
    let nummern: Vec<u64> =
        OFFEN.with(|o| o.borrow().iter().filter(|(_, v)| v.tab == tab).map(|(nr, _)| *nr).collect());
    for nr in nummern {
        erledigen(nr, Antwort::Menue { befehl: None });
    }
}

/// Die Rechte, die eine Seite erfragen kann, mit ihrem Namen in der Oberfläche.
const RECHTE: &[(COREWEBVIEW2_PERMISSION_KIND, &str)] = &[
    (COREWEBVIEW2_PERMISSION_KIND_MICROPHONE, "mikrofon"),
    (COREWEBVIEW2_PERMISSION_KIND_CAMERA, "kamera"),
    (COREWEBVIEW2_PERMISSION_KIND_GEOLOCATION, "standort"),
    (COREWEBVIEW2_PERMISSION_KIND_NOTIFICATIONS, "benachrichtigungen"),
    (COREWEBVIEW2_PERMISSION_KIND_CLIPBOARD_READ, "zwischenablage"),
    (COREWEBVIEW2_PERMISSION_KIND_MULTIPLE_AUTOMATIC_DOWNLOADS, "downloads"),
    (COREWEBVIEW2_PERMISSION_KIND_FILE_READ_WRITE, "dateien"),
    (COREWEBVIEW2_PERMISSION_KIND_OTHER_SENSORS, "sensoren"),
    (COREWEBVIEW2_PERMISSION_KIND_MIDI_SYSTEM_EXCLUSIVE_MESSAGES, "midi"),
    (COREWEBVIEW2_PERMISSION_KIND_WINDOW_MANAGEMENT, "fenster"),
    (COREWEBVIEW2_PERMISSION_KIND_LOCAL_FONTS, "schriften"),
];

pub(crate) fn recht_name(art: COREWEBVIEW2_PERMISSION_KIND) -> &'static str {
    RECHTE.iter().find(|(a, _)| *a == art).map_or("sonstiges", |(_, n)| n)
}

pub(crate) fn recht_art(name: &str) -> Option<COREWEBVIEW2_PERMISSION_KIND> {
    RECHTE.iter().find(|(_, n)| *n == name).map(|(a, _)| *a)
}

/// Höchstens so viel markierten Text gibt das Menü weiter (für „Suchen nach …“).
const AUSWAHL_MAX: usize = 200;

unsafe fn menue_eintraege(args: &ICoreWebView2ContextMenuRequestedEventArgs) -> windows::core::Result<Vec<MenueEintrag>> {
    let liste = args.MenuItems()?;
    let mut anzahl = 0u32;
    liste.Count(&mut anzahl)?;
    let mut eintraege = Vec::new();
    for i in 0..anzahl {
        let eintrag = liste.GetValueAtIndex(i)?;
        let mut art = COREWEBVIEW2_CONTEXT_MENU_ITEM_KIND_COMMAND;
        eintrag.Kind(&mut art)?;
        let mut aktiv = BOOL::default();
        eintrag.IsEnabled(&mut aktiv)?;
        if art != COREWEBVIEW2_CONTEXT_MENU_ITEM_KIND_COMMAND || !aktiv.as_bool() {
            continue;
        }
        let mut befehl = 0i32;
        eintrag.CommandId(&mut befehl)?;
        eintraege.push(MenueEintrag { befehl, name: text(|p| eintrag.Name(p)) });
    }
    Ok(eintraege)
}

pub unsafe fn anbinden(app: &AppHandle, id: &str, core: &ICoreWebView2) -> windows::core::Result<()> {
    let mut token = 0i64;

    if let Ok(core11) = core.cast::<ICoreWebView2_11>() {
        let (app, id) = (app.clone(), id.to_string());
        core11.add_ContextMenuRequested(
            &ContextMenuRequestedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                let ziel = args.ContextMenuTarget()?;
                let mut ort = POINT::default();
                args.Location(&mut ort)?;
                let ja = |lesen: &dyn Fn(*mut BOOL) -> windows::core::Result<()>| {
                    let mut wert = BOOL::default();
                    lesen(&mut wert).is_ok() && wert.as_bool()
                };
                let link = ja(&|p| ziel.HasLinkUri(p)).then(|| text(|p| ziel.LinkUri(p)));
                let bild = ja(&|p| ziel.HasSourceUri(p)).then(|| text(|p| ziel.SourceUri(p)));
                let auswahl = ja(&|p| ziel.HasSelection(p))
                    .then(|| text(|p| ziel.SelectionText(p)).chars().take(AUSWAHL_MAX).collect::<String>());
                let bearbeitbar = ja(&|p| ziel.IsEditable(p));
                let eintraege = menue_eintraege(&args)?;
                args.SetHandled(true)?;
                let nr = ablegen(&id, Anfrage::Menue(args.clone()), args.GetDeferral()?);
                melden(
                    &app,
                    TabEreignis::Kontextmenue {
                        id: id.clone(),
                        nr,
                        x: ort.x,
                        y: ort.y,
                        eintraege,
                        link,
                        bild,
                        auswahl,
                        bearbeitbar,
                    },
                );
                Ok(())
            })),
            &mut token,
        )?;
    }

    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_ScriptDialogOpening(
            &ScriptDialogOpeningEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                let mut art = COREWEBVIEW2_SCRIPT_DIALOG_KIND_ALERT;
                args.Kind(&mut art)?;
                let art = match art {
                    COREWEBVIEW2_SCRIPT_DIALOG_KIND_CONFIRM => "confirm",
                    COREWEBVIEW2_SCRIPT_DIALOG_KIND_PROMPT => "prompt",
                    COREWEBVIEW2_SCRIPT_DIALOG_KIND_BEFOREUNLOAD => "beforeunload",
                    _ => "alert",
                };
                let herkunft = text(|p| args.Uri(p));
                let nachricht = text(|p| args.Message(p));
                let vorgabe = text(|p| args.DefaultText(p));
                let nr = ablegen(&id, Anfrage::Dialog(args.clone()), args.GetDeferral()?);
                melden(
                    &app,
                    TabEreignis::Dialog { id: id.clone(), nr, dialog: art.into(), herkunft, text: nachricht, vorgabe },
                );
                Ok(())
            })),
            &mut token,
        )?;
    }

    {
        let (app, id) = (app.clone(), id.to_string());
        core.add_PermissionRequested(
            &PermissionRequestedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                let mut art = COREWEBVIEW2_PERMISSION_KIND_UNKNOWN_PERMISSION;
                args.PermissionKind(&mut art)?;
                let herkunft = text(|p| args.Uri(p));
                let nr = ablegen(&id, Anfrage::Recht(args.clone()), args.GetDeferral()?);
                melden(&app, TabEreignis::Recht { id: id.clone(), nr, recht: recht_name(art).into(), herkunft });
                Ok(())
            })),
            &mut token,
        )?;
    }

    if let Ok(core10) = core.cast::<ICoreWebView2_10>() {
        let (app, id) = (app.clone(), id.to_string());
        core10.add_BasicAuthenticationRequested(
            &BasicAuthenticationRequestedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                let herkunft = text(|p| args.Uri(p));
                let bereich = text(|p| args.Challenge(p));
                let nr = ablegen(&id, Anfrage::Anmeldung(args.clone()), args.GetDeferral()?);
                melden(&app, TabEreignis::Anmeldung { id: id.clone(), nr, herkunft, bereich });
                Ok(())
            })),
            &mut token,
        )?;
    }

    Ok(())
}
