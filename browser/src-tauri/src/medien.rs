//! Medienrechte der eigenen Oberfläche.
//!
//! WebView2 beantwortet `getUserMedia` auf `tauri.localhost` nicht von selbst:
//! Die Anfrage bleibt offen, und die lokale Sprachsuche meldet „Mikrofon
//! verweigert“. Wie im Smart System erlaubt dieser Handler ausschließlich
//! Mikrofon und ausschließlich der eigenen Oberfläche; Seiten-Tabs haben ihren
//! eigenen Dialog (`tabs/desktop/rueckfragen.rs`).

use tauri::{AppHandle, Manager};

/// Nur die gebaute Oberfläche und der Vite-Dev-Server.
pub(crate) fn eigene_oberflaeche(uri: &str) -> bool {
    const EIGENE: [&str; 4] = [
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:1450",
        "http://127.0.0.1:1450",
    ];
    EIGENE
        .iter()
        .any(|basis| uri == *basis || uri.starts_with(&format!("{basis}/")))
}

pub fn freigeben(app: &AppHandle) {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_MICROPHONE, COREWEBVIEW2_PERMISSION_STATE_ALLOW,
    };
    use webview2_com::{take_pwstr, PermissionRequestedEventHandler};
    use windows::core::PWSTR;

    let Some(fenster) = app.get_webview_window("main") else { return };
    let _ = fenster.with_webview(|webview| unsafe {
        let Ok(core) = webview.controller().CoreWebView2() else { return };
        let mut merkzettel = 0_i64;
        let _ = core.add_PermissionRequested(
            &PermissionRequestedEventHandler::create(Box::new(|_, args| {
                let Some(args) = args else { return Ok(()) };
                let mut art = COREWEBVIEW2_PERMISSION_KIND::default();
                args.PermissionKind(&mut art)?;
                if art != COREWEBVIEW2_PERMISSION_KIND_MICROPHONE {
                    return Ok(());
                }
                let mut uri = PWSTR::null();
                args.Uri(&mut uri)?;
                if eigene_oberflaeche(&take_pwstr(uri)) {
                    args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                }
                Ok(())
            })),
            &mut merkzettel,
        );
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nur_die_eigene_oberflaeche_und_der_dev_server() {
        for gut in [
            "http://tauri.localhost",
            "https://tauri.localhost/index.html",
            "http://localhost:1450/",
            "http://127.0.0.1:1450/browser.html",
        ] {
            assert!(eigene_oberflaeche(gut), "{gut}");
        }
        for fremd in [
            "https://example.org",
            "https://tauri.localhost.evil.example/",
            "http://localhost:1451/",
            "http://localhost:1450.evil.example/",
            "https://localhost:1450/",
        ] {
            assert!(!eigene_oberflaeche(fremd), "{fremd}");
        }
    }
}
