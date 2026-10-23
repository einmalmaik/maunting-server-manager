# Maunting Search (die Suchseite von Maunting Pontis)

> Schreibweise: **Maunting** — immer mit „au“. Nie „Mounting“. Im Deutschen heißt
> die Oberfläche *Maunting Search* bzw. *Maunting Suche*, nie „MSM-Suche“ und
> nie mit einem Hinweis auf SearXNG. Die SearXNG-Herkunft steht ausschließlich in
> der Dokumentation und in der Datenschutzerklärung.

## Was es ist

Maunting Search ist die Suchseite von Maunting Pontis
(`pontis://suche?q=…`). Sie ist ein eigener Dienst des Browsers, kein eingebettetes
Fenster auf eine fremde Suchmaschine: Layout, Suchleiste, Kategorien, Dropdowns,
Kacheln und Fußzeile sind Bausteine der MSM-Design-DNA.

Zwei Hälften greifen ineinander:

| Hälfte | Zuständig | Datei |
|---|---|---|
| Treffer, Vorschläge, Wissens-Panel, Autocomplete, Medien-Proxy | Backend (`routers/browser_suche.py`) | `backend/routers/browser_suche.py` |
| KI-Zusammenfassung, selbständige Weiterrecherche, Folgefragen | `ai_search_summary_service.py` | `frontend/src/browser/seite/suchzusammenfassung/` |

Die Seite selbst liegt in `frontend/src/browser/seite/MsmSuche.tsx`. Der Name der
Datei ist historisch; die Oberfläche zeigt „Maunting Search“.

## Seitenstruktur

Die Seite folgt einem festen Aufbau, der auf Desktop und Handy gleich bleibt:

```
┌──────────────────────────────────────────────────────────────┐
│ Kopfzeile (fixiert)                                          │
│   MSB-Logo · Maunting Search · DNA-Suchleiste · Region/Sprache │
│   Kategorie-Tabs: Web · Bilder · Videos · News   + Statistik │
├──────────────────────────────────────────────────────────────┤
│ Links (7/12)                      │ Rechts (5/12)           │
│  1. KI-Überblick (Singra)         │  Wikipedia-/            │
│     · Kopieren · Quellen n        │  Wissens-Panel         │
│     · Quellen-Karten (3 Stück)    │  · Artikelbild         │
│     · Folgefragen-Eingabe         │  · Steckbrief          │
│  2. Weitere Fragen zum Thema      │  · „Dazu suchen …"     │
│  3. Organische Treffer            │                        │
│  4. Ähnliche Suchanfragen         │                        │
│  5. Seitennavigation              │                        │
├──────────────────────────────────────────────────────────────┤
│ Fußzeile: Maunting Search · AGPLv3-Hinweis                   │
└──────────────────────────────────────────────────────────────┘
```

Am Handy wird das Raster einspaltig: Das Wissens-Panel reiht sich einklappbar
unter der KI-Box ein, Snippets werden auf zwei Zeilen gekürzt, Tabs und
Quellen-Karten lassen sich seitlich wischen. Es gibt nie waagerechtes Scrollen.

## Datenfluss

```text
Browser: pontis://suche?q=…
  │  POST /api/browser/suche          { q, seite, sicher, sprache, kategorie }
  │  POST /api/browser/suche/autocomplete
  │  POST /api/browser/suche/wissen
  │  GET  /api/browser/suche/bild?u=…
  ▼
Backend (eigener Server)
  ├── SearXNG-Instanz des Servers   → Treffer, Vorschläge, Infobox
  ├── Wikipedia (REST)             → Zusammenfassung, Bild, Infobox-Felder
  ├── /suche/bild (Proxy)          → nur freigegebene Bildadressen
  └── /suche/zusammenfassung (SSE) → KI-Zusammenfassung
```

Alle Begriffe gehen per **POST**, damit sie in keinem Zugriffslog stehen. Das
Backend protokolliert keinen Suchbegriff. Die Grenzen je Konto stehen in
`backend/routers/browser_suche.py` (60 Anfragen je Minute, davon getrennt
Autocomplete, Wissen, Bilder und Zusammenfassung).

### Warum der Medien-Proxy existiert

Eine Trefferseite darf keine fremden Adressen nachladen: Sonst sieht jede
gefundene Seite die IP-Adresse jedes Nutzers. Gleichzeitig sollen Vorschauen und
Wikipedia-Bilder erscheinen. Deshalb:

