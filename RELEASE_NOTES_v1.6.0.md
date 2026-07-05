# MSM v1.6.0 — Backup-System (M1-M4) + Recovery App + Auth-Setup-Recovery

Großes Release mit zwei großen Features, einer generischen Auth-Recovery, mehreren P1-Fixes
und einer kompletten Standalone-Desktop-App für Backup-Entschlüsselung.

## Highlights

### Backup-System M1: Lokal verschlüsselte Backups mit optionalem S3-Off-Site-Upload
Server-Backups werden jetzt standardmäßig mit dem Backup-Passwort via DIS zu `.enc`-Dateien
verschlüsselt (AES-256-GCM). S3-Upload ist Best-Effort: bei S3- oder DIS-Ausfall bleibt das
lokale Backup erhalten. Panel hat eine Cloud-Status-Anzeige pro Backup, Restore von Cloud ist
möglich, Retention via Scheduler.

### Backup-System M3: Panel-Backups (Self-Backup des Panels)
Das MSM-Panel kann jetzt sich selbst backupen — Datenbank-Dump, Konfiguration und Secrets
(DIS-verschlüsselt). Vorbereiten + Restore aus dem Panel heraus. Retention-basiertes Cleanup.

### Recovery App: Tauri v2 Standalone-Desktop-App für `.enc`-Entschlüsselung
Neue Subapp `recovery/` (Tauri v2 + React + Vite + TypeScript). Liest MSM-Backup `.enc`-Dateien,
entschlüsselt sie offline via DIS-Streaming-Rust-Crate, zeigt File-Tree und Preview, erlaubt
Extrahieren einzelner Files. **Komplett offline**, kein Server nötig — nutzt die gleiche
DIS-Encryption wie das Panel. Build via GitHub Actions: `.github/workflows/recovery-release.yml`
erzeugt Windows-Installer + Linux-AppImage bei jedem `recovery-v*`-Tag.

### Auth-Setup-Recovery (generisch, nicht Hytale-spezifisch)
Game-Server-Container, die interaktive OAuth-Flows brauchen (z.B. Hytale, andere OAuth-Spiele),
wurden bisher beim Token-Expiry mit kryptischem Container-Exit "failed" behandelt. Jetzt:

- Beim Container-Start erkennt MSM Log-Pattern (`oauth2: invalid_grant`, `please visit the URL`,
  `authorization code:`, `could not get signed URL`) automatisch, dass ein Auth-Flow nötig ist.
- Credentials werden ins `.bak` verschoben (oder passende Datei-Pattern), Container wird im
  TTY-Modus neu gestartet (`tty=True` opt-in auf `run_container()`).
- `Server.auth_required` Flag wird gesetzt → Panel rendert einen **AuthSetupBanner** mit
  Anleitung ("URL in Console öffnen").
- Container streamt Auth-URL ins Live-Console (URL_RE macht sie automatisch klickbar).
- Nach erfolgreichem Browser-Auth schreibt der Container neue Credentials → MSM erkennt
  sie via File-Watch → stoppt TTY-Container, restartet normal, Banner verschwindet.
- Generisch: kein Game-Type-Hardcode, funktioniert für jedes OAuth-Pattern.

### Admin: User-Delete FK-Cleanup
`DELETE /api/admin/users/{id}` schlug mit `ForeignKeyViolation` fehl, wenn der User
`audit_logs`/`refresh_tokens`/`jwt_blacklist`/`backup_codes`-Einträge hatte. Cleanup vor dem
User-Delete: Audit-Logs auf `user_id=NULL` (Forensik bleibt), die anderen drei werden gelöscht.

### Post-Deploy Hook: `scripts/sync-venv.sh`
Idempotenter Wrapper um `pip install -r requirements.txt`. Verhindert `ModuleNotFoundError`
nach Code-Pushes, die neue Pakete einführen, ohne dass `update.sh` zwingend läuft.

## Geänderte Bereiche

