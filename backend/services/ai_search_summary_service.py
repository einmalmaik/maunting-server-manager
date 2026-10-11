"""Neutrale, temporäre KI-Suchzusammenfassung für SearXNG im Secure Browser.

Eigenschaften:
- 0 Bytes Speicherung in der Datenbank (kein Chat, keine Nachricht, keine Persistenz).
- Streng isolierter, neutraler One-Shot-Completion-Stream (kein Gedächtnis, keine Ethik-Engine).
- Günstig & schnell: Minimaler Token-Overhead, fast-streaming.
- Quellenangaben mit [1], [2], verknüpft mit den organischen Suchergebnissen.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator
import httpx
from sqlalchemy.orm import Session

from models.ai_provider import AiProvider
from models.user import User
from services import (
    ai_limit_service,
    ai_provider_service,
    ai_reasoning,
    ai_web_search_service,
    permission_service,
)
from services.ai_provider_service import fuer_suchzusammenfassung, resolve_api_key
from services.openai_compatible_adapter import StreamUsage, stream_chat_completion

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Du bist ein neutraler, sachlicher Suchassistent. Beantworte die Anfrage des Nutzers auf Basis der Suchergebnisse objektiv, präzise und direkt.

Regeln:
1. Nutze ausschließlich die gegebenen Suchergebnisse als Informationsquelle. Erfinde keine Fakten.
2. Wenn die Suchergebnisse die Frage nicht beantworten, erkläre dies kurz und sachlich.
3. Belege Aussagen mit Quellennummern in eckigen Klammern, z. B. [1] oder [2].
4. Strukturiere übersichtlich mit Markdown (Absätze oder kurze Aufzählungspunkte).
5. Keine Floskeln, keine Begrüßungen, keine formelhaften Schlusssätze. Beginne direkt mit der Information."""


def suchzusammenfassung_anbieter(
    db: Session,
    user: User,
    bevorzugte_id: int | None = None,
) -> tuple[AiProvider, str] | None:
    """Ermittelt den berechtigten Zugang und das Modell für die Suchzusammenfassung.

    Reihenfolge:
    1. Bevorzugter Zugang (übergebene ID oder `user.ai_provider_id`), falls aktiv
       und `search_summary_enabled` mit gültigem `search_summary_model`.
    2. Erster aktiver Anbieter mit `search_summary_enabled=True` und gesetztem
       `search_summary_model`.
    3. Gibt None zurück, falls nirgends aktiviert (Feature ist deaktiviert).
    """
    for kennung in (bevorzugte_id, getattr(user, "ai_provider_id", None)):
        if kennung:
            gefunden = db.get(AiProvider, kennung)
            if gefunden is not None and fuer_suchzusammenfassung(gefunden):
                model = (gefunden.search_summary_model or "").strip()
                if model:
                    return gefunden, model

    kandidaten = (
        db.query(AiProvider)
        .filter(
            AiProvider.enabled.is_(True),
            AiProvider.search_summary_enabled.is_(True),
            AiProvider.search_summary_model.isnot(None),
        )
        .order_by(AiProvider.id.asc())
        .all()
    )
    for kandidat in kandidaten:
        if fuer_suchzusammenfassung(kandidat):
            if not kandidat.requires_api_key or kandidat.operator_api_key_encrypted:
                model = (kandidat.search_summary_model or "").strip()
                if model:
                    return kandidat, model

    return None


def suchzusammenfassung_konfig(db: Session, user: User) -> dict[str, Any]:
    """Liefert Status, Modell und für die Rolle erlaubte Denkstufen."""
    if not permission_service.has_global_permission(db, user, "ai.chat.use"):
        return {"aktiviert": False, "modell": None, "denkstufen": [], "max_denkstufe": None}

    anbieter_tupel = suchzusammenfassung_anbieter(db, user)
    if anbieter_tupel is None:
        return {"aktiviert": False, "modell": None, "denkstufen": [], "max_denkstufe": None}

    _provider, model = anbieter_tupel
    deckel = ai_limit_service.resolve_effective_limits(db, user).max_reasoning_effort

    if deckel is not None and deckel <= ai_reasoning.MIN_RANG:
        erlaubte_stufen: list[str] = []
        max_stufe = None
    elif deckel is None:
        erlaubte_stufen = list(ai_reasoning.RANGFOLGE)
        max_stufe = "max"
    else:
        erlaubte_stufen = list(ai_reasoning.RANGFOLGE[:deckel])
        max_stufe = ai_reasoning.stufe_fuer_rang(deckel)

    return {
        "aktiviert": True,
        "modell": model,
        "denkstufen": erlaubte_stufen,
        "max_denkstufe": max_stufe,
    }


def baue_suchkontext(treffer: list[dict[str, Any]]) -> str:
    """Formatiert die Suchergebnisse in eine durchnummerierte Referenzliste."""
    if not treffer:
        return "Keine Suchergebnisse verfügbar."

    zeilen: list[str] = ["Suchergebnisse:"]
    for t in treffer:
        idx = t.get("index", 1)
        titel = (t.get("titel") or "").strip()
        url = (t.get("url") or "").strip()
        inhalt = (t.get("inhalt") or "").strip()
        # Kürze den Auszug pro Treffer sinnvoll, um Token-Budget sparsam zu halten
        if len(inhalt) > 600:
            inhalt = inhalt[:597] + "..."
        zeilen.append(f"[{idx}] {titel}\nURL: {url}\nAuszug: {inhalt}\n")

    return "\n".join(zeilen)


