//! Ein Paket aus dem Chrome Web Store holen. Google sieht dabei die
//! IP-Adresse und die Kennung der Erweiterung (Datenschutzerklärung,
//! „Erweiterungen“).

use std::time::Duration;

use super::crx::ist_id;

/// Größer ist kein Paket, das wir annehmen (Bitwarden: 24 MB).
const HOECHSTENS_BYTES: usize = 128 * 1024 * 1024;

/// Die Kennung aus einer Eingabe: die Kennung selbst oder ein Link auf die
/// Seite im Store (`chromewebstore.google.com/detail/<name>/<id>`, auch die
/// alte Adresse unter `chrome.google.com/webstore`).
pub fn id_aus_eingabe(eingabe: &str) -> Option<String> {
    let eingabe = eingabe.trim();
    if ist_id(eingabe) {
        return Some(eingabe.into());
    }
    let url = url::Url::parse(eingabe).ok()?;
    let store = match url.host_str()? {
        "chromewebstore.google.com" => true,
        "chrome.google.com" => url.path().starts_with("/webstore/"),
        _ => false,
    };
    if url.scheme() != "https" || !store {
        return None;
    }
    url.path_segments()?.find(|t| ist_id(t)).map(str::to_string)
}

/// Die Fassung der WebView2, damit der Store ein passendes Paket liefert.
fn fassung() -> String {
    use webview2_com::Microsoft::Web::WebView2::Win32::GetAvailableCoreWebView2BrowserVersionString;
    let mut text = windows::core::PWSTR::null();
    let gelesen = unsafe { GetAvailableCoreWebView2BrowserVersionString(windows::core::PCWSTR::null(), &mut text) };
    let fassung = if gelesen.is_ok() { webview2_com::take_pwstr(text) } else { String::new() };
    let nur_zahlen = fassung.split(' ').next().unwrap_or_default();
    if !nur_zahlen.is_empty() && nur_zahlen.bytes().all(|b| b.is_ascii_digit() || b == b'.') {
        nur_zahlen.to_string()
    } else {
        "140.0.0.0".into()
    }
}

pub fn adresse(id: &str) -> String {
    format!(
        "https://clients2.google.com/service/update2/crx?response=redirect&prodversion={}&acceptformat=crx3&x=id%3D{id}%26uc",
        fassung()
    )
}

/// Holt das Paket. Fehlercodes: `nicht_verfuegbar` (204, nicht im Store
/// oder nicht für diese Fassung), `zu_gross`, `netz`.
pub async fn holen(id: &str) -> Result<Vec<u8>, String> {
    let mut antwort = crate::netz::client(Duration::from_secs(120))
        .map_err(|_| "netz".to_string())?
        .get(adresse(id))
        .send()
        .await
        .map_err(|_| "netz".to_string())?;
    if antwort.status() == reqwest::StatusCode::NO_CONTENT || antwort.status() == reqwest::StatusCode::NOT_FOUND {
        return Err("nicht_verfuegbar".into());
    }
    if !antwort.status().is_success() {
        return Err("netz".into());
    }
    if antwort.content_length().is_some_and(|l| l as usize > HOECHSTENS_BYTES) {
        return Err("zu_gross".into());
    }
    let mut bytes = Vec::new();
    while let Some(teil) = antwort.chunk().await.map_err(|_| "netz".to_string())? {
        if bytes.len() + teil.len() > HOECHSTENS_BYTES {
            return Err("zu_gross".into());
        }
        bytes.extend_from_slice(&teil);
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    const ID: &str = "ddkjiahejlhfcafbddmgiahcphecmpfh";

    #[test]
    fn nimmt_kennung_und_links_auf_den_store() {
        for gut in [
            ID.to_string(),
            format!("  {ID}\n"),
            format!("https://chromewebstore.google.com/detail/ublock-origin-lite/{ID}"),
            format!("https://chromewebstore.google.com/detail/{ID}?hl=de"),
            format!("https://chrome.google.com/webstore/detail/x/{ID}"),
        ] {
            assert_eq!(id_aus_eingabe(&gut).as_deref(), Some(ID), "{gut}");
        }
        for schlecht in [
            format!("http://chromewebstore.google.com/detail/x/{ID}"),
            format!("https://chromewebstore.google.com.boese.example/detail/x/{ID}"),
            format!("https://chrome.google.com/x/{ID}"),
            format!("https://microsoftedge.microsoft.com/addons/detail/{ID}"),
            "abc".into(),
            ID.to_uppercase(),
        ] {
            assert_eq!(id_aus_eingabe(&schlecht), None, "{schlecht}");
        }
    }

    #[test]
    fn die_adresse_nennt_nur_kennung_und_fassung() {
        let a = url::Url::parse(&adresse(ID)).unwrap();
        assert_eq!(a.host_str(), Some("clients2.google.com"));
        let x = a.query_pairs().find(|(k, _)| k == "x").unwrap().1;
        assert_eq!(x, format!("id={ID}&uc"));
    }
}
