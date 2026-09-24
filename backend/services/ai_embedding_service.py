"""Lokale Satz-Vektoren fuer die Gedaechtnissuche.

Warum ueberhaupt lokal: Der KI-Provider koennte Embeddings liefern, aber
gemessen hat OpenRouter kein kostenloses Embedding-Modell (402 bei
`text-embedding-3-small`, 400 bei `openrouter/free`). Ein Gedaechtnis, dessen
Suche einen bezahlten Account voraussetzt, waere fuer einen Teil der Betreiber
schlicht tot. Deshalb rechnet MSM selbst — offline, ohne Tokenkosten, ohne dass
Erinnerungen fuer die *Suche* das Haus verlassen.

Warum `model2vec` und nicht mehr: Es sind **statische** Embeddings, also eine
vorberechnete Tabelle plus Mittelung — kein neuronales Netz zur Laufzeit. Damit
kommt es ohne torch und ohne ONNX-Runtime aus. Gemessen: 23 Pakete statt 35 bei
mem0ai, kein Telemetriepaket, keine Kollision mit unserem gepinnten
`httpx==0.27.0`, kein zweites Provider-SDK neben dem eigenen Adapter.

**Was diese Suche nicht ist.** Statische Embeddings kennen keinen Satzkontext.
Gemessen an Wortpaaren: `Zeitzone`/`timezone` 0,62, aber `Sicherung`/`backup`
nur 0,27 — die Sprachbruecke traegt ungleichmaessig. Unverwandtes trennt das
Modell zuverlaessig (nahe 0,0), aber es ersetzt kein Sprachmodell. Deshalb ist
die Aehnlichkeit in `ai_memory_service` nur *ein* Signal neben Wortabgleich,
Nutzung und Aktualitaet.

**Fehlt das Modell, faellt nichts aus.** Ein abgebrochener Download oder ein
unvollstaendiges Update darf das Panel nicht lahmlegen: dann liefert
``encode`` schlicht ``None`` und der Abruf nutzt die Kriterien ohne Vektoren.
Dieser Zustand ist aber weder endgültig noch unsichtbar: ``_load`` versucht es
nach ``NEUVERSUCH_NACH_SEKUNDEN`` erneut, und ``is_ready`` sagt jederzeit, ob
gerade gerechnet werden kann. Vorher war beides nicht so — ein einziger
schlechter Moment beim Laden schaltete die Bedeutungssuche bis zum
Prozess-Neustart ab, und der Betreiber erfuhr davon nur als eine Warnzeile im
Log.

**Der Rückfall ist eine Entscheidung des Betreibers.** Fehlt das lokale Modell,
darf `encode` bei Google AI Studio oder OpenAI rechnen lassen — aber nur bei
dem Anbieter, den der Betreiber unter `RUECKFALL_SCHLUESSEL` gewählt hat.
Standard ist keiner. Der Rückfall schickt Gedächtnistexte, Skillbeschreibungen
und jede Chatfrage im Klartext an diesen Anbieter, auch wenn im Chat ein ganz
anderer gewählt ist; bis zum 24.09.2026 geschah das bei Google ohne jede Frage,
sobald irgendein Google-Zugang aktiv war. Ist kein Rückfall gewählt, gilt der
Satz oben wieder wörtlich: für die Suche verlässt nichts das Haus.

Das lokale Modell bleibt immer der erste Weg. Ein Rückfallanbieter ersetzt es
nur, solange es fehlt — sonst verließe jede Suche das Haus, nur weil ein
Schlüssel eingetragen ist.
"""

from __future__ import annotations

import logging
import threading
import time
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

# Ausgabegroesse von `potion-multilingual-128M`. Fest verdrahtet, damit ein
# gespeicherter Vektor erkennbar nicht mehr zum geladenen Modell passt.
EMBEDDING_DIMENSIONS = 256

# Wieviele Bytes ein gespeicherter Vektor belegt: 256 Zahlen zu je vier. Auch
# diese Zahl ist fest, und deshalb ist eine abweichende Länge kein kurzer
# Vektor, sondern ein beschädigter — siehe `bytes_zu_vektor`.
EMBEDDING_BYTES = EMBEDDING_DIMENSIONS * 4

