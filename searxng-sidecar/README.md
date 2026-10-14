# Maunting Search Sidecar (SearXNG)

Isolierter, lokaler Metasuchmaschinen-Sidecar für den Maunting Server Manager.
Er versorgt die Suchseite des Secure Browsers (**Maunting Search**) und die
Websuche des KI-Assistenten. Der Browser zeigt im UI keinen SearXNG-Hinweis –
die Suchseite tritt als **Maunting Search** auf. Die Metasuche-Architektur basiert
technisch auf SearXNG, das unter der **GNU Affero General Public License
(AGPLv3)** steht; Details und Zuschreibungspflichten in
[`docs/maunting-search.md`](../docs/maunting-search.md).

## Zweck
Ermöglicht private, unlimitierte Websuchen ohne Abhängigkeit von externen
kostenpflichtigen Such-APIs. Keine Suchbegriffe verlassen das Gerät des Nutzers
bis auf diesen lokalen Sidecar.

## Architektur & Sicherheit
- **Bind-Adresse:** Ausschließlich `127.0.0.1:8888` (nicht öffentlich im Internet erreichbar).
- **Formate:** JSON-API (`/search?q=...&format=json`) und Autocomplete
  (`/autocompleter?q=...`) — beides braucht Maunting Search, siehe `settings.yml`.
- **Bilder:** `image_proxy: false`. Vorschaubilder und Wikipedia-Bilder lädt das
  Backend kontrolliert über `/api/browser/suche/bild`; der Browser kennt keine
  Direktadresse einer gefundenen Seite.
- **Isolation:** Rootless Container mit eingeschränkten Linux Capabilities.
- **SSRF-Schutz:** Backend filtert private Netzwerke vor und nach der Suche.

## Zwei Wege, die diese Instanz benutzen

| Weg | Route | Wofür |
|---|---|---|
| Maunting Search | `POST /api/browser/suche` (u. a.) | Treffer, Vorschläge, Wissen, Medien-Proxy |
| Singra | `ai_web_search_service.searxng_treffer` | Werkzeug `web_search` der KI |

Ein eigener Endpunkt wird im Backend hinterlegt
(`ai_web_search_service.store_searxng_url`), nie im Browser.
