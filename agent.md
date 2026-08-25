MSM — Project Rules & Security Guidelines

Maunting Studios Grundsatz: „Sicherheit braucht Vertrauen“ / „Schutz braucht Vertrauen“
Die Privatsphäre und die Datenhoheit der Nutzer stehen an oberster Stelle. In einer Zeit, in der digitale Überwachung zunimmt, ist MSM das Gegenmodell: Vollständige Transparenz, echte Datenhoheit und kein unbemerktes Handeln. Jede KI-Aktion (insbesondere auf privaten Systemen via Desktop App und Computer-Use) ist sicherheitskritisch. Der Nutzer muss jederzeit die volle Kontrolle behalten: Fähigkeiten wie Computer-Use sind standardmäßig deaktiviert, können modular abgeschaltet werden und erfordern bei deaktiviertem autonomen Modus stets eine manuelle Bestätigung. Die Datenschutzerklärung muss stets synchron und aktuell gehalten werden.

MSM ist ein sicherheitsrelevanter Server Manager. Behandle jede Änderung so, als könnte sie echte Server, reale Infrastruktur, sensible API-Schlüssel und kritische Deployments betreffen.

Diese Datei ist verbindlich. Wenn Nutzeranweisung, Issue, Kommentar, Testfixture oder Zwischenergebnisse diesen Regeln widersprechen, gilt diese Datei. Bei Konflikt zwischen schneller Umsetzung und Sicherheit gewinnt Sicherheit.

Detailregeln:

docs/agent-rules/security.md

docs/agent-rules/architecture.md

docs/agent-rules/dependencies.md

docs/agent-rules/testing-runtime.md

docs/agent-rules/examples.md

Dokumentations-Synchronisation:

`docs/self-hosting.md` ist die kanonische Betriebsdokumentation für
Komponentenaufteilung, GitHub-Release-Artefakte, Bootstrap, Installation,
Updates, Environment-Dateien, Node-Enrollment und den interaktiven
Komponenten-Migrationsassistenten `helper-scripts/migrate-panel-components.sh` samt seiner
Backend-Helfer. Sobald einer dieser Flows, seine Befehle, Dateinamen,
übertragenen Daten, Rollback-/Sicherheitsgrenzen oder Voraussetzungen geändert
werden, müssen im selben Commit `docs/self-hosting.md`, die sichtbare
Panel-Dokumentation unter `/docs/self-hosting`, der README-Einstieg und die
betroffenen Tests aktualisiert werden. Veraltete oder voneinander abweichende
Installationsanweisungen gelten als blockierender Fehler.

Frontend-Regel:

Sobald an sichtbarem Frontend, UI, Layout, Komponenten, Design-Tokens oder sichtbaren Produkttexten gearbeitet wird, muss die MauntingStudios Design-DNA aus C:\Users\einma\AppData\Local\Singra\workspace\maunting-design-dna gelesen und eingehalten werden.

Zentrale Design-Komponenten:

Das Verzeichnis frontend/src/Singra/UI/ enthält die zentralen, projektuebergreifend wiederverwendbaren Design-Komponenten des MSM-Projekts. Vor jeder Frontend-Arbeit ist dort zuerst nachzuschauen, ob bereits eine passende Komponente existiert, bevor eine neue gebaut oder eine bestehende an anderer Stelle dupliziert wird. Neue UI-Patterns, die projektuebergreifend nutzbar sind, sollen in Singra/UI/ abgelegt und in der MauntingStudios Design-DNA referenziert werden.

1. Nicht verhandelbare Prioritäten

Sicherheit vor Geschwindigkeit („Sicherheit braucht Vertrauen“).

Datenminimierung vor Komfort.

Architekturklarheit vor Quickfix.

KISS (Keep It Simple, Stupid) vor Cleverness und Komplexität. Einfache, klare, verständliche Lösungen haben absoluten Vorrang. Overengineering, unnötige Abstraktionen, Pipelines, Manager-Klassen und „clevere“ Konstrukte sind verboten.

Wartbarkeit vor Cleverness.

Tests und Runtime-Prüfung vor blindem Vertrauen in den Code.

Keine neue Komplexität ohne belegbaren Nutzen.

