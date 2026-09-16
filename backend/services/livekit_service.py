"""LiveKit: Zugangstoken, Raumabfragen und die Betreiber-Umschaltung.

Die einzige Stelle im Backend, die LiveKit kennt. Alles andere fragt hier.

**Warum kein `livekit-api`.** Ein LiveKit-Zugangstoken ist ein gewoehnliches
HS256-JWT mit einem `video`-Anspruch; `python-jose` liegt seit dem ersten Tag im
Abhaengigkeitsbaum und signiert bereits jede Panel-Sitzung. Die Room-API spricht
Twirp ueber JSON, also gewoehnliches POST mit Bearer-Kopf, und dafuer gibt es
`httpx`. Das offizielle SDK wuerde `aiohttp`, `protobuf` und `livekit-protocol`
nachziehen — drei Pakete fuer die rund vierzig Zeilen, die hier stehen.
Entscheidung und Ausstiegsplan: docs/agent-rules/dependencies.md.

**Was ein Betreiber einstellen kann.** Standard ist der mitgelieferte Sidecar
auf 127.0.0.1:7880, den Caddy unter `/livekit` nach aussen gibt; Schluessel und
Geheimnis erzeugt `install.sh` einmalig. Wer stattdessen LiveKit Cloud oder
einen eigenen Server nutzt, traegt URL, Schluessel und Geheimnis im Panel ein.
Das Geheimnis liegt dann DIS-verschluesselt in den Panel-Einstellungen und
verlaesst den Prozess nie im Klartext.
"""

from __future__ import annotations

import ipaddress
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from jose import jwt
from sqlalchemy.orm import Session

from config import settings
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

Modus = Literal["lokal", "extern"]

SCHLUESSEL_MODUS = "livekit_modus"
SCHLUESSEL_URL = "livekit_url"
SCHLUESSEL_API_KEY = "livekit_api_key"
SCHLUESSEL_SECRET_ENC = "livekit_api_secret_encrypted"
SECRET_AAD = "msm:settings:livekit_api_secret"

# Eine Stunde. Kurz genug, dass ein abgegriffenes Token nicht ewig gilt, lang
# genug, dass ein Gespraech nicht mittendrin an einer abgelaufenen Signatur
# scheitert. Bei einem Reconnect holt der Client ohnehin ein frisches Token.
TOKEN_TTL_SEKUNDEN = 3600

# Der Sidecar hoert auf 127.0.0.1; Caddy reicht `/livekit/*` dorthin durch.
LOKALE_HTTP_URL = "http://127.0.0.1:7880"
LOKALER_PFAD = "/livekit"


class LivekitNichtEingerichtet(RuntimeError):
    """Weder lokaler Sidecar noch externe Zugangsdaten sind nutzbar."""


class LivekitZielAbgelehnt(ValueError):
    """Die eingetragene Adresse ist kein zulaessiges Ziel."""


@dataclass(frozen=True)
class LivekitKonfiguration:
    modus: Modus
    client_url: str
    """Was der Browser waehlt (wss://...)."""
    api_url: str
    """Was das Backend fuer die Room-API waehlt (https:// bzw. http://)."""
    api_key: str
    api_secret: str

    @property
    def konfiguriert(self) -> bool:
        return bool(self.client_url and self.api_key and self.api_secret)


