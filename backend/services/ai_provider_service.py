"""Sichere Provider-Konfiguration ohne SDK- oder Secret-Leaks."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from models import AiProvider, AiUserCredential
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

    normalized_path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))


def _hint(secret: str) -> str:
    return "********" + secret[-4:] if len(secret) >= 4 else "********"


def _operator_aad(provider_id: int) -> str:
    return f"msm:ai:provider:{provider_id}:operator-key"


def _user_aad(provider_id: int, user_id: int) -> str:
    return f"msm:ai:provider:{provider_id}:user:{user_id}:api-key"


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


def set_user_credential(
    db: Session, *, user_id: int, provider_id: int, api_key: str
) -> AiUserCredential:
    if not api_key.strip():
        raise AiProviderConfigurationError("API-Key darf nicht leer sein")
    credential = (
        db.query(AiUserCredential)
        .filter(
            AiUserCredential.user_id == user_id,
            AiUserCredential.provider_id == provider_id,
        )
        .first()
    )
    encrypted = DisClient.encrypt(api_key, aad=_user_aad(provider_id, user_id))
    if credential is None:
        credential = AiUserCredential(
            user_id=user_id,
            provider_id=provider_id,
            api_key_encrypted=encrypted,
            api_key_hint=_hint(api_key),
        )
        db.add(credential)
    else:
        credential.api_key_encrypted = encrypted
        credential.api_key_hint = _hint(api_key)
    db.flush()
    return credential


def resolve_api_key(db: Session, provider: AiProvider, user_id: int) -> str | None:
    """Nutzt BYOK vor Betreiber-Key und gibt den Klartext nur an den Adapter."""
    credential = (
        db.query(AiUserCredential)
        .filter(
            AiUserCredential.user_id == user_id,
            AiUserCredential.provider_id == provider.id,
        )
        .first()
    )
    if credential is not None:
        return DisClient.decrypt(
            credential.api_key_encrypted, aad=_user_aad(provider.id, user_id)
        )
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
