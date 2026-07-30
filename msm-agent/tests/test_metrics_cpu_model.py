"""Agent /metrics inventory: cpu_model is optional and never secret."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from routers import metrics as metrics_mod


def test_read_cpu_model_from_proc_cpuinfo() -> None:
    fake = MagicMock()
    fake.is_file.return_value = True
    fake.read_text.return_value = (
        "processor\t: 0\n"
        "vendor_id\t: AuthenticAMD\n"
        "model name\t: AMD EPYC 7763 64-Core Processor\n"
        "cpu MHz\t\t: 2450.000\n"
    )
    with patch.object(metrics_mod, "Path", return_value=fake):
        assert metrics_mod._read_cpu_model() == "AMD EPYC 7763 64-Core Processor"


def test_read_cpu_model_falls_back_to_platform() -> None:
    fake = MagicMock()
    fake.is_file.return_value = False
    with (
        patch.object(metrics_mod, "Path", return_value=fake),
        patch.object(metrics_mod.platform, "processor", return_value="Intel(R) Core(TM) i9"),
    ):
        assert metrics_mod._read_cpu_model() == "Intel(R) Core(TM) i9"


def test_metrics_payload_includes_cpu_model_key() -> None:
    with (
        patch.object(metrics_mod.psutil, "cpu_count", return_value=4),
        patch.object(metrics_mod.psutil, "cpu_percent", return_value=12.5),
        patch.object(
            metrics_mod.psutil,
            "virtual_memory",
            return_value=MagicMock(total=8 * 1024**3, used=2 * 1024**3, percent=25.0),
        ),
        patch.object(
            metrics_mod.psutil,
            "disk_usage",
            return_value=MagicMock(total=100 * 1024**3, used=40 * 1024**3, percent=40.0),
        ),
        patch.object(
            metrics_mod.psutil,
            "net_io_counters",
            return_value=MagicMock(bytes_sent=1, bytes_recv=2),
        ),
        patch.object(metrics_mod.docker_service, "ping", return_value=False),
        patch.object(metrics_mod, "_read_cpu_model", return_value="Test CPU"),
    ):
        payload = metrics_mod.metrics()

    assert payload["cpu_model"] == "Test CPU"
    assert payload["cpu_count"] == 4
    assert "ram_total_bytes" in payload
