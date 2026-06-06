# MSM Backup-Cloud Redesign — Plan

Stand: 2026-06-06  
Branch: `ms/backup-cloud-redesign`  
Worktree: `C:\Users\einma\.minimax\worktrees\msm-backup-cloud`

> **Status:** Plan final, 5 Design-Entscheidungen getroffen. Wartet auf User-Go zur Implementation.

---

## 1. Hintergrund / Problem

Aktuell speichert MSM alle Backups lokal unter `/opt/msm/backups/<server_id>/`. Das ist **kein echtes Backup**: fällt der Root-Server aus, sind Panel UND Backups weg. Der Wunsch: Backups in einen externen Storage-Provider auslagern, sodass ein Komplett-Verlust des Roots überlebbar wird.

Ziel: Ein Provider-Modell, das **heute lokal perfekt funktioniert** und **morgen in 99 % der Cloud-Storage-Anbieter läuft** (S3-API, Hetzner Storage Box, Backblaze B2, MinIO, Cloudflare R2, Wasabi etc.). Das gleiche Konzept soll später identisch für PostgreSQL (lokal vs. managed) wiederverwendet werden.

## 2. Scope

### In Scope (dieser Branch)

- **Provider-Abstraktion** für Backup-Storage: ein Interface, das 99 % der Cloud-Storages abdeckt.
- **Generisches Metadaten-JSON** pro Backup (Server-Name, Game, Timestamp, Panel-Version, Backup-Version, CPU/RAM/Disk-Limits, Ports, Public-Bind-IP).
- **Zentrale Backup-Logik** bleibt Single Source of Truth (heute `services/backup_service.py`); wird um Provider-Schritt erweitert.
- **Retention** funktioniert für lokal UND cloud (gleicher Algorithmus, provider-agnostisch).
- **Restore** funktioniert transparent — beim Cloud-Provider lädt das System automatisch herunter, entpackt, ersetzt. Server ist während Restore offline.
- **Live-Progress** im Frontend (Bytes done / total, Prozent) — bei mehreren GB Pflicht.
- **install.sh** bekommt eine neue Frage "Backup-Storage" (5. Frage) mit Re-Install-„Keep/Change"-Logik.
- **Frische Installation mit vorhandenen Cloud-Backups**: System erkennt Orphan-Backups, Admin kann Server aus der Cloud restaurieren (Ports werden neu vergeben, Limits aus Metadaten übernommen).
- **Tests** für die neue Provider-Schicht (lokal voll, S3 via moto gemockt).

### In Scope (dieser Branch) — finale Entscheidungen

- **Fünf Cloud-Provider + 1 lokal** in v1:
  1. `local` (file system, Default, /opt/msm/backups/)
  2. `s3` (boto3 — AWS S3, Hetzner S3, Cloudflare R2, Backblaze B2, MinIO, Wasabi)
  3. `sftp` (paramiko — Hetzner Storage Box, jeder generische SFTP-Server)
  4. `dropbox` (offizielles Dropbox-SDK)
  5. `gcs` (google-cloud-storage, Service-Account-Auth)
  6. `azure` (azure-storage-blob, Connection-String-Auth)
  Alle hinter demselben `BackupProvider`-Interface; alle identisch getestet (lokal real, Rest mit SDK-Mocks).
- **Client-seitige Verschlüsselung der tar.gz vor Upload** (AES-256-GCM, Schlüssel im `.env`, `chmod 600`). Provider-Bucket-Compromise darf keine lesbaren Backups leaken. Metadata-JSON bleibt unverschlüsselt (enthält nur öffentlich sichtbare Felder wie Server-Name, Game, Größe).
- **Cloud-only-Mode:** sobald Cloud konfiguriert ist, gibt es **keine lokale Kopie** mehr. Tar-Datei wird nach erfolgreichem Upload aus `/var/tmp/` gelöscht (heute schon so), ein extra `local mirror` entfällt.
- **Auto-Migration alter lokaler Backups** beim ersten Cloud-Enable: ein One-Shot-Job läuft im Hintergrund, lädt alle bestehenden lokalen Backups hoch, löscht die lokale Datei erst nach bestätigtem Cloud-Upload. UI zeigt Live-Progress, Abbruch-sicher, idempotent (Retry nach Crash möglich).
- **Dashboard-Banner** nach Login für Orphan-Backups (kein Blocking-Wizard). User entscheidet selbst, wann er den Restore startet.

### Out of Scope (für später)

- PostgreSQL-Cloud-Frage (gleiche UX, eigenes Branch).
- WebDAV (für Hetzner Storage Box; SFTP deckt das ab).
- WebDAV-Anbieter wie Nextcloud/ownCloud (S3-Adapter können das, weil viele WebDAV-Server S3-Gateway-Optionen haben).
- Auto-Migration der DB-Schemata über mehrere Releases.

## 3. Empfohlener Ansatz

### 3.1 Provider-Abstraktion (KISS, klein, testbar)

```
backend/services/backup_provider/
    __init__.py           # Factory get_provider() liest .env
    base.py               # BackupProvider ABC + BackupLocation DTO + BackupMetadata
    local.py              # File-System-Provider (heutiges Verhalten, default)
    s3.py                 # boto3-basiert (S3-kompatibel)
```

**BackupProvider**-Interface (5 Methoden, alle async-tauglich):

```python
class BackupProvider(ABC):
    name: str
    def test_connection(self) -> bool: ...
    def upload(self, local_path: Path, remote_key: str, *, progress_cb=None) -> BackupLocation: ...
    def download(self, remote_key: str, local_path: Path, *, progress_cb=None) -> None: ...
    def delete(self, remote_key: str) -> None: ...
    def list_metadata(self) -> list[BackupMetadata]: ...
```

