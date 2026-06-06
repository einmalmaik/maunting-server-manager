"""SFTP-Backup-Provider (Hetzner Storage Box + jeder generische SFTP-Server).

Setzt ``paramiko`` voraus. Sync-API, wird vom Backup-Service in
``asyncio.to_thread`` gewrappt.

Auth-Modell v1: NUR Passwort-Auth (kein SSH-Key). Begruendung in ADR-0009.
Hetzner Storage Box unterstuetzt beides, aber Password-only ist der
einfachste Self-Hosted-Pfad. SSH-Key kann spaeter ohne API-Bruch
hinzugefuegt werden (paramiko unterstuetzt beides).

Path-Layout (Konvention, gleich wie bei local/s3):

    <base_path>/<server_id>/<filename>          (Daten, z. B. server_42_...tar.gz.enc)
    <base_path>/<server_id>/<filename>.meta.json   (Meta-File daneben)

Security:
  - Host-Key-Validierung: ``load_system_host_keys()`` + ``RejectPolicy``.
    Unbekannte Hosts werden ABGELEHNT (verhindert MITM). User kann mit
    ``ssh-keyscan host >> ~/.ssh/known_hosts`` vorab akzeptieren.
    install.sh wird das als Teil des SFTP-Setups anbieten.
  - Path-Traversal-Schutz: ``remote_key`` muss relativ sein, ohne ``..``.
    Voller Pfad muss unter ``base_path`` bleiben.
  - Fehlertexte generisch (kein Host/User/Pfad/Passwort im Log).
  - Passwort liegt nur im Konstruktor-Argument; keine Persistenz, kein
    Re-Logging, keine Echo in Errors.
  - Adapter sieht nur Chiffretext (Verschluesselung im Caller).
  - ``allow_agent=False``, ``look_for_keys=False`` beim Connect: verhindert
    versehentliches Verwenden von SSH-Agent-Keys oder ``~/.ssh/id_rsa``.
    Wir wollen ausschliesslich das konfigurierte Passwort.
"""
import logging
import posixpath
import stat as stat_mod
from pathlib import Path
from typing import Iterator, Optional

import paramiko
from paramiko.sftp_client import SFTPClient

from .base import (
    BackupLocation,
    BackupMetadata,
    BackupProvider,
    ProgressCallback,
    ProviderError,
)

logger = logging.getLogger(__name__)

META_SUFFIX = ".meta.json"  # Backup "<key>.enc" → Meta "<key>.meta.json"

# Errors, die als "Provider-Fehler" klassifiziert werden (Connect, Auth,
# Network, SFTP-IO). Generische Fehlermeldung nach aussen; Typname landet
# im Log (ohne Details).
_SFTP_ERRORS: tuple[type[BaseException], ...] = (
    paramiko.AuthenticationException,
    paramiko.SSHException,
    paramiko.sftp.SFTPError,
    OSError,
    IOError,
    EOFError,
)


