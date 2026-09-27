"""Gibt die IPv4-Adresse aus, die der Sidecar Anrufern fuer die Medien nennt.

Aufruf durch install.sh und update.sh: `python3 medienadresse.py <backend/.env>`.
Das Ergebnis landet als NODE_IP in livekit-sidecar/.env.

Warum nicht LiveKits eigenes `use_external_ip`: das fragt beim Start per STUN
bei Google nach und bricht ab, wenn der Container keinen Namen aufloesen kann.
Unter rootless Docker zeigt die Namensaufloesung im Container auf den
systemd-resolved-Stub 127.0.0.53, den es dort nicht gibt; der Sidecar startete
am 27.09.2026 deshalb im Minutentakt neu. Auf dem Host klappt die Aufloesung,
und die Adresse, unter der Browser das Panel erreichen, ist auch die richtige
fuer die Medienports.

Reihenfolge wie im Backend (`_lokale_client_url`): MSM_LIVEKIT_URL, dann
MSM_API_URL, dann MSM_PANEL_URL. MSM_LIVEKIT_NODE_IP schlaegt alles, fuer
Aufbauten hinter einem Proxy oder CDN, dessen Adresse nicht die des Servers ist.
Ohne brauchbares Ergebnis wird nichts ausgegeben; das aufrufende Skript warnt.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit


def _env_lesen(pfad: Path) -> dict[str, str]:
    werte: dict[str, str] = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, wert = zeile.split("=", 1)
        werte[name.strip()] = wert.strip().strip('"').strip("'")
    return werte


def _brauchbar(adresse: ipaddress.IPv4Address) -> bool:
    # 127.0.1.1 steht auf Debian oft fuer den eigenen Hostnamen in /etc/hosts.
    # Private Adressen bleiben erlaubt: ein Panel nur im eigenen Netz ist ein
    # gueltiger Aufbau, und dort ist die private Adresse die richtige.
    return not (
        adresse.is_loopback
        or adresse.is_link_local
        or adresse.is_unspecified
        or adresse.is_multicast
    )


def medienadresse(env: dict[str, str]) -> str:
    vorgabe = env.get("MSM_LIVEKIT_NODE_IP", "")
    if vorgabe:
        try:
            return str(ipaddress.IPv4Address(vorgabe))
        except ValueError:
            return ""

    for schluessel in ("MSM_LIVEKIT_URL", "MSM_API_URL", "MSM_PANEL_URL"):
        wert = env.get(schluessel, "")
        if not wert:
            continue
        host = urlsplit(wert if "://" in wert else f"//{wert}").hostname
        if not host:
            continue
        try:
            eintraege = socket.getaddrinfo(host, None, socket.AF_INET)
        except OSError:
            return ""
        kandidaten = [ipaddress.IPv4Address(e[4][0]) for e in eintraege]
        brauchbar = [a for a in kandidaten if _brauchbar(a)]
        # Eine oeffentliche Adresse vor einer privaten, falls beide auftauchen.
        brauchbar.sort(key=lambda a: not a.is_global)
        return str(brauchbar[0]) if brauchbar else ""
    return ""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Aufruf: medienadresse.py <backend/.env>")
    print(medienadresse(_env_lesen(Path(sys.argv[1]))))
