from __future__ import annotations

import asyncio
import json
import socket
import struct
import threading
from typing import Callable

import pytest

from services import docker_service
from services.guardian_contract import ProbeConfig
from services.guardian_probes import execute_probe


def _config(probe_type: str, **overrides) -> ProbeConfig:
    base = {
        "check_id": probe_type.replace("_", "-"),
        "type": probe_type,
        "interval_seconds": 1,
        "timeout_seconds": 1,
        "failure_threshold": 1,
        "success_threshold": 1,
    }
    base.update(overrides)
    return ProbeConfig.model_validate(base)


def test_process_probe_uses_docker_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        docker_service,
        "inspect_container_state",
        lambda _name: {"running": True, "status": "running", "oom_killed": False},
    )
    result = asyncio.run(execute_probe(_config("process"), "msm-srv-1"))
    assert result.healthy is True
    assert result.code == "process_running"


def test_udp_mapping_is_docker_exposure_not_sendto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        docker_service,
        "inspect_container_state",
        lambda _name: {
            "running": True,
            "port_bindings": {"25565/udp": [{"host_ip": "0.0.0.0", "host_port": 25565}]},
        },
    )
    mapped = asyncio.run(
        execute_probe(_config("udp_port_mapping", target_port=25565), "msm-srv-1")
    )
    missing = asyncio.run(
        execute_probe(_config("udp_port_mapping", target_port=25566), "msm-srv-1")
    )
    assert mapped.healthy is True
    assert missing.healthy is False


def test_real_tcp_success_and_failure() -> None:
    async def scenario() -> tuple[bool, bool]:
        server = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            success = await execute_probe(
                _config("tcp", target_host="127.0.0.1", target_port=port),
                "msm-srv-1",
            )
        failure = await execute_probe(
            _config("tcp", target_host="127.0.0.1", target_port=port, timeout_seconds=0.2),
            "msm-srv-1",
        )
        return success.healthy, failure.healthy

    assert asyncio.run(scenario()) == (True, False)