class SFTPProvider(BackupProvider):
    """SFTP-Backup-Adapter fuer Hetzner Storage Box und generische SFTP-Server."""

    name = "sftp"

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        base_path: str,
        port: int = 22,
        timeout: int = 30,
    ) -> None:
        if not host:
            raise ProviderError("SFTP-Host nicht konfiguriert")
        if not user:
            raise ProviderError("SFTP-User nicht konfiguriert")
        if not password:
            raise ProviderError("SFTP-Passwort nicht konfiguriert")
        if not base_path:
            raise ProviderError("SFTP-Basispfad nicht konfiguriert")
        # base_path muss absolut sein (sonst landen wir im CWD des Servers —
        # nicht-deterministisch und schlecht auditierbar).
        if not base_path.startswith("/"):
            raise ProviderError("SFTP-Basispfad muss absolut sein (mit / beginnen)")
        if not (1 <= int(port) <= 65535):
            raise ProviderError("SFTP-Port ausserhalb des erlaubten Bereichs")

        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        # Normalisiere: kein trailing slash, kein ".."
        self.base_path = posixpath.normpath(base_path)
        self.timeout = int(timeout)

    # ── private helpers ───────────────────────────────────────────────────

    def _connect(self) -> paramiko.SSHClient:
        """Oeffnet eine SSH-Verbindung. Caller ist fuer ``close()`` verantwortlich.

        Wir nutzen try/finally-Konvention (kein context manager), weil
        paramiko.SSHClient von Haus aus kein ``__enter__`` / ``__exit__``
        implementiert — eigene Wrapper waeren zusaetzliche Komplexitaet
        ohne Mehrwert fuer diesen Use-Case.
        """
        client = paramiko.SSHClient()
        # Lade ~/.ssh/known_hosts. Hetzner-Storage-Box: User fuegt
        # Fingerprint einmalig hinzu (install.sh).
        client.load_system_host_keys()
        # Strict: unbekannte Hosts ablehnen (verhindert MITM).
        client.set_missing_host_key_policy(paramiko.RejectPolicy)
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                timeout=self.timeout,
                # Wichtig: wir wollen AUSSCHLIESSLICH das konfigurierte
                # Passwort verwenden, nicht versehentlich SSH-Agent-Keys
                # oder ~/.ssh/id_rsa.
                allow_agent=False,
                look_for_keys=False,
            )
        except _SFTP_ERRORS as e:
            logger.warning("SFTP-Connect fehlgeschlagen: %s", type(e).__name__)
            client.close()
            raise ProviderError("SFTP-Verbindung fehlgeschlagen") from e
        return client

    def _full_path(self, remote_key: str) -> str:
        """Berechnet den vollen SFTP-Pfad, mit Traversal-Check.

        remote_key: ``<server_id>/<filename>`` (z. B. ``42/server.tar.gz.enc``)
        Voller Pfad: ``<base_path>/<server_id>/<filename>``
        """
        if not remote_key:
            raise ProviderError("Ungueltiger Backup-Key")
        if remote_key.startswith("/"):
            raise ProviderError("Ungueltiger Backup-Key")
        if ".." in Path(remote_key).parts:
            raise ProviderError("Ungueltiger Backup-Key")
        full = posixpath.normpath(posixpath.join(self.base_path, remote_key))
        # Strikte Bounds-Check: full muss base_path + "/" + ... sein ODER
        # identisch mit base_path (theoretisch moeglich, aber wir lehnen
        # leere Keys oben ab).
        if full != self.base_path and not full.startswith(self.base_path + "/"):
            raise ProviderError("Backup-Key ausserhalb des erlaubten Bereichs")
        return full

    @staticmethod
    def _meta_key(remote_key: str) -> str:
        """Mappt Daten-Key auf Meta-Key (Konvention: <key>.meta.json)."""
        return remote_key + META_SUFFIX

    @staticmethod
    def _progress_wrapper(
        progress_cb: Optional[ProgressCallback],
    ) -> Optional[callable]:
        """Mappt paramiko's ``(bytes_so_far, total)`` auf unser ``Callable[[int], None]``.

        paramiko ruft das Callback kumulativ auf (``bytes_so_far`` = Summe
        aller bisher uebertragenen Bytes), was exakt unserem
        ProgressCallback-Vertrag entspricht. Wir droppen nur das
        ``total``-Argument.
        """
        if progress_cb is None:
            return None

        def cb(bytes_so_far: int, _total: int) -> None:
            progress_cb(bytes_so_far)

        return cb

    @staticmethod
    def _mkdir_p(sftp: SFTPClient, remote_dir: str) -> None:
        """Erstellt ``remote_dir`` rekursiv (``mkdir -p``). Existenz ist ok.

        paramiko hat kein ``makedirs``-Pendant; wir laufen den Pfad ab
        und legen fehlende Ebenen an. Existiert das Ziel schon (z. B.
        durch parallele Uploads), schlucken wir den ``IOError``.
        """
        if not remote_dir or remote_dir == "/":
            return
        try:
            sftp.stat(remote_dir)
            return  # existiert bereits
        except IOError:
            pass
        parent = posixpath.dirname(remote_dir)
        if parent and parent != remote_dir:
            SFTPProvider._mkdir_p(sftp, parent)
        try:
            sftp.mkdir(remote_dir)
        except IOError:
            # Race condition: zwischen stat und mkdir angelegt → ok.
            pass

    def _walk_meta_files(
        self, sftp: SFTPClient, root: str
    ) -> Iterator[str]:
        """Generator: liefert alle Pfade zu ``*.meta.json`` unterhalb ``root``."""
        try:
            entries = sftp.listdir_attr(root)
        except IOError:
            return
        for entry in entries:
            child = posixpath.join(root, entry.filename)
            if stat_mod.S_ISDIR(entry.st_mode):
                yield from self._walk_meta_files(sftp, child)
            elif child.endswith(META_SUFFIX):
                yield child

    # ── public API ────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Prueft Credentials + Erreichbarkeit + base_path existent/erstellbar.

        Side-Effect: legt ``base_path`` an, falls noch nicht existent
        (idempotentes ``mkdir -p``). Das macht die Install-Flow
        einfacher — der User muss den Pfad nicht vorher manuell anlegen.
        """
        try:
            client = self._connect()
        except ProviderError:
            return False
        try:
            sftp = client.open_sftp()
            try:
                # base_path anlegen, falls fehlend (idempotent)
                self._mkdir_p(sftp, self.base_path)
            finally:
                sftp.close()
        except _SFTP_ERRORS:
            return False
        finally:
            client.close()
        return True

    def upload(
        self,
        local_path: Path,
        remote_key: str,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> BackupLocation:
        if not local_path.is_file():
            raise ProviderError("Lokale Datei existiert nicht")
        full = self._full_path(remote_key)
        remote_dir = posixpath.dirname(full)
        try:
            client = self._connect()
        except ProviderError:
            raise
        try:
            sftp = client.open_sftp()
            try:
                self._mkdir_p(sftp, remote_dir)
                sftp.put(
                    str(local_path),
                    full,
                    callback=self._progress_wrapper(progress_cb),
                )
            finally:
                sftp.close()
        except _SFTP_ERRORS as e:
            logger.warning("SFTP-Upload fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Upload fehlgeschlagen") from e
        finally:
            client.close()

        try:
            size_bytes = local_path.stat().st_size
        except OSError:
            size_bytes = 0
        size_mb = int(size_bytes // (1024 * 1024)) if size_bytes else None
        return BackupLocation(remote_key=remote_key, size_mb=size_mb)

    def download(
        self,
        remote_key: str,
        local_path: Path,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        full = self._full_path(remote_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            client = self._connect()
        except ProviderError:
            raise
        try:
            sftp = client.open_sftp()
            try:
                sftp.get(
                    full,
                    str(local_path),
                    callback=self._progress_wrapper(progress_cb),
                )
            finally:
                sftp.close()
        except _SFTP_ERRORS as e:
            logger.warning("SFTP-Download fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Download fehlgeschlagen") from e
        finally:
            client.close()

    def delete(self, remote_key: str) -> None:
        """Loescht Daten- und Meta-Datei. Idempotent (fehlend = ok)."""
        try:
            full = self._full_path(remote_key)
            meta_full = self._full_path(self._meta_key(remote_key))
        except ProviderError:
            # Malformed key — idempotenter Cleanup toleriert das
            return
        try:
            client = self._connect()
        except ProviderError:
            raise
        try:
            sftp = client.open_sftp()
            try:
                for path in (full, meta_full):
                    try:
                        sftp.remove(path)
                    except IOError:
                        # Existiert nicht → ok (idempotent)
                        pass
            finally:
                sftp.close()
        except _SFTP_ERRORS as e:
            logger.warning("SFTP-Delete fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Loeschen fehlgeschlagen") from e
        finally:
            client.close()

    def list_metadata(self) -> list[BackupMetadata]:
        """Scannt ``base_path`` rekursiv nach ``*.meta.json`` und parsed jedes.

        Kaputte / nicht-parsebare Files werden uebersprungen (kein Raise).
        Connect/IO-Errors werfen ``ProviderError`` (fuer
        Migration-Visibility im Backend).
        """
        results: list[BackupMetadata] = []
        try:
            client = self._connect()
        except ProviderError:
            raise
        try:
            sftp = client.open_sftp()
            try:
                for meta_path in self._walk_meta_files(sftp, self.base_path):
                    try:
                        with sftp.open(meta_path, "r") as f:
                            raw = f.read().decode("utf-8")
                        results.append(BackupMetadata.from_json(raw))
                    except (
                        IOError,
                        OSError,
                        ValueError,
                        TypeError,
                        UnicodeDecodeError,
                    ) as e:
                        # Generischer Skip-Log ohne Pfad-Leak
                        logger.warning(
                            "Ueberspringe kaputte Backup-Metadaten: %s",
                            type(e).__name__,
                        )
                        continue
            finally:
                sftp.close()
        except _SFTP_ERRORS as e:
            raise ProviderError("Liste fehlgeschlagen") from e
        finally:
            client.close()
        return results
