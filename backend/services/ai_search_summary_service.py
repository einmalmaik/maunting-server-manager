"""Neutrale, temporäre KI-Suchzusammenfassung für SearXNG im Secure Browser.

Eigenschaften:
- 0 Bytes Speicherung in der Datenbank (kein Chat, keine Nachricht, keine Persistenz).
- Streng isolierter, neutraler Completion-Stream (kein Gedächtnis, keine Ethik-Engine).
- Günstig & schnell: Minimaler Token-Overhead, fast-streaming.
- Quellenangaben mit [1], [2], verknüpft mit den organischen Suchergebnissen.
- Folgefragen recherchieren selbständig weiter: Die Nachfrage wird mit Blick auf
  den Verlauf in eigenständige Suchanfragen umgeschrieben (Koreferenzen wie
  „das" werden aufgelöst), danach laufen höchstens zwei gezielte Nachsuchen.
  Bei einem Themenwechsel ersetzen die frischen Treffer den alten Kontext, bei
  einer Vertiefung ergänzen sie ihn. Reicht kein Treffer, antwortet die KI aus
  eigenem Wissen — unmissverständlich als unbelegt gekennzeichnet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator
import urllib.parse
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


SYSTEM_PROMPT = """Du bist ein neutraler, sachlicher Suchassistent für Maunting Search. Beantworte die Anfrage des Nutzers auf Basis der Suchergebnisse und der darin enthaltenen Volltext-Auszüge objektiv, präzise, vollständig und direkt.

Die Suchergebnisse sind in Abschnitte gegliedert („Treffer zur Ausgangssuche",
„Frische Treffer zur Nachfrage“). Beziehe dich bei einer Nachfrage vorrangig
auf die frischen Treffer.

Regeln:
1. Nutze vorrangig die gegebenen Suchergebnisse und deren Volltext-Auszüge als Informationsquelle. Lies die Artikel, Tabellen und Spezifikationen gründlich und extrahiere konkrete Fakten (z. B. Veröffentlichungszeiträume, Versionsstände, Benchmark-Werte, FPS, Testergebnisse, Zitate, technische Details), um direkte Fragen sofort auf den Punkt zu beantworten. Erfinde keine Fakten.
2. Enthält die Nutzeranfrage mehrere Teilfragen oder verschiedene Themen zugleich (z. B. „Was ist Eisen und wofür braucht man das und wie koche ich Gulasch?“), beantworte **jede einzelne Teilfrage** vollständig in einem klar gegliederten Abschnitt mit eigener Zwischenüberschrift.
3. Bei Rezept- oder Kochanfragen („Wie koche ich ...?“) liefere direkt eine strukturierte Übersicht mit **Zutaten** und **Zubereitungsschritten**. Bei naturwissenschaftlichen, technischen oder lexikalischen Fragen („Was ist ...?“) liefere eine klare Definition, Kerneigenschaften und Verwendungszwecke.
4. Reichen die Suchergebnisse für die Frage oder eine Teilfrage nicht aus, darfst du aus eigenem Wissen antworten. Kennzeichne unbelegte Teile unmissverständlich mit dem Hinweis „Ohne Quellenbeleg:“ und setze dort keine [n]-Quellenangaben.
5. Belege Aussagen aus den Suchergebnissen mit Quellennummern in eckigen Klammern, z. B. [1] oder [2].
6. Strukturiere übersichtlich mit Markdown (Fettdruck, kurze Absätze, Aufzählungspunkte). Keine Floskeln, keine Begrüßungen, keine formelhaften Schlusssätze.
7. Wenn in den Quellen Markdown-Tabellen, Spezifikationen, Benchmark-Scores (z. B. FPS, Punkte, Latenzen) oder strukturierte Vergleiche vorliegen, werte die exakten Zahlenwerte direkt aus und stelle sie in deiner Antwort übersichtlich gegenüber (z. B. als eigene Vergleichstabelle oder strukturierte Gegenüberstellung)."""


UMSCHREIBEN_SYSTEM = """Du formulierst Nachfragen oder zusammengesetzte Fragen für eine Metasuche um. Lese den bisherigen Dialog und die aktuelle Anfrage und liefere eigenständige Suchanfragen, die ohne den Dialog verständlich sind. Löse dabei Bezüge auf („das“, „dort“, „die Zutaten“ → der gemeinte Gegenstand) und teile Mehrfachfragen („Was ist X und wie koche ich Y?“) in getrennte, präzise Suchanfragen für jedes Teilthema auf.

