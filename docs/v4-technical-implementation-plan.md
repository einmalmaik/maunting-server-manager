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

### Phase-6-Vertrag

- Eine Integration handelt ausschließlich im Namen eines vom Betreiber
  benannten Panel-Dienstbenutzers. Dieser muss aktiv sein und `servers.create`
  besitzen; der Owner-Account ist ausgeschlossen. Verliert der Dienstbenutzer
  sein Recht, scheitert auch der Shop-Aufruf. Es gibt keinen namenlosen
  Provisionierungspfad an RBAC vorbei.
- `PUT /api/hoster/v1/services/{external_service_id}` setzt den gewünschten
  Zustand (`active`, `suspended`, `terminated`). `(integration_id,
  external_service_id)` ist ein Unique-Constraint; ein wiederholter Aufruf
  führt denselben Vertrag weiter und erzeugt keinen zweiten Server. Die
  Provisionierung läuft über `server_provisioning_service.provision_server` mit
  einem an den Vertrag gebundenen Idempotency-Key, Start/Stop über
  `server_action_service.request_lifecycle_operation` — beides mit
  Actor-Origin `external`.
- Der Vertrag wird vor dem ersten Zustandswechsel festgeschrieben. Scheitert
  die Provisionierung, bleibt ein abfragbarer Vertrag mit `status="failed"` und
  stabilem Fehlercode zurück, statt spurlos zurückgerollt zu werden.
- Der Shop übermittelt keine internen MSM-Details. Ein Produkt wird über
  `(integration_id, external_product_key)` auf Blueprint, Ressourcenpaket,
  optionale Node und Backup-Intervall abgebildet. Antworten enthalten weder
  Node-Namen noch Hostadressen, Ports oder Installationspfade.
- Kunden erhalten ausschließlich serverbezogene Rechte auf genau ihrem Server.
  Netzwerk-, Ressourcen-, Reinstall- und Datenbankadministration sind nicht
  enthalten; `servers.delete` bleibt global und damit für Kunden unerreichbar.
  Eine Sperrung entzieht diese Rechte und stoppt den Server, lässt den
  Panelaccount des Kunden aber bestehen.
- Eine Kündigung löscht nichts sofort. Sie sperrt den Server und setzt eine
  Frist (`terminate_grace_days`, Default 7). Erst ein späterer Wartungslauf
  löscht über den gemeinsamen `server_deletion_service` — mit erneuter
  Rechteprüfung gegen den Dienstbenutzer.
- Der externe Kundenbezeichner wird nur als SHA-256-Hash gespeichert. Die
  externe Service-ID bleibt im Klartext, weil Idempotenz, Statusabfrage und
  Support sie benötigen. E-Mail-Adressen werden nur übernommen, wenn sie in MSM
  noch frei sind; eine bestehende Adresse führt nie zu einer automatischen
  Kontoübernahme.
- API-Keys liegen ausschließlich als SHA-256-Hash vor, Webhook-Secrets
  DIS-verschlüsselt, Handoff-Token als SHA-256-Hash. Klartextwerte werden genau
  einmal beim Erzeugen bzw. Rotieren zurückgegeben. Unbekannter, falscher und
  deaktivierter API-Key liefern dieselbe `401`-Antwort.
- Handoffs gelten fünf Minuten, genau einmal und nur für eine feste Allowlist
  interner Pfade (`/servers`, `/servers/{id}`, `/dashboard`). Der Verbrauch ist
  ein bedingtes UPDATE und damit auch bei parallelen Klicks eindeutig. Jeder
  Fehlerfall führt einheitlich auf die Loginseite; der Token erscheint weder im
  Audit noch in Logs.
- Webhooks sind HMAC-SHA256 über `timestamp.body` signiert (`X-MSM-Signature`,
  `X-MSM-Timestamp`) — das Secret verlässt MSM nie. Zustellungen sind mit
  `next_attempt_at` in der Datenbank persistiert und überleben einen
  Panel-Neustart; fünf Versuche mit wachsendem Abstand, 4xx ohne Wiederholung,
  manueller Retry über das Panel.
- Panelseitige Verwaltung erfordert `panel.hoster.read` bzw.
  `panel.hoster.write` und CSRF. Die externe API kennt keinen Cookie-Pfad, die
  Handoff-Einlösung keinen API-Key. Alle Vorgänge erscheinen mit gemeinsamer
  Korrelations-ID und Origin `external` im secret-freien Audit.
- Der Server-Löschpfad wurde aus dem Router in `server_deletion_service`
  extrahiert, damit Panel und Hoster-Anbindung dieselbe Implementierung
  verwenden. Prüfbare Schritte laufen dort jetzt vor den unwiderruflichen, und
  nach außen gehen nur stabile Fehlercodes statt roher Pfad- und Agentfehler.
- Self-Hosted bleibt unberührt: ohne angelegte Integration existiert kein
  Verhalten dieser Phase. Die Migration ist in beide Richtungen getestet.

## Phase 7 — Credentials, Kubernetes und Betrieb

