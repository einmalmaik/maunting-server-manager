Agenten-Regeln: Dependencies

Stand: 2026-05-24

Ergänzt die Root-AGENTS.md. Diese Datei ist zu lesen, wenn neue Libraries, Major-Updates, SSH/Auth/Storage/Logging/Telemetry-Abhängigkeiten, Build-Tooling oder transitive Dependency-Flächen betroffen sind.

1. Grundsatz

Jede Dependency ist ein Supply-Chain-Risiko.

Eine neue Bibliothek ist nur erlaubt, wenn sie einen klaren Sicherheits-, Wartbarkeits- oder Plattformnutzen hat. Komfort allein reicht nicht.

Neue Dependencies sind besonders kritisch, wenn sie berühren:

SSH-Keys (Public/Private)

Server-Passwörter

API- und Auth-Tokens

Storage (Datenbank für Server-Konfigurationen)

Remote-Command-Execution (Terminal/SSH-Clients)

Webhook Secrets

Logging

Telemetrie

Error Reporting

Build- und Packaging-Prozess

2. Harte Verbote

Verboten:

unmaintained SSH-, Auth-, Storage- oder Secret-Libraries

Libraries mit unklarem Sicherheitsmodell oder mangelhafter Dokumentation

Libraries, die sensible Server-Daten (IPs, Ports, Metadaten) an externe Dienste senden

Telemetrie-, Analytics- oder Error-Reporting-Libraries im Server-Management-Kontext ohne explizite Datenschutzentscheidung

Dependencies, die Frontend-/Backend-Pfade durch globale Side Effects unklar machen

Komfort-Libraries für triviale Logik (KISS-Verstoß)

neue SSH/Auth/Storage-Libraries ohne ADR (Architecture Decision Record) oder Security-Doku

Major-Updates in Security-Pfaden ohne Changelog-, Test- und Runtime-Prüfung

direkte Nutzung von SSH/Terminal-Libraries aus fachfremder UI-Logik heraus

Pakete, die globale Prototypen, globale Error-Handler oder das globale Storage-Verhalten verändern

3. Pflichtprüfung vor neuer Dependency

Vor jeder neuen Dependency dokumentieren:

Kriterium

Muss beantwortet werden

Zweck

Welches konkrete Projektproblem löst sie?

Notwendigkeit

Warum reicht vorhandener Code oder eine Plattform-API nicht?

Security

Berührt sie Server-Credentials, Keys, Auth, Storage oder Remote-Commands?

Wartung

Wie aktiv wird sie gepflegt?

Advisories

Gibt es Security Advisories oder offene CVEs?

Transitive Fläche

Wie groß ist die transitive Dependency-Fläche?

API

Ist die API klein, verständlich und schwer falsch zu benutzen?

Bundle

Ist die Größe und Angriffsfläche vertretbar?

Lizenz

Ist die Lizenz kompatibel mit Projekt und Distribution?

Kapselung

Wird sie hinter einem Adapter/einer Fassade isoliert?

Entfernbarkeit

Wie kann sie wieder entfernt oder ersetzt werden?

Alternativen

Welche bessere oder sicherere Alternative wurde geprüft?

Eine Dependency ohne beantwortete Prüfung darf nicht eingeführt werden.

4. Bewertungsschema

Einstufung:

Niedriges Risiko

reine Dev-Dependency

kein Zugriff auf Runtime-Daten

keine Netzwerk-, Storage-, SSH- oder Auth-Berührung

kleine transitive Fläche

gut wartbar

leicht entfernbar

Trotzdem: dokumentieren, warum sie gebraucht wird.

Mittleres Risiko

Runtime-Dependency

UI-nahe Nutzung (z. B. UI-Komponenten-Bibliotheken)

keine sensiblen Server-Daten

begrenzte transitive Fläche

kleine API

Erforderlich: gezielte Tests und Bundle-/Runtime-Prüfung.

Hohes Risiko

SSH / Remote Execution

Auth (RBAC, Session Management)

Storage

Secret Handling (Key Management)

Telemetrie

Error Reporting

Build-/Packaging-Supply-Chain

globale Polyfills oder Side Effects

Erforderlich: ADR oder Security-Doku, Alternativenvergleich, Adapter, Tests, Runtime-Prüfung.

5. Dependency-Kapselung

Regeln:

Fachlogik importiert riskante Libraries (wie z.B. ssh2) nicht direkt.

SSH-, Storage-, Auth-, Telemetry- und Error-Reporting-Libraries werden hinter einer Fassade oder einem Adapter gekapselt.

Adapter haben kleine APIs.

