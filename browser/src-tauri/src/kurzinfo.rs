//! Namensblasen über der Seite.
//!
//! Die Tabs liegen als eigene Fenster über der Oberfläche (AGENTS.md
//! Punkt 121): eine Kurzinfo der Oberfläche, die in den Seitenbereich ragt,
//! ist darunter unsichtbar. Die Leiste links zeigt nur Symbole, ihre Namen
//! stehen rechts daneben, also über der Seite. Windows zeichnet sie deshalb
//! in einem kleinen Fenster, das dem Hauptfenster gehört: es liegt über
//! Oberfläche und Tabs, nimmt weder Fokus noch Maus und geht mit dem
//! Hauptfenster weg. Die Seite merkt davon nichts, anders als beim Verdecken.
//!
//! Steht die Leiste rechts, steht der Name links vom Symbol (`Richtung::Links`).
//!
//! Auf Android liegt nichts neben der Seite; dort tut der Befehl nichts.
#![cfg_attr(not(windows), allow(dead_code))]

use serde::Deserialize;
use tauri::AppHandle;

/// Wohin die Blase vom Punkt `x`/`y` aus wächst.
#[derive(Deserialize, Default, Clone, Copy, PartialEq, Debug)]
#[serde(rename_all = "snake_case")]
pub enum Richtung {
    /// `x` ist die linke Kante, `y` die senkrechte Mitte.
    #[default]
    Rechts,
    /// `x` ist die rechte Kante, `y` die senkrechte Mitte.
    Links,
    /// Oben in der Mitte des Bildschirms, größer; `x` und `y` zählen nicht.
    /// Der Hinweis beim Vollbild einer Seite (`vollbildHinweis.ts`).
    Oben,
}

/// Eine Blase: Text, Ankerpunkt in CSS-Pixeln der Oberfläche, Farben als RGB
/// aus den Design-Tokens.
#[derive(Deserialize)]
pub struct Blase {
    text: String,
    x: f64,
    y: f64,
    #[serde(default)]
    richtung: Richtung,
    hintergrund: [u8; 3],
    schrift: [u8; 3],
    rand: [u8; 3],
}

/// Ohne `blase` verschwindet sie.
#[tauri::command(async)]
pub fn kurzinfo(app: AppHandle, blase: Option<Blase>) -> Result<(), String> {
    #[cfg(windows)]
    return fenster::zeigen(&app, blase);
    #[cfg(not(windows))]
    {
        let _ = (app, blase);
        Ok(())
    }
}

/// Höchstens so viele Zeichen; eine Blase ist ein Name, kein Text.
const HOECHSTENS: usize = 80;

fn gekuerzt(text: &str) -> String {
    text.chars().filter(|&z| !z.is_control() && !crate::downloads::ist_unsichtbar(z)).take(HOECHSTENS).collect()
}

/// Linke Kante der Blase in Bildschirmpixeln.
fn linke_kante(x: i32, breite: i32, richtung: Richtung) -> i32 {
    match richtung {
        Richtung::Rechts => x,
        Richtung::Links => x - breite,
        Richtung::Oben => x - breite / 2,
    }
}

#[cfg(windows)]
mod fenster {
    use std::cell::{Cell, RefCell};