# Kennung des Modells, mit dem ein gespeicherter Vektor entstanden ist. Wechselt
# der Betreiber das Modell, passen alte Vektoren nicht mehr — sie werden dann
# ignoriert statt falsche Ähnlichkeiten zu liefern. Sie steht hier und nicht in
# den beiden Diensten, die sie brauchen: Gedächtnis und Fertigkeiten lasen die
# Kennung früher je als eigenes Literal, und wer eines davon beim Modellwechsel
# übersieht, bekommt in genau einem der beiden Bereiche stille Falschtreffer.
MODEL_TAG = "potion-multilingual-128M"

# Die Anbieter, bei denen `encode` ersatzweise rechnen darf, mit dem
# Einbettungsmodell, das genommen wird, wenn der Zugang keines vorgibt. Beide
# sprechen denselben OpenAI-kompatiblen `/embeddings`-Endpunkt und kürzen per
# `dimensions` auf `EMBEDDING_DIMENSIONS` — deshalb ein Weg für beide
# (`encode_ueber_anbieter`). Azure OpenAI fehlt, weil dort jede Ressource ihr
# eigenes Einbettungs-Deployment braucht, das MSM nicht kennt.
RUECKFALL_MODELLE: dict[str, str] = {
    "google": "text-embedding-004",
    "openai": "text-embedding-3-small",
}

# Breite der Spalte `embedding_model` in beiden Tabellen. Ein Rückfallvektor
# trägt `<anbieter>:<modell>` (`_tag`). Alle Räume haben 256 Zahlen und bestehen
# jede Längenprüfung — getrennt hält sie allein diese Kennung. Vorher trug auch
# ein Google-Vektor `MODEL_TAG`, und sobald das lokale Modell zurück war,
# verglich die Suche potion-Fragen mit Google-Einträgen: Zahlen ohne Bedeutung,
# und nichts meldete es.
_TAG_MAX = 64

# Panel-Einstellung: bei welchem Anbieter `encode` ohne lokales Modell rechnen
# darf, ein Schlüssel aus `RUECKFALL_MODELLE` oder "off". Fehlt der Eintrag,
# heißt das "off".
RUECKFALL_SCHLUESSEL = "ai_embedding_fallback"

# Der Vorgänger, ein Ja/Nein nur für Google, stand einen Tag lang in new-feat.
# Ein dort gesetztes Ja gilt weiter als "google", solange nichts Neues gewählt
# ist.
_ALTER_SCHLUESSEL = "ai_embedding_google_fallback"


@dataclass(frozen=True)
class Kodierung:
    """Vektoren samt dem Modell, das sie gerechnet hat.

    Die Kennung gehört an den Vektor und nicht an eine Konstante: `encode` hat
    zwei Quellen, und welche gerade antwortet, weiß nur `encode`. Wer speichert,
    legt ``modell`` daneben ab; wer vergleicht, nimmt nur gespeicherte Vektoren
    mit derselben Kennung wie seine Frage.
    """

    vektoren: list[list[float]]
    modell: str


# Wie lange ein gescheiterter Ladeversuch gilt, bevor MSM es noch einmal
# probiert. Vorher galt er für immer: wer die halb entpackten Gewichte
# nachträglich vervollständigte oder den knappen Speicher freiräumte, bekam die
# Bedeutungssuche trotzdem erst nach einem Neustart des Panels zurück. Zehn
# Minuten sind so gewählt, dass die ursprüngliche Sorge — ein Ladeversuch von
# rund fünf Sekunden je Chatnachricht — ausgeschlossen bleibt: schlimmstenfalls
# kostet ein dauerhaft defektes Modell alle zehn Minuten einmal diese fünf
# Sekunden, und in der Zwischenzeit läuft alles wie bisher ohne Vektoren.
NEUVERSUCH_NACH_SEKUNDEN = 600.0

