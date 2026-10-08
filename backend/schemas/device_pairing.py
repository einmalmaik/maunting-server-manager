"""Ein- und Ausgaben der Geraetekopplung.

Der Kopplungscode taucht in genau **einem** Schema auf: `PairingCreated`, der
Antwort auf die Anfrage, die ihn erzeugt hat. Nirgends sonst — er ist ein
Geheimnis mit zehn Minuten Haltbarkeit, und ein zweites Schema damit waere ein
zweiter Weg, auf dem er in ein Protokoll geraet.
"""

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field

from schemas.passkey import Zweitnachweis
from services.device_pairing_service import MAX_BEZEICHNUNG, MAX_VERLAUF_BYTES


class PairingCreateRequest(BaseModel):
    """Was der Benutzer im Panel angibt: wie das Geraet heissen soll, und der
    Nachweis, dass er es selbst ist (Passwort, bei 2FA der Code oder der Passkey)."""

    label: str = Field(default="", max_length=MAX_BEZEICHNUNG)
    password: str = Field(default="", max_length=256)
    otp_code: str = Field(default="", max_length=16)
    passkey: Zweitnachweis | None = None


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
    # Welche App koppelt. Nur `desktop` (MSS) bekommt die Werkzeuge fuer den
    # Rechner; der Browser (MSB) ist fuer die KI eine Panel-Sitzung.
    geraet: Literal["desktop", "browser"] = "desktop"


class PairedDevice(BaseModel):
    """Ein Eintrag der Geraeteliste. Traegt die Familie, weil daran der Widerruf
    haengt — aber nie ein Token und nie den Code."""

    family: str
    label: str
    paired_at: datetime | None = None
    is_active: bool = True
    last_active_at: datetime | None = None


class VerlaufAblegen(BaseModel):
    """Der versiegelte Verlauf fuer das frisch gekoppelte Geraet.

    Ein Textblock und sonst nichts. Was drinsteht, geht den Server nichts an:
    versiegelt wurde gegen den Geraeteschluessel des neuen Geraets, und der
    liegt nur dort. Der Deckel ist derselbe wie bei Medien — er begrenzt eine
    Anfrage, er schuetzt keinen Inhalt.
    """

    blob: str = Field(min_length=1, max_length=MAX_VERLAUF_BYTES)


class VerlaufAntwort(BaseModel):
    """Was das neue Geraet abholt. ``None`` heisst: liegt (noch) nichts."""

    blob: str | None = None
