# MSM v4.0 — technischer Implementierungsplan

Stand: 2026-08-01  
Quelle: `docs/ai-engine-planning.md` (verbindliches Produkt-Zielbild)

## Leitplanken

- Bestehende Self-Hosted-Flows bleiben kompatibel.
- Panel, KI und externe Integrationen verwenden dieselben autorisierten
  Backend-Services. Es entstehen keine parallelen Lifecycle-Pfade.
- Die KI darf nur typisierte, erlaubte MSM-Aktionen aufrufen. Freie Shell-,
  Python- oder Host-Befehle sind kein Bestandteil der KI-Schnittstelle.
- Secrets werden ausschließlich über DIS verschlüsselt und niemals in
  Antworten, Audit-Details oder Logs ausgegeben.
- Destruktive Aktionen benötigen weiterhin eine explizite menschliche
  Bestätigung. Ein autonomer Modus darf diese Grenze nicht aufheben.
- Jeder Abschnitt wird als kleiner vertikaler Schnitt mit Migration,
  Positiv-/Negativtests und Runtime-Prüfung ausgeliefert.

## Phase 1 — Identität und Autorisierung

1. Mehrere globale Rollen pro Benutzer einführen. Die bestehende `role_id`
   bleibt während der Migration als kompatible Primärrolle erhalten.
2. Effektive Rechte als Vereinigungsmenge aller Rollen auswerten; Owner- und
   serverbezogene Delegationsregeln bleiben unverändert.
3. AI-spezifische Rechte und Limits ergänzen. Limits werden im Backend
   durchgesetzt; die höchste erlaubte Grenze gewinnt, unbegrenzt gewinnt über
   begrenzt und eine Anfrage wird genau einmal gezählt.
4. Audit-Einträge um Auslöser (`direct`, `ai`, `external`, `system`) und eine
   Korrelations-ID erweitern, ohne Chat-, Log- oder Secret-Inhalte zu speichern.

## Phase 2 — Gemeinsame Aufgaben- und Provisionierungslogik

1. Die bestehende Servererstellung aus dem Router in einen kleinen,
   transaktionalen Provisionierungsservice verschieben.
2. Panel, spätere AI-Tools und externe API rufen denselben Service mit einem
   expliziten Actor-Kontext auf.
3. Persistente, idempotente Aufgaben mit Status, Wiederholungskennung und
   nachvollziehbaren Fehlerklassen einführen.
4. Node-Ausführung bleibt über die vorhandenen Node-/Guardian-Services
   isoliert; zentrale RBAC-Prüfungen werden nicht an Worker delegiert.

### Phase-2-Vertrag

- `POST /api/servers` sowie Start, Stop, Restart und Kill akzeptieren optional
  `Idempotency-Key`. Derselbe Benutzer, Task-Typ, Key und Request-Fingerprint
  liefern denselben Vorgang; ein abweichender Request mit demselben Key endet
  mit `409 idempotency_key_conflict`.
- Ein expliziter Retry verwendet einen neuen `Idempotency-Key` und verweist mit
  `X-Task-Retry-Of` auf den fehlgeschlagenen Vorgang. Erfolgreiche oder inhaltlich
  abweichende Vorgänge sind nicht retrybar.
- `GET /api/tasks` und `GET /api/tasks/{id}` liefern secret-freie Metadaten.
  Benutzer sehen eigene Tasks; `system.audit.read` erlaubt die panelweite Sicht.
- Idempotency-Keys und Requests werden ausschließlich als SHA-256-Fingerprints
  gespeichert. Einmalige PostgreSQL-Credentials werden bei einem Replay nicht
  rekonstruiert und erscheinen nie in Task oder Audit.
- Die Ziel-Node wird während Kapazitätsprüfung, Portvergabe und gemeinsamer
  Persistierung von Server und Ports in PostgreSQL gesperrt. Remote-Ausführung
  bleibt hinter den bestehenden TLS-/Token-gesicherten Node-Services.
