//! Sprachdaten der Seitenübersetzung von Mozilla laden.
//!
//! Das CDN von Mozilla schickt keine CORS-Köpfe; die Oberfläche kann die
//! Dateien deshalb nicht selbst holen. Rust lädt sie, aber nur, was in der
//! festen Liste steht (`frontend/src/browser/uebersetzung/modelle.json`), und
//! höchstens so viele Bytes, wie dort stehen. Die SHA-256 prüft die
//! Oberfläche, bevor sie eine Datei benutzt oder ablegt (`modelle.ts`).

use std::collections::HashMap;
use std::sync::LazyLock;
use std::time::Duration;

use tauri::ipc::{Channel, Response};

const CDN: &str = "https://firefox-settings-attachments.cdn.mozilla.net/";
/// Ein Modell hat gut 30 MB; auch langsame Leitungen sollen fertig werden.
const FRIST: Duration = Duration::from_secs(30 * 60);
/// So oft meldet der Kanal den Fortschritt.
const MELDEN_ALLE: usize = 256 * 1024;

const LISTE: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/../../frontend/src/browser/uebersetzung/modelle.json"));

/// Ort jeder Datei der Liste und ihre Größe.
static ERLAUBT: LazyLock<HashMap<String, usize>> = LazyLock::new(|| erlaubt(LISTE));

fn erlaubt(liste: &str) -> HashMap<String, usize> {
    let mut orte = HashMap::new();
    let Ok(wert) = serde_json::from_str::<serde_json::Value>(liste) else { return orte };
    let mut nehmen = |d: &serde_json::Value| {
        if let (Some(ort), Some(groesse)) = (d["ort"].as_str(), d["groesse"].as_u64()) {
            orte.insert(ort.to_string(), groesse as usize);
        }
    };
    nehmen(&wert["wasm"]);
    for paar in wert["paare"].as_object().into_iter().flat_map(|p| p.values()) {
        for datei in paar.as_object().into_iter().flat_map(|d| d.values()) {
            nehmen(datei);
        }
    }
    orte
}

/// Lädt eine Datei der Liste. `fortschritt` bekommt die bisher geladenen Bytes.
/// Fehler: `unbekannt`, `netz`, `groesse`.
#[tauri::command(async)]
pub async fn sprachdaten_laden(ort: String, fortschritt: Channel<u64>) -> Result<Response, String> {
    let groesse = *ERLAUBT.get(&ort).ok_or("unbekannt")?;
    let mut antwort = crate::netz::client(FRIST)
        .map_err(|_| "netz")?
        .get(format!("{CDN}{ort}"))
        .send()
        .await
        .map_err(|_| "netz")?;
    if !antwort.status().is_success() {
        return Err("netz".into());
    }
    let mut bytes = Vec::with_capacity(groesse);
    let mut gemeldet = 0;
    while let Some(teil) = antwort.chunk().await.map_err(|_| "netz")? {
        if bytes.len() + teil.len() > groesse {
            return Err("groesse".into());
        }
        bytes.extend_from_slice(&teil);
        if bytes.len() - gemeldet >= MELDEN_ALLE {
            gemeldet = bytes.len();
            let _ = fortschritt.send(gemeldet as u64);
        }
    }
    if bytes.len() != groesse {
        return Err("groesse".into());
    }
    let _ = fortschritt.send(groesse as u64);
    Ok(Response::new(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn laedt_nur_dateien_aus_der_liste() {
        assert!(ERLAUBT.len() > 100, "Engine und alle Modelle");
        let wasm = ERLAUBT.iter().find(|(o, _)| o.contains("translations-wasm")).expect("Engine");
        assert_eq!(*wasm.1, 4_960_506);
        let liste = r#"{"wasm":{"ort":"a/w.wasm","groesse":3},"paare":{"de-en":{"model":{"ort":"a/m.bin","groesse":9,"name":"x"}}}}"#;
        let orte = erlaubt(liste);
        assert_eq!(orte.get("a/w.wasm"), Some(&3));
        assert_eq!(orte.get("a/m.bin"), Some(&9));
        assert_eq!(orte.len(), 2);
        assert!(erlaubt("kaputt").is_empty());
    }

    #[test]
    fn eine_fremde_adresse_wird_nicht_geladen() {
        let kanal = Channel::new(|_| Ok(()));
        let ergebnis = tauri::async_runtime::block_on(sprachdaten_laden("../../evil".into(), kanal));
        assert_eq!(ergebnis.err().as_deref(), Some("unbekannt"));
    }
}
