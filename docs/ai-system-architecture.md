# MSM AI-System: technische Architektur und Datenflüsse

Stand: 28. August 2026  
Grundlage: Quellcode im MSM-Repository. Dieses Dokument beschreibt den Ist-Zustand, nicht nur die gewünschte Zielarchitektur. Es enthält weder Zugangsdaten noch Inhalte konkreter Nutzer, Erinnerungen oder Server.

## Kurzantworten

### Hat MSM ein eigenes Agentic Framework?

Ja. MSM verwendet kein externes Agenten-Framework wie LangGraph. Die eigene Ausführungsbasis besteht aus:

- persistierten Läufen in `AiRun`;
- einem Live-Vermittler `ai_run_broker` für Ereignisse und Wiederanbindung;
- dem modularen Paket `services/ai_stream` für Laufstart, Kontext, Streaming, Lese- und Schreibphasen;
- dem zentralen Werkzeugkatalog `ai_tool_registry`;
- serverseitigen Ausführern in `ai_action_service`;
- Vorschlägen, Bestätigungen, Autonomie, Guardian- und Worker-Flows.

Der Begriff „Agentic Framework“ ist daher berechtigt: Es gibt Rollen, Zustände, Werkzeugkataloge, Laufgrenzen, Folge-Runden, persistierte Wartezustände, Hintergrund-Worker und einen Ereignisstrom. Die Ausführung bleibt aber eine MSM-eigene Orchestrierung, keine Frame-Pipeline wie Pipecat.

Die Datei `docs/agentic-framework.md` beschreibt das Gehirn-/Worker-Modell. Sie trägt selbst den Status „Konzept, nicht umgesetzt“. Der Code implementiert bereits Teile davon, insbesondere die Rollen `voll`, `gehirn` und `worker`; der vollständige Anspruch des Konzeptdokuments darf jedoch nicht automatisch als vollständig ausgeliefert gelten.

### Wie ruft die KI Werkzeuge auf?

Das Modell erhält eine feste, serverseitig erzeugte OpenAI-kompatible Function-Calling-Liste. Es kann weder Shell-Befehle noch beliebige URLs oder Python-Funktionen aufrufen. Es gibt nur benannte Werkzeuge mit JSON-Schema. Der Ablauf lautet:

1. Das Modell streamt einen `tool_call` mit Name und JSON-Argumenten.
2. Der Provider-Adapter sammelt die Argumentteile und validiert sie am Ende der Provider-Runde als JSON-Objekt.
3. Der Streaming-Engine prüft Namen, Rolle, Herkunft, Rundenlimit und den Werkzeugtyp.
4. Der Read- oder Write-Pfad führt das Werkzeug serverseitig aus bzw. erzeugt einen Vorschlag.
5. Das Ergebnis wird redigiert, als Tool-Ergebnis an die nächste Modellrunde übergeben und als Live-Ereignis an Chat/Voice publiziert.

Die tatsächliche Berechtigung wird nicht durch den Modellprompt und nicht im Frontend entschieden. Sie wird beim konkreten Werkzeug im Backend geprüft und bei Prefetch-Cache-Hits erneut geprüft.

### Was ist „Realtime“ in MSM?

MSM besitzt zwei Echtzeit-Ränder, die denselben AI-Run nutzen:

- Chat: HTTP-Endpunkt startet einen Run und liefert Server-Sent Events (SSE).
- Voice: WebSocket überträgt PCM-Audio und Statusrahmen; das Backend transkribiert, startet denselben Run und gibt Audio-/Text-/Werkzeugereignisse über den Socket zurück.

Der gemeinsame Kern ist nicht der HTTP- oder WebSocket-Transport, sondern `AiRun` plus `ai_run_broker`. Deshalb kann ein Browser-Stream getrennt werden, ohne dass der Run zwangsläufig abbricht. Ein späterer Abonnent erhält zuerst einen Snapshot und anschließend die Live-Fortsetzung.

## Systemübersicht

