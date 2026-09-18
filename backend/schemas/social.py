from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_serializer, field_validator


class AchievementResponse(BaseModel):
    id: str
    title: str
    description: str
    category: str
    points: int
    icon: str
    unlocked: bool = False
    unlocked_at: datetime | None = None
    global_unlocked_percentage: float = 0.0
    rarity_tier: str = "common"
    rarity_text: str = ""


class AchievementsOverviewResponse(BaseModel):
    achievements: list[AchievementResponse] = Field(default_factory=list)
    total_unlocked: int = 0
    total_available: int = 0
    prestige_score: int = 0


class UserStatsResponse(BaseModel):
    total_achievements: int = 0
    unlocked_achievements: int = 0
    total_points: int = 0
    earned_points: int = 0
    active_time_seconds: int = 0
    active_time_by_category: dict[str, int] = Field(default_factory=dict)
    # Frontend aliases
    achievements_unlocked: int = 0
    total_activity_seconds: int = 0
    categories: dict[str, int] = Field(default_factory=dict)


class PresenceInfo(BaseModel):
    status: str = "offline"
    device_type: str = "web"
    custom_status: str | None = None
    activity_label: str | None = None
    activity_detail: str | None = None
    updated_at: datetime | None = None


class FriendResponse(BaseModel):
    id: int
    user_id: int
    username: str
    avatar_url: str | None = None
    status: str
    is_requester: bool = False
    created_at: datetime
    presence: PresenceInfo | None = None


class FriendRequestCreate(BaseModel):
    username: str = ""
    target_username: str | None = None

    def __init__(self, **data):
        if "target_username" in data and not data.get("username"):
            data["username"] = data["target_username"]
        super().__init__(**data)


class PresenceUpdateRequest(BaseModel):
    status: str = Field("online", pattern="^(online|away|invisible)$")
    device_type: str | None = Field(None, pattern="^(web|desktop|mobile)$")
    custom_status: str | None = Field(None, max_length=128)
    activity_label: str | None = Field(None, max_length=128)
    activity_detail: str | None = Field(None, max_length=128)


class ActivityPingRequest(BaseModel):
    category: str = Field("general", max_length=32)
    seconds: int = Field(30, ge=1, le=300)


class PrivacyUpdateRequest(BaseModel):
    privacy: str = Field(..., pattern="^(private|friends|public)$")


import json

import re

# Was neu in eine Mailbox geschrieben werden darf.
#
# Geprüft wird ausschließlich beim Schreiben: bereits abgelegte Umschläge alter
# Formate bleiben liegen, es kann nur nichts Neues in ihnen entstehen. Damit
# setzt der Server die Abschaffung der unsicheren Kanalableitungen durch, statt
# sich darauf zu verlassen, dass jeder Client mitzieht.
#
# Gestrichen wurden `sv-e2ee-v1:` und `sv-e2ee-team-v1:` — beide leiteten ihren
# Schlüssel aus den Benutzerkennungen ab (`sha256("msm:dm:key:<a>:<b>")`), die in
# der Datenbank stehen. Der Server konnte sie mitlesen; das ist keine
# Ende-zu-Ende-Verschlüsselung, sondern eine, die genau den nicht aussperrt, den
# sie aussperren soll. `sv-e2ee-ratchet-v1:` war eine SHA-256-Kette ohne
# DH-Schritt und hatte nie einen Aufrufer.
VALID_E2EE_PREFIXES = (
    "sv-e2ee-group-v1:",
    "sv-e2ee-hybrid-v1:",
    "sv-e2ee-dr-v1:",
)

# Gerätekennung im Klartextkopf eines Double-Ratchet-Umschlags. Kein Punkt, der
# trennt die Felder.
_GERAETEKENNUNG = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Kennung des Gruppenschlüssels: die ersten 16 Hexzeichen seines SHA-256.
_GRUPPEN_KEY_ID = re.compile(r"^[0-9a-f]{16}$")