- Offene Tasks werden beim Neustart abgeglichen. Nicht nachweisbar fortsetzbare
  Worker werden mit einem stabilen Fehlercode beendet; es erfolgt kein blindes
  Wiederholen externer Seiteneffekte.

## Phase 3 — AI Provider und sicherer Chat

1. Provider-Konfiguration für OpenAI-kompatible Endpunkte aufbauen. OpenRouter,
   direkte Provider und lokale Modelle werden über denselben kleinen Adapter
   angebunden; keine Provider-SDK-Dependency ist erforderlich.
2. Panel- und Benutzer-API-Keys mit DIS und kontextgebundener AAD speichern.
   Read-Endpunkte liefern ausschließlich `configured` und eine nicht
   reversible Maske.
3. Persistente globale und serverbezogene Gespräche, Nachrichten und
   Streaming-Antworten einführen. Serverseitige Ownership- und RBAC-Prüfung
   erfolgt bei jedem Zugriff.
4. Kontext wird vor Provider-Aufrufen minimiert, redigiert und begrenzt.
   Große Logs oder vollständige Konfigurationen werden nicht ungeprüft
   übertragen.

### Phase-3-Vertrag

- Provider werden ausschließlich von `panel.settings.read/write` verwaltet.
  Öffentliche Ziele benötigen HTTPS; private und Loopback-Ziele müssen vom
  Betreiber explizit freigegeben werden. Link-local, reservierte, multicast-
  und unspezifizierte Adressen bleiben gesperrt. DNS und Zielpolicy werden bei
  Konfiguration und unmittelbar vor jedem Request erneut geprüft; Redirects
  sind deaktiviert.
- Operator-Keys verwenden die DIS-AAD
  `msm:ai:provider:{provider_id}:operator-key`, persönliche BYOK-Credentials
  `msm:ai:provider:{provider_id}:user:{user_id}:api-key`. Ein persönlicher Key
  hat Vorrang. Ein Entschlüsselungsfehler fällt niemals still auf einen anderen
  Key zurück.
- Normale Nutzer erhalten weder Provider-URL noch Key-Maske des Operators.
  Read-Pfade entschlüsseln keine Secrets. Providerframes, Response-Bodies und
  URLs werden weder an den Browser gegeben noch geloggt.
- `ai.chat.use` schützt globale und serverbezogene Unterhaltungen. Ownership
  und bei Server-Chats zusätzlich `server.view` werden bei jedem Zugriff und
  nochmals direkt vor dem Provider-Aufruf geprüft. Fremde oder nachträglich
  unberechtigte Chats antworten einheitlich mit `404`.
- Antworten laufen als authentifizierter `POST`-SSE-Stream mit CSRF-Schutz.
  Die Request-UUID ist persistent eindeutig: abgeschlossene Antworten können
  replayt werden, aktive, fehlgeschlagene oder widersprüchliche Wiederverwendung
  endet ohne zweiten Provider-Aufruf.
- Provideraufrufe laufen ohne lang gehaltene Datenbanktransaktion. Nachrichten
  und Kontingentreservierung werden vorher atomar persistiert und danach in
  einer kurzen Transaktion finalisiert. Partielle oder nach Prozessabbruch
  unklare Nutzung wird konservativ bis zur Reservierung angerechnet.
- Modellkontext enthält ausschließlich begrenzte Chat-Historie und eine feste
  Allowlist unkritischer Serverfelder. Erkannte Secrets werden redigiert;
  Netzwerkadressen, interne Pfade, vollständige Konfigurationen, Logs und
  Credentials werden nicht übertragen. Die sichtbare Datenschutzerklärung
  nennt Speicherung, externen Transfer, BYOK und Verbrauchsmetadaten.
- Der Adapter basiert auf der bereits vorhandenen `httpx`-Dependency. Es wurde
  kein Provider-SDK und keine weitere Dependency aufgenommen. Monetäre Kosten
  bleiben ohne belastbare Preisquelle bei null; die UI verspricht keine
  erfundene Kostengenauigkeit.

## Phase 4 — Typisierte AI-Tools und Bestätigungen

