"""Ein Backup, das sich selbst belegt.

Vor dieser Aenderung war "das Backup war erfolgreich" eine Behauptung ohne
Gegenstand. `Backup` hatte weder Status noch Pruefsumme; wer nachsehen wollte,
fand `size_mb` — eine Ganzzahl in Megabyte, die fuer jedes Archiv unter 1 MB
`0` ist. Ein frisch angelegter Server hat genau so eines. Und
`switch_server_blueprint` prueft seit jeher `backup_record.status == "failed"`
auf eine Spalte, die es nie gab: die Bedingung konnte unter keinen Umstaenden
zutreffen, und unmittelbar danach wird das gesamte Serververzeichnis geleert.

Seitdem gilt: `verified_at` ist gesetzt, wenn das Archiv **nach dem Schreiben
nachgemessen** wurde — Datei vorhanden, nicht leer, Pruefsumme gerechnet. Nur
darauf stuetzen sich der Blueprint-Wechsel und die autonome Guardian-Heilung,
die beide loeschen duerfen, wenn ein Nachweis vorliegt.

Die Tests hier halten beide Richtungen fest: dass der Nachweis entsteht, wo er
entstehen darf, und dass er **ausbleibt**, wo nichts gemessen wurde.
"""

import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from models import Backup, Server
from services.backup_paths import BackupPlan


_PLAN = BackupPlan(scope="full")


def _tar_schreibt(inhalt: bytes):
    """Ersatz fuer `create_full_backup_tar`, der wirklich eine Datei anlegt.

    Die uebrigen Backup-Tests patchen `os.path.getsize`, weil ihnen der Inhalt
    gleichgueltig ist. Hier ist er der Gegenstand: eine Pruefsumme laesst sich
    nur ueber Bytes rechnen, die es gibt.
    """
    def _tar(filepath, *args, **kwargs):
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(filepath).write_bytes(inhalt)
        return MagicMock()
    return _tar


def _lauf(db: Session, server: Server, tmp_path: Path, inhalt: bytes):
    """Ein `run_backup` mit echtem Archiv in `tmp_path`.

    `run_backup` baut sein Zielverzeichnis fest als `/opt/msm/backups/{id}`.
    Auf einer Entwicklermaschine ist das je nach Plattform `C:\\opt\\msm\\...`
    oder ein Pfad ohne Schreibrecht — beides will man nicht anlegen, nur um eine
    Pruefsumme zu testen. Deshalb wird der eine `join`-Aufruf umgebogen, der
    diesen Pfad bildet, und **nur** dieser: alle anderen laufen unveraendert
    weiter. Ein pauschal ersetztes `os.path.join` waere waehrend des Blocks auch
    fuer SQLAlchemy und pytest in Kraft.
    """
    from services.backup_service import run_backup

    ziel = tmp_path / "backups"
    ziel.mkdir(parents=True, exist_ok=True)
    echtes_join = os.path.join
    wurzel = "/opt/msm/backups"

    def _umbiegen(*teile):
        if teile and str(teile[0]).startswith(wurzel):
            return echtes_join(str(ziel), *teile[1:])
        return echtes_join(*teile)

    with (
        patch("services.backup_service.create_full_backup_tar", side_effect=_tar_schreibt(inhalt)),
        patch("services.backup_service.cleanup_old_backups"),
        patch("services.backup_service.backup_plan_for_server", return_value=_PLAN),
        patch("services.backup_service.os.makedirs"),
        patch.object(os.path, "join", side_effect=_umbiegen),
    ):
        return run_backup(server.id, db)


def _install(server: Server, db: Session, tmp_path: Path) -> None:
    quelle = tmp_path / "install"
    quelle.mkdir(exist_ok=True)
    (quelle / "welt.dat").write_text("inhalt", encoding="utf-8")
    server.install_dir = str(quelle)
    db.commit()


