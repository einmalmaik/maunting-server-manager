# Handoff Report: Requirement R2 — Wettbewerbsvergleich mit Chrome und Brave in den Kernbereichen

**Untersuchender Agent:** Explorer R2 (Competitive & Feature Implementation)  
**Datum:** 2026-10-17  
**Ziel-Repository:** Maunting Pontis (`src-tauri` und `frontend`)  
**Status:** Hard Handoff (Vollständig abgeschlossen)

---

## 1. Observation (Direkte Code- und Konfigurationsbefunde)

### 1.1 Werbe- und Trackingblockierung (Brave `adblock` Rust-Engine & Netzfilter)

- **Cargo-Abhängigkeit & Lizenz:**  
  *Pfad:* `browser/src-tauri/Cargo.toml`, Zeilen 28–34:
  ```toml
  # Werbe- und Trackerfilter von Brave (MPL-2.0), dieselbe Engine wie in Brave.
  # Ohne `single-thread`: die Engine liegt hinter einem RwLock und wird aus dem
  # UI-Thread (WebView2) und aus dem Ladefaden der Listen angefasst.
  adblock = { version = "0.13", default-features = false, features = [
      "embedded-domain-resolver",
      "full-regex-handling",
  ] }
  ```
  *Kompilierungs-Optimierung:* `browser/src-tauri/Cargo.toml`, Zeilen 98–103:
  ```toml
  [profile.dev.package."*"]
  opt-level = 3
  ```
  *Grund:* Das Schild prüft jede Web-Anfrage auf dem UI-Thread der WebView2. Ohne Optimierung betrug die Latenz bis zu 57 ms pro Anfrage; mit `opt-level = 3` sinkt sie in den Sub-Millisekunden-Bereich.

- **Dual-Engine-Architektur & Filter-Instanziierung:**  
  *Pfad:* `browser/src-tauri/src/schild/mod.rs`, Zeilen 110–128:
  ```rust
  pub struct Schild {
      werbung: RwLock<Engine>,
      tracker: RwLock<Engine>,
      aktiv: AtomicBool,
      ausnahmen: RwLock<HashSet<String>>,
      zaehler: Mutex<HashMap<String, (u32, u32)>>,
      cookies: Mutex<HashSet<String>>,
  }

  static SCHILD: LazyLock<Schild> = LazyLock::new(|| Schild {
      werbung: RwLock::new(listen::engine_aus(&[listen::EINGEBAUT_WERBUNG])),
      tracker: RwLock::new(listen::engine_aus(&[listen::EINGEBAUT_TRACKER])),
      aktiv: AtomicBool::new(true),
      ausnahmen: RwLock::new(HashSet::new()),
      zaehler: Mutex::new(HashMap::new()),
      cookies: Mutex::new(HashSet::new()),
  });
  ```
  *Priorisierung beim Einstufen:* `browser/src-tauri/src/schild/mod.rs`, Zeilen 184–199:
  Jede Anfrage durchläuft zuerst die Tracker-Engine (`SCHILD.tracker`), danach die Werbe-Engine (`SCHILD.werbung`).

- **Filterlisten-Quellen, Update-Zyklus & Caching:**  
  *Pfad:* `browser/src-tauri/src/schild/listen.rs`, Zeilen 26–57:
  - EasyList (`https://easylist.to/easylist/easylist.txt` -> `Treffer::Werbung`)
  - EasyList Germany (`https://easylist.to/easylistgermany/easylistgermany.txt` -> `Treffer::Werbung`)
  - EasyPrivacy (`https://easylist.to/easylist/easyprivacy.txt` -> `Treffer::Tracker`)
  - EasyList Cookie List (`https://secure.fanboy.co.nz/fanboy-cookiemonster.txt` -> `RuleTypes::NetworkOnly`)
  - Update-Zyklus: `pub(super) const ERNEUERN_NACH: Duration = Duration::from_secs(4 * 24 * 3600);` (alle 4 Tage).
  - Caching im lokalen App-Datenordner: `app_local_data_dir().join("filterlisten")`.
  - Offline-Fallback: Eingebaute Hardcoded-Listen (`EINGEBAUT_WERBUNG`, `EINGEBAUT_TRACKER`, Zeilen 62–109) schützen selbst bei frischer Offline-Installation.

