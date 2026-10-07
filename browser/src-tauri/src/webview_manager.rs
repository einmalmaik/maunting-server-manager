use std::collections::HashMap;
use std::sync::Mutex;
use serde::{Deserialize, Serialize};
use tauri::{command, AppHandle, Emitter, State};
use uuid::Uuid;

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
}

impl Default for TabManager {
    fn default() -> Self {
        Self {
            tabs: Mutex::new(HashMap::new()),
            aktiver_tab_id: Mutex::new(None),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct TabZustand {
    pub tabs: Vec<TabItem>,
    pub aktiver_tab_id: Option<String>,
}

#[command]
pub fn tab_zustand_holen(state: State<'_, TabManager>) -> TabZustand {
    let tabs_guard = state.tabs.lock().unwrap();
    let aktiver_guard = state.aktiver_tab_id.lock().unwrap();

    let mut tab_liste: Vec<TabItem> = tabs_guard.values().cloned().collect();
    // Sortiere stabil nach ID oder Reihenfolge
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
        url: start_url,
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
        // Alle anderen Tabs inaktiv setzen
        for tab in tabs_guard.values_mut() {
            tab.ist_aktiv = false;
        }
        tabs_guard.insert(tab_id.clone(), neuer_tab.clone());

        let mut aktiver_guard = state.aktiver_tab_id.lock().unwrap();
        *aktiver_guard = Some(tab_id);
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
        tab.ist_aktiv = id == &tab_id;
    }

    let mut aktiver_guard = state.aktiver_tab_id.lock().unwrap();
    *aktiver_guard = Some(tab_id);

    let _ = app.emit("msb:tabs_geaendert", ());
    Ok(())
}

#[command]
pub fn tab_schliessen(
    app: AppHandle,
    state: State<'_, TabManager>,
    tab_id: String,
) -> Result<Option<String>, String> {
    let mut tabs_guard = state.tabs.lock().unwrap();
    tabs_guard.remove(&tab_id);

    let mut aktiver_guard = state.aktiver_tab_id.lock().unwrap();
    if *aktiver_guard == Some(tab_id) {
        // Nächsten Tab zum aktiven machen
        let naechster = tabs_guard.keys().next().cloned();
        if let Some(ref n_id) = naechster {
            if let Some(t) = tabs_guard.get_mut(n_id) {
                t.ist_aktiv = true;
            }
        }
        *aktiver_guard = naechster;
    }

    let result = aktiver_guard.clone();
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
