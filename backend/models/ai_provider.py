"""AI-Provider-Konfiguration — vollstaendig in der Hand des Betreibers."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiProvider(Base):
    """Ein vom Betreiber eingerichteter Zugang zu einem unterstützten Anbieter.

    Ziel-Anbieter, Modell und Schlüssel legt der Betreiber fest; ein Benutzer
    wählt nur noch unter dem aus, was freigegeben ist. Es gab hier einmal einen
    zweiten Weg — jeder Benutzer durfte einen eigenen API-Key mitbringen, und
    `resolve_api_key` nahm ihn **vor** dem des Betreibers. Für ein Panel, das
    ein Hoster betreibt, ist das der falsche Weg herum: der Kunde zahlt für den
    Dienst, und ein eigener Schlüssel wäre ein zweiter Abrechnungspfad neben
    dem kalkulierten.

    **Die frei eintragbare Basis-URL ist entfallen** (`provider_kind` statt
    `base_url`). Sie war flexibel und teuer zugleich: MSM wusste nichts über
    das Ziel, brauchte deshalb eine SSRF-Prüfung mit IP-Pinning, und konnte
    über das Modell dahinter keine Aussage treffen — welche Denkstufen es
    kennt, ob es abschaltbar ist, ob es überhaupt nachdenkt. Genau das braucht
    ein Panel, das Denktiefe je Rolle begrenzen will.

    Mit einem Anbieter aus `ai_provider_registry` gehört die Adresse dem
    Programm, und mit ihr kommt der Modellkatalog. Was ein Modell kann, steht
    deshalb **nicht** in dieser Tabelle: es kommt bei Bedarf aus
    `ai_model_catalog` und wäre hier eine Kopie, die still veraltet.
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # Schlüssel aus `ai_provider_registry.ANBIETER`. Bestimmt Adresse und
    # Katalog; beides gehört damit nicht mehr in die Eingabe.
    provider_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="openrouter"
    )
    default_model: Mapped[str] = mapped_column(String(256), nullable=False)
    # Die Stimme, mit der ein Sprachzugang antwortet — eine aus
    # `ai_voice_session.STIMMEN`. Sie steht hier und nicht im Katalog, weil sie
    # keine Eigenschaft des Modells ist, sondern eine Wahl des Betreibers: alle
    # acht kann jedes Realtime-Modell, und welche davon zum Panel passt, weiß
    # nur er.
    #
    # ``None`` heißt **nicht** „alloy", sondern „nichts hinterlegt";
    # `ai_voice_session.STANDARDSTIMME` löst das bei jedem Verbinden neu auf.
    # Der Unterschied ist erst am Tag sichtbar, an dem MSM die Standardstimme
    # wechselt: ein eingetragenes „alloy" wäre dann eine Entscheidung, die der
    # Betreiber nie getroffen hat. Deshalb wird hier nie ein Standard
    # hineingeschrieben — auch nicht beim Anlegen.
    default_voice: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_api_key: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    operator_api_key_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    operator_api_key_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Vom Betreiber gepflegter Preis je eine Million Tokens, in
    # **US-Cent-Microunits** (1 Cent = 10.000). Frueher standen hier ganze Cent,
    # und daran scheiterte schon die Eingabe: „1,20 €" liess sich nicht
    # eintragen, weil zwischen 1 und 2 Cent nichts lag.
    #
    # Der Wert ist nur noch die **Rueckfallebene**. OpenRouter meldet in der
    # letzten Zeile jedes Streams den tatsaechlich belasteten Betrag; der wird
    # gebucht. Hierher greift die Abrechnung erst, wenn der Anbieter schweigt —
    # und markiert die Zeile dann als `cost_source='estimate'`, damit niemand
    # eine Schaetzung fuer eine Messung haelt. Ohne Wert bleiben die Kosten bei
    # null und das rollenbasierte Kostenlimit greift nicht; MSM raet keinen Preis.
    token_price_micro_usd_per_million: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
