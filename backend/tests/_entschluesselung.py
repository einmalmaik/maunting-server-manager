"""Was ein Gedächtnisweg am DIS-Sidecar öffnet — gezählt und gestört.

Seit Gedächtnis v2 gehen Text, Titel und Name einer Erinnerung gemeinsam durch
`DisClient.decrypt_many`; ein `decrypt` je Zeile gibt es auf den Lesewegen
nicht mehr. Wer den Aufwand messen will, zählt deshalb zwei Größen getrennt:

* ``texte`` — wie viele Erinnerungstexte geöffnet wurden. Das begrenzt ein
  Deckel, gleich auf welchem Weg sie durch den Sidecar gingen.
* ``aufrufe`` — wie viele Roundtrips das gekostet hat. Darum geht es beim
  Bündeln.

Titel, Namen, Themen und frühere Fassungen tragen eigene AAD-Formen
(``msm:ai:memory:<feld>:…``) und zählen nicht als Text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from services.dis_client import DisClient, DisDecryptionError, DisSidecarError

_NEBENFELDER = frozenset({"key", "titel", "thema", "version"})


def ist_text(aad: str | None) -> bool:
    """Ob ``aad`` zum Text einer Erinnerung gehört (`ai_memory_service._aad`)."""
    if not aad or not aad.startswith("msm:ai:memory:"):
        return False
    return aad.split(":", 4)[3] not in _NEBENFELDER


@dataclass
class Sidecarzaehler:
    #: Die AADs je `decrypt_many`-Aufruf, in Aufrufreihenfolge.
    stapel: list[list[str]] = field(default_factory=list)
    #: Die AAD je einzelnem `decrypt`.
    einzeln: list[str] = field(default_factory=list)

    @property
    def texte(self) -> int:
        alle = [aad for aads in self.stapel for aad in aads] + self.einzeln
        return sum(1 for aad in alle if ist_text(aad))

    @property
    def aufrufe(self) -> int:
        return len(self.stapel) + len(self.einzeln)


def mitzaehlen(monkeypatch: pytest.MonkeyPatch) -> Sidecarzaehler:
    """Zählt ab jetzt jeden Entschlüsselungsaufruf, ohne ihn zu verändern."""
    zaehler = Sidecarzaehler()
    viele = DisClient.decrypt_many
    eins = DisClient.decrypt

    def decrypt_many(items):
        zaehler.stapel.append([aad or "" for _chiffrat, aad in items])
        return viele(items)

    def decrypt(payload, *, aad=None):
        zaehler.einzeln.append(aad or "")
        return eins(payload, aad=aad)

    monkeypatch.setattr(DisClient, "decrypt_many", staticmethod(decrypt_many))
    monkeypatch.setattr(DisClient, "decrypt", staticmethod(decrypt))
    return zaehler


def unlesbar_machen(monkeypatch: pytest.MonkeyPatch, *kennungen: str) -> None:
    """Die Texte dieser Erinnerungen lassen sich nicht mehr öffnen.

    Wie nach verdrehter AAD oder gewechseltem Schlüssel: der Sidecar antwortet,
    nur dieser eine Wert nicht. `decrypt_many` liefert dafür ``None``,
    `decrypt` wirft — genau wie der echte Sidecar.
    """
    viele = DisClient.decrypt_many
    eins = DisClient.decrypt

    def kaputt(aad: str | None) -> bool:
        return ist_text(aad) and any((aad or "").endswith(k) for k in kennungen)

    def decrypt_many(items):
        echt = viele(items)
        return [None if kaputt(aad) else klar for (_c, aad), klar in zip(items, echt)]

    def decrypt(payload, *, aad=None):
        if kaputt(aad):
            raise DisDecryptionError("AAD passt nicht mehr")
        return eins(payload, aad=aad)

    monkeypatch.setattr(DisClient, "decrypt_many", staticmethod(decrypt_many))
    monkeypatch.setattr(DisClient, "decrypt", staticmethod(decrypt))


def sidecar_tot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Sidecar antwortet gar nicht mehr — jeder Lesevorgang scheitert."""

    def tot(*_args, **_kwargs):
        raise DisSidecarError("Sidecar nicht erreichbar")

    monkeypatch.setattr(DisClient, "decrypt_many", staticmethod(tot))
    monkeypatch.setattr(DisClient, "decrypt", staticmethod(tot))