_lock = threading.Lock()
_model = None
#: Zeitpunkt des letzten gescheiterten Ladeversuchs auf der monotonen Uhr, oder
#: ``None``, solange es keinen gab. Ein einziger Merker für beide Fehlerarten —
#: fehlende Dateien wie beschädigte Gewichte —, weil beide auf demselben Weg
#: heilen: jemand legt das Verzeichnis in Ordnung, ohne das Panel anzufassen.
#: Der frühere ``_load_failed`` unterschied sie ebenfalls nicht, hielt aber bis
#: zum Prozess-Neustart. Teuer ist ohnehin nur das Laden selbst; ``is_available``
#: kostet zwei Dateiblicke und darf deshalb bei jedem Versuch neu antworten.
_letzter_fehlschlag: float | None = None


def model_path() -> Path:
    """Verzeichnis des lokalen Modells.

    Bewusst *kein* Modellname, der zur Laufzeit nachgeladen wird: `model2vec`
    wuerde einen unbekannten Namen bei HuggingFace suchen. Ein Panel, das im
    Betrieb Gewichte aus dem Internet nachlaedt, ist eine Supply-Chain-Flaeche,
    die wir nicht wollen. Das Modell kommt einmalig beim Update.
    """
    configured = (settings.ai_embedding_model_dir or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "ml-models" / "potion-multilingual-128M"


def is_available() -> bool:
    """Laesst sich hier ein Vektor berechnen? Ohne das Modell zu laden.

    Geprueft werden **beide** Voraussetzungen: die Gewichte auf der Platte und
    die Bibliothek im Interpreter. Vorher zaehlten nur die Dateien — in einer
    Umgebung mit heruntergeladenem Modell, aber fehlendem `model2vec` meldete
    die Funktion "verfuegbar", und `encode` lieferte trotzdem nichts. Wer sich
    darauf verliess (etwa eine Testvorbedingung), bekam einen Fehlschlag
    gemeldet, wo ein sauberes Ueberspringen richtig gewesen waere.

    `find_spec` importiert nicht, es sucht nur — die Kosten sind ein
    Dateisystemblick, nicht das Laden der Bibliothek.

    Was diese Funktion **nicht** beantwortet: ob das Laden dieser Dateien auch
    gelingt. Beschädigte Gewichte bestehen sie anstandslos. Wer den tatsächlich
    einsatzbereiten Zustand braucht, fragt ``is_ready``.
    """
    from importlib.util import find_spec

    path = model_path()
    if not ((path / "config.json").is_file() and (path / "model.safetensors").is_file()):
        return False
    try:
        return find_spec("model2vec") is not None
    except (ImportError, ValueError):
        return False


def _fehlschlag_gilt_noch() -> bool:
    """Ist der letzte Fehlschlag jung genug, um einen neuen Versuch zu sparen?

    Dieselbe Frist beantwortet zwei Fragen: ob ``_load`` es noch einmal
    probiert und was ``is_ready`` meldet. Zwei Fristen wären zwei Wahrheiten
    über denselben Zustand.
    """
    if _letzter_fehlschlag is None:
        return False
    return (time.monotonic() - _letzter_fehlschlag) < NEUVERSUCH_NACH_SEKUNDEN


def rueckfall(db: Session | None = None) -> str | None:
    """Der gewählte Rückfallanbieter, oder ``None``. Standard: keiner."""
    from services.panel_settings_service import PanelSettingsService

    wert = PanelSettingsService.get(RUECKFALL_SCHLUESSEL, "", db=db).strip().lower()
    if not wert:
        alt = PanelSettingsService.get(_ALTER_SCHLUESSEL, "false", db=db)
        wert = "google" if alt.strip().lower() == "true" else "off"
    return wert if wert in RUECKFALL_MODELLE else None


def set_rueckfall(anbieter: str | None, db: Session) -> str | None:
    """Wählt den Rückfallanbieter; gespeichert wird mit dem Commit des Aufrufers.

    ``None`` schaltet den Rückfall ab. Ein unbekannter Anbieter ist ein
    ``ValueError`` — lieber abgelehnt als still als „aus" gespeichert.
    """
    from services.panel_settings_service import PanelSettingsService

    if anbieter is not None and anbieter not in RUECKFALL_MODELLE:
        raise ValueError(f"unbekannter Rückfallanbieter: {anbieter}")
    PanelSettingsService.set(RUECKFALL_SCHLUESSEL, anbieter or "off", db=db)
    return anbieter


def lokal_bereit() -> bool:
    """Kann das lokale Modell gerade rechnen? Ohne es zu laden.

    Der lokale Teil von `is_ready`, einzeln gefragt von der Einstellungsseite:
    dort entscheidet er, ob der Google-Schalter überhaupt etwas bewirkt.
    """
    if _model is not None:
        return True
    if _fehlschlag_gilt_noch():
        return False
    return is_available()


def is_ready() -> bool:
    """Kann die Bedeutungssuche gerade rechnen? Einschließlich Ladefehlern.

    Der Unterschied zu ``is_available``: dort zählt nur, was auf der Platte und
    im Interpreter liegt, hier zählt zusätzlich, ob das Laden zuletzt
    gescheitert ist. Beschädigte Gewichte bestehen ``is_available`` nämlich —
    die Dateien sind ja da — und scheitern erst in ``from_pretrained``. Genau
    dieser Fall war bisher nirgends sichtbar außer als eine Warnzeile im Log
    beim ersten Versuch; der Betreiber sah an keiner Stelle, dass sein
    Gedächtnis seither ohne Bedeutungssuche arbeitet.

    Geladen wird hier nichts — die Antwort muss auch in einem GET billig sein.
    Ist die Frist des Fehlschlags abgelaufen, entscheidet wieder allein, was im
    Verzeichnis liegt: der nächste Abruf wird es ohnehin erneut versuchen.

    Ein Rückfallzugang zählt nur, wenn der Betreiber ihn gewählt hat.
    """
    if lokal_bereit():
        return True
    try:
        anbieter = rueckfall()
        if anbieter is None:
            return False
        from database import SessionLocal

        with SessionLocal() as db:
            return anbieter in zugaenge_mit_schluessel(db)
    except Exception:
        return False


def _load():
    """Laedt das Modell; ein Fehlschlag gilt, bis die Frist abgelaufen ist.

    Das Laden dauert rund fuenf Sekunden. Ohne den Merker wuerde jede Anfrage
    es erneut versuchen — bei einem defekten Modellverzeichnis waere das ein
    Timeout je Chatnachricht statt einer einzelnen Warnung. Ohne Frist am
    Merker wäre es das andere Extrem, und das war der Zustand bis eben: ein
    einziger schlechter Moment — halb entpackte Gewichte, kurzzeitig zu wenig
    Speicher — schaltete die Bedeutungssuche bis zum Prozess-Neustart ab.
    """
    global _model, _letzter_fehlschlag
    if _model is not None:
        return _model
    with _lock:
        if _model is not None or _fehlschlag_gilt_noch():
            return _model
        path = model_path()
        if not is_available():
            logger.warning(
                "AI-Embeddingmodell fehlt unter %s — Gedaechtnissuche laeuft ohne Vektoren", path
            )
            _letzter_fehlschlag = time.monotonic()
            return None
        try:
            from model2vec import StaticModel

            _model = StaticModel.from_pretrained(str(path))
            _letzter_fehlschlag = None
            logger.info("AI-Embeddingmodell geladen: %s", path)
        except Exception as exc:
            # Beschaedigte Gewichte, fehlende Bibliothek, zu wenig Speicher:
            # alles Gruende, ohne Vektoren weiterzumachen statt abzustuerzen.
            logger.warning(
                "AI-Embeddingmodell konnte nicht geladen werden (%s) — "
                "Gedaechtnissuche laeuft ohne Vektoren", type(exc).__name__,
            )
            _letzter_fehlschlag = time.monotonic()
            return None
    return _model


def encode_ueber_anbieter(
    texts: list[str],
    *,
    api_key: str,
    base_url: str,
    model: str,
    client: Any | None = None,
) -> list[list[float]] | None:
    """Berechnet 256-dimensionale normalisierte Vektoren über `/embeddings`.

    Google AI Studio und OpenAI sprechen hier dieselbe Form; der Unterschied
    liegt allein in ``base_url`` und ``model``.
    """
    if not texts:
        return []
    clean_model = model[len("models/"):] if model.startswith("models/") else model
    try:
        import httpx
        import numpy as np

        url = f"{base_url.rstrip('/')}/embeddings"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": clean_model,
            "input": texts,
            "dimensions": EMBEDDING_DIMENSIONS,
        }

        def _send(c: Any) -> Any:
            return c.post(url, headers=headers, json=payload, timeout=30.0)

        if client is not None:
            resp = _send(client)
        else:
            with httpx.Client() as temp_c:
                resp = _send(temp_c)

        if resp.status_code != 200:
            logger.warning(
                "Embedding-Rückfall fehlgeschlagen: model=%s status=%s body=%s",
                clean_model,
                resp.status_code,
                resp.text[:200],
            )
            return None

        data = resp.json().get("data", [])
        raw_vectors = []
        for item in data:
            vec = item.get("embedding", [])
            vec = vec[:EMBEDDING_DIMENSIONS]
            if len(vec) < EMBEDDING_DIMENSIONS:
                vec = vec + [0.0] * (EMBEDDING_DIMENSIONS - len(vec))
            raw_vectors.append(vec)

        if not raw_vectors:
            return None

        matrix = np.asarray(raw_vectors, dtype="float32")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (matrix / norms).astype("float32").tolist()
    except Exception as exc:
        logger.warning("Embedding-Rückfall fehlgeschlagen: error=%s", type(exc).__name__)
        return None


