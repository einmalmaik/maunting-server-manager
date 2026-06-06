# ADR-0011: GCS-Provider mit offiziellem google-cloud-storage SDK

Status: Accepted
Date: 2026-06-06

## Context

Das neue Backup-System soll Google Cloud Storage (GCS) als
Backup-Ziel anbieten. GCS ist die Object-Storage-Loesung von Google
Cloud Platform und bietet starke Konsistenz, Versioning, IAM-basierte
Zugriffskontrolle und optional KMS-Encryption serverseitig.

GCS hat zwar einen **S3-Compatibility-Mode** (HMAC-Keys), aber fuer
eine native Integration ist das offizielle google-cloud-storage SDK
sauberer: native Semantik (z. B. `bucket.IAMConfiguration`),
`storage.objectAdmin`-Rolle, und keine S3-Inkompatibilitaets-
Ueberraschungen.

## Decision

Wir nutzen das **offizielle `google-cloud-storage` Python-SDK**
(>= 2.16.0, aktuell 2.19.x) als einzige GCS-Lib. Adapter liegt hinter
dem `BackupProvider`-Interface in `services/backup_provider/gcs.py`.

Auth-Pattern: **Service-Account-JSON-Datei**, deren Pfad in
`.env` als `MSM_BACKUP_GCS_SA_FILE` liegt. Die Datei wird vom User
manuell angelegt (`chmod 600`, root-owned) und der Pfad in `.env`
eingetragen. Service-Account braucht `roles/storage.objectAdmin`
auf den Bucket.

## Begruendung

### Library-Wahl: offizielles google-cloud-storage SDK

- **Native Loesung:** Das SDK ist die kanonische Python-Lib fuer GCS.
  Google-maintained, regelmaessige Releases, offizielle Doku.
- **Sync-API passend zum Use-Case:** Backup-Operationen laufen ohnehin
  in `asyncio.to_thread`. Async-SDK waere zusaetzliche Komplexitaet
  ohne Vorteil.
- **Methoden-Mapping 1:1 zu unserem Interface:** `bucket.blob().upload_from_filename`
  (upload), `download_to_filename` (download), `list_blobs(prefix=...)`
  (list), `delete()` (delete). Schlank, ohne Glue-Code.
- **Auto-Pagination:** `list_blobs` liefert einen Iterator ueber alle
  Pages automatisch — kein manuelles Cursor-Management wie bei Dropbox.
- **IAM-Integration:** Service-Account-Keyfile + Rollen-Konzept
  (`storage.objectAdmin`) ist Standard-Google-Pattern. Kein Custom-
  Credential-Manager noetig.

### Auth-Wahl: Service-Account-JSON (Pfad in .env)

- **Standard GCP-Pattern:** Service-Account-Keyfiles sind der
  Standard-Weg fuer Server-zu-Server-Auth. Alternativen:
  - **Application Default Credentials (ADC)**: wuerde GCE/GKE-
    Metadaten-Server oder `GOOGLE_APPLICATION_CREDENTIALS`-Env lesen.
    ADC ist gut fuer GCE, aber fuer einen self-hosted Server auf
    Hetzner/DigitalOcean/etc. ist die explizite JSON-Datei klarer
    und reproduzierbarer.
  - **OAuth-User-Token**: wie Dropbox-Pattern, aber OAuth-Flow
    einmalig manuell waere umstaendlicher als JSON-Datei vom
    Service-Account-Setup.
- **Pfad statt base64-String in .env:** JSON-Datei auf der Platte
  (`chmod 600`) ist sicherer und ermoeglicht Key-Rotation ohne
  Panel-Restart. `cat` zur Inspektion, `gcloud iam service-accounts
  keys create` zum Rotieren.
- **Minimaler Scope:** `roles/storage.objectAdmin` ermoeglicht
  Upload/Download/Delete/Liste auf den Bucket. Kein Project-weiter
  Admin noetig.

### S3-Compatibility-Mode NICHT genutzt