def validate_rsa_public_key_jwk(key_str: str) -> dict:
    """Validiert, dass ein übergebener String ein sicherer RSA-OAEP Public Key im JWK-Format ist."""
    if not isinstance(key_str, str) or not key_str.strip():
        raise ValueError("Public Key darf nicht leer sein.")
    try:
        data = json.loads(key_str)
    except Exception as exc:
        raise ValueError("Public Key ist kein gültiges JSON.") from exc

    if not isinstance(data, dict):
        raise ValueError("Public Key JWK muss ein JSON-Objekt sein.")

    if data.get("kty") != "RSA":
        raise ValueError(f"Ungültiger Schlüsseltyp: erwartet 'RSA', erhalten '{data.get('kty')}'.")

    # RFC 7517 / RFC 3447 private key components
    forbidden_private_keys = {"d", "p", "q", "dp", "dq", "qi", "dmp1", "dmq1", "coeff", "oth"}
    present_forbidden = forbidden_private_keys.intersection(data.keys())
    if present_forbidden:
        raise ValueError(
            f"Sicherheitsverletzung: Private Schlüsselparameter ({', '.join(sorted(present_forbidden))}) dürfen nicht im Public Key enthalten sein."
        )

    modulus = data.get("n")
    exponent = data.get("e")
    if not modulus or not isinstance(modulus, str) or not exponent or not isinstance(exponent, str):
        raise ValueError("Unvollständiger RSA-Schlüssel: 'n' (Modulus) und 'e' (Exponent) sind erforderlich.")

    if len(modulus) < 300:
        raise ValueError("Unsichere Schlüssellänge: Mindestens RSA-2048 erforderlich.")

    # Algorithm confusion and key misuse prevention
    if "alg" in data and data["alg"]:
        valid_algs = {"RSA-OAEP", "RSA-OAEP-256", "RSA-OAEP-384", "RSA-OAEP-512"}
        if data["alg"] not in valid_algs:
            raise ValueError(
                f"Sicherheitsverletzung: Nicht unterstützter oder unsicherer Algorithmus '{data['alg']}' für E2EE Public Key."
            )

    if "use" in data and data["use"]:
        if data["use"] != "enc":
            raise ValueError(
                f"Sicherheitsverletzung: Ungültige Schlüsselverwendung '{data['use']}' für E2EE Verschlüsselungsschlüssel (erwartet 'enc')."
            )

    if "key_ops" in data and isinstance(data["key_ops"], list):
        forbidden_ops = {"sign", "verify"}
        if forbidden_ops.intersection(data["key_ops"]):
            raise ValueError("Sicherheitsverletzung: Signatur-Operationen sind in E2EE Public Keys verboten.")

    return data


