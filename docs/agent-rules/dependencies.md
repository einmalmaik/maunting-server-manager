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
    Image-Wechsel träfe jede Installation. Bei bis zu 5.000 Einträgen je Bereich
    (`ai_limit_service.MAX_MEMORY_ENTRIES_MAX` — die 100, die hier ursprünglich
    stand, ist seit 2026-08-15 nur noch der Ausgangswert je Rolle) ist ein
    Skalarprodukt in Python weiterhin schneller als der Datenbank-Roundtrip.
    Der Deckel stieg am 2026-08-19 von 1.000 auf 5.000, und damit war die hier
    verlangte Neuprüfung fällig; sie ging noch einmal für Python aus. Gemessen
    an diesem Tag bei 5.000 Zeilen: das Skalarprodukt selbst kostet 38 ms, das
    Einbetten der Frage 0,4 ms — die Zeit geht mit 381 ms in das Lesen der
    Vektoren aus ihrer JSON-Spalte. pgvector löste beides, aber die Rechnung
    klemmt nicht, und das Format ist eine Entscheidung in unserer eigenen
    Tabelle: dieselben Zahlen als float32-Bytes brauchen 4,0 ms statt 381 ms,
    ohne Erweiterung und ohne fremdes Image. Der Abstand ist trotzdem
    aufgebraucht: bis
    zur Index-Schwelle von etwa 10.000 Einträgen ist es keine Zehnerpotenz
    mehr, sondern Faktor zwei, und in einen Prompt fließen mehrere Bereiche
    nebeneinander. Steigt der Deckel erneut, ist pgvector neu zu prüfen — und
    dann ohne den Ausweg, dass zuerst das Format an der Reihe wäre.

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

---

## pyyaml — Frontmatter der mitgelieferten Skills

Stand: 2026-08-09

Problem:
  Skills sind seit Phase E Textdateien mit einem YAML-Kopf (`name`,
  `description`). Der Kopf muss gelesen werden, bevor ein Skill überhaupt im
  Verzeichnis auftauchen kann.

