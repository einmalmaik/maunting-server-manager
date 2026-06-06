# ADR-0010: Dropbox-Provider mit offiziellem Python-SDK

Status: Accepted
Date: 2026-06-06

## Context

Das neue Backup-System soll Dropbox als Backup-Ziel anbieten.
Dropbox hat **keine S3-kompatible API**, der einzige Weg ist das
offizielle Python-SDK oder eine direkte HTTP-Implementierung der
`content.dropboxapi.com` Endpoints. Wir nutzen das SDK.

## Decision

Wir nutzen das **offizielle `dropbox` Python-SDK** (>= 10.0.0,
aktuell 12.x) als einzige Dropbox-Lib. Adapter liegt hinter dem
`BackupProvider`-Interface in `services/backup_provider/dropbox.py`.

Auth-Pattern: **App-Key + App-Secret + manuell generierter
Refresh-Token** (Standard server-zu-server). Das SDK handhabt
Auto-Refresh der Access-Tokens intern.

## Begruendung

### Library-Wahl: offizielles dropbox SDK

- **Nur gangbarer Weg ohne S3-Compat-Layer:** Dropbox hat keine
  S3-kompatible API. Alternativen:
  - S3-Compat via DataGateway o.ae.: gibt es **nicht** fuer Dropbox
    (im Gegensatz zu z. B. Wasabi oder Backblaze B2).
  - Direktes HTTP gegen `content.dropboxapi.com`: ~300-500 LoC,
    inkl. OAuth-Refresh, Multipart-Upload, Pagination, Error-Tagging.
    KISS-Verstoss.
  - SDK: komplett, gewartet, dokumentiert.
- **Offiziell, Dropbox-maintained:** das SDK ist die kanonische
  Python-Lib. Regelmaessige Releases, Issue-Tracker auf GitHub.
- **Eingebauter OAuth-Refresh:** `Dropbox(app_key=..., app_secret=...,
  oauth2_refresh_token=...)` macht Token-Refresh transparent — kein
  eigener Token-Manager noetig.
- **Sync-API passend zum Use-Case:** Backup-Operationen laufen ohnehin
  in `asyncio.to_thread`. Async-SDK (gibt es nicht offiziell von
  Dropbox) waere zusaetzliche Komplexitaet.
- **Kleine transitive Flaeche:** `dropbox` + `stone` + `ply` (alle
  Dropbox-maintained). Keine externen HTTP-Libs (dropbox zieht keine
  `requests`-Dep fuer den Sync-Client, sondern nutzt urllib3 nicht —
  stone macht HTTP ueber `urllib`).

### Auth-Wahl: App-Key + Secret + Refresh-Token

- **Standard server-zu-server Pattern:** Dropbox-Apps koennen
  "Offline-Zugriff" anfordern, was einen Refresh-Token liefert, der
  nicht ablaeuft (nur manuell rotiert werden kann).
- **Einmaliger manueller OAuth-Flow:** User erstellt eine Dropbox-App
  in der Dropbox-Konsole, generiert einmalig den Refresh-Token via
  OAuth-`/oauth2/token`-Tool (z. B. `curl` gegen den Endpunkt oder
  der offizielle OAuth-Generator). Das ist **bewusst manuell**, weil:
  - Kein In-App-OAuth-Browser-Flow im Server-Installer (headless,
    KISS, keine OAuth-Callback-URL-Hinterlegung noetig).
  - Der Refresh-Token ist langfristig stable — User muss ihn nur
    einmal generieren.
  - Security: kein Token-Flow ueber MSM-Frontend, User behaltet
    Kontrolle in der Dropbox-Konsole.
- **Refresh-Token-Aufbewahrung:** Liegt in `.env` mit `chmod 600`,
  gleiches Muster wie S3-Secret-Key / SFTP-Passwort. Client-seitige
  AES-256-GCM (ADR-0013) schuetzt zusaetzlich gegen kompromittierte
  Dropbox-Credentials.

## Konsequenzen

### Single-Shot-Upload-Limit (150 MB)

- `dropbox.files_upload` ist per SDK-Docs auf 150 MB pro Call
  limitiert.
- **V1-Verhalten:** Provider wirft `ProviderError("Upload
  fehlgeschlagen")` mit Hinweis auf Chunked-Upload-Notwendigkeit bei
  Files > 150 MB. Backup-Operationen brechen ab.
- **Zukuenftige Erweiterung:** `upload_session_start` +
  `upload_session_append_batched` + `upload_session_finish` fuer
  Multi-GB-Backups. ADR dokumentiert dies; konkrete Implementation
  ist ein eigener Commit.
- **Pragmatische Einschaetzung:** die meisten Game-Server-Backups
  (Savegames + Configs, ohne komplette Spieldaten) liegen unter
  100 MB. DayZ/CS-Maps koennen allerdings mehrere GB gross sein —
  dort ist Chunked-Upload dann Pflicht.

### Dropbox-API-Pfade beginnen mit /

- `files_list_folder("/msm-backups")` etc. — Pfade MÜSSEN mit
  fuehrendem `/` kommen. Constructor validiert `base_path.startswith("/")`.