1. Read-only-Tools für Serverstatus, Kapazität und begrenzte Logdiagnose.
2. Vorschau-/Diff-Tools für Konfigurationsänderungen mit Versionsprüfung gegen
   konkurrierende Änderungen.
3. Ausführung ausschließlich nach erneuter Backend-Autorisierung und, falls
   erforderlich, bestätigtem Aktions-Token.
4. Snapshot vor schreibenden Änderungen, Server-Mutex und korreliertes Audit.

### Phase-4-Vertrag

- Nur eine feste Tool-Allowlist wird an Provider übergeben. Sie enthält
  minimierte Status-/Kapazitätsabfragen, auf 200 Zeilen und 24.000 Zeichen
  begrenzte redigierte Logs, erlaubte Text-Configs sowie Vorschläge für
  Start/Stop/Restart, Backup und revisionsgebundene Config-Änderungen. Freie
  Shell-, Python-, Host-, Delete-, Wipe-, Restore- oder Reinstall-Tools sind
  nicht registriert.
- Read-Tools prüfen unmittelbar vor dem Zugriff erneut aktiven Benutzer,
  Conversation-Ownership, `server.view` und bei Configs zusätzlich
  `server.files.read`. Provider erhalten keine Node-Namen/-Hosts, internen
  Pfade oder Credentials. Log- und Config-Ausgaben werden redigiert und hart
  begrenzt.
- Write-Tool-Calls führen nichts aus. Sie erzeugen eine persistente Proposal
  mit ausschließlich minimierter Vorschau. Der eigentliche Payload wird mit
  der DIS-AAD `msm:ai:action-proposal:v1:{proposal_id}` verschlüsselt; Config-
  Inhalte erscheinen weder in API-Antworten noch Audit-Details.
- Die Benutzeroberfläche verlangt eine sichtbare manuelle Bestätigung. Erst
  danach stellt das Backend einen fünf Minuten gültigen Einmal-Token aus,
  speichert nur dessen SHA-256-Hash und erwartet den Klartext-Token direkt im
  anschließenden Execute-Request. Der Token wird nach erfolgreicher Prüfung
  atomar verbraucht und nicht im Frontend-Zustand persistiert.
- Confirm und Execute prüfen Ownership, aktiven Benutzer und RBAC erneut.
  Config-Vorschläge sind an eine SHA-256-Revision gebunden; vor dem atomaren
  Schreiben wird der bisherige Inhalt im vorhandenen verschlüsselten
  Versionsspeicher gesichert. Config und Backup verwenden den gemeinsamen
  nicht blockierenden Server-Lifecycle-Mutex; konkurrierende Vorgänge enden
  sicher mit Konflikt statt parallel zu schreiben.
- Lifecycle-Aktionen laufen über den gemeinsamen idempotenten
  `server_action_service` mit Actor-Origin `ai`. Backups verwenden den
  bestehenden Orchestrator, Configs den mit dem File-Manager geteilten
  revisionssicheren Service. Proposal, Bestätigung und Ergebnis erhalten
  dieselbe Korrelations-ID im secret-freien Audit.
- Beim Prozessstart werden Proposal-Zustände `executing` ohne automatische
  Wiederholung als `AI_ACTION_INTERRUPTED` beendet. Damit werden unbekannte
  externe Seiteneffekte nie blind wiederholt.

## Phase 5 — Memory, Skills und Anhänge

### Memory-Vertrag

- Memory ist explizit einsehbar, editierbar, löschbar und pro Benutzer
  abschaltbar. Die Scopes sind `user`, `server` und `panel`; Server-Memory ist
  immer zusätzlich an den Eigentümer gebunden, Panel-Memory darf nur mit
  `panel.settings.write` verändert werden.
- Inhalte liegen ausschließlich DIS-verschlüsselt mit objektgebundener AAD in
  der Datenbank. Secret-ähnliche Werte werden vor dem Schreiben abgewiesen;
  Schlüssel, Länge und Eintragszahl sind hart begrenzt. Ein Opt-out entfernt
  Memory vollständig aus neu aufgebautem Providerkontext.