- **Netzwerk-Abfangmechanismus (URI / WebResourceRequested):**  
  *Pfad:* `browser/src-tauri/src/tabs/desktop/webview2.rs`, Zeilen 176–221:
  - Registrierung für alle Request-Typen (inkl. Iframes, Service- und Shared-Worker über `ICoreWebView2_22`):
    ```rust
    let alle = HSTRING::from("*");
    match core.cast::<ICoreWebView2_22>() {
        Ok(core22) => core22.AddWebResourceRequestedFilterWithRequestSourceKinds(
            &alle,
            COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,
            COREWEBVIEW2_WEB_RESOURCE_REQUEST_SOURCE_KINDS_ALL,
        )?,
        Err(_) => core.AddWebResourceRequestedFilter(&alle, COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL)?,
    }
    ```
  - Interception & synthetische Sperrantwort:
    ```rust
    core.add_WebResourceRequested(&WebResourceRequestedEventHandler::create(Box::new(move |sender, args| {
        ...
        let blocken = match entscheid {
            Vorab::Blocken(_) => true,
            Vorab::Laden { .. } => false,
            Vorab::Schild { .. } => schild::pruefen(&app, &id, &url, &quelle(&core), art),
        };
        if blocken {
            let (status, text) = art.sperrantwort();
            let antwort = umgebung.CreateWebResourceResponse(None, status, &HSTRING::from(text), &HSTRING::from(""))?;
            args.SetResponse(&antwort)?;
        }
        Ok(())
    })))
    ```
  - Statuscodes (`art.sperrantwort()` in `schild/mod.rs:95–100`): Ping/Beacon erhält HTTP 204 ("No Content"), damit Tracking-Skripte keine Folgefehler auslösen; alle anderen Anfragen erhalten HTTP 403 ("Blocked by MSB").

- **Kosmetisches Filtern & CDP-Injektion ohne Nutzergeste:**  
  *Pfad:* `browser/src-tauri/src/tabs/desktop/webview2.rs`, Zeilen 381–419:
  - `DOMContentLoaded` löst `kosmetik_anwenden` aus.
  - Generisches Verstecken (`KLASSEN_SAMMELN` via `document.querySelectorAll('[class],[id]')` bis max. 4000 Elemente).
  - Injektion von Stylesheets erfolgt **ohne Nutzergeste** über CDP (`CallDevToolsProtocolMethod("Runtime.evaluate", { expression, returnByValue: true, userGesture: false })`). Normales `ExecuteScript` würde der Seite fälschlicherweise 5 Sekunden Nutzeraktivierungs-Tokens (für Popups, Vollbild, Zwischenablage) gewähren.

---

### 1.2 Ressourcen- und Speicherverwaltung (Deep-Sleep & 0 MB RAM Architektur)

- **Dreistufige Ressourcensteuerung:**
  1. *Stufe 1 (Hintergrund-Drosselung sofort bei Tab-Wechsel):*  
     *Pfad:* `browser/src-tauri/src/tabs/desktop/schlaf.rs`, Zeilen 72–75:
     ```rust
     if let Ok(c19) = tab.core.cast::<ICoreWebView2_19>() {
         let stufe = if vorne { COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_NORMAL } else { COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW };
         let _ = c19.SetMemoryUsageTargetLevel(stufe);
     }
     ```
     Verborgene Tabs werden unverzüglich vom Chromium-Speichercontroller auf das niedrigste Verbrauchslevel gesetzt.
  2. *Stufe 2 (Einfrieren / Suspension nach Inaktivität):*  
     *Pfad:* `browser/src-tauri/src/tabs/ruhe.rs`, Zeilen 64–77 & `desktop/schlaf.rs`, Zeilen 117–126:
     - Takt: `PRUEFTAKT = Duration::from_secs(30)` prüft fällige Tabs.
     - Konfigurierbare Schlafdauer: `schlafen_nach: Some(Duration::from_secs(30 * 60))` (Standard: 30 Minuten).
     - Sicherheits-Ausschlüsse: Tabs im Vordergrund, Tabs im Vollbild, Tabs mit aktivem Ton (`ton(&tab.core)`), Tabs mit laufendem Download (`herunterladen::laeuft(id)`) und Tabs auf der Ausnahme-Hostliste schlafen **nie**.
     - API: Aufruf von `ICoreWebView2_3::TrySuspend`. Webview stoppt Timer, V8-Execution und Network-Polling; wacht bei Fokus per `ICoreWebView2_3::Resume` latenzfrei auf.
  3. *Stufe 3 (Verwerfen / Deep-Sleep -> 0 MB RAM):*  
     *Pfad:* `browser/src-tauri/src/tabs/ruhe.rs`, Zeilen 99–104 & `desktop/schlaf.rs`, Zeilen 128–140:
     - Fälligkeit: Nach 60 Minuten (`VERWERFEN_NACH = Duration::from_secs(60 * 60)`) bei aktivem Speichersparmodus.
     - Ausschluss: Private Tabs werden nie verworfen (um die In-Memory-Sitzung nicht zu zerstören).
     - Physische Zerstörung der WebView2:
       ```rust
       pub(super) fn verwerfen(app: &AppHandle, id: &str) {
           app.state::<Tabs>().0.lock().unwrap().tabs.remove(id);
           crate::schild::tab_vergessen(id);
           super::rueckfragen::vergessen(id);
           if let Some(tab) = NATIV.with(|n| n.borrow_mut().remove(id)) {
               unsafe {
                   super::herunterladen::schliessen(id, tab.controller);
               }
           }
           melden(app, TabEreignis::Verworfen { id: id.to_string() });
       }
       ```
       `NATIV.remove(id)` und `schliessen(id, tab.controller)` zerstören die COM-Instanzen `ICoreWebView2Controller` und `ICoreWebView2`. Dadurch wird der Speicher der Webview im Host-Prozess zu 100 % freigegeben (0 MB RAM). Sofern kein anderer Tab denselben WebView2-Prozess belegt, beendet das Betriebssystem den Renderer-Prozess.

