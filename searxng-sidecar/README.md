# MSM SearXNG Search Sidecar

Isolierter, lokaler Metasuchmaschinen-Sidecar für den Maunting Server Manager.

## Zweck
Ermöglicht dem MSM-KI-Assistenten vollwertige, private und unlimitierte Websuchen ohne Abhängigkeit von externen kostenpflichtigen Such-APIs.

## Architektur & Sicherheit
- **Bind-Adresse:** Ausschließlich `127.0.0.1:8888` (nicht öffentlich im Internet erreichbar).
- **Format:** JSON-API (`/search?q=...&format=json`).
- **Isolation:** Rootless Container mit eingeschränkten Linux Capabilities.
- **SSRF-Schutz:** Backend filtert private Netzwerke vor und nach der Suche.