Antworte ausschließlich mit JSON in genau dieser Form: {"suchanfragen": ["...", "..."], "themenwechsel": true/false}.
- Höchstens 3 kurze Suchanfragen, die alle Teilfragen der Anfrage abdecken.
- "themenwechsel": true, wenn die Nachfrage ein neues Thema beginnt und die bisherigen Treffer dafür nichts mehr taugen; false, wenn sie das bisherige Thema vertieft oder ergänzt."""

#: Mehr Nachsuchen löst eine Folgefrage nie aus (Kosten- und Latenzgrenze).
NACHSUCHE_MAX_RUNDEN = 2
#: Höchstens so viele Suchanfragen liefert das Umschreiben.
UMSCHREIBEN_MAX_ANFRAGEN = 3
#: So viele frische Trefferseiten werden höchstens geöffnet, wenn Snippets dünn sind.
SEITEN_MAX = 2

_MEHRFACH_FRAGE_MUSTER = re.compile(
    r"\b(?:und|sowie|außerdem|ausserdem)\s+(?:wie|was|wofür|wofuer|warum|wann|wer|wo|welche|welcher|welches)\b|\?.+\?",
    re.IGNORECASE,
)


def ist_zusammengesetzte_anfrage(frage: str) -> bool:
    """Erkennt, ob schon die erste Suchanfrage aus mehreren Teilfragen besteht
    (z. B. „Was ist Eisen und wofür braucht man das und wie koche ich Gulasch?“)."""
    text = (frage or "").strip()
    if len(text.split()) < 6:
        return False
    return bool(_MEHRFACH_FRAGE_MUSTER.search(text))


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
        volltext = (t.get("volltext") or "").strip()
        # Kürze den Auszug pro Treffer sinnvoll, um Token-Budget sparsam zu halten
        if len(inhalt) > 600:
            inhalt = inhalt[:597] + "..."
        if volltext:
            max_volltext = 15000 if t.get("deep") else 2500
            if len(volltext) > max_volltext:
                volltext = volltext[:max_volltext - 3] + "..."
            label = "Volltext-Auszug (Deep-Dive)" if t.get("deep") else "Volltext-Auszug"
            zeilen.append(f"[{idx}] {titel}\nURL: {url}\nAuszug: {inhalt}\n{label}: {volltext}\n")
        else:
            zeilen.append(f"[{idx}] {titel}\nURL: {url}\nAuszug: {inhalt}\n")

    return "\n".join(zeilen)


def erkenne_deep_dive_quellen(
    q: str,
    treffer: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Erkennt, ob eine Nutzeranfrage tiefe Details aus bestimmten oder allen Quellen verlangt."""
    text = (q or "").lower()
    if not text or not treffer:
        return []

    ziel_indices: set[int] = set()
    for m in re.findall(r"\[(\d+)\]", text):
        try:
            ziel_indices.add(int(m))
        except ValueError:
            pass

    for m in re.findall(r"\bquelle\s+(\d+)\b", text):
        try:
            ziel_indices.add(int(m))
        except ValueError:
            pass

    ordnung_map = {
        "erste": 1, "ersten": 1, "1.": 1,
        "zweite": 2, "zweiten": 2, "2.": 2,
        "dritte": 3, "dritten": 3, "3.": 3,
        "vierte": 4, "vierten": 4, "4.": 4,
    }
    for wort, idx in ordnung_map.items():
        if f"{wort} quelle" in text:
            ziel_indices.add(idx)

    for t in treffer:
        url = t.get("url", "")
        if url:
            try:
                domain = urllib.parse.urlparse(url).netloc.lower()
                domain_core = domain.replace("www.", "").split(".")[0]
                if len(domain_core) >= 4 and domain_core in text:
                    ziel_indices.add(t.get("index", 1))
            except Exception:
                pass

    if ziel_indices:
        return [t for t in treffer if t.get("index") in ziel_indices]

    detail_begriffe = (
        "genauer", "detail", "details", "benchmark", "benchmarks",
        "tabelle", "tabellen", "spezifikation", "spezifikationen",
        "deep-dive", "deep dive", "volltext", "ausführlich",
        "fps", "score", "punkte", "metriken", "vergleichstabelle",
    )
    if any(b in text for b in detail_begriffe):
        return treffer[:2]

    return []