- **`local.py`** = das heutige Verhalten, aber hinter demselben Interface (kein Fork der Logik).
- **`s3.py`** nutzt `boto3` (sync), Upload in `run_in_executor` mit Multipart + Progress-Callback → schreibt Bytes in den bestehenden `_active_backups`-Dict. boto3 spricht S3, aber auch Hetzner Storage Box (S3-kompatibel), Backblaze B2 (S3-Endpoint), Cloudflare R2, Wasabi, MinIO. → **99 % Coverage mit einer Lib**.
- **`sftp.py`** (Hetzner Storage Box + jeder generische SFTP-Server) nutzt `paramiko` (sync, im Executor). Hetzner Storage Boxen haben **keine S3-API nativ** — SFTP/SSH ist der Standard-Zugriff. Vorteil gegenüber S3: keine Vendor-Lock-In, keine Extra-Credentials auf der Hetzner-Box, funktioniert mit jeder Hetzner-Storage-Box ohne weitere Konfiguration. SFTP-Adapter ist klein (~150 Zeilen): connect, mkdir, put, get, list, remove. Pfade im Provider-Namespace: `msm-backups/<server_id>/<filename>`.
- **`dropbox.py`** nutzt das offizielle `dropbox` Python-SDK. Auth via App-Key + App-Secret + **manuell generiertes Refresh-Token** (Standard-Pattern für server-zu-server, weil OAuth-Flow einmalig ist). Upload via `files_upload`, Download via `files_download`, List via `files_list_folder`, Delete via `files_delete_v2`. Namespace: `/msm-backups/<server_id>/<filename>`. Sync-API, im Executor.
- **`gcs.py`** nutzt `google-cloud-storage`. Auth via **Service-Account-JSON**, das in `.env` als mehrzeiliger String (oder alternativ als Pfad) liegt. Service-Account braucht `roles/storage.objectAdmin` auf den Bucket. Upload via `bucket.blob().upload_from_filename`, Download via `download_to_filename`, List via `list_blobs(prefix=...)`, Delete via `delete()`. Sync-API, im Executor.
- **`azure.py`** nutzt `azure-storage-blob`. Auth via **Connection-String** (`DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net`) — einfachster Self-Hosted-Pfad, kein Azure-AD-Setup nötig. Upload via `upload_blob`, Download via `download_blob`, List via `list_blobs`, Delete via `delete_blob`. Sync-API, im Executor.
- `get_provider()` liest `MSM_BACKUP_PROVIDER` aus `.env` (Werte: `local` | `s3` | `sftp` | `dropbox` | `gcs` | `azure`, default: `local`) und instanziiert den passenden Adapter.

### 3.2 Zentrale Logik bleibt Single Source of Truth

`run_backup` in `services/backup_service.py` wird minimal erweitert:

```
1. Tar.gz lokal in /var/tmp/msm-backup-tmp/{server_id}_{ts}.tar.gz   (heute schon so)
2. Backup-Objekt in DB anlegen  (Status "uploading", remote_key vergeben)
3. Provider.upload()  mit Progress-Callback in _active_backups
4. Lokale Temp-Datei löschen
5. cleanup_old_backups()  (jetzt: provider.delete() für überschüssige Records)
6. _active_backups leeren
```

**Wichtig:** Tar-Logik ändert sich NICHT. Provider-Aufruf ist eine zusätzliche Stufe. Alle bestehenden Tests in `test_backup_service.py` bleiben grün, wenn wir den Provider-Stage hinter einem `if provider == "local": return local_legacy_path` oder per Default-Mock testen.

### 3.3 Metadata-Schema (Backup-Version 1)

Pro Backup wird eine `metadata.json` parallel zum `tar.gz` im Provider abgelegt (gleicher Remote-Key + `.meta.json` Suffix). Inhalt:

```json
{
  "backup_version": 1,
  "server_id": 123,
  "server_name": "Mein Minecraft",
  "game_type": "minecraft",
  "created_at": "2026-06-06T15:30:00Z",
  "panel_version": "v1.6.0",
  "cpu_limit_percent": 200,
  "ram_limit_mb": 4096,
  "disk_limit_gb": 50,
  "public_bind_ip": null,
  "ports": [
    {"role": "game",  "port": 25565, "protocol": "tcp"},
    {"role": "query", "port": 25565, "protocol": "udp"},
    {"role": "rcon",  "port": 25575, "protocol": "tcp"}
  ]
}
```

`panel_version` wird beim Backup zur Laufzeit aus dem Release-Tag des installierten Builds gelesen (gleiche Quelle wie `MSM_GITHUB_OWNER`/`MSM_GITHUB_REPO` + ausgelieferte `VERSION` Datei, die install.sh ins `/opt/msm/VERSION` schreibt). Damit kann der Restore auf einem komplett neu installierten Panel die exakte Quell-Panel-Version protokollieren — nützlich für Migrationen.

`Backup`-DB-Model bekommt drei neue Spalten (alle additive, default-NULL → SQLite/Postgres-Migration verträglich):

```python
provider:    Mapped[str] = mapped_column(String(32),  default="local")
remote_key:  Mapped[str] = mapped_column(String(512), nullable=True)
metadata_json: Mapped[str] = mapped_column(Text,        nullable=True)  # Snapshot
```

### 3.3a Client-seitige Verschlüsselung (AES-256-GCM)

**Zweck:** Provider-Bucket-Box-Compromise darf keine lesbaren tar.gz leaken. Backups enthalten Server-Savegames, Configs (häufig mit RCON-Passwörtern), Mod-Listen, ggf. eingebettete Datenbank-Dumps.

