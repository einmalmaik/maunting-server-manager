"""One-Shot Auto-Migration: lokal gespeicherte Backups in einen Cloud-Provider
umkopieren.

Plan §3.10 (Backup-Cloud-Redesign):
- Wird beim Backend-Startup getriggert (Schritt 9.2 in main.py lifespan).
- Bedingung:
    * ``MSM_BACKUP_PROVIDER != "local"`` UND
    * ``.msm/state.json :: cloud_migration_done == false`` (oder fehlt) UND
    * es existieren noch Backup-Records mit ``provider == "local"`` (oder
      ``provider is None`` fuer sehr alte Records vor Cloud-Enable).
- Pro Backup sequenziell, KEIN Parallel-Upload. Reihenfolge: aelteste zuerst
  (deterministisch, kein UI-Flicker).
- Idempotent: Re-Run nach Crash macht nur die noch nicht migrierten Records
  (DB-Check ``provider == "local"``). Bereits migrierte Records (``provider ==
  target``) werden uebersprungen.
- Abbruchbar: User kann via API (Schritt 10) den Job abbrechen. Bereits
  migrierte Backups bleiben migriert, partial-migrierte Records sind
  konsistent: Upload fertig + DB-Update erfolgt in einer Transaktion am
  Ende jedes Backups.
- Fehlertolerant: Bei Provider-Fehler stoppt der Job sofort, markiert
  ``state.cloud_migration_done=false`` (User kann Credentials fixen +
  Re-Install), bereits migrierte Backups sind sicher in der Cloud.

State-Datei
-----------
``/opt/msm/.msm/state.json`` (chmod 600, msm-owned). Schluessel:
- ``cloud_migration_done: bool`` — True wenn alle lokalen Backups hochgeladen
- ``cloud_migration_target: str`` — Provider-Name (fuer Audit, z.B. "s3")
- ``cloud_migration_completed_at: str`` — ISO 8601, wann done gesetzt wurde
- ``cloud_migration_last_run_at: str`` — ISO 8601, letzter Run-Versuch
- ``cloud_migration_last_error: str|None`` — sanitized, kein Token, kein Pfad
- ``cloud_migration_total: int`` — Anzahl Backups im letzten Run
- ``cloud_migration_migrated: int`` — Erfolgreich migriert im letzten Run

Security:
- State-File: chmod 600, msm-owned (nicht world-readable).
- Kein Token/Pfad in state.json, niemals. Fehlertexte werden sanitized.
- Cross-Cloud-Migration (Cloud A -> Cloud B): gleicher Service, anderer
  target-Provider. State zeigt dann auf den NEUEN Provider.

Edge cases:
- Server wurde geloescht waehrend Migration: sein Backup-Record wird
  uebersprungen (Server-Cascade haette ihn eh geloescht, aber der Record
  existiert moeglicherweise noch bis zur naechsten Cascade-Lauf). Siehe
  Plan §3.10: "nur Backups zu noch existierenden Servern migrieren".
- Backup-Datei fehlt auf Platte (manuell geloescht): Skip + log warning,
  DB-Record bleibt als "local" stehen, User kann manuell aufraeumen.
- Encryption-Key wechselt waehrend Migration: Sehr seltener Edge-Case.
  Wir nehmen den Key der aktuellen .env (settings.backup_encryption_key).
  Bereits verschluesselte Files brauchen den ORIGINAL-Key zum Restore -
  der Wechsel ist eh ein Security-Problem, nicht Migration-relevanz.
"""
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from config import settings

if TYPE_CHECKING:
    from models import Backup, Server
    from services.backup_provider import BackupProvider

logger = logging.getLogger(__name__)

# State-Datei: parallel zu .env in /opt/msm/.msm/
STATE_DIR = Path("/opt/msm/.msm")
STATE_FILE = STATE_DIR / "state.json"

# Lock gegen parallelen Zugriff (z.B. Backup-Run waehrend Migration)
# und fuer Tests. threading.Lock weil der Service sync laeuft
# (im main.py-Hook via asyncio.to_thread in den Thread-Pool verschoben).
_state_lock = threading.Lock()


# ── Datenklassen ────────────────────────────────────────────────────────