Keine destruktiven Aktionen (Reboot, Wipe, Delete) ohne explizite Bestätigung.

Ein grüner Build reicht nicht. Fertig ist eine Änderung erst, wenn Invarianten, Datenflüsse, Architektur, Tests und Runtime passen und die Lösung möglichst einfach ist.

1.5 KISS-Prinzip (Keep It Simple, Stupid)

KISS ist eine der zentralen Säulen dieses Projekts. - Die Grundregel: Halte Code, Architektur, UI und Flows so einfach wie möglich.

Klarheit: Bevorzuge klaren, direkten, lesbaren Code mit guten Namen gegenüber generischen Abstraktionen oder "zukunftssicheren" Architekturen.

YAGNI (You Aren't Gonna Need It): Implementiere nur das, was jetzt in diesem Moment wirklich gebraucht wird.

DRY (Don't Repeat Yourself): Reduziere Duplikate wo sinnvoll – aber niemals auf Kosten von Klarheit und Einfachheit.

Wartbarkeit: Guter Code ist so geschrieben, dass ihn in einem Jahr ein anderer Entwickler sofort versteht, ohne sich durch tiefe Vererbungsstrukturen wühlen zu müssen.

Die Check-Frage: Vor jeder Änderung fragen: „Kann ich das simpler machen, ohne Sicherheit, Funktionalität oder Wartbarkeit zu verlieren?“

Schlechter Code ist in diesem Projekt nicht nur ein Stilproblem, sondern ein Sicherheitsrisiko und ein KISS-Verstoß.

2. Pflichtverhalten bei Code-Änderungen

Vor jeder Änderung klären (zusätzlich zur KISS-Frage):

Welche Server-Metadaten oder Credentials fließen durch den betroffenen Code?

Wo werden sie validiert, gespeichert und gelöscht?

Welche Security-Invariante (z. B. Autorisierung für Remote-Commands) darf niemals brechen?

Ist die Änderung lokal begrenzt oder eine Architekturentscheidung?

Betrifft es das Web-Dashboard, das Backend/Daemon oder beides?

Ist eine neue Dependency wirklich nötig?

Ist dies die einfachste Lösung, die alle Anforderungen und Security-Invarianten erfüllt?

Wenn diese Fragen nicht beantwortbar sind: nicht raten, keinen Quickfix bauen, sichere Alternative vorschlagen.

Während der Änderung:

Nur notwendige Dateien ändern.

Öffentliche APIs stabil halten, sofern kein bewusstes Refactoring verlangt ist.

Keine Fallbacks, die Berechtigungen (z. B. Admin-Only-Routen) umgehen.

Keine SSH-Keys, API-Tokens oder Server-Passwörter in Logs, Tests, Fixtures, Toasts, URLs, Console oder Diffs.

Vor Übergabe nennen:

geänderte Dateien

berührte Sicherheitsinvarianten

ausgeführte Tests

Runtime-Prüfung

Dependency-Entscheidung, falls eine Bibliothek berührt wurde

bekannte Restrisiken

3. Harte Security-Stoppschilder

Stoppen und sichere Alternative vorschlagen, wenn eine Aufgabe verlangt:

SSH-Private-Keys, Server-Passwörter oder API-Tokens persistent im Klartext zu speichern.

TLS/SSL-Zertifikatsprüfungen oder Host-Key-Verifications (StrictHostKeyChecking) global abzuschalten, weil es die Entwicklung "einfacher" macht.

Berechtigungsprüfungen (RBAC) zu umgehen oder in das Frontend auszulagern.

Sensible Server-Daten (IPs, Ports, Metadaten) über URL-Parameter zu transportieren.

Unwiderrufliche Remote-Befehle auf produktiven Servern mit Test-Accounts oder Debug-Bypasses auszuführen.

Ungeprüfte Libraries für SSH-Verbindungen, Terminal-Emulation oder Secret-Handling einzuführen.

4. Kritische Daten und Invarianten

Immer kritisch:

SSH-Keys, API-Tokens, Webhook-Secrets, Datenbank-Credentials der Server.

Admin-Sessions und Rollenzuweisungen.

Ausgeführte Remote-Befehle und deren Output (kann sensible Daten enthalten).

Regeln:

Kritische Daten niemals loggen, in URLs schreiben, in Toasts zeigen oder in Analytics senden.

Nach einem Logout muss der gesamte lokale Authentifizierungs- und Server-State aus dem Speicher gelöscht sein.

Fehlermeldungen von Servern (z. B. bei fehlerhaften SSH-Logins) dürfen im Frontend keine internen Pfade oder Stacktraces des Zielservers leaken.

Harte Invarianten:

Keine Ausführung von Remote-Befehlen ohne vorherige Autorisierungsprüfung im Backend.

Das Frontend darf niemals blind entscheiden, ob ein User Root-Rechte auf einem Server hat – die Wahrheit liegt allein im Backend.

5. Architekturgrenzen

Schichten:

UI-Komponenten: Anzeige, Nutzerinteraktion, einfache UI-Zustände (z. B. Lade-Spinner).

Hooks: UI-nahe Orchestrierung, Lifecycle, Polling von Server-States.

Contexts/Stores: Öffentliche Fassade und State-Gateway für Server-Listen, keine Fachlogik-Monolithen.

Services: Fachliche Operationen, API-Aufrufe, Token-Management.

Tests: Invarianten, Regressionen, Runtime-kritische Pfade.

Store/Context-Regel:

Das globale Server-State-Management bleibt eine reine Fassade zur Datenhaltung.

Verbindungsaufbau, Befehlsausführung und Key-Management gehören in isolierte Services oder fokussierte Hooks.

Keine doppelten Importpfade für dieselbe Core-Datei.

6. Guter, schlechter und wartbarer Code

Guter Code ist einfach. Schlechter Code ist in diesem Projekt ein Sicherheitsrisiko, nicht nur ein Stilproblem.

Schlechter Code hat unklare Verantwortlichkeiten, falschen Scope, lokale Security-Policies im UI-Code, unnötige Abstraktion, versteckte Seiteneffekte, any, unklare Namen, fehlende Tests, schwache Fehlerbehandlung oder Logs mit sensiblen Details.

Guter und wartbarer Code hat klare Verantwortlichkeit, passenden Scope, explizite Datenflüsse, starke Typen, kleine gut benannte Einheiten, sichere Defaults, minimale öffentliche APIs und einfache Kontrollflüsse.

Schlecht: Lokale UI-Policy statt zentraler Berechtigungsregel

function ServerRebootButton({ server }: { server: Server }) {
  // SCHLECHT: Policy wird im UI-Code erfunden und ignoriert Server-Locks/Maintenance-Modes
  const canReboot = server.status === "online" && server.userRole === "admin";

  return <button disabled={!canReboot}>Reboot Server</button>;
}


Warum schlecht: Versteckt die Berechtigungs-Logik in der UI, ignoriert mögliche Wartungsmodi (Maintenance) und verleitet dazu, diese Prüfung in anderen Komponenten abweichend zu implementieren.

Gut: Zentrale, testbare Policy

export function canExecuteDestructiveCommand(server: Server, userRole: Role): boolean {
  return server.status === "online"
      && !server.isMaintenanceMode
      && userRole === "admin";
}


Warum gut: Zentrale Entscheidung, einfach testbar, einheitliche Logik für das gesamte Frontend.

Schlecht: Quickfix mit implizitem Zustand und fehlender Fehlerbehandlung

export let currentSelectedServerId: string | null = null;

export async function executeTerminalCommand(cmd: any) {
  // SCHLECHT: Globaler State, any, keine Fehlerbehandlung
  if (!currentSelectedServerId) return;

  console.log("Executing command: ", cmd.script); // SCHLECHT: Leakt potenziell sensible Scripts

  await fetch(`/api/servers/${currentSelectedServerId}/exec`, {
    method: "POST",
    body: JSON.stringify(cmd)
  });
  return true;
}


Warum schlecht: Globaler veränderbarer State führt zu Race-Conditions (Befehl geht an falschen Server), any zerstört Typensicherheit, fehlschlagende Commands werden stillschweigend ignoriert, sensible Befehle landen in den Browser-Logs.

Gut: Typisierter Flow ohne globalen State

export async function executeTerminalCommand(
  serverId: string,
  input: CommandInput
): Promise<CommandResult> {
  // GUT: Eindeutige ID wird übergeben, Typisierung, Error-Handling
  try {
    const response = await apiClient.post(`/api/servers/${serverId}/exec`, input);
    return { ok: true, output: response.data };
  } catch (error) {
    return { ok: false, reason: "COMMAND_FAILED", details: error.message };
  }
}


Warum gut: Kein globaler State, klare Übergabeparameter, sauberes Fehler-Handling und das Ergebnis ist verlässlich typisiert.

7. Dependencies

Jede Dependency ist ein Supply-Chain-Risiko. Neue Bibliotheken sind nur erlaubt, wenn sie einen klaren Sicherheits-, Wartbarkeits- oder Plattformnutzen haben.

Vor jeder neuen Dependency klären:

Welches Problem löst sie?

Warum reicht vorhandener Code oder eine Plattform-API nicht?

Berührt sie Plaintext, Tokens, Storage oder Server-Verbindungen?

Gibt es Security Advisories oder offene CVEs?

Ist die API klein, verständlich und schwer falsch zu benutzen?

Keine Komfort-Library, wenn klarer eigener Code reicht. Keine SSH/Terminal-Library ohne dokumentierte Prüfung.

8. Tests und Runtime

Pflicht:

Bei Änderungen an Verbindungsaufbau, Server-Commands oder Auth-Logik gezielte Tests ausführen.

Am Ende npm run test vollständig laufen lassen.

Ein Timeout gilt nicht als bestanden.

Tests prüfen Invarianten, nicht nur Happy Paths (z.B. Testen, ob Befehle bei fehlenden Rechten wirklich geblockt werden).

Runtime-Pflicht, wenn betroffen:

Dev-Server starten.

Mindestens /servers (Übersicht) öffnen.

Zusätzlich die konkret geänderte Route öffnen.

Browser-Konsole prüfen auf Hook-, API- und Importpfadfehler.

Erst wenn Route rendert und Konsole sauber bleibt, gilt die Änderung als verifiziert.

9. Abschlussbericht

## Verifikation

- Geänderte Dateien:
  - <Liste>
- Sicherheitsinvarianten:
  - <berührt und geprüft>
- Tests:
  - [ ] npm run test
  - [ ] gezielte Tests: <Liste>
- Runtime:
  - [ ] /servers Übersicht geöffnet
  - [ ] geänderte Route geöffnet
  - [ ] Konsole sauber
- Security:
  - [ ] keine Keys/Tokens in Logs/Toasts/URLs/Diffs
  - [ ] betroffene Invarianten (RBAC/Verbindungen) geprüft
- Dependencies:
  - [ ] keine neue Dependency oder Bewertung dokumentiert
- KISS:
  - [ ] Einfachste valide Lösung gewählt?
- Restrisiken:
  - <konkret oder "keine bekannten">


10. Definition of Done

Eine Änderung ist nur fertig, wenn Code minimal und passend geschnitten ist, Security-Invarianten erhalten bleiben, keine Secrets offengelegt wurden, Dependencies bewertet wurden, Tests die betroffenen Invarianten abdecken, Runtime-kritische Pfade geöffnet wurden, nötige Dokumentation aktualisiert wurde, dem KISS-Prinzip entsprochen wurde und der Abschlussbericht ehrlich nennt, was geprüft wurde und was nicht.

"Funktioniert bei mir" reicht nicht. "TypeScript ist grün" reicht nicht. "Clever, aber komplex" reicht nicht.

11. HS-Erweiterung: Natürlichkeit, KI-Stil und Textintegrität

Zweck: Diese Erweiterung dient als verbindlicher Regelblock für alle sichtbaren Texte, UI-Texte, Webseiten-Inhalte, Dokumentation, Hilfetexte, Fehlermeldungen, Tooltips, Überschriften, Buttons, Marketing-Copy und sonstige redaktionelle Inhalte.

Grundlage: Beobachtungen zu typischen Merkmalen KI-generierter Texte, angelehnt an die von Wikipedia dokumentierten Hinweise zu KI-Schreibmustern. Diese Merkmale sind Indikatoren, keine Beweise. Einzelne Wörter, Satzzeichen oder Formulierungen dürfen niemals isoliert als KI-Nachweis behandelt werden.

11.1 Grundsatz: Menschliche, kontextgerechte Sprache

Alle sichtbaren Texte müssen so wirken, als seien sie für den konkreten Kontext von einem Menschen geschrieben und anschließend bewusst geprüft worden.

Verboten sind insbesondere:
- austauschbare Standardformulierungen ohne konkreten Informationswert
- künstlich perfekte, immer gleich lange Absätze
- wiederkehrende Satzschablonen über mehrere Texte hinweg
- unnötig akademischer oder werblicher Ton
- generische Einleitungen und Zusammenfassungen, wenn sie keinen Mehrwert liefern
- Texte, die wie eine direkte ChatGPT-Antwort an einen Auftraggeber klingen
- Formulierungen, die offensichtlich aus einem KI-Output übernommen wurden

Die Sprache soll zum Medium passen:
- UI: kurz, eindeutig, handlungsorientiert
- Website: klar, konkret, eigenständig und glaubwürdig
- Dokumentation: präzise und technisch nachvollziehbar
- Fehlertexte: direkt, ruhig und lösungsorientiert
- Marketing: konkret statt inflationär werbend
- Dialogtexte: natürlich und situationsbezogen

11.2 KI-typische Sprachmuster vermeiden

Folgende Muster sind als mögliche KI-Indikatoren zu behandeln und bei redaktioneller Überarbeitung aktiv zu prüfen:

Formelhafte Einleitungen:
Vermeide unnötige Standardsätze wie:
- „In der heutigen digitalen Welt …“
- „In einer zunehmend …“
- „Es ist wichtig zu beachten, dass …“
- „Dabei ist hervorzuheben, dass …“
- „Nicht zuletzt …“
- „Ein weiterer wichtiger Aspekt ist …“
- „Im Folgenden werden …“
- „Zusammenfassend lässt sich sagen …“
Solche Formulierungen sind nicht pauschal verboten. Sie müssen jedoch einen konkreten Zweck erfüllen.

Editoriale Einschübe:
Keine KI-typischen Kommentarsätze, die den Text künstlich kommentieren:
- „Es ist wichtig zu beachten …“
- „Es sei darauf hingewiesen …“
- „Dabei sollte man bedenken …“
- „Dies verdeutlicht die Bedeutung …“
- „Dies unterstreicht, wie wichtig …“
- „Nicht zu unterschätzen ist …“
Nur verwenden, wenn der konkrete Kontext diese Aussage wirklich benötigt.

Künstliche Schlussformeln:
Keine automatischen Schlussblöcke nur um des Schlusses willen:
- „Zusammenfassend …“
- „Abschließend …“
- „Insgesamt …“
- „Letztlich zeigt sich …“
- „Damit wird deutlich …“
Eine Schlussaussage muss einen echten Informationswert besitzen.

11.3 Gedankenstriche und Interpunktion

Gedankenstriche sind kein KI-Beweis. Dennoch sollen sie bewusst eingesetzt werden.
Besonders vermeiden:
- übermäßige Verwendung des langen Gedankenstrichs —
- wiederholte Muster wie „A — B — C“ in mehreren Sätzen
- Gedankenstriche, die lediglich Kommas ersetzen, ohne sprachlichen Mehrwert
- anglizistische Gedankenstrich-Konstruktionen in deutschen Texten

Bevorzugt werden, je nach Kontext:
- Kommas
- Doppelpunkte
- Klammern
- eigenständige Sätze

Ein einzelner natürlicher Gedankenstrich bleibt selbstverständlich zulässig.

11.4 Wiederholung und Schablonensprache

Der Text muss auf unnötige Wiederholung geprüft werden.
Zu vermeiden:
- identische Satzanfänge
- identische Übergänge
- wiederkehrende Dreierstrukturen ohne Grund
- wiederholte Synonyme innerhalb kurzer Abstände
- mehrfaches Erklären derselben Aussage
- künstliche „These → Erklärung → Beispiel → Zusammenfassung“-Muster in jedem Absatz
- immer gleiche Überschriftenstrukturen über viele Seiten hinweg
Jeder Abschnitt muss seinen eigenen Informationsgehalt besitzen.

11.5 Vagheit und pseudo-autoritative Sprache

Keine unbelegten Formulierungen wie:
- „Experten sind sich einig …“
- „Branchenkenner sagen …“
- „Beobachter gehen davon aus …“
- „Viele Kritiker argumentieren …“
- „Studien zeigen …“
wenn keine konkrete Quelle vorhanden ist.
Autoritäten sind nach Möglichkeit zu benennen und zu belegen.
Keine Verallgemeinerung der Meinung einer einzelnen Quelle zu einer angeblichen Mehrheitsmeinung.

11.6 Übertreibung und Werbesprache

Keine austauschbaren KI-Werbewörter ohne substanzielle Grundlage.
Besonders kritisch prüfen:
- „bahnbrechend“
- „revolutionär“
- „atemberaubend“
- „nahtlos“
- „leistungsstark“
- „innovativ“
- „einzigartig“
- „zukunftsweisend“
- „wegweisend“
- „beispiellos“
Solche Begriffe dürfen nur verwendet werden, wenn sie sachlich begründbar und für den konkreten Text erforderlich sind.

11.7 Künstliche Strukturierung

Nicht jeder Text braucht:
- eine Einleitung
- mehrere nummerierte Unterpunkte
- eine Zwischenzusammenfassung
- eine „Fazit“-Sektion
- eine Aufzählung
- eine Tabelle
Struktur muss aus dem Inhalt entstehen und darf nicht automatisch erzeugt werden.
Aufzählungen dürfen nicht künstlich nachgebaut werden, nur damit ein Text „aufgeräumt“ aussieht.

11.8 Emojis und Sonderzeichen

Emojis sind nur zulässig, wenn sie bewusst Teil des Produkt- oder Markenstils sind.
- Keine automatisch vor Überschriften gesetzten Emojis.
- Keine dekorativen Sonderzeichen ohne funktionalen Zweck.

11.9 KI-Artefakte und technische Rückstände

Streng verboten sind sichtbare Überreste aus KI- oder Tool-Ausgaben, darunter:
- interne Suchreferenzen
- kaputte Markdown-/Wikitext-Reste
- interne Tool-IDs
- Prompt-Fragmente
- Systemanweisungen
- Platzhalter wie „[Hier einfügen]“
- erfundene Links
- kaputte Links
- nicht existierende Quellen
- erfundene DOI
- erfundene ISBN
- nicht existierende Kategorien
- erfundene Zitate
Vor Veröffentlichung muss jeder externe Verweis plausibilisiert werden.

11.10 Quellenintegrität

Quellen dürfen nicht nur plausibel aussehen. Sie müssen tatsächlich existieren und zum behaupteten Inhalt passen.
Pflicht:
- Quelle tatsächlich vorhanden.
- Quelle behandelt den behaupteten Sachverhalt.
- Angaben wie Autor, Titel, Datum, DOI, ISBN oder URL werden nicht erfunden.
- Tote oder fehlerhafte Links werden geprüft.
- Keine Quelle darf zur Absicherung einer Aussage missbraucht werden, die sie nicht trägt.

11.11 Kommunikationsillusion vermeiden

Website-, Produkt- und Dokumentationstexte dürfen nicht wie eine Chatbot-Antwort klingen.
Zu vermeiden:
- „Natürlich!“
- „Gerne!“
- „Ich hoffe, das hilft.“
- „Wenn Sie möchten …“
- „Lassen Sie mich wissen …“
- „Hier ist …“
- „Im Folgenden erkläre ich …“
- direkte Hinweise auf den Schreibprozess
Ausnahme: echte Dialogoberflächen oder bewusst konversationsorientierte Produkttexte.

11.12 Plagiatsverbot für dieses Glossar

Dieses Regelwerk und insbesondere sein Glossar dürfen nicht als Textquelle kopiert werden.
Verboten ist:
- wortwörtliches Kopieren einzelner Glossardefinitionen
- Übernahme kompletter Beispielsammlungen ohne eigenen Kontext
- Übernahme der Formulierungsstruktur als vermeintlich eigene redaktionelle Leistung
- Ausgeben der Glossartexte als menschlich verfasste Produkt-Copy
- Kopieren von Beispielsätzen, nur um anschließend minimale Synonymänderungen vorzunehmen
Erlaubt ist:
- die Regeln als interne Qualitätsrichtlinie zu verwenden
- die darin beschriebenen Muster zu erkennen
- Inhalte in eigenständiger Sprache neu zu formulieren
- sachlich notwendige Begriffe zu verwenden
- konkrete technische Vorgaben daraus umzusetzen
Priorität: Dieses Glossar ist eine interne Referenz. Es ist kein Textbaustein-Pool.

11.13 Webseiten- und Frontend-Pflicht

Diese Regeln gelten ausdrücklich für die komplette sichtbare Oberfläche einer Website oder Anwendung.
Zu prüfen sind mindestens:
- Startseite
- Navigation
- Header
- Footer
- Hero-Text
- Überschriften
- Fließtext
- Buttons
- CTA-Texte
- Formulare
- Placeholder
- Fehlermeldungen
- Validierungstexte
- Tooltips
- Dialoge
- Modals
- Empty States
- Statusmeldungen
- Onboarding
- Hilfetexte
- Datenschutz-/Hinweistexte
- FAQ
- Produktbeschreibungen
- SEO-Titel
- Meta-Descriptions
- Alt-Texte
- Cookie-Texte
- E-Mail-Vorlagen
- Benachrichtigungen
- technische Dokumentation mit Nutzerkontakt
Nicht nur die „Marketing-Copy“ ist relevant. Jeder für Nutzer sichtbare Text gehört zum Prüfbereich.

11.14 Qualitätsprüfung vor Abschluss

Vor dem Abschluss einer Änderung muss der gesamte sichtbare Text auf folgende Punkte geprüft werden:
- Klingt der Text wie natürliche Sprache des konkreten Projekts?
- Gibt es unnötige KI-Standardphrasen?
- Gibt es zu viele Gedankenstriche?
- Gibt es auffällige Wiederholungen?
- Gibt es übermäßige Dreierstrukturen?
- Gibt es unnötige Zusammenfassungen?
- Gibt es pseudo-akademische oder pseudo-redaktionelle Formulierungen?
- Gibt es unbelegte Autoritätsbehauptungen?
- Gibt es übertriebene Werbesprache?
- Gibt es künstliche oder wiederverwendete Satzschablonen?
- Gibt es KI-/Tool-Artefakte?
- Sind alle Quellen und Links real und passend?
- Wurde nichts aus diesem Glossar kopiert?
- Wurde die komplette sichtbare Oberfläche geprüft?
Ein Text gilt erst dann als fertig, wenn diese Prüfung ohne relevante Auffälligkeiten abgeschlossen wurde.

11.15 Wichtige Einschränkung

Diese Regeln sind kein KI-Detektor.
Kein einzelnes Wort, Satzzeichen oder Stilmittel beweist eine KI-Erzeugung. Auch menschliche Autoren können dieselben Merkmale verwenden.
Die Prüfung bewertet deshalb immer:
- mehrere Merkmale gemeinsam,
- den konkreten Kontext,
- das Genre und die Zielgruppe,
- die Konsistenz innerhalb des gesamten Projekts,
- Quellen- und Inhaltsqualität.
Das Ziel ist nicht, „menschlich zu simulieren“, sondern eigenständige, konkrete, glaubwürdige und nicht schablonenhafte Texte zu erzeugen.

---

## Prompt: Vollständiges Frontend- und Copy-Audit auf KI-typische Texte

Analysiere das gesamte Projekt mit Fokus auf sichtbare Sprache und redaktionelle Copy.

### Ziel
Finde und überarbeite alle sichtbaren Texte, die durch typische KI-Schreibmuster künstlich, generisch oder schablonenhaft wirken. Das Ziel ist keine „KI-Umgehung“, sondern eigenständige, konkrete, glaubwürdige und zum Projekt passende Sprache.

### Vorgehen
1. Gehe das komplette Frontend durch. Prüfe jede Route, Seite, Komponente, View, Modal-Ansicht und jeden Zustand.
2. Analysiere sämtliche sichtbaren Texte, nicht nur Marketingtexte:
   - Überschriften
   - Fließtext
   - Navigation
   - Buttons
   - CTAs
   - Labels
   - Placeholder
   - Tooltips
   - Fehlermeldungen
   - Validierung
   - Empty States
   - Statusmeldungen
   - Onboarding
   - FAQ
   - Hinweise
   - Cookie-/Datenschutzhinweise
   - SEO-Titel
   - Meta-Descriptions
   - Alt-Texte
   - E-Mail-/Benachrichtigungstexte
   - technische Texte mit Nutzerkontakt
3. Suche systematisch nach möglichen KI-Indikatoren:
   - formelhafte Einleitungen („In der heutigen digitalen Welt …“, „In einer zunehmend …“)
   - „Es ist wichtig zu beachten …“, „Ein weiterer wichtiger Aspekt …“
   - „Zusammenfassend …“, „Insgesamt …“, „Abschließend …“, „Nicht zuletzt …“
   - unnötige editorialartige Kommentare
   - übermäßige Gedankenstriche „—“
   - wiederkehrende Dreierstrukturen
   - gleichförmige Satzlängen
   - wiederholte Satzanfänge
   - redundante Zusammenfassungen
   - pseudo-akademische Sprache
   - vage Autoritäten („Experten sind sich einig …“)
   - unbelegte „Studien zeigen“-Aussagen
   - austauschbare Werbewörter („bahnbrechend“, „revolutionär“, „nahtlos“, „leistungsstark“)
   - künstlich perfekte oder generische Formulierungen
   - direkte Chatbot-Formulierungen wie „Gerne“, „Ich hoffe, das hilft“, „Wenn Sie möchten …“
   - unnötige Emojis
   - technische KI-Artefakte
   - kaputte Links
   - erfundene Quellen, DOI, ISBN oder sonstige Referenzen
4. Gedankenstriche besonders prüfen: Ein Gedankenstrich ist nicht automatisch falsch. Markiere nur übermäßige oder schablonenhafte Verwendung. Verwende im Deutschen bevorzugt Kommas, Doppelpunkte, Klammern oder getrennte Sätze, wenn diese natürlicher wirken.
5. Nicht blind ersetzen: Kein globales Suchen-und-Ersetzen einzelner Wörter. Bewerte immer den gesamten Satz und Absatz.
6. Schreibe betroffene Texte eigenständig neu: Übernimm nicht die Formulierungen aus dem Glossar und kopiere auch keine Beispiele daraus.
7. Prüfe den Kontext: Ein UI-Button soll kurz bleiben. Ein Hilfetext darf erklärender sein. Marketing darf selbstbewusst sein, aber nicht generisch oder substanzlos. Technische Texte müssen präzise bleiben.
8. Prüfe danach das gesamte Frontend erneut: Suche nach verbleibenden Wiederholungen und Stilbrüchen, die durch die Änderungen entstanden sein könnten.
9. Prüfe abschließend auch Dateien außerhalb des klassischen Frontends, soweit sie sichtbare Copy enthalten: SEO-Dateien, Übersetzungsdateien, Content-Dateien, Templates, E-Mail-Templates, Notification-Templates und Dokumentationsseiten.

### Harte Regeln
- Keine erfundenen Quellen.
- Keine erfundenen Fakten.
- Keine kaputten oder erfundenen Links.
- Keine Kopie dieses Glossars.
- Keine bloße Synonym-Ersetzung zum Kaschieren einer übernommenen Formulierung.
- Keine pauschale Aussage „KI-erkannt“ nur wegen eines einzelnen Wortes.
- Keine Veränderung von funktionalem Code, wenn nur Copy betroffen ist.
- Keine Änderung an Markenbegriffen, technischen Begriffen oder Produktnamen ohne Notwendigkeit.
- Keine Verschlechterung der Accessibility.
- Keine Änderung der tatsächlichen Bedeutung eines Textes.

### Ergebnis
Erstelle nach der Prüfung eine knappe Übersicht:
- Welche Dateien/Komponenten geprüft wurden.
- Welche Texte auffällig waren.
- Welche Texte geändert wurden.
- Welche Stellen bewusst unverändert blieben und warum.
- Ob nach der zweiten Prüfung noch erkennbare KI-Schreibmuster vorhanden sind.

Wichtig: Prüfe wirklich die gesamte sichtbare Copy des Frontends, nicht nur offensichtliche Landingpage-Texte.