### Backend
- `services/auth_setup_service.py` (neu): Detector, Credential-Mover, Wait-Watcher, Recovery-Orchestrator
- `services/server_lifecycle_service.py`: Recovery-Thread in `_run_start` eingehängt
- `services/docker_service.py`: opt-in `tty=True` Parameter auf `run_container()`
- `services/backup_orchestrator.py`: encrypted tar.gz → optional S3 upload (M1)
- `services/panel_backup_service.py`: Self-Backup Scheduler + Retention (M3)
- `services/backup_crypto_service.py`: DIS-Streaming-Verschlüsselung
- `services/s3_service.py` + `boto3==1.43.40`: S3-Upload-Provider
- `services/backup_service.py`: encrypted tar.gz Support, P1-Fixes für DB-Dump/Restore
- `models/server.py`: `auth_required` Flag für Auth-Recovery
- `models/backup.py`: S3-Felder (`s3_key`, `s3_bucket`, `encrypted`)
- `models/panel_backup.py` (neu): Panel-Self-Backup Model
- `routers/auth.py`, `routers/backups.py`, `routers/panel_backups.py`, `routers/servers.py`:
  Auth-Banner, Panel-Backup-Routes, Cancel-Endpoint
- `main.py`: Schema-Migrationen für `backups.s3_key/s3_bucket/encrypted` und
  `servers.auth_required`
- `scripts/sync-venv.sh` (neu): Post-Deploy pip-Sync

### Frontend
- `components/server/AuthSetupBanner.tsx` (neu): Auth-Recovery-Banner mit Cancel-Button
- `pages/ServerDetail.tsx`: Banner-Mount unter dem Header
- `pages/Backups.tsx`: S3-Cloud-Status, Upload-to-Cloud, Restore-from-Cloud
- `pages/PanelBackups.tsx` (neu): Self-Backup Page mit List, Create, Settings, Delete
- `components/server/PanelBackups.tsx` (neu): Restore-Vorbereiten Modal
- `i18n` (de/en): Keys für `server.authSetup.*`

### Recovery App (neu)
- `recovery/` Subapp: Tauri v2 + React + TypeScript + DIS-Streaming-Rust-Crate
- Design-DNA-konform (MauntingStudios dunkel, HSL-Tokens, Inter)
- File-Tree, Save-Button, Preview für `.enc`-Entschlüsselung
- i18n: de + en
- Offline, kein Server nötig

### Build & Deploy
- `.github/workflows/recovery-release.yml` (neu): Windows + Linux Tauri-Builds,
  triggered by `recovery-v*` Tags oder `workflow_dispatch`

## Sicherheit & Privacy

- **Zero-Knowledge**: Backups werden mit DIS-derived Key aus User-Passwort verschlüsselt.
  S3-Upload nutzt denselben verschlüsselten Bytestream (kein Klartext auf S3).
- **Key-Lifecycle**: DIS-Keys werden in try/finally invalidiert, auch bei Fehlern.
- **Keine Secrets in Logs**: Hostnames, Pfade, Tokens werden im Log-Output redacted.
- **Auth-Recovery ohne Datenleak**: `auth_setup_service` loggt Container-Output nur auf
  Pattern-Match, nicht im Klartext.

## Bug Fixes

- `fix(backup)`: fehlende Schema-Migration für `backups.s3_key/s3_bucket/encrypted`
  (verhinderte jeden Backup-Endpoint-Call)
- `fix(admin)`: User-Delete FK-Violation auf audit_logs/refresh_tokens/jwt_blacklist/backup_codes
- `fix(gitignore)`: Merge remote cleanup + Restore `*.db` rule + untrack 433 versehentlich
  getrackte `.cache/pip`-Files
- `fix(privacy)`: encrypted S3 storage zero-knowledge emphasis
- `fix(decrypt-stream)`: stream frame-by-frame to prevent OOM (VAL-FIX-011)
- `fix(restore)`: use systemctl (not --user) for msm-panel.service
- `fix(backup)`: DB dump/restore P1 fixes (VAL-FIX-007/008/009)
- `fix(backups)`: replace invalid CSS tokens with status-success design tokens

## Breaking Changes

Keine. Alle Änderungen sind additiv. Bestehende lokale Backups funktionieren weiter
(Backup-Passwort kann nachträglich gesetzt werden — bestehende Backups bleiben als
Plaintext-tar.gz lesbar).

## Upgrade-Hinweise

```bash
# 1. Pull
cd /opt/msm && git pull

# 2. venv synchronisieren (neue Pakete: boto3, moto, ...)
sudo bash scripts/sync-venv.sh

# 3. Panel restarten (Migrationen laufen automatisch)
sudo systemctl restart msm-panel
```

Wer die Recovery-App testen will:

```bash
# Trigger Recovery-App Build via GitHub Actions
gh workflow run recovery-release.yml --ref main -f tag=recovery-v0.1.0
```

## Danksagung

Danke an alle Tester, die die Backup-System M1-M4 Phasen durchlaufen haben und Feedback
zu Encryption-Flow, S3-Upload und Recovery-App UX gegeben haben.

— Maunting Studios