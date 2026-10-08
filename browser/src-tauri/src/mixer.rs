//! Name und Symbol im Lautstärkemixer von Windows.
//!
//! Den Ton spielt der Audio-Dienst von WebView2 (`msedgewebview2.exe`, ein
//! Kindprozess des Browsers), und der Mixer nannte ihn „Microsoft Edge
//! WebView2“. Sobald ein Tab Ton abspielt, bekommen die Audio-Sitzungen aller
//! Prozesse unter diesem Prozess unseren Namen und unser Symbol. Windows nimmt
//! beides auch von einem anderen Prozess an (10/2026 im Mixer nachgesehen).
//! Die Sitzung lebt so lange wie der Audio-Dienst; startet er neu, setzt der
//! nächste Ton den Namen wieder.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use tauri::AppHandle;
use windows::core::{Interface, HSTRING, PWSTR};
use windows::Win32::Foundation::CloseHandle;
use windows::Win32::Media::Audio::*;
use windows::Win32::System::Com::*;
use windows::Win32::System::Diagnostics::ToolHelp::*;

/// Vom Ton-Ereignis eines Tabs. Läuft auf eigenem Faden; der Audio-Dienst legt
/// seine Sitzung manchmal erst kurz nach dem Ereignis an.
pub fn benennen(app: &AppHandle) {
    static LAEUFT: AtomicBool = AtomicBool::new(false);
    if LAEUFT.swap(true, Ordering::AcqRel) {
        return;
    }
    let name = app.config().product_name.clone().unwrap_or_else(|| app.package_info().name.clone());
    std::thread::spawn(move || {
        let symbol = std::env::current_exe().map(|p| format!("{},0", p.display())).unwrap_or_default();
        for warten in [0, 500, 2000] {
            std::thread::sleep(Duration::from_millis(warten));
            match unsafe { sitzungen_benennen(&name, &symbol) } {
                Ok(0) => continue,
                Ok(_) => break,
                Err(fehler) => {
                    eprintln!("[MSB] Name im Lautstärkemixer nicht gesetzt: {fehler}");
                    break;
                }
            }
        }
        LAEUFT.store(false, Ordering::Release);
    });
}

/// Prozess → Elternprozess, für alle laufenden Prozesse.
fn eltern() -> HashMap<u32, u32> {
    let mut karte = HashMap::new();
    unsafe {
        let Ok(schnappschuss) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else { return karte };
        let mut e = PROCESSENTRY32W { dwSize: size_of::<PROCESSENTRY32W>() as u32, ..Default::default() };
        let mut weiter = Process32FirstW(schnappschuss, &mut e).is_ok();
        while weiter {
            karte.insert(e.th32ProcessID, e.th32ParentProcessID);
            weiter = Process32NextW(schnappschuss, &mut e).is_ok();
        }
        let _ = CloseHandle(schnappschuss);
    }
    karte
}

/// Liegt `pid` unter `wurzel`? Die Tiefe ist begrenzt, falls Windows eine
/// Kennung schon neu vergeben hat und eine Schleife entsteht.
fn liegt_unter(karte: &HashMap<u32, u32>, mut pid: u32, wurzel: u32) -> bool {
    for _ in 0..8 {
        match karte.get(&pid) {
            Some(&eltern) if eltern == wurzel => return true,
            Some(&eltern) if eltern != 0 && eltern != pid => pid = eltern,
            _ => return false,
        }
    }
    false
}

unsafe fn text(p: PWSTR) -> String {
    if p.is_null() {
        return String::new();
    }
    let s = p.to_string().unwrap_or_default();
    CoTaskMemFree(Some(p.0 as _));
    s
}

/// Benennt die Sitzungen unserer Kindprozesse auf allen Wiedergabegeräten und
/// gibt zurück, wie viele es gab.
unsafe fn sitzungen_benennen(name: &str, symbol: &str) -> windows::core::Result<usize> {
    let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    let karte = eltern();
    let wir = std::process::id();
    let geraete: IMMDeviceEnumerator = CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)?;
    let liste = geraete.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE)?;
    let mut gefunden = 0;
    for g in 0..liste.GetCount()? {
        let Ok(verwaltung) = liste.Item(g)?.Activate::<IAudioSessionManager2>(CLSCTX_ALL, None) else { continue };
        let sitzungen = verwaltung.GetSessionEnumerator()?;
        for i in 0..sitzungen.GetCount()? {
            let sitzung = sitzungen.GetSession(i)?;
            let Ok(pid) = sitzung.cast::<IAudioSessionControl2>().and_then(|s| s.GetProcessId()) else { continue };
            if !liegt_unter(&karte, pid, wir) {
                continue;
            }
            gefunden += 1;
            if text(sitzung.GetDisplayName()?) != name {
                sitzung.SetDisplayName(&HSTRING::from(name), std::ptr::null())?;
                sitzung.SetIconPath(&HSTRING::from(symbol), std::ptr::null())?;
            }
        }
    }
    Ok(gefunden)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nur_nachfahren_des_browsers() {
        // 10 = Browser, 20 = WebView2, 30 = Audio-Dienst; 40 gehört anderen.
        let karte = HashMap::from([(20, 10), (30, 20), (40, 5), (50, 50)]);
        assert!(liegt_unter(&karte, 30, 10));
        assert!(liegt_unter(&karte, 20, 10));
        assert!(!liegt_unter(&karte, 40, 10));
        assert!(!liegt_unter(&karte, 10, 10), "der Browser selbst spielt keinen Ton");
        assert!(!liegt_unter(&karte, 50, 10), "Schleife endet");
    }
}