def _aktiver_zugang(db: Session, anbieter: str):
    """Der erste aktive Zugang dieses Anbieters mit Betreiberschlüssel, oder ``None``."""
    from models import AiProvider

    return (
        db.query(AiProvider)
        .filter(
            AiProvider.provider_kind == anbieter,
            AiProvider.enabled.is_(True),
            AiProvider.operator_api_key_encrypted.isnot(None),
        )
        .first()
    )


def zugaenge_mit_schluessel(db: Session) -> list[str]:
    """Welche Rückfallanbieter einen aktiven Zugang mit Schlüssel haben."""
    return [anbieter for anbieter in RUECKFALL_MODELLE if _aktiver_zugang(db, anbieter)]


def _zugang(db: Session, anbieter: str) -> tuple[str, str, str] | None:
    """Schlüssel, Adresse und Einbettungsmodell eines Rückfallanbieters, oder ``None``.

    Das Modell kommt aus dem Zugang, wenn dort ein Einbettungsmodell eingetragen
    ist, sonst aus `RUECKFALL_MODELLE`. Die Adresse steht in der Anbieterdatei
    (`ai_provider_registry`), nicht beim Betreiber.
    """
    from services import ai_provider_registry, ai_provider_service

    zugang = _aktiver_zugang(db, anbieter)
    if zugang is None:
        return None
    key = ai_provider_service.resolve_api_key(db, zugang, 0)
    if not key:
        return None
    modell = RUECKFALL_MODELLE[anbieter]
    if zugang.default_model and "embedding" in zugang.default_model.lower():
        modell = zugang.default_model
    return key, ai_provider_registry.anbieter(anbieter).base_url, modell


