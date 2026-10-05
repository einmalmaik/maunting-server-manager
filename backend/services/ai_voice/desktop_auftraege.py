"""Computer-Use per Stimme: Aufträge an den Rechner aus einer Sprachsitzung.

Im Chat parkt der Lauf, bis der Rechner geantwortet hat (`_desktop_behandeln`),
und das Ergebnis kommt beim Wecken als Meldung des Panels. Eine Sprachsitzung
kann nicht parken: jedes Werkzeug muss in `REALTIME_TOOL_TIMEOUT_SECONDS`
antworten, und ein Mensch, der eine Karte liest, braucht länger. Bis zum
05.10.2026 bot die Stimme die Desktop-Werkzeuge deshalb gar nicht an — und
wurden sie doch gerufen, endete jeder Aufruf im benannten Fehlschlag von
`server_tools`.

Der Weg hier teilt den Aufruf in zwei Hälften:

1. **Übergeben.** Das Werkzeug legt den Auftrag an und antwortet sofort: „an
   den Rechner übergeben, das Ergebnis kommt als Meldung“. Das Modell spricht
   weiter oder wartet, aber es meldet nichts als erledigt.
2. **Nachreichen.** Ein Nebenlauf der Sitzung fragt die Datenbank nach
   fertigen Aufträgen — sie ist die Wahrheit, denn die Meldung der App landet
   womöglich auf einem anderen Arbeitsprozess — und stellt das Ergebnis in
   einer Gesprächspause zu, mit Bildschirmfoto, wenn das Modell Bilder liest.

Rechte, Autonomie und Gerät entscheidet dasselbe wie im Chat:
`_desktop_argumente` (``autonom`` urteilt das Panel, nie das Modell),
`angebotene_werkzeuge` und die eingefrorene Familie der Sitzung.

**Der Lauf.** Ein Auftrag braucht eine `run_id` (NOT NULL, Fremdschlüssel).
Je Sitzung entsteht beim ersten Auftrag ein Lauf im Gespräch der Sitzung, gleich
als ``completed`` mit ``stop_reason="voice_session"``: so weckt ihn
`lauf_fortsetzen` nie (`darf_fortsetzen`), der Neustart-Abgleich fasst ihn
nicht an, und `_alte_loeschen` räumt die Aufträge nach einem Tag weg.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from uuid import uuid4

from database import SessionLocal
from models import AiRun, User
from services.ai_tool_registry import DESKTOP_TOOLS

logger = logging.getLogger(__name__)

#: Wie viele Aufträge einer Sitzung gleichzeitig offen sein dürfen. Ein Modell
#: in einer Schleife soll nicht zwanzig Karten auf den Bildschirm legen.
MAX_OFFENE = 3

STOP_GRUND = "voice_session"

#: Wie lange die Frage an den Modellkatalog dauern darf.
KATALOGFRIST_SEKUNDEN = 3.0

UEBERGABE_HINWEIS = (
    "An den Rechner des Benutzers übergeben. Das Ergebnis kommt gleich als "
    "Meldung des Panels — sag bis dahin nicht, es sei erledigt, und ruf das "
    "Werkzeug nicht erneut auf. Ohne autonomen Modus bestätigt der Benutzer "
    "den Schritt auf einer Karte am Rechner."
)


def ist_desktop(name: str) -> bool:
    return name in DESKTOP_TOOLS


def _blick(call) -> bool:
    return (
        call.name == "desktop_system"
        and (call.arguments or {}).get("aktion") == "bildschirm"
    )


class SprachAuftraege:
    """Die offenen Rechner-Aufträge einer Sprachsitzung.

    `anlegen` und `fertige_abholen` laufen in Threads (`asyncio.to_thread`);
    das Schloss hält die Liste der offenen Kennungen zwischen beiden heil.
    """

    def __init__(
        self,
        *,
        user_id: int,
        conversation_id: str,
        herkunft: str,
        familie: str | None,
    ) -> None:
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.herkunft = herkunft
        self.familie = familie
        self._run_id: str | None = None
        self._offen: list[str] = []
        self._schloss = threading.Lock()

    @property
    def offen(self) -> bool:
        return bool(self._offen)

    def _lauf(self, db) -> str:
        if self._run_id is None:
            run = AiRun(
                id=str(uuid4()),
                conversation_id=self.conversation_id,
                user_id=self.user_id,
                status="completed",
                stop_reason=STOP_GRUND,
            )
            db.add(run)
            db.flush()
            self._run_id = run.id
        return self._run_id

    def anlegen(self, call, *, sieht: bool | None) -> tuple[dict, str | None, str | None]:
        """Legt den Auftrag an. Rückgabe ``(wert, fehler, auftrag_id)``.

        ``wert`` ist die sofortige Antwort an das Modell: die Übergabe oder
        ein benannter Fehler. Nie ein „erledigt“.
        """
        from services import desktop_job_service
        from services.ai_action_service import angebotene_werkzeuge
        from services.ai_stream.interactions import _desktop_argumente
        from services.ai_stream.types import KEIN_BLICK_GRUND

        if self.herkunft != "desktop":
            # Der Katalog bietet sie aus dem Panel gar nicht an
            # (`herkunft_schnitt`); das hier ist die zweite Schranke.
            fehler = "Der Rechner ist nur aus der Desktop-App erreichbar."
            return {"error": fehler}, fehler, None
        if sieht is False and _blick(call):
            # Wie im Chat (`_sieht_nicht`): ein Foto, das niemand lesen kann,
            # ist eine Aufnahme samt Indikator für nichts.
            return {"error": KEIN_BLICK_GRUND}, KEIN_BLICK_GRUND, None
        with self._schloss:
            if len(self._offen) >= MAX_OFFENE:
                fehler = (
                    f"Schon {MAX_OFFENE} Aufträge warten auf den Rechner. Warte "
                    "auf ihre Ergebnisse, bevor du weitere gibst."
                )
                return {"error": fehler}, fehler, None
            with SessionLocal() as db:
                user = db.get(User, self.user_id)
                if user is None or not user.is_active:
                    fehler = "AI-Zugriff wurde entzogen"
                    return {"error": fehler}, fehler, None
                if call.name not in angebotene_werkzeuge(db, user):
                    fehler = "Dieses Werkzeug steht in dieser Sitzung nicht zur Verfügung"
                    return {"error": fehler}, fehler, None
                job = desktop_job_service.anlegen(
                    db,
                    user_id=self.user_id,
                    run_id=self._lauf(db),
                    tool_call_id=call.id,
                    tool_name=call.name,
                    arguments=_desktop_argumente(
                        db, user_id=self.user_id, call=call, sieht=sieht
                    ),
                    familie=self.familie,
                )
                db.commit()
                auftrag_id = job.id
            self._offen.append(auftrag_id)
        logger.info(
            "Sprachsitzung uebergibt an den Rechner werkzeug=%s auftrag=%s",
            call.name, auftrag_id,
        )
        return {"uebergeben": True, "hinweis": UEBERGABE_HINWEIS}, None, auftrag_id

    def fertige_abholen(self) -> list[tuple[str, dict]]:
        """Die Aufträge, die inzwischen abgeschlossen sind — samt Ergebnis.

        Wer abgeholt ist, gilt nicht mehr als offen. Verfallene stehen als
        Verfall darin (`desktop_job_service.abgeschlossene`).
        """
        from services import desktop_job_service

        with self._schloss:
            kennungen = list(self._offen)
        if not kennungen:
            return []
        with SessionLocal() as db:
            fertig = desktop_job_service.abgeschlossene(
                db, user_id=self.user_id, job_ids=kennungen
            )
        if fertig:
            erledigt = {auftrag_id for auftrag_id, _ in fertig}
            with self._schloss:
                self._offen = [k for k in self._offen if k not in erledigt]
        return fertig


def meldung(ergebnisse: list[dict], *, sieht: bool | None) -> tuple[str, list[str]]:
    """Text und Bilder (base64-JPEG) der Meldung an das Modell.

    Derselbe Wortlaut wie im Chat (`_desktopmeldung`), damit das Modell beide
    Wege gleich liest. Liest es keine Bilder, fällt das Foto heraus — die
    Notiz im Text sagt dann, dass es eins gab.
    """
    from services.ai_stream.interactions import _desktopmeldung

    nachricht = _desktopmeldung(ergebnisse)
    inhalt = nachricht["content"]
    if isinstance(inhalt, str):
        return inhalt, []
    text = ""
    bilder: list[str] = []
    for teil in inhalt:
        if teil.get("type") == "text":
            text = str(teil.get("text") or "")
        elif teil.get("type") == "image_url":
            url = str((teil.get("image_url") or {}).get("url") or "")
            _, _, b64 = url.partition("base64,")
            if b64:
                bilder.append(b64)
    if sieht is False:
        return text, []
    return text, bilder


def openai_inhalt(text: str, bilder: list[str]) -> list[dict]:
    """Inhalt einer Nachricht für Realtime und das GPT-Live-Backend."""
    return [
        {"type": "input_text", "text": text},
        *(
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{bild}"}
            for bild in bilder
        ),
    ]


def gemini_teile(text: str, bilder: list[str]) -> list[dict]:
    """Teile eines Zugs für Gemini Live (``clientContent``)."""
    return [
        {"text": text},
        *(
            {"inlineData": {"mimeType": "image/jpeg", "data": bild}}
            for bild in bilder
        ),
    ]


async def modell_sieht(http, provider_kind: str, modellname: str, schluessel: str | None) -> bool | None:
    """Liest dieses Modell Bilder? ``None`` heißt „unbekannt“ — auch, wenn der
    Katalog nicht rechtzeitig antwortet. Nur ein ausdrückliches ``False`` hält
    ein Bildschirmfoto zurück (wie `_sieht_nicht` im Chat)."""
    from services import ai_model_catalog

    try:
        modell = await asyncio.wait_for(
            ai_model_catalog.finde(http, provider_kind, modellname, schluessel=schluessel),
            timeout=KATALOGFRIST_SEKUNDEN,
        )
    except Exception as exc:  # noqa: BLE001 — ein stummer Katalog heißt „unbekannt“
        logger.info("Modellkatalog stumm (%s) — Bilder gehen mit", type(exc).__name__)
        return None
    return modell.sieht if modell is not None else None


async def uebergeben(
    auftraege: SprachAuftraege,
    call,
    *,
    sieht: bool | None,
    frist: float,
) -> tuple[dict, str | None, dict, str | None]:
    """Das Werkzeug einer Sprachsitzung für den Rechner — sofort beantwortet.

    Rückgabe ``(wert, fehler, anzeige, auftrag_id)``; ``auftrag_id`` ist
    ``None``, wenn kein Auftrag entstand.
    """
    try:
        wert, fehler, auftrag_id = await asyncio.wait_for(
            asyncio.to_thread(auftraege.anlegen, call, sieht=sieht),
            timeout=frist,
        )
    except Exception as exc:  # noqa: BLE001 — benannt statt still
        logger.warning("Rechner-Auftrag aus der Sprache gescheitert: %s", type(exc).__name__)
        fehler = "Der Auftrag an den Rechner konnte nicht angelegt werden"
        wert, auftrag_id = {"error": fehler}, None
    anzeige = {"tool_name": call.name, **({"failed": True} if fehler else {})}
    return wert, fehler, anzeige, auftrag_id
