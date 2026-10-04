"""Was der Browser nach `navigator.credentials.create()` / `.get()` schickt.

Binaerfelder als Base64url, wie sie `PublicKeyCredential.toJSON()` liefert.
Die Grenzen sind grosszuegig, aber endlich: eine Credential-ID hat hoechstens
1023 Byte, ein RSA-4096-Schluessel knapp 600 Byte DER.
"""

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

_B64 = r"^[A-Za-z0-9_-]*$"


class PasskeyNachweisInhalt(BaseModel):
    clientDataJSON: str = Field(max_length=4096, pattern=_B64)
    authenticatorData: str = Field(max_length=4096, pattern=_B64)
    signature: str = Field(max_length=2048, pattern=_B64)
    userHandle: str | None = Field(default=None, max_length=128, pattern=_B64)


class PasskeyNachweis(BaseModel):
    """Antwort auf eine Bestaetigung (§5.2.2 AuthenticatorAssertionResponse)."""

    id: str = Field(max_length=1400, pattern=_B64)
    rawId: str = Field(max_length=1400, pattern=_B64)
    type: Literal["public-key"]
    response: PasskeyNachweisInhalt


class BrowserNachweis(BaseModel):
    """Verweis auf eine Bestaetigung, die im Browser mit dem Passkey geschah.

    Fuer die Desktop-App: ihr Passkey-Konto hat keinen Passkey fuer
    `tauri.localhost`. Sie oeffnet das Panel im Browser, dort bestaetigt der
    Passkey, und die App reicht hier nur die Kennung des Vorgangs weiter
    (`passkey_service.browser_*`).
    """

    type: Literal["browser"]
    vorgang: str = Field(min_length=20, max_length=64, pattern=_B64)


#: Was vor einer geschuetzten Aktion als zweiter Faktor kommt, wenn das Konto
#: einen Passkey hat: die Antwort des Browsers oder der Verweis auf eine
#: Bestaetigung im Browser. Nur fuer angemeldete Zwecke, nie beim Login.
Zweitnachweis = Annotated[Union[PasskeyNachweis, BrowserNachweis], Field(discriminator="type")]


class BrowserVorgangRequest(BaseModel):
    zweck: str = Field(max_length=32)


class BrowserVorgangKennung(BaseModel):
    vorgang: str = Field(min_length=20, max_length=64, pattern=_B64)


class BrowserBestaetigungRequest(BaseModel):
    vorgang: str = Field(min_length=20, max_length=64, pattern=_B64)
    zahl: int = Field(ge=10, le=99)
    passkey: PasskeyNachweis


class PasskeyAnlageInhalt(BaseModel):
    clientDataJSON: str = Field(max_length=4096, pattern=_B64)
    authenticatorData: str = Field(max_length=8192, pattern=_B64)
    publicKey: str = Field(max_length=2048, pattern=_B64)
    publicKeyAlgorithm: int
    transports: list[str] = Field(default_factory=list, max_length=8)


class PasskeyAnlage(BaseModel):
    """Antwort auf das Anlegen (§5.2.1 AuthenticatorAttestationResponse)."""

    id: str = Field(max_length=1400, pattern=_B64)
    rawId: str = Field(max_length=1400, pattern=_B64)
    type: Literal["public-key"]
    response: PasskeyAnlageInhalt


class PasskeyOptionenRequest(BaseModel):
    zweck: str = Field(max_length=32)


class TwoFactorDisableRequest(BaseModel):
    """Body von `/2fa/disable` fuer Passkey-Konten. TOTP bleibt im Query-Parameter."""

    passkey: Zweitnachweis | None = None


class FaktorNachweis(BaseModel):
    """Nachweis, bevor ein zweiter Faktor dazukommt oder wegfaellt.

    Bei aktiver 2FA ein eingerichteter Faktor, sonst das Passwort
    (`passkey_service.frischer_nachweis_fehlt`). Ein Backup-Code gilt nur fuer
    einen neuen Faktor (App einrichten, Passkey anlegen) und wird dabei
    verbraucht. Entfernen, Abschalten und neue Codes nimmt er nicht an.

    Bis 04.10.2026 galt er hier gar nicht. Ein Konto nur mit dem Passkey vom
    PC kam am neuen Handy zwar per Backup-Code hinein, konnte dort aber
    keinen Passkey anlegen: dafuer verlangte das Profil den Passkey vom PC.
    """

    password: str | None = Field(None, max_length=256)
    otp_code: str | None = Field(None, pattern=r"^\d{6}$")
    passkey: Zweitnachweis | None = None
    backup_code: str | None = Field(None, max_length=16)


class PasskeyHinzufuegen(PasskeyAnlage):
    name: str | None = Field(None, max_length=64)


class PasskeyEintrag(BaseModel):
    id: int
    name: str | None
    created_at: datetime | None
    last_used_at: datetime | None