**Algorithmus:** AES-256-GCM (authenticated encryption, schützt vor Manipulation). `cryptography.hazmat.primitives.ciphers.aead.AESGCM` — kein Eigenbau (Security.md §4 verbietet eigene Krypto). Per-Backup-Key: 32 Byte Zufall, plus 12 Byte Nonce, beide werden dem Output vorangestellt.

**Schlüssel-Management:**
- Ein einziger **Master-Key** (32 Byte, base64) wird beim ersten Cloud-Enable generiert und in `.env` als `MSM_BACKUP_ENCRYPTION_KEY` gespeichert.
- `.env` hat bereits `chmod 600` (heutiges Muster) — kein neuer Schutz nötig.
- Schlüssel wird **nicht** vom `SECRET_KEY` abgeleitet: wenn der User SECRET_KEY rotiert, sollen alte Backups lesbar bleiben.
- Beim Restore / Auto-Migration lädt das Panel den Key aus `.env` und entschlüsselt lokal vor dem Extract.
- Klartext-Warnung in `show_current_config` („Backup-Verschlüsselungs-Key konfiguriert").

**File-Format (auf dem Provider):**
```
[ 1 byte version=0x01 ][ 12 byte nonce ][ ciphertext+tag ]
                  └──────── AES-256-GCM ────────┘
```
- Datei-Endung: `.tar.gz.enc` (Suffix `enc` macht in `mc cp`/S3-Browser klar, dass verschlüsselt)
- Metadata-JSON (`*.meta.json`) bleibt **unverschlüsselt** (nur öffentlich sichtbare Felder)
- Provider-Server-Side-Encryption (S3 SSE / SFTP-Disk-Encryption) ist komplementär, nicht Ersatz

**Schlüssel-Backup-Empfehlung:** Im Installer-Output + README klar kommunizieren: „Wer seinen `.env` verliert, verliert alle Backups." Optional: `MSM_PANEL_URL/backup-recovery` zeigt bei Eingabe des Master-Keys (z. B. via Recovery-Code-Generator) einen QR-Code zum Ausdrucken. Für v1: nur Klartext-Warnung, kein Self-Serve-Recovery — das wäre ein eigenes Feature.

**Performance-Impact:** AES-256-GCM auf moderner Hardware schafft mehrere GB/s — kein Flaschenhals im Vergleich zu Netzwerk-Upload.

**Abgrenzung zur `cryptography`-Dep:** ist eh schon im `requirements.txt` (für `python-jose` und Fernet). Keine neue Dependency.

### 3.4 Live-Progress (Frontend)

`/api/backups/{id}/status` (existiert, wird um Progress-Felder erweitert) gibt zurück:

```json
{
  "active": true,
  "operation": "uploading",
  "phase": "upload",                       // creating | uploading | downloading
  "bytes_done": 524288000,
  "bytes_total": 2147483648,
  "percent": 24,
  "started_at": "...",
  "estimated_size_mb": 2048
}
`

`Backups.tsx` zeigt zusätzlich zur bestehenden Live-Uhr einen Progress-Bar + MB-Zähler.

### 3.5 Retention (provider-agnostisch)

`cleanup_old_backups()` iteriert wie bisher über die DB-Records, sortiert nach `created_at desc`, offset(keep), und löscht pro Record via `provider.delete(remote_key)`. Bei `local` ist `delete()` einfach `os.remove()`. Bei `s3` ist es `s3.delete_object()`. Gleicher Code-Pfad, eine Code-Stelle, getestet für beide.

### 3.6 Restore (provider-agnostisch)

`POST /api/backups/{id}/restore/{backup_id}` funktioniert heute mit `backup.filename` direkt. Neu:

1. Container stoppen + remove (heute schon so).
2. Status auf `downloading` setzen, Progress = 0/total.
3. Provider.download(remote_key, /tmp/...).  
   Bei `local`: `shutil.copy()`.  
   Bei `s3`: `s3.download_file()` mit Callback.
4. `set_active_backup_status(server_id, "restoring", size)`.
5. Extract (heutige Logik, sicher mit `_safe_extract_backup_tar`).
6. Metadata aus `metadata_json` in `server.cpu_limit_percent`, `ram_limit_mb`, `disk_limit_gb`, `public_bind_ip` zurückschreiben.
7. Ports: bestehende `port_allocation_service` neu aufrufen, dabei die aus der Metadata gelesenen Rollen (game/query/rcon) übernehmen und nur die **konkreten Portnummern** neu vergeben (alter Port könnte belegt sein).
8. UFW neu öffnen.
9. Status auf `stopped`, klar im Frontend sichtbar.

**Server bleibt offline während Restore** (heute schon so, dokumentiert). User drückt manuell Start.

### 3.7 Frische Installation → Restore aus Cloud (Dashboard-Banner)

Flow:

1. `install.sh` fragt `MSM_BACKUP_PROVIDER` + Provider-spezifische Credentials (S3: 5 Felder; SFTP: Host, Port, User, Password, Pfad).
2. Direkt nach `.env`-Write: ein **Probe** läuft — `provider.list_metadata()`. Wenn nicht leer UND die DB beim Erst-Start leer ist (keine Server angelegt), schreibt install.sh `MSM_PENDING_CLOUD_RESTORE=1` in `.env` UND gibt einen Hinweis ins Install-Log.
3. Backend liest beim Startup `MSM_PENDING_CLOUD_RESTORE`. Endpoint `GET /api/setup/pending-restores` (auth-required, normaler Admin-Token) listet die Metadaten.
4. **Frontend: CloudRestoreBanner** auf dem Dashboard. Erscheint, solange `pending-restores` nicht leer ist. Klick öffnet **CloudRestoreWizard**:
   - Spalte 1: Liste der Spiele (Name, Game, Erstell-Datum, Größe), Checkbox pro Eintrag, "Alle auswählen", "Auswahl wiederherstellen", "Später".
   - Spalte 2: Live-Progress pro ausgewähltem Server (Pending → Downloading → Extracting → Creating Server → Done | Fehlertext).
   - Spalte 3: Done-Zusammenfassung mit „Zum Server"-Button pro Eintrag.
5. **User kann jederzeit „Später" sagen** — Banner bleibt sichtbar, bis die Liste leer ist (entweder alles restored oder explizit „Liste verwerfen").
6. **Fehlerhandling pro Server (unverändert):**
   - Download failt → Server wird nicht angelegt, Liste zeigt "Download fehlgeschlagen" + sanitized Grund, User kann Retry oder Skip.
   - Extract failt → Server-Row existiert, `status="error"`, `status_message="Wiederherstellung fehlgeschlagen"`.
   - Andere Server laufen unabhängig weiter.