```text
                           ┌───────────────────────────────────────────┐
                           │                Frontend                   │
                           │ Chat SSE · Voice WebSocket · Globus/UI    │
                           └──────────────┬───────────────┬────────────┘
                                          │               │
                           Chat-Nachricht │               │ PCM / Steuerrahmen
                                          ▼               ▼
                  ┌───────────────────────┐   ┌────────────────────────┐
                  │ routers/ai_chat.py    │   │ routers/ai_voice.py    │
                  └───────────┬───────────┘   └───────────┬────────────┘
                              │                           │
                              │                    VAD → STT
                              └──────────┬────────────────┘
                                         ▼
                          ┌────────────────────────────┐
                          │ AiRun + ai_stream launcher │
                          │ Kontext · Modell · Zustand │
                          └──────────────┬─────────────┘
                                         ▼
                ┌──────────────────────────────────────────┐
                │ ai_stream.engine.segment_ausfuehren       │
                │ Providerstream · Tool-Runden · Finalize   │
                └───────┬───────────────────┬──────────────┘
                        │                   │
                        ▼                   ▼
            ┌──────────────────┐  ┌─────────────────────────┐
            │ ai_run_broker    │  │ ai_tool_registry        │
            │ Snapshot + Events│  │ Kategorien + Rechte     │
            └───────┬──────────┘  └───────────┬─────────────┘
                    │                         ▼
                    │              ┌─────────────────────────┐
                    │              │ ai_action_service       │
                    │              │ Read-Tools / Vorschläge │
                    │              └─────┬─────────┬─────────┘
                    │                    │         │
                    ▼                    ▼         ▼
             Chat-SSE / Voice-TTS   Geo/Web/...  Server/Calendar/Mail/...
```

## 1. Eingang und Identität

### Chat

`backend/routers/ai_chat.py` nimmt eine Nachricht entgegen, authentifiziert den Benutzer über die normale Session, prüft die Chat-Berechtigung und wählt einen zulässigen Provider. Der Router legt nicht selbst die Fachlogik fest. Er:

1. redigiert die Nachricht (`redact_sensitive_text`),
2. ermittelt Denkstufe und Kontextfenster,
3. übergibt nur IDs und freigegebene Eingabewerte an `lauf_beginnen_nebenher`,
4. eröffnet und abonniert den Broker vor dem Start des Runs,
5. liefert den Broker als SSE-Stream zurück.

Das Abonnieren vor `lauf_starten` schließt ein Rennen: Der erste Text-Delta kann nicht verloren gehen, weil der Kanal bereits existiert.

### Voice

`backend/routers/ai_voice.py` schützt den WebSocket mit Origin-Prüfung, Session-Authentifizierung und Berechtigung. Der Socket liegt bewusst unter `/api`, damit die sichere Cookie-/Desktop-Authentifizierung greift und kein Token in einer URL nötig ist.

`backend/services/ai_voice_bridge.py` ist die Übersetzungsschicht:

```text
Browser-Mikrofon (PCM16)
  → Pausenerkennung (`ai_voice_vad`)
  → Transkription (`ai_stt`)
  → derselbe AiRun wie im Chat
  → Broker-Ereignisse
  → Satzpuffer / ElevenLabs-TTS
  → Browser-Lautsprecher und Bildschirmtext
```

Die Voice-Brücke entscheidet nicht über Rechte oder Aktionen. Sie übersetzt Ein- und Ausgabe. Sie kann Beleg-Codeblöcke auf dem Bildschirm anzeigen, statt sie vorzulesen, und kann eine gesprochene Zustimmung an den bestehenden Vorschlagsfluss weitergeben.

### Teiltranskripte und Intent-Prefetch

Der Browser kann zusätzlich `teil_transkript`-Rahmen senden. `Sprachbruecke._verarbeite_teil_transkript` ruft den lokalen `StreamingIntentClassifier` auf. Nach mindestens drei Wörtern vergleicht er lokale Embeddings gegen mehrsprachige Intent-Prototypen. Das Warmup läuft getrennt vom Chunk-Pfad; die Klassifikation soll nicht auf Netzwerk warten.

Aktuell ist die spekulative Allowlist bewusst klein:

- `analyze_region`
- `web_search`
- `calendar_read`
- `read_server_status`
- `search_memory`

Nur ein Intent mit ausreichender Konfidenz und vollständigen Argumenten startet einen Prefetch. Der Cache ist pro Voice-Sitzung und Benutzer getrennt, hat zehn Sekunden TTL und wird bei Intent-/Entitätswechsel invalidiert. Ein späterer normaler Tool-Aufruf prüft Berechtigungen erneut, bevor ein Treffer zurückgegeben wird.

Wichtig: Diese spekulative Schicht ist ein Zusatzpfad. Sie ist nicht die zentrale Tool-Orchestrierung aller Werkzeuge.