- Default: `/msm-backups`. Anpassbar ueber
  `MSM_BACKUP_DROPBOX_PATH`.

### Parent-Folder werden automatisch angelegt

- Im Gegensatz zu SFTP (wo wir `mkdir -p` brauchen) legt Dropbox
  die Parent-Folder beim Upload automatisch an. Spart Code und
  vermeidet Race-Conditions bei parallelen Uploads.

### Path-Traversal-Schutz (analog zu S3/SFTP)

- Constructor + `_full_path` validieren:
  - Key nicht leer
  - Key beginnt nicht mit `/` (sonst koennte man aus base_path
    ausbrechen)
  - Kein `..` in Key-Teilen
  - Final-Pfad bleibt unter `base_path`

### Progress-Callback: einmaliger Final-Call

- `files_upload` hat keinen nativen Progress-Callback. Wir reporten
  einmalig am Ende mit der finalen Dateigroesse (gleiche Semantik
  wie LocalProvider).
- Fuer Live-Progress bei grossen Files waere Chunked-Upload mit
  per-Chunk-Report noetig — siehe oben.

### Error-Handling: 'not_found' = OK fuer idempotente Ops

- `delete()` und `list_metadata()` muessen idempotent sein. Dropbox
  wirft `ApiError` mit `error.is_path()` + `lookup.is_not_found()`
  bei fehlenden Pfaden. Wir erkennen das via Helper
  `_is_not_found()` und tolerieren es (continue fuer delete,
  leere Liste fuer list).
- Andere API-Fehler (auth, rate-limit, internal) werden generisch
  als `ProviderError` geworfen — kein Pfad, kein Token im Log.

### Pagination in list_metadata

- `files_list_folder` liefert max 500 Entries pro Page +
  `has_more` + `cursor`. Wir loopen via `files_list_folder_continue`
  bis `has_more == False`.
- Pro Meta-File ein zusaetzlicher `files_download`-Call (N+1). Bei
  vielen Backups ineffizient, aber Backup-Operationen sind nicht
  high-frequency. KISS akzeptabel.

## Alternativen

- **Direktes HTTP gegen `content.dropboxapi.com`:** ~300-500 LoC,
  inkl. OAuth-Refresh + Multipart + Error-Tagging. KISS-Verstoss.
  Verworfen.
- **S3-Compat-Adapter mit DataGateway:** existiert nicht fuer
  Dropbox. Verworfen.
- **Andere Libs (z. B. `python-dropbox-api`):** inoffiziell,
  unmaintained. Verworfen.
- **Async-SDK:** Dropbox bietet kein offizielles async SDK.
  `aiohttp`-Wrapper waere Custom-Code. Verworfen.

## Security

- **Refresh-Token-Handling:** Liegt nur im Konstruktor-Argument;
  wird weder geloggt, noch in Errors/Dumps geschrieben. Fehlertexte
  sind generisch ("Upload fehlgeschlagen" — kein Pfad, kein Token).
- **App-Secret analog:** gleich behandelt wie Refresh-Token.
- **App-Key:** weniger sensitiv (oeffentlich in Dropbox-App-Liste
  sichtbar), aber wir loggen es trotzdem nicht.
- **Path-Traversal-Schutz** (siehe oben).
- **Adapter sieht nur Chiffretext** (Verschluesselung im Caller,
  ADR-0013).
- **`.env` mit `chmod 600`** — etabliertes MSM-Muster, kein neuer
  Schutz noetig.

## Test-Coverage

- 44 Tests mit `FakeDropboxClient` (in-memory, kein echter Dropbox-
  Account noetig):
  - **Contract:** Interface-Implementierung, Constructor-Validierung
    (leere Felder, base_path-Format, Normalisierung)
  - **Connection:** True bei valid Creds + existentem base_path,
    legt base_path an falls fehlend, False bei AuthError, False bei
    non-path API-Fehler
  - **Upload/Download:** Roundtrip byte-genau, intermediate-dirs,
    missing key/source, Upload-Groessenlimit (150 MB),
    Progress-Callback, WriteMode.overwrite
  - **Delete:** Daten + Meta, fehlende Dateien, malformed key,
    non-not-found Fehler → raise
  - **List-Metadata:** parsed, kaputte Files skipped, Pagination
    (multi-page), empty base_path, not_found → leere Liste,
    Folder-Eintraege ignoriert
  - **Security Path-Traversal:** absolute Pfade, "..", mixed,
    leerer Key
  - **Factory:** dropbox-Branch, fehlende Credentials

## Review

Diese ADR ist zu reviewen, sobald:
- Dropbox die API breaking changed (z. B. neues Auth-Modell Pflicht)
- Single-Shot-Limit steigt oder faellt signifikant
- Wir Chunked-Upload via `upload_session_*` einführen (eigener
  Folge-Commit, kein ADR-Bruch)
- Dropbox ein S3-Compat-Gateway anbietet (würde die Frage
  "warum nicht S3-Provider" neu aufwerfen)