def _deute_umschreibung(text: str, rueckfall: str) -> dict[str, Any]:
    """Liest das JSON des Umschreibe-Schritts; bei jeder Abweichung gilt der
    Rückfall: eine wörtliche Nachsuche ohne Themenwechsel (bisheriges Verhalten)."""
    rueckfall_wert: dict[str, Any] = {"suchanfragen": [rueckfall], "themenwechsel": False}
    bereinigt = (text or "").strip()
    if bereinigt.startswith("```"):
        bereinigt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", bereinigt).strip()
    try:
        daten = json.loads(bereinigt)
    except (json.JSONDecodeError, ValueError):
        return rueckfall_wert
    if not isinstance(daten, dict):
        return rueckfall_wert
    roh = daten.get("suchanfragen")
    if not isinstance(roh, list):
        return rueckfall_wert
    anfragen = [str(a).strip()[:200] for a in roh if str(a).strip()][:UMSCHREIBEN_MAX_ANFRAGEN]
    if not anfragen:
        return rueckfall_wert
    return {"suchanfragen": anfragen, "themenwechsel": bool(daten.get("themenwechsel", False))}


async def suchanfragen_umschreiben(
    client: httpx.AsyncClient,
    *,
    provider: AiProvider,
    api_key: str | None,
    model: str,
    frage: str,
    verlauf: list[dict[str, Any]],
) -> dict[str, Any]:
    """Formuliert eine Nachfrage mit Blick auf den Verlauf in eigenständige
    Suchanfragen um. Scheitert der Schritt, gilt der Rückfall aus
    `_deute_umschreibung` — nie ein Abbruch der Zusammenfassung."""
    auszug: list[str] = []
    for eintrag in (verlauf or [])[-6:]:
        text = (eintrag.get("content") or "").strip()
        if eintrag.get("role") in ("user", "assistant") and text:
            auszug.append(f"{eintrag['role']}: {text[:1000]}")
    messages = [
        {"role": "system", "content": UMSCHREIBEN_SYSTEM},
        {
            "role": "user",
            "content": "Bisheriger Dialog:\n" + ("\n".join(auszug) or "(leer)") + f"\n\nAktuelle Nachfrage:\n{frage}",
        },
    ]
    try:
        teile: list[str] = []
        verwendung = StreamUsage()
        async for chunk in stream_chat_completion(
            client,
            provider=provider,
            api_key=api_key,
            messages=messages,
            usage=verwendung,
            model=model,
        ):
            if chunk.kind == "content" and chunk.text:
                teile.append(chunk.text)
        return _deute_umschreibung("".join(teile), frage)
    except Exception as exc:
        logger.debug("Umschreiben der Nachfrage fehlgeschlagen: %s", exc)
        return {"suchanfragen": [frage], "themenwechsel": False}