    use tauri::{AppHandle, Manager};
    use windows::core::{w, PCWSTR};
    use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, RECT, SIZE, WPARAM};
    use windows::Win32::Graphics::Dwm::{DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUNDSMALL};
    use windows::Win32::Graphics::Gdi::*;
    use windows::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows::Win32::UI::WindowsAndMessaging::*;

    use super::{gekuerzt, Blase};

    struct Bild {
        text: Vec<u16>,
        hintergrund: COLORREF,
        schrift: COLORREF,
        font: HFONT,
        hoehe: i32,
    }

    thread_local! {
        static FENSTER: Cell<Option<HWND>> = const { Cell::new(None) };
        static BILD: RefCell<Option<Bild>> = const { RefCell::new(None) };
    }

    fn farbe([r, g, b]: [u8; 3]) -> COLORREF {
        COLORREF(r as u32 | (g as u32) << 8 | (b as u32) << 16)
    }

    pub fn zeigen(app: &AppHandle, blase: Option<Blase>) -> Result<(), String> {
        let haupt = app.get_webview_window("main").ok_or("Hauptfenster fehlt")?;
        app.run_on_main_thread(move || unsafe {
            match blase {
                Some(b) => {
                    let Ok(besitzer) = haupt.hwnd() else { return };
                    anzeigen(HWND(besitzer.0), haupt.scale_factor().unwrap_or(1.0), b)
                }
                None => {
                    if let Some(f) = FENSTER.get() {
                        let _ = ShowWindow(f, SW_HIDE);
                    }
                }
            }
        })
        .map_err(|e| e.to_string())
    }

    unsafe fn anlegen(besitzer: HWND) -> Option<HWND> {
        let instanz = GetModuleHandleW(None).ok()?;
        let klasse = WNDCLASSW {
            lpfnWndProc: Some(ablauf),
            hInstance: instanz.into(),
            lpszClassName: w!("MsbKurzinfo"),
            ..Default::default()
        };
        RegisterClassW(&klasse);
        let f = CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            w!("MsbKurzinfo"),
            PCWSTR::null(),
            WS_POPUP,
            0,
            0,
            0,
            0,
            Some(besitzer),
            None,
            Some(instanz.into()),
            None,
        )
        .ok()?;
        let ecke = DWMWCP_ROUNDSMALL;
        let _ = DwmSetWindowAttribute(f, DWMWA_WINDOW_CORNER_PREFERENCE, &ecke as *const _ as _, size_of_val(&ecke) as u32);
        FENSTER.set(Some(f));
        Some(f)
    }

    unsafe fn anzeigen(besitzer: HWND, faktor: f64, b: Blase) {
        let Some(f) = FENSTER.get().or_else(|| anlegen(besitzer)) else { return };
        let px = |v: f64| (v * faktor).round() as i32;
        let oben = b.richtung == super::Richtung::Oben;
        let hoehe = px(if oben { 16.0 } else { 12.0 });
        let text: Vec<u16> = gekuerzt(&b.text).encode_utf16().collect();

        BILD.with(|bild| {
            let mut bild = bild.borrow_mut();
            let font = match bild.take() {
                Some(alt) if alt.hoehe == hoehe => alt.font,
                Some(alt) => {
                    let _ = DeleteObject(alt.font.into());
                    schrift(hoehe)
                }
                None => schrift(hoehe),
            };
            *bild = Some(Bild { text: text.clone(), hintergrund: farbe(b.hintergrund), schrift: farbe(b.schrift), font, hoehe });
        });
        let rand = farbe(b.rand);
        let _ = DwmSetWindowAttribute(f, DWMWA_BORDER_COLOR, &rand as *const _ as _, size_of_val(&rand) as u32);

        let mut groesse = SIZE::default();
        let dc = GetDC(Some(f));
        let alt = SelectObject(dc, BILD.with(|b| b.borrow().as_ref().map(|b| b.font)).unwrap_or_default().into());
        let _ = GetTextExtentPoint32W(dc, &text, &mut groesse);
        SelectObject(dc, alt);
        ReleaseDC(Some(f), dc);

        // px-2 py-1 wie die Kurzinfo der Oberfläche, der Hinweis px-4 py-2.
        let breite = groesse.cx + px(if oben { 32.0 } else { 16.0 });
        let hoch = groesse.cy + px(if oben { 16.0 } else { 8.0 });
        let mut punkt = POINT { x: px(b.x), y: px(b.y) };
        if oben {
            // Am Bildschirm, nicht am Fenster: das stellt gerade erst auf Vollbild um.
            let mut info = MONITORINFO { cbSize: size_of::<MONITORINFO>() as u32, ..Default::default() };
            let _ = GetMonitorInfoW(MonitorFromWindow(besitzer, MONITOR_DEFAULTTONEAREST), &mut info);
            let m = info.rcMonitor;
            punkt = POINT { x: (m.left + m.right) / 2, y: m.top + px(24.0) + hoch / 2 };
        } else {
            let _ = ClientToScreen(besitzer, &mut punkt);
        }
        let links = super::linke_kante(punkt.x, breite, b.richtung);
        let _ = SetWindowPos(f, Some(HWND_TOP), links, punkt.y - hoch / 2, breite, hoch, SWP_NOACTIVATE | SWP_SHOWWINDOW);
        let _ = InvalidateRect(Some(f), None, true);
    }

    unsafe fn schrift(hoehe: i32) -> HFONT {
        CreateFontW(
            -hoehe,
            0,
            0,
            0,
            FW_MEDIUM.0 as i32,
            0,
            0,
            0,
            DEFAULT_CHARSET,
            OUT_DEFAULT_PRECIS,
            CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY,
            0,
            w!("Segoe UI"),
        )
    }

    unsafe extern "system" fn ablauf(f: HWND, nachricht: u32, w: WPARAM, l: LPARAM) -> LRESULT {
        match nachricht {
            // Die Maus geht hindurch, an die Leiste darunter.
            WM_NCHITTEST => LRESULT(HTTRANSPARENT as isize),
            WM_MOUSEACTIVATE => LRESULT(MA_NOACTIVATE as isize),
            WM_ERASEBKGND => LRESULT(1),
            WM_PAINT => {
                let mut ps = PAINTSTRUCT::default();
                let dc = BeginPaint(f, &mut ps);
                let mut flaeche = RECT::default();
                let _ = GetClientRect(f, &mut flaeche);
                BILD.with(|bild| {
                    if let Some(b) = bild.borrow_mut().as_mut() {
                        let pinsel = CreateSolidBrush(b.hintergrund);
                        FillRect(dc, &flaeche, pinsel);
                        let _ = DeleteObject(pinsel.into());
                        let alt = SelectObject(dc, b.font.into());
                        SetBkMode(dc, TRANSPARENT);
                        SetTextColor(dc, b.schrift);
                        DrawTextW(dc, &mut b.text, &mut flaeche, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
                        SelectObject(dc, alt);
                    }
                });
                let _ = EndPaint(f, &ps);
                LRESULT(0)
            }
            _ => DefWindowProcW(f, nachricht, w, l),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{gekuerzt, linke_kante, Blase, Richtung};

    #[test]
    fn eine_blase_links_endet_am_punkt() {
        assert_eq!(linke_kante(500, 80, Richtung::Rechts), 500);
        assert_eq!(linke_kante(500, 80, Richtung::Links), 420);
        assert_eq!(linke_kante(500, 80, Richtung::Oben), 460);
        let ohne: Blase = serde_json::from_str(r#"{"text":"a","x":1,"y":2,"hintergrund":[0,0,0],"schrift":[0,0,0],"rand":[0,0,0]}"#).unwrap();
        assert_eq!(ohne.richtung, Richtung::Rechts);
    }

    #[test]
    fn steuerzeichen_fallen_und_lange_texte_werden_gekuerzt() {
        assert_eq!(gekuerzt("Mes\u{202E}\nsenger"), "Messenger");
        // Dieselbe Liste wie für Dateinamen: Trennhilfe, Füller und unsichtbare Operatoren.
        assert_eq!(gekuerzt("Mes\u{00AD}sen\u{115F}g\u{2062}er"), "Messenger");
        assert_eq!(gekuerzt(&"a".repeat(200)).len(), 80);
    }
}
