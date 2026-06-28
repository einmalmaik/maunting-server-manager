from unittest.mock import patch

import pytest

from services import postgres_service
from services.docker_service import PortPublish
from services.postgres_service import PostgresServiceError


def test_managed_postgres_starts_with_loopback_only_binding(monkeypatch):
    monkeypatch.setattr(postgres_service.settings, "managed_postgres_host", "127.0.0.1")
    monkeypatch.setattr(postgres_service.settings, "managed_postgres_port", 15432)
    monkeypatch.setattr(postgres_service.settings, "managed_postgres_data_dir", "/tmp/msm-pg-test")

    with patch("services.postgres_service.docker_service.ensure_network", return_value={"ok": True}), \
         patch("services.postgres_service.docker_service.inspect_state", return_value=None), \
         patch("services.postgres_service.docker_service.run_container", return_value={"ok": True}) as run_container, \
         patch("services.postgres_service.os.makedirs"), \
         patch("services.postgres_service._encrypted_admin_password", return_value="encrypted"), \
         patch("services.postgres_service._admin_password", return_value="secret"):
        postgres_service.ensure_internal_postgres()

    kwargs = run_container.call_args.kwargs
    assert kwargs["image"] == "postgres:17-alpine"
    assert kwargs["network"] == "msm-internal"
    assert kwargs["read_only_rootfs"] is False
    assert isinstance(kwargs["ports"][0], PortPublish)
    assert kwargs["ports"][0].host_ip == "127.0.0.1"
    assert kwargs["ports"][0].host_port == 15432


def test_managed_postgres_rejects_public_host_binding(monkeypatch):
    monkeypatch.setattr(postgres_service.settings, "managed_postgres_host", "0.0.0.0")
    with pytest.raises(PostgresServiceError):
        postgres_service._db_host()


def test_identifier_validation_rejects_unsafe_names():
    with pytest.raises(ValueError):
        postgres_service._validate_identifier("public; drop database postgres")
    with pytest.raises(ValueError):
        postgres_service._validate_identifier("../secret")