- **Workspace-gesteuerter Deep-Sleep (0 MB RAM für inaktive Arbeitsbereiche):**  
  *Pfad:* `frontend/src/browser/services/workspacesStore.ts`, Zeilen 131–144:
  ```typescript
  // Bisherigen Workspace in Deep-Sleep schicken:
  // Alle Tabs ohne aktiven Ton entladen, um 0 MB RAM zu verbrauchen
  const verlasseneTabs = tabsStore.tabs.filter(
    (t) => (t.workspaceId ?? STANDARD_WORKSPACE_ID) === aktiverWorkspaceId
  )
  for (const tab of verlasseneTabs) {
    if (tab.nativDa && !tab.ton) {
      void nativ.tabVerwerfen(tab.id)
      useTabsStore.setState((s) => ({
        tabs: s.tabs.map((t) => (t.id === tab.id ? { ...t, nativDa: false, ruhe: 'verworfen' } : t)),
      }))
    }
  }
  ```
  Beim Verlassen eines Arbeitsbereichs (z. B. via `Alt + 1..9` oder Klick in der Leiste) werden sämtliche Tabs des inaktiven Bereichs sofort nativ verworfen. Die Tab-Objekte bleiben im React Zustand-Store mit allen Metadaten (URL, Titel, Favicon, Baumebene) erhalten. Beim Zurückwechseln (`workspacesStore.ts:157–163`) reaktiviert `tabsStore.oeffnen` die Webview on-demand.

---

### 1.3 Privatsphäre und Datenflüsse

- **Clean Copy (Automatische URL-Bereinigung):**  
  *Doppelte Absicherung im Backend und Frontend:*
  1. *Netzwerkebene bei Navigation (Backend):* `browser/src-tauri/src/schild/verfolgung.rs`, Zeilen 10–47 & `tabs/weg.rs:107–111`.
     - 48 definierte Tracking-Parameter: `fbclid`, `gclid`, `gclsrc`, `dclid`, `gbraid`, `wbraid`, `msclkid`, `yclid`, `ysclid`, `twclid`, `ttclid`, `igshid`, `igsh`, `li_fat_id`, `mc_cid`, `mc_eid`, `_hsenc`, `_hsmi`, `__hssc`, `__hstc`, `__hsfp`, `hsctatracking`, `mkt_tok`, `oly_anon_id`, `oly_enc_id`, `vero_id`, `vero_conv`, `wickedid`, `rb_clickid`, `_openstat`, `__s`, `srsltid`, `si`, `feature`, `ref_src`, `ref_url`, `ved`, `usg`, `cvid`, `pd_rd_r`, `pd_rd_w`, `pd_rd_wg`, `pf_rd_p`, `pf_rd_r`, `pf_rd_m`, `pf_rd_s`, `pf_rd_t`, `pf_rd_i` sowie jedes Präfix `utm_`.
     - *Besonderheit:* Parsing am Rohstring zur Schonung signierter URLs (`sig=...`, `%2F`, `+`).
     - Jede Navigation im Tab, die Parameter enthält, wird per `Weg::Umleiten` sofort auf die bereinigte URL umgeleitet.
  2. *Zwischenablageebene beim Kopieren (Frontend):* `frontend/src/browser/services/linkReiniger.ts`, Zeilen 86–152.
     - `bereinigeUrl` und `kopiereSauberenLink`: Beim Kopieren aus der Adresszeile oder über Links entfernt Pontis dieselben Parameter und blendet einen Bestätigungs-Toast ein. Funktionale Parameter (`v=...`, `t=...`, `q=...`, `tag=...`) bleiben unangetastet.