GCS hat einen S3-Compat-Mode via HMAC-Keys. Vorteile: man koennte
unseren S3-Provider direkt nutzen. Nachteile:
- HMAC-Keys sind ein zusaetzliches Secret, das verwaltet werden muss.
- Manche GCS-Features (IAM, KMS, Object-Versioning) sind nicht oder
  anders ueber S3-Compat zugaenglich.
- Native-Integration ist klarer und zukunftssicherer.

Entscheidung: native SDK. S3-Compat-Provider bleibt fuer AWS,
Hetzner S3, Cloudflare R2, Backblaze B2, MinIO, Wasabi.

## Konsequenzen

### Dependency-Flaeche: schwer aber gut gepflegt

`google-cloud-storage` zieht transitiv:
- `google-cloud-core` (klein, harmlos)
- `google-api-core` (HTTP-Framework, mittel)
- `grpcio` (gRPC, gross, ~30-50 MB nativ; wird nur geladen wenn
  tatsaechlich GCS genutzt — Lazy-Import in der Factory)
- `protobuf` (gross, aber Standard)
- `google-auth` (Auth, klein)

Die Gesamt-Install-Groesse steigt um ~50-100 MB. Begruendung:
- Google-maintained, regelmaessige Security-Patches.
- grpcio ist Industriestandard fuer alle grossen Cloud-SDKs
  (Google, Azure, AWS ueber verschiedene Pfade).
- Lazy-Import in der Factory verhindert, dass User ohne GCS-
  Konfiguration die Lib beim Startup laden.

### Path-Layout: flache Keys mit Prefix

GCS hat keine echten Folder — Keys sind flach mit `/` als Separator.
Konvention:
```
<path_prefix>/<server_id>/<filename>              (Daten)
<path_prefix>/<server_id>/<filename>.meta.json   (Meta)
```
Default-Prefix: `msm-backups` (anpassbar ueber
`MSM_BACKUP_GCS_PATH_PREFIX`).

Folder-Marker (size=0, endet mit `/`) sind in der Natur von GCS
selten, aber wir filtern sie in `list_metadata` defensiv raus.

### Parent-Keys werden automatisch angelegt

Im Gegensatz zu SFTP (wo wir `mkdir -p` brauchen), aber anders als
S3 (das auch implizit ist), ist GCS explizit flach. "Parent-Folder"
existieren nur als Prefix im Key-Namen. Kein expliziter mkdir noetig.

### Path-Traversal-Schutz (analog zu S3/SFTP/Dropbox)

- Constructor + `_full_key` validieren:
  - Key nicht leer
  - Key beginnt nicht mit `/`
  - Kein `..` in Key-Teilen
  - Final-Key bleibt unter `path_prefix`

### Progress-Callback: einmaliger Final-Call

Das GCS-SDK bietet in `upload_from_filename` / `download_to_filename`
**keinen** per-call Progress-Callback (anders als boto3). Wir
reporten daher einmalig am Ende mit der finalen Dateigroesse
(gleiche Semantik wie LocalProvider/DropboxProvider).

`blob.chunk_size = 5 * 1024 * 1024` aktiviert resumable Upload mit
5-MB-Chunks (klein genug fuer saubere Retries bei Netzwerkabbruch,
gross genug fuer guten Throughput).

### Idempotente delete()

GCS `blob.delete()` auf einen nicht-existenten Blob wirft
`google.api_core.exceptions.NotFound`. Wir erkennen das im
Adapter und tolerieren es (continue). Andere Fehler (Forbidden,
ServiceUnavailable) propagieren als generischer `ProviderError`.

### list_metadata: Auto-Pagination

`client.list_blobs(bucket, prefix=...)` liefert einen Iterator, der
automatisch alle Pages konsumiert. Wir filtern auf `*.meta.json`
und parsen pro Eintrag. Kaputte Meta-Files werden uebersprungen.

### Service-Account-File-Lebenszyklus

- User erstellt im GCP-Console ein Service-Account mit
  `roles/storage.objectAdmin` auf den Backup-Bucket.
