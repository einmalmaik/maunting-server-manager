"""Claude auf Azure — dieselbe Ressource, ein anderer Dialekt.

Anthropic-Modelle laufen seit dem 18.11.2025 in Microsoft Foundry (vormals
Azure AI Foundry), seit dem 29.06.2026 allgemein verfügbar. Sie sprechen dort
**nicht** OpenAIs Chat-Completions, sondern Anthropics eigene Messages-API —
belegt von beiden Seiten wörtlich:

* Microsoft Learn: „Your target URI from your deployment details, which is of
  the form ``https://<resource name>.services.ai.azure.com/anthropic/v1/messages``."
* Anthropic: „Both methods use Azure-hosted endpoints in the format
  ``https://{resource}.services.ai.azure.com/anthropic/v1/*``."

Die **Adresse des Anbieters** endet dabei vor der Version — Azure zeigt sie im
Portal als ``https://<resource>.services.ai.azure.com/anthropic`` an, und genau
diese Form nimmt auch Anthropics SDK als ``base_url``. ``/v1/messages`` gehört
zur Operation und steht deshalb im Adapter, nicht hier.

**Warum ein eigener ``kind`` und keine Weiche im Adapter.** Der vorgelegte Plan
wollte Claude im `openai_compatible_adapter` an den Anthropic-Weg abzweigen,
sobald das Modell nach Claude aussieht. Das geht aus zwei Gründen nicht: der
Deployment-Name ist frei gewählt (``prod-chat`` kann Claude sein, ``claude-x``
muss es nicht), und der Adapter darf keinen einzelnen Anbieter beim Namen
kennen — sonst wächst mit jedem Eintrag eine Verzweigung in einer Datei, die
niemandem gehört (siehe `basis.Anbieter.anfrage_erweiterungen`). Die Weiche
steht deshalb dort, wo schon die für OpenAIs Responses-API steht: an
`protokoll_chat`.

Dass dieselbe Azure-Ressource GPT- und Claude-Deployments führen kann, ist
dabei kein Widerspruch — der Betreiber legt zwei Zugänge an, einen je Dialekt,
mit demselben Ressourcennamen und demselben Schlüssel. Das ist eine Zeile mehr
in seiner Liste und dafür kein Ratespiel im Sendepfad.

**Kein Katalog**, und hier ist es sogar belegt statt nur ungewiss: Anthropic
nennt die Models API ausdrücklich unter dem, was Foundry nicht unterstützt —
„Requests that use these features against a deployment hosted on Azure return a
``400 Bad Request`` error by design". Die Fähigkeiten holt `ai_model_catalog`
deshalb über ``faehigkeiten_aus`` beim Nachschlagen des einzelnen Modells:
heisst das Deployment ``claude-sonnet-5``, findet es OpenRouters
``anthropic/claude-sonnet-5`` und damit Fenster und Denkstufen. Sonst bleibt
beides unbekannt.

**Nachdenken heisst hier nicht ``budget_tokens``.** Der vorgelegte Plan sah
``thinking: {"type": "enabled", "budget_tokens": …}`` vor; das ist die alte
Form. Microsofts Modelltabelle führt für ``claude-opus-5``, ``claude-sonnet-5``,
``claude-fable-5``, ``claude-opus-4-8`` und ``claude-opus-4-7`` in der Spalte
``enabled`` ein **„No"** — dort wird eine Anfrage mit ``budget_tokens``
abgelehnt. Aktuell ist ``thinking: {"type": "adaptive"}`` plus
``output_config: {"effort": …}``, und dessen Wortschatz (``low`` … ``max``) ist
wortgleich mit `ai_reasoning.RANGFOLGE`. Damit braucht es keine Umrechnung von
einem Stufenwort in eine Tokenzahl — also auch keine Zahlentabelle im Code,
gegen die diese Registry gebaut ist. Gesendet wird das in
`anthropic_messages_adapter`.
"""

from __future__ import annotations

from services.ai_provider_registry.basis import Anbieter, Modell


ANBIETER = Anbieter(
    kind="azure_anthropic",
    label="Azure · Anthropic Claude",
    # Dieselbe Vorlage wie bei `azure_openai`, anderer Pfad. Derselbe
    # Ressourcenname des Betreibers passt auf beide.
    #
    # **Ohne ``/v1``**, anders als bei `azure_openai`. Das ist kein Versehen und
    # keine Unsauberkeit, sondern der Schnitt, den Anthropic selbst zieht: die
    # Version gehört zum Pfad der Operation, nicht zur Adresse des Anbieters.
    # Azure zeigt im Portal genau diese Form (``…/anthropic``), Anthropics SDK
    # nimmt sie als ``base_url``, und `anthropic_messages_adapter` hängt
    # ``/v1/messages`` an. Ein ``/v1`` hier ergäbe ``…/anthropic/v1/v1/messages``.
    base_url="https://{ressource}.services.ai.azure.com/anthropic",
    # Belegt kein Katalog: die Models API ist auf Foundry nicht unterstützt.
    catalog_url=None,
    key_url="https://ai.azure.com/",
    key_prefix=None,
    ressource_noetig=True,
    # ``api-key`` — so im cURL-Beispiel der Anthropic-Doku. Microsofts eigenes
    # Beispiel nimmt ``x-api-key``; Anthropic nennt beide ausdrücklich
    # („Use either the ``api-key`` or ``x-api-key`` header"). ``Authorization:
    # Bearer`` ist hier **nicht** gleichwertig: dieser Kopf trägt bei Claude auf
    # Azure ein Entra-ID-Token, nicht den Ressourcenschlüssel.
    schluessel_kopf="api-key",
    schluessel_praefix="",
    faehigkeiten_aus="openrouter",
    faehigkeiten_praefix="anthropic/",
    # Leer, und das ist kein Versehen: ``anfrage_erweiterungen`` beschreibt den
    # Wortschatz von ``/chat/completions``. Dieser Zugang spricht ihn gar nicht
    # — was er zum Nachdenken sendet, entscheidet `anthropic_messages_adapter`
    # aus den Parametern der gemeinsamen Signatur, genau wie es der
    # Responses-Adapter für OpenAI tut.
    anfrage_erweiterungen=frozenset(),
    protokoll_chat="anthropic_messages",
    # Claude hört nicht zu; einen Transkriptionsendpunkt gibt es unter
    # ``/anthropic`` nicht.
    gehoer_wege=(),
    # Keine Empfehlung, aus demselben Grund wie bei `azure_openai`: die Kennung
    # ist ein Deployment-Name aus dem Konto des Betreibers.
    empfehlung=None,
)


def katalog_lesen(rohdaten: dict) -> Modell | None:
    """Wird nie gerufen — die Models API gibt es auf Foundry nicht.

    Steht hier aus demselben Grund wie ihr Gegenstück in `azure_openai`: der
    Controller sammelt Eintrag und Leser aus derselben Quelle, damit es einen
    Anbieter ohne Leser gar nicht geben kann.
    """
    del rohdaten
    return None