## 2. Laufmodell: AiRun, Zustand und Broker

### Persistierter Lauf

`AiRun` repräsentiert eine konkrete Bearbeitung einer Nachricht. Beim Start entstehen:

- die persistierte Nutzernachricht;
- eine zunächst leere Assistant-Nachricht mit Status `streaming`;
- eine Nutzungsreservierung für Tokens/Kosten;
- ein Laufzustand (`state_json`) mit z. B. Provider-Nachrichten, Rolle, Herkunft, Gerätefamilie, Runden- und Toolsignaturen.

Der Zustand enthält zeitweise Kontext, der nicht dauerhaft liegen bleiben soll. `ai_run_service.arbeitsspeicher_leeren` entfernt Arbeitsgedächtnis an den vorgesehenen Endpunkten. Die persistierten Nachrichten und Tool-Resultate bleiben der prüfbare Verlauf.

### Broker

`backend/services/ai_run_broker.py` ist der prozesslokale Fan-out für Live-Ereignisse. Ein Run publiziert; Chat und Voice abonnieren. Der Broker hält einen begrenzten Snapshot mit geordneten Abschnitten:

- `delta`: sichtbarer Antworttext;
- `reasoning`: optionaler Denktext;
- `tool`: ein Werkzeugstatus/-ergebnis;
- `proposal` oder `action`: Vorschlag bzw. autonome Aktion;
- `question`: Rückfrage;
- `run`: Laufstatus.

Die Abschnitte bewahren die Reihenfolge von Text und Tool-Ereignissen. Bei einem Reconnect sendet `lauf_verfolgen` zuerst einen Snapshot und dann neue Ereignisse. Der Broker ist bewusst prozesslokal; er ist kein verteilter Eventbus und nicht prozessübergreifend wiederherstellbar.

### Lifecycle

`ai_run_service` plant die asyncio-Aufgabe, hält Nebenläufigkeitsplätze, kann wartende Läufe wecken und räumt abgebrochene Prozessläufe auf. Ein neuer Run derselben Unterhaltung löst den Vorgänger kontrolliert ab; das verhindert, dass ein alter Lauf später noch in eine inzwischen andere Unterhaltung schreibt.

## 3. Kontext und Modellrollen

### Kontextaufbau

`services/ai_stream/launcher.py` erzeugt den Provider-Kontext über `ai_context_service.build_provider_messages`. Darin liegen abhängig von Rolle und Berechtigung unter anderem:

- Systemprompt aus `ai_prompt.py`;
- gekürzte Gesprächshistorie;
- aktivierte persönliche bzw. zulässige Erinnerungen;
- situative Lageinformationen;
- gegebenenfalls Skill-Index und Serverbezug;
- der für diese Rolle gefilterte Werkzeugkatalog.

Der Kontext hat ein Budget. Vor jeder Provider-Runde verkürzt die Engine ältere Inhalte, bevor ein Provider wegen Kontextüberschreitung ablehnt. Werkzeugergebnisse werden dabei strukturerhaltend gekürzt, damit Tool-Call und Tool-Resultat weiter zueinander passen.

### Rollen

`ai_stream.context._rolle_ableiten` kennt drei Betriebsrollen:

| Rolle | Wann | Werkzeugraum |
|---|---|---|
| `voll` | Standard oder kein konfiguriertes Worker-Modell | herkömmlicher vollständiger, berechtigungsgefilterter Katalog |
| `gehirn` | primärer Chat, Worker-Modell konfiguriert, Recht `ai.background.use` vorhanden | Erinnerungen, Worker-Steuerung und eng begrenzte Desktop-Funktionen |
| `worker` | Unterhaltung vom Typ Worker | Arbeitswerkzeuge, aber kein Start weiterer Worker |

Guardian- und Aufgabenläufe erhalten zusätzlich engere Werkzeugmengen. Die Rolle wird im Run-Zustand eingefroren. Eine Modellantwort kann ihre Rolle, Herkunft oder Gerätefamilie nicht über Tool-Argumente erweitern.

### Erinnerungen

`ai_memory_service.py` trennt persönliche, serverbezogene, geteilte Server-, Team- und Panel-Scopes. Die Scope-Kennung wird zentral aus Benutzer, Server und Team gebildet. Erinnerungen sind standardmäßig deaktiviert. Gespeicherte Inhalte und Embeddings werden verschlüsselt bzw. über die dafür vorgesehenen Schutzpfade behandelt; AAD bindet Einträge an ihren Scope, sodass ein Datenbank-Umhängen sie nicht lesbar macht.