def baue_nachrichten(
    q: str,
    treffer: list[dict[str, Any]],
    verlauf: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Erstellt den minimalen Prompt-Verlauf (System + Kontext + Frage/Verlauf)."""
    kontext = baue_suchkontext(treffer)
    erster_nutzer_text = f"{kontext}\n\nNutzeranfrage:\n{q}"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    verlauf_bereinigt = verlauf or []
    if not verlauf_bereinigt:
        messages.append({"role": "user", "content": erster_nutzer_text})
        return messages

    # Bei Dialogverlauf (Nachfragen im ephemeren Reiter-Chat):
    messages.append({"role": "user", "content": erster_nutzer_text})
    # Falls das Gespräch sehr lang wird (> 8 Nachrichten), werden ältere Runden
    # auf ein rollendes Fenster beschränkt, um das Kontextlimit sparsam zu halten
    letzte_runden = verlauf_bereinigt[-8:]
    for eintrag in letzte_runden:
        rolle = eintrag.get("role")
        inhalt = (eintrag.get("content") or "").strip()
        if rolle in ("user", "assistant") and inhalt:
            messages.append({"role": rolle, "content": inhalt})

    return messages


async def stream_suchzusammenfassung(
    db: Session,
    user: User,
    q: str,
    treffer: list[dict[str, Any]],
    verlauf: list[dict[str, Any]] | None = None,
    bevorzugte_id: int | None = None,
    denkstufe: str | None = None,
    neue_suche: bool = True,
) -> AsyncIterator[str]:
    """Streamt die Suchzusammenfassung als SSE (text/event-stream)."""
    # 1. Rechteprüfung
    if not permission_service.has_global_permission(db, user, "ai.chat.use"):
        yield f"data: {json.dumps({'type': 'disabled', 'reason': 'permission'})}\n\n"
        return

    # 2. Anbieter- und Modellauswahl
    anbieter_tupel = suchzusammenfassung_anbieter(db, user, bevorzugte_id)
    if anbieter_tupel is None:
        yield f"data: {json.dumps({'type': 'disabled', 'reason': 'not_configured'})}\n\n"
        return

    provider, model = anbieter_tupel
    api_key = resolve_api_key(db, provider, user.id)
    if provider.requires_api_key and not api_key:
        yield f"data: {json.dumps({'type': 'disabled', 'reason': 'no_key'})}\n\n"
        return

    # 3. Automatische Ergänzungs-Suche bei Folgefragen
    gesamt_treffer = list(treffer)
    if verlauf and neue_suche and q.strip():
        try:
            frische = ai_web_search_service.searxng_treffer(q.strip(), limit=5)
            bekannte_urls = {t.get("url") for t in gesamt_treffer}
            frische_eintraege: list[dict[str, Any]] = []
            for t in frische:
                u = t.get("url")
                if u and u not in bekannte_urls:
                    bekannte_urls.add(u)
                    idx = len(gesamt_treffer) + 1
                    eintrag = {
                        "index": idx,
                        "titel": t.get("title", ""),
                        "url": u,
                        "inhalt": t.get("snippet", ""),
                        "bild": t.get("bild"),
                    }
                    gesamt_treffer.append(eintrag)
                    frische_eintraege.append(eintrag)
            if frische_eintraege:
                yield f"data: {json.dumps({'type': 'neue_treffer', 'treffer': frische_eintraege})}\n\n"
        except Exception as exc:
            logger.debug("Automatische Ergänzungssuche für Folgefrage fehlgeschlagen: %s", exc)

    # 4. Denkstufe auflösen und prüfen
    aktiv_denken = bool(denkstufe and denkstufe.lower() not in ("fast", "schnell", "aus", "none"))
    wunsch_stufe = denkstufe.lower().strip() if aktiv_denken and denkstufe else None

    konfig_daten = suchzusammenfassung_konfig(db, user)
    erlaubte_stufen = konfig_daten.get("denkstufen", [])
    max_stufe = konfig_daten.get("max_denkstufe")

    # 5. Nachrichten zusammenstellen
    messages = baue_nachrichten(q, gesamt_treffer, verlauf)

    # 6. Streamen
    usage = StreamUsage()
    yield f"data: {json.dumps({'type': 'start', 'model': model, 'provider': provider.name, 'denkstufen': erlaubte_stufen, 'max_denkstufe': max_stufe})}\n\n"

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            denken, stufe = await ai_reasoning.vorgabe(
                client,
                db,
                user=user,
                provider=provider,
                aktiv=aktiv_denken,
                wunsch=wunsch_stufe,
            )

            async for chunk in stream_chat_completion(
                client,
                provider=provider,
                api_key=api_key,
                messages=messages,
                usage=usage,
                model=model,
                reasoning=denken,
                reasoning_effort=stufe,
            ):
                if chunk.kind == "thought" and chunk.text:
                    yield f"data: {json.dumps({'type': 'thought', 'text': chunk.text})}\n\n"
                elif chunk.kind == "content" and chunk.text:
                    yield f"data: {json.dumps({'type': 'content', 'text': chunk.text})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'model': model})}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
