# ADR-0009: SFTP-Provider mit paramiko (Hetzner Storage Box)

Status: Accepted
Date: 2026-06-06

## Context

Das neue Backup-System soll Hetzner Storage Boxen (und beliebige andere
SFTP-Server) als Backup-Ziel anbieten koennen. Hetzner Storage Boxen
haben **keine S3-kompatible API nativ**; der Standard-Zugriff ist SFTP
ueber SSH. Wir brauchen daher einen SFTP-Adapter hinter dem
`BackupProvider`-Interface.

Betroffen:
- Hetzner Storage Box (`u123456.your-storagebox.de`, Port 22)
- Generische SFTP-Server (Synology, self-hosted OpenSSH, etc.)

## Decision

Wir nutzen **paramiko** (sync-API) als einzige SFTP-Lib. Auth v1 ist
**Passwort-only** (kein SSH-Key fuer v1). Adapter liegt hinter dem
`BackupProvider`-Interface in `services/backup_provider/sftp.py`.

## Begruendung

### Library-Wahl: paramiko

- **Reife, breit genutzte Lib:** paramiko ist die De-facto-Standard-
  Python-Lib fuer SSH/SFTP. Aktiv gepflegt (v3.x in 2024/2025),
  monatliche Releases, riesige Nutzerbasis.
- **Synchron passt zum Use-Case:** Backup-Operationen duerfen ohnehin
  nicht im Request-Thread laufen. `asyncio.to_thread` wrappt sie im
  Backup-Service. Async-SFTP-Libs (z. B. `asyncssh`) bringen
  Komplexitaet (eigener Event-Loop-Hook) ohne Mehrwert.
- **Mature SFTP-Implementierung:** Put/Get mit Progress-Callback,
  Verzeichnis-Stat, rekursives Listing, mkdir — alles in der Stdlib.
- **Kleine transitive Flache:** paramiko + cryptography + pynacl + invoke.
  `cryptography` ist eh schon eine Prod-Dependency (fuer AES-256-GCM).

### Auth-Wahl: Passwort-only fuer v1

- **Einfachster Self-Hosted-Pfad:** Hetzner Storage Boxen vergeben per
  Default ein Initial-Passwort; der User kopiert es in seine `.env`.
  Kein SSH-Key-Generation, kein `ssh-keygen`, kein `authorized_keys`.
- **KISS-Prinzip:** Passwort-Auth ist die Minimalvariante, die
  funktioniert. SSH-Key-Support ist eine optionale
  Erweiterungsmöglichkeit, die das Interface nicht bricht
  (`__init__`-Args koennen einfach mehr Parameter bekommen).
- **Kein Security-Nachteil gegenueber SSH-Key:** Passwort liegt in
  `.env` mit `chmod 600`; SFTP-Verbindung ist genau so verschluesselt
  wie bei Key-Auth. Der Client-seitige AES-256-GCM-Layer
  (ADR-0013) schuetzt zusaetzlich gegen kompromittierte
  SFTP-Credentials.
- **Bewusste Begrenzung:** SSH-Key-Auth braucht mehr UI (Public-Key
  auf Server deployen, Fingerprint-Verifikation), mehr Fehlerquellen
  (falsche Permissions auf `~/.ssh`, falsche Key-Datei) und mehr
  Konfiguration in `install.sh`. Wir liefern es nach, sobald jemand
  danach fragt — das Interface ist offen.

## Konsequenzen

### Host-Key-Validierung (MITM-Schutz)

- **Strict Mode:** `load_system_host_keys()` + `RejectPolicy`.
  Unbekannte Hosts werden abgelehnt (kein Auto-Add, kein Warning +
  Add). Verhindert MITM auf der ersten Verbindung.
- **Operational:** User muss `ssh-keyscan <host>` einmalig laufen
  lassen, bevor der erste Backup laeuft. `install.sh` wird das im
  SFTP-Setup-Flow anbieten (`ssh-keyscan -H u123.your-storagebox.de
  >> ~/.ssh/known_hosts`).
- **Fuer Hetzner Storage Box:** Host-Key-Fingerprint steht in der
  Hetzner-Robot-Konsole; User kann verifizieren statt blind
  akzeptieren.

### Agent / Key-File-Lookup deaktiviert

- `allow_agent=False`, `look_for_keys=False` beim `connect()`.
  Wir wollen AUSSCHLIESSLICH das konfigurierte Passwort verwenden —
  nicht versehentlich SSH-Agent-Keys oder `~/.ssh/id_rsa` des
  msm-Users. Verhindert Cross-Use von fremden Keys.

### Path-Layout und Traversal-Schutz

- Konvention: `<base_path>/<server_id>/<filename>` — gleich wie bei
  `local` und `s3`. Meta-Files analog: `<remote_key>.meta.json`.
