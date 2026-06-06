# ADR-0008: S3-Provider mit boto3

Status: Accepted
Date: 2026-06-06

## Context

Das neue Backup-System muss mit S3-kompatiblen Cloud-Storage-Anbietern
sprechen, ohne dass wir fuer jeden Anbieter eine eigene Lib pflegen.
Betroffen: AWS S3, Hetzner Object Storage, Cloudflare R2, Backblaze B2
(per S3-Endpoint), MinIO, Wasabi, DigitalOcean Spaces.

## Decision

Wir nutzen **boto3** (sync-API) als einzige S3-Lib. Adapter liegt hinter
dem `BackupProvider`-Interface in `services/backup_provider/s3.py`.

## Begruendung

- **Eine Lib, viele Anbieter:** boto3 ist die kanonische S3-Lib, die
  jeder S3-kompatible Anbieter unterstuetzt. Backblaze B2, R2 und
  Wasabi dokumentieren explizit boto3 als empfohlenen Client.
- **Sync-API ist hier kein Nachteil:** Backup-Operationen duerfen ohnehin
  nicht im Request-Thread laufen. `asyncio.to_thread` wrappt sie im
  Backup-Service. Async-S3-Libs (aioboto3) bringen Komplexitaet
  (eigener Event-Loop-Hook) ohne Mehrwert fuer diesen Use-Case.
- **Multipart + Progress-Callback out of the box:** boto3's
  `s3.transfer.TransferConfig` macht Multipart-Threshold (5 MB) und
  progress reporting trivial.
- **Mature, breit maintained:** AWS-maintained, monatliche Releases,
  riesige Maintainer-Basis.

## Konsequenzen

- **Checksum-Trailer deaktiviert:** `request_checksum_calculation="when_required"`.
  Spart CPU/Netzwerk-Overhead (default seit boto3 1.34 fuegt
  CRC32-Trailer zu jedem PUT hinzu). Echt-S3 validiert Checks
  serverseitig trotzdem, Funktionalitaet bleibt erhalten.
- **read_timeout=300s** fuer grosse Downloads (mehrere GB dauern).
  Connect-Timeout 30s.
- **Auto-Retries:** `max_attempts=3, mode="standard"` (boto3-Default).
  Kein eigener Retry-Loop noetig.
- **Transitive Flache:** boto3 + botocore + jmespath + urllib3 + python-dateutil.
  Keine surprises, alles bewahrt und gut gewartet.

## Alternativen

- **aioboto3:** async-Sibling von boto3. Wuerde Multipart + Progress
  via asyncio-Tasks ermoeglichen, aber der Backup-Service ist ohnehin
  in `asyncio.to_thread` gewrappt. Mehr Komplexitaet, kein Nutzen.
  Verworfen.
- **minio-py:** speziell fuer MinIO. AWS-S3 nicht offiziell supported.
  Verworfen.
- **Direktes httpx:** ~400 LoC, AWS-SigV4-Signing, Multipart von Hand.
  KISS-Verstoss. Verworfen.

## Security

- Credentials kommen aus `.env` (`MSM_BACKUP_S3_ACCESS_KEY` /
  `MSM_BACKUP_S3_SECRET_KEY`), `chmod 600`. Kein AWS-IAM-Role-Pattern,
  weil das auf einem einfachen Self-Hosted-Server die Komplexitaet nicht
  rechtfertigt.
- Adapter loggt niemals den Key oder Bucket-Name im Fehlerfall
  (generische Fehlermeldung + generischer Log-Type).
- Provider sieht nur Chiffretext (Verschluesselung im Caller).

## Test-Coverage

- `moto[s3]>=5.0` als dev-dependency mockt die S3-API lokal
- 23 Tests: Upload/Download Roundtrip, Idempotente delete, List-Metadata
  (mit kaputten Files), Progress-Callback, Custom-Endpoint (Hetzner),
  Factory-Wiring, Constructor-Validierung
- Progress-Callback-Tests sind pragmatisch: pruefen "wurde aufgerufen
  mit Wert > 0", nicht "exakter Final-Wert" — moto's Multipart-Callback
  ist nicht stabil. Echte S3 in Prod ist es.

## Review

Diese ADR ist zu reviewen, sobald:
- boto3 ein Breaking Change im Transfer-Manager macht
- Wir KMS-Encryption (aws:kms) serverseitig aktivieren wollen
- AWS einen neuen Auth-Flow (z. B. SSO-Mandatory) einfuehrt