Preferences sollen die Auswahl und Priorisierung beeinflussen. Sie dürfen Sicherheits-, Wetter- oder andere allgemein wichtige Informationen nicht durch eine bloße Modellbehauptung unterdrücken.

## 4. Provider-Streaming

### Provider-Auswahl

`ai_provider_service.py` und `ai_provider_registry/` kapseln Provider-Konfiguration, Modellwahl, verschlüsselte Operator-Schlüssel und zulässige Fähigkeiten. Der Sprachmodus wählt getrennt:

- einen STT-fähigen Zugang;
- einen Chat-/LLM-fähigen Zugang;
- einen TTS-fähigen Zugang mit konfigurierter Stimme.

Provider-Schlüssel werden serverseitig aufgelöst. Sie werden weder an das Frontend geliefert noch in URLs abgelegt.

### Streaming-Adapter

`openai_compatible_adapter.stream_chat_completion` liest einen OpenAI-kompatiblen SSE-Stream. Es liefert interne `StreamChunk`-Objekte für:

- Antworttext (`content`);
- optionalen Denktext (`reasoning`);
- den erkannten Tool-Namen (`tool_start`).

Tool-Argumente werden fragmentweise gesammelt. Erst nach dem `[DONE]`-Rahmen werden sie als JSON geparst und als `ProviderToolCall` in `StreamUsage.tool_calls` abgelegt. Anthropic und OpenAI-Responses besitzen entsprechende Adapter, damit die Engine ein einheitliches internes Format erhält.

### Was heute tatsächlich parallel läuft

Während ein Modell Text streamt, veröffentlicht die Engine jeden Text-Delta sofort im Broker. Die Voice-Brücke gibt vollständige Sätze ohne Warten auf die Gesamtantwort an TTS weiter. Read-Tools derselben Tool-Runde werden anschließend mit begrenzter Nebenläufigkeit parallel ausgeführt.

Das ist echtes Text-Streaming und echte Parallelität zwischen mehreren Read-Tools. Es ist noch keine vollständige „Tool während der laufenden Modellrunde starten“-Architektur: Die Engine beginnt die konkrete Tool-Ausführung erst, nachdem die Provider-Runde beendet ist und alle Tool-Argumente vollständig vorliegen. `tool_start` ist heute daher ein frühes UI-Ereignis, kein Beweis, dass das Tool bereits läuft.

## 5. Werkzeugkatalog und Ausführung

### Der Katalog ist die zentrale Wahrheit

`backend/services/ai_tool_registry.py` beschreibt für jedes Werkzeug mindestens seine Art:

- `server_read` oder `global_read`;
- `server_write` oder `global_write`;
- weitere Gruppen für Rückfragen, Delegation, Guardian, Aufgaben oder Desktop.

Zusätzlich enthält der Katalog Angebotsrechte. Aus ihm entstehen `READ_TOOLS`, `WRITE_TOOLS`, Rollen- und Herkunftsschnitte. `ai_action_service.provider_tool_definitions()` baut daraus die Funktionsschemas, die an das Modell gehen.

Das Modell bekommt damit nur Werkzeuge, die grundsätzlich passend erscheinen. Das ist Führung, nicht die Sicherheitsentscheidung: jede Ausführung prüft erneut.

### Read-Tools

Nach einer Provider-Runde führt `ai_stream.read_tools._tool_followup_messages` zulässige Read-Calls aus. Die Funktion:

1. prüft Rolle, Herkunft, Guardian-/Aufgabenbindung und Unterhaltung erneut;
2. begrenzt die parallele Breite passend zur Datenbank;
3. führt einzelne Calls über `asyncio.to_thread` aus;
4. begrenzt jeden Call durch eine Zeitgrenze;
5. publiziert ein Tool-Ereignis, sobald genau dieser Call fertig ist;
6. erzeugt Tool-Resultate für die nächste Modellrunde.

`ai_action_service.execute_read_tool` ist der Dispatcher. Er prüft, dass der Name ein Read-Tool ist, löst gegebenenfalls den Server auf, prüft Rechte und delegiert an den passenden Ausführer. Beispiele:

