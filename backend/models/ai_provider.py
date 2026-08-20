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

    Seit Azure trägt die Tabelle mit `azure_resource_name` wieder **ein Stück**
    Adresse — ein DNS-Label, keine URL. Warum das nötig ist, was daran anders
    ist als die alte `base_url` und was an Restrisiko bleibt, steht bei der
    Spalte selbst.
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # Schlüssel aus `ai_provider_registry.ANBIETER`. Bestimmt Adresse und
    # Katalog; beides gehört damit nicht mehr in die Eingabe.
    provider_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="openrouter"
    )
    default_model: Mapped[str | None] = mapped_column(
        String(256), nullable=True, default=None
    )
    # Die Stimme, mit der der Sprachmodus vorliest — eine Kennung aus dem
    # ElevenLabs-Konto des Betreibers. Sie steht hier und nicht im Katalog, weil
    # sie keine Eigenschaft des Modells ist, sondern eine Wahl: jede Stimme kann
    # jedes Sprachmodell sprechen, und welche zum Panel passt, weiß nur er.
    #
    # Hier stand bis zum 2026-08-16 eine der acht OpenAI-Realtime-Stimmen. Die
    # Spalte ist geblieben und hat die Bedeutung gewechselt — deshalb ist sie
    # jetzt 64 Zeichen breit und wird beim Speichern auf ``[A-Za-z0-9_-]``
    # geprüft: der Wert geht in einen **URL-Pfad**
    # (``/v1/text-to-speech/{voice}/stream-input``), und was dort ungeprüft
    # landet, ist kein Schreibfehler mehr, sondern eine fremde Adresse.
    #
    # ``None`` heißt „nichts hinterlegt" und nicht „irgendeine": ohne Stimme
    # lehnt der Sprachmodus die Verbindung ab, statt eine zu raten. Eine
    # geratene Stimme wäre eine Entscheidung, die der Betreiber nie getroffen
    # hat — und sie stünde in seiner Abrechnung.
    default_voice: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Das Modell, das Gesprochenes zu Text macht. Nur an einem Chatzugang von
    # Bedeutung, und dort auch nur, wenn jemand den Sprachmodus benutzt.
    #
    # Eine **zweite** Modellspalte und keine Erweiterung von `default_model`:
    # die beiden beantworten verschiedene Fragen. `default_model` ist das
    # Modell, das denkt und Werkzeuge ruft; dieses hier hört nur zu. Sie
    # zusammenzulegen hieße, für jedes Gespräch das teure Modell die Audiodaten
    # lesen zu lassen — oder das billige denken.
    #
    # Es gibt bei OpenRouter keinen Transkriptions-Endpunkt (am 2026-08-16
    # nachgesehen); Audio geht als ``input_audio``-Teil in eine gewöhnliche
    # Chatanfrage. Hier gehört deshalb ein **hörfähiges Chatmodell** hinein und
    # nicht `whisper` oder `gpt-4o-transcribe` — die gibt es dort nicht.
    #
    # ``None`` heißt „nichts hinterlegt": dann gibt es keinen Sprachmodus über
    # diesen Zugang. Auch hier wird nie ein Standard hineingeschrieben.
    transcription_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Das Modell, mit dem die Worker dieses Zugangs arbeiten — die vierte
    # Funktion an derselben Zeile (docs/agentic-framework.md, Abschnitt 5):
    # `default_model` denkt im Gespräch (Gehirn), `transcription_model` hört,
    # `default_voice` spricht, dieses hier **arbeitet**. Eine eigene Spalte für
    # eine eigene Frage, aus demselben Grund wie beim Gehör: das Gehirn muss
    # schnell sein, ein Worker darf langsam und gründlich sein — dasselbe Feld
    # für beide hieße, für jede Begrüßung das gründliche Modell zu bezahlen
    # oder jeden Auftrag dem schnellen anzuvertrauen.
    #
    # ``None`` heißt „keine Worker-Rolle konfiguriert": dann gilt der heutige
    # Ein-Modell-Betrieb (ein Lauf, volles Werkzeugangebot, kein Gehirn/Worker-
    # Schnitt). Kein Hard-Stop, kein geratenes Modell.
    worker_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Die **feste** Denkstufe der Worker — ein Stufenwort aus
    # `ai_reasoning.RANGFOLGE`, beim Speichern geprüft. Anders als im Chat
    # wählt sie nicht der Kunde je Nachricht, sondern der Betreiber einmal am
    # Zugang: er zahlt die Arbeit und legt fest, wie gründlich sie ist. Der
    # Rollendeckel `max_reasoning_effort` klemmt weiterhin nur die **Wahl des
    # Kunden** im Gespräch; auf diese Betreiberentscheidung wird er bewusst
    # nicht angewandt. Zur Laufzeit wird das Wort trotzdem gegen den Katalog
    # des Worker-Modells geklemmt (nie teurer, nie unbekannt) — ein Stufenwort,
    # das das Modell nicht führt, darf keine Segmente mit 400 töten.
    #
    # ``None`` heißt „nicht nachdenken" — derselbe Standard wie heute bei
    # unbeaufsichtigten Läufen, keine geratene Tiefe.
    worker_reasoning_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Der Name der Azure-Ressource dieses Zugangs — das eine Stück Adresse, das
    # MSM nicht selbst weiß.
    #
    # **Das ist ausdrücklich nicht die Rückkehr der `base_url` von oben.** Dort
    # stand eine ganze Adresse: Schema, Host, Port, Pfad — alles Eingabe, und
    # damit alles Angriffsfläche. Hier steht ein einzelnes DNS-Label. Schema,
    # Suffix und Pfad bleiben als Vorlage in der Anbieterdatei
    # (``https://{ressource}.services.ai.azure.com/openai/v1``), und geprüft
    # wird das Label mit ``re.fullmatch`` gegen die Form eines Labels — kein
    # Punkt, kein Schrägstrich, kein Zeilenumbruch, nicht länger als 63 Zeichen
    # (`ai_provider_service._assert_ressource`).
    #
    # Ohne diese Spalte gäbe es Azure nicht: dort hat jede Ressource ihren
    # eigenen Host, und einen gemeinsamen Einstieg für alle Kunden gibt es
    # nicht. Was dabei an Restrisiko bleibt — Private Link kann einen gültigen
    # Namen innerhalb eines VNet auf eine private Adresse lenken —, steht bei
    # `ai_provider_registry.basis.Anbieter.ressource_noetig`.
    #
    # ``None`` heißt „nicht hinterlegt". Für jeden Anbieter ohne
    # ``ressource_noetig`` ist das der Normalfall und die Spalte bleibt
    # unbeachtet; für Azure lehnt der Service Anlegen und Aktivieren ohne
    # Namen ab, statt eine Zeile zu führen, die niemand benutzen kann.
    #
    # 64 Zeichen breit, obwohl 63 die Grenze ist: Azure vergibt Namen bis 64,
    # und eine Spalte, die knapper ist als das Erlaubte, verwandelte einen
    # erklärbaren Formfehler in einen Datenbankfehler.
    azure_resource_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
