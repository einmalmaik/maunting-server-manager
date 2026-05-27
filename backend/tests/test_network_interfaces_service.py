"""Tests fuer network_interfaces_service.

psutil.net_if_addrs() wird gemockt, damit die Tests unabhaengig von der
konkreten Test-Maschine sind.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import patch

from services import network_interfaces_service as nis


def _addr(ip: str) -> SimpleNamespace:
    """Minimaler Stub fuer psutil.snicaddr (nur die Felder, die der Service nutzt)."""
    return SimpleNamespace(family=socket.AF_INET, address=ip, netmask=None, broadcast=None, ptp=None)


class TestListHostInterfaces:
    def test_sorting_public_first_then_private_then_loopback(self):
        fake = {
            "lo": [_addr("127.0.0.1")],
            "eth0": [_addr("192.168.1.10"), _addr("203.0.113.5")],
            "eth1": [_addr("169.254.10.1")],
        }
        with patch("services.network_interfaces_service.psutil.net_if_addrs", return_value=fake):
            ifs = nis.list_host_interfaces()

        ips = [h.ip for h in ifs]
        assert ips == ["203.0.113.5", "192.168.1.10", "169.254.10.1", "127.0.0.1"]

    def test_classification_flags(self):
        fake = {
            "lo": [_addr("127.0.0.1")],
            "eth0": [_addr("10.0.0.5"), _addr("8.8.8.8")],
            "eth1": [_addr("169.254.0.1")],
        }
        with patch("services.network_interfaces_service.psutil.net_if_addrs", return_value=fake):
            ifs = {h.ip: h for h in nis.list_host_interfaces()}

        assert ifs["127.0.0.1"].is_loopback is True
        assert ifs["127.0.0.1"].is_private is False
        assert ifs["10.0.0.5"].is_private is True
        assert ifs["10.0.0.5"].is_loopback is False
        assert ifs["8.8.8.8"].is_private is False
        assert ifs["8.8.8.8"].is_loopback is False
        assert ifs["169.254.0.1"].is_link_local is True

    def test_skips_non_ipv4_families(self):
        non_ipv4_family = getattr(socket, "AF_PACKET", object())
        fake = {"eth0": [SimpleNamespace(family=non_ipv4_family, address="aa:bb", netmask=None, broadcast=None, ptp=None)]}
        with patch("services.network_interfaces_service.psutil.net_if_addrs", return_value=fake):
            assert nis.list_host_interfaces() == []

    def test_deduplicates_same_ip_on_multiple_interfaces(self):
        fake = {
            "eth0": [_addr("10.0.0.5")],
            "eth0.0": [_addr("10.0.0.5")],
        }
        with patch("services.network_interfaces_service.psutil.net_if_addrs", return_value=fake):
            ifs = nis.list_host_interfaces()
        assert len(ifs) == 1
        assert ifs[0].ip == "10.0.0.5"


class TestDefaultBindIp:
    def test_prefers_public_ip(self):
        fake = {
            "lo": [_addr("127.0.0.1")],
            "eth0": [_addr("192.168.1.10"), _addr("203.0.113.5")],
        }
        with patch("services.network_interfaces_service.psutil.net_if_addrs", return_value=fake):
            assert nis.default_bind_ip() == "203.0.113.5"

    def test_falls_back_to_private_when_no_public(self):
        fake = {
            "lo": [_addr("127.0.0.1")],
            "eth0": [_addr("10.0.0.5")],
        }
        with patch("services.network_interfaces_service.psutil.net_if_addrs", return_value=fake):
            assert nis.default_bind_ip() == "10.0.0.5"

    def test_returns_none_when_only_loopback(self):
        fake = {"lo": [_addr("127.0.0.1")]}
        with patch("services.network_interfaces_service.psutil.net_if_addrs", return_value=fake):
            assert nis.default_bind_ip() is None
