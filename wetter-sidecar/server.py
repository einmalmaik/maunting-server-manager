"""Wetter-Sidecar für Maunting Pontis.

Läuft neben MSM wie SearXNG/LiveKit und wird vom Backend unter
`127.0.0.1:8890` gelesen. Das Gerät spricht nur mit MSM; der Sidecar holt
globale offene Modelldaten (NOAA GFS, 0,5°) und beantwortet Punkte lokal.
Kein Nutzerstandort und keine Nutzer-IP verlassen den Server.

Zusätzlich liegt eine Ortsliste (GeoNames cities5000, CC BY 4.0) im
Datenordner. Sie wird einmalig geladen und lokal durchsucht; die Suche geht
nie an einen Dritten.

DWD-Warnungen sind bewusst noch nicht enthalten; sie folgen als eigener
Schritt, sobald die globale Vorhersage stabil läuft.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
import time
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query

DATEN = Path(os.environ.get("MSM_WETTER_DATEN", "/daten"))
GFS = DATEN / "gfs"
ORTE_DATEI = DATEN / "cities5000.txt"
PLZ_DB = DATEN / "plz.sqlite"
STATE = DATEN / "state.json"
CITIES_URL = "https://download.geonames.org/export/dump/cities5000.zip"
PLZ_ZIP_URL = "https://download.geonames.org/export/zip/postalCodes.zip"
NOMADS = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p50.pl"
VARIABLEN = (
    "var_TMP=on&var_RH=on&var_UGRD=on&var_VGRD=on&var_TCDC=on&var_PRATE=on"
    "&lev_2_m_above_ground=on&lev_10_m_above_ground=on&lev_surface=on&lev_entire_atmosphere=on"
)
FRISTEN = range(0, 49, 3)
KOPF = {"User-Agent": "MSM-Wetter-Sidecar/1.0 (self-hosted)"}
WARN_URL = (
    "https://maps.dwd.de/geoserver/dwd/ows?service=WFS&version=2.0.0"
    "&request=GetFeature&typeNames=dwd:Warnungen_Gemeinden_vereinigt"
    "&outputFormat=application/json&srsName=EPSG:4326"
)
_warnungen: list[dict[str, Any]] = []
_warn_lock = threading.Lock()

app = FastAPI(title="MSM Wetter-Sidecar", docs_url=None, redoc_url=None)
_state_lock = threading.Lock()
_orte: list[dict[str, Any]] | None = None
_orte_lock = threading.Lock()
_plz_lock = threading.Lock()


def _log(text: str) -> None:
    print(f"[wetter] {text}", flush=True)


# ---------------------------------------------------------------- Orte & PLZ
def _normal(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def _plz_db_init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plz (
            land TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            ascii_name TEXT NOT NULL,
            bundesland TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plz_code ON plz(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plz_ascii ON plz(ascii_name)")


def _plz_db_bereit() -> bool:
    return PLZ_DB.exists() and PLZ_DB.stat().st_size > 1024


def _plz_db_laden() -> None:
    """Lädt die weltweiten Postleitzahlen von GeoNames herunter und indiziert sie lokal in SQLite."""
    DATEN.mkdir(parents=True, exist_ok=True)
    if _plz_db_bereit() and (time.time() - PLZ_DB.stat().st_mtime <= 60 * 86400):
        return
    with _plz_lock:
        if _plz_db_bereit() and (time.time() - PLZ_DB.stat().st_mtime <= 60 * 86400):
            return
        tmp_db = DATEN / "plz.sqlite.tmp"
        if tmp_db.exists():
            tmp_db.unlink()
        try:
            _log(f"Lade weltweite Postleitzahlen von {PLZ_ZIP_URL} ...")
            with httpx.stream("GET", PLZ_ZIP_URL, timeout=120, follow_redirects=True, headers=KOPF) as antwort:
                antwort.raise_for_status()
                roh = b"".join(antwort.iter_bytes())
            with zipfile.ZipFile(io.BytesIO(roh)) as archiv:
                with archiv.open("postalCodes.txt") as datei:
                    conn = sqlite3.connect(tmp_db)
                    conn.execute("PRAGMA synchronous = OFF")
                    conn.execute("PRAGMA journal_mode = MEMORY")
                    _plz_db_init(conn)
                    stapel: list[tuple[str, str, str, str, str, float, float]] = []
                    for zeile_bytes in datei:
                        zeile = zeile_bytes.decode("utf-8", errors="replace").strip()
                        if not zeile:
                            continue
                        f = zeile.split("\t")
                        if len(f) < 11:
                            continue
                        land, code, name = f[0].strip(), f[1].strip(), f[2].strip()
                        bundesland = f[3].strip() if len(f) > 3 else ""
                        try:
                            lat = float(f[9])
                            lon = float(f[10])
                        except ValueError:
                            continue
                        if not code or not name:
                            continue
                        ascii_name = _normal(name)
                        stapel.append((land, code, name, ascii_name, bundesland, lat, lon))
                        if len(stapel) >= 10000:
                            conn.executemany(
                                "INSERT INTO plz (land, code, name, ascii_name, bundesland, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                stapel,
                            )
                            stapel.clear()
                    if stapel:
                        conn.executemany(
                            "INSERT INTO plz (land, code, name, ascii_name, bundesland, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            stapel,
                        )
                    conn.commit()
                    conn.close()
            tmp_db.replace(PLZ_DB)
            _log(f"Postleitzahlen-Datenbank bereit ({PLZ_DB.stat().st_size} Bytes)")
        except Exception as exc:  # noqa: BLE001
            _log(f"Postleitzahlen konnten nicht geladen werden: {exc}")
            if tmp_db.exists():
                tmp_db.unlink()


def plz_lader_starten() -> None:
    if not any(t.name == "plz-lader" for t in threading.enumerate()):
        threading.Thread(target=_plz_db_laden, name="plz-lader", daemon=True).start()


def _orte_laden() -> list[dict[str, Any]]:
    global _orte
    with _orte_lock:
        if _orte is not None:
            return _orte
        DATEN.mkdir(parents=True, exist_ok=True)
        if not ORTE_DATEI.exists() or time.time() - ORTE_DATEI.stat().st_mtime > 30 * 86400:
            try:
                with httpx.stream("GET", CITIES_URL, timeout=90, follow_redirects=True, headers=KOPF) as antwort:
                    antwort.raise_for_status()
                    roh = b"".join(antwort.iter_bytes())
                with zipfile.ZipFile(io.BytesIO(roh)) as archiv:
                    ORTE_DATEI.write_bytes(archiv.read("cities5000.txt"))
                _log(f"Ortsliste geladen ({ORTE_DATEI.stat().st_size} Bytes)")
            except Exception as exc:  # noqa: BLE001 - ohne Liste bleibt der Rest nutzbar
                _log(f"Ortsliste nicht ladbar: {exc}")
        orte: list[dict[str, Any]] = []
        if ORTE_DATEI.exists():
            for zeile in ORTE_DATEI.read_text(encoding="utf-8", errors="replace").splitlines():
                felder = zeile.split("\t")
                if len(felder) < 15 or felder[6] != "P":
                    continue
                try:
                    einwohner = int(felder[14] or 0)
                    lat, lon = float(felder[4]), float(felder[5])
                except ValueError:
                    continue
                name, ascii_name, land = felder[1], felder[2], felder[8]
                if not name:
                    continue
                orte.append({"name": name, "ascii": ascii_name, "land": land, "lat": lat, "lon": lon, "einwohner": einwohner})
        _orte = orte
        return orte


def orte_suchen(q: str) -> list[dict[str, Any]]:
    eingabe = q.strip()
    begriff = _normal(eingabe)
    if len(begriff) < 2:
        return []

    treffer: list[dict[str, Any]] = []
    gesehen: set[tuple[str, str, float, float]] = set()

    # 1. Lokale SQLite-PLZ-Suche (falls vorhanden)
    if _plz_db_bereit():
        try:
            conn = sqlite3.connect(f"file:{PLZ_DB}?mode=ro", uri=True)
            teile = eingabe.split()
            # Wenn mehrere Teile (z. B. "10115 Berlin" oder "Berlin 10115")
            if len(teile) >= 2 and any(t.isdigit() for t in teile):
                code_teil = next((t for t in teile if t.isdigit()), "")
                name_teil = _normal(" ".join(t for t in teile if not t.isdigit()))
                cur = conn.execute(
                    "SELECT code, name, land, lat, lon FROM plz WHERE code LIKE ? AND ascii_name LIKE ? LIMIT 20",
                    (f"{code_teil}%", f"{name_teil}%"),
                )
            elif any(c.isdigit() for c in eingabe):
                # Vorwiegend PLZ (z. B. 10115, 90210, 75001)
                cur = conn.execute(
                    "SELECT code, name, land, lat, lon FROM plz WHERE code LIKE ? LIMIT 20",
                    (f"{eingabe}%",),
                )
            else:
                # Ortsname in der PLZ-Tabelle
                cur = conn.execute(
                    "SELECT code, name, land, lat, lon FROM plz WHERE ascii_name LIKE ? ORDER BY length(name) LIMIT 20",
                    (f"{begriff}%",),
                )
            for code, name, land, lat, lon in cur.fetchall():
                anzeige_name = f"{code} {name}" if code and not name.startswith(code) else name
                schluessel = (land, anzeige_name, round(lat, 2), round(lon, 2))
                if schluessel not in gesehen:
                    gesehen.add(schluessel)
                    treffer.append({
                        "name": anzeige_name,
                        "land": land,
                        "lat": round(lat, 4),
                        "lon": round(lon, 4),
                    })
            conn.close()
        except Exception as exc:  # noqa: BLE001
            _log(f"Fehler bei PLZ-Suche: {exc}")

    # Wenn bereits genügend Treffer (z. B. bei gezielter PLZ-Eingabe), direkt liefern
    if len(treffer) >= 10:
        return treffer[:10]

    # 2. Ergänzende Ortssuche über GeoNames cities
    staedte_treffer = []
    for ort in _orte_laden():
        name = _normal(ort["name"])
        ascii_name = _normal(ort["ascii"])
        if not (name.startswith(begriff) or ascii_name.startswith(begriff)):
            continue
        staedte_treffer.append(ort)
        if len(staedte_treffer) >= 200:
            break
    staedte_treffer.sort(key=lambda o: o["einwohner"], reverse=True)

    for o in staedte_treffer:
        schluessel = (o["land"], o["name"], round(o["lat"], 2), round(o["lon"], 2))
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            treffer.append({
                "name": o["name"],
                "land": o["land"],
                "lat": round(o["lat"], 4),
                "lon": round(o["lon"], 4),
            })
            if len(treffer) >= 10:
                break

    return treffer[:10]


# ---------------------------------------------------------------- DWD-Warnungen
def _dwd_json(roh: bytes) -> Any:
    for kodierung in ("utf-8", "cp1252", "latin-1"):
        try:
            return json.loads(roh.decode(kodierung))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("DWD-Antwort nicht lesbar")


def _bbox(geom: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def sammeln(teil: Any) -> None:
        if isinstance(teil, (int, float)):
            return
        if len(teil) == 2 and isinstance(teil[0], (int, float)):
            xs.append(float(teil[0]))
            ys.append(float(teil[1]))
            return
        for stueck in teil:
            sammeln(stueck)

    sammeln(geom.get("coordinates", []))
    return (min(xs), min(ys), max(xs), max(ys)) if xs and ys else (0.0, 0.0, 0.0, 0.0)


def _in_ring(lon: float, lat: float, ring: list[Any]) -> bool:
    innen = False
    j = len(ring) - 1
    for i, punkt in enumerate(ring):
        xi, yi = punkt[0], punkt[1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            innen = not innen
        j = i
    return innen


def _in_polygon(lon: float, lat: float, ringe: list[Any]) -> bool:
    if not ringe or not _in_ring(lon, lat, ringe[0]):
        return False
    return not any(_in_ring(lon, lat, loch) for loch in ringe[1:])


def _in_geom(lon: float, lat: float, geom: dict[str, Any]) -> bool:
    typ = geom.get("type")
    if typ == "Polygon":
        return _in_polygon(lon, lat, geom.get("coordinates", []))
    if typ == "MultiPolygon":
        return any(_in_polygon(lon, lat, teil) for teil in geom.get("coordinates", []))
    return False


def _warnungen_laden() -> None:
    antwort = httpx.get(WARN_URL, timeout=60, headers=KOPF, follow_redirects=True)
    antwort.raise_for_status()
    daten = _dwd_json(antwort.content)
    neu: list[dict[str, Any]] = []
    for feature in daten.get("features", []):
        geom = feature.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        p = feature.get("properties") or {}
        neu.append(
            {
                "titel": p.get("HEADLINE") or p.get("EVENT") or "Wetterwarnung",
                "text": p.get("DESCRIPTION") or "",
                "stufe": p.get("SEVERITY") or "",
                "gruppe": p.get("EC_GROUP") or "",
                "beginn": p.get("ONSET"),
                "ende": p.get("EXPIRES"),
                "geom": geom,
                "bbox": _bbox(geom),
            }
        )
    global _warnungen
    with _warn_lock:
        _warnungen = neu
    _log(f"{len(neu)} DWD-Warnungen geladen")


def warnungen_fuer(lat: float, lon: float) -> list[dict[str, Any]]:
    with _warn_lock:
        liste = list(_warnungen)
    treffer = []
    for warnung in liste:
        x1, y1, x2, y2 = warnung["bbox"]
        if lon < x1 or lon > x2 or lat < y1 or lat > y2:
            continue
        if not _in_geom(lon, lat, warnung["geom"]):
            continue
        treffer.append({k: warnung[k] for k in ("titel", "text", "stufe", "gruppe", "beginn", "ende")})
    return treffer[:3]


def _warn_lader() -> None:
    while True:
        try:
            _warnungen_laden()
        except Exception as exc:  # noqa: BLE001 - alte Warnungen bleiben aktiv
            _log(f"DWD-Warnungen nicht ladbar: {exc}")
        time.sleep(600)


# ---------------------------------------------------------------- GFS-Lader
def _gfs_url(zyklus: datetime, stunde: int) -> str:
    tag, lauf = zyklus.strftime("%Y%m%d"), zyklus.strftime("%H")
    return (
        f"{NOMADS}?file=gfs.t{lauf}z.pgrb2full.0p50.f{stunde:03d}"
        f"&dir=%2Fgfs.{tag}%2F{lauf}%2Fatmos&{VARIABLEN}"
        "&subregion=&toplat=90&leftlon=0&rightlon=360&bottomlat=-90"
    )


def _kuerzlich(zyklus: datetime) -> datetime:
    jetzt = datetime.now(timezone.utc) - timedelta(hours=5)
    return jetzt.replace(minute=0, second=0, microsecond=0, hour=jetzt.hour - jetzt.hour % 6)


def _lade_datei(url: str, ziel: Path) -> None:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=180, follow_redirects=True, headers=KOPF) as antwort:
        antwort.raise_for_status()
        with ziel.open("wb") as datei:
            for stueck in antwort.iter_bytes(1 << 16):
                datei.write(stueck)
    if ziel.stat().st_size < 100_000:
        ziel.unlink(missing_ok=True)
        raise RuntimeError("Antwort ist zu klein (kein GRIB)")


def _zyklus_holen(zyklus: datetime) -> None:
    ziel = GFS / f"run-{zyklus.strftime('%Y%m%d%H')}"
    ziel.mkdir(parents=True, exist_ok=True)
    dateien = []
    for stunde in FRISTEN:
        pfad = ziel / f"f{stunde:03d}.grib2"
        if not pfad.exists() or pfad.stat().st_size < 100_000:
            _lade_datei(_gfs_url(zyklus, stunde), pfad)
        dateien.append({"stunde": stunde, "datei": pfad.name})
    zustand = {
        "zyklus": zyklus.isoformat(),
        "stand": datetime.now(timezone.utc).isoformat(),
        "ordner": ziel.name,
        "dateien": dateien,
        "quelle": "NOAA GFS 0.50°",
    }
    with _state_lock:
        (STATE.parent / "state.json.tmp").write_text(json.dumps(zustand), encoding="utf-8")
        (STATE.parent / "state.json.tmp").replace(STATE)
    _log(f"Modelllauf {zyklus.strftime('%d.%m. %H:%M UTC')} bereit")
    for alt in GFS.glob("run-*"):
        if alt.name != ziel.name:
            for datei in alt.glob("*"):
                datei.unlink(missing_ok=True)
            alt.rmdir()


def _lader() -> None:
    _orte_laden()
    while True:
        try:
            _orte_laden()
            kandidat = _kuerzlich(datetime.now(timezone.utc))
            if STATE.exists():
                try:
                    vorhanden = datetime.fromisoformat(json.loads(STATE.read_text(encoding="utf-8"))["zyklus"])
                    if vorhanden >= kandidat:
                        time.sleep(1800)
                        continue
                except (ValueError, KeyError, OSError):
                    pass
            for rueck in range(4):
                zyklus = kandidat - timedelta(hours=6 * rueck)
                try:
                    _zyklus_holen(zyklus)
                    break
                except Exception as exc:  # noqa: BLE001 - nächster Lauf
                    _log(f"Lauf {zyklus.isoformat()} nicht verfügbar: {exc}")
            else:
                _log("Kein Modelllauf erreichbar; alter Lauf bleibt aktiv")
        except Exception as exc:  # noqa: BLE001 - Lader nie sterben lassen
            _log(f"Lader: {exc}")
        time.sleep(1800)


def lader_starten() -> None:
    if not any(t.name == "wetter-lader" for t in threading.enumerate()):
        threading.Thread(target=_lader, name="wetter-lader", daemon=True).start()


# ---------------------------------------------------------------- GRIB lesen
def _wert(datei: Path, lat: float, lon: float, kategorie: int, nummer: int) -> float | None:
    import eccodes  # lokal: der Rest der Datei bleibt ohne eccodes importierbar

    with datei.open("rb") as roh:
        while True:
            gid = eccodes.codes_grib_new_from_file(roh)
            if gid is None:
                return None
            try:
                if eccodes.codes_get(gid, "parameterCategory") != kategorie:
                    continue
                if eccodes.codes_get(gid, "parameterNumber") != nummer:
                    continue
                ni = eccodes.codes_get(gid, "Ni")
                nj = eccodes.codes_get(gid, "Nj")
                lat1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                lat2 = eccodes.codes_get(gid, "latitudeOfLastGridPointInDegrees")
                lon1 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                werte = eccodes.codes_get_values(gid)
                if nj <= 1 or ni <= 1:
                    continue
                j = round((lat1 - lat) / (lat1 - lat2) * (nj - 1))
                i = round(((lon - lon1) % 360) / 360 * ni) % ni
                j = max(0, min(nj - 1, j))
                index = j * ni + i
                if 0 <= index < len(werte):
                    return float(werte[index])
            finally:
                eccodes.codes_release(gid)


def _lage(temp: float, regen: float, wolken: float) -> str:
    if regen >= 0.2 and temp <= 1.0:
        return "schnee"
    if regen >= 0.2:
        return "regen"
    if wolken >= 75:
        return "bewoelkt"
    if wolken >= 35:
        return "teils"
    return "klar"


def vorhersage(lat: float, lon: float) -> dict[str, Any]:
    if not STATE.exists():
        raise HTTPException(status_code=503, detail={"code": "WETTER_NICHT_BEREIT"})
    try:
        zustand = json.loads(STATE.read_text(encoding="utf-8"))
        zyklus = datetime.fromisoformat(zustand["zyklus"])
        ordner = GFS / zustand["ordner"]
        dateien = sorted(zustand["dateien"], key=lambda d: d["stunde"])
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(status_code=503, detail={"code": "WETTER_NICHT_BEREIT"}) from exc

    schritte: list[dict[str, Any]] = []
    for eintrag in dateien:
        pfad = ordner / eintrag["datei"]
        temp = _wert(pfad, lat, lon, 0, 0)
        if temp is None:
            continue
        temp -= 273.15
        regen = (_wert(pfad, lat, lon, 1, 7) or 0.0) * 3600.0
        wolken = _wert(pfad, lat, lon, 6, 1) or 0.0
        feuchte = _wert(pfad, lat, lon, 1, 1) or 0.0
        u = _wert(pfad, lat, lon, 2, 2) or 0.0
        v = _wert(pfad, lat, lon, 2, 3) or 0.0
        schritte.append({
            "stunde": eintrag["stunde"],
            "zeit": (zyklus + timedelta(hours=eintrag["stunde"])).isoformat(),
            "temp": round(temp, 1),
            "regen": round(max(0.0, regen), 2),
            "wolken": round(wolken),
            "feuchte": round(feuchte),
            "wind": round((u * u + v * v) ** 0.5, 1),
            "lage": _lage(temp, regen, wolken),
        })
    if not schritte:
        raise HTTPException(status_code=503, detail={"code": "WETTER_NICHT_BEREIT"})

    jetzt = schritte[0]
    naechste = [s for s in schritte if s["stunde"] > 0][:4]
    tage: dict[str, dict[str, Any]] = {}
    for schritt in schritte:
        tag = schritt["zeit"][:10]
        if tag not in tage:
            tage[tag] = {"datum": tag, "min": schritt["temp"], "max": schritt["temp"], "lage": schritt["lage"]}
        else:
            tage[tag]["min"] = min(tage[tag]["min"], schritt["temp"])
            tage[tag]["max"] = max(tage[tag]["max"], schritt["temp"])
            if schritt["regen"] > 0:
                tage[tag]["lage"] = schritt["lage"]
    return {
        "jetzt": jetzt,
        "stunden": naechste,
        "tage": list(tage.values())[:3],
        "warnungen": warnungen_fuer(lat, lon),
        "warnquelle": "Deutscher Wetterdienst",
        "quelle": zustand.get("quelle", "NOAA GFS"),
        "stand": zyklus.isoformat(),
    }


# ---------------------------------------------------------------- HTTP
@app.on_event("startup")
def starten() -> None:
    if "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ:
        return
    DATEN.mkdir(parents=True, exist_ok=True)
    lader_starten()
    plz_lader_starten()
    if not any(t.name == "dwd-warnungen" for t in threading.enumerate()):
        threading.Thread(target=_warn_lader, name="dwd-warnungen", daemon=True).start()


@app.get("/health")
def health() -> dict[str, Any]:
    bereit = STATE.exists()
    return {"ok": bereit, "bereit": bereit, "orte": len(_orte or []), "plz": _plz_db_bereit()}


@app.get("/orte")
def orte(q: str = Query(min_length=2, max_length=120)) -> dict[str, Any]:
    return {"orte": orte_suchen(q)}


@app.get("/wetter")
def wetter(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)) -> dict[str, Any]:
    return vorhersage(lat, lon)
