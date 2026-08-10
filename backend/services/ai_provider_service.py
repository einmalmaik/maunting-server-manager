"""Sichere Provider-Konfiguration ohne SDK- oder Secret-Leaks."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from models import AiProvider
from services.dis_client import DisClient


class AiProviderConfigurationError(ValueError):
    """Die Provider-Konfiguration verletzt den sicheren URL-Vertrag."""


def _resolved_addresses(host: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise AiProviderConfigurationError("Provider-Host konnte nicht aufgeloest werden") from exc
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for info in infos:
        try:
            addresses.add(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError):
            continue
    if not addresses:
        raise AiProviderConfigurationError("Provider-Host lieferte keine gueltige Adresse")
    return addresses


def _assert_addresses_allowed(
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    *,
    scheme: str,
    allow_private_network: bool,
) -> None:
    """Prueft eine konkrete Adressmenge gegen die SSRF-Politik.

    Bewusst als eigene Funktion: die Politik muss auf *genau die* Adressen
    angewendet werden, mit denen anschliessend verbunden wird. Eine Pruefung,
    die auf einer anderen Aufloesung basiert als die Verbindung, ist keine.
    """
    if scheme == "http" and (
        not allow_private_network
        or any(not (address.is_private or address.is_loopback) for address in addresses)
    ):
        raise AiProviderConfigurationError("HTTP ist nur fuer explizit freigegebene private Provider erlaubt")

    for address in addresses:
        if address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise AiProviderConfigurationError("Provider-Ziel ist aus SSRF-Schutzgruenden gesperrt")
        if (address.is_private or address.is_loopback) and not allow_private_network:
            raise AiProviderConfigurationError("Private Provider-Ziele benoetigen eine explizite Freigabe")


# Endpunktpfade, die ein Anbieter in seiner Dokumentation als *vollstaendige*
# URL zeigt. OpenRouter dokumentiert z. B. "https://openrouter.ai/api/v1/chat/
# completions". Wird das als Basis-URL eingetragen, haengt der Adapter sein
# eigenes "/chat/completions" an und jede Anfrage endet in einem 404 — sichtbar
# nur als "ai.errors.provider". Der Eintrag ist ein naheliegender
# Bedienfehler, kein Angriff, und wird deshalb still normalisiert.
_ENDPOINT_SUFFIXES = ("/chat/completions", "/completions", "/responses")


def _strip_endpoint_suffix(path: str) -> str:
    """Schneidet einen versehentlich mitkopierten Endpunktpfad ab."""
    lowered = path.lower()
    for suffix in _ENDPOINT_SUFFIXES:
        if lowered.endswith(suffix):
            return path[: -len(suffix)]
    return path


def validate_provider_base_url(base_url: str, *, allow_private_network: bool) -> str:
    """Validiert und normalisiert einen OpenAI-kompatiblen Basis-Endpunkt.

    Private/Loopback-Ziele benoetigen eine explizite Betreiberfreigabe. Link-
    Local-, reservierte und unspezifizierte Ziele bleiben immer gesperrt, damit
    insbesondere Cloud-Metadata-Dienste nicht erreichbar werden.
    """
    value = (base_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AiProviderConfigurationError("Provider-URL muss eine absolute HTTP(S)-URL sein")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AiProviderConfigurationError("Provider-URL darf keine Credentials, Query oder Fragmente enthalten")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise AiProviderConfigurationError("Provider-URL enthaelt einen ungueltigen Port") from exc
    _assert_addresses_allowed(
        _resolved_addresses(parsed.hostname),
        scheme=parsed.scheme,
        allow_private_network=allow_private_network,
    )

    normalized_path = _strip_endpoint_suffix(parsed.path.rstrip("/")).rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))


def _hint(secret: str) -> str:
    return "********" + secret[-4:] if len(secret) >= 4 else "********"


def _operator_aad(provider_id: int) -> str:
    return f"msm:ai:provider:{provider_id}:operator-key"


def create_provider(
    db: Session,
    *,
    name: str,
    base_url: str,
    default_model: str,
    enabled: bool,
    requires_api_key: bool,
    allow_private_network: bool,
    operator_api_key: str | None,
    # Optional: ohne Preis bleiben die Kosten bei null (siehe estimate_cost_microunits).
    token_price_cents_per_million: int | None = None,
) -> AiProvider:
    if not name.strip() or not default_model.strip():
        raise AiProviderConfigurationError("Provider-Name und Modell duerfen nicht leer sein")
    provider = AiProvider(
        name=name.strip(),
        base_url=validate_provider_base_url(
            base_url, allow_private_network=allow_private_network
        ),
        default_model=default_model.strip(),
        enabled=enabled,
        requires_api_key=requires_api_key,
        allow_private_network=allow_private_network,
        token_price_cents_per_million=token_price_cents_per_million,
    )
    db.add(provider)
    db.flush()
    if operator_api_key:
        provider.operator_api_key_encrypted = DisClient.encrypt(
            operator_api_key, aad=_operator_aad(provider.id)
        )
        provider.operator_api_key_hint = _hint(operator_api_key)
    db.flush()
    return provider


def update_provider(
    db: Session,
    provider: AiProvider,
    *,
    values: dict,
    operator_api_key: str | None,
    clear_operator_api_key: bool,
) -> AiProvider:
    if any(field in values and not str(values[field]).strip() for field in ("name", "default_model")):
        raise AiProviderConfigurationError("Provider-Name und Modell duerfen nicht leer sein")
    allow_private = values.get("allow_private_network", provider.allow_private_network)
    if "base_url" in values or "allow_private_network" in values:
        provider.base_url = validate_provider_base_url(
            values.get("base_url", provider.base_url),
            allow_private_network=allow_private,
        )
    for field in ("name", "default_model"):
        if field in values:
            setattr(provider, field, values[field].strip())
    for field in ("enabled", "requires_api_key", "allow_private_network"):
        if field in values:
            setattr(provider, field, values[field])
    if "token_price_cents_per_million" in values:
        provider.token_price_cents_per_million = values['token_price_cents_per_million']
    if clear_operator_api_key:
        provider.operator_api_key_encrypted = None
        provider.operator_api_key_hint = None
    elif operator_api_key:
        provider.operator_api_key_encrypted = DisClient.encrypt(
            operator_api_key, aad=_operator_aad(provider.id)
        )
        provider.operator_api_key_hint = _hint(operator_api_key)
    db.flush()
    return provider


def resolve_api_key(db: Session, provider: AiProvider, user_id: int) -> str | None:
    """Der Schluessel des Betreibers, im Klartext nur an den Adapter.

    Hier stand einmal BYOK: ein Benutzerschluessel wurde **vor** dem des
    Betreibers genommen. Das ist entfallen — der Betreiber stellt Schluessel,
    Modell und Provider. Ein eigener Nutzerschluessel waere ein zweiter
    Abrechnungspfad neben dem, den der Betreiber kalkuliert hat.

    ``db`` und ``user_id`` bleiben in der Signatur, obwohl sie nicht mehr
    gebraucht werden: alle Aufrufer reichen sie durch, und sie wegzunehmen waere
    eine Aenderung an jedem Aufrufpfad fuer einen kosmetischen Gewinn. Sollte
    der Betreiber je Schluessel je Rolle oder je Team einfuehren, sind sie
    genau die Angaben, die es dafuer braucht.
    """
    del db, user_id
    if provider.operator_api_key_encrypted:
        return DisClient.decrypt(
            provider.operator_api_key_encrypted, aad=_operator_aad(provider.id)
        )
    return None


def assert_provider_destination(provider: AiProvider) -> str | None:
    """Revalidiert das Providerziel unmittelbar vor jedem Request.

    Rueckgabe ist die freigegebene IP-Adresse als String — oder ``None``, wenn
    der Host bereits als IP-Literal konfiguriert ist und nichts zu pinnen ist.

    Warum die Adresse zurueckgegeben wird: eine Pruefung, deren Ergebnis danach
    weggeworfen wird, schuetzt nicht vor DNS-Rebinding. Der Client wuerde den
    Namen erneut aufloesen und koennte dabei eine andere — etwa interne —
    Adresse erhalten. Der Aufrufer verbindet sich deshalb mit genau der hier
    geprueften Adresse.

    Entscheidend ist, dass Pruefung und Rueckgabe aus *derselben* Aufloesung
    stammen. Frueher wurde zuerst ueber `validate_provider_base_url` geprueft und
    danach ein zweites, unabhaengiges `getaddrinfo` fuer das Pinning gemacht —
    lieferte das eine andere Adresse, wurde exakt die nie geprueft. Genau die
    Luecke, die diese Funktion schliessen soll.
    """
    parsed = urlparse(
        (provider.base_url or "").strip().rstrip("/")
    )
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AiProviderConfigurationError("Provider-URL muss eine absolute HTTP(S)-URL sein")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AiProviderConfigurationError("Provider-URL darf keine Credentials, Query oder Fragmente enthalten")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise AiProviderConfigurationError("Provider-URL enthaelt einen ungueltigen Port") from exc

    hostname = parsed.hostname
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        # Ein IP-Literal wird nicht aufgeloest, muss aber trotzdem der Politik
        # genuegen — sonst waere `http://169.254.169.254` einfach erlaubt.
        _assert_addresses_allowed(
            {literal},
            scheme=parsed.scheme,
            allow_private_network=provider.allow_private_network,
        )
        return None

    addresses = _resolved_addresses(hostname)
    _assert_addresses_allowed(
        addresses,
        scheme=parsed.scheme,
        allow_private_network=provider.allow_private_network,
    )
    # Deterministisch dieselbe Adresse waehlen, damit Keep-Alive-Verbindungen
    # nicht bei jedem Request auf einen anderen Endpunkt springen.
    return str(sorted(addresses, key=str)[0])


def estimate_cost_microunits(provider: AiProvider, tokens: int) -> int:
    """Rechnet Tokens in Verbrauchskosten um.

    Ohne gepflegten Preis bleibt das Ergebnis null — MSM erfindet keinen Preis.
    Die Rechnung laeuft bewusst ganzzahlig: 1 Cent sind 10.000 Microunits, der
    Preis gilt je eine Million Tokens, also `tokens * cent / 100`.
    """
    price = provider.token_price_cents_per_million
    if not price or tokens <= 0:
        return 0
    return (int(tokens) * int(price)) // 100
