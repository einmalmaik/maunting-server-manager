# ADR-0012: Azure-Provider mit offiziellem azure-storage-blob SDK

Status: Accepted
Date: 2026-06-06

## Context

Das neue Backup-System soll Azure Blob Storage als Backup-Ziel
anbieten. Azure Blob ist Microsofts Object-Storage-Loesung, in
MSM-Setups typischerweise fuer Kunden die bereits Azure-Infrastruktur
nutzen oder Azure-Vorteile (Geo-Redundanz, immutability-Policies,
Legal-Hold) brauchen.

## Decision

Wir nutzen das **offizielle `azure-storage-blob` Python-SDK**
(>= 12.0.0, aktuell 12.29.x) als einzige Azure-Lib. Adapter liegt
hinter dem `BackupProvider`-Interface in
`services/backup_provider/azure.py`.

Auth-Pattern: **Connection-String** (kein Azure-AD-Setup, simpelster
Self-Hosted-Pfad). Der User generiert den Connection-String im
Azure-Portal (Storage-Account → "Access keys" → "Show keys" → eine
der zwei Keys kopieren, inkl. Praefix
`DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net`).
Connection-String liegt in `.env` mit `chmod 600`.

## Begruendung

### Library-Wahl: offizielles azure-storage-blob SDK

- **Native Loesung:** das SDK ist die kanonische Python-Lib fuer
  Azure Blob. Microsoft-maintained, regelmaessige Releases.
- **Sync-API passend zum Use-Case:** Backup-Operationen laufen ohnehin
  in `asyncio.to_thread`. Async-API wuerde zusaetzliche Komplexitaet
  ohne Vorteil bringen.
- **Methoden-Mapping 1:1 zu unserem Interface:** `upload_blob` (upload),
  `download_blob` (download), `list_blobs` (list), `delete_blob` (delete).
  Schlank, kein Glue-Code.
- **Container-Manager inklusive:** `BlobServiceClient` und
  `ContainerClient` bieten `exists()` und `create_container()` fuer
  den Initial-Setup, sodass der Installer den Container nicht
  manuell anlegen muss.
- **Auto-Pagination:** `list_blobs(name_starts_with=...)` liefert
  einen `ItemPaged`-Iterator, der automatisch alle Pages konsumiert.

### Auth-Wahl: Connection-String

- **Standard Self-Hosted Pattern:** Connection-String ist der
  einfachste Weg, Azure-Blob-Credentials in einer Applikation zu
  hinterlegen. Enthaelt Endpoint + Account-Name + Account-Key in
  einem String.
- **Alternative: DefaultAzureCredential** mit Azure-AD: erfordert
  Azure-AD-App-Registration, Service-Principal, Tenant-ID, Client-ID
  und Client-Secret. Komplexer; fuer self-hosted Single-Tenant-
  Setups overkill.
- **Alternative: SAS-Token** (Shared Access Signature): URL-gebunden,
  zeitlich befristet. Sinnvoll fuer kurzlebige Delegation, aber
  nicht fuer dauerhafte Server-Credentials.
- **Connection-String = Klartext in .env mit chmod 600** — gleiches
  Pattern wie S3-Secret-Key, SFTP-Passwort, GCS-SA-JSON-Pfad,
  Dropbox-Refresh-Token. Etabliertes MSM-Muster.

### S3-Compatibility-Mode NICHT genutzt

Azure Blob bietet keinen echten S3-Compat-Mode. Es gibt externe
Tools (z. B. `azure-storage-blob` mit eigenen Adaptern), aber die
sind alle gegenueber dem nativen SDK Mehraufwand ohne Vorteil.
Entscheidung: native SDK, klar und zukunftssicher.

## Konsequenzen

### Live-Progress: native Azure-Hook + manueller Stream-Wrapper

Das Azure-SDK bietet einen **nativen progress_hook** fuer Upload
(direkt in der `upload_blob`-Signatur, dokumentiert als
`Callable[[int, Optional[int]], None]` mit
`(bytes_transferred, total_bytes)`). Wir nutzen ihn 1:1, ohne
Custom-Invent.

Fuer Download hat das SDK **keinen** nativen Hook, aber
`download_blob()` liefert einen `StorageStreamDownloader`. Wir
konsumieren ihn per `readinto(file_wrapper)` in einer Schleife bis
EOF (`while readinto > 0`). Der `_ProgressFileWrapper` zaehlt Bytes
beim `write()` und ruft `progress_cb` kumulativ.

**Wichtig:** `readinto` ist NICHT single-shot — der Azure-Stream
liefert pro Call einen Block (typisch 4 MB). Ohne Schleife wuerde
der Provider nur den ersten Block lesen und der Download fuer
Multi-GB-Backups unvollstaendig bleiben. **Bug-Fix-Insight aus
Schritt 5 (GCS):** bei allen Stream-basierten Downloads die
EOF-Bedingung explizit prufen, nicht single-shot annehmen.

**Fallback ohne progress_cb:**
- Upload: `upload_blob(f, length=size, overwrite=True)` ohne Hook.
- Download: `readall()` statt `readinto` (alle Bytes am Stueck).
- Garantiert: keine `_ProgressFileWrapper` Konstruktion ohne
  progress_cb.

### Path-Layout: Container + Pfad-Prefix + Key

Azure hat **Container** als top-level-Namespace. Layout:
```
<container>/<path_prefix>/<server_id>/<filename>              (Daten)
<container>/<path_prefix>/<server_id>/<filename>.meta.json   (Meta)
```
- Default-Container: `msm-backups` (anpassbar via
  `MSM_BACKUP_AZURE_CONTAINER`).