1. Die Suche (oder das Wissens-Panel) liefert Adressen von Bildern.
2. Das Backend merkt sie sich für 15 Minuten (`_merke_bild_url`) — sonst nichts.
3. `GET /api/browser/suche/bild?u=…` lädt eine solche Adresse **nur**, wenn sie
   freigegeben ist, öffentlich ist und wirklich ein PNG/JPEG/WebP/GIF ist
   (Magic-Bytes-Prüfung, höchstens 2 MB).
4. Lädt ein Bild nicht, verschwindet der Bildcontainer — oder zeigt eine
   DNA-Kachel. Ein kaputtes Bildsymbol gibt es nie.

Favicons nehmen einen anderen Weg: Rust holt sie bei der Seite selbst, dekodiert
sie um und gibt ein PNG zurück (`frontend/src/browser/seite/start/KachelSymbol.tsx`
→ `browser/src-tauri/src/kachelsymbol.rs`). Der Browser spricht nie mit einer
Favicon-API.

## Kategorien

| Kategorie | Parameter | Was die Seite zeigt |
|---|---|---|
| Web | (keiner) | KI-Überblick, Fragen, Treffer, Verwandtes, Paginierung |
| Bilder | `categories: images` | Raster mit Vorschau über den Medien-Proxy |
| Videos | `categories: videos` | Karten mit Wiedergabe-Symbol und Domain |
| News | `categories: news` | Chronologische Meldungen, lokal gefiltert |

Kategorien brauchen **keine** eigene Freigabe in der Instanz: Die Tabs schicken
`categories=…` als Suchparameter, SearXNG filtert danach selbst. Leere Tabs
bedeuten, dass keine Engine der Kategorie gerade etwas liefert. Was MAUNTING
SEARCH tun kann: Zusätzliche Engine-Gruppen (z. B. wissenschaftliche Suche)
über `engines:` in `settings.yml` definieren und in der Tabelle oben
nachtragen — dazu mehr unter „Instanz anpassen“.

Die Suche bleibt immer auf dem eigenen Server. „Mit DuckDuckGo suchen“ gibt es
nur als Rückfall, wenn der eigene Suchdienst nicht eingerichtet ist (`503`).

## Wissens-Panel

Reihenfolge der Quellen:

1. **SearXNG-Infobox.** Liefert die Instanz ein `infoboxes`-Element, enthält es
   bereits Zusammenfassung, Artikelbild und strukturierte Felder (Gründung, CEO,
   Hauptsitz …). Der Titel wird aus der Adresse des Eintrags gelesen — im Feld
   `infobox` nennt SearXNG nur die Herkunft.
2. **Wikipedia-Fallback.** Gibt es keine Infobox, fragt das Backend die
   Wikipedia-REST-API der eingestellten Sprache und liest zusätzlich den
   Wikitext der Seite, um die Felder der Infobox-Vorlage zu lesen.

Der Fallback ist bewusst konservativ: Nur Felder, deren Wert ohne verschachtelte
Vorlage lesbar ist, kommen durch. `Gründung = 14. März 2002` erscheint,
`Umsatz = {{USD|4.6e9}}` fällt weg. Ein Wert, den das Panel nicht lesen kann,
wird nicht durch eine Schätzung ersetzt. Kein Treffer bedeutet: kein Panel.

## Kontextfragen statt Satz-Schablonen

Früher baute die Seite aus dem Suchbegriff Sätze wie *„Was sind die wichtigsten
Details zu X?“*. Das war immer gleich und selten hilfreich. Heute:

- Fragen und verwandte Begriffe kommen aus den **Suchvorschlägen** der eigenen
  Instanz, nicht aus einer Schablone.
- Fehlen Vorschläge, leitet die Seite Themen aus den **Titeln der Treffer** ab.
- **Keine Sperrliste.** Es gibt keine Liste „trivialer“ Einzelwörter: Eine Suchmaschine
  soll zu jedem Begriff vertiefen, und eine Wortliste könnte nie alle kennen.
  Bleibt beides leer, erscheint die Box nicht — das entscheidet das Material,
  nicht der Code.

## KI-Überblick (Singra)