def validate_e2ee_envelope_format(envelope_str: str) -> None:
    """Validiert, dass ein Umschlag ein gültiges DIS E2EE-Format besitzt und kein Plaintext ist."""
    import base64

    if not isinstance(envelope_str, str) or not envelope_str.strip():
        raise ValueError("Umschlag darf nicht leer sein.")

    trimmed = envelope_str.strip()
    matched_prefix = None
    for prefix in VALID_E2EE_PREFIXES:
        if trimmed.startswith(prefix):
            matched_prefix = prefix
            break

    if not matched_prefix:
        raise ValueError(
            "Ungültiges E2EE-Umschlagformat: Nur versionierte DIS-Umschläge ("
            + ", ".join(VALID_E2EE_PREFIXES)
            + ") werden akzeptiert. Plaintext ist verboten."
        )

    payload = trimmed[len(matched_prefix):].strip()
    if not payload:
        raise ValueError("Umschlag-Payload darf nicht leer sein.")

    # Plaintext-Leak-Erkennung
    if (
        payload.startswith("{")
        or payload.startswith("[")
        or '"text":' in payload
        or '"sender_id":' in payload
        or '"ciphertext":' in payload
    ):
        raise ValueError("Sicherheitsverletzung: Unverschlüsselter Klartext-Payload im E2EE-Umschlag erkannt.")

    if any(c in payload for c in "\r\n\t"):
        raise ValueError("Ungültige Steuerzeichen im E2EE-Umschlag erkannt.")

    if matched_prefix == "sv-e2ee-dr-v1:":
        # <vonKonto>.<vonGeraet>.<fuerGeraet>.<base64(sv-dr-msg-v1:…)>
        #
        # Dieser Zweig kehrt eigenständig zurück. Die gemeinsame Schlussprüfung
        # unten liest die ersten zwölf Bytes als IV und verwirft einen Null-IV —
        # ein Double-Ratchet-Umschlag trägt aber gar keinen IV mit sich, seine
        # Nonce leitet sich aus dem Einmal-Nachrichtenschlüssel ab. Angewandt
        # würde sie zufällig gültige Umschläge verwerfen und nichts absichern.
        teile = payload.split(".")
        if len(teile) != 4:
            raise ValueError(
                "Ungültiges Double-Ratchet-Format: Konto, Absendergerät, Zielgerät und Rumpf erwartet."
            )
        konto_roh, von_geraet, fuer_geraet, rumpf = teile
        if not konto_roh.isdigit() or int(konto_roh) <= 0:
            raise ValueError("Ungültige Absenderkennung im Double-Ratchet-Umschlag.")
        for kennung in (von_geraet, fuer_geraet):
            if not _GERAETEKENNUNG.match(kennung):
                raise ValueError("Ungültige Gerätekennung im Double-Ratchet-Umschlag.")
        try:
            roh = base64.b64decode(rumpf, validate=True)
        except Exception as exc:
            raise ValueError("Ungültige Base64-Kodierung im Double-Ratchet-Rumpf.") from exc
        if not roh.startswith(b"sv-dr-msg-v1:"):
            raise ValueError("Double-Ratchet-Rumpf trägt kein gültiges DIS-Nachrichtenformat.")
        return

    if matched_prefix == "sv-e2ee-group-v1:":
        # <keyId>.<ciphertext>
        #
        # Die Kennung ist der Hash des Gruppenschlüssels und steht im Klartext,
        # weil ein Gerät mehrere Generationen hält und wissen muss, welche
        # gemeint ist. Sie sagt dem Server nichts: sie hängt allein am
        # Schlüssel, den er nie sieht. Der alte Gruppenumschlag trug hier
        # ausschließlich Base64 ohne Punkt und fällt damit durch — genau so
        # gewollt, sein Schlüssel ergab sich aus der Gruppenkennung.
        if "." not in payload:
            raise ValueError("Ungültiges Gruppen-Payload-Format: Schlüsselkennung vor dem Chiffretext fehlt.")
        key_id, gruppen_ct = payload.split(".", 1)
        if not _GRUPPEN_KEY_ID.match(key_id):
            raise ValueError("Ungültige Schlüsselkennung im Gruppen-Umschlag.")
        ct_to_check = gruppen_ct.strip()
    elif matched_prefix == "sv-e2ee-hybrid-v1:":
        if "." not in payload:
            raise ValueError("Ungültiges Hybrid-Payload-Format: Punkt-Trennzeichen zwischen Schlüssel und Chiffretext fehlt.")
        wrapped_part, ct = payload.split(".", 1)
        if not wrapped_part.strip():
            raise ValueError("Schlüsselkomponente im Hybrid-Umschlag fehlt.")
        for wk in wrapped_part.split(":"):
            cleaned_wk = wk.strip()
            if len(cleaned_wk) < 50:
                raise ValueError("Ungültige RSA-Schlüsselkomponente im Hybrid-Umschlag.")
            try:
                base64.b64decode(cleaned_wk, validate=True)
            except Exception as exc:
                raise ValueError("Ungültige Base64-Kodierung der Schlüsselkomponente im Hybrid-Umschlag.") from exc
        ct_to_check = ct.strip()
    else:
        ct_to_check = payload

    if len(ct_to_check) < 38:
        raise ValueError("Chiffretext zu kurz für gültigen IV und AEAD-Tag (mindestens 38 Zeichen / 28 Bytes erforderlich).")

    try:
        raw_bytes = base64.b64decode(ct_to_check, validate=True)
    except Exception as exc:
        raise ValueError("Ungültige Base64-Kodierung im E2EE-Chiffretext.") from exc

    if len(raw_bytes) < 28:
        raise ValueError("Dekodierter Chiffretext zu kurz (mindestens 28 Bytes für 12-Byte-IV und 16-Byte-Tag).")

    iv = raw_bytes[:12]
    if all(b == 0 for b in iv):
        raise ValueError("Sicherheitsverletzung: Schwacher/ungültiger Null-IV (Nonce) im E2EE-Umschlag erkannt.")


class E2eeBlindEnvelopeCreate(BaseModel):
    blind_mailbox_id: str = Field(..., min_length=16, max_length=64)
    ciphertext_envelope: str = Field(..., min_length=10)
    recipient_id: int | None = Field(None, description="Optionale Empfänger-User-ID zur strikten Push-Filterung")
    client_uuid: str | None = Field(None, max_length=64, description="Client-UUID zur Idempotenz und Deduplizierung")
    is_control: bool = Field(False, description="Markiert interne Steuernachrichten (z. B. Lesequittungen, Quittungen)")
    control_type: str | None = Field(None, description="Typ des Steuersignals (read_receipt, delivery_receipt, edit, delete)")


    @field_validator("ciphertext_envelope")
    @classmethod
    def validate_ciphertext(cls, v: str) -> str:
        validate_e2ee_envelope_format(v)
        return v


class E2eeBlindEnvelopeResponse(BaseModel):
    id: int
    blind_mailbox_id: str
    ciphertext_envelope: str
    client_uuid: str | None = None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        iso = dt.isoformat()
        if iso.endswith("+00:00"):
            iso = iso[:-6] + "Z"
        return iso