def _oeffentliches_ziel(url: str) -> bool:
    """Verhindert SSRF: nur oeffentliche Ziele, kein loopback, kein privates Netz.

    Gleiche Politik wie `_is_safe_public_url` in `ai_web_search_service`. Sie
    gilt ausschliesslich fuer den Externmodus; der lokale Sidecar ist fest
    verdrahtet und geht diesen Weg nie.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("https", "wss"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def normalisiere_externe_url(url: str) -> str:
    """Prueft und vereinheitlicht eine vom Betreiber eingetragene Adresse.

    LiveKit Cloud gibt `wss://projekt.livekit.cloud` aus; manche tragen
    `https://` ein. Beides ist dieselbe Adresse, gespeichert wird `wss://`.
    """
    roh = (url or "").strip().rstrip("/")
    if not roh:
        raise LivekitZielAbgelehnt("Bitte eine LiveKit-Adresse eintragen.")
    if "://" not in roh:
        roh = f"wss://{roh}"
    if roh.startswith("https://"):
        roh = "wss://" + roh[len("https://") :]
    if not _oeffentliches_ziel(roh):
        raise LivekitZielAbgelehnt(
            "Nur oeffentlich erreichbare wss://- oder https://-Adressen sind zulaessig. "
            "Fuer einen Server im eigenen Netz bleibt der integrierte Modus."
        )
    return roh


def _api_url_aus_client_url(client_url: str) -> str:
    if client_url.startswith("wss://"):
        return "https://" + client_url[len("wss://") :]
    if client_url.startswith("ws://"):
        return "http://" + client_url[len("ws://") :]
    return client_url


def _lokale_client_url() -> str:
    """`wss://<panel-host>/livekit`, abgeleitet aus `panel_url`.

    Der Browser erreicht den Sidecar nur ueber Caddy. Ein Betreiber soll dafuer
    nichts eintragen muessen, deshalb kommt die Adresse aus der Domain, die bei
    der Installation ohnehin gesetzt wurde.
    """
    basis = (settings.livekit_url or "").strip().rstrip("/")
    if basis:
        return basis
    panel = (settings.panel_url or "").strip().rstrip("/")
    if not panel:
        return ""
    if panel.startswith("https://"):
        return "wss://" + panel[len("https://") :] + LOKALER_PFAD
    if panel.startswith("http://"):
        return "ws://" + panel[len("http://") :] + LOKALER_PFAD
    return ""


def modus(db: Session | None = None) -> Modus:
    gespeichert = PanelSettingsService.get(SCHLUESSEL_MODUS, "lokal", db).strip().lower()
    return "extern" if gespeichert == "extern" else "lokal"


def konfiguration(db: Session | None = None) -> LivekitKonfiguration:
    if modus(db) == "extern":
        client_url = PanelSettingsService.get(SCHLUESSEL_URL, "", db).strip()
        api_key = PanelSettingsService.get(SCHLUESSEL_API_KEY, "", db).strip()
        verschluesselt = PanelSettingsService.get(SCHLUESSEL_SECRET_ENC, "", db)
        api_secret = ""
        if verschluesselt:
            try:
                api_secret = AuthService.decrypt_secret(verschluesselt, aad=SECRET_AAD)
            except Exception:
                api_secret = ""
        return LivekitKonfiguration(
            modus="extern",
            client_url=client_url,
            api_url=_api_url_aus_client_url(client_url),
            api_key=api_key,
            api_secret=api_secret,
        )
    return LivekitKonfiguration(
        modus="lokal",
        client_url=_lokale_client_url(),
        api_url=LOKALE_HTTP_URL,
        api_key=(settings.livekit_api_key or "").strip(),
        api_secret=(settings.livekit_api_secret or "").strip(),
    )


def speichere_konfiguration(
    neuer_modus: Modus,
    url: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    db: Session | None = None,
) -> None:
    """Schreibt die Betreiberauswahl. Das Geheimnis geht verschluesselt hinein."""
    if neuer_modus == "lokal":
        PanelSettingsService.set(SCHLUESSEL_MODUS, "lokal", db)
        return

    normalisiert = normalisiere_externe_url(url or "")
    # Leer heisst bei Schluessel wie Geheimnis „unveraendert lassen". Die
    # Oberflaeche zeigt beide nur maskiert an und schickt sie nie zurueck; ohne
    # diese Regel wuerde der maskierte Platzhalter als echter Wert gespeichert.
    schluessel = (api_key or "").strip() or PanelSettingsService.get(
        SCHLUESSEL_API_KEY, "", db
    ).strip()
    if not schluessel:
        raise LivekitZielAbgelehnt("Ohne API-Key kann sich das Panel nicht anmelden.")

    PanelSettingsService.set(SCHLUESSEL_MODUS, "extern", db)
    PanelSettingsService.set(SCHLUESSEL_URL, normalisiert, db)
    PanelSettingsService.set(SCHLUESSEL_API_KEY, schluessel, db)
    if api_secret:
        PanelSettingsService.set(
            SCHLUESSEL_SECRET_ENC,
            AuthService.encrypt_secret(api_secret, aad=SECRET_AAD),
            db,
        )
    elif not PanelSettingsService.get(SCHLUESSEL_SECRET_ENC, "", db):
        raise LivekitZielAbgelehnt("Ohne API-Secret kann sich das Panel nicht anmelden.")


def csp_origin(db: Session | None = None) -> str:
    """Die Herkunft, die `connect-src` zusaetzlich erlauben muss.

    Im Lokalmodus leer: der Sidecar liegt hinter Caddy auf derselben Herkunft
    und ist von `'self'` bereits gedeckt.
    """
    if modus(db) != "extern":
        return ""
    client_url = PanelSettingsService.get(SCHLUESSEL_URL, "", db).strip()
    if not client_url:
        return ""
    parsed = urllib.parse.urlparse(client_url)
    if not parsed.hostname:
        return ""
    herkunft = f"{parsed.scheme}://{parsed.netloc}"
    https_variante = _api_url_aus_client_url(herkunft)
    return f"{herkunft} {https_variante}" if https_variante != herkunft else herkunft


# ── Zugangstoken ────────────────────────────────────────────────────────────


def zugangstoken(
    raum: str,
    identity: str,
    anzeigename: str,
    *,
    api_key: str,
    api_secret: str,
    ttl_sekunden: int = TOKEN_TTL_SEKUNDEN,
    darf_veroeffentlichen: bool = True,
) -> str:
    """Erzeugt ein LiveKit-Zugangstoken (HS256-JWT mit `video`-Anspruch)."""
    jetzt = int(time.time())
    anspruch: dict[str, Any] = {
        "iss": api_key,
        "sub": identity,
        "nbf": jetzt,
        "exp": jetzt + ttl_sekunden,
        "name": anzeigename,
        "video": {
            "room": raum,
            "roomJoin": True,
            "canPublish": darf_veroeffentlichen,
            "canSubscribe": True,
            "canPublishData": True,
            "canUpdateOwnMetadata": True,
        },
    }
    return jwt.encode(anspruch, api_secret, algorithm="HS256")


def _verwaltungstoken(api_key: str, api_secret: str, raum: str | None = None) -> str:
    """Token fuer die Room-API. Kein `roomJoin`, nur Lesen."""
    jetzt = int(time.time())
    video: dict[str, Any] = {"roomList": True}
    if raum:
        video["roomAdmin"] = True
        video["room"] = raum
    return jwt.encode(
        {
            "iss": api_key,
            "sub": "msm-panel",
            "nbf": jetzt,
            "exp": jetzt + 60,
            "video": video,
        },
        api_secret,
        algorithm="HS256",
    )


# ── Room-API (Twirp ueber JSON) ─────────────────────────────────────────────


def _twirp(
    api_url: str,
    api_key: str,
    api_secret: str,
    methode: str,
    rumpf: dict[str, Any],
    raum: str | None = None,
    zeitlimit: float = 5.0,
) -> dict[str, Any]:
    antwort = httpx.post(
        f"{api_url.rstrip('/')}/twirp/livekit.RoomService/{methode}",
        json=rumpf,
        headers={
            "Authorization": f"Bearer {_verwaltungstoken(api_key, api_secret, raum)}",
            "Content-Type": "application/json",
        },
        timeout=zeitlimit,
    )
    antwort.raise_for_status()
    return antwort.json() or {}


def verbindung_pruefen(
    adresse: str, api_key: str, api_secret: str
) -> tuple[bool, str, int]:
    """Prueft uebergebene Zugangsdaten, ohne sie zu speichern.

    `adresse` darf `wss://` oder `https://` sein, beides fuehrt zur selben
    Room-API. Rueckgabe: (erreichbar, Meldung, Anzahl aktiver Raeume). Der Test
    laeuft bewusst gegen die Eingabe und nicht gegen den gespeicherten Stand:
    ein falsches Geheimnis soll die Datenbank gar nicht erst erreichen.
    """
    if not adresse or not api_key or not api_secret:
        return False, "URL, API-Key und API-Secret werden alle drei gebraucht.", 0
    try:
        daten = _twirp(_api_url_aus_client_url(adresse), api_key, api_secret, "ListRooms", {})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return False, "API-Key oder API-Secret wurden abgelehnt.", 0
        return False, f"LiveKit antwortete mit HTTP {exc.response.status_code}.", 0
    except httpx.HTTPError:
        return False, "LiveKit ist unter dieser Adresse nicht erreichbar.", 0
    except Exception:
        return False, "Die Antwort von LiveKit war unlesbar.", 0
    return True, "Verbindung steht.", len(daten.get("rooms") or [])


def raum_teilnehmer(raum: str, db: Session | None = None) -> int:
    """Wie viele gerade in diesem Raum sind. Bei Fehlern 0, nie eine Ausnahme.

    Die Zahl schmueckt eine Einladungskarte. Wenn LiveKit klemmt, soll die
    Karte trotzdem erscheinen, nur eben ohne Live-Hinweis.
    """
    konf = konfiguration(db)
    if not konf.konfiguriert or not raum:
        return 0
    try:
        daten = _twirp(
            konf.api_url, konf.api_key, konf.api_secret, "ListParticipants", {"room": raum}, raum=raum
        )
    except Exception:
        return 0
    return len(daten.get("participants") or [])


def status(db: Session | None = None) -> dict[str, Any]:
    """Was der Admin-Bereich anzeigt. Enthaelt nie das Geheimnis."""
    konf = konfiguration(db)
    if not konf.konfiguriert:
        fehlend = (
            "Der integrierte LiveKit-Sidecar ist auf diesem Server nicht eingerichtet. "
            "Eine Neuinstallation legt ihn an; bis dahin hilft der externe Modus."
            if konf.modus == "lokal"
            else "Adresse, API-Key oder API-Secret fehlen."
        )
        return {
            "modus": konf.modus,
            "url": konf.client_url,
            "konfiguriert": False,
            "erreichbar": False,
            "fehler": fehlend,
            "api_key_maskiert": _maskiere(konf.api_key),
            "raeume_aktiv": 0,
        }
    # Gegen `api_url` und nicht gegen `client_url`: im lokalen Modus ist das
    # 127.0.0.1:7880 statt des Umwegs ueber Caddy, und der Status soll sagen, ob
    # LiveKit laeuft, nicht ob der Reverse Proxy durchreicht.
    erreichbar, meldung, raeume = verbindung_pruefen(
        konf.api_url, konf.api_key, konf.api_secret
    )
    return {
        "modus": konf.modus,
        "url": konf.client_url,
        "konfiguriert": True,
        "erreichbar": erreichbar,
        "fehler": None if erreichbar else meldung,
        "api_key_maskiert": _maskiere(konf.api_key),
        "raeume_aktiv": raeume,
    }


def _maskiere(wert: str) -> str:
    if not wert:
        return ""
    if len(wert) <= 8:
        return "*" * len(wert)
    return "*" * (len(wert) - 4) + wert[-4:]