- Providerkontext kennzeichnet Memory als unvertrauenswürdige Präferenzdaten
  und begrenzt die insgesamt übertragenen Zeichen. Löschregeln für Benutzer
  und Server entfernen die zugehörigen privaten Einträge.

### Skill-Vertrag

- Skills sind unveränderliche Versionen aus einer festen Allowlist typisierter
  MSM-Tools. Freie Skripte, Shell-Kommandos und unbekannte Argumente sind nicht
  zulässig. Nutzbar ist ausschließlich die aktuellste aktivierte Version;
  bekannte UUIDs älterer Versionen können nicht wiederholt ausgeführt werden.
- Leseschritte durchlaufen dieselben aktuellen RBAC- und Datenminimierungs-
  grenzen wie der Chat. Schreibschritte erzeugen ausschließlich Phase-4-
  Proposals und bleiben ohne manuelle Bestätigung wirkungslos.
- Wiederverwendbare Config-Schreibschritte sind bewusst ausgeschlossen, weil
  ein statischer Skill keine aktuelle Dateirevision sicher festhalten kann.

### Attachment-Vertrag

- Anhänge werden ohne Klartext-Dateipfad als verschlüsselte, konversations- und
  benutzergebundene Datensätze gespeichert. Erlaubt sind begrenzte UTF-8-
  Text-/Konfigurationsformate sowie strukturell geprüfte PNG- und JPEG-Bilder;
  Archive, Programme, PDFs, Pfadtraversal, Nullbytes, Secret-ähnliche Texte und
  übergroße Bilddimensionen werden vor der Speicherung abgewiesen.
- Pro Unterhaltung sind höchstens zehn aktive Anhänge zulässig. Zum Provider
  gelangen höchstens fünf berechtigte Anhänge; Text wird zusätzlich über alle
  Anhänge auf 12.000 Zeichen begrenzt und immer als unvertrauenswürdig
  markiert. Verlust der Attachment-Berechtigung entfernt sie sofort aus dem
  neu aufgebauten Kontext.
- Es gibt keine Archivübernahme und keine neue Parser-Abhängigkeit. Dadurch
  kann der AI-Upload weder Mods noch Serverdateien direkt installieren oder
  überschreiben.

## Phase 6 — Hoster-Integration

1. Externe Identitäten anhand `(integration_id, external_subject)` zuordnen;
   E-Mail allein verknüpft keine Accounts.
2. Idempotente Desired-State-API auf die gemeinsame Provisionierungs- und
   Lifecycle-Logik abbilden.
3. Kurzlebige Einmal-Handoffs mit gehashtem Token, erlaubter interner Route und
   atomarem Verbrauch einführen. Tokenwerte erscheinen weder in Audit noch
   Logs.
4. Signierte, wiederholbare Webhooks mit Zustellstatus und Secret-Redaktion.

## Phase 7 — Credentials, Kubernetes und Betrieb

1. Panel-, Benutzer- und Server-Credentials trennen; Klartext wird nach dem
   Speichern nicht erneut ausgegeben. Hoster-Credentials fallen nur nach
   expliziter Policy auf Panel-Credentials zurück.
2. Kubernetes-Manifeste und Betriebsabläufe erst auf Basis der stabilen
   Aufgaben-, Node- und Secret-Verträge liefern.
3. Installation, Updates, Migration, Rollback und Datenschutzdokumentation
   synchron mit `docs/self-hosting.md`, Panel-Doku, README und Tests halten.

## Abnahmekriterien je Schnitt

- Eingaben sind begrenzt und validiert; erwartete Fehler sind typisiert und
  nutzerverständlich, ohne interne Details offenzulegen.
- Positive, negative, leere und extreme Eingaben sind getestet.
- Keine Secrets in Logs, URLs, Toasts, Fixtures, Audit oder Diffs.
- Gezielt betroffene Tests und die vollständigen Backend-/Frontend-Testläufe
  sind erfolgreich.
- Betroffene Routen rendern zur Laufzeit und die Browser-Konsole bleibt sauber.
- Neue Dependencies sind entweder vermieden oder nach den Projektregeln
  dokumentiert.