7. Nach Restore aller ausgewählten ODER nach „Liste verwerfen": `MSM_PENDING_CLOUD_RESTORE=0` setzen, Banner verschwindet.

**Resource-Limits:** kommen aus dem Metadata-JSON. `public_bind_ip` wird **ignoriert** (würde bei neuem Host nicht passen). Ports: aus Metadata die Rolle, der konkrete Port wird via `port_allocation_service` frisch vergeben (gegen aktuelle Belegung auf dem neuen Host).

**Fehlerhandling pro Server:**
- Wenn `download` failt → Server wird nicht angelegt, Liste zeigt "Download fehlgeschlagen" + Grund (sanitized, kein Path-Leak), User kann Retry oder Skip.
- Wenn `extract` failt → Server-Row existiert, aber `status="error"`, `status_message="Wiederherstellung fehlgeschlagen"`, Liste zeigt das.
- Andere Server laufen unabhängig weiter.
- Bereits angelegte Server (mit Fehler) können in der Server-Liste einzeln nachgetestet / gelöscht werden.

**Resource-Limits:** kommen aus dem Metadata-JSON. `public_bind_ip` wird **ignoriert** (würde bei neuem Host nicht passen). Ports: aus Metadata die Rolle, der konkrete Port wird via `port_allocation_service` frisch vergeben (gegen aktuelle Belegung auf dem neuen Host).

### 3.8 install.sh — neue 5. Frage (mit Re-Install-Logik)

Frischer Install (Schritt 5/5 nach Postgres):
```
Schritt 5/5: Backup-Storage
  Wo sollen Backups gespeichert werden?
  1) Lokal (Default, keine Cloud)
  2) Cloud — S3-kompatibel (AWS, Hetzner S3, Cloudflare R2, Backblaze B2, MinIO, Wasabi)
  3) Hetzner Storage Box (SFTP)
  4) Dropbox
  5) Google Cloud Storage
  6) Microsoft Azure Blob Storage
  [1/2/3/4/5/6]:
```

Per-Provider-Folgefragen:
- **S3:** 5 Felder (Bucket, Region, Endpoint [leer = AWS-Default], Access-Key, Secret-Key [read -s])
- **SFTP:** 5 Felder (Host [z.B. u123456.your-storagebox.de], Port [default 22], Username, Passwort [read -s], Pfad-Prefix [z.B. /msm-backups])
- **Dropbox:** 3 Felder (App Key, App Secret [read -s], Refresh-Token [read -s]). Der User muss den Refresh-Token manuell einmalig via Dropbox-OAuth-`/oauth2/token`-Tool generieren (KISS: kein In-App-OAuth-Flow im Installer).
- **GCS:** 2 Felder (Bucket-Name, Service-Account-JSON-Pfad [z.B. /opt/msm/secrets/gcs-sa.json] — der User legt die Datei selbst dort ab, der Pfad wird ins `.env` geschrieben; Datei bleibt 0600 root-owned)
- **Azure:** 2 Felder (Storage-Account-Name, Connection-String [read -s])

Re-Install „Change"-Modus übernimmt das etablierte 1-Frage-„Backup-Storage ändern?"-Toggle (analog Postgres), gefolgt von Detailfragen nur bei Änderung. **Wichtig:** beim Wechsel von `local` auf Cloud wird `MSM_PENDING_AUTO_MIGRATION=1` in `.env` geschrieben → Backend-Startup triggert Auto-Migration. Beim Wechsel von Cloud auf `local` werden Cloud-Records NICHT zurückkopiert (Cloud-only-Mode, siehe 3.2). Beim Wechsel **zwischen Cloud-Providern** (`s3` → `gcs`) wird ein **Cloud-Provider-Migrations-Job** angeboten, der die Backups vom alten zum neuen Provider umkopiert (sequenziell, idempotent, abbrechbar) — das ist NICHT Auto-Migration, das ist Provider-Migration.

`load_current_env()` und `show_current_config()` werden um die neuen Variablen erweitert (Klartext nur für Endpoint/Region/Host, Access/Secret/Passwort/Refresh-Token/Connection-String als `***`).