Adapter haben Tests für Erfolg, Fehler und Missbrauch (z.B. Timeout-Verhalten).

Migration auf eine andere Library muss möglich bleiben.

Die restliche Codebasis soll nicht von Library-spezifischen Typen abhängig werden, wenn diese Typen nicht Teil des fachlichen Modells sind.

Schlecht:

import { Client } from "ssh2";

export async function executeScript(serverIp: string, key: string, script: string) {
  const conn = new Client();
  // ... direkte SSH-Logik überall im Projekt verstreut
  conn.connect({ host: serverIp, privateKey: key });
}


Warum schlecht: Fachlogik hängt direkt an einer spezifischen SSH-Library, Key-Management findet unkontrolliert statt, Migration auf ein anderes Protokoll/Library ist enorm aufwendig.

Gut:

export async function executeScript(server: Server, script: string) {
  // SSH-Logik ist sicher im serverConnectionService gekapselt
  return serverConnectionService.execute({
    serverId: server.id,
    script,
    context: { executedBy: "admin-user" }
  });
}


Warum gut: Fachlogik nutzt Projekt-Services, SSH/Connection-Details sind gekapselt, Logging und Kontext sind explizit steuerbar.

6. Komfort-Libraries

Keine Bibliothek für triviale Logik.

Schlecht:

import leftPad from "left-pad";

export function formatServerId(id: string) {
  return leftPad(id, 6, "0");
}


Warum schlecht: externe Supply-Chain für triviale Logik, unnötige Auditfläche, kein Sicherheitsnutzen.

Gut:

export function formatServerId(id: string): string {
  return id.trim().padStart(6, "0");
}


Warum gut: nativ, verständlich, testbar, keine zusätzliche Angriffsfläche.

7. Bestehende Dependencies

Eine bestehende Dependency darf nicht blind weitergetragen werden, nur weil sie schon im Projekt ist.

Wenn eine bestehende Bibliothek berührt wird, prüfen:

Wird sie noch benötigt?

Gibt es eine sicherere Plattform-API?

Gibt es eine kleinere Alternative?

Gibt es offene CVEs oder Advisories?

Ist die Nutzung korrekt gekapselt?

Wird sie an mehr Stellen importiert als nötig?

Hat sich die API unsicher verändert?

Gibt es neue transitive Abhängigkeiten?

Muss ein Adapter angepasst werden?

8. Updates

Vor Minor-/Patch-Updates in normalen Pfaden:

Changelog prüfen

Tests ausführen

Runtime öffnen, wenn UI/Build/Runtime betroffen ist

Vor Major-Updates oder Updates in Security-Pfaden (z.B. SSH-Clients, Auth-Middleware):

Changelog prüfen

Breaking Changes prüfen

Security Advisories prüfen

Migrationshinweise prüfen

betroffene Adapter prüfen

gezielte Tests ergänzen

npm run test vollständig ausführen

Runtime-Prüfung durchführen

Risiko und Restrisiko dokumentieren

Keine Massenupdates, wenn nur eine gezielte Änderung nötig ist.

9. Telemetrie, Analytics und Error Reporting

Besonders kritisch in einem Server Manager.

Regeln:

Keine Telemetrie im Server-Management-Pfad ohne explizite Datenschutzentscheidung.

Keine SSH-Keys, Passwörter, IP-Adressen, Hostnamen, Server-Metadaten oder Command-Outputs an externe Dienste (wie Sentry, LogRocket etc.) senden.

Keine vollständigen URLs tracken, wenn sie Tokens oder sensiblen State enthalten könnten.

Keine produktiven Stacktraces an externe Tools senden, wenn sie API-Schlüssel oder interne Pfade leaken könnten.

Error-Reporting nur mit einer strikten Sanitizing-Fassade (die IPs und Keys vor dem Senden zensiert) und dazugehörigen Tests.

Opt-out/Opt-in-Regeln müssen dokumentiert sein, falls Telemetrie existiert.

10. Build- und Tooling-Dependencies

Build-Tools können Supply-Chain- und Runtime-Risiken erzeugen.

Prüfen:

Verändert das Tool Importpfade?

Erzeugt es doppelte Modulidentität?

Verändert es Tree-Shaking?

Fügt es globale Polyfills ein?

Leakt es Env-Variablen in Client-Bundles (z.B. Datenbank-Passwörter in das Frontend)?

Greift es auf Netzwerk, Dateisystem oder Secrets zu?

Schreibt es generierte Dateien mit sensiblen Daten?

11. ADR-Vorlage für riskante Dependencies

# ADR: <Dependency-Name>

## Problem

<Welches konkrete Problem löst die Dependency?>

