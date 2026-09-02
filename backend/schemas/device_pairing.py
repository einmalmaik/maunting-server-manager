"""Ein- und Ausgaben der Geraetekopplung.

Der Kopplungscode taucht in genau **einem** Schema auf: `PairingCreated`, der
Antwort auf die Anfrage, die ihn erzeugt hat. Nirgends sonst — er ist ein
Geheimnis mit zehn Minuten Haltbarkeit, und ein zweites Schema damit waere ein
zweiter Weg, auf dem er in ein Protokoll geraet.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from services.device_pairing_service import MAX_BEZEICHNUNG


class PairingCreateRequest(BaseModel):
    """Was der Benutzer im Panel angibt: wie das Geraet heissen soll."""

    label: str = Field(default="", max_length=MAX_BEZEICHNUNG)


class PairingCreated(BaseModel):
    """Der Code — einmal, hier, sonst nirgendwo."""

    code: str
    expires_at: datetime
    label: str
    qr_data_uri: str | None = None


class PairingRedeemRequest(BaseModel):
    """Was die App schickt. Der Code wird nachsichtig gelesen, deshalb grosszuegig
    bemessen: Striche, Leerzeichen und Kleinschreibung fallen beim Vergleich weg
    (`device_pairing_service.normalisieren`)."""

    code: str = Field(min_length=8, max_length=64)
    label: str = Field(default="", max_length=MAX_BEZEICHNUNG)


class PairedDevice(BaseModel):
    """Ein Eintrag der Geraeteliste. Traegt die Familie, weil daran der Widerruf
    haengt — aber nie ein Token und nie den Code."""

    family: str
    label: str
    paired_at: datetime | None = None