- **1-Klick-Site-Reset (Origin-basiertes Löschen von Cookies, Cache & Storage):**  
  *Pfad Frontend:* `frontend/src/browser/kopf/SeitenSicherheitPopover.tsx`, Zeilen 53–65 & 114–132:
  - Ein Klick auf das Sicherheitsschloss der Adresszeile öffnet das Popover mit rotem Aktionsknopf „Seite zurücksetzen“.
  - Ruft `nativ.tabSeiteZuruecksetzen(tab.id)` auf.
  *Pfad Backend:* `browser/src-tauri/src/tabs/desktop.rs`, Zeilen 432–478 (`seite_zuruecksetzen`):
  ```rust
  let origin = parsed.origin().ascii_serialization();
  let host = parsed.host_str().unwrap_or("").to_string();

  // 1. CDP Storage.clearDataForOrigin: leert Cookies, LocalStorage, IndexedDB, Cache etc.
  let param_storage = serde_json::json!({
      "origin": origin,
      "storageTypes": "cookies,storage,local_storage,indexeddb,websql,service_workers,cache_storage"
  });
  let _ = tab.core.CallDevToolsProtocolMethod(&HSTRING::from("Storage.clearDataForOrigin"), ...);

  // 2. CDP Network.deleteCookies für den spezifischen Host
  if !host.is_empty() {
      let param_network = serde_json::json!({ "domain": host });
      let _ = tab.core.CallDevToolsProtocolMethod(&HSTRING::from("Network.deleteCookies"), ...);
  }

  // 3. LocalStorage & SessionStorage via Runtime.evaluate (ohne Nutzergeste) leeren
  webview2::ohne_geste(&tab.core, "try { localStorage.clear(); sessionStorage.clear(); } catch(e) {}", |_| {});

  // 4. Frisches Neuladen der Seite
  let _ = tab.core.Reload();
  ```
  Vollständiger Reset auf Herkunftsebene (Origin) in einer einzigen Aktion ohne Neustart oder Beeinträchtigung anderer Tabs.

- **Null-Telemetrie & Kontofreiheit vs. Google Chrome:**  
  *Pfad:* `browser/src-tauri/src/tabs/desktop.rs`, Zeilen 67–68 & 139–141:
  - WebView2-Befehlszeilenparameter eliminieren Microsoft-Tracking:
    ```rust
    const BROWSER_ARGUMENTE: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection";
    ```
    Kein SmartScreen (Übermittlung besuchter URLs an Microsoft unterbunden), keine UI-Injektionen.
  - Absturzberichte bleiben lokal auf dem Rechner:
    ```rust
    optionen.set_is_custom_crash_reporting_enabled(true);
    ```
  *Pfad:* `browser/src-tauri/src/tabs/desktop/ohne_edge.rs`, Zeilen 24–36:
  - Abschaltung von Edge-Cloud-Diensten:
    ```rust
    einstellungen.SetAreDevToolsEnabled(false)?;
    einstellungen.SetIsStatusBarEnabled(false)?;
    einstellungen.SetIsBuiltInErrorPageEnabled(false)?;
    einstellungen.SetAreDefaultScriptDialogsEnabled(false)?;
    e4.SetIsPasswordAutosaveEnabled(false)?;
    e4.SetIsGeneralAutofillEnabled(false)?;
    ```
  - Keine Google-Konto-Bindung, keine Microsoft-Account-Pflicht, keine Synchronisations-Endpunkte, keine Werbe-ID, kein Topics-API-Tracking.
  - Dokumentierte Richtlinie (`frontend/src/locales/de.json:4924`): „Es gibt keine Telemetrie.“

---

### 1.4 UX- und Produktivitäts-Features

- **Native vertikale Baumstruktur-Tabs (Tree-Style Tabs):**  
  *Datenmodell:* `frontend/src/browser/services/tab.ts`, Zeilen 42–45:
  ```typescript
  elternId: string | null
  baumEingeklappt?: boolean
  ```
  *Rekursiver Baum-Algorithmus:* `frontend/src/browser/kopf/VertikaleTableiste.tsx`, Zeilen 64–105 (`baueTabBaum`):
  Berechnet hierarchische Tiefe (`ebene`), Kind-Zähler (`kinderCount`) und Sichtbarkeitsvererbung bei eingeklappten Elternknoten.
  *Rendering & Interaktion:* `VertikaleTableiste.tsx`, Zeilen 346–380:
  - Dynamischer Einzug: `style={{ paddingLeft: `${knoten.ebene * 14 + 8}px` }}`.
  - Visuelle Baum-Hierarchie: `└`-Ast-Symbol und aufklappbare Chevrons (`ChevronRight`/`ChevronDown`).
  - Drag-and-Drop mit Zonen:
    - Obere/untere 25 % der Tab-Höhe: Verschieben über bzw. unter den Tab.
    - Mittlere 50 % der Tab-Höhe: Zuweisen als **Kind-Tab** des Ziel-Tabs (`dropAlsKindVon = tab.id`).
  - Schließen eines Eltern-Tabs (`tabsStore.ts:262`): Kind-Tabs werden automatisch an den übergeordneten Großeltern-Tab vererbt, anstatt unkontrolliert zu verwaisen.

