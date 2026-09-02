# Pipecat-Integration für den MSM-Sprachmodus

Stand: 29. August 2026

Pipecat ist die interne Frame-Orchestrierung des Legacy-Sprachmodus. Die
Sicherheitsarchitektur bleibt MSM: AiRun, Run-Broker, Guardian, Tool-Registry,
RBAC, Proposal-Flow und DIS werden nicht ersetzt.

## Tatsächlicher Ausgangspunkt

Die Provideradapter sammeln Toolargumente stückweise und veröffentlichen bei
einem abgeschlossenen Tool-Call `tool_ready`. Die Engine kann danach die eng
begrenzte, erneut autorisierte Read-Allowlist schon während des restlichen
Providerstreams ausführen. Schreibende Werkzeuge bleiben außerhalb dieses
Pfads und benötigen weiterhin Proposal oder serverseitig freigegebene
Autonomie.

Voice und Chat teilen bereits AiRun und den Broker. Der Browservertrag bleibt
absichtlich getrennt: Voice verwendet WebSocket mit PCM und sicheren JSON-
Rahmen, Chat verwendet SSE.

## Umsetzung

- `pydantic==2.10.6` erfüllt die Pipecat-Voraussetzung, ohne FastAPI,
  pydantic-settings oder httpx anzuheben.
- `pipecat-ai==1.8.1` ist ohne Provider-Extras gepinnt.
- `services/ai_voice/pipecat_pipeline.py` ist die einzige Produktivstelle mit
  Pipecat-Imports. Sie nimmt den bereits akzeptierten WebSocket entgegen,
  erstellt Audio- und Steuerframes, ruft die vorhandene Pausenerkennung und
  Voice-Session auf und gibt sichere UI- sowie TTS-Ausgabeframes zurück.
- Die bestehende Bridge bleibt eine schmale Fassade für VAD, Transkription,
  AiRun, Broker, TTS und die bestehende Voice-Policy. Es gibt keinen zweiten
  Voice- oder Tool-Pfad und kein Laufzeit-Feature-Flag.

Provider-Schlüssel, Abschriften, Toolargumente und Rohresultate werden nicht
in Pipecat-Frames, WebSocket-Nutzlasten, Metriken oder Logs geschrieben.
`geo_analysis` und `web_results` bleiben Teil der bereinigten Toolprojektion.

## Unterbrechung und Fehler

Bei bestätigter Sprache unterbricht Pipecat nur die Audioausgabe. Die vorhandene
TTS-Sitzung wird geschlossen; der AiRun läuft weiter. `unterbrechen` hat
dieselbe Wirkung. Nur `abbrechen` oder `abort` invalidiert Prefetch und beendet
die serverseitig der Sitzung zugeordneten Runs.

Der vorhandene `voice_output_checkpoint` bleibt eine flüchtige Näherung für
bereits an TTS gegebenen Text. Eine exakte Hörposition würde Browser-
Wiedergabequittungen und einen neuen Wire-Vertrag verlangen und ist nicht Teil
dieser Änderung.

Fehlt Pipecat oder passt die Version nicht, startet das Panel weiterhin. Der
Sprachmodus meldet sich über sein bestehendes `available`-Feld als nicht
verfügbar und besitzt keinen Legacy-Fallback. Der Rückweg erfolgt durch das
vorherige Release-Artefakt; es gibt keine Datenbankmigration.

## Sicherheits- und Betriebsgrenzen

Pipecat-Runner, LiveKit-Serializer, Pipecat-Providerdienste, zusätzliche
Listener, Ports und HTTP-Endpunkte sind nicht Teil der Integration. Vor einem
Release werden die aufgelösten Python-3.12-/Linux-Abhängigkeiten, Lizenzen,
Wheels und aktuelle High-/Critical-Advisories erneut geprüft.

Die Datenflüsse zu Modell- und Sprachdienstleistern ändern sich im Legacy-Modus
nicht. Ein panelweit aktivierter OpenAI-Realtime-Zugang wählt stattdessen WebRTC
mit serverseitigem Sideband und ruft Pipecat, STT und ElevenLabs nicht auf.
Pipecat läuft ausschließlich im Panelprozess und speichert keine Aufnahmen. Der
abweichende direkte Audiofluss des Realtime-Modus ist in der Betriebsdoku und
der Datenschutzerklärung getrennt beschrieben.

## Abnahme

Die gezielten Voice-, Tool-, Provider-, Prefetch-, AiRun- und Redaction-Tests
prüfen weiterhin Authentifizierung, `ai.voice.use`, sichere Toolprojektion und
den Ausschluss spekulativer Writes. Neue Tests sichern die Pipecat-Importgrenze
und die Parität von PCM- und JSON-Rahmen ab.

Für Barge-in wird von `rede_nachgewiesen` gemessen, nicht vom akustischen
Sprachbeginn. Die Ausgabe muss im p95 innerhalb von 200 ms stoppen. Vor dem
Release folgen vollständige Backend- und Frontend-Suiten, Build, Browser- und
Desktop-Voice-Prüfung sowie ein Staging-Soak ohne Queue- oder Speicherwachstum.