- User laedt den JSON-Keyfile herunter, legt ihn als
  `/opt/msm/secrets/gcs-sa.json` ab, `chmod 600 msm:msm`.
- User traegt `MSM_BACKUP_GCS_SA_FILE=/opt/msm/secrets/gcs-sa.json`
  in `.env` ein (install.sh macht das automatisch).
- Key-Rotation: User erstellt neuen Key im GCP-Console, ersetzt
  die JSON-Datei, restartet das Panel. Keine Code-Aenderung.

## Alternativen

- **S3-Compat via HMAC-Keys:** wuerde S3-Provider doppelt nutzen.
  Verworfen (siehe oben).
- **Andere Libs (z. B. `gcloud-python`):** identisch zum SDK.
  Verworfen, da `google-cloud-storage` der kanonische Name ist.
- **Async-SDK (`google-cloud-storage` + asyncio):** existiert nicht
  offiziell. `aiohttp`-Wrapper waere Custom-Code. Verworfen.
- **Application Default Credentials (ADC):** schoen fuer GCE, aber
  fuer self-hosted Server zu implizit. Verworfen.
- **OAuth-User-Token:** wie Dropbox, aber JSON-Datei ist klarer
  fuer Server-Setups. Verworfen.

## Security

- **Service-Account-JSON-File:** Liegt nur in `MSM_BACKUP_GCS_SA_FILE`
  (vom Installer validiert). Datei muss `chmod 600` haben, der
  Installer prueft das (zukuenftige Erweiterung).
- **Keyfile-Lesen:** nur durch `google.cloud.storage.Client.from_service_account_json`
  — kein eigenes Parsing im Adapter.
- **Fehlertexte:** generisch ("Upload fehlgeschlagen" — kein
  Bucket-Name, kein Pfad, kein Project-ID).
- **Path-Traversal-Schutz** (siehe oben).
- **Adapter sieht nur Chiffretext** (Verschluesselung im Caller,
  ADR-0013).
- **`.env` mit `chmod 600`** — etabliertes MSM-Muster, kein neuer
  Schutz noetig. Pfad zur JSON-Datei (nicht der JSON-Inhalt) ist
  in `.env`.

## Test-Coverage

- 42 Tests mit `FakeGcsClient` (in-memory, kein echter GCS-Account
  noetig). `from_service_account_json` wird per monkeypatch ersetzt,
  sodass die ECHTE GCS-Initialisierungs-Logik (ValueError/OSError
  auf kaputten Files) getestet wird:
  - **Contract:** Interface-Implementierung, Constructor-Validierung
    (leere Felder, path_prefix-Format, Normalisierung, SA-File
    fehlt → ProviderError, SA-File JSON kaputt → ProviderError)
  - **Connection:** True bei existentem Bucket, False bei 404,
    False bei Auth-Fehler, False bei Forbidden, False bei
    ServiceUnavailable
  - **Upload/Download:** Roundtrip byte-genau, intermediate-dirs,
    missing key/source, Upload-Fehler-Propagation, Progress-Callback
    (einmaliger Final-Call)
  - **Delete:** Daten + Meta, fehlende Dateien, malformed key,
    non-not_found Fehler → raise
  - **List-Metadata:** parsed, kaputte Files skipped, empty bucket,
    korrekter Prefix, GCS-Errors → raise, Folder-Marker ignoriert
  - **Security Path-Traversal:** absolute Pfade, "..", mixed,
    leerer Key
  - **Factory:** gcs-Branch, fehlende Credentials

## Review

Diese ADR ist zu reviewen, sobald:
- Google die API breaking changed (z. B. Default-Authentication-
  Modell aendert sich)
- Wir Multi-Region Buckets oder Cross-Region Replication einfuehren
- Wir KMS-Encryption serverseitig aktivieren (zusaetzliche Config)
- Wir resumable-Upload mit echtem Progress-Callback brauchen
  (groessere Files > 1 GB)