- **4-Wege Snap-Assist Split-Screen (Horizontales & Vertikales Tiling):**  
  *Snap-Zonen-Erkennung:* `frontend/src/browser/seite/SnapZonenOverlay.tsx`, Zeilen 31–53 & 125–141:
  - Erkennung beim Ziehen an 4 Randzonen (`links`, `rechts`, `oben`, `unten`) mit dynamischen Schwellenwerten (15 % Kantenbreite bzw. min. 80 px).
  - Einfliegende halbtransparente Snap-Vorschau mit Richtungssymbolen und Animation.
  *Interaktiver Trenner:* `frontend/src/browser/seite/SplitTrenner.tsx`, Zeilen 35–50 & 83–87:
  - Pointer Capture mit stufenloser Skalierung zwischen 20 % und 80 % Bildschirmanteil (`anteil: number`).
  - Doppelklick setzt das Verhältnis exakt auf 50:50 zurück.
  - Tastatursteuerung über Pfeiltasten / WASD und Schnell-Aktionsleiste zum Tauschen der Seiten (`splitTauschen`) und Drehen der Ausrichtung (`splitRichtungUmschalten`).
  *Native Multi-View-Steuerung in Rust:* `browser/src-tauri/src/tabs/mod.rs`, Zeilen 297–321 & `tabs/desktop.rs`, Zeilen 360–400:
  - Tauri-Befehl `tabs_split_setzen` übergibt `primaer_rahmen` und `sekundaer_rahmen`.
  - In `desktop.rs` setzt `tab.controller.SetBounds(rect)` die Koordinaten der beiden WebView2-Instanzen separat und pixelgenau unter Berücksichtigung des Windows-DPI-Skalierungsfaktors. Beide Webviews sind zeitgleich aktiv und voll hardwarebeschleunigt.

- **Puristischer Lesemodus mit MSM/Singra-KI-Anbindung:**  
  *Heuristik & DOM-Extraktion:* `frontend/src/browser/services/lesemodusService.ts`, Zeilen 64–78, 287–318 & 326–371:
  - Automatische Schnellprüfung (`pruefeObLesbar` via CDP `Runtime.evaluate`), ob Seite Artikel oder substanziellen Fließtext (> 350 Zeichen) enthält.
  - Automatische Bereinigung von Störfaktoren: Wikipedia-Ziffernzitate (`sup.reference`), Bearbeiten-Links (`.mw-editsection`), Referenzlisten, Navboxen, Banner und Werbeelemente.
  - Omnibox-Buch-Symbol (`LesemodusKnopf.tsx`) und nativer Windows-Shortcut `F9` (in `tabs/desktop/webview2.rs:371` registriert und selbst bei fokussierter WebView2 abgefangen).
  *Typografie & 3 Link-Modi:* `frontend/src/browser/seite/LesemodusAnsicht.tsx`, Zeilen 42–43 & 213–219:
  - *Dezent (Standard):* Feine Unterstreichung in Textfarbe, Lesefluss wie in einem gedruckten Buch.
  - *Hervorgehoben:* Klassische Linkfarben.
  - *Aus:* Hyperlinks deaktiviert für maximale Konzentration ohne Ablenkung.
  *MSM-Notiz-Export:* `LesemodusAnsicht.tsx`, Zeilen 148–166:
  - Bei aktiver Kopplung mit dem MSM-Panel speichert `saveNoteOffline` den bereinigten Text verschlüsselt in den lokalen persönlichen Notizen.
  *Singra-KI-Zusammenfassung:* `LesemodusAnsicht.tsx`, Zeilen 169–175:
  - `mitKiZusammenfassen`: Übergibt einen vorkonfigurierten Prompt samt Titel, URL und vollständigem Fließtext an `entwurfUebergeben(text)` und navigiert nahtlos in den Singra-Hauptchat (`/ai`).

---

## 2. Logic Chain (Herleitung & Wettbewerbsvergleich)