`/opt/msm/.env` bekommt:
```
MSM_BACKUP_PROVIDER="local"   # oder "s3" | "sftp" | "dropbox" | "gcs" | "azure"
MSM_BACKUP_S3_BUCKET="..."
MSM_BACKUP_S3_REGION="..."
MSM_BACKUP_S3_ENDPOINT=""
MSM_BACKUP_S3_ACCESS_KEY="..."
MSM_BACKUP_S3_SECRET_KEY="..."
MSM_BACKUP_SFTP_HOST="..."
MSM_BACKUP_SFTP_PORT=22
MSM_BACKUP_SFTP_USER="..."
MSM_BACKUP_SFTP_PASSWORD="..."
MSM_BACKUP_SFTP_PATH="/msm-backups"
MSM_BACKUP_DROPBOX_APP_KEY="..."
MSM_BACKUP_DROPBOX_APP_SECRET="..."
MSM_BACKUP_DROPBOX_REFRESH_TOKEN="..."
MSM_BACKUP_GCS_BUCKET="..."
MSM_BACKUP_GCS_SA_FILE="/opt/msm/secrets/gcs-sa.json"
MSM_BACKUP_AZURE_ACCOUNT="..."
MSM_BACKUP_AZURE_CONNECTION_STRING="..."
MSM_BACKUP_ENCRYPTION_KEY="<base64 32 bytes>"  # generiert beim ersten Cloud-Enable
MSM_PENDING_CLOUD_RESTORE=0
MSM_PENDING_AUTO_MIGRATION=0
```

`MSM_PENDING_CLOUD_RESTORE` wird im Probe direkt nach `.env`-Write auf `1` gesetzt, falls Probe Treffer hat UND die lokale DB als leer erkannt wird. (Backend-Setup-Route setzt am Ende auf `0`.)

`MSM_PENDING_AUTO_MIGRATION` wird auf `1` gesetzt, wenn `MSM_BACKUP_PROVIDER` von `local` auf einen Cloud-Provider wechselt UND bestehende lokale Backups existieren. (Backend-Migration-Service setzt am Ende auf `0` und schreibt `state.cloud_migration_done=true`.)

### 3.9a Verschlüsselungs-Entscheidung (mit Begründung)

- **Algorithmus:** AES-256-GCM (AEAD), bereitgestellt von `cryptography.hazmat.primitives.ciphers.aead.AESGCM`.
- **Warum nicht Fernet:** Fernet ist ein Overkill-Format (Version-Prefix + HMAC + Timestamp) — wir brauchen nur AEAD, AES-256-GCM ist direkter und schneller.
- **Warum nicht age / GPG:** KISS, eine Lib weniger, `cryptography` ist eh schon im Dep-Tree.
- **Warum nicht S3-Server-Side-Encryption (SSE) allein:** schützt nicht vor kompromittierten S3-Credentials oder dem SFTP-User.
- **Wartung:** `cryptography` ist Python-SSF-lizenziert, breitester Maintainer-Stab, gepatched bei Heartbleed-Style-Issues innerhalb von Stunden.
- **Advisories:** keine offenen CVEs auf `AESGCM`-Pfad.
- **Exit-Plan:** kann 1:1 durch jede andere AEAD-Lib (z.B. `pyca/aegis` falls existent) ersetzt werden.

### 3.9 Dependency-Entscheidungen (mit Begründung)

**boto3** (S3-Provider):
- **Zweck:** S3-kompatibles Multipart-Upload/Download inkl. Progress-Callbacks.
- **Warum nicht aioboto3 / httpx / minio-py:** boto3 ist die am breitesten getestete S3-Library, spricht **alle** S3-kompatiblen Anbieter, hat stabile Multipart-API + Callback-Hook. Sync-API ist hier kein Problem, weil Backup-Operationen ohnehin nicht im Request-Thread laufen dürfen — `asyncio.to_thread` / `loop.run_in_executor` kapselt das. aioboto3 wäre zusätzliche Komplexität ohne Vorteil.
- **Wartung:** AWS-maintained, Releases monatlich.
- **Advisories:** Stand 2026-05 keine offenen relevanten CVEs (Verifikation im ADR).
- **Transitive Fläche:** moderat (botocore + jmespath + urllib3).
- **Kapselung:** komplett hinter `services/backup_provider/s3.py`.
- **Exit-Plan:** Library kann 1:1 durch `minio-py`, `aioboto3` oder `httpx` ersetzt werden, ohne den Rest zu berühren.

**paramiko** (SFTP-Provider für Hetzner Storage Box):
- **Zweck:** SFTP/SSH-Zugriff auf Hetzner Storage Box. Upload/Download via SFTP, Verzeichnis-Layout, Datei-Löschen.
- **Warum nicht pysftp:** pysftp ist unmaintained (letzter Release 2018), paramiko ist die darunterliegende Lib und aktiv gepflegt.
- **Warum nicht direkter ssh-CLI-Aufruf:** Klar gekapselter, testbarer Code ist paramiko vorzuziehen. KISS leidet nicht, der Adapter bleibt < 200 Zeilen.
- **Wartung:** aktiv, Python Software Foundation-lizenziert, breite Maintainer-Basis.
- **Advisories:** Stand 2026-05 keine offenen relevanten CVEs (Verifikation im ADR).
- **Transitive Fläche:** klein (bcrypt + pynacl + cryptography — `cryptography` ist eh schon drin).
- **Kapselung:** komplett hinter `services/backup_provider/sftp.py`.
- **Exit-Plan:** kann durch `asyncssh` (async) oder direkten `httpx`-basierten WebDAV-Adapter ersetzt werden, ohne den Rest zu berühren.

**dropbox** (Dropbox-SDK):
- **Zweck:** Native Dropbox-Uploads ohne S3-Kompatibilitäts-Layer. Auth via App-Credentials + Refresh-Token.
- **Warum nicht S3-Pattern (DatGateway o.Ä.):** Dropbox hat **keine** S3-kompatible API. Der einzige Weg ist das native SDK oder direktes HTTP-Implementieren der `content.dropboxapi.com` Endpoints. SDK ist klar vorzuziehen.
- **Wartung:** Dropbox-maintained, offizielles SDK, Releases regelmäßig.
- **Transitive Fläche:** klein (dropbox + stone-urllib + requests-tiny).
- **Kapselung:** komplett hinter `services/backup_provider/dropbox.py`.