Die Zusammenfassung ist neutral und flüchtig: 0 Bytes in der Datenbank, nur im
Speicher des Tabs. Quellenverweise `[1]`, `[2]` heben die passende Quellen-Karte
hervor und springen zum Treffer. Folgefragen laufen über denselben Strom: Das
Backend schreibt die Nachfrage in eigenständige Suchanfragen um, sucht höchstens
zweimal nach, öffnet höchstens zwei Trefferseiten und reiht frische Treffer in
den Kontext ein (Details in `docs/ai-system-architecture.md`). Der
Recherche-Modus (Schnell/Tiefgehend) steht als DNA-Dropdown neben der Eingabe.

## Seitennavigation

Web zeigt die Seiten 1–4 plus „Weitere Ergebnisse“. Seite 1..3 lädt jeweils eine
frische Trefferseite; „Weitere“ hängt die nächste Seite an, ohne Doppelte.
Bilder, Videos und News zeigen keine Seitennavigation.

## Instanz anpassen

SearXNG ist stark konfigurierbar, und alles davon wirkt sich direkt auf die
Treffer von Maunting Search aus. Zwei Ebenen, die sauber getrennt sind:

| Ebene | Was hier eingestellt wird | Wo |
|---|---|---|
| **Daten** (SearXNG) | Welche Engines, in welcher Kategorie, mit welchem Gewicht, Timeout, Sprache, SafeSearch | `searxng-sidecar/settings.yml` |
| **Darstellung** (Maunting Search) | Layout, KI-Box, Wissens-Panel, Medien-Proxy, DNA | eigenes Frontend/Backend |

Die SearXNG-eigene Oberfläche spielt bei uns **keine** Rolle (`ui:`-Optionen,
Themes, Plugins für die HTML-UI): Der Nutzer sieht nie SearXNG, sondern Maunting
Search. Nützlich ist deshalb vor allem der Datenteil.

### Engines zusammenstellen

```yaml
use_default_settings: true
engines:
  # Nur diese Engines behalten (alles andere fällt weg)
  keep_only: [duckduckgo, wikipedia, wikidata, brave]
  # Oder gezielt einzelne entfernen
  # remove: [google, qwant]
```

Reihenfolge und Dichte der Treffer steuert `weight` — ein größerer Wert hebt
die Ergebnisse dieser Engine in der Liste an:

```yaml
engines:
  - name: duckduckgo
    weight: 2.0
  - name: wikipedia
    weight: 1.0
```

### Kategorien je Engine

Welche Engine im Bilder-, Video- oder News-Tab sucht, steht pro Engine:

```yaml
engines:
  - name: duckduckgo
    categories: [general, images, videos, news]
  - name: wikidata
    categories: [general]
```

Leere Tabs bedeuten: keine Engine dieser Kategorie liefert gerade etwas.

### Sprache, SafeSearch, Zeitverhalten

```yaml
search:
  default_lang: "de"          # ohne Region-Auswahl
  # languages: [de, en]      # nur diese Sprachen anbieten
  safe_search: 0              # 0/1/2; 2 = streng, nutzt der Jugendschutz
  method: "POST"              # Suchbegriff steht in keinem Zugriffslog
  # max_page: 10             # wie viele Seiten die API annimmt
  ban_timeout: 15             # Sekunden, die eine traurige Engine pausiert
outgoing:
  request_timeout: 3.0        # globaler Timeout je Engine-Anfrage
  retries: 1                  # erneuter Versuch bei Fehlern
```

Maunting Search schickt die Sprache aus seinem Regions-Dropdown (derzeit
`de`, `en`, `at`, `ch`, `fr`, `es`, `it`, `nl`); `default_lang` greift nur, wenn
der Browser keine eigene Region mitgibt.

### Eigene Engines und Datenschutz-Plugins

Was unser Backend nicht abbildet, lässt sich über SearXNG nachbauen:

```yaml
engines:
  - name: meine-interne-suche
    engine: xpath
    search_url: https://wiki.example.de/index.php?search={query}
    url_xpath: //div[@class="result"]/a/@href
    title_xpath: //div[@class="result"]/a
    categories: [general]
    shortcut: my
```

Plugins arbeiten auf den Ergebnissen, bevor das Backend sie sieht:

```yaml
plugins:
  - tracker_url_remover     # utm_*, fbclid … aus Links entfernen
  - hash_finder             # MD5/SHA ansehen, ohne den Tab zu wechseln
  - unit_converter          # 10 kg in lb
  - tor_check
```