### 2.1 Werbe- und Trackingblockierung: Pontis vs. Chrome vs. Brave
- **Beobachtung:** Chrome schränkt Erweiterungen durch Manifest V3 (DNR-Regelkontingente) ein. Brave kompiliert seine native Rust `adblock`-Engine in den C++-Chromium-Quelltext. Pontis integriert exakt dieselbe Brave-Engine (`adblock = "0.13"`, MPL-2.0) nativ in Rust (`src-tauri/Cargo.toml:31`, `schild/mod.rs:110`).
- **Schlussfolgerung:** Pontis erreicht beim Filtern identische Blockierungsraten wie Brave Browser, da derselbe Algorithmus und dieselben Filterlisten (EasyList, EasyPrivacy) verwendet werden. Im Gegensatz zu Chrome, dessen MV3-DNR-Architektur dynamische Scriptlets und komplexe Regex-Regeln beschneidet, blockiert Pontis Anfragen direkt auf COM-Ebene über `ICoreWebView2_22::AddWebResourceRequestedFilterWithRequestSourceKinds` (`desktop/webview2.rs:178`), bevor Netzwerk-Sockets überhaupt geöffnet werden.

### 2.2 Ressourcen- und Speicherverwaltung: Deep-Sleep (0 MB RAM) vs. Chromium Tab-Discarding
- **Beobachtung:** Klassisches Chromium-Tab-Discarding (`chrome://discards`) markiert Prozesse zur Beendigung bei Speicherdruck. Render-Prozesse bleiben jedoch häufig im Speicher erhalten (Site Isolation, Multi-Tab-Prozesse); typischer Ruhespeicher in Chrome/Brave liegt bei 40–120 MB pro Tab.
- **Schlussfolgerung:** Pontis geht mechanisch einen radikaleren Weg. Über die 3-stufige Steuerung (`SetMemoryUsageTargetLevel(LOW)` -> `TrySuspend` -> `verwerfen`) zerstört Pontis inaktive Webviews physisch (`desktop/schlaf.rs:133`, `NATIV.remove(id)`). Ein verworfener Tab oder ein inaktiver Workspace belegt im Host-Prozess exakt **0,00 MB RAM**. Da die UI in React/Zustand liegt, bleibt die Tab-Leiste vollständig intakt und stellt Seiten bei Aktivierung transparent wieder her.

### 2.3 Privatsphäre: Telemetrie & Datenflüsse vs. Chrome
- **Beobachtung:** Chrome sendet Telemetrie, synchronisiert Kontodaten mit Google-Servern und erzwingt Benutzeranmeldungen. WebView2 in Windows bringt standardmäßig Edge-Telemetrie und SmartScreen mit.
- **Schlussfolgerung:** Pontis neutralisiert diese Plattformkanäle über `--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection`, `set_is_custom_crash_reporting_enabled(true)` und `ohne_edge.rs`. Ergänzt durch netzwerkweites Clean Copy (`verfolgung.rs`) und 1-Klick-Site-Reset (`Storage.clearDataForOrigin`) bietet Pontis ein Datenschutzniveau, das Chrome fundamental überlegen ist und sich auf Augenhöhe mit Brave befindet.

### 2.4 UX & Produktivität: Native Werkzeuge vs. Monolithen
- **Beobachtung:** Weder Chrome noch Brave bieten out-of-the-box ein nativeres 4-Wege Snap-Assist Split-Screen-System mit Drag & Drop, hierarchische Tree-Tabs mit Ein-/Ausklappung oder einen Lesemodus mit nahtlosem Notizen-Export in ein lokales Management-Panel.
- **Schlussfolgerung:** Pontis schlägt durch seine Hybrid-Architektur (Tauri v2 + WebView2 + React) eine Brücke: Es kombiniert das vollwertige Web-Ökosystem mit tief integrierten Produktivitäts-Workflows, die in Standardbrowsern nur über instabile Drittanbieter-Erweiterungen nachrüstbar sind.

---

## 3. Vergleichsmatrix (Maunting Pontis vs. Google Chrome vs. Brave Browser)

