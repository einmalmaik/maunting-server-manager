# ADR-0013: Client-seitige Backup-Verschluesselung mit AES-256-GCM

Status: Accepted
Date: 2026-06-06

## Context

Das Backup-System soll Cloud-Storage nutzen, damit ein Komplett-Verlust
des Root-Servers ueberlebbar wird. Cloud-Compromise ist ein reales
Szenario (S3-Bucket-Policy-Fehler, SFTP-Server-Breach, fauler Admin,
gestohlene Credentials). Die tar.gz-Dateien enthalten:
- Savegames (kein Hochsicherheits-Risiko, aber Reputations-Schaden)
- Server-Configs mit RCON-Passwoertern und Steam-Workshop-Keys
- Mit etwas Pech inkludierte Datenbank-Dumps

Daher: **Client-seitige Verschluesselung ist mandatory.** Ein
Provider-Compromise darf dem Angreifer keine lesbaren Backups liefern.

## Decision

Wir verschluesseln jede tar.gz-Datei **vor** dem Upload mit **AES-256-GCM**
(authenticated encryption) und entschluesseln sie **nach** dem Download.
Das passiert im Backup-Service (`services/backup_service.py`), nicht im
Provider-Adapter. So bleibt der Provider-Adapter klein und kennt keine
Krypto.

Algorithmus: `cryptography.hazmat.primitives.ciphers.aead.AESGCM`.

File-Format:
```
[ 1 byte version=0x01 ][ 12 byte nonce ][ ciphertext + 16 byte tag ]
```

Schluessel-Management:
- 32-Byte-Master-Key, generiert via `secrets.token_urlsafe(32)`,
  base64-kodiert, gespeichert in `.env` als `MSM_BACKUP_ENCRYPTION_KEY`.
- `.env` hat bereits `chmod 600` (heutiges Muster).
- Key wird **nicht** von `SECRET_KEY` abgeleitet (Rotation-Resilience).
- Bei Restore: Key kommt aus `.env`, entschluesselt wird lokal.
- **Wer `.env` verliert, verliert alle Cloud-Backups.** Wird in
  install.sh-Output und PATCHNOTES explizit kommuniziert.

## Begruendung

- **AES-256-GCM** ist der Industrie-Standard fuer authentifizierte
  Verschluesselung. AEAD = Confidentiality + Integrity in einer
  Operation. 256-Bit-Key gilt Stand 2026 als ausreichend gegen
  klassische und (mit den richtigen Annahmen) Quanten-Angriffe.
- **Warum nicht Fernet?** Fernet ist Overkill: Version-Prefix + HMAC +
  Timestamp sind unnoetig. AES-256-GCM ist direkter, schneller, und
  `cryptography` exponiert es als pure Primitive.
- **Warum nicht age / GPG?** `cryptography` ist bereits im Dep-Tree
  (`python-jose[cryptography]`, `argon2`); eine zusaetzliche Lib waere
  reiner Supply-Chain-Risiko ohne Nutzen.
- **Warum nicht SSE (Server-Side-Encryption) allein?** SSE hilft nicht
  gegen kompromittierte S3-Credentials. Client-side loest das Problem
  tatsaechlich.
- **KISS:** ~110 Zeilen Code in `backup_encryption.py`, kein Keyring,
  keine Lib, keine Config-Dateien ausser `.env`. Wer den Code liest,
  sieht in 5 Minuten was passiert.

## Konsequenzen

- **Master-Key in `.env`:** wenn der User `.env` verliert (Re-Install
  ohne Backup, Disk-Crash ohne Backup), sind alle Cloud-Backups
  unlesbar. Mitigation: README + install.sh-Output sagen das klar.
- **Kein Key-Rotation-UI:** Schluessel-Wechsel wuerde bedeuten, dass
  alle alten Backups neu verschluesselt werden muessten. Out of Scope.
- **Master-Key nicht im Backup:** die `.env` wird **nicht** mitgesichert
  (sonst waere der Kreis rund und Klartext-Backups waeren trivial).
- **Performance:** AES-256-GCM auf moderner Hardware: mehrere GB/s.
  Kein Flaschenhals im Vergleich zu Netzwerk-Upload.
- **Test-Coverage:** Roundtrip, falscher Key, Tamper-Detection, falsches
  Format, falsche Laenge, Cleanup bei Write-Failure. Negativ-Tests sind
  explizit mit drin (siehe `tests/test_backup_encryption.py`).

## Alternativen

- **Fernet:** Overhead ohne Mehrwert. Verworfen.
- **age:** gute Lib, aber extra Dep. Verworfen.
- **GPG:** alt, schwere Toolchain, overkill. Verworfen.
- **AES-256-CBC + HMAC-SHA256 (eigenbau):** explicit verboten per
  AGENTS.md §4 (keine eigene Krypto). Verworfen.
- **Provider-Side-Encryption (SSE / Azure SSE / GCS CMEK):** ergaenzt
  client-side, ersetzt es aber nicht. Beide koexistieren.

## Review

Diese ADR ist zu reviewen, sobald:
- Wir > 1000 Backups pro Server haben und die File-Format-Migration
  von 0x01 auf 0x02 ansteht
- Quanten-Computer eine reale Bedrohung fuer AES-256 werden
- Wir Multi-Tenant-Backup-Sharing brauchen (Key pro Empfanger)