**google-cloud-storage** (GCS-Native):
- **Zweck:** Native GCS-Uploads mit Service-Account-Auth.
- **Warum nicht S3-Compat via HMAC-Keys:** GCS unterstützt zwar S3-Compat, aber für eine native Lösung brauchen wir die GCS-Semantik (z. B. `KMS`-Verschlüsselung, IAM-Rollen). Native ist sauberer.
- **Wartung:** Google-maintained, das offizielle SDK.
- **Transitive Fläche:** moderat (google-cloud-core + google-api-core + grpcio + protobuf). Schwer, aber gut gepflegt.
- **Risiko:** grpcio ist eine größere native Lib. ADR dokumentiert dies; Abbhängigkeit wird sorgfältig gekapselt.
- **Kapselung:** komplett hinter `services/backup_provider/gcs.py`.

**azure-storage-blob** (Azure Blob):
- **Zweck:** Native Azure Blob-Uploads mit Connection-String-Auth.
- **Warum nicht S3-Compat:** Azure Blob hat zwar einen S3-Compat-Layer in einigen Konfigurationen, aber für native Nutzung ist das Azure-SDK einfacher.
- **Wartung:** Microsoft-maintained, offizielles SDK.
- **Transitive Fläche:** moderat (azure-core + msrest + cryptography — `cryptography` ist eh schon drin).
- **Kapselung:** komplett hinter `services/backup_provider/azure.py`.

Alle 5 Cloud-Provider-ADRs werden vor Implementation angelegt:
- `docs/adr/0007-backup-s3-library.md` (boto3)
- `docs/adr/0008-backup-sftp-library.md` (paramiko)
- `docs/adr/0009-backup-dropbox-library.md` (dropbox)
- `docs/adr/0010-backup-gcs-library.md` (google-cloud-storage)
- `docs/adr/0011-backup-azure-library.md` (azure-storage-blob)

### 3.10 Auto-Migration (lokal → Cloud, einmalig)

Wenn `MSM_BACKUP_PROVIDER` von `local` auf `s3` oder `sftp` wechselt (per Re-Install), läuft beim nächsten Backend-Start **einmalig** ein Migrations-Job im Hintergrund:

**Trigger:** Backend liest beim Startup die aktuelle Provider-Config UND vergleicht mit `state.cloud_migration_done` (ein Marker in `.msm/state.json` neben `.env`). Wenn `MSM_BACKUP_PROVIDER != "local"` UND `state.cloud_migration_done == false` UND es lokale Backup-Records (`provider="local"` oder `provider is null`) gibt → Job startet.

**Job-Ablauf (pro Backup, sequenziell, KISS — kein Parallel-Upload):**
1. Lokale `tar.gz` lesen (DB-Record hat `filename`).
2. Metadata-JSON aus DB (`metadata_json`) oder aus dem parallelen `*.meta.json` File laden.
3. Provider-Upload: `provider.upload(local_path, remote_key, progress_cb)`.
4. DB-Record updaten: `provider="s3"|"sftp"`, `remote_key=<neu>`, `filename=None` (lokaler Pfad nicht mehr relevant).
5. **Lokale Datei löschen** (erst nach bestätigtem Cloud-Upload).
6. Nächstes Backup.

**Sicherheit:**
- Idempotent: Re-Run nach Crash macht nur die noch-nicht-migrierten Backups (DB-Records mit `provider="local"`).
- Abbruchbar: User kann jederzeit via API abbrechen — dann bleiben alle noch nicht hochgeladenen Backups lokal, partial-migrierte Records werden sauber zurückgerollt.
- Kein Lock auf Backups während Migration (Restore/Manual-Backup für den Server, der gerade migriert wird, ist gesperrt — wir setzen `active_backup_status`).

**UI:**
- Auf dem Dashboard erscheint ein **zweiter Banner** (parallel zum CloudRestoreBanner): "X Backups werden in die Cloud migriert..." mit Live-Progress + Cancel-Button.
- Banner verschwindet, wenn Job fertig oder abgebrochen.
- Nach Abschluss setzt der Job `state.cloud_migration_done=true`.

**Edge Cases:**
- Migration schlägt bei Backup #3 fehl → #1, #2 sind in Cloud, #3 bleibt lokal, #4..N bleiben lokal. Beim nächsten Start wird Job wieder angeboten (idempotent).
- Cloud-Credentials falsch → Job stoppt sofort, klarer Fehlertext im Banner, User kann Credentials in `.env` fixen und `state.cloud_migration_done=false` manuell zurücksetzen oder einfach Banner re-triggern.
- Server wird während Migration gelöscht: sein Backup wird trotzdem migriert (es ist in der DB, auch wenn Cascade den Server wegblendet — nein, Backup-Cascade würde ihn killen; daher: Migration läuft VOR Cascade, oder wir nehmen gelöschte Server-Backups aus dem Migrations-Scope raus). **Entscheidung:** nur Backups zu **noch existierenden** Servern migrieren; gelöschte-Server-Backups bleiben, wo sie sind (KISS).

## 4. Architektur-Skizze (Text-Form)

```
[ User / Cron / GamePlugin.start ]
            │
            ▼
  run_backup(server_id, db)         ← SoT, services/backup_service.py
            │
            ├── tar.gz (lokal, /var/tmp/...)
            ├── metadata.json (lokal)
            ├── DB-Insert (Status: uploading)
            │
            ▼
  provider = get_provider(settings) ← services/backup_provider/__init__.py
            │
            ▼
  provider.upload(local, remote_key, progress_cb)   ← backup_provider/{local,s3}.py
            │                                            progress_cb → _active_backups[server_id]
            ▼
  provider.delete(old_keys)        ← cleanup_old_backups()
            │
            ▼
  [ Restore umgekehrt, mit extract + Port-Allokation ]
            │
            ▼
  [ Fresh-Install-Setup: probe → pending list → user picks → restore each ]
```

