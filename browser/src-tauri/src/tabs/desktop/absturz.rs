//! Welche Prozessfehler einer WebView2 den Tab treffen.
//!
//! `ProcessFailed` kommt für jeden Prozess, den die Seite benutzt: Rahmen
//! fremder Herkunft, GPU, Netzwerk- und andere Hilfsprozesse, auch für einen
//! Renderer, der nur kurz hängt. Bis 09.10.2026 galt jeder davon als Absturz,
//! und der Tab zeigte die Absturzseite über einer Seite, die weiterlief.
//! Abgestürzt ist der Tab nur, wenn sein Seitenprozess oder
//! der ganze Browserprozess weg ist; alles andere startet WebView2 selbst neu,
//! ein Rahmen zeigt seinen eigenen Fehler.

use webview2_com::Microsoft::Web::WebView2::Win32::*;
use windows::core::Interface;

use crate::tabs::{melden, TabEreignis};

pub(super) fn trifft_den_tab(art: COREWEBVIEW2_PROCESS_FAILED_KIND) -> bool {
    art == COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED
        || art == COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED
}

pub(super) unsafe fn verarbeiten(app: &tauri::AppHandle, id: &str, args: &ICoreWebView2ProcessFailedEventArgs) {
    let mut art = COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED;
    let gelesen = args.ProcessFailedKind(&mut art);
    let mut grund = COREWEBVIEW2_PROCESS_FAILED_REASON_UNEXPECTED;
    let mut code = 0i32;
    if let Ok(args2) = args.cast::<ICoreWebView2ProcessFailedEventArgs2>() {
        let _ = args2.Reason(&mut grund);
        let _ = args2.ExitCode(&mut code);
    }
    if gelesen.is_err() || trifft_den_tab(art) {
        eprintln!("[MSB] Seite eines Tabs abgestürzt (Art {}, Grund {}, Code {code:#x})", art.0, grund.0);
        melden(app, TabEreignis::Absturz { id: id.to_string() });
        return;
    }
    eprintln!("[MSB] Hilfsprozess eines Tabs beendet (Art {}, Grund {}, Code {code:#x}); der Tab läuft weiter", art.0, grund.0);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nur_seite_und_browser_sind_ein_absturz() {
        assert!(trifft_den_tab(COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED));
        assert!(trifft_den_tab(COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED));
        for art in [
            COREWEBVIEW2_PROCESS_FAILED_KIND_FRAME_RENDER_PROCESS_EXITED,
            COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_UNRESPONSIVE,
            COREWEBVIEW2_PROCESS_FAILED_KIND_GPU_PROCESS_EXITED,
            COREWEBVIEW2_PROCESS_FAILED_KIND_UTILITY_PROCESS_EXITED,
            COREWEBVIEW2_PROCESS_FAILED_KIND_SANDBOX_HELPER_PROCESS_EXITED,
            COREWEBVIEW2_PROCESS_FAILED_KIND_PPAPI_PLUGIN_PROCESS_EXITED,
            COREWEBVIEW2_PROCESS_FAILED_KIND_PPAPI_BROKER_PROCESS_EXITED,
            COREWEBVIEW2_PROCESS_FAILED_KIND_UNKNOWN_PROCESS_EXITED,
        ] {
            assert!(!trifft_den_tab(art), "Art {} ist kein Absturz des Tabs", art.0);
        }
    }
}