def normalisiere_dialog_verlauf(
    messages: list[dict[str, str]],
    aktuelle_frage: str | None = None,
) -> list[dict[str, str]]:
    """Stellt sicher, dass nach der System-Prompt strikt alterniert wird:
    system -> user -> assistant -> user -> assistant.
    Aufeinanderfolgende identische Rollen werden zusammengeführt."""
    if not messages:
        return []

    system_msgs: list[dict[str, str]] = []
    dialog_msgs: list[dict[str, str]] = []

    for m in messages:
        rolle = m.get("role")
        inhalt = (m.get("content") or "").strip()
        if not inhalt:
            continue
        if rolle == "system":
            system_msgs.append({"role": "system", "content": inhalt})
        elif rolle in ("user", "assistant"):
            if dialog_msgs and dialog_msgs[-1]["role"] == rolle:
                vorheriger_inhalt = dialog_msgs[-1]["content"]
                if inhalt not in vorheriger_inhalt:
                    dialog_msgs[-1]["content"] = f"{vorheriger_inhalt}\n\n{inhalt}"
            else:
                dialog_msgs.append({"role": rolle, "content": inhalt})

    # Wenn der Dialog mit assistant endet und eine aktuelle Frage vorliegt,
    # muss die Frage als finaler Nutzer-Turn angehängt werden, damit der LLM-Chat-Contract
    # gewahrt bleibt.
    if dialog_msgs and dialog_msgs[-1]["role"] == "assistant" and aktuelle_frage:
        frage_text = aktuelle_frage.strip()
        if frage_text:
            dialog_msgs.append({"role": "user", "content": frage_text})

    return system_msgs + dialog_msgs


