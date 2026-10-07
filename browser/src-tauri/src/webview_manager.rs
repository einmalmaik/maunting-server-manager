use std::collections::HashMap;
use std::sync::Mutex;
use serde::{Deserialize, Serialize};
use tauri::{
    command, AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, State, WebviewBuilder,
    WebviewUrl, Wry,
};
use uuid::Uuid;

use crate::adblock::{kosmetisches_adblock_script, pruefe_url_block, BlockArt};
use crate::autofill::autofill_beobachter_script;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TabItem {
    pub id: String,
    pub url: String,
    pub title: String,
    pub favicon: Option<String>,
    pub ist_aktiv: bool,
    pub ist_lade_vorgang: bool,
    pub kann_zurueck: bool,
    pub kann_vorwaerts: bool,
    pub ist_angeheftet: bool,
    pub ist_inkognito: bool,
}

pub struct TabManager {
    pub tabs: Mutex<HashMap<String, TabItem>>,
    pub aktiver_tab_id: Mutex<Option<String>>,
    pub bounds: Mutex<(f64, f64, f64, f64)>, // (x, y, width, height)
}

impl Default for TabManager {
    fn default() -> Self {
        Self {
            tabs: Mutex::new(HashMap::new()),
            aktiver_tab_id: Mutex::new(None),
            bounds: Mutex::new((48.0, 76.0, 1200.0, 720.0)),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct TabZustand {
    pub tabs: Vec<TabItem>,
    pub aktiver_tab_id: Option<String>,
}

fn erstelle_oder_zeige_child_webview(
    app: &AppHandle,
    state: &TabManager,
    tab_id: &str,
    ziel_url: &str,
) -> Result<(), String> {
    let main_window = app.get_window("main").ok_or("Hauptfenster nicht gefunden")?;
    let (x, y, width, height) = *state.bounds.lock().unwrap();

    let is_incognito = {
        let tabs_guard = state.tabs.lock().unwrap();
        tabs_guard.get(tab_id).map(|t| t.ist_inkognito).unwrap_or(false)
    };

    let webview_label = tab_id;

    if let Some(wv) = app.get_webview(webview_label) {
        let _ = wv.set_position(LogicalPosition::new(x, y));
        let _ = wv.set_size(LogicalSize::new(width, height));
        if ziel_url != "about:blank" {
            if let Ok(parsed) = ziel_url.parse() {
                let _ = wv.navigate(parsed);
            }
            let _ = wv.show();
            let _ = wv.set_focus();
        } else {
            let _ = wv.hide();
        }
        return Ok(());
    }

    if ziel_url == "about:blank" {
        return Ok(());
    }

    let parsed_url: tauri::Url = ziel_url.parse().map_err(|e| format!("Ungültige URL: {}", e))?;

    let adblock_code = kosmetisches_adblock_script();
    let autofill_code = autofill_beobachter_script();
    let combined_init = format!("{}\n{}", adblock_code, autofill_code);

    let app_handle_nav = app.clone();
    let tab_id_nav = tab_id.to_string();

    let app_handle_load = app.clone();
    let tab_id_load = tab_id.to_string();

    let mut builder = WebviewBuilder::<Wry>::new(webview_label, WebviewUrl::External(parsed_url))
        .initialization_script(&combined_init)
        .on_page_load(move |_wv: tauri::Webview, payload: tauri::webview::PageLoadPayload<'_>| {
            let url_str = payload.url().as_str();
            match payload.event() {
                tauri::webview::PageLoadEvent::Started => {
                    let _ = app_handle_load.emit(
                        "msb:tab_laedt",
                        serde_json::json!({
                            "id": tab_id_load,
                            "url": url_str,
                        }),
                    );
                }
                tauri::webview::PageLoadEvent::Finished => {
                    let _ = app_handle_load.emit(
                        "msb:tab_geladen",
                        serde_json::json!({
                            "id": tab_id_load,
                            "url": url_str,
                        }),
                    );
                }
            }
        })
        .on_navigation(move |url: &tauri::Url| {
            let url_str = url.as_str();
            match pruefe_url_block(url_str) {
                BlockArt::Werbung => {
                    let _ = crate::adblock::adblock_zaehler_erhoehen("werbung".to_string());
                    let _ = app_handle_nav.emit(
                        "msb:adblock_ereignis",
                        serde_json::json!({
                            "typ": "werbung",
                            "url": url_str,
                            "tab_id": tab_id_nav,
                        }),
                    );
                    false
                }
                BlockArt::Tracker => {
                    let _ = crate::adblock::adblock_zaehler_erhoehen("tracker".to_string());
                    let _ = app_handle_nav.emit(
                        "msb:adblock_ereignis",
                        serde_json::json!({
                            "typ": "tracker",
                            "url": url_str,
                            "tab_id": tab_id_nav,
                        }),
                    );
                    false
                }
                BlockArt::Erlaubt => {
                    let _ = app_handle_nav.emit(
                        "msb:tab_navigiert",
                        serde_json::json!({
                            "id": tab_id_nav,
                            "url": url_str,
                        }),
                    );
                    true
                }
            }
        });

    if is_incognito {
        builder = builder.incognito(true);
    }

    let wv = main_window
        .add_child(
            builder,
            LogicalPosition::new(x, y),
            LogicalSize::new(width, height),
        )
        .map_err(|e| format!("Fehler beim Hinzufügen der Webview: {}", e))?;

    let _ = wv.show();
    let _ = wv.set_focus();
    Ok(())
}

#[command]
pub fn tab_zustand_holen(state: State<'_, TabManager>) -> TabZustand {
    let tabs_guard = state.tabs.lock().unwrap();
    let aktiver_guard = state.aktiver_tab_id.lock().unwrap();

    let mut tab_liste: Vec<TabItem> = tabs_guard.values().cloned().collect();
    tab_liste.sort_by(|a, b| a.id.cmp(&b.id));

    TabZustand {
        tabs: tab_liste,
        aktiver_tab_id: aktiver_guard.clone(),
    }
}

#[command]
pub fn tab_erstellen(
    app: AppHandle,
    state: State<'_, TabManager>,
    url: Option<String>,
    inkognito: bool,
) -> Result<TabItem, String> {
    let tab_id = format!("tab-{}", Uuid::new_v4().to_string().chars().take(8).collect::<String>());
    let start_url = url.unwrap_or_else(|| "about:blank".to_string());
    let start_titel = if start_url == "about:blank" {
        "Neuer Tab".to_string()
    } else {
        start_url.clone()
    };

    let neuer_tab = TabItem {
        id: tab_id.clone(),
        url: start_url.clone(),
        title: start_titel,
        favicon: None,
        ist_aktiv: true,
        ist_lade_vorgang: false,
        kann_zurueck: false,
        kann_vorwaerts: false,
        ist_angeheftet: false,
        ist_inkognito: inkognito,
    };

    {
        let mut tabs_guard = state.tabs.lock().unwrap();
        for (id, tab) in tabs_guard.iter_mut() {
            tab.ist_aktiv = false;
            if let Some(other_wv) = app.get_webview(id) {
                let _ = other_wv.hide();
            }
        }
        tabs_guard.insert(tab_id.clone(), neuer_tab.clone());

        let mut aktiver_guard = state.aktiver_tab_id.lock().unwrap();
        *aktiver_guard = Some(tab_id.clone());
    }

    if start_url != "about:blank" {
        let _ = erstelle_oder_zeige_child_webview(&app, &state, &tab_id, &start_url);
    }

    let _ = app.emit("msb:tabs_geaendert", ());
    Ok(neuer_tab)
}

#[command]
pub fn tab_aktivieren(
    app: AppHandle,
    state: State<'_, TabManager>,
    tab_id: String,
) -> Result<(), String> {
    let mut tabs_guard = state.tabs.lock().unwrap();
    if !tabs_guard.contains_key(&tab_id) {
        return Err("Tab existiert nicht".to_string());
    }

    for (id, tab) in tabs_guard.iter_mut() {
        let ist_dieser = id == &tab_id;
        tab.ist_aktiv = ist_dieser;
        if !ist_dieser {
            if let Some(other_wv) = app.get_webview(id) {
                let _ = other_wv.hide();
            }
        }
    }

    let mut aktiver_guard = state.aktiver_tab_id.lock().unwrap();
    *aktiver_guard = Some(tab_id.clone());

    let target_tab = tabs_guard.get(&tab_id).cloned();
    drop(tabs_guard);
    drop(aktiver_guard);

    if let Some(tab) = target_tab {
        if tab.url != "about:blank" {
            let _ = erstelle_oder_zeige_child_webview(&app, &state, &tab.id, &tab.url);
        } else if let Some(wv) = app.get_webview(&tab.id) {
            let _ = wv.hide();
        }
    }

    let _ = app.emit("msb:tabs_geaendert", ());
    Ok(())
}

#[command]
pub fn tab_schliessen(
    app: AppHandle,
    state: State<'_, TabManager>,
    tab_id: String,
) -> Result<Option<String>, String> {
    if let Some(wv) = app.get_webview(&tab_id) {
        let _ = wv.close();
    }

    let mut tabs_guard = state.tabs.lock().unwrap();
    tabs_guard.remove(&tab_id);

    let mut aktiver_guard = state.aktiver_tab_id.lock().unwrap();
    if *aktiver_guard == Some(tab_id) {
        let naechster = tabs_guard.keys().next().cloned();
        if let Some(ref n_id) = naechster {
            if let Some(t) = tabs_guard.get_mut(n_id) {
                t.ist_aktiv = true;
            }
        }
        *aktiver_guard = naechster;
    }

    let result = aktiver_guard.clone();
    drop(tabs_guard);
    drop(aktiver_guard);

    if let Some(ref n_id) = result {
        let _ = tab_aktivieren(app.clone(), state, n_id.clone());
    }

    let _ = app.emit("msb:tabs_geaendert", ());
    Ok(result)
}

#[command]
pub fn tab_aktualisieren(
    app: AppHandle,
    state: State<'_, TabManager>,
    tab_id: String,
    url: Option<String>,
    titel: Option<String>,
    ist_lade_vorgang: Option<bool>,
    kann_zurueck: Option<bool>,
    kann_vorwaerts: Option<bool>,
) -> Result<(), String> {
    let mut tabs_guard = state.tabs.lock().unwrap();
    if let Some(tab) = tabs_guard.get_mut(&tab_id) {
        if let Some(u) = url {
            tab.url = u;
        }
        if let Some(t) = titel {
            tab.title = t;
        }
        if let Some(l) = ist_lade_vorgang {
            tab.ist_lade_vorgang = l;
        }
        if let Some(z) = kann_zurueck {
            tab.kann_zurueck = z;
        }
        if let Some(v) = kann_vorwaerts {
            tab.kann_vorwaerts = v;
        }
    }
    let _ = app.emit("msb:tabs_geaendert", ());
    Ok(())
}

#[command]
pub fn tab_navigieren(
    app: AppHandle,
    state: State<'_, TabManager>,
    tab_id: String,
    url: String,
) -> Result<(), String> {
    let bereinigte_url = if url == "about:blank" || url.starts_with("about:") {
        url
    } else if url.starts_with("http://") || url.starts_with("https://") {
        url
    } else {
        format!("https://{}", url)
    };

    {
        let mut tabs_guard = state.tabs.lock().unwrap();
        for (id, t) in tabs_guard.iter_mut() {
            t.ist_aktiv = id == &tab_id;
            if id != &tab_id {
                if let Some(other_wv) = app.get_webview(id) {
                    let _ = other_wv.hide();
                }
            }
        }
        let tab = tabs_guard.entry(tab_id.clone()).or_insert_with(|| TabItem {
            id: tab_id.clone(),
            url: bereinigte_url.clone(),
            title: if bereinigte_url == "about:blank" {
                "Neuer Tab".to_string()
            } else {
                bereinigte_url.clone()
            },
            favicon: None,
            ist_aktiv: true,
            ist_lade_vorgang: bereinigte_url != "about:blank",
            kann_zurueck: false,
            kann_vorwaerts: false,
            ist_angeheftet: false,
            ist_inkognito: false,
        });
        tab.url = bereinigte_url.clone();
        tab.title = if bereinigte_url == "about:blank" {
            "Neuer Tab".to_string()
        } else {
            bereinigte_url.clone()
        };
        tab.ist_lade_vorgang = bereinigte_url != "about:blank";
        tab.kann_zurueck = true;

        let mut aktiver_guard = state.aktiver_tab_id.lock().unwrap();
        *aktiver_guard = Some(tab_id.clone());
    }

    erstelle_oder_zeige_child_webview(&app, &state, &tab_id, &bereinigte_url)?;
    let _ = app.emit(
        "msb:tab_navigiert",
        serde_json::json!({
            "id": tab_id,
            "url": bereinigte_url,
        }),
    );
    let _ = app.emit("msb:tabs_geaendert", ());
    Ok(())
}

#[command]
pub fn tab_zurueck(app: AppHandle, tab_id: String) -> Result<(), String> {
    if let Some(wv) = app.get_webview(&tab_id) {
        let _ = wv.eval("window.history.back()");
    }
    Ok(())
}

#[command]
pub fn tab_vorwaerts(app: AppHandle, tab_id: String) -> Result<(), String> {
    if let Some(wv) = app.get_webview(&tab_id) {
        let _ = wv.eval("window.history.forward()");
    }
    Ok(())
}

#[command]
pub fn tab_neu_laden(app: AppHandle, tab_id: String) -> Result<(), String> {
    if let Some(wv) = app.get_webview(&tab_id) {
        let _ = wv.eval("window.location.reload()");
    }
    Ok(())
}

#[command]
pub fn tab_bounds_anpassen(
    app: AppHandle,
    state: State<'_, TabManager>,
    x: f64,
    y: f64,
    breite: f64,
    hoehe: f64,
) -> Result<(), String> {
    {
        let mut bounds_guard = state.bounds.lock().unwrap();
        *bounds_guard = (x, y, breite, hoehe);
    }

    let aktiver_id = {
        state.aktiver_tab_id.lock().unwrap().clone()
    };

    if let Some(id) = aktiver_id {
        if let Some(wv) = app.get_webview(&id) {
            let _ = wv.set_position(LogicalPosition::new(x, y));
            let _ = wv.set_size(LogicalSize::new(breite, hoehe));
        }
    }
    Ok(())
}

#[command]
pub fn tab_sichtbarkeit_setzen(
    app: AppHandle,
    state: State<'_, TabManager>,
    sichtbar: bool,
) -> Result<(), String> {
    let aktiver_id = {
        state.aktiver_tab_id.lock().unwrap().clone()
    };

    if let Some(id) = aktiver_id {
        if let Some(wv) = app.get_webview(&id) {
            if sichtbar {
                let tabs_guard = state.tabs.lock().unwrap();
                if let Some(tab) = tabs_guard.get(&id) {
                    if tab.url != "about:blank" {
                        let _ = wv.show();
                    }
                }
            } else {
                let _ = wv.hide();
            }
        }
    }
    Ok(())
}

#[command]
pub fn hauptfenster_fokussieren(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_window("main") {
        let _ = win.set_focus();
    }
    Ok(())
}

#[command]
pub fn fenster_schliessen(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_window("main") {
        let _ = win.close();
    }
    Ok(())
}

#[command]
pub fn fenster_minimieren(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_window("main") {
        let _ = win.minimize();
    }
    Ok(())
}

#[command]
pub fn fenster_maximieren_umschalten(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_window("main") {
        let is_max = win.is_maximized().unwrap_or(false);
        if is_max {
            let _ = win.unmaximize();
        } else {
            let _ = win.maximize();
        }
    }
    Ok(())
}