## Entscheidung

<Welche Dependency wird verwendet und wo wird sie gekapselt?>

## Alternativen

- <Alternative 1>
- <Alternative 2>
- Plattform-API
- eigener minimaler Code

## Security-Bewertung

- Berührt Server-Credentials/SSH-Keys/Auth/Storage/Remote-Commands?
- Advisories/CVEs geprüft?
- Maintainer-Aktivität geprüft?
- Transitive Dependencies geprüft?

## Nutzung im Projekt

- Import nur in: <Adapter/Fassade>
- Tests: <Liste>
- Runtime-Prüfung: <Liste>

## Exit-Plan

<Wie wird die Dependency ersetzt oder entfernt?>


12. Dependency-Review-Checkliste

[ ] Löst die Dependency ein echtes Projektproblem?

[ ] Reicht vorhandener Code oder Plattform-API wirklich nicht?

[ ] Berührt sie Server-Credentials, Keys, Auth, Storage oder Remote-Commands?

[ ] Security Advisories/CVEs geprüft?

[ ] Maintainer-Aktivität geprüft?

[ ] Transitive Dependency-Fläche geprüft?

[ ] Lizenz geprüft?

[ ] API klein und schwer falsch zu nutzen?

[ ] Hinter Adapter/Fassade gekapselt?

[ ] Tests ergänzt?

[ ] Runtime geprüft, wenn betroffen?

[ ] Alternative dokumentiert?

[ ] Exit-Plan vorhanden?

13. S3-Backup-Dependencies (M1-M4 Backup-System)

Stand: 2026-07-05

Dokumentiert die Pflichtpruefung (Sektion 3) fuer die beiden neuen
Dependencies des MSM-Backup-Systems (S3-Cloud-Backups mit DIS-Verschluesselung).

13.1 boto3==1.43.40 (Runtime-Dependency, mittleres/hohes Risiko)

Problem:
  S3-kompatibler Object-Storage fuer verschluesselte Off-Site-Backups.
  Benoetigt fuer Upload, Download, Listing und Delete von Backup-Objekten bei
  jedem S3-kompatiblen Provider (Backblaze B2, Wasabi, Hetzner, MinIO, AWS).

Notwendigkeit:
  Eigene S3-Implementierung (HTTP + SigV4 + Multipart) waere extrem aufwendig
  und fehleranfaelligig. boto3 ist der offizielle AWS-Client, gilt als
  De-facto-Standard, ist auditiert und wird aktiv gepflegt.

Security:
  Beruehrt Storage und S3-Credentials. Credentials werden verschluesselt via
  DIS in panel_settings gespeichert (AAD="msm:backup:s3") und erst zur
  Laufzeit in S3Service._get_client entschluesselt. Keine Credentials in
  Logs oder Fehlermeldungen (generische Messages, ClientError wird ohne
  Credential-Leak weitergereicht). botocore wird transitiv durch
  boto3==1.43.40 gepinnt (gleiche Version, AWS-Versionierungsschema).

Advisories/CVEs:
  Zum Zeitpunkt der Einfuehrung (2026-07-05) keine bekannten offenen CVEs
  fuer boto3 1.43.40 / botocore 1.43.40. Vor jedem Update erneut pruefen.

Transitive Flaeche:
  boto3 zieht botocore, s3transfer, jmespath, python-dateutil, urllib3.
  Begrenzt und kontrolliert (alle AWS-offiziell).

Lizenz: Apache-2.0 (kompatibel).

Kapselung:
  Import NUR in services/s3_service.py (S3Service-Fassade). Fachlogik und
  Routers importieren boto3 nicht direkt. S3Service ist Single Source of
  Truth fuer alle S3-Operationen (DRY).

Tests:
  backend/tests/test_s3_service.py und weitere Backup-Tests nutzen moto
  fuer S3-Mocking. Siehe services.yaml test-* Befehle.

Runtime:
  Backend-Dev-Server startet, Backup-Config-API funktionieren, S3-Verbindungs-
  test klappt mit echten Backblaze B2-Credentials (E2E-Validierung M1-M4).

Entfernbarkeit / Exit-Plan:
  S3Service-Fassade ermoeglicht Austausch gegen alternative S3-Client-Library
  (z.B. aiobotocore, minio-py) ohne Aenderung an Fachlogik, Routers oder
  Frontend. Nur services/s3_service.py waere anzupassen.

13.2 moto[s3]==5.2.2 (Test-Only-Dependency, niedriges Risiko)

Problem:
  S3-API muss in Tests gemockt werden, ohne echte S3-Verbindung oder
  Credentials. Ermöglicht deterministische, isolierte Backup-Tests.