- Serverstatus, Logs, Dateien und Backups;
- persönliche erlaubte Erinnerungen;
- Dokumentation und Skills;
- Websuche;
- Kalender-Lesezugriffe;
- `analyze_region` für Geo, Wetter, Satellit, regionale Signale.

Ein Read-Tool kann intern selbst mehrere Datenquellen kombinieren. `ai_geo_service.analyze_region` geocodiert beispielsweise WGS84-Koordinaten, lädt Wetterdaten und verbindet sie mit Sentinel-/regionalen Daten. Diese Fachparallelität gehört in den Geo-Service, nicht in das Frontend.

### Write-Tools

Schreibende und destruktive Werkzeuge laufen nicht durch den spekulativen Prefetch. Die Engine trennt sie von Read-Runden. `ai_stream.write_tools._schreibrunde_ausfuehren` erstellt Vorschläge oder führt nur dann autonom aus, wenn die serverseitige Autonomie-Policy dies erlaubt.

Vor einer tatsächlichen Aktion greifen, je nach Tool und Konfiguration:

- Toolname-/Argumentsvalidierung;
- RBAC und serverbezogene Rechte;
- Guardian-/Aufgabenbegrenzungen;
- Autonomie-Policy;
- Bestätigungskarte oder gesprochene Zustimmung;
- optionaler Ethics-Advisor;
- Audit- und Ergebnisprotokoll.

E-Mail-Versand ist kein spekulativer Vorgang. Der Outbox-Fluss meldet eingereiht, gesendet oder fehlgeschlagen statt einen Erfolg vorwegzunehmen.

### Wie Tools „untereinander“ funktionieren

Tools rufen sich nicht frei gegenseitig auf. Sie sind Einheiten hinter dem zentralen Dispatcher. Die Orchestrierung entsteht in der Streaming-Engine:

```text
Modell fordert z. B. list_my_servers und read_server_status an
  → Engine prüft beide Calls
  → Read-Pfad startet sie innerhalb der erlaubten Breite parallel
  → jedes Resultat wird einzeln an UI/Broker gemeldet
  → die komplette, geordnete Ergebnismenge geht als Tool-Nachrichten
    an die nächste Modellrunde
  → Modell formuliert daraus die Antwort oder fordert weitere Tools an
```

Abhängige Aktionen bleiben sequenziell: Eine Konfigurationsänderung soll nicht parallel zu einem vorher nötigen Backup laufen. Diese Reihenfolge ist eine Sicherheitszusage und kein Optimierungsfehler.

## 6. Geo-, Satelliten- und regionale Daten

`ai_geo_service.py` ist die Fachfassade der Regionsanalyse. Sie arbeitet mit WGS84-Koordinaten und Bounding-Boxes. Die primären Schritte sind Geocoding, Wetter und Regionalanalyse; sie setzt langlebige, begrenzte HTTP-Verbindungen, TLS und kurze Caches ein.

`ai_satellite_service.py` kapselt Copernicus Data Space Ecosystem / Sentinel. Zugangsdaten liegen verschlüsselt in den Panel-Einstellungen; Token und STAC-Suchen haben In-Memory-Caches und Single-Flight-Schutz gegen parallele identische Kaltstarts. Externe Metadaten werden vor der Modellübergabe redigiert.

`ai_regional_connectors_service.py` bündelt ergänzende regionale Quellen, etwa Nachrichten, öffentliche soziale Signale oder Verkehr, soweit ein Anbieter konfiguriert bzw. öffentlich verfügbar ist. Ein leerer Bereich bedeutet nicht automatisch „normal und stabil“, sondern kann auch fehlende Quelle, fehlende Regionabdeckung oder einen Fehler bedeuten. Die UI sollte diese Zustände getrennt darstellen.

Die Karten-/Globusdarstellung ist Frontend-Logik. Geo-Daten aus Tool- und Voice-Events müssen über die bestehenden WebSocket-/SSE-Nutzlasten ankommen; sonst kann der Sprachmodus die korrekten Satelliten- und Rechercheergebnisse nicht rendern.

## 7. Voice-Ausgabe und Unterbrechung

Die Voice-Brücke erhält Broker-Ereignisse und verarbeitet sie so:

- Text-Deltas werden durch `Belegfilter` geführt.
- Fertige Sätze gehen unmittelbar an die offene TTS-Sitzung.
- Code-/Logbelege gehen als Bildschirmereignis heraus und werden nicht vorgelesen.
- Tool-Start und Tool-Resultate werden als UI-Ereignisse weitergereicht.
- Vorschläge werden gesprochen und warten dann auf dieselbe serverseitige Bestätigung wie im Chat.