1. Panel-, Benutzer- und Server-Credentials trennen; Klartext wird nach dem
   Speichern nicht erneut ausgegeben. Hoster-Credentials fallen nur nach
   expliziter Policy auf Panel-Credentials zurück.
2. Kubernetes-Manifeste und Betriebsabläufe erst auf Basis der stabilen
   Aufgaben-, Node- und Secret-Verträge liefern.
3. Installation, Updates, Migration, Rollback und Datenschutzdokumentation
   synchron mit `docs/self-hosting.md`, Panel-Doku, README und Tests halten.

### Phase-7-Vertrag: Credentials

- Zugangsdaten existieren auf drei Ebenen. Panelweit bleiben sie unverändert in
  `panel_settings`; neu hinzu kommen `user_credentials` (der Tresor eines
  Benutzers) und `server_credential_bindings` (welcher Server welches Credential
  für welche Art verwendet). Ein Server verweist auf ein Credential, statt
  dessen Wert zu kopieren — eine Rotation wirkt damit sofort.
- Die Auflösungsreihenfolge ist **Server-Bindung → Umgebungsvariable →
  Panel-Zugang**. Die Bindung steht bewusst oben: sie ist die spezifischste
  Aussage. Ohne Bindung verhält sich alles exakt wie bisher, deshalb bleibt ein
  Self-Hosted-Betrieb unberührt.
- Scoped sind `github_token` und `steam_account` — genau die beiden, die
  Zielpunkt 17 nennt. Der **Steam-Web-API-Key bleibt panelweit**: er dient
  Workshop-Metadatenabfragen, nicht dem Zugriff auf Kundendaten, und
  `get_steam_service()` ist ein prozessglobaler Singleton, dessen Aufteilung ein
  eigener Umbau wäre.
- Geheimnisse liegen ausschließlich DIS-verschlüsselt mit der objektgebundenen
  AAD `msm:credential:{id}:secret` vor. Es gibt keinen Lesepfad, der Klartext
  ausliefert; Antworten enthalten nur Bezeichnung, Benutzername und die letzten
  vier Zeichen.
- Jeder Benutzer verwaltet seinen eigenen Tresor ohne zusätzliche Berechtigung.
  Binden an einen Server erfordert `server.credentials.manage`, und gebunden
  werden darf **nur ein Credential, das dem Handelnden selbst gehört** — sonst
  könnte jemand mit Serverrechten fremde Zugangsdaten in Betrieb nehmen.
  Hoster-Kunden erhalten dieses Recht auf ihrem eigenen Server.
- Der zentrale Fallback ist eine Betreiberentscheidung
  (`credentials.allow_panel_fallback`, Default `true`). Ist er aus, läuft ein
  Server ohne eigene Bindung nicht still mit dem Betreiberzugang, sondern meldet
  einen verständlichen Fehler.
- Ein nicht entschlüsselbares gebundenes Credential fällt **nie** stillschweigend
  auf den Panel-Zugang zurück; es endet mit `503`. Ein gebundenes Credential
  lässt sich nicht löschen, solange ein Server es verwendet.
- Die Serveroberfläche zeigt nur die Arten, die der Blueprint dieses Servers
  tatsächlich verlangt, samt Herkunft und Zuordnung.

### Phase-7-Vertrag: Kubernetes

- Kubernetes betreibt die **Control Plane** (Panel, DIS-Sidecar, optional
  PostgreSQL). Gameserver laufen unverändert als Docker-Container über den
  `msm-agent` auf angebundenen Nodes. Kapazität wächst über weitere Nodes, nicht
  über weitere Panel-Replicas.
- Der DIS-Sidecar bindet ausschließlich an `127.0.0.1` und läuft deshalb als
  Container **im selben Pod**. Es gibt bewusst keinen Service und keinen Port
  9100 nach außen.
- `replicas: 1` und `strategy: Recreate` sind eine Korrektheitsbedingung, keine
  Vorsicht: Scheduler-Jobs, Lifecycle-Sperren und der Settings-Cache liegen im
  Prozessspeicher. Zwei Instanzen würden Serveraktionen doppelt ausführen.
- Geheimnisse kommen ausschließlich per `secretKeyRef`. Im angewendeten
  Manifestsatz existiert kein `Secret`-Objekt; `10-secrets.example.yaml` ist
  reine Feldreferenz mit `REPLACE_ME`-Platzhaltern.
- Alle Container laufen unprivilegiert (`runAsNonRoot`, `drop: ALL`, keine
  Privilege-Escalation) mit gesetzten CPU- und Memory-Limits. Der Namespace
  erzwingt `pod-security.kubernetes.io/enforce: restricted`.
- Startup-, Readiness- und Liveness-Probe verwenden `GET /api/health`. Die
  Startup-Probe lässt bis zu fünf Minuten für Migrationen. NetworkPolicies
  verweigern standardmäßig alles; die Metadata-Adresse `169.254.169.254` bleibt
  auch ausgehend gesperrt.
- Diese Zusagen sind in `backend/tests/test_kubernetes_manifests.py` als Test
  festgehalten, damit sie beim Bearbeiten der Manifeste nicht still wegbrechen.

## Phase 8 — Shop-API-Referenz, erweiterte AI-Werkzeuge, autonomer Modus