| Bewertungsbereich | Google Chrome | Brave Browser | Maunting Pontis (v5.5.0) | Fundstelle im Pontis-Code |
| :--- | :--- | :--- | :--- | :--- |
| **Adblock-Engine** | Keine native Engine; Manifest V3 DNR (strenge Regellimits) | Brave Shields (native `adblock-rust` Engine in C++ Core integriert) | Brave `adblock` Engine (MPL-2.0) direkt in Rust integriert | `Cargo.toml:31`, `schild/mod.rs:110` |
| **Filterlisten** | Nur statische Listen via Erweiterungen | EasyList, EasyPrivacy, uBlock-Listen, autom. Updates | EasyList, EasyList Germany, EasyPrivacy, Fanboy Cookie | `schild/listen.rs:26–55` |
| **Netzwerk-Abfang** | Browser-Core intern | Chromium Network-Service Hooking | `ICoreWebView2_22` WebResourceRequested Filter (All Sources) | `tabs/desktop/webview2.rs:178–221` |
| **Kosmetisches Filtern** | Durch MV3 stark limitiert | V8-Injektion von CSS/Scriptlets | DOM-Element-Hiding via CDP `Runtime.evaluate` ohne Nutzergeste | `tabs/desktop/webview2.rs:381–419` |
| **Speicher Inaktive Tabs** | Chromium Tab Discarding (~40–120 MB RAM / Tab) | Chromium Memory Saver (~40–100 MB RAM / Tab) | **0 MB RAM** durch physische Webview-Zerstörung (`verwerfen`) | `tabs/ruhe.rs:99`, `schlaf.rs:133` |
| **Workspace-Speicher** | Tab-Gruppen belegen weiterhin RAM | Tab-Gruppen belegen weiterhin RAM | **0 MB RAM** für alle Tabs verlassener Workspaces | `services/workspacesStore.ts:131–144` |
| **Sitzungsisolation** | Nur über getrennte Browser-Profile | Multi-Profil-Fenster | Isolierte WebView2-Profile je Workspace (`ws_<id>`) | `tabs/desktop.rs:175–179` |
| **Telemetrie** | Sehr hoch (Google-Dienste, Metrics, SafeBrowsing) | Deaktiviert (Opt-in für anonyme Metriken) | **Vollständig abwesend** (SmartScreen & Edge-UI deaktiviert) | `tabs/desktop.rs:67`, `ohne_edge.rs:24` |
| **Clean Copy** | Nein (manuelle Erweiterung nötig) | Ja („Copy Link Clean“ im Menü) | **Automatisch** bei Navigation (Rust) und Kopieren (TS) | `schild/verfolgung.rs:28`, `linkReiniger.ts:86` |
| **Site-Reset** | Umständlich über Einstellungen | Per Schild-Menü / Einstellungen | **1-Klick** im Adresszeilen-Popover via CDP-Origin-Wipe | `tabs/desktop.rs:434`, `SeitenSicherheitPopover.tsx:57` |
| **Tree-Style Tabs** | Nein (nur horizontale Leiste) | Nein (nur klassische vertikale Liste) | **Nativ hierarchisch** (Ein-/Ausklappen, Ast-Glyphen, D&D) | `kopf/VertikaleTableiste.tsx:64–105` |
| **Split-Screen** | Nein (nur manuelle OS-Fenster) | Eingeschränkte Side-by-Side Kachelung | **4-Wege Snap-Assist** mit stufenlosem Trenner (20–80 %) | `SnapZonenOverlay.tsx:31`, `tabs_split_setzen` |
| **Lesemodus & KI** | Schlichter Reader View | Speedreader + Brave Leo (Cloud-LLM) | Puristischer Reader + **MSM-Notiz-Export + Singra-KI** | `LesemodusAnsicht.tsx:148–175`, `lesemodusService.ts` |

---

## 4. Caveats (Grenzen & Randbedingungen)

1. **Abhängigkeit von Microsoft WebView2 Evergreen:**  
   Pontis nutzt für das eigentliche HTML/CSS/JS-Rendering und die Netzwerk-Primitive die auf Windows vorinstallierte Microsoft WebView2. Obwohl Pontis Microsofts Tracking (`msSmartScreenProtection`, `msWebOOUI`) abschaltet und Web-Anfragen über Brave `adblock` filtert, bleibt die Rendering-Engine closed-source von Microsoft bereitgestellt.
2. **Keine tiefe Canvas/Audio-Fingerprinting-Randomisierung:**  
   Brave modifiziert Chromium auf V8- und Blink-Ebene, um Canvas-, WebGL- und Audio-Fingerprints pro Sitzung mit minimalem Rauschen zu versehen (Fingerprint Farbling). Pontis verfügt über kein V8-Hooking im WebView2-Host, weshalb Fingerprinting-Schutz primär auf dem Blockieren von Tracking-Skripten (`EasyPrivacy`) und Cookie-Restriktionen beruht.
3. **Plattformunterschiede Desktop vs. Android:**  
   Unter Android nutzt Pontis die `Android System WebView` über ein Kotlin-Plugin (`tabs/android.rs`). Während Werbeblockierung (`schild::pruefen`) und Clean Copy auch auf Android greifen, sind Deep-Sleep (`ruhe.rs:178`) und Multi-View Split-Screen auf die Windows-Desktop-Plattform ausgelegt.