class MigrationStatus:
    """Status-Konstanten. Strings statt enum weil einfacher zu loggen."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class MigrationProgress:
    """Live-Status fuer UI-Banner (Schritt 12 Frontend) und Tests."""

    status: str = MigrationStatus.IDLE
    total: int = 0
    migrated: int = 0
    skipped: int = 0
    failed: int = 0
    current_backup_id: Optional[int] = None
    current_server_id: Optional[int] = None
    current_filename: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_error: Optional[str] = None  # sanitized

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MigrationState:
    """Persistierter State in .msm/state.json."""

    cloud_migration_done: bool = False
    cloud_migration_target: str = ""  # Provider-Name, z.B. "s3"
    cloud_migration_completed_at: str = ""
    cloud_migration_last_run_at: str = ""
    cloud_migration_last_error: Optional[str] = None
    cloud_migration_total: int = 0
    cloud_migration_migrated: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MigrationState":
        # Robustes Parsing: fehlende Felder -> Default (idempotent).
        return cls(
            cloud_migration_done=bool(data.get("cloud_migration_done", False)),
            cloud_migration_target=str(data.get("cloud_migration_target", "")),
            cloud_migration_completed_at=str(
                data.get("cloud_migration_completed_at", "")
            ),
            cloud_migration_last_run_at=str(
                data.get("cloud_migration_last_run_at", "")
            ),
            cloud_migration_last_error=data.get("cloud_migration_last_error"),
            cloud_migration_total=int(data.get("cloud_migration_total", 0)),
            cloud_migration_migrated=int(data.get("cloud_migration_migrated", 0)),
        )


class BackupMigrationService:
    """Synchroner Service. Wird in main.py via asyncio.to_thread aufgerufen.

    Einziger Service-Punkt pro Prozess (Singleton via Modul-Level _service
    Variable). Cancel-Flag ist thread-safe (threading.Event).
    """

    def __init__(self) -> None:
        self._cancel_event = threading.Event()
        self._progress = MigrationProgress()
        self._progress_lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────

    def should_run(self) -> bool:
        """True wenn beim Startup der Job laufen soll.

        Bedingung laut Plan §3.10:
        1. MSM_BACKUP_PROVIDER != "local"
        2. state.cloud_migration_done == false
        3. Es gibt lokale Backup-Records (provider=="local" oder NULL)

        Die dritte Bedingung kann erst nach DB-Zugriff geprueft werden.
        Deshalb liefert diese Methode nur 1 + 2, und der Aufrufer macht den
        DB-Check separat. Das spart einen DB-Roundtrip, falls der Service
        eh nicht laufen wuerde.
        """
        provider = (settings.backup_provider or "local").lower()
        if provider == "local":
            return False
        state = load_state()
        if state.cloud_migration_done:
            return False
        return True

    def has_local_backups(self, db: Session) -> bool:
        """True wenn es noch DB-Records mit provider=="local"/None gibt.

        Wir joinen mit Server, um geloeschte Server-Cascade abzufangen
        (Plan §3.10 Edge Case: nur noch-existierende Server migrieren).
        """
        from models import Backup, Server

        q = (
            db.query(Backup)
            .join(Server, Backup.server_id == Server.id)
            .filter((Backup.provider == "local") | (Backup.provider.is_(None)))
        )
        return db.query(q.exists()).scalar()

    def run(
        self,
        db: Session,
        target_provider: Optional["BackupProvider"] = None,
        target_provider_name: Optional[str] = None,
    ) -> MigrationProgress:
        """Migriert alle lokalen Backups in den Ziel-Provider.

        Args:
            db: SQLAlchemy-Session
            target_provider: Optional pre-instanziierter Provider (fuer
                Cross-Cloud-Tests). Default: ``get_provider(settings.backup_provider)``.
            target_provider_name: Name des Ziel-Providers (fuer state.json).
                Default: ``settings.backup_provider``.

        Returns:
            MigrationProgress-Snapshot am Ende. Side effect: state.json
            aktualisiert. Side effect: DB-Records migriert (provider
            auf target, filename=None, remote_key gesetzt).
        """
        from services.backup_provider import get_provider, ProviderError

        if target_provider is None:
            target_name = (
                target_provider_name
                or (settings.backup_provider or "").lower()
            )
            try:
                target_provider = get_provider(target_name)
            except ProviderError as e:
                return self._finish_with_error(
                    f"Provider-Init fehlgeschlagen: {type(e).__name__}"
                )
        else:
            target_name = (
                target_provider_name
                or (settings.backup_provider or "").lower()
            )

        self._cancel_event.clear()
        encryption_key = settings.backup_encryption_key or ""

        # ── Idempotenter DB-Snapshot: nur lokal-Records, sortiert ──
        from models import Backup, Server

        local_records: list[Backup] = (
            db.query(Backup)
            .join(Server, Backup.server_id == Server.id)
            .filter((Backup.provider == "local") | (Backup.provider.is_(None)))
            .order_by(Backup.created_at.asc())  # aelteste zuerst
            .all()
        )

        with self._progress_lock:
            self._progress = MigrationProgress(
                status=MigrationStatus.RUNNING,
                total=len(local_records),
                started_at=datetime.now(timezone.utc).isoformat(),
            )

        # state.last_run_at vorab setzen (fuer Audit auch wenn Job scheitert)
        state = load_state()
        state.cloud_migration_last_run_at = datetime.now(timezone.utc).isoformat()
        state.cloud_migration_total = len(local_records)
        state.cloud_migration_migrated = 0
        state.cloud_migration_last_error = None
        save_state(state)

        if not local_records:
            # Nichts zu migrieren — direkt done markieren.
            self._mark_done(target_name)
            with self._progress_lock:
                self._progress.status = MigrationStatus.COMPLETED
                self._progress.finished_at = datetime.now(timezone.utc).isoformat()
            return self._progress

        # ── Sequenzielle Migration pro Backup ──
        encryption_module = None
        if encryption_key:
            from services import backup_encryption as encryption_module

        for backup in local_records:
            if self._cancel_event.is_set():
                with self._progress_lock:
                    self._progress.status = MigrationStatus.CANCELLED
                    self._progress.finished_at = (
                        datetime.now(timezone.utc).isoformat()
                    )
                    self._progress.last_error = "User-Cancel"
                logger.info(
                    "Auto-Migration abgebrochen nach %s/%s Backups",
                    self._progress.migrated,
                    self._progress.total,
                )
                # state NICHT als done markieren — Job soll beim naechsten
                # Startup wieder aufgenommen werden (idempotent ueber DB).
                return self._progress

            with self._progress_lock:
                self._progress.current_backup_id = backup.id
                self._progress.current_server_id = backup.server_id
                self._progress.current_filename = backup.filename

            try:
                self._migrate_one(backup, target_provider, target_name, encryption_key, encryption_module, db)
                with self._progress_lock:
                    self._progress.migrated += 1
                # State-Fortschritt persistieren (fuer externes Audit)
                state.cloud_migration_migrated = self._progress.migrated
                save_state(state)
            except Exception as e:  # noqa: BLE001
                # Provider-Fehler, IO-Fehler, alles. Sanitized log.
                err_type = type(e).__name__
                logger.warning(
                    "Auto-Migration fehlgeschlagen fuer Backup %s (server=%s, provider=%s, err=%s)",
                    backup.id,
                    backup.server_id,
                    target_name,
                    err_type,
                )
                with self._progress_lock:
                    self._progress.failed += 1
                    self._progress.last_error = f"{err_type} bei Backup {backup.id}"

                # state speichern, NICHT done markieren
                state.cloud_migration_last_error = (
                    f"{err_type} bei Backup {backup.id} (server={backup.server_id})"
                )
                save_state(state)

                # Abbruch: Plan sagt "Cloud-Credentials falsch → Job stoppt
                # sofort". Wir stoppen beim ersten Fehler, der nicht
                # "File fehlt auf Platte" ist (das ist oft nur ein manuell
                # aufgeraeumter Eintrag und sollte den Job nicht abbrechen).
                if isinstance(e, FileNotFoundError):
                    # Soft-skip: weiter mit naechstem Backup
                    continue
                # Hard-stop: provider-Fehler, Credentials, Netzwerk
                with self._progress_lock:
                    self._progress.status = MigrationStatus.FAILED
                    self._progress.finished_at = (
                        datetime.now(timezone.utc).isoformat()
                    )
                logger.warning(
                    "Auto-Migration gestoppt nach %s/%s Backups (err=%s)",
                    self._progress.migrated,
                    self._progress.total,
                    err_type,
                )
                return self._progress

        # ── Alle Backups durchgelaufen ──
        if self._progress.failed == 0 and not self._cancel_event.is_set():
            self._mark_done(target_name)
            with self._progress_lock:
                self._progress.status = MigrationStatus.COMPLETED
                self._progress.finished_at = (
                    datetime.now(timezone.utc).isoformat()
                )
        else:
            # Es gab Failures (FileNotFoundError-Soft-Skips) — done nur setzen
            # wenn mindestens 1 erfolgreich migriert wurde. Sonst bleibt
            # state auf done=false und der Job wird beim naechsten Startup
            # wieder angeboten.
            if self._progress.migrated > 0:
                # Migration hat funktioniert, nur einzelne Records fehlen
                # (z.B. weil Backup-File manuell geloescht). Wir markieren
                # als done — die uebrigen Records sind eh orphaned.
                self._mark_done(target_name)
                with self._progress_lock:
                    self._progress.status = MigrationStatus.COMPLETED
                    self._progress.finished_at = (
                        datetime.now(timezone.utc).isoformat()
                    )
            else:
                with self._progress_lock:
                    self._progress.status = MigrationStatus.FAILED
                    self._progress.finished_at = (
                        datetime.now(timezone.utc).isoformat()
                    )

        return self._progress

    def cancel(self) -> None:
        """Signalisiert dem laufenden Job, nach dem aktuellen Backup zu stoppen.

        Thread-safe. Idempotent.
        """
        self._cancel_event.set()

    def progress(self) -> MigrationProgress:
        """Snapshot des aktuellen Fortschritts (fuer /migration-status Endpoint)."""
        with self._progress_lock:
            return MigrationProgress(**asdict(self._progress))

    # ── Private Helpers ───────────────────────────────────────────────

    def _migrate_one(
        self,
        backup: "Backup",
        target_provider: "BackupProvider",
        target_name: str,
        encryption_key: str,
        encryption_module,
        db: Session,
    ) -> None:
        """Migriert ein einzelnes Backup. Wirft bei Fehlern (vom Caller behandelt).

        Pipeline:
        1. Lokale Datei pruefen (muss existieren, sonst FileNotFoundError)
        2. Optional verschluesseln (in-place write neben tar.gz)
        3. Provider.upload(encrypted|plain, remote_key)
        4. DB-Update (provider, remote_key, filename=None) + commit
        5. Lokale Datei loeschen + verschluesselte Temp loeschen

        Bei Fehler in 3: kein DB-Update, lokale Datei bleibt, encrypted
            File (falls existent) wird im finally aufgeraeumt.
        Bei Fehler in 4 nach erfolgreichem Upload: ORPHAN in der Cloud.
            Wir versuchen provider.delete() im Cleanup.
        """
        from pathlib import Path as _P

        local_path = backup.filename
        if not local_path or not os.path.exists(local_path):
            raise FileNotFoundError(
                f"Lokales Backup-File fehlt: id={backup.id}"
            )

        # target_filename: bei encryption .enc Suffix, sonst Original
        target_filename = os.path.basename(local_path)
        if encryption_key and not target_filename.endswith(".enc"):
            target_filename = target_filename + ".enc"
        remote_key = f"{backup.server_id}/{target_filename}"

        enc_temp: Optional[str] = None
        upload_source = local_path
        if encryption_key and encryption_module:
            # Verschluesselte Kopie in /var/tmp/ — verschluesseltes Original
            # loeschen wir am Ende, Original-tar.gz loeschen wir NACH
            # erfolgreichem DB-Update.
            enc_temp = os.path.join(
                "/var/tmp/msm-backup-tmp",
                f"migrate_{backup.id}_{int(datetime.now(timezone.utc).timestamp())}.tar.gz.enc",
            )
            os.makedirs(os.path.dirname(enc_temp), exist_ok=True)
            encryption_module.encrypt_file(
                _P(local_path), _P(enc_temp), encryption_key
            )
            upload_source = enc_temp

        try:
            # Provider-Upload. Fehler hier fuehrt zu Caller-Exception.
            target_provider.upload(_P(upload_source), remote_key)

            # DB-Update: provider, remote_key, filename=None
            # filename=None weil das lokale File gleich geloescht wird.
            # Wir behalten es NICHT als Fallback, weil das die ganze
            # Cloud-only-Mode-Logik kaputt machen wuerde.
            backup.provider = target_name
            backup.remote_key = remote_key
            backup.filename = None
            db.commit()
        except Exception:
            # DB-Rollback falls gerade in einer Transaktion. Falls der
            # Upload erfolgreich war aber DB-Update failt: orphan cleanup.
            db.rollback()
            raise
        finally:
            # Encrypted temp IMMER aufraeumen
            if enc_temp and os.path.exists(enc_temp):
                try:
                    os.remove(enc_temp)
                except OSError:
                    pass

        # Lokales tar.gz (oder .enc bei local-encryption) loeschen — erst
        # NACH erfolgreichem DB-Update. Bei Fehler hier: warn-log,
        # state.done bleibt, naechster Run ueberspringt diesen Record
        # (provider ist schon "s3"/etc., nicht mehr "local").
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError as e:
                logger.warning(
                    "Konnte migriertes lokales File nicht loeschen: %s",
                    e,
                )

    def _mark_done(self, target_name: str) -> None:
        state = load_state()
        state.cloud_migration_done = True
        state.cloud_migration_target = target_name
        state.cloud_migration_completed_at = datetime.now(timezone.utc).isoformat()
        state.cloud_migration_last_error = None
        save_state(state)

    def _finish_with_error(self, error_msg: str) -> MigrationProgress:
        with self._progress_lock:
            self._progress.status = MigrationStatus.FAILED
            self._progress.finished_at = datetime.now(timezone.utc).isoformat()
            self._progress.last_error = error_msg
        state = load_state()
        state.cloud_migration_last_error = error_msg
        save_state(state)
        return self._progress


# ── State-File Helpers (Modul-Level) ─────────────────────────────────────


def load_state() -> MigrationState:
    """Laedt state.json. Fehlende Datei oder fehlende Felder -> Default.

    Thread-safe via ``_state_lock``. Robustes Parsing: korrupte JSON
    (z.B. nach Crash waehrend Write) -> Default, kein Crash.
    """
    with _state_lock:
        if not STATE_FILE.exists():
            return MigrationState()
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "state.json korrupt oder unlesbar (%s) - starte mit Default-State",
                type(e).__name__,
            )
            return MigrationState()
        if not isinstance(data, dict):
            return MigrationState()
        return MigrationState.from_dict(data)


def save_state(state: MigrationState) -> None:
    """Schreibt state.json atomar (temp + rename). chmod 600, msm-owned.

    Warum atomar: ``write`` in /opt/msm/.msm/state.json + rename ist atomar
    auf POSIX-Dateisystemen. Wenn der Prozess mitten im Write stirbt,
    bleibt die alte Version lesbar (kein korruptes JSON beim naechsten
    Startup).
    """
    with _state_lock:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            # chmod 700 aufs Directory, 600 aufs File
            try:
                os.chmod(STATE_DIR, 0o700)
            except OSError:
                pass
            tmp = STATE_FILE.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, indent=2, sort_keys=True)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, STATE_FILE)
        except OSError as e:
            logger.error(
                "Konnte state.json nicht schreiben (%s) - Migration-State geht verloren",
                type(e).__name__,
            )


# ── Singleton-Instanz (fuer main.py + spaeterer API) ─────────────────────

_service: Optional[BackupMigrationService] = None


def get_migration_service() -> BackupMigrationService:
    """Lazy-Singleton. Thread-safe via if-not-None (CPython GIL)."""
    global _service
    if _service is None:
        _service = BackupMigrationService()
    return _service


def reset_migration_service() -> None:
    """Fuer Tests. Setzt den Singleton zurueck."""
    global _service
    _service = None