def _tag(anbieter: str, modell: str) -> str:
    """Die Kennung eines Rückfallvektors: Anbieter plus gerechnetes Modell.

    Das Modell steht mit darin, weil `text-embedding-004` und
    `text-embedding-3-small` ebenso wenig in einen Raum gehören wie potion und
    Google. ``models/`` fällt weg wie in `encode_ueber_anbieter`, damit dieselbe
    Wahl dieselbe Kennung ergibt, gleich wie der Betreiber sie geschrieben hat.
    """
    if modell.startswith("models/"):
        modell = modell[len("models/"):]
    return f"{anbieter}:{modell}"[:_TAG_MAX]


def _rueckfall_zugang(db: Session | None) -> tuple[str, str, str, str] | None:
    """Anbieter, Schlüssel, Adresse und Modell des gewählten Rückfalls, oder ``None``.

    ``None`` auch dann, wenn der Betreiber keinen Rückfall gewählt hat — das
    ist die eine Stelle, an der die Wahl wirkt; `encode`, `aktives_modell` und
    damit jeder Aufrufer gehen hier durch. Gerechnet wird in der Sitzung des
    Aufrufers, sonst in einer eigenen.
    """
    try:
        anbieter = rueckfall(db)
        if anbieter is None:
            return None
        if db is not None:
            zugang = _zugang(db, anbieter)
        else:
            from database import SessionLocal

            with SessionLocal() as eigene:
                zugang = _zugang(eigene, anbieter)
        return None if zugang is None else (anbieter, *zugang)
    except Exception:
        return None


