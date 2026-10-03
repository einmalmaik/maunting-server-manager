from __future__ import annotations

from datetime import datetime
import re
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from schemas.passkey import Zweitnachweis


HEX_64_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")
SALZ_REGEX = re.compile(r"^[A-Za-z0-9+/=_-]{16,128}$")

# Obergrenze fuer die Summe aller Ciphertexte eines Requests.
#
# Je Mutation galt 1 MiB, je Request bis zu 100 Mutationen — also 100 MiB pro
# Aufruf. `/blind-sync` braucht kein Konto, und 60 Aufrufe je Minute und Herkunft
# sind erlaubt: das sind 6 GB pro Minute, die ein beliebiger Fremder in die
# Datenbank schreiben darf. Ein Tresor-Eintrag ist in der Praxis deutlich
# kleiner (Anhaenge sind bei 500 KB gedeckelt, `padPayload` rundet auf 4-KiB-
# Bloecke), ein ganzer Sync-Stapel sprengt 8 MiB also nicht.
MAX_MUTATION_PAYLOAD_BYTES = 8 * 1024 * 1024


class VaultMutation(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, description="Eindeutige ID des Eintrags (Client-seitig generiert)")
    ciphertext: str = Field(..., max_length=1048576, description="Vollstaendig verschluesselter AES-GCM Ciphertext-Envelope (sv-vault-v1:)")
    revision: int = Field(..., ge=0, description="Lokale Revisionsnummer")
    is_deleted: bool = Field(default=False, description="Tombstone-Flag fuer Loeschungen")
    # Nur fuer endgueltiges Loeschen: geschrieben wird nur, wenn der Eintrag
    # noch auf dieser Revision steht. Sonst hat ein anderes Geraet ihn
    # inzwischen geaendert (etwa aus dem Papierkorb geholt), und dessen
    # Fassung bleibt. Die Reihenfolge entscheidet weiter der Stand im Umschlag.
    expected_revision: Optional[int] = Field(default=None, ge=0, le=9007199254740991)


def _pruefe_gesamtgroesse(mutations: List["VaultMutation"]) -> List["VaultMutation"]:
    gesamt = sum(len(m.ciphertext) for m in mutations)
    if gesamt > MAX_MUTATION_PAYLOAD_BYTES:
        raise ValueError(
            f"Gesamtgroesse der Mutationen ueberschreitet {MAX_MUTATION_PAYLOAD_BYTES} Bytes"
        )
    return mutations


# Das neueste Eintragsformat, das ein Bucket verlangen darf (Frontend:
# `VAULT_EINTRAG_FORMAT`). Wird mit jedem neuen Format angehoben.
VAULT_FORMAT_BEKANNT = 1


class VaultSyncRequest(BaseModel):
    bucket_id: str = Field(..., min_length=64, max_length=64, description="Blinde 64-Hex Bucket-ID, abgeleitet aus dem Client-Master-Secret")
    since_revision: int = Field(default=0, ge=0, le=9007199254740991, description="Revisions-Wasserzeichen des Clients")
    mutations: List[VaultMutation] = Field(default_factory=list, max_length=100, description="Neue oder aktualisierte verschluesselte Eintraege")
    # Welches Eintragsformat die App versteht, und ab welchem sie den Bucket
    # nur noch beschreiben laesst (siehe `VaultBucketFormat`). Aeltere Apps
    # schicken beides nicht.
    client_format: Optional[int] = Field(default=None, ge=0, le=1000)
    # Nie hoeher, als dieser Server kennt: sonst sperrt ein einziger Aufruf
    # mit erfundenem Format jede echte App fuer immer aus.
    min_client_format: Optional[int] = Field(default=None, ge=0, le=VAULT_FORMAT_BEKANNT)

    @field_validator("bucket_id")
    @classmethod
    def validate_bucket_id(cls, v: str) -> str:
        if not HEX_64_REGEX.match(v):
            raise ValueError("bucket_id must be a 64-character hex string")
        return v.lower()

    @field_validator("mutations")
    @classmethod
    def validate_mutations_size(cls, v: List[VaultMutation]) -> List[VaultMutation]:
        return _pruefe_gesamtgroesse(v)


class VaultBlindSyncRequest(BaseModel):
    bucket_id: str = Field(..., min_length=64, max_length=64, description="Blinde 64-Hex Bucket-ID")
    auth_token: str = Field(..., min_length=64, max_length=64, description="Blinder Besitznachweis (SHA-256 Hex)")
    since_revision: int = Field(default=0, ge=0, le=9007199254740991, description="Revisions-Wasserzeichen des Clients")
    mutations: List[VaultMutation] = Field(default_factory=list, max_length=100, description="Neue oder aktualisierte verschluesselte Eintraege")
    # Welches Eintragsformat die App versteht, und ab welchem sie den Bucket
    # nur noch beschreiben laesst (siehe `VaultBucketFormat`). Aeltere Apps
    # schicken beides nicht.
    client_format: Optional[int] = Field(default=None, ge=0, le=1000)
    # Nie hoeher, als dieser Server kennt: sonst sperrt ein einziger Aufruf
    # mit erfundenem Format jede echte App fuer immer aus.
    min_client_format: Optional[int] = Field(default=None, ge=0, le=VAULT_FORMAT_BEKANNT)

    @field_validator("bucket_id")
    @classmethod
    def validate_bucket_id(cls, v: str) -> str:
        if not HEX_64_REGEX.match(v):
            raise ValueError("bucket_id must be a 64-character hex string")
        return v.lower()

    @field_validator("auth_token")
    @classmethod
    def validate_auth_token(cls, v: str) -> str:
        if not HEX_64_REGEX.match(v):
            raise ValueError("auth_token must be a 64-character hex string")
        return v.lower()

    @field_validator("mutations")
    @classmethod
    def validate_mutations_size(cls, v: List[VaultMutation]) -> List[VaultMutation]:
        return _pruefe_gesamtgroesse(v)