Bei Barge-In schließt die aktuelle TTS-Ausgabe. Ein expliziter `abbrechen`-/`abort`-Rahmen ist von einer bloßen Sprachunterbrechung getrennt. Der letzte an die Stimme gegebene Text wird nur flüchtig für den nächsten Voice-Zug verwendet, damit bereits gesprochene Inhalte nicht automatisch wiederholt werden.

Der Frontend-Hook `frontend/src/components/ai/voice/useSprachsitzung.ts` verwaltet WebSocket, Mikrofon und Wiedergabe, aber keine Berechtigungslogik. Er reagiert auf Status, Audio, Text, Tool-, Vorschlags-, Intent- und Geo-Ereignisse.

## 8. Sicherheitsgrenzen

| Grenze | Durchsetzung |
|---|---|
| Identität | Authentifizierte HTTP-/WebSocket-Sitzung, Origin-Prüfung im Voice-Rand |
| Werkzeugmenge | zentraler Registry-Katalog, Rollen-, Herkunfts-, Guardian- und Aufgabenschnitt |
| Rechte | Backend-Prüfung beim konkreten Server/Tool, nicht nur im Prompt oder UI |
| Schreibaktionen | Vorschlag/Bestätigung oder serverseitig aktivierte Autonomie-Policy |
| Provider-Schlüssel | verschlüsselt gespeichert, serverseitig via DIS-/Auth-Pfad aufgelöst |
| Satelliten-Credentials | verschlüsselt mit festem AAD, nur serverseitig genutzt |
| Externe Ergebnisse | Größenlimits und Redaction vor Kontext/Anzeige |
| Prefetch | Read-only-Allowlist, Sitzungsbindung, TTL, erneute Rechteprüfung |
| Desktop-Aktionen | Herkunft und Gerätefamilie kommen aus der authentifizierten Sitzung, nicht aus Modellargumenten |
| Abbruch | Statusänderung und Task-Abbruch; keine freie Nachausführung eines alten Runs |

## 9. Latenzmessung und Caches

`ai_latency_metrics.py` sammelt im Prozess aggregierte Dauerwerte. Relevante Punkte sind unter anderem Provider-Vorbereitung, erster Provider-Chunk, Tool-Start, Tool-Laufzeit und Prefetch-Hit/Miss. Es werden keine Transkripte, Suchbegriffe, Orte, Empfänger, Ergebnisse oder Schlüssel als Messwert gespeichert.

Leistungsrelevante Caches sind absichtlich unterschiedlich geschnitten:

- Provider-/HTTP-Verbindungen: pro Service wiederverwendet und begrenzt;
- Geo: kurzer Geocoding-Cache plus In-Flight-Deduplizierung;
- Sentinel: Token-Cache, Single-Flight und kurzer STAC-Cache;
- Web-/Regionalsignale: fachbezogene Kurzzeitcaches;
- Voice-Prefetch: zehn Sekunden, nur Sitzung plus Benutzer plus exakte Argumente;
- Erinnerung: persistiert und zugriffsgeschützt, keine globale Ergebnisweitergabe.

## 10. Bekannte Architekturgrenzen

Diese Punkte sind wichtig, damit „vorhanden“ nicht mit „Ziel erreicht“ verwechselt wird.