Notwendigkeit:
  Echte S3-Aufrufe in Unit-/Integration-Tests waeren langsam, teuer und
  wuerden echte Credentials benoetigen. moto mockt die S3-API in-memory.

Security:
  Test-only. KEIN Zugriff auf Runtime-Daten in Produktion. Keine Netzwerk-,
  Storage-, SSH- oder Auth-Beruehrung ausserhalb von Tests. Wird nicht in
  requirements.txt aufgefuehrt, sondern in dev-requirements.txt ( strikte
  Trennung Prod vs. Test).

Advisories/CVEs:
  Zum Zeitpunkt der Einfuehrung (2026-07-05) keine bekannten offenen CVEs
  fuer moto 5.2.2. Vor jedem Update erneut pruefen.

Transitive Flaeche:
  moto zieht weitere Submodule (responses, werkzeug, jinja2, u.a.). Begrenzt
  und nur in Dev-Umgebung relevant.

Lizenz: Apache-2.0 (kompatibel).

Kapselung:
  Import NUR in backend/tests/ (`from moto import mock_aws`). NIEMALS in
  services/, routers/, models/ oder schemas/ importieren.

Tests:
  Selbst nicht Gegenstand von Tests, sondern Test-Infrastruktur.

Entfernbarkeit / Exit-Plan:
  Loeschen von dev-requirements.txt und Anpassung der Tests an alternativa
  Mocking-Strategie (z.B. boto3-Stub) ermoeglicht Entfernung.

13.3 Installations-Trennung

- requirements.txt:    boto3==1.43.40 (Produktion + Dev)
- dev-requirements.txt: moto[s3]==5.2.2 (NUR Dev/Test)
- Prod-Install:        pip install -r requirements.txt
- Dev-Install:         pip install -r requirements.txt -r dev-requirements.txt

Diese Trennung stellt sicher, dass moto niemals in Produktion landet und
die transitive Flaeche in Prod minimal bleibt.

14. pytest-asyncio==1.4.0 (Test-Only-Dependency, niedriges Risiko)

Problem:
  Scheduler-, Streaming- und Multi-Node-Tests enthalten echte async Testfälle.
  Ohne ein registriertes Asyncio-Pytest-Plugin werden diese Tests nicht
  ausgeführt und können trotz vorhandener Assertions keine Regression erkennen.

Notwendigkeit:
  Eigene Pytest-Hooks oder wiederholte asyncio.run-Wrapper würden
  Test-Infrastruktur duplizieren und Fixture-/Cancellation-Verhalten nur
  unvollständig nachbauen. pytest-asyncio ist die offizielle, fokussierte
  Pytest-Erweiterung für asyncio-Tests.

Security:
  Ausschließlich Dev/Test. Kein Import in Produktion, kein Netzwerkzugriff
  durch das Plugin und kein Zugriff auf Server-Credentials oder Runtime-State.

Wartung und Advisories:
  Version 1.4.0 wurde am 2026-05-26 veröffentlicht, unterstützt Python
  3.10-3.14 und ist auf PyPI als Production/Stable klassifiziert. Zum
  Prüfzeitpunkt 2026-07-28 waren keine offenen Security Advisories bekannt.

Transitive Fläche und Lizenz:
  Kleine Testfläche auf Basis des bereits genutzten pytest; Apache-2.0.

Kapselung:
  Ausschließlich pytest-Plugin-Aktivierung über backend/dev-requirements.txt
  und @pytest.mark.asyncio in backend/tests/. Kein Runtime-Import.

Exit-Plan:
  Markierte Tests auf explizite asyncio.run-Szenarien umstellen und den einen
  Eintrag aus dev-requirements.txt entfernen.

---

## model2vec + numpy — Lokale Embeddings für das KI-Gedächtnis

Stand: 2026-08-08

Problem:
  Die Auswahl der Erinnerungen bei knappem Kontextbudget lief über
  Wortüberlappung. Die greift ausschließlich innerhalb einer Sprache: ein
  deutscher Eintrag und eine englische oder französische Frage haben kein
  gemeinsames Wort, und das Gedächtnis wirkt in diesem Moment defekt, obwohl
  der passende Eintrag danebenliegt.

Notwendigkeit:
  Semantische Ähnlichkeit braucht Vektoren. Gemessen am 2026-08-08 bietet
  OpenRouter kein kostenloses Embedding-Modell — HTTP 402 bei
  `openai/text-embedding-3-small`, HTTP 400 bei `openrouter/free`. Ein
  Gedächtnis, dessen Suche einen bezahlten Provider-Account voraussetzt, wäre
  für einen Teil der Betreiber nicht nutzbar. Also lokal.

