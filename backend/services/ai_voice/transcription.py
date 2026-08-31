"""STT-Zugang und verbrauchsgenaue Abschrift-Buchung."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from database import SessionLocal
from models import AiProvider, User
from services.openai_compatible_adapter import StreamUsage


@dataclass(slots=True, repr=False)
class Abschrift:
    """Flüchtiges STT-Ergebnis; der Wortlaut gehört nie in ein Log oder Repr."""

    wortlaut: str
    messwerte: StreamUsage


@dataclass(slots=True, repr=False)
class HoerErgebnis:
    """Ergebnis des STT-Wegs ohne Inhalt in einer automatischen Repräsentation."""

    abschrift: Abschrift | None
    grund: Literal["ok", "anbieter", "unverstanden", "kontingent"]


async def hoeren(
    *,
    client,
    user_id: int,
    provider_id: int,
    pcm: bytes,
    resolve_api_key,
) -> HoerErgebnis:
    """Löst Zugang, transkribiert und bucht den STT-Verbrauch eines Turns."""

    from services import ai_stt

    zugang, schluessel = await asyncio.to_thread(
        zugang_holen,
        user_id=user_id,
        provider_id=provider_id,
        resolve_api_key=resolve_api_key,
    )
    if zugang is None:
        return HoerErgebnis(abschrift=None, grund="anbieter")
    messwerte = StreamUsage()
    try:
        wortlaut = await ai_stt.hoeren(
            client, provider=zugang, api_key=schluessel, pcm=pcm, usage=messwerte
        )
    except ai_stt.NichtsVerstanden:
        return HoerErgebnis(abschrift=None, grund="unverstanden")
    abschrift = Abschrift(wortlaut=wortlaut, messwerte=messwerte)
    dauer_sekunden = max(1, round(len(pcm) / 48_000)) if pcm else 1
    gebucht = await asyncio.to_thread(
        abschrift_verbuchen,
        user_id=user_id,
        zugang=zugang,
        messwerte=abschrift.messwerte,
        wortlaut=abschrift.wortlaut,
        dauer_sekunden=dauer_sekunden,
    )
    if not gebucht:
        return HoerErgebnis(abschrift=None, grund="kontingent")
    return HoerErgebnis(abschrift=abschrift, grund="ok")


def zugang_holen(
    *, user_id: int, provider_id: int, resolve_api_key
) -> tuple[AiProvider | None, str | None]:
    """Löst Provider und Schlüssel pro Zug mit kurzlebiger Datenbanksitzung auf."""

    with SessionLocal() as db:
        zugang = db.get(AiProvider, provider_id)
        if zugang is None or not zugang.enabled:
            return None, None
        schluessel = resolve_api_key(db, zugang, user_id)
        db.expunge(zugang)
        return zugang, schluessel


def abschrift_verbuchen(
    *, user_id: int, zugang: AiProvider, messwerte: StreamUsage, wortlaut: str, dauer_sekunden: int = 1
) -> bool:
    """Bucht nach erfolgreicher Transkription; ``False`` bedeutet nur Kontingent."""

    from services import ai_usage_service
    from services.ai_provider_service import estimate_cost_microunits

    geschaetzt = messwerte.total_tokens
    if geschaetzt is None:
        geschaetzt = max(1, len(wortlaut) // 4)
    geschaetzt = min(geschaetzt, ai_usage_service.TOKEN_LIMIT_MAX)
    with SessionLocal() as db:
        benutzer = db.get(User, user_id)
        if benutzer is None:
            return True
        try:
            ereignis = ai_usage_service.reserve_ai_usage(
                db,
                benutzer,
                request_id=uuid4(),
                estimated_tokens=geschaetzt,
                estimated_cost_microunits=estimate_cost_microunits(zugang, geschaetzt),
                dictation_seconds=dauer_sekunden,
                provider_id=zugang.id,
                model=zugang.transcription_model,
            )
            tokens, kosten, herkunft = ai_usage_service.abrechnung(
                messwerte,
                reserved_tokens=ereignis.reserved_tokens,
                estimated_actual_tokens=geschaetzt,
                token_price_micro_usd_per_million=zugang.token_price_micro_usd_per_million,
            )
            ai_usage_service.complete_ai_usage(
                db,
                ereignis,
                actual_tokens=tokens,
                actual_cost_microunits=kosten,
                aufschluesselung=messwerte,
                cost_source=herkunft,
            )
            db.commit()
        except ai_usage_service.AiQuotaExceeded:
            db.rollback()
            return False
        except Exception:
            db.rollback()
            # Die Providerkosten sind bereits entstanden. Ein Buchungsproblem
            # darf die gesprochene Anfrage nicht als Kontingentfehler ausgeben.
            return True
    return True