## 5. Betroffene Dateien (Plan)

**Backend (Änderung):**
- `backend/services/backup_service.py` — Run/Restore/Cleanup erweitern um Provider-Stage, Auto-Migration-Trigger
- `backend/services/backup_migration_service.py` (neu) — One-Shot-Migration (DB-Check, Sequenzieller Upload, Idempotenz, Abbruch-Flag)
- `backend/models/backup.py` — 3 neue Spalten (`provider`, `remote_key`, `metadata_json`)
- `backend/routers/backups.py` — Status um `phase`/`percent`/`bytes_*`; Restore liest Metadata
- `backend/routers/setup.py` (neu) — `GET /pending-restores`, `POST /restore-orphan/{idx}`, `GET /migration-status`, `POST /migration-cancel`, `POST /pending-restores/discard`
- `backend/services/backup_provider/__init__.py` — Factory + `get_provider()`
- `backend/services/backup_provider/base.py` — ABC + DTOs
- `backend/services/backup_provider/local.py` — Filesystem-Adapter
- `backend/services/backup_provider/s3.py` — boto3-Adapter
- `backend/services/backup_provider/sftp.py` — paramiko-Adapter (Hetzner Storage Box)
- `backend/services/backup_provider/dropbox.py` — dropbox-SDK-Adapter
- `backend/services/backup_provider/gcs.py` — google-cloud-storage-Adapter
- `backend/services/backup_provider/azure.py` — azure-storage-blob-Adapter
- `backend/services/backup_encryption.py` — AES-256-GCM-Wrapper (encode/decode-File)
- `backend/services/backup_migration_service.py` (neu) — One-Shot-Migration (lokal → Cloud, Cloud-Provider → Cloud-Provider, DB-Check, Sequenzieller Upload, Idempotenz, Abbruch-Flag)
- `backend/config.py` — neue Settings (`backup_provider`, `backup_s3_*`, `backup_sftp_*`, `backup_dropbox_*`, `backup_gcs_*`, `backup_azure_*`, `backup_encryption_key`, `pending_cloud_restore`)
- `backend/main.py` (oder `lifespan`) — Startup-Hook: liest `state.cloud_migration_done`, triggert Migration falls nötig

**Backend (Tests):**
- `backend/tests/test_backup_provider_local.py` (neu)
- `backend/tests/test_backup_provider_s3.py` (neu, moto-Mock)
- `backend/tests/test_backup_provider_sftp.py` (neu, paramiko-Mock)
- `backend/tests/test_backup_provider_dropbox.py` (neu, dropbox-SDK-Mock)
- `backend/tests/test_backup_provider_gcs.py` (neu, google-cloud-storage-Mock)
- `backend/tests/test_backup_provider_azure.py` (neu, azure-storage-blob-Mock)
- `backend/tests/test_backup_encryption.py` (neu) — AES-256-GCM Roundtrip, falscher Key schlägt fehl, Metadata bleibt klartext
- `backend/tests/test_backup_migration_service.py` (neu) — Idempotenz, Crash-Recovery, Abbruch, Cross-Cloud-Migration
- `backend/tests/test_backup_service.py` (erweitern: Provider-Stage mocked, Metadata-Persistenz, Retention über Provider, Encryption-Wrapper pro Provider)
- `backend/tests/test_setup_restore_orphan.py` (neu) — Pending-List, Per-Server-Fehler, Discard
- `backend/tests/test_setup_migration_banner.py` (neu)

**Install-Script:**
- `install.sh` — neuer Schritt 5/5 im Fresh-Flow, neuer Schritt 4/5 im Change-Flow, Probe nach `.env`-Write, `load_current_env` + `show_current_config` erweitern
- `install.sh` — SFTP-spezifische Fragen (Host, Port, User, Passwort, Pfad), `read -s` für Secret

**Frontend (UI):**
- `frontend/src/pages/Backups.tsx` — Progress-Bar + MB-Zähler
- `frontend/src/components/setup/CloudRestoreBanner.tsx` (neu) — Dashboard-Banner
- `frontend/src/components/setup/CloudRestoreWizard.tsx` (neu) — Liste + Live-Progress + Done
- `frontend/src/components/setup/CloudMigrationBanner.tsx` (neu) — zweiter Banner für Auto-Migration
- `frontend/src/api/client.ts` (oder Setup-API-Helper) — Endpoints für Pending-Restores + Migration-Status
- `frontend/src/pages/Dashboard.tsx` (oder AppShell) — Banner-Mount
- i18n: `frontend/src/locales/de.json` + `en.json` — neue Keys (`backups.uploading`, `setup.cloudRestore.*`, `setup.cloudMigration.*`)

**Doku / Meta:**
- `docs/adr/0007-backup-s3-library.md` (neu) — boto3-Entscheidung
- `docs/adr/0008-backup-sftp-library.md` (neu) — paramiko-Entscheidung
- `docs/adr/0009-backup-dropbox-library.md` (neu) — dropbox-Entscheidung
- `docs/adr/0010-backup-gcs-library.md` (neu) — google-cloud-storage-Entscheidung
- `docs/adr/0011-backup-azure-library.md` (neu) — azure-storage-blob-Entscheidung
- `docs/adr/0012-backup-encryption.md` (neu) — AES-256-GCM-Entscheidung + Key-Management
- `backend/requirements.txt` — 4 neue Deps: `boto3`, `paramiko`, `dropbox`, `google-cloud-storage`, `azure-storage-blob`
- `PATCHNOTES-ms-backup-cloud-redesign.md` (am Ende, wie üblich)