def aktives_modell(*, db: Session | None = None) -> str | None:
    """Die Kennung, die `encode` jetzt liefern würde, ohne etwas zu rechnen.

    Gebraucht von `ai_memory_service._vektoren_nachziehen`: dort muss vor dem
    Rechnen feststehen, welche gespeicherten Vektoren zum heutigen Modell
    passen und welche neu müssen. Ändert sich die Quelle zwischen dieser Frage
    und dem Rechnen, schadet das nicht: gespeichert wird die Kennung aus der
    `Kodierung`, und der nächste Abruf holt den Rest nach.
    """
    if _load() is not None:
        return MODEL_TAG
    zugang = _rueckfall_zugang(db)
    return None if zugang is None else _tag(zugang[0], zugang[3])


def encode(
    texts: list[str], *, db: Session | None = None, nur_lokal: bool = False
) -> Kodierung | None:
    """Wandelt Texte in normalisierte Vektoren um, oder ``None`` ohne Modell.

    Zurück kommt eine `Kodierung`: die Vektoren **und** die Kennung des
    Modells, das sie gerechnet hat — lokal `MODEL_TAG`, im Rückfall
    `<anbieter>:<modell>`. Nur ein Vergleich zwischen gleichen Kennungen hat
    eine Bedeutung.

    Normalisiert wird hier, damit die Aehnlichkeit spaeter ein reines
    Skalarprodukt ist — der Aufrufer muss nichts ueber Vektorlaengen wissen.

    ``db`` ist die Sitzung des Aufrufers. Gebraucht wird sie nur für den
    Rückfall, und wer mitten in einer Schreibarbeit rechnet, muss sie
    mitgeben. Eine zweite, eigene Sitzung liegt in der Testsuite auf derselben
    Verbindung (`StaticPool`), und ihr Schließen rollt die offene Arbeit des
    Aufrufers zurück: `learn_skill` legte das persönliche Team an, der Rückfall
    schloss seine Sitzung, und der Skill zeigte danach auf ein Team, das es
    nicht mehr gab — gemeldet als „parallel geändert". Nur wer keine Sitzung hat
    (Absichtserkennung, Werkzeugauswahl), bekommt hier eine eigene, kurze.

    ``nur_lokal`` schließt den Rückfall aus, auch wenn er gewählt ist.
    Für Aufrufer, die nicht auf das Netz warten dürfen: die Absichtserkennung
    der Stimme rechnet synchron in der Ereignisschleife, je Teiltranskript.
    """
    if not texts:
        return Kodierung([], MODEL_TAG)
    model = _load()
    if model is None:
        if nur_lokal:
            return None
        # Rückfall — nur beim Anbieter, den der Betreiber gewählt hat.
        zugang = _rueckfall_zugang(db)
        if zugang is None:
            return None
        anbieter, key, base_url, modell = zugang
        vektoren = encode_ueber_anbieter(texts, api_key=key, base_url=base_url, model=modell)
        return None if vektoren is None else Kodierung(vektoren, _tag(anbieter, modell))
    try:
        import numpy as np

        vectors = model.encode(texts)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        # Ein Nullvektor entsteht bei reinem Sonderzeichentext. Ohne diesen
        # Schutz waere das Ergebnis NaN und jede Aehnlichkeit unbrauchbar.
        norms[norms == 0] = 1.0
        return Kodierung((vectors / norms).astype("float32").tolist(), MODEL_TAG)
    except Exception as exc:
        logger.warning("AI-Embedding fehlgeschlagen error=%s", type(exc).__name__)
        return None