Warum nicht selbst parsen:
  Der Kopf hat nur zwei flache Zeichenkettenfelder — ein Fünfzeiler mit
  `split(":", 1)` läge nahe. Er würde aber bei Anführungszeichen, mehrzeiligen
  Werten und doppelten Doppelpunkten still das Falsche tun, und genau solche
  Zeichen stehen in Skill-Beschreibungen ("Nutzen bei: Timeouts, 'Server nicht
  gefunden'"). Ein Parser, der in diesen Fällen ohne Fehlermeldung einen halben
  Satz liefert, ist schlechter als eine Bibliothek.

Warum diese:
  `pyyaml` steckt ohnehin im Abhängigkeitsbaum — `huggingface_hub` verlangt
  `pyyaml>=5.1`, und `huggingface_hub` kommt über `model2vec`. Es wird hier nur
  ausdrücklich gepinnt, weil `ai_skill_service` es direkt benutzt: eine
  Bibliothek, auf die man sich verlässt, gehört in requirements.txt und nicht
  in den Zufall eines transitiven Baums.

Security:
  Ausschließlich `yaml.safe_load`. `yaml.load` kann beliebige Python-Objekte
  erzeugen und ist damit eine Codeausführung; `safe_load` kennt nur
  Grunddatentypen. Gelesen werden nur Dateien aus `backend/ai_skills/`, also
  Repo-Inhalt — kein Benutzerupload, kein Netzwerkinhalt.

Kapselung:
  Ausschließlich `services/ai_skill_service.py::_parse_shipped`. Eine
  beschädigte Datei fällt dort mit einer Logzeile aus dem Verzeichnis, statt
  den Start des Panels zu verhindern.

Exit-Plan:
  Ein eigener Parser wäre möglich, wenn man den Kopf auf ein striktes Format
  ohne Sonderzeichen festlegt. Solange das nicht entschieden ist, bleibt die
  Bibliothek — und sie kostet nichts, weil sie ohnehin installiert wird.

## `data-url` (Rust, über das Tauri-Feature `webview-data-url`) — 23.08.2026

Problem:
  Der Sichtfeld-Indikator (`smart-system/src-tauri/src/sichtfeld.rs`) ist das
  kleine rote Schild, das aufleuchtet, sobald die KI ein Bildschirmfoto macht.
  Es ist ein eigenes, rahmenloses Tauri-Fenster und braucht Inhalt: ein
  Fragment HTML mit einem Punkt und einem Satz.

Warum nicht als Datei im Bundle:
  Weil genau das der stille Weg wäre, den Indikator loszuwerden. Er hat
  bewusst keinen Schalter — wer ihn abschalten will, müsste am Quelltext
  arbeiten. Eine HTML-Datei im Bundle dagegen kann ein Installer, ein
  Build-Schritt oder ein aufgeräumtes `dist`-Verzeichnis auslassen, und dann
  scheitert der Fensterbau still. Der Indikator verschwindet, ohne dass jemand
  eine Zeile Code geändert hätte, und die Aufnahme läuft weiter (so ist die
  Fehlerbehandlung gebaut, und so muss sie sein). Als `data:`-URL steht der
  Inhalt im Binärcode neben dem Aufruf.

Warum diese:
  Es ist keine gewählte Bibliothek, sondern das, was Tauri selbst für sein
  Feature `webview-data-url` einzieht — `data-url` 0.3.2 aus dem
  Servo-Projekt, MIT/Apache-2.0, reines Parsen von `data:`-URLs, kein I/O,
  kein Netzwerk, keine eigenen Abhängigkeiten (eine einzige neue Zeile in
  `Cargo.lock`). Die Alternative wäre ein eigener Fenstertyp gewesen — mehr
  Code für weniger.

Security:
  Der einzige `data:`-Inhalt, der je gebaut wird, ist eine Konstante im
  Quelltext. Kein Benutzertext, kein Modelltext, nichts aus dem Netz geht
  dort hinein — sonst wäre es eine Stelle, an der fremder Text zu HTML wird.
  Wer das ändern will, ändert die Zusage des Moduls mit.

Kapselung:
  Ausschließlich `sichtfeld.rs::fenster_zeigen`. Sonst nirgends im Programm.

Exit-Plan:
  Fällt das Feature weg, wird aus dem Fragment eine Datei im Bundle — mit
  einem Test, der ihre Existenz im gebauten Paket prüft. Erst dann, denn ohne
  diesen Test wäre die Datei genau das Risiko, das oben beschrieben ist.

## `zune-jpeg` / `zune-core` (Rust, über das `image`-Feature `jpeg`) — 23.08.2026

Problem:
  Das Bildschirmfoto des Benutzerrechners muss zum Modell. Als PNG ist ein
  Vollbild regelmäßig größer als eine Million Zeichen Base64 — es passte
  weder durch die Größengrenze der Auftragsbrücke noch sinnvoll in ein
  Kontextfenster. Genau daran scheiterte die Bildschirmsicht bis zu diesem
  Tag: aufgenommen wurde, angekommen ist nie etwas.

Warum diese:
  Es ist keine gewählte Bibliothek, sondern das, was `image` für seinen
  JPEG-Kodierer einzieht. Die Kiste `image` steht seit dem 21.08.2026 im
  Baum (Verkleinern der Aufnahme, Tray-Icons); geändert hat sich nur die
  Feature-Liste von `["png"]` auf `["png", "jpeg"]`. `zune-jpeg` ist der
  reine Rust-Codec dahinter, `zune-core` sein gemeinsamer Typenspeicher —
  beide MIT/Apache-2.0, kein I/O, kein Netzwerk, kein FFI, zwei Einträge in
  `Cargo.lock`.

  Die erste Fassung dieses Kommentars behauptete "kein neues Paket im Baum",
  weil beide Kodierer derselben Kiste gehören. Das war falsch: ein Feature
  zieht Abhängigkeiten nach, und `Cargo.lock` sagt es. Der Satz steht hier,
  damit der nächste Leser nicht denselben Kurzschluss zieht.

Alternativen:
  Ein kleineres PNG (stärker verkleinern) wurde verworfen — Text auf einem
  Bildschirmfoto ist genau das, was gelesen werden soll, und er verschwindet
  als erstes. Gemessen liegt ein Foto mit 1280x720 als JPEG q75 bei 104.124
  Zeichen Base64 und bleibt gut lesbar.

Security:
  Kodiert wird ausschließlich, was `bildschirm.rs` selbst aufgenommen hat.
  Es wird nie ein fremdes JPEG **dekodiert** — der Angriffsweg eines
  Bildparsers (fremde Datei, präparierte Header) existiert hier nicht.

Kapselung:
  Ausschließlich `smart-system/src-tauri/src/bildschirm.rs`. Sonst nirgends.

Exit-Plan:
  Fällt `image` weg, fällt beides mit. Ein eigener JPEG-Kodierer kommt nicht
  in Frage; dann lieber wieder PNG und eine kleinere Kantenlänge.

## `maplibre-gl` 6.6.0 — optionale MapTiler-Detailkarte (28.08.2026)

Problem und Notwendigkeit:
  Die Canvas-Kugel kann eine Welttextur mathematisch korrekt darstellen, aber
  keine stufenlos lesbaren Stadt- und Landkarten liefern. MapTiler stellt
  hierfür Vektorkacheln und aktuelle Bildkarten bereit; MapLibre ist der
  schlanke, providerneutrale WebGL-Renderer. Ein eigenes Tile-Rendering wäre
  deutlich mehr Code und würde keine Kartenquelle ersetzen.

Security und Datenschutz:
  Die Bibliothek verarbeitet nur Kartenkacheln im Browser. Sie erhält weder
  Panel-Credentials noch Servermetadaten. Der Betreiber hinterlegt einen
  verschlüsselt gespeicherten, auf die Panel-Origin beschränkten MapTiler-
  Browser-Key. Der Key wird nur nach `ai.satellite.use` an die Kartenansicht
  ausgegeben; ein unbeschränkter oder serverseitiger MapTiler-Key ist verboten.

Wartung, Lizenz und Fläche:
  Version 6.6.0, BSD-3-Clause, aktives Open-Source-Projekt. Die direkte
  Abhängigkeitsfläche besteht aus 12 kleinen Geometrie-/Vektorkachel-Paketen.
  `npm audit --omit=dev` meldet keine MapLibre-Advisory; drei bereits
  vorhandene React-Router-Advisories bleiben separat offen.

Kapselung und Exit:
  Ausschließlich `MapTilerDetailMap.tsx` importiert MapLibre, dynamisch erst
  beim Hineinzoomen. Entfernen heißt: diese Komponente, die optionale
  Einstellung und die drei MapTiler-Endpunkte löschen; Sentinel und der
  bestehende Globus bleiben unverändert.

## Pipecat — für die Voice-Pipeline vorerst nicht freigegeben (28.08.2026)

Die Jarvis-Voice-Spezifikation nennt Pipecat als mögliche Orchestrierung. Die
Prüfung von `pipecat-ai` 1.8.1 (BSD-2-Clause, Python ab 3.11) hat jedoch einen
nicht lokalen Umbau des Python-Stacks ergeben: Pydantic ab 2.10.6, OpenAI SDK,
ONNX Runtime, Numba, Audio-Resampling, NLTK, Pillow, Protobuf und weitere
direkte Laufzeitpakete. MSM pinnt derzeit Pydantic 2.8.2 und benutzt für
OpenAI-kompatible Anbieter, Whisper und ElevenLabs bewusst kleine, eigene
Adapter. Ein erzwungenes Upgrade würde deren Verträge und den gesamten
Abhängigkeitsbaum gleichzeitig ändern.

Die Version 0.0.108 reduziert diese Fläche nicht; sie verlangt ebenfalls
Pydantic ab 2.10.6 sowie ONNX Runtime, Numba, Transformer und Audio-Pakete.
Deshalb wird Pipecat nicht als optionaler zweiter Voice-Pfad eingebaut. Das
würde die zentrale Tool- und Berechtigungspipeline duplizieren und bei einem
Fehler zwei voneinander abweichende Sicherheitswege schaffen.

Stattdessen bleibt der vorhandene, zentrale Voice-Run die einzige Pipeline:
Streaming-Adapter → Run-Broker → Tool-Registry/Guardian → TTS. Barge-In
schließt dort ausschließlich die Ausgabe; ein ausdrücklicher Abbruch beendet
den Run. Eine spätere Pipecat-Einführung braucht zuerst einen eigenen
Kompatibilitäts- und Migrationsentscheid für Pydantic, den Audio-Stack,
Provider-Adapter und vollständige Sicherheits-/Lasttests.