1. **Keine Pipecat-Schicht:** Voice nutzt heute eine eigene Bridge aus VAD, STT, AiRun-Broker und TTS. Es gibt keine einheitliche Frame-Pipeline für Audio, Text, Tool-Argumente und UI.
2. **Tools starten nicht beim vollständigen Argument-JSON:** Der Adapter sammelt Tool-Argumente während des Streams, aber die Engine dispatcht die Ausführung erst nach dem Ende der Provider-Runde. Das verhindert die gewünschte Überlappung aus natürlicher Erstreaktion, laufendem Tool und eintreffenden Daten.
3. **Chat und Voice teilen den Run, aber nicht einen vollständigen Interaktionsvertrag:** Beide sehen Broker-Ereignisse, doch Kamera-, Fortschritts-, TTS- und Tool-Ready-Ereignisse sind nicht als ein gemeinsames Frame-Protokoll modelliert.
4. **STT ist pro abgeschlossener Äußerung:** VAD trennt erst eine Äußerung, danach erfolgt die Transkription. Die Teiltranskript-Schnittstelle versorgt Classifier/UI, ersetzt aber keine vollständig streamingfähige STT-Pipeline.
5. **Intent-Prefetch ist bewusst begrenzt:** Nur fünf sichere Read-Tools sind zugelassen. Das ist sicher, aber keine generische Beschleunigung aller Tools.
6. **Natürliche Informationsdosierung ist vor allem Prompt-Verhalten:** Der Sprachprompt fordert kurze, direkte Antworten. Es gibt noch keinen technischen Antwortplan, der Nachrichten, Wetter, Verkehr, Social-Signale und Sehenswürdigkeiten als priorisierte Datenereignisse einzeln in die Sprechreihenfolge einsortiert.
7. **Konzept und Code können auseinanderlaufen:** `docs/agentic-framework.md` enthält Architekturziele, die über den heute nachweisbaren Code hinausgehen. Bei einer Refactor-Entscheidung ist Code plus Tests die operative Wahrheit.

## 11. Zielbild für den nächsten Realtime-Refactor

Der nächste Umbau sollte nicht Tool-Registry, Guardian, DIS oder Aktionsexekutoren ersetzen. Diese Schichten sind die Sicherheitsbasis. Er sollte den Transport und die Zeitsteuerung vereinheitlichen:

```text
Input/Audio/Chat
  → einheitliche Interaktionsereignisse
  → Intent-/Geo-Prefetch (nur sichere Reads)
  → LLM-Stream mit inkrementellen Tool-Argumenten
  → Tool-ready-Dispatcher mit zentralen Sicherheitsprüfungen
  → Ergebnisse als Ereignisse an UI, TTS und nächste Modellrunde
  → persistierter AiRun bleibt Audit- und Wiederanbindungsschicht
```

Pipecat kann den Voice-Rand dieser Architektur übernehmen: Audioframes, VAD/STT, TTS, Satzpuffer, Playback und Interrupts. Die bestehende MSM-Agentenlogik sollte als klar abgegrenzter Processor dahinter bleiben. Für Chat braucht es denselben Ereignisvertrag, auch wenn Chat selbst kein Audio transportiert.

Vor einer Migration müssen die bestehenden Sicherheitsinvarianten als Tests festgehalten werden: keine Tool-Ausführung ohne Backend-Rechte, keine spekulativen Writes, keine Provider-Schlüssel im Client, keine freie Desktop-Herkunft, keine alte Run-Ausführung nach kontrollierter Ablösung und keine unbestätigte destruktive Aktion.

## Quellindex

| Bereich | Primäre Quelldateien |
|---|---|
| Chat-Eingang/SSE | `backend/routers/ai_chat.py`, `backend/services/ai_run_broker.py` |
| Voice/WebSocket | `backend/routers/ai_voice.py`, `backend/services/ai_voice_bridge.py`, `backend/services/ai_voice_vad.py`, `backend/services/ai_stt.py`, `backend/services/ai_tts.py`, `backend/services/ai_tts_elevenlabs.py` |
| Run-Start und Lebenszyklus | `backend/services/ai_stream/launcher.py`, `backend/services/ai_run_service.py`, `backend/services/ai_stream/lifecycle.py` |
| Modellstream | `backend/services/ai_stream/engine.py`, `backend/services/openai_compatible_adapter.py`, `backend/services/openai_responses_adapter.py`, `backend/services/anthropic_messages_adapter.py` |
| Tools | `backend/services/ai_tool_registry.py`, `backend/services/ai_action_service.py`, `backend/services/ai_stream/read_tools.py`, `backend/services/ai_stream/write_tools.py` |
| Kontext/Rollen | `backend/services/ai_context_service.py`, `backend/services/ai_stream/context.py`, `backend/services/ai_prompt.py` |
| Erinnerungen | `backend/services/ai_memory_service.py` |
| Intent und Prefetch | `backend/services/ai_intent_classifier.py` |
| Geo/Satellit/Region | `backend/services/ai_geo_service.py`, `backend/services/ai_satellite_service.py`, `backend/services/ai_regional_connectors_service.py` |
| Frontend Voice | `frontend/src/components/ai/voice/useSprachsitzung.ts` |
| Konzeptstand | `docs/agentic-framework.md`, `docs/ai-engine-planning.md` |