- Path-Prefix: optional, default leer (anpassbar via
  `MSM_BACKUP_AZURE_PATH_PREFIX`).

### Container wird auto-erstellt

`test_connection()` und `upload()` pruefen via `container.exists()`
ob der Container existiert. Falls nicht, wird er via
`create_container()` idempotent angelegt. Spart dem User einen
manuellen Azure-Portal-Schritt.

### Path-Traversal-Schutz (analog zu allen anderen Providern)

- Constructor + `_full_blob_name` validieren:
  - Key nicht leer
  - Key beginnt nicht mit `/`
  - Kein `..` in Key-Teilen
  - Final-Key bleibt unter `path_prefix` (wenn gesetzt)
  - Bei leerem `path_prefix` nur normalize + check

### Progress-Hook-Signatur: Mappen auf kumulativen Counter

Der Azure-Hook feuert `(bytes_transferred, total_bytes)` pro Block.
Wir mappen auf unseren kumulativen Counter:
```python
def hook(done: int, _total: Optional[int]) -> None:
    progress_cb(done)
```
Total wird ignoriert, weil unsere Backup-Service-Logik die
Prozentberechnung basierend auf der DB-`size_mb`-Spalte macht
(nicht basierend auf Hook-Total — das waere redundant).

### Idempotente delete()

Azure `delete_blob()` auf einen nicht-existenten Blob wirft
`azure.core.exceptions.ResourceNotFoundError`. Wir erkennen das
im Adapter und tolerieren es (continue). Andere Fehler
(`ServiceRequestError`, `ClientAuthenticationError`) propagieren
als generischer `ProviderError`.

### list_metadata: Auto-Pagination

`container_client.list_blobs(name_starts_with=prefix)` liefert
einen `ItemPaged` mit automatischer Pagination. Wir filtern auf
`*.meta.json` und parsen pro Eintrag. Kaputte Meta-Files werden
uebersprungen.

## Alternativen

- **S3-Compat via Azure-Data-Lake-Storage-Gen2 mit S3-Endpoint:**
  existiert, ist aber komplexer (anderes Auth-Modell, andere
  Hierarchie). Verworfen.
- **Andere Libs (z. B. `azure-mgmt-storage`):** Management-Plane,
  nicht Data-Plane. Falsche Lib fuer Backup-Operations. Verworfen.
- **DefaultAzureCredential / Azure-AD:** sicherer fuer Multi-Tenant-
  Setups, aber fuer self-hosted Single-Tenant-Setups overkill.
  Verworfen.
- **SAS-Token in .env:** wuerde Rotation komplexer machen. Verworfen.
- **Async-SDK:** offiziell nicht angeboten. Eigener Wrapper waere
  Custom-Code. Verworfen.

## Security

- **Connection-String-Handling:** Liegt nur im Konstruktor-Argument;
  wird weder geloggt, noch in Errors/Dumps geschrieben. Fehlertexte
  sind generisch ("Upload fehlgeschlagen" — kein Account-Name, kein
  Container, kein Pfad).
- **Constructor validiert das Format** (AccountName= + AccountKey=
  muss enthalten sein), gibt frueh einen klaren ProviderError statt
  erst beim ersten API-Call.
- **Path-Traversal-Schutz** (siehe oben).
- **Adapter sieht nur Chiffretext** (Verschluesselung im Caller,
  ADR-0013).
- **`.env` mit `chmod 600`** — etabliertes MSM-Muster, kein neuer
  Schutz noetig.

## Test-Coverage

- 41 Tests mit `FakeBlobServiceClient` (in-memory, kein echter
  Azure-Account noetig). `from_connection_string` wird per
  monkeypatch ersetzt, sodass die ECHTE Azure-Initialisierungs-
  Logik (ValueError auf kaputten Strings) getestet wird:
  - **Contract:** Interface-Implementierung, Constructor-Validierung
    (leere Felder, Connection-String-Format-Checks)
  - **Connection:** True bei existentem Container, False bei Auth-
    Fehler / Service-Error, Container-Auto-Create bei Missing
  - **Upload ohne progress_cb:** Single-Shot, keine Hook-Construction
  - **Upload mit progress_cb:** Azure-Hook mit kumulativer Byte-
    Anzeige (Single-Block + Multi-Block mit 3 Calls)
  - **Upload:** Container-Auto-Create, missing source, Error-Propagation
  - **Download ohne progress_cb:** Single-Shot `readall()`, intermediate-dirs
  - **Download mit progress_cb:** Stream mit `readinto`-Schleife bis
    EOF (3 kumulative Calls bei 10 MB / 4 MB Blocks)
  - **Delete:** Daten + Meta, fehlende Dateien, malformed key,
    non-not_found Fehler → raise
  - **List-Metadata:** parsed, kaputte Files skipped, empty container,
    Azure-Errors → raise, Folder-Marker ignoriert
  - **Path-Prefix:** wird korrekt dem Blob-Namen vorangestellt
  - **Security Path-Traversal:** absolute Pfade, "..", mixed, leer
  - **Factory:** azure-Branch, fehlende Connection-String

## Review

Diese ADR ist zu reviewen, sobald:
- Azure die API breaking changed (z. B. Connection-String-Format aendert)
- Wir Azure-AD als Auth-Alternative anbieten wollen
- Wir Immutable-Blob-Policies (WORM) integrieren
- Wir Cross-Region-Replication oder Geo-Redundanz setup automatisieren
- Das SDK einen nativen progress_hook fuer download_blob einbaut
  (kann dann der Stream-Wrapper durch eine 1:1 Hook-Integration
  ersetzt werden — gleiche Semantik, weniger Code)
