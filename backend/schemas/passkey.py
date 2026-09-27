"""Was der Browser nach `navigator.credentials.create()` / `.get()` schickt.

Binaerfelder als Base64url, wie sie `PublicKeyCredential.toJSON()` liefert.
Die Grenzen sind grosszuegig, aber endlich: eine Credential-ID hat hoechstens
1023 Byte, ein RSA-4096-Schluessel knapp 600 Byte DER.
"""

from typing import Literal

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

    passkey: PasskeyNachweis | None = None