class VaultBlindRegisterRequest(BaseModel):
    """Authentifizierte Bindung des blinden Besitznachweises an den eigenen Bucket."""

    bucket_id: str = Field(..., min_length=64, max_length=64, description="64-Hex Bucket-ID")
    auth_token: str = Field(..., min_length=64, max_length=64, description="Blinder Besitznachweis (SHA-256 Hex)")

    @field_validator("bucket_id", "auth_token")
    @classmethod
    def validate_hex(cls, v: str) -> str:
        if not HEX_64_REGEX.match(v):
            raise ValueError("value must be a 64-character hex string")
        return v.lower()


class VaultBlindCheckRequest(VaultBlindRegisterRequest):
    """Unauthentifizierte Probe, ob ein blinder Besitznachweis passt. Legt nichts an."""


class VaultResetRequest(BaseModel):
    """Tresor zuruecksetzen: dieselben Nachweise wie beim Loeschen des Kontos.

    Das Konto-Passwort, wenn eins hinterlegt ist; bei aktiver 2FA zusaetzlich
    ein eingerichteter Faktor; immer das Wort „delete", das die Seite nicht
    einfuegen laesst.
    """

    password: Optional[str] = Field(default=None, max_length=256)
    otp_code: Optional[str] = Field(default=None, pattern=r"^\d{6}$")
    passkey: Optional[Zweitnachweis] = None
    confirmation: str = Field(default="", max_length=32)


class VaultEntryOut(BaseModel):
    id: str
    ciphertext: str
    revision: int
    is_deleted: bool
    updated_at: datetime


class VaultSyncResponse(BaseModel):
    server_revision: int
    entries: List[VaultEntryOut]
    # IDs der Mutationen, die wegen `expected_revision` nicht geschrieben wurden.
    conflicts: List[str] = Field(default_factory=list)
    # Es liegen weitere Eintraege ueber `server_revision` bereit.
    has_more: bool = False


class VaultHintSetRequest(BaseModel):
    hint: str = Field(..., min_length=1, max_length=512, description="Passwort-Hinweis fuer das Master-Passwort")


class VaultHintStatusResponse(BaseModel):
    has_hint: bool
    last_requested_at: datetime | None = None
    can_request: bool = True
    cooldown_seconds_remaining: int = 0


class VaultSaltResponse(BaseModel):
    kdf_salt: str | None = None
    bucket_id: str | None = None
    has_vault: bool = False


class VaultSaltSetRequest(BaseModel):
    kdf_salt: str = Field(..., min_length=16, max_length=128, description="Base64- oder Hex-kodierter KDF-Salt")
    bucket_id: str = Field(..., min_length=64, max_length=64, description="64-Hex Bucket-ID")
    # Nur noetig, wenn der Bucket schon blind registriert ist (erster Abgleich
    # lief ohne `/salt`). Dann belegt er den Besitz.
    auth_token: Optional[str] = Field(default=None, min_length=64, max_length=64, description="Blinder Besitznachweis (SHA-256 Hex)")

    @field_validator("auth_token")
    @classmethod
    def validate_auth_token(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not HEX_64_REGEX.match(v):
            raise ValueError("auth_token must be a 64-character hex string")
        return v.lower()

    @field_validator("bucket_id")
    @classmethod
    def validate_bucket_id(cls, v: str) -> str:
        if not HEX_64_REGEX.match(v):
            raise ValueError("bucket_id must be a 64-character hex string")
        return v.lower()

    @field_validator("kdf_salt")
    @classmethod
    def validate_kdf_salt(cls, v: str) -> str:
        # Nur Hex- oder Base64-Zeichen. Ein Salz aus Leerzeichen bestand bis
        # 02.10.2026 die Laengenpruefung und wurde gekuerzt als "" gespeichert:
        # danach galt es als nicht gesetzt und liess sich ueberschreiben.
        v = v.strip()
        if not SALZ_REGEX.match(v):
            raise ValueError("kdf_salt must be 16-128 hex or base64 characters")
        return v


class VaultBlobAnlegen(BaseModel):
    """Ein neuer Blob: Kennung, Chunkzahl, Groesse des Chiffrats und der Hash
    des Loeschschluessels. Mehr erfaehrt der Server ueber die Datei nicht."""

    id: str = Field(..., min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    chunk_count: int = Field(..., ge=1, le=1_000_000)
    bytes_total: int = Field(..., ge=1, le=1024 ** 4)
    delete_verifier: str = Field(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class VaultBlobLoeschen(BaseModel):
    schluessel: str = Field(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class VaultBlobsKlein(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=100)


class VaultEingangAnlegen(BaseModel):
    """Ein Datensatz fuer den Posteingang: Kennung vom Client, Inhalt nur Chiffrat."""

    id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    # Druckbares ASCII: der Umschlag ist JSON mit Base64 darin.
    ciphertext: str = Field(..., min_length=1, max_length=16 * 1024, pattern=r"^[ -~]+$")


class VaultEingangDatensatz(BaseModel):
    id: str
    ciphertext: str
    created_at: datetime


class VaultEingangListe(BaseModel):
    eintraege: List[VaultEingangDatensatz]
    # Kennung, ab der die naechste Seite beginnt; fehlt auf der letzten.
    weiter: Optional[str] = None


class VaultBlobStatus(BaseModel):
    state: str
    chunk_count: int
    vorhanden: List[int]


class VaultSpeicher(BaseModel):
    belegt: int
    quote: int
    in_loeschung: int
    #: Nicht geloeschte Blobs. Je Datei und Fassung sind es drei.
    blobs: int