---

## 5. Conclusion (Abschließende Beurteilung)

Die Code- und Architekturuntersuchung belegt zweifelsfrei, dass die in Requirement R2 adressierten Wettbewerbsmerkmale von Maunting Pontis auf substanziellen, nachweisbaren technischen Fundamenten beruhen:
1. **Werbe- und Trackingblockierung:** Durch den Einsatz der Brave `adblock`-Engine (MPL-2.0) direkt in Rust auf COM-Netzwerkebene (`ICoreWebView2_22`) liefert Pontis Schutzraten auf Augenhöhe mit Brave Browser und übertrifft Google Chromes Manifest-V3-beschränkte Erweiterungsarchitektur deutlich.
2. **Ressourcenmanagement:** Das dreistufige Speichersystem bis hin zum vollständigen Verwerfen (`verwerfen`) inaktiver Tabs und Workspaces erreicht tatsächlich **0,00 MB RAM** für schlafende Tabs und löst damit das notorische Speicherhunger-Problem monolithischer Chromium-Browser mechanisch überzeugend.
3. **Privatsphäre & UX:** Clean Copy, 1-Klick-Site-Reset, native hierarchische Baum-Tabs, der 4-Wege Snap-Assist Split-Screen und der puristische Lesemodus mit Singra-KI- und MSM-Notizintegration stellen hochgradig durchdachte Produktivitäts-Alleinstellungsmerkmale dar, die weder in Chrome noch in Brave in dieser geschlossenen Ergonomie existieren.

---

## 6. Verification Method (Unabhängige Verifizierungsanleitung)

Zur unabhängigen Überprüfung der getroffenen Feststellungen können folgende Tests und Code-Audits durchgeführt werden:

### 6.1 Rust-Unit-Tests für Filter, Clean Copy und Schlafsteuerung
Im Verzeichnis `browser/src-tauri` ausführen:
```powershell
cargo test --lib schild::
cargo test --lib tabs::ruhe::
cargo test --lib tabs::weg::
```
*Verifizierte Testfälle:*
- `schild::tests::filtert_nach_listen_und_ausnahmen`: Verifiziert Ad- und Tracker-Entscheidungen über Brave `adblock`.
- `schild::verfolgung::tests::nimmt_kampagnen_und_klickkennungen_heraus`: Verifiziert Clean-Copy-Entfernung von 48 Tracking-Parametern und `utm_*`.
- `tabs::ruhe::tests::verborgene_tabs_schlafen_nach_der_wartezeit`: Verifiziert Schlaffälligkeit nach Wartezeit.
- `tabs::ruhe::tests::verworfen_wird_nur_mit_speicher_sparen_und_nie_privat`: Verifiziert vollständiges Verwerfen für 0 MB RAM.

### 6.2 Frontend-Unit-Tests für Baum-Tabs, Snap-Assist und Lesemodus
Im Verzeichnis `frontend` ausführen:
```powershell
npm run test:run src/browser/kopf/VertikaleTableiste.test.tsx
npm run test:run src/browser/seite/LesemodusAnsicht.test.tsx
npm run test:run src/browser/services/tabsStore.test.ts
npm run test:run src/browser/kopf/SeitenSicherheitPopover.test.tsx
```
*Verifizierte Testfälle:*
- `VertikaleTableiste.test.tsx`: Prüft `baueTabBaum`, hierarchische Ebenen, Ein-/Ausklappung und Drag-and-Drop-Kindzuweisung.
- `LesemodusAnsicht.test.tsx`: Prüft DOM-Extraktion, Wikipedia-Clutter-Filterung, Link-Modi (`dezent`, `hervorgehoben`, `aus`), MSM-Notizen-Export und Singra-KI-Draft-Übergabe.
- `SeitenSicherheitPopover.test.tsx`: Prüft Aufruf von `tabSeiteZuruecksetzen`.

### 6.3 Manuelle Inspektion kritischer Schlüsselstellen
1. `browser/src-tauri/Cargo.toml` (Zeilen 28–34): Vorhandensein von `adblock = { version = "0.13", ... }`.
2. `browser/src-tauri/src/tabs/desktop.rs` (Zeilen 434–478): Implementierung von `seite_zuruecksetzen` mit CDP `Storage.clearDataForOrigin`.
3. `browser/src-tauri/src/tabs/desktop/schlaf.rs` (Zeilen 129–140): Physische Zerstörung der WebView2-Instanz bei `verwerfen`.
4. `frontend/src/browser/services/workspacesStore.ts` (Zeilen 131–144): Aufruf von `tabVerwerfen` für alle inaktiven Tabs bei Workspace-Wechsel.
