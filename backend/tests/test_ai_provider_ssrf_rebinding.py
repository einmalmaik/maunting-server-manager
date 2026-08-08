"""DNS-Rebinding: die gepinnte Adresse muss die geprueufte Adresse sein.

`assert_provider_destination` gab frueher eine Adresse aus einer *zweiten*,
ungeprueften Namensaufloesung zurueck: erst pruefte `validate_provider_base_url`
eine Aufloesung, danach fragte die Funktion den Resolver erneut und pinnte
`sorted(...)[0]` daraus. Liefert der Resolver dazwischen etwas anderes — genau
das ist DNS-Rebinding —, verband sich der Adapter mit einer Adresse, die nie
gegen die Sperrliste gehalten wurde.

Die bestehenden Tests konnten das nicht finden, weil sie `_resolved_addresses`
auf einen konstanten Wert monkeypatchen. Hier antwortet der Resolver bewusst bei
jedem Aufruf anders.
"""

from __future__ import annotations

import ipaddress

import pytest

from models import AiProvider
from services import ai_provider_service


def _provider(**overrides) -> AiProvider:
    values = {
        "id": 1,
        "name": "Rebind",
        "base_url": "https://api.example.invalid/v1",
        "default_model": "model-a",
        "enabled": True,
        "requires_api_key": False,
        "allow_private_network": False,
    }
    values.update(overrides)
    return AiProvider(**values)


def _sequence_resolver(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[int]:
    """Ersetzt den Resolver durch eine Folge von Antworten.

    Die letzte Antwort wiederholt sich, damit ein zusaetzlicher Aufruf den Test
    nicht mit einem IndexError beendet statt mit einer Aussage.
    """
    calls = [0]

    def resolve(_host: str) -> set:
        index = min(calls[0], len(answers) - 1)
        calls[0] += 1
        return {ipaddress.ip_address(answers[index])}

    monkeypatch.setattr(ai_provider_service, "_resolved_addresses", resolve)
    return calls


@pytest.mark.parametrize("rebound", ["10.0.0.5", "169.254.169.254", "127.0.0.1"])
def test_a_second_resolution_can_not_smuggle_in_an_unchecked_address(
    monkeypatch: pytest.MonkeyPatch, rebound: str
) -> None:
    """Erst oeffentlich (wie bei der Anlage), dann intern — der Angriff.

    Auf dem alten Code lief die Politikpruefung gegen die erste Aufloesung,
    gepinnt wurde aber die zweite: die Funktion gab `rebound` zurueck und der
    Adapter verband sich dorthin. Heute existiert die zweite Aufloesung nicht
    mehr, deshalb kann nur die geprueufte Adresse herauskommen.

    169.254.169.254 ist dabei der teuerste Einzelfall — der Cloud-Metadata-Dienst.
    """
    _sequence_resolver(monkeypatch, "93.184.216.34", rebound)

    pinned = ai_provider_service.assert_provider_destination(_provider())

    assert pinned == "93.184.216.34"
    address = ipaddress.ip_address(pinned)
    assert not (address.is_private or address.is_loopback or address.is_link_local)


@pytest.mark.parametrize("internal", ["10.0.0.5", "169.254.169.254", "127.0.0.1"])
def test_an_internal_resolution_is_rejected_outright(
    monkeypatch: pytest.MonkeyPatch, internal: str
) -> None:
    """Loest der Name direkt nach innen auf, gibt es nichts zu pinnen."""
    _sequence_resolver(monkeypatch, internal)

    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.assert_provider_destination(_provider())


def test_pinned_address_comes_from_exactly_one_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genau eine Aufloesung — sonst gibt es wieder ein Fenster dazwischen."""
    calls = _sequence_resolver(monkeypatch, "93.184.216.34")

    assert ai_provider_service.assert_provider_destination(_provider()) == "93.184.216.34"
    assert calls[0] == 1, (
        "Mehr als eine Namensaufloesung oeffnet erneut ein Rebinding-Fenster "
        f"(Aufrufe: {calls[0]})"
    )


def test_private_target_stays_allowed_with_explicit_operator_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein lokal betriebenes Modell darf weiterhin funktionieren."""
    _sequence_resolver(monkeypatch, "10.0.0.5")

    provider = _provider(base_url="http://ollama.internal/v1", allow_private_network=True)
    assert ai_provider_service.assert_provider_destination(provider) == "10.0.0.5"


def test_ip_literal_is_still_checked_against_the_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein IP-Literal wird nicht aufgeloest — geprueft werden muss es trotzdem.

    Sonst waere `https://169.254.169.254/v1` schlicht erlaubt, weil an der
    Aufloesung nichts zu pruefen ist.
    """
    _sequence_resolver(monkeypatch, "93.184.216.34")

    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.assert_provider_destination(
            _provider(base_url="https://169.254.169.254/v1")
        )

    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.assert_provider_destination(
            _provider(base_url="https://10.0.0.5/v1")
        )

    public = _provider(base_url="https://93.184.216.34/v1")
    assert ai_provider_service.assert_provider_destination(public) is None