class TestNachweisEntsteht:
    def test_sha256_and_verified_at_are_written(
        self, db: Session, test_server: Server, tmp_path: Path
    ):
        """Der Normalfall — und die Pruefsumme gehoert wirklich zu der Datei.

        Ein Wert, der nur vorhanden ist, belegt nichts. Deshalb wird er hier
        gegen die Bytes nachgerechnet, die auf der Platte liegen.
        """
        _install(test_server, db, tmp_path)
        inhalt = b"ein kleines, aber vollstaendiges Archiv"

        backup = _lauf(db, test_server, tmp_path, inhalt)

        assert backup.verified_at is not None
        assert backup.sha256 == hashlib.sha256(inhalt).hexdigest()
        assert len(backup.sha256) == 64

    def test_an_archive_below_one_megabyte_still_counts(
        self, db: Session, test_server: Server, tmp_path: Path
    ):
        """Genau der Fall, an dem eine Pruefung auf `size_mb` gescheitert waere.

        `size_mb` ist `bytes // (1024*1024)` und damit `0` fuer jedes Archiv
        unter einem Megabyte. Ein frisch angelegter Server hat genau so eines.
        Haette der Nachweis daran gehangen, waere ausgerechnet der harmloseste
        Fall als Fehlschlag gewertet worden — und die KI haette einen intakten
        Server nie anfassen duerfen.
        """
        _install(test_server, db, tmp_path)

        backup = _lauf(db, test_server, tmp_path, b"winzig")

        assert backup.size_mb == 0
        assert backup.verified_at is not None

    def test_an_empty_archive_is_a_failure_and_leaves_no_record(
        self, db: Session, test_server: Server, tmp_path: Path
    ):
        """Null Bytes sind kein Backup.

        Der Abbruch ist wichtiger als die Fehlermeldung: es darf keine Zeile
        entstehen, die spaeter als Freigabe zum Loeschen gelesen wird.
        """
        _install(test_server, db, tmp_path)
        vorher = db.query(Backup).filter(Backup.server_id == test_server.id).count()

        with pytest.raises(RuntimeError):
            _lauf(db, test_server, tmp_path, b"")

        assert db.query(Backup).filter(Backup.server_id == test_server.id).count() == vorher


class TestNachweisBleibtAus:
    def test_an_unmeasurable_archive_yields_a_record_without_proof(
        self, db: Session, test_server: Server, tmp_path: Path
    ):
        """Nicht nachmessbar heisst unbewiesen — nicht "fehlgeschlagen".

        Die Datei ist geschrieben, `getsize` meldet eine Groesse, aber das
        Lesen scheitert (Rechte, Netzlaufwerk, im Test schlicht: die Datei ist
        nicht da). Der Datensatz entsteht trotzdem, denn das Archiv existiert
        aus Sicht des Dienstes; `verified_at` bleibt aber leer.

        Diese Richtung ist die sicherheitsrelevante: sie entscheidet, dass ein
        unbelegtes Backup **nicht** zum Loeschen berechtigt.
        """
        from services.backup_service import run_backup

        _install(test_server, db, tmp_path)

        with (
            patch("services.backup_service.create_full_backup_tar"),
            patch("services.backup_service.os.makedirs"),
            patch("services.backup_service.os.path.getsize", return_value=4096),
            patch("services.backup_service.cleanup_old_backups"),
            patch("services.backup_service.backup_plan_for_server", return_value=_PLAN),
        ):
            backup = run_backup(test_server.id, db)

        assert backup is not None
        assert backup.sha256 is None
        assert backup.verified_at is None

    def test_sha256_of_swallows_the_read_error(self, tmp_path: Path):
        """`_sha256_of` darf ein fertiges Backup nicht nachtraeglich umbringen.

        Es sitzt innerhalb des `try`, dessen `except` die Datei aufraeumt und
        `RuntimeError("Backup fehlgeschlagen")` wirft. Floege der Lesefehler
        weiter, verloere der Betreiber ein Archiv, das vollstaendig auf der
        Platte lag — wegen einer Messung.
        """
        from services.backup_service import _sha256_of

        assert _sha256_of(str(tmp_path / "gibtesnicht.tar.gz")) is None


class TestS3BehauptetNichts:
    def test_s3_key_stays_empty_when_the_object_cannot_be_confirmed(
        self, db: Session, test_server: Server, tmp_path: Path
    ):
        """Eine ausgebliebene Ausnahme ist keine Aussage ueber den Bucket.

        `upload_stream` gibt nichts zurueck. Bisher wurde `s3_key` gesetzt,
        sobald der Aufruf ohne Fehler zurueckkam — die Zusage "liegt in S3" ruhte
        damit auf einer Abwesenheit. Jetzt wird nachgesehen; bleibt das Objekt
        unbestaetigt, bleibt die Behauptung ungestellt und das Backup gilt als
        lokales, aber nachgewiesenes.
        """
        from services import backup_orchestrator

        archiv = tmp_path / "server_1.enc"
        archiv.write_bytes(b"verschluesselt")
        backup = Backup(
            server_id=test_server.id,
            filename=str(archiv),
            size_mb=0,
            sha256="a" * 64,
        )
        db.add(backup)
        db.commit()
        db.refresh(backup)

        s3 = MagicMock()
        s3.object_size.return_value = None  # nichts im Bucket gefunden
        with (
            patch("services.s3_service.S3Service", s3),
            patch(
                "services.backup_config_service.BackupConfigService.get_s3_config",
                return_value={"bucket": "msm"},
            ),
        ):
            backup_orchestrator._upload_to_s3(backup, db, test_server.id)

        assert backup.s3_key is None
        assert backup.s3_bucket is None
        # Der lokale Nachweis bleibt unberuehrt — er hat mit S3 nichts zu tun.
        assert backup.sha256 == "a" * 64
