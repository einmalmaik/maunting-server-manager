"""Die Dokumentation des Panels, lesbar fuer das Modell.

**Warum es dieses Modul gibt.** Im Systemprompt steht ueber MSM selbst kein
Satz ausser der Rollenzeile. Auf jede Frage nach Blueprints, Social-Login,
Self-Hosting, der Hoster-API oder dem Datenschutz antwortete das Modell bisher
aus seinem Training — also mit Wissen ueber *andere* Panels. Das klingt richtig
und ist es fast nie.

**Warum die Sprachdatei allein nicht reicht.** Der naheliegende Weg waere,
`frontend/src/locales/de.json` zu lesen: dort liegen 53.000 Zeichen Prosa der
Doku-Seiten. Der harte Vertrag steht aber nicht darin, sondern als Konstante in
den TSX-Dateien. `docsHosterApi.statusCode.port_conflict` erklaert, *was*
`port_conflict` bedeutet — die Zeichenkette `port_conflict` selbst ist nur der
JSON-Schluessel und kommt in keinem Text vor. Dasselbe gilt fuer Endpunktpfade,
Headernamen, `desired_state`-Werte und die Admin-Permissions. Ein reiner
i18n-Leser kann zusammenfassen und muss bei jeder konkreten Frage raten.

**Woher der Text also kommt: je Seite von dort, wo er heute schon gepflegt
wird.** Fuer Blueprints, Hoster-API und Self-Hosting ist das `docs/*.md` — es
wird mitausgeliefert (`install.sh` rsyncht `docs/` nach `$MSM_DIR/docs/`), ist
laenger als die Seite und `docs/hoster-api.md` wird von
`test_hoster_api_docs_contract.py` gegen die echten Router gehalten. Fuer
Social-Login und Datenschutz gibt es keine Markdown-Fassung; dort ist die
Sprachdatei die einzige Quelle. Zwei Quellen, beide vorhanden — keine dritte,
die jemand nachpflegen muesste.

Immer `de.json`, nie eine der neun uebrigen Sprachdateien: die tragen nur einen
veralteten Teil von `docs` und keinen der anderen Namensraeume.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Dieselbe Hoehe, die im Repo und unter /opt/msm stimmt: services/ -> backend/
# -> Wurzel. Vorbild ist `routers/nodes.py`, das die Helper-Skripte genauso
# findet.
WURZEL = Path(__file__).resolve().parents[2]
LOCALE_DE = WURZEL / "frontend" / "src" / "locales" / "de.json"

# Ein Abschnitt am Stueck. Grosszuegig genug fuer jeden vorkommenden Abschnitt
# (der laengste liegt bei rund 4.000 Zeichen), aber weit unter dem Budget einer
# Werkzeugrunde (48.000) — eine ganze Seite auf einmal wuerde alle anderen
# Ergebnisse aus dem Folgekontext draengen.
MAX_ABSCHNITT_ZEICHEN = 12_000
MAX_TREFFER = 12
SCHNIPSEL_ZEICHEN = 320

# Die Marke stammt aus `ai_context_service`. Ohne sie haelt das Modell den
# Ausschnitt fuer das vollstaendige Ergebnis.
GEKUERZT = " [...gekuerzt]"


@dataclass(frozen=True)
class Abschnitt:
    schluessel: str
    titel: str
    text: str


@dataclass(frozen=True)
class Seite:
    schluessel: str
    titel: str
    route: str
    quelle: str  # "markdown" | "i18n"
    datei: Path | None = None
    namensraum: str | None = None
    # Nur fuer i18n-Seiten: die Reihenfolge, in der die Seite ihre Abschnitte
    # **rendert**. Sie ist nicht die Reihenfolge der Sprachdatei — bei
    # `privacyPolicy` stehen `hoster` und `credentials` im JSON am Ende und auf
    # der Seite in der Mitte. Wer die JSON-Reihenfolge ausgibt, behauptet eine
    # Gliederung, die niemand zu sehen bekommt.
    reihenfolge: tuple[str, ...] = ()


# Die Zuordnung Namensraum <-> Route ist **nicht** ableitbar: die
# Blueprint-Doku haengt am Namensraum `docs`, die Datenschutzseite an
# `privacyPolicy`, und `/docs` selbst an `docsIndex`. Drei von sechs brechen
# jede Konvention, deshalb steht die Tabelle hier ausgeschrieben.
SEITEN: dict[str, Seite] = {
    "blueprints": Seite(
        schluessel="blueprints",
        titel="Blueprints",
        route="/docs/blueprints",
        quelle="markdown",
        datei=WURZEL / "docs" / "blueprints.md",
    ),
    "hoster-api": Seite(
        schluessel="hoster-api",
        titel="Hoster-API (Shop-Anbindung)",
        route="/docs/hoster-api",
        quelle="markdown",
        datei=WURZEL / "docs" / "hoster-api.md",
    ),
    "self-hosting": Seite(
        schluessel="self-hosting",
        titel="Self-Hosting und Betrieb",
        route="/docs/self-hosting",
        quelle="markdown",
        datei=WURZEL / "docs" / "self-hosting.md",
    ),
    "oauth": Seite(
        schluessel="oauth",
        titel="Social-Login (OAuth / OIDC)",
        route="/docs/oauth",
        quelle="i18n",
        namensraum="docsOAuth",
        reihenfolge=("intro", "presets", "create", "security", "troubleshooting"),
    ),
    "datenschutz": Seite(
        schluessel="datenschutz",
        titel="Datenschutzerklaerung",
        route="/privacy",
        quelle="i18n",
        namensraum="privacyPolicy",
        # Wie `Privacy.tsx` rendert, nicht wie `de.json` sortiert.
        reihenfolge=(
            "scope", "accounts", "infrastructure", "protection", "providers",
            "ai", "desktopApp", "hoster", "credentials", "storage", "retention",
            "responsibility",
        ),
    ),
}

# Stand der Datenschutzerklaerung. Er steht als Literal in `Privacy.tsx` und in
# keiner Sprachdatei — also genau die zwei Angaben, die ein Modell sonst
# erfindet. Ein Test haelt sie gegen die TSX-Datei.
DATENSCHUTZ_VERSION = "2.6"
DATENSCHUTZ_STAND = "2026-08-23"

# `de.json` fuehrt neben `privacyPolicy` einen zweiten, **toten** Namensraum
# `privacy` — sieben Schluessel, darunter "6. Verschluesselte Cloud-Backups
# (S3)". Keine Seite rendert ihn, und `privacyPolicy.sections.ai.heading`
# traegt bereits die Nummer 6. Wer beide liest, meldet zwei Abschnitte 6 und
# zitiert einen S3-Abschnitt, den es nicht gibt.
TOTE_NAMENSRAEUME = frozenset({"privacy"})


# ── Zwischenspeicher ─────────────────────────────────────────────────────

@dataclass
class _Eintrag:
    mtime: float
    abschnitte: list[Abschnitt] = field(default_factory=list)


_cache: dict[str, _Eintrag] = {}
_locale_cache: tuple[float, dict] | None = None


def _locale() -> dict:
    """`de.json` einmal je Aenderung lesen. 217 kB pro Werkzeugaufruf waeren
    unnoetig, und die Datei aendert sich nur bei einem Update."""
    global _locale_cache
    mtime = LOCALE_DE.stat().st_mtime
    if _locale_cache is None or _locale_cache[0] != mtime:
        _locale_cache = (mtime, json.loads(LOCALE_DE.read_text(encoding="utf-8")))
    return _locale_cache[1]


# ── Normalisierung fuer die Suche ────────────────────────────────────────

_FALTUNG = (
    ("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "s"),
    ("ae", "a"), ("oe", "o"), ("ue", "u"), ("ss", "s"),
)


def falten(text: str) -> str:
    """Umlaute **und** ihre ASCII-Umschreibung auf dieselbe Form bringen.

    Vier Schluessel der Sprachdatei stehen als `Massgeblich`, `ausschliesslich`,
    `verschluesselt` und `standardmaessig` da, waehrend der Benutzer
    "massgeblich" oder "Gedaechtnis" oder "Gedächtnis" schreibt. Ohne Faltung
    findet die Suche den Absatz nicht — und "nichts gefunden" ist hier die
    gefaehrlichste aller Antworten, weil sie wie eine Auskunft aussieht.

    Die Faltung ist bewusst grob: `ue -> u` macht aus "Quelle" ein "qlle". Weil
    sie auf Suchbegriff **und** Text gleich angewendet wird, entstehen dadurch
    hoechstens zusaetzliche Treffer, nie fehlende. In dieser Richtung ist der
    Fehler harmlos, in der anderen nicht.
    """
    gefaltet = unicodedata.normalize("NFC", text.lower())
    for von, nach in _FALTUNG:
        gefaltet = gefaltet.replace(von, nach)
    return gefaltet


def _slug(text: str) -> str:
    roh = falten(text)
    roh = re.sub(r"[^a-z0-9]+", "-", roh).strip("-")
    return roh[:64] or "abschnitt"


# ── Markdown ─────────────────────────────────────────────────────────────

_H2 = re.compile(r"^## +(.+?)\s*$", re.M)


def _markdown_abschnitte(datei: Path) -> list[Abschnitt]:
    """An `##` schneiden, `###` bleibt beim Elternabschnitt.

    Bewusst nicht auch an `###`: "PHP" und "Python" unter "Signatur nachrechnen"
    sind ohne ihre Ueberschrift sinnlos, und ein Verzeichnis aus 90 Eintraegen
    ist keine Gliederung mehr.
    """
    text = datei.read_text(encoding="utf-8")
    treffer = list(_H2.finditer(text))
    abschnitte: list[Abschnitt] = []
    kopf = text[: treffer[0].start()].strip() if treffer else text.strip()
    if kopf:
        # Alles vor der ersten `##` — Titelzeile und Vorspann. Der traegt bei
        # allen drei Dateien die Einordnung, wofuer die Seite ueberhaupt da ist.
        abschnitte.append(Abschnitt("einleitung", "Einleitung", kopf))
    for i, m in enumerate(treffer):
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
        titel = m.group(1).strip()
        koerper = text[m.end():ende].strip()
        abschnitte.append(Abschnitt(_slug(titel), titel, f"## {titel}\n\n{koerper}"))
    return abschnitte


# ── i18n ─────────────────────────────────────────────────────────────────

def _oauth_abschnitte(ns: dict) -> list[Abschnitt]:
    """Die OAuth-Seite hat fuenf Abschnitte mit je eigener Form.

    Bewusst je Abschnitt von Hand gebaut statt generisch geplaettet: die
    Preset-Namen kommen aus einem **fremden** Namensraum
    (`settings.oauth.preset.*`). Wer nur `docsOAuth` liest, bekommt sieben
    Beschreibungen ohne die Namen, zu denen sie gehoeren.
    """
    presets = _locale().get("settings", {}).get("oauth", {}).get("preset", {})
    toc = ns.get("toc", {})

    zeilen_presets = [
        f"- {presets.get(key, key)}: {wert}"
        for key, wert in ns.get("presets", {}).items()
        if key != "title"
    ]
    return [
        Abschnitt("intro", toc.get("intro", "Einleitung"), ns.get("intro", {}).get("body", "")),
        Abschnitt(
            "presets",
            ns.get("presets", {}).get("title", "Preset-Uebersicht"),
            "\n".join(zeilen_presets),
        ),
        Abschnitt(
            "create",
            ns.get("create", {}).get("title", "Provider anlegen"),
            "\n".join(
                f"{i}. {ns['create'][f'step{i}']}"
                for i in range(1, 99)
                if f"step{i}" in ns.get("create", {})
            ),
        ),
        Abschnitt(
            "security",
            ns.get("security", {}).get("title", "Sicherheit"),
            "\n".join(
                f"- {ns['security'][f'rule{i}']}"
                for i in range(1, 99)
                if f"rule{i}" in ns.get("security", {})
            ),
        ),
        Abschnitt(
            "troubleshooting",
            toc.get("troubleshooting", "Troubleshooting"),
            "\n\n".join(
                f"{ns['troubleshooting'][f'err{i}Title']}\n{ns['troubleshooting'][f'err{i}Body']}"
                for i in range(1, 99)
                if f"err{i}Title" in ns.get("troubleshooting", {})
            ),
        ),
    ]


def _datenschutz_abschnitte(ns: dict) -> list[Abschnitt]:
    sections = ns.get("sections", {})
    abschnitte = [
        Abschnitt(
            "stand",
            "Stand des Dokuments",
            f"Version {DATENSCHUTZ_VERSION}, Stand {DATENSCHUTZ_STAND}.\n\n"
            f"{ns.get('intro', '')}\n\n{ns.get('callout', '')}".strip(),
        )
    ]
    for key in SEITEN["datenschutz"].reihenfolge:
        eintrag = sections.get(key)
        if not isinstance(eintrag, dict):
            continue
        teile = [eintrag.get("body", "")]
        teile += [f"- {wert}" for wert in eintrag.get("items", {}).values()]
        abschnitte.append(
            Abschnitt(key, eintrag.get("heading", key), "\n".join(t for t in teile if t))
        )
    return abschnitte


def _i18n_abschnitte(seite: Seite) -> list[Abschnitt]:
    daten = _locale()
    ns = daten.get(seite.namensraum or "")
    if not isinstance(ns, dict):
        return []
    if seite.schluessel == "oauth":
        return _oauth_abschnitte(ns)
    return _datenschutz_abschnitte(ns)


# ── Zugriff ──────────────────────────────────────────────────────────────

class DokuNichtVerfuegbar(RuntimeError):
    """Die Quelle fehlt oder ist nicht lesbar.

    Ausdruecklich **kein** leeres Ergebnis: "in der Doku steht nichts dazu" und
    "ich konnte die Doku nicht lesen" sind zwei verschiedene Auskuenfte, und nur
    eine davon darf das Modell weitergeben. Dasselbe Muster wie `web_search`,
    das bei Ausfall `available: false` meldet statt einer leeren Trefferliste.
    """


def abschnitte(seiten_schluessel: str) -> list[Abschnitt]:
    seite = SEITEN.get(seiten_schluessel)
    if seite is None:
        raise DokuNichtVerfuegbar(f"Unbekannte Seite: {seiten_schluessel}")
    quelle = seite.datei if seite.quelle == "markdown" else LOCALE_DE
    try:
        mtime = quelle.stat().st_mtime
    except OSError as exc:
        raise DokuNichtVerfuegbar(f"Quelle nicht lesbar: {quelle.name}") from exc

    eintrag = _cache.get(seiten_schluessel)
    if eintrag is None or eintrag.mtime != mtime:
        try:
            gebaut = (
                _markdown_abschnitte(quelle)
                if seite.quelle == "markdown"
                else _i18n_abschnitte(seite)
            )
        except (OSError, ValueError, KeyError) as exc:
            raise DokuNichtVerfuegbar(f"Quelle nicht lesbar: {quelle.name}") from exc
        if not gebaut:
            raise DokuNichtVerfuegbar(f"Quelle ohne Abschnitte: {quelle.name}")
        eintrag = _Eintrag(mtime=mtime, abschnitte=gebaut)
        _cache[seiten_schluessel] = eintrag
    return eintrag.abschnitte


def verzeichnis(seiten_schluessel: str) -> dict:
    """Die Gliederung einer Seite — billig, damit das Modell nicht raten muss.

    Ohne diesen Schritt muesste es Abschnittsnamen erfinden, um lesen zu
    koennen. Genau das soll die Belegpflicht verhindern.
    """
    seite = SEITEN[seiten_schluessel]
    rows = abschnitte(seiten_schluessel)
    return {
        "page": seite.schluessel,
        "title": seite.titel,
        "panel_page": seite.route,
        "sections": [
            {"section": a.schluessel, "title": a.titel, "chars": len(a.text)}
            for a in rows
        ],
    }


def abschnitt(seiten_schluessel: str, abschnitts_schluessel: str) -> dict:
    seite = SEITEN[seiten_schluessel]
    rows = abschnitte(seiten_schluessel)
    treffer = next((a for a in rows if a.schluessel == abschnitts_schluessel), None)
    if treffer is None:
        raise KeyError(abschnitts_schluessel)
    text = treffer.text
    gekuerzt = len(text) > MAX_ABSCHNITT_ZEICHEN
    return {
        "page": seite.schluessel,
        "section": treffer.schluessel,
        "title": treffer.titel,
        "panel_page": seite.route,
        "text": (text[:MAX_ABSCHNITT_ZEICHEN] + GEKUERZT) if gekuerzt else text,
        "truncated": gekuerzt,
        "total_chars": len(text),
    }


def suche(begriff: str, seiten_schluessel: str | None = None) -> list[dict]:
    """Volltext ueber alle Seiten, gefaltet auf beiden Seiten.

    Liefert Abschnittskennungen, mit denen `read_docs` weiterarbeiten kann —
    die Kombination ist dieselbe wie `search_server_files` vor `read_config`.
    """
    nadel = falten(begriff.strip())
    if not nadel:
        return []
    kandidaten = [seiten_schluessel] if seiten_schluessel else list(SEITEN)
    treffer: list[dict] = []
    for key in kandidaten:
        seite = SEITEN[key]
        try:
            rows = abschnitte(key)
        except DokuNichtVerfuegbar:
            # Eine unlesbare Seite darf die Suche ueber die uebrigen nicht
            # abbrechen; dass sie fehlt, meldet der Aufrufer gesondert.
            continue
        for a in rows:
            heuhaufen = falten(f"{a.titel}\n{a.text}")
            if heuhaufen.find(nadel) < 0:
                continue
            # Der Schnipsel kommt aus dem **ungefalteten** Text: was das
            # Modell zu sehen bekommt, muss lesbar sein und der Seite
            # entsprechen, nicht der Suchform.
            #
            # Die Fundstelle wird dafuer im Original **neu** gesucht. Hier
            # wurde einmal die gefaltete Position weiterverwendet — zwei
            # Koordinatenfehler auf einmal: der Titel steckt im Heuhaufen,
            # aber nicht im Text, und die Faltung kuerzt („ue"→„u", „ss"→„s";
            # die Docs sind durchgaengig ASCII-transkribiert). Ein Treffer
            # 8.000 Zeichen tief lag real Hunderte Zeichen hinter der
            # gefalteten Position, und der Schnipsel zeigte den Suchbegriff
            # gar nicht. Findet die Rohsuche nichts (der Treffer lebt nur in
            # der Faltung oder im Titel), zeigt der Schnipsel den
            # Abschnittsanfang — ehrlich ungenau statt praezise falsch.
            pos = a.text.lower().find(begriff.strip().lower())
            start = max(0, pos - SCHNIPSEL_ZEICHEN // 3) if pos >= 0 else 0
            treffer.append({
                "page": seite.schluessel,
                "section": a.schluessel,
                "title": a.titel,
                "panel_page": seite.route,
                "snippet": a.text[start:start + SCHNIPSEL_ZEICHEN].strip(),
            })
            if len(treffer) >= MAX_TREFFER:
                return treffer
    return treffer