def baue_nachrichten(
    q: str,
    treffer: list[dict[str, Any]],
    verlauf: list[dict[str, Any]] | None = None,
    frische_ab: int | None = None,
) -> list[dict[str, str]]:
    """Erstellt den minimalen Prompt-Verlauf (System + Kontext + Frage/Verlauf).

    `frische_ab` trennt alte Treffer von frischen Nachsuchen in zwei beschriftete
    Abschnitte, damit das Modell bei einer Nachfrage weiß, was gerade gesucht
    wurde. Ohne Angabe bleibt es bei einer einzigen Liste (bisheriges Verhalten).
    """
    if frische_ab is None or frische_ab <= 0 or frische_ab >= len(treffer):
        kontext = baue_suchkontext(treffer)
    else:
        kontext = (
            "Treffer zur Ausgangssuche:\n"
            + baue_suchkontext(treffer[:frische_ab])
            + "\n\nFrische Treffer zur Nachfrage:\n"
            + baue_suchkontext(treffer[frische_ab:])
        )
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

    return normalisiere_dialog_verlauf(messages, q)


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

    # 3. Selbständige Nachrecherche bei Folgefragen: Umschreiben (Koreferenzen
    #    auflösen), höchstens zwei gezielte Nachsuchen, danach bei Bedarf die
    #    besten frischen Seiten öffnen. Synchrone Arbeit läuft nie auf der
    #    Ereignisschleife (`asyncio.to_thread`). Die Kennziffern laufen immer
    #    weiter, damit [n]-Verweise in der Kachel auf die Treffer darunter zeigen.
    #    Ein Client für Nachrecherche und Antwortstrom gemeinsam.
    gesamt_treffer = list(treffer)
    frische_ab: int | None = None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            braucht_nachrecherche = bool(
                neue_suche
                and q.strip()
                and (verlauf or ist_zusammengesetzte_anfrage(q))
            )
            if braucht_nachrecherche:
                umschrieben = await suchanfragen_umschreiben(
                    client,
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    frage=q.strip(),
                    verlauf=verlauf or [],
                )
                if verlauf and umschrieben["themenwechsel"]:
                    gesamt_treffer = []
                frische_ab = len(gesamt_treffer)
                bekannte_urls = {t.get("url") for t in gesamt_treffer}
                frische_eintraege: list[dict[str, Any]] = []
                for anfrage in umschrieben["suchanfragen"][:NACHSUCHE_MAX_RUNDEN]:
                    yield f"data: {json.dumps({'type': 'recherche', 'anfrage': anfrage})}\n\n"
                    frische = await asyncio.to_thread(ai_web_search_service.searxng_treffer, anfrage, limit=5)
                    for t in frische:
                        u = t.get("url")
                        if u and u not in bekannte_urls:
                            bekannte_urls.add(u)
                            eintrag = {
                                "index": len(gesamt_treffer) + 1,
                                "titel": t.get("title", ""),
                                "url": u,
                                "inhalt": t.get("snippet", ""),
                                "bild": t.get("bild"),
                            }
                            gesamt_treffer.append(eintrag)
                            frische_eintraege.append(eintrag)
                for eintrag in frische_eintraege[:SEITEN_MAX]:
                    seite = await asyncio.to_thread(ai_web_search_service.scrape_url, eintrag.get("url", ""))
                    inhalt = (seite.get("content") or "").strip()
                    if inhalt:
                        eintrag["volltext"] = inhalt[:2500]
                if frische_eintraege:
                    yield f"data: {json.dumps({'type': 'neue_treffer', 'treffer': frische_eintraege})}\n\n"

            # Top-Webseiten im Volltext einlesen (bis zu 8 Seiten parallel im Thread-Pool)
            seiten_kandidaten = [
                t for t in gesamt_treffer[:8]
                if t.get("url") and not t.get("volltext")
            ]
            if seiten_kandidaten:
                urls_zum_scrapen = [t["url"] for t in seiten_kandidaten]
                scraped_texte = await asyncio.to_thread(
                    ai_web_search_service.scrape_top_urls_parallel,
                    urls_zum_scrapen,
                    2500,
                    len(urls_zum_scrapen),
                )
                for t in seiten_kandidaten:
                    u = t.get("url")
                    if u in scraped_texte:
                        t["volltext"] = scraped_texte[u]

            # Deep-Dive Erkennung bei Folgefragen oder Detailanfragen:
            # Falls gezielt Quellen referenziert werden (z. B. [1], Domain oder Detailfragen),
            # bis zu 15.000 Zeichen Volltext nachladen.
            if verlauf:
                deep_kandidaten = erkenne_deep_dive_quellen(q, gesamt_treffer)
                for ziel in deep_kandidaten:
                    u = ziel.get("url")
                    if u and not ziel.get("deep"):
                        titel_kurz = ziel.get("titel") or u
                        yield f"data: {json.dumps({'type': 'recherche', 'anfrage': f'Deep Dive: {titel_kurz}'})}\n\n"
                        deep_res = await asyncio.to_thread(
                            ai_web_search_service.scrape_url,
                            u,
                            15000,
                            deep=True,
                        )
                        if isinstance(deep_res, dict) and "error" not in deep_res:
                            inhalt = (deep_res.get("content") or "").strip()
                            if inhalt:
                                ziel["volltext"] = inhalt
                                ziel["deep"] = True

            # 4. Denkstufe auflösen und prüfen
            aktiv_denken = bool(denkstufe and denkstufe.lower() not in ("fast", "schnell", "aus", "none"))
            wunsch_stufe = denkstufe.lower().strip() if aktiv_denken and denkstufe else None

            konfig_daten = suchzusammenfassung_konfig(db, user)
            erlaubte_stufen = konfig_daten.get("denkstufen", [])
            max_stufe = konfig_daten.get("max_denkstufe")

            # 5. Nachrichten zusammenstellen
            messages = baue_nachrichten(q, gesamt_treffer, verlauf, frische_ab)

            # 6. Streamen
            usage = StreamUsage()
            yield f"data: {json.dumps({'type': 'start', 'model': model, 'provider': provider.name, 'denkstufen': erlaubte_stufen, 'max_denkstufe': max_stufe})}\n\n"

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