### Shop-API-Referenz

`docs/hoster-api.md` und die In-App-Seite `/docs/hoster-api` beschreiben den
vollständigen Vertrag: alle fünf externen Endpunkte plus die zwölf
Admin-Endpunkte, jedes Request- und Response-Feld, das Zustandsvokabular, die
Webhook-Nutzlast mit allen Eventnamen und ein nachrechenbares HMAC-Beispiel.

`SERVICE_STATUSES` in `hoster_service_lifecycle.py` ist die gemeinsame Quelle;
`_set_status()` erzwingt sie bei jeder Zuweisung.
`test_hoster_api_docs_contract.py` schlägt fehl, sobald ein Endpunkt, ein
Status, ein Antwortfeld oder ein Betriebswert (Timeout, Backoff, Retention,
Payload-Grenze) nicht dokumentiert ist — und wenn das HMAC-Beispiel nicht mehr
zu `sign_payload()` passt.

Dokumentiert ist ausdrücklich auch, was vorher nur im Code stand: dass eine
Payload über 16 KiB **still verworfen** wird und dass eine `4xx`-Antwort des
Empfängers die Zustellung endgültig beendet.

### Erweiterte AI-Werkzeuge

Die Tool-Allowlist wächst von 7 auf 15 server-bezogene plus 3 globale Werkzeuge.
Neu lesend: Ports, Mods, Backups, Guardian-Vorfälle, KI-Aktionsverlauf,
Mod-Updates, Workshop-Suche; im Panel-Chat Blueprint-Liste und Hostkapazität.
Neu schreibend: `propose_mod_install` und `propose_server_create`.

- **Kein zweiter Erstellungsweg.** `propose_server_create` ruft ausschließlich
  `server_provisioning_service.provision_server` mit `ActorContext(origin="ai")`
  und `idempotency_key=f"ai-{proposal_id}"`. `propose_mod_install` nutzt
  `install_mod_bg` samt dessen Install-Lock — die KI bekommt keinen eigenen
  Downloadpfad, Zielpunkt 16 bleibt unangetastet.
- **`ai_action_proposals.server_id` ist nullable**, weil ein Erstellungsvorschlag
  vor der Ausführung keinen Server hat. Nach der Ausführung trägt er ihn.
- **Bis zu drei Read-Runden** statt einer. Ohne das war „Kapazität lesen →
  Blueprints lesen → vorschlagen" unmöglich.
- **Tool-Ergebnisse werden persistiert** (`ai_tool_results`) und als ein
  untrusted `user`-Block wieder eingespeist, begrenzt auf 8.000 Zeichen. Eine
  Rückfrage sieht die zuvor gelesenen Daten damit noch.
- **Jedes Schreib-Tool verlangt `reason` und `expected_effect`** (Zielpunkt 3.6).
  Ein Skill-Schritt liefert stattdessen seine Herkunft — die präzisere Angabe.

### Autonomer Modus

`ai.autonomous.use` bekommt eine Wirkung. Vier Bedingungen müssen gleichzeitig
gelten: die Berechtigung, ein aktiver `AiAutonomyGrant` (pro Server oder
panelweit, spezifischer gewinnt **auch wenn er abschaltet**), ein Werkzeug
außerhalb von `ALWAYS_CONFIRM_TOOLS`, und freies Stundenbudget.

Die Ausführung läuft über `execute_autonomously()` und damit über exakt dieselben
zwei Schritte wie eine bestätigte Aktion — `confirm_proposal` und
`execute_proposal`. Autonomie ersetzt genau einen Schritt: den Klick des
Menschen. Rechteprüfung, Aktivprüfung, Server-Mutex und Audit bleiben. Ist das
Budget erschöpft, schlägt nichts fehl; der Vorschlag wird wieder
bestätigungspflichtig.

### Sicherheitsbefunde

- `assert_provider_destination` prüft und pinnt jetzt dieselbe DNS-Auflösung.
  Vorher stammte die gepinnte Adresse aus einer zweiten, ungeprüften Abfrage —
  exakt das Rebinding-Fenster, das die Funktion schließen sollte.
- Skill-Läufe reservieren über `reserve_ai_usage`. Vorher liefen sie an
  `requests_per_minute` und `concurrent_operations` vollständig vorbei.
- Die FastAPI-Doku-Routen liegen unter `/api/docs`, `/api/redoc`,
  `/api/openapi.json` und verlangen `panel.settings.read`. Vorher überschattete
  Swagger im Single-Host-Betrieb die Doku-Seite des Panels, und das vollständige
  Schema war ohne Login abrufbar.
- Tool-Ergebnisse tragen ein `untrusted`-Flag, Memory läuft als `role: user`
  statt `system` und escaped Zeilenumbrüche, und der Bestätigungsdialog nennt
  Werkzeug, Änderung und Herkunft.

### Migrationskette

`test_migration_chain_upgrade.py` fährt die neun Migrationen dieses Branches
per `downgrade` auf den Stand von `main` und wieder hoch — echte DDL statt
`stamp`. Vorher führten zwei von ihnen ihr `upgrade()` in keinem Test aus.

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