def vektor_zu_bytes(vektor: Sequence[float]) -> bytes:
    """Packt einen Vektor in die Form, in der er gespeichert wird.

    **Warum nicht als JSON.** Gemessen am 19.08.2026 an 5.000 Einträgen:
    dieselben Zahlen als Text zu lesen kostet 381 ms und 26,7 MB aus der
    Datenbank, als float32-Bytes 4 ms und 5,1 MB. Das war über die Hälfte
    der gesamten Rechenzeit eines Chatabrufs — für einen reinen
    Formatwechsel. Die Zahlen kommen ohnehin aus einem float32-Modell; JSON
    schrieb sie nur als Ziffernfolge aus und musste sie Ziffer für Ziffer
    zurückrechnen.

    Die Form auf der Platte ist 256 native ``float`` hintereinander, ohne
    Rahmen und ohne Kopf. Auf jeder Plattform, auf der MSM läuft (x86-64,
    ARM64), sind das vier Bytes je Zahl in Little-Endian; ein Umzug der
    Datenbank auf eine Maschine mit anderer Byte-Reihenfolge oder anderer
    ``float``-Breite wäre ein Bruch. Was danach herauskäme, ist aber nicht
    lautlos falsch: eine andere Breite scheitert an der Längenprüfung in
    `bytes_zu_vektor`, und die Zeile gilt dann als vektorlos statt als
    unähnlich. Eine Umrechnung dafür vorzuhalten wäre Aufwand für eine
    Plattform, die es hier nicht gibt.
    """
    return array("f", vektor).tobytes()


def bytes_zu_vektor(roh: bytes | None) -> Sequence[float] | None:
    """Liest einen gespeicherten Vektor, oder ``None``, wenn er nicht taugt.

    ``array`` aus der Standardbibliothek und nicht numpy: das Ergebnis ist
    eine gewöhnliche Zahlenfolge und bleibt damit dieselbe Währung, die
    `encode` liefert — kein Aufrufer muss etwas über Arrays wissen. Schnell
    ist es trotzdem, weil `similarity` den Puffer direkt übernimmt: gemessen
    3,3 ms für 5.000 Vektoren, gegen 30 ms mit ``struct.unpack`` und 381 ms
    mit JSON.

    Eine Länge, die nicht zu `EMBEDDING_BYTES` passt, gilt als beschädigt
    und damit als *fehlend*. Sie klaglos zu lesen hieße, drei Zahlen gegen
    256 zu vergleichen — das schlüge dann irgendwo weit weg von der Ursache
    fehl, statt hier einen Eintrag ohne Bedeutungsanteil zu ergeben.
    """
    if not roh or len(roh) != EMBEDDING_BYTES:
        return None
    werte = array("f")
    werte.frombytes(roh)
    return werte


def similarity(
    query_vector: Sequence[float], vectors: list[Sequence[float]]
) -> list[float]:
    """Kosinus-Aehnlichkeit gegen bereits normalisierte Vektoren.

    Bei hoechstens einigen hundert Eintraegen ist das ein Skalarprodukt in
    Millisekunden. Ein Vektorindex wie pgvector lohnt sich ab etwa zehntausend
    Eintraegen — und haette hier einen Wechsel des Postgres-Images fuer jede
    Installation bedeutet.
    """
    if not vectors or not query_vector:
        return []
    try:
        import numpy as np

        matrix = np.asarray(vectors, dtype="float32")
        query = np.asarray(query_vector, dtype="float32")
        return matrix.dot(query).tolist()
    except Exception as exc:
        logger.warning("AI-Aehnlichkeit fehlgeschlagen error=%s", type(exc).__name__)
        return []


def reset_for_tests() -> None:
    """Vergisst den geladenen Zustand. Nur fuer Tests."""
    global _model, _letzter_fehlschlag
    with _lock:
        _model = None
        _letzter_fehlschlag = None