`tracker_url_remover` ist für unsere Suchseite der wertvollste: die Links in den
Treffern werden sauber, bevor sie beim Nutzer ankommen.

### Was SearXNG **nicht** kann

Das sollte man vor dem Umbau wissen, damit keine falsche Erwartung entsteht:

- **Ranking wie Google.** SearXNG aggregiert und gewichtet grob (`weight`), aber
  es kennt die Signale einer kommerziellen Suche nicht. Die Trefferqualität
  hängt an den Quellen-Engines.
- **Wartung.** Engines brechen regelmäßig, wenn eine Seite ihr HTML oder ihre
  API ändert. `display_error_messages: true` zeigt das im Instanz-Log; ein
  Update von SearXNG bringt meist Reparaturen mit.
- **KI und Zusammenfassung.** SearXNG tut das nicht — die KI-Box, die
  Folgefragen und die selbständige Weiterrecherche sind unsere Schicht
  (`ai_search_summary_service.py`).
- **Bilder für jedes Ergebnis.** Vorschaubilder kommen aus den Engines
  (`img_src`, `thumbnail`). Wo keine sind, zeigt Maunting Search die Karte ohne
  Bild — das ist normal, kein Fehler.
- **Eigene Anmeldung, Personalisierung, Vorschläge für einzelne Nutzer.** Nicht
  vorgesehen und aus Datenschutzgründen nicht gewollt.
- **Das UI.** Jede Suchmaschinen-Optik, die nicht der eigenen gleicht, müsste in
  Maunting Search gebaut werden — nicht in SearXNG.

### Was bei uns schon eingestellt ist

`searxng-sidecar/settings.yml` im Repository: JSON und HTML als Formate,
`autocomplete: duckduckgo`, `method: POST`, `limiter: false` (wir begrenzen im
Backend je Konto), `image_proxy: false` (Bilder laufen über `/suche/bild`),
`debug: false`, Instanzname `MSM Search Sidecar`. Der Port liegt auf `127.0.0.1`
des Servers (`docker-compose.yml`: `127.0.0.1:8888`), das Netz ist also nicht im
Internet erreichbar. `secret_key` kommt aus `searxng-sidecar/.env`.

## Lizenzen

SearXNG ist freie Software unter der **GNU Affero General Public License
(AGPLv3)**. Maunting Search ist eine eigene Oberfläche dafür; die Metasuche-Architektur
basiert technisch auf SearXNG. Der Quellcode der Instanz wird vom Betreiber
vorgehalten und kann Nutzern auf Anfrage angeboten werden — genau dieses
Versprechen verlangt die AGPLv3. Der Fußzeilen-Hinweis der Suchseite weist darauf
hin, damit die Zuschreibung nicht verloren geht.

Drei Dinge bleiben bei jeder Anpassung der InstanzPflicht:

- Die Wikipedia-Abfragen gehen vom **Server** aus und mit einem eigenen
  User-Agent (`MauntingSearch/1.0`), damit Wikipedia sie nicht abweist.
- Ein eigener SearXNG-Endpunkt wird im Backend gesetzt
  (`ai_web_search_service.store_searxng_url`), nicht im Browser.
- Wird die Instanz umbenannt oder ihr Erscheinungsbild geändert, ändert sich am
  Namen **Maunting Search** im UI nichts.

---

## Quellindex

| Bereich | Datei |
|---|---|
| Suchseite | `frontend/src/browser/seite/MsmSuche.tsx` |
| KI-Kachel | `frontend/src/browser/seite/suchzusammenfassung/SuchzusammenfassungKachel.tsx` |
| Gemeinsame Suchleiste | `frontend/src/browser/kopf/SuchEingabeLeiste.tsx` (auch auf der Startseite) |
| Medien-Proxy-Anzeige | `frontend/src/browser/seite/SuchBild.tsx` |
| Favicons | `frontend/src/browser/seite/start/KachelSymbol.tsx`, `browser/src-tauri/src/kachelsymbol.rs` |
| Routen | `backend/routers/browser_suche.py` |
| Suche, Autocomplete, Wissen, Bilder | `backend/services/ai_web_search_service.py` |
| KI-Zusammenfassung | `backend/services/ai_search_summary_service.py` |
| Sidecar | `searxng-sidecar/` |
| Datenschutz | `frontend/src/pages/Privacy.tsx`, `privacyPolicy.sections.browser.items.mauntingSearch` |