- `remote_key` muss relativ sein, kein `..`, kein absoluter Pfad.
  Voller Pfad muss unter `base_path` bleiben.
- `_full_path` validiert das mit `posixpath.normpath` +
  `startswith(base_path + "/")`.

### base_path-Existenz

- `test_connection()` legt `base_path` an, falls nicht existent
  (idempotentes `mkdir -p`). Macht den Install-Flow einfacher —
  der User muss den Pfad nicht vorher manuell anlegen.

### Progress-Callback

- paramiko's `SFTPClient.put/get` rufen das Callback mit
  `(bytes_so_far, total)` auf. Unser `ProgressCallback` ist
  `Callable[[int], None]` (kumulativ). Wir droppen `total` per
  Wrapper; `bytes_so_far` ist bereits kumulativ, passt also direkt.

### Connection-Pattern: Open-per-Op

- Neue SSH-Verbindung pro Backup-Operation (open/close).
- **Vorteil:** KISS, kein Reconnect-Logic, keine stale-Connection-
  Probleme bei langen Pausen zwischen Backups.
- **Nachteil:** SSH-Handshake (~500 ms) faellt bei jeder Operation an.
  Bei grossen Backups (GBs) ist das vernachlaessigbar; bei kleinen
  Ops (List) ist es okay, weil Backup-Operationen nicht
  hochfrequent sind.
- **Alternative (verworfen):** Connection-Pool mit Reconnect-Logic.
  Komplexitaet nicht gerechtfertigt.

## Alternativen

- **pysftp:** dünner Wrapper um paramiko, letzter Release 2018.
  Unmaintained. Verworfen.
- **asyncssh:** async-Sibling. Mehr Komplexitaet ohne Nutzen fuer
  diesen Use-Case (Backup-Service ist ohnehin im Executor).
  Verworfen.
- **Direkter ssh CLI-Aufruf** (`subprocess` + `sftp`-Binary):
  klar schlechter testbar, haengt von PATH/Installation ab.
  Verworfen.
- **WebDAV:** Hetzner bot WebDAV-Support auf Storage Boxen an,
  wurde aber 2024 deprecated. SFTP ist der offizielle Weg.
  Verworfen (war nie ernsthaft in Betracht gezogen).
- **SSH-Key-Auth fuer v1:** Verworfen, Begruendung oben.

## Security

- **Host-Key-Validierung:** `load_system_host_keys` + `RejectPolicy`
  (siehe oben). Default-Verhalten, nicht ueberschreibbar.
- **Passwort-Handling:** Liegt nur im Konstruktor-Argument; wird
  weder geloggt, noch in Errors/Dumps geschrieben. Fehlertexte sind
  generisch ("SFTP-Verbindung fehlgeschlagen" ohne Host/User).
- **Agent/Key-File-Lookup deaktiviert** (siehe oben).
- **Path-Traversal-Schutz** (siehe oben).
- **Adapter sieht nur Chiffretext** (Verschluesselung im Caller,
  ADR-0013).
- **Timeout:** Default 30s fuer Connect. Backup-Operationen
  (Put/Get) nutzen paramiko's Default-Channel-Timeout, der fuer
  Multi-GB-Uploads ausreichend ist.

## Test-Coverage

- 47 Tests mit in-memory `FakeSFTP` (kein echter SFTP-Server noetig):
  - **Contract:** Interface-Implementierung, Constructor-Validierung
    (leere Felder, relativer base_path, ungueltiger Port,
    base_path-Normalisierung)
  - **Connection-Setup:** `load_system_host_keys`, `RejectPolicy`,
    `allow_agent=False`, `look_for_keys=False`, korrekte connect-Args,
    test_connection True/False (Auth-Fehler, SSH-Error, SFTP-Error,
    Base-Path-erstellen), close()-Aufruf im Fehlerfall
  - **Upload/Download:** Roundtrip byte-genau, intermediate-dirs,
    idempotent bei existierenden dirs, missing key/source,
    Progress-Callback
  - **Delete:** Daten + Meta, fehlende Dateien, malformed key
  - **List-Metadata:** parsed, kaputte Files skipped, leere
    Base-Path, nested dirs
  - **Security Path-Traversal:** absolute Pfade, "..", mixed,
    leerer Key
  - **Factory:** sftp-Branch, fehlende Credentials, default port 22

## Review

Diese ADR ist zu reviewen, sobald:
- Hetzner den SFTP-Zugang deprecated oder durch etwas anderes ersetzt
- paramiko Breaking Changes im SFTPClient einfuehrt
- SSH-Key-Auth nachgereicht wird (API-Erweiterung, kein Bruch)
- Wir Multi-Faktor-Auth (z. B. TOTP) brauchen