## 6. Verifikation (Definition of Done)

- [ ] **Unit-Tests** für alle 6 Provider-Adapter grün (lokal real, S3 via `moto`, SFTP via paramiko-Mock, Dropbox/GCS/Azure via SDK-Mock).
- [ ] **Encryption-Tests:** Roundtrip, falscher Key → fail, falsches Nonce → fail, Metadata bleibt klartext.
- [ ] **Integration-Tests** für `run_backup` mit gemocktem Provider, Retention über Provider, Metadata-Persistenz, Verschlüsselung im File.
- [ ] **Restore-Tests** decken alle 5 Cloud-Provider-Pfade ab (`cloud → local-tmp → decrypt → extract → ports neuzuteilen`).
- [ ] **Setup-Tests** decken Pending-Restore-Endpoint, Per-Server-Fehler-Propagation, Discard-Endpoint ab.
- [ ] **Auto-Migration-Tests:** Idempotenz nach Crash, Cancel mitten im Lauf, falsche Credentials → sauberer Abbruch, alle Backups migriert → `state.cloud_migration_done=true`, Cross-Cloud-Provider-Migration.
- [ ] Bestehende `test_backup_service.py`-Tests bleiben grün (Provider-Stage als MOCK-Default).
- [ ] **`npm run test` vollständig grün** (Frontend + Backend).
- [ ] **Manuelle Runtime** (gegen echte Provider-Buckets, lokale Test-Credentials):
  - Frischer Install mit Local-Provider → bestehende Backup-Listen + Restore funktionieren wie vorher.
  - Frischer Install mit jedem der 5 Cloud-Provider → Backup erscheint im Provider-Bucket, Restore lädt aus Cloud (Round-Trip verschlüsselt).
  - Frischer Install + vorhandene Cloud-Backups → Dashboard-Banner listet sie, Wizard legt Server mit Metadaten-Limits + frischen Ports an.
  - Bestehendes MSM → Provider auf Cloud wechseln → Auto-Migration läuft, Banner zeigt Progress, danach Cloud-only.
  - Cloud-Provider-Wechsel (z.B. S3 → GCS) → Cross-Cloud-Migration läuft.
- [ ] **Security-Check:**
  - Kein S3-Secret/Access-Key, SFTP-Passwort, Dropbox-Refresh-Token, GCS-SA-JSON, Azure-Connection-String in Logs / Toasts / Diffs / .env-Output (`read -s` + `***`).
  - Probe ruft **nur** `list_metadata` auf, kein Download ohne expliziten User-Klick.
  - Generische Fehlermeldungen bei Provider-Fehlern (kein Pfad-Leak, keine Token-Leak).
  - GCS-SA-JSON-Datei wird mit `chmod 600 msm:msm` angelegt, Pfad nur in `.env`.
  - `MSM_PENDING_CLOUD_RESTORE=0` wird nach Restore sauber zurückgesetzt.
  - Master-Encryption-Key landet nur in `.env` (chmod 600), kein Display im UI.
- [ ] **KISS-Check:** keine Manager-/Pipeline-Klassen, keine zukunftssicheren Abstraktionen, ein klarer tar-Stage-Encrypt-Provider-Stage-Retention-Pfad.
- [ ] **Restrisiken dokumentiert** im PATCHNOTES.

## 7. Annahmen & Grenzen

- **Annahme:** `panel_version` wird aus `/opt/msm/VERSION` gelesen, das install.sh beim Deploy schreibt.
- **Annahme:** Hetzner-Storage-Box-User ist ein **Sub-Account mit eigenem Home-Pfad**, nicht der Root-User der Box. Konfigurierbar per `MSM_BACKUP_SFTP_PATH`.
- **Grenze:** SFTP-Adapter unterstützt nur Passwort-Auth (kein SSH-Key für v1) — kann später ergänzt werden, da paramiko beides kann.
- **Grenze:** Auto-Migration läuft sequenziell (kein Parallel-Upload) — bei großen Backups kann das dauern, aber ist ressourcenschonend und einfacher zu implementieren. UI zeigt Live-Progress, User kann abbrechen.
- **Grenze:** Kein Self-Serve-Recovery des Master-Keys (nur Klartext-Warnung in install.sh). Wer den `.env` verliert, verliert alle Cloud-Backups.

## 8. Finale Entscheidungen (geklärt)

1. **Provider-Scope v1:** 5 Cloud-Provider + 1 lokal: `local` + `s3` (boto3) + `sftp` (paramiko) + `dropbox` + `gcs` + `azure`. Alle hinter demselben Interface.
2. **Storage-Modus:** Cloud-only. Keine lokale Kopie.
3. **Restore-UX:** Dashboard-Banner nach Login, kein blockierender Wizard.
4. **Migration:** Auto-Migration einmalig beim ersten Cloud-Enable (lokal → Cloud), idempotent, UI-Banner, abbrechbar. **Cross-Cloud-Provider-Migration** (Cloud A → Cloud B) wird beim Re-Install angeboten.
5. **Verschlüsselung:** AES-256-GCM client-seitig, Master-Key in `.env`, **mandatory** für v1.

---

*Plan wartet auf Go zur Implementation. Reihenfolge: 1) Provider-Interfaces + lokaler Provider + Encryption-Wrapper, 2) S3-Provider, 3) SFTP-Provider, 4) Dropbox-Provider, 5) GCS-Provider, 6) Azure-Provider, 7) Backup-Service-Refactor, 8) install.sh, 9) Auto-Migration + Cross-Cloud-Migration-Service, 10) Setup-Routes + Wizard-Endpoint, 11) Frontend-Progress, 12) Frontend-Wizard + Banners + Migration-Banner, 13) i18n, 14) PATCHNOTES.*
