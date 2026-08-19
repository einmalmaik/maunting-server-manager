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
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from config import settings


logger = logging.getLogger(__name__)

# Ausgabegroesse von `potion-multilingual-128M`. Fest verdrahtet, damit ein
# gespeicherter Vektor erkennbar nicht mehr zum geladenen Modell passt.
EMBEDDING_DIMENSIONS = 256

# Kennung des Modells, mit dem ein gespeicherter Vektor entstanden ist. Wechselt
# der Betreiber das Modell, passen alte Vektoren nicht mehr — sie werden dann
# ignoriert statt falsche Ähnlichkeiten zu liefern. Sie steht hier und nicht in
# den beiden Diensten, die sie brauchen: Gedächtnis und Fertigkeiten lasen die
# Kennung früher je als eigenes Literal, und wer eines davon beim Modellwechsel
# übersieht, bekommt in genau einem der beiden Bereiche stille Falschtreffer.
MODEL_TAG = "potion-multilingual-128M"

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
    """
    if _model is not None:
        return True
    if _fehlschlag_gilt_noch():
        return False
    return is_available()


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


def encode(texts: list[str]) -> list[list[float]] | None:
    """Wandelt Texte in normalisierte Vektoren um, oder ``None`` ohne Modell.

    Normalisiert wird hier, damit die Aehnlichkeit spaeter ein reines
    Skalarprodukt ist — der Aufrufer muss nichts ueber Vektorlaengen wissen.
    """
    if not texts:
        return []
    model = _load()
    if model is None:
        return None
    try:
        import numpy as np

        vectors = model.encode(texts)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        # Ein Nullvektor entsteht bei reinem Sonderzeichentext. Ohne diesen
        # Schutz waere das Ergebnis NaN und jede Aehnlichkeit unbrauchbar.
        norms[norms == 0] = 1.0
        return (vectors / norms).astype("float32").tolist()
    except Exception as exc:
        logger.warning("AI-Embedding fehlgeschlagen error=%s", type(exc).__name__)
        return None


def similarity(query_vector: list[float], vectors: list[list[float]]) -> list[float]:
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