Alternativen (gemessen, nicht geschätzt):
  - mem0ai: 35 Pakete. Ausgeschlossen aus drei unabhängigen Gründen.
    `posthog` ist Pflichtabhängigkeit — Abschnitt 2 dieser Datei listet
    Analytics-Bibliotheken im Server-Management-Kontext unter harte Verbote.
    `httpx>=0.28.0` kollidiert mit unserem gepinnten `httpx==0.27.0` im
    SSRF-geschützten Ausgangspfad. `openai>=1.90.0` wäre ein zweites
    Provider-SDK neben dem eigenen Adapter, ohne dessen IP-Pinning.
  - fastembed: 30 Pakete, davon `onnxruntime` als großes Binärpaket. Kein
    Ausschlussgrund, aber deutlich mehr Fläche bei gleichem Nutzen.
  - Postgres-Volltextsuche: sprachgebunden und damit am Problem vorbei.
  - pgvector: MSM verwaltet seinen PostgreSQL selbst mit `postgres:17-alpine`
    (config.managed_postgres_image), das die Erweiterung nicht enthält. Ein
    Image-Wechsel träfe jede Installation. Bei höchstens 100 Einträgen je
    Bereich ist ein Skalarprodukt in Python ohnehin schneller als der
    Datenbank-Roundtrip; ein Vektorindex lohnt ab etwa 10.000 Einträgen.

Security:
  `model2vec` berührt weder Secrets noch Server-Verbindungen. Der Dienst
  bekommt bereits entschlüsselten Erinnerungstext und liefert Zahlen zurück.
  Kein Netzwerkzugriff zur Laufzeit: die Gewichte kommen einmalig über
  `backend/scripts/fetch_embedding_model.py`, aufgerufen aus install.sh und
  update.sh. Ein Panel, das im Betrieb Gewichte nachlädt, wäre eine
  Supply-Chain-Fläche und ist ausdrücklich ausgeschlossen.

  Der berechnete Vektor liegt unverschlüsselt neben dem weiterhin
  DIS-verschlüsselten Wert. Begründung: der `key` derselben Zeile steht ohnehin
  im Klartext und verrät mehr als 256 Gleitkommazahlen, aus denen sich der Text
  nicht rekonstruieren lässt. Der Gewinn ist konkret — die Auswahl findet vor
  dem Entschlüsseln statt, was pro Chatnachricht dutzende Sidecar-Aufrufe
  spart. Restrisiko benannt: wer die Datenbank besitzt, kann mit den Vektoren
  Vermutungen über Inhalte bestätigen, den Text aber nicht lesen.

Transitive Fläche und Lizenz:
  23 Pakete, im Wesentlichen numpy, tokenizers, huggingface_hub. MIT.
  `huggingface_hub` verlangt `httpx<1,>=0.23.0` und ist damit mit unserem
  `httpx==0.27.0` verträglich — geprüft per Auflösungstest.

Modell:
  `minishlab/potion-multilingual-128M`, MIT, rund 507 MB. Statische
  Embeddings, also eine vorberechnete Tabelle plus Mittelung — kein neuronales
  Netz zur Laufzeit, daher kein torch und keine ONNX-Runtime.

  Gemessene Grenzen, damit niemand mehr erwartet als es kann: `Zeitzone` zu
  `timezone` 0,62, aber `Sicherung` zu `backup` nur 0,27. Unverwandtes trennt
  es zuverlässig (nahe 0,0). In einem kleinen Vergleich über acht Fragen traf
  Wortabgleich 5, Embedding 6, beide kombiniert 6. Deshalb ersetzt die
  Bedeutungssuche den Wortabgleich nicht, sondern ergänzt ihn — im
  Gameserver-Umfeld besteht viel Fachsprache aus Lehnwörtern, die wörtlich in
  deutschen Einträgen stehen.

Kapselung:
  Ausschließlich `services/ai_embedding_service.py`. Kein anderer Modulteil
  importiert model2vec oder numpy direkt.

Exit-Plan:
  `encode()` liefert `None`, wenn das Modell fehlt oder nicht lädt — die
  Auswahl läuft dann über Wortabgleich, Nutzung und Aktualität weiter. Das ist
  kein theoretischer Ausstieg, sondern ein getesteter Betriebszustand
  (`test_ai_memory_embeddings.py::test_without_a_model_nothing_breaks`).
  Entfernen heißt: zwei Zeilen aus requirements.txt, den Dienst löschen, die
  beiden Spalten per Migration fallen lassen.