class E2eeMailboxSyncItem(BaseModel):
    blind_mailbox_id: str
    max_envelope_id: int
    unread_count: int


class E2eeMailboxSyncResponse(BaseModel):
    mailboxes: list[E2eeMailboxSyncItem]


class E2eeTypingSignalCreate(BaseModel):
    blind_mailbox_id: str = Field(..., min_length=16, max_length=64)
    status: str = Field(..., pattern="^(typing|recording|idle)$")
    recipient_id: int | None = None


class DirectChatResponse(BaseModel):
    id: int
    other_user_id: int
    other_username: str
    other_avatar_url: str | None = None
    blind_mailbox_id: str
    is_friend: bool = False
    is_blocked: bool = False
    other_privacy: str = "friends"
    presence: PresenceInfo | None = None
    created_at: datetime
    updated_at: datetime


class CanMessageResponse(BaseModel):
    can_message: bool
    reason: str | None = None
    blind_mailbox_id: str | None = None


class E2eeDeviceUpdate(BaseModel):
    """Ein Gerät veröffentlicht seinen eigenen Schlüssel.

    Kein `user_id` im Rumpf — die Benutzerkennung kommt aus der Sitzung. Ein
    Gerät kann damit ausschließlich seinen eigenen Eintrag schreiben.
    """

    device_id: str = Field(..., min_length=8, max_length=64)
    public_key: str = Field(..., min_length=10, max_length=8192)
    label: str = Field(default="", max_length=64)

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, v: str) -> str:
        wert = v.strip()
        if not all(c.isalnum() or c in "-_" for c in wert):
            # Der Punkt trennt die Felder im Wire-Format `sv-e2ee-dr-v1:`.
            # Eine Kennung, die ihn enthielte, zerlegte den Umschlag.
            raise ValueError("Geraetekennung darf nur Buchstaben, Ziffern, - und _ enthalten.")
        return wert

    @field_validator("public_key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        validate_rsa_public_key_jwk(v)
        return v


class E2eeDeviceItem(BaseModel):
    device_id: str
    public_key: str
    label: str = ""


class SocialProfileResponse(BaseModel):
    user_id: int
    username: str
    avatar_url: str | None = None
    privacy: str
    restricted: bool = False
    is_friend: bool = False
    presence: PresenceInfo | None = None
    stats: UserStatsResponse | None = None
    achievements: list[AchievementResponse] | None = None


class ChatGroupCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    description: str | None = Field(None, max_length=256)
    avatar_url: str | None = None


class ChatGroupMemberResponse(BaseModel):
    user_id: int
    username: str
    avatar_url: str | None = None
    role: str
    permissions: str | None = None
    joined_at: datetime


class ChatGroupMemberUpdate(BaseModel):
    role: str = Field(..., pattern="^(admin|moderator|member)$")
    permissions: str | None = Field(None, max_length=256)


class ChatGroupPermissionsUpdate(BaseModel):
    default_permissions: str = Field(..., min_length=2, max_length=256)


class ChatGroupResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    avatar_url: str | None = None
    invite_code: str
    owner_user_id: int
    member_count: int
    role: str
    default_permissions: str | None = None
    # Vom Backend entschieden, nicht von der Oberflaeche abgeleitet: was hier
    # false ist, beantwortet der Anruf-Endpunkt mit 403.
    can_start_call: bool = False
    can_join_call: bool = False
    can_share_screen: bool = False
    can_mute_others: bool = False
    can_kick_from_call: bool = False
    created_at: datetime
    members: list[ChatGroupMemberResponse] = []
    room_token: str | None = None
    live_call: bool = False


class ChatGroupInvitePublicResponse(BaseModel):
    group_id: int
    name: str
    description: str | None = None
    avatar_url: str | None = None
    member_count: int
    # Fuer die Vorschaukarte im Chat: laeuft gerade ein Gruppenanruf, und wie
    # viele sind drin. Bewusst nur Ja/Nein und eine Zahl.
    live_call: bool = False
    live_participants: int = 0


class GroupCallRoomResponse(BaseModel):
    room_token: str
    group_id: int
    max_peers: int = Field(default=16, ge=2, le=16)


class GroupCallRoomJoinRequest(BaseModel):
    room_token: str = Field(..., min_length=16, max_length=128)


class ChatStoryCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)
    media_url: str | None = None
    background: str = Field("gradient-1", max_length=64)


class ChatStoryResponse(BaseModel):
    id: int
    user_id: int
    username: str
    avatar_url: str | None = None
    content: str
    media_url: str | None = None
    background: str
    created_at: datetime
    expires_at: datetime
    is_self: bool = False