async def _one_http_response(status: int, body: bytes, *, location: str | None = None):
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        headers = [f"HTTP/1.1 {status} Test", f"Content-Length: {len(body)}", "Connection: close"]
        if location:
            headers.append(f"Location: {location}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handler, "127.0.0.1", 0)


def test_http_expected_unexpected_redirect_and_size_limit() -> None:
    async def run_once(status: int, body: bytes, **options):
        server = await _one_http_response(status, body, location=options.pop("location", None))
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await execute_probe(
                _config(
                    "http-ping",
                    target_host="127.0.0.1",
                    target_port=port,
                    path="/health",
                    expected_statuses=[200, 204],
                    **options,
                ),
                "msm-srv-1",
            )

    assert asyncio.run(run_once(204, b"")).healthy is True
    unexpected = asyncio.run(run_once(503, b"down"))
    assert unexpected.code == "http_unexpected_status"
    redirect = asyncio.run(run_once(302, b"", location="http://169.254.169.254/"))
    assert redirect.code == "http_redirect_rejected"
    large = asyncio.run(run_once(200, b"x" * 32, max_response_bytes=16))
    assert large.code == "http_response_too_large"


@pytest.mark.parametrize("host", ["169.254.169.254", "0.0.0.0", "224.0.0.1", "metadata.invalid"])
def test_http_unsafe_targets_rejected_before_execution(host: str) -> None:
    with pytest.raises(ValueError):
        _config("http-ping", target_host=host, target_port=80, path="/health")


def _varint(value: int) -> bytes:
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        output.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(output)


def test_minecraft_status_success_and_malformed_packet() -> None:
    async def run(malformed: bool):
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(1024)
            if malformed:
                writer.write(b"\xff\xff\xff\xff\x7f")
            else:
                body = json.dumps({"version": {"name": "synthetic", "protocol": 765}}).encode()
                packet = b"\x00" + _varint(len(body)) + body
                writer.write(_varint(len(packet)) + packet)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await execute_probe(
                _config("minecraft-status", target_host="127.0.0.1", target_port=port),
                "msm-srv-1",
            )

    assert asyncio.run(run(False)).healthy is True
    assert asyncio.run(run(True)).healthy is False


def _udp_server(handler: Callable[[socket.socket], None]) -> tuple[int, threading.Thread]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    def run() -> None:
        try:
            handler(sock)
        finally:
            sock.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, thread


def test_minecraft_query_success_and_failure() -> None:
    def handler(sock: socket.socket) -> None:
        request, address = sock.recvfrom(1024)
        session = request[3:7]
        sock.sendto(b"\x09" + session + b"12345\x00", address)
        stat_request, address = sock.recvfrom(1024)
        sock.sendto(b"\x00" + stat_request[3:7] + b"synthetic\x00", address)

    port, thread = _udp_server(handler)
    success = asyncio.run(
        execute_probe(
            _config("minecraft-query", target_host="127.0.0.1", target_port=port),
            "msm-srv-1",
        )
    )
    thread.join(2)
    assert success.healthy is True

    closed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    failure = asyncio.run(
        execute_probe(
            _config(
                "minecraft-query",
                target_host="127.0.0.1",
                target_port=closed_port,
                timeout_seconds=0.2,
            ),
            "msm-srv-1",
        )
    )
    assert failure.healthy is False


def test_source_query_with_and_without_challenge() -> None:
    def challenged(sock: socket.socket) -> None:
        _first, address = sock.recvfrom(1024)
        challenge = b"\x01\x02\x03\x04"
        sock.sendto(b"\xff\xff\xff\xff\x41" + challenge, address)
        second, address = sock.recvfrom(1024)
        assert second.endswith(challenge)
        sock.sendto(b"\xff\xff\xff\xff\x49synthetic", address)

    port, thread = _udp_server(challenged)
    result = asyncio.run(
        execute_probe(
            _config("source-query", target_host="127.0.0.1", target_port=port),
            "msm-srv-1",
        )
    )
    thread.join(2)
    assert result.healthy is True

    def direct(sock: socket.socket) -> None:
        _request, address = sock.recvfrom(1024)
        sock.sendto(b"\xff\xff\xff\xff\x49synthetic", address)

    direct_port, direct_thread = _udp_server(direct)
    direct_result = asyncio.run(
        execute_probe(
            _config("source-query", target_host="127.0.0.1", target_port=direct_port),
            "msm-srv-1",
        )
    )
    direct_thread.join(2)
    assert direct_result.healthy is True


def test_unknown_probe_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        _config("unknown")


def test_dynamic_probe_loading_and_unloading() -> None:
    from services.guardian_probes import discover_probes
    from pathlib import Path
    
    # 1. Verify standard probes exist
    probes = discover_probes()
    assert "process" in probes
    assert "tcp" in probes
    assert "minecraft-status" in probes

    # 2. Dynamically write a custom probe
    custom_probe_path = Path(__file__).parent.parent / "services" / "guardian_probes" / "dynamic_test_probe.py"
    try:
        custom_probe_path.write_text(
            'PROBE_TYPE = "dynamic-test-probe"\n'
            'async def execute(config, container_name):\n'
            '    from services.guardian_probes import _result\n'
            '    return _result(0.0, True, "dynamic_ok")\n'
        )
        
        # 3. Verify it is detected
        probes = discover_probes()
        assert "dynamic-test-probe" in probes
        
        # 4. Verify we can execute it
        config = _config("dynamic-test-probe", target_host="127.0.0.1", target_port=80)
        result = asyncio.run(execute_probe(config, "msm-srv-1"))
        assert result.healthy is True
        assert result.code == "dynamic_ok"
        
    finally:
        # 5. Delete it and verify it's hot-unloaded immediately
        if custom_probe_path.exists():
            custom_probe_path.unlink()
        
        probes = discover_probes()
        assert "dynamic-test-probe" not in probes


def test_dynamic_probe_broken_syntax_ignored() -> None:
    from services.guardian_probes import discover_probes
    from pathlib import Path
    
    broken_probe_path = Path(__file__).parent.parent / "services" / "guardian_probes" / "broken_syntax_probe.py"
    try:
        # Write broken syntax code
        broken_probe_path.write_text("PROBE_TYPE = 'broken-probe'\nthis is invalid syntax Python code !!!\n")
        
        # Verify discovering probes doesn't raise and skips the broken file
        probes = discover_probes()
        assert "broken-probe" not in probes
    finally:
        if broken_probe_path.exists():
            broken_probe_path.unlink()


def test_dynamic_probe_runtime_crash_handled() -> None:
    from services.guardian_probes import discover_probes
    from pathlib import Path
    
    crashing_probe_path = Path(__file__).parent.parent / "services" / "guardian_probes" / "crashing_probe.py"
    try:
        crashing_probe_path.write_text(
            'PROBE_TYPE = "crashing-probe"\n'
            'async def execute(config, container_name):\n'
            '    raise ZeroDivisionError("Simulated division by zero")\n'
        )
        
        # Verify it is detected
        probes = discover_probes()
        assert "crashing-probe" in probes
        
        # Verify calling it yields a structured failed result instead of raising
        config = _config("crashing-probe", target_host="127.0.0.1", target_port=80)
        result = asyncio.run(execute_probe(config, "msm-srv-1"))
        
        assert result.healthy is False
        assert result.code == "driver_execution_error"
        assert result.evidence["error_class"] == "ZeroDivisionError"
        assert result.evidence["error_message"] == "Simulated division by zero"
    finally:
        if crashing_probe_path.exists():
            crashing_probe_path.unlink()
        
        probes = discover_probes()
        assert "crashing-probe" not in probes

