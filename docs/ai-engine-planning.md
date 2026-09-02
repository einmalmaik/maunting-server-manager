Maunting Service Manager v4.0 — Zielbild

Dokumenttyp: Produkt-, Funktions- und ErgebnisbeschreibungZielversion: MSM v4.0Status: Verbindliches Zielbild für die spätere technische PlanungZweck: Dieses Dokument beschreibt, was MSM v4.0 am Ende können und wie sich das System verhalten soll. Es legt noch nicht verbindlich fest, mit welchen Datenbanktabellen, Klassen, Endpunkten oder einzelnen Programmbibliotheken das Ergebnis umgesetzt wird.

1. Gesamtziel

Maunting Service Manager soll sich mit v4.0 von einem reinen Server-Panel zu einer intelligenten, verteilten und für Hoster nutzbaren Server-Management-Plattform weiterentwickeln.

Der Kern von MSM bleibt dabei erhalten:

MSM bleibt vollständig selbst hostbar.

Bestehende Self-Hosted-Funktionen dürfen nicht entfernt oder künstlich eingeschränkt werden.

Server werden weiterhin über das vorhandene Blueprint-System erstellt und betrieben.

Das Panel bleibt die zentrale Verwaltungsoberfläche.

Nutzerrechte und Serverrechte bleiben die verbindliche Sicherheitsgrenze.

Die einzelnen Server laufen auf angebundenen Hosts beziehungsweise Nodes.

Der Guardian- und Lifecycle-Bereich bleibt für den tatsächlichen Serverbetrieb zuständig.

Neu hinzukommen drei große Hauptbereiche:

ein vollständiges Kubernetes- und Multi-Host-Zielsystem;

eine tief integrierte KI für Einrichtung, Konfiguration und Diagnose;

eine einfach nutzbare Hoster- und Shop-Anbindung zur automatischen Bereitstellung von Servern und Benutzerzugängen.

Alle drei Bereiche müssen miteinander verbunden sein. Sie dürfen keine voneinander getrennten Parallelwelten bilden.

2. Zielpunkt 0 — Vollständiges Kubernetes- und Multi-Host-System

MSM v4.0 soll als vollständige Multi-Host-Plattform ausgelegt sein.

Das bedeutet, dass ein zentrales MSM-Panel mehrere physische oder virtuelle Hosts verwalten kann. Auf diesen Hosts werden die eigentlichen Gameserver und weitere verwaltete Dienste ausgeführt.

Kubernetes soll dabei als vollständige Plattformgrundlage berücksichtigt werden. Das Ziel ist kein kleiner Zusatz oder ein einzelnes Deployment-Beispiel, sondern eine Umgebung, in der MSM kontrolliert und nachvollziehbar über mehrere Hosts skalieren kann.

2.1 Gewünschtes Endergebnis

Am Ende soll ein Betreiber:

mehrere Hosts oder Nodes mit einem zentralen Panel verbinden können;

Server automatisch auf geeigneten Hosts erstellen lassen;

sehen können, auf welchem Host ein Server läuft;

freie und belegte Ressourcen der Hosts sehen;

Hosts hinzufügen, warten, sperren oder entfernen können;

Server kontrolliert zwischen Hosts verschieben können;

Ausfälle einzelner Hosts erkennen können;

den gewünschten Serverzustand zentral verwalten können;

Serveraufgaben zuverlässig an den richtigen Host übermitteln können;

den Status der Aufgaben und Server wieder im Panel erhalten;

neue Kapazität hinzufügen können, ohne die gesamte Plattform neu aufzubauen.

2.2 Kubernetes-Ziel

Das Kubernetes-System soll MSM ermöglichen:

das zentrale Panel und notwendige Plattformdienste reproduzierbar bereitzustellen;

mehrere Worker-Hosts einzubinden;

Dienste kontrolliert zu verteilen;

Zustände auch nach Neustarts oder Ausfällen wiederherzustellen;

Updates und Rollouts kontrolliert durchzuführen;

interne Kommunikation zwischen Panel, Workern und Diensten abzusichern;

Secrets nicht unkontrolliert in Konfigurationen oder Logs offenzulegen;

Ressourcenbegrenzungen durchzusetzen;

Dienste und Gameserver nachvollziehbar zu überwachen.

Das konkrete technische Kubernetes-Design wird später separat festgelegt. Dieses Zielbild schreibt nur vor, dass das Endergebnis als vollständige, wartbare Multi-Host-Plattform funktionieren muss.

2.3 Zentrale Steuerung und verteilte Ausführung

Das zentrale Panel entscheidet:

welcher Benutzer welche Aktion ausführen darf;

welcher Server erstellt, geändert oder gelöscht werden soll;

welcher Host für einen Server geeignet ist;

welche Ressourcen ein Server erhalten darf;

welche Konfiguration und welches Blueprint verwendet werden;

welcher gewünschte Zustand gelten soll.

Die Hosts führen die erlaubten Aufgaben aus und melden ihren Zustand zurück.

Das zentrale Panel darf sich nicht darauf verlassen, dass ein Host allein für Rechte und Besitzverhältnisse sorgt. Die verbindliche Prüfung findet weiterhin zentral statt.

2.4 Ereignis- und Pub/Sub-System

Das verteilte System soll ereignisorientiert arbeiten.

Serveraktionen, KI-Aktionen, Provisionierung, Statusänderungen und Host-Ereignisse sollen über ein gemeinsames, nachvollziehbares Aufgaben- und Ereignissystem verbunden sein.

Das gewünschte Ergebnis ist:

Das Panel kann eine Aufgabe einstellen.

Die zuständige Komponente übernimmt sie.

Der Bearbeitungsstatus ist sichtbar.

Fehler gehen nicht verloren.

Aufgaben können nach einem Neustart weitergeführt oder sauber als fehlgeschlagen markiert werden.

Doppelte Nachrichten erzeugen nicht versehentlich doppelte Server.

Nutzer, KI und externe Hoster-API sehen denselben tatsächlichen Zustand.

Statusänderungen können an andere Systeme weitergegeben werden.

Ob dafür intern Pub/Sub, Queues, Events oder eine Kombination verwendet wird, wird in der technischen Planung entschieden. Wichtig ist das einheitliche Ergebnis.

3. Zielpunkt 1 — Integrierte intelligente KI

MSM soll eine echte integrierte KI erhalten.

Die KI soll nicht nur ein Chatfenster mit allgemeinem Wissen sein. Sie soll den Serverkontext verstehen und innerhalb der erlaubten MSM-Funktionen bei der tatsächlichen Serververwaltung helfen.

3.1 Hauptaufgaben der KI

Die KI soll Benutzer unterstützen bei:

dem Erstellen eines neuen Servers;

der Auswahl eines passenden Spiels oder Blueprints;

der Einrichtung eines Servers;

der Auswahl sinnvoller Ressourcen;

dem Einstellen von Serverkonfigurationen;

dem Lesen und Verstehen von Konfigurationsdateien;

dem Erkennen fehlerhafter oder widersprüchlicher Einstellungen;

dem Auswerten von Logs;

dem Erkennen typischer Start-, Mod-, Netzwerk- und Konfigurationsfehler;

dem Installieren und Verwalten unterstützter Mods;

dem Prüfen von Modabhängigkeiten;

dem Erkennen ausstehender Modupdates;

dem Erstellen von Backups;

dem Vorbereiten von Änderungen;

dem Neustarten oder Stoppen eines Servers;

dem Erklären von Fehlern in verständlicher Sprache;

dem Vorschlagen sicherer Lösungsschritte;

dem Wiederverwenden bewährter Abläufe.

3.2 Vernünftiger Chat

Der Chat soll:

längere Unterhaltungen unterstützen;

Gespräche dauerhaft speichern können;

nach einem Neustart des Panels weiter verfügbar sein;

mehrere getrennte Unterhaltungen erlauben;

globale und serverbezogene Unterhaltungen unterstützen;

Dateien, Logs, Konfigurationen und Bilder als Anhänge erlauben;

Antworten streamen;

laufende Aktionen anzeigen;

Rückfragen stellen, wenn notwendige Angaben fehlen;

Ergebnisse verständlich und nicht nur als Rohdaten darstellen;

geplante Änderungen vor der Ausführung sichtbar machen;

den Benutzer nicht mit internen technischen Agenteninformationen überladen.

Die KI soll ältere Gesprächsteile sinnvoll zusammenfassen können, damit ein langer Chat weitergeführt werden kann, ohne bei jeder Nachricht die vollständige Historie erneut zu übertragen.

3.3 Serverkontext

Wenn der Benutzer die KI innerhalb eines Servers öffnet, soll sie automatisch den zulässigen Kontext dieses Servers kennen.

Dazu können gehören:

Spiel und Blueprint;

aktueller Status;

Host oder Node;

Ressourcenlimits;

Ports;

relevante Konfigurationsdateien;

vorhandene Mods;

bekannte Fehler;

Guardian-Ereignisse;

Backups;

die letzten relevanten Logzeilen;

geplante und bereits ausgeführte KI-Änderungen.

Die KI darf nur Informationen erhalten, die der angemeldete Benutzer selbst sehen darf.

3.4 Rechte des Benutzers

Die KI handelt niemals mit eigenen unbegrenzten Rechten.

Sie übernimmt die Rechte des Benutzers, der mit ihr arbeitet.

Beispiele:

Darf der Benutzer Logs sehen, darf die KI für ihn Logs auswerten.

Darf der Benutzer keine Dateien ändern, darf die KI keine Config anwenden.

Darf der Benutzer den Server nur starten, aber nicht löschen, darf die KI ihn ebenfalls nicht löschen.

Darf der Benutzer einen fremden Server nicht sehen, darf die KI diesen Server ebenfalls nicht abrufen.

Eine manipulierte Nachricht oder ein Jailbreak darf diese Grenze nicht umgehen.

Die tatsächliche Rechteprüfung muss im Server- und Panel-System stattfinden. Die Aussage des Sprachmodells allein darf nie als Berechtigung gelten.

3.5 Keine ungeprüfte Programmcode-Ausführung

Die KI soll keinen frei generierten und ungeprüften Programmcode als Serveraktion ausführen.

Nicht gewünscht sind:

beliebige generierte Bash-Skripte;

frei generierter Python-Code;

ungeprüfte Systembefehle;

vom Modell erfundene Administratorbefehle;

direkter Hostzugriff;

ein allgemeines KI-Terminal mit Root-Rechten.

Die KI soll stattdessen vorhandene, klar definierte MSM-Funktionen verwenden.

Sie darf beispielsweise sagen:

Server starten;

Backup erstellen;

Config lesen;

Änderungsvorschlag erzeugen;

geprüfte Configänderung anwenden;

unterstützte Mod installieren;

Logs abrufen.

Die Ausführung erfolgt weiterhin durch die dafür vorgesehenen MSM-Dienste.

3.6 Änderungen, Vorschau und Sicherheit

Bei Änderungen soll die KI möglichst zeigen:

was geändert wird;

warum es geändert wird;

welche Datei oder Einstellung betroffen ist;

welcher vorherige und neue Wert gilt;

welche Folgen erwartet werden;

ob ein Neustart erforderlich ist.

Vor riskanten Änderungen soll ein Snapshot oder Backup möglich sein.

Wenn sich eine Datei nach der Analyse durch einen anderen Nutzer verändert hat, darf die KI nicht blind ihre alte Änderung darüber schreiben.

3.7 Unterstützter und autonomer Modus

Der Standardmodus ist ein unterstützter Modus.

In diesem Modus:

analysiert die KI;

schlägt Aktionen vor;

zeigt Änderungen;

wartet auf die Bestätigung des Benutzers.

Optional soll ein autonomer Modus verfügbar sein.

Dieser darf nur:

ausdrücklich aktiviert werden;

durch Rollen und Rechte erlaubt sein;

definierte nicht destruktive Aufgaben selbst ausführen;

durch Sicherheitsgrenzen beschränkt sein.

Bestimmte Aktionen müssen immer manuell bestätigt werden:

Server löschen;

vollständiger Wipe;

Neuinstallation;

Wiederherstellung eines Backups;

Wechsel des Spiels oder Blueprints;

Rotation oder Änderung sensibler Secrets;

Änderung von Benutzerrechten.

3.8 Anbieter und eigene API-Keys

Die KI soll unterschiedliche Modellanbieter verwenden können.

Gewünscht sind:

OpenRouter als zentrale Möglichkeit für viele Modelle;

direkte Anbieter;

lokale oder selbst betriebene Modelle;

OpenAI-kompatible Endpunkte;

Bring Your Own Key;

ein durch den Betreiber bezahltes Modell;

eine gemischte Nutzung aus Betreiber-Key und User-Key.

API-Keys müssen geschützt gespeichert werden und dürfen nicht in Logs, Antworten oder Frontend-Zuständen auftauchen.

Das UI soll nicht fest an eine kurze, schnell veraltete Liste bestimmter Modellnamen gebunden sein.

4. Zielpunkt 2 — Langzeitgedächtnis und Skills

Die KI soll nicht jede wiederkehrende Aufgabe vollständig neu erlernen müssen.

4.1 Gedächtnis

Die KI soll sich auf Wunsch merken können:

bevorzugte Spiele oder Blueprints;

typische RAM-Zuweisungen;

bevorzugte Hosts oder Regionen;

Wartungszeiten;

bekannte Besonderheiten eines Servers;

bevorzugte Modpacks;

gewünschte Antwortsprache;

wiederkehrende Einstellungen.

Das Gedächtnis muss:

vom Benutzer einsehbar sein;

bearbeitet werden können;

gelöscht werden können;

abschaltbar sein;

keine API-Keys, Passwörter oder andere Secrets speichern;

zwischen Benutzer-, Server- und Panelkontext unterscheiden.

4.2 Skills

Ein Skill ist ein wiederverwendbarer, nachvollziehbarer Arbeitsablauf.

Beispiele:

einen bestimmten Minecraft-Server nach den bevorzugten Regeln einrichten;

ein bekanntes Modpack installieren;

nach einem typischen Fehler bestimmte Logs prüfen;

vor einem Update Backup, Update und Healthcheck durchführen;

einen neuen Server nach den Standards des Hosters vorbereiten.

Skills sollen:

aus erlaubten MSM-Aktionen bestehen;

versioniert werden;

sichtbar und abschaltbar sein;

geprüft werden;

die Rechte des ausführenden Benutzers beachten;

keine freien Skripte oder versteckten Programme enthalten;

bei riskanten Schritten weiterhin Bestätigungen verlangen.

5. Zielpunkt 3 — Multi-Role-System

MSM soll ein Discord-artiges System mit mehreren Rollen pro Benutzer erhalten.

5.1 Gewünschtes Verhalten

Ein Benutzer kann gleichzeitig mehrere Rollen besitzen.

Beispiel:

User;

AI-VIP;

Support;

Server-Operator.

Die Rechte der Rollen werden gemeinsam ausgewertet.

Eine zusätzliche Rolle ergänzt Rechte. Sie ersetzt nicht zwangsläufig alle anderen Rollen.

5.2 Rollen und Serverrechte

Es soll weiterhin einen Unterschied geben zwischen:

panelweiten Rollen;

serverbezogenen Rechten.

Eine globale Supportrolle darf beispielsweise bestimmte Verwaltungsbereiche sehen, ohne automatisch Eigentümer aller Server zu werden.

Ein Kunde kann für seinen gemieteten Server die notwendigen Rechte erhalten, ohne dieselben Rechte auf fremden Servern zu besitzen.

5.3 KI-Rechte

Rollen sollen unter anderem steuern können:

ob ein Benutzer den KI-Chat verwenden darf;

ob Anhänge erlaubt sind;

ob Memory verwendet werden darf;

ob Skills verwendet oder erstellt werden dürfen;

ob Websuche erlaubt ist;

ob autonomer Modus erlaubt ist;

ob der Benutzer nur seine eigene Nutzung oder die Nutzung aller Benutzer sehen darf.

6. Zielpunkt 4 — KI-Limits pro Rolle

Unter den Panel-Einstellungen soll ein eigener Bereich für die KI entstehen.

Dort kann der Betreiber für jede Rolle festlegen:

tägliches Tokenlimit;

wöchentliches Tokenlimit;

monatliches Tokenlimit;

Anfragen pro Minute;

optional gleichzeitige KI-Vorgänge;

optional ein Kostenlimit;

optional erlaubte Modelle oder Provider.

6.1 Mehrere Rollen und Limits

Besitzt ein Benutzer mehrere Rollen, soll eine klar verständliche Regel gelten.

Das Ziel ist:

zusätzliche bezahlte oder privilegierte Rollen erhöhen die verfügbaren Limits;

ein Benutzer mit User und AI-VIP erhält das höhere AI-VIP-Limit;

ein unbegrenztes Kontingent gewinnt über ein begrenztes Kontingent;

dieselbe Anfrage wird nicht mehrfach gezählt;

die tatsächliche Begrenzung wird im Backend durchgesetzt.

6.2 UI

Alle Zahlenfelder verwenden den bereits integrierten MSM NumberStepper.

Die Oberfläche soll:

übersichtlich sein;

pro Rolle einen verständlichen Abschnitt anzeigen;

Hilfetexte und Beispiele liefern;

zwischen Rechten und Limits unterscheiden;

nicht die Rollenverwaltung mit zahlreichen Zahlenfeldern überladen.

Die Rollenverwaltung enthält die Rechte. Die AI-Einstellungen enthalten die Kontingente.

6.3 Verbrauchsanzeige

Der Betreiber soll sehen können:

Gesamtverbrauch;

Nutzung pro Benutzer;

Nutzung pro Server;

Nutzung pro Modell oder Provider;

geschätzte Kosten;

blockierte Anfragen;

ausgeschöpfte Limits.

Der Benutzer soll seinen eigenen verbleibenden Verbrauch sehen können.

7. Zielpunkt 5 — Jailbreak-, Missbrauchs- und Sicherheitsschutz

Die KI benötigt mehrere Schutzschichten.

7.1 Gewünschte Schutzwirkung

Das System soll erkennen oder verhindern können:

Versuche, Systemanweisungen offenzulegen;

Versuche, Secrets aus Logs oder Configs auszulesen;

Versuche, fremde Server abzurufen;

Versuche, nicht erlaubte Tools auszuführen;

absichtliche Kostenüberlastung;

extrem große Eingaben;

schädliche Anhänge;

manipulierte Modarchive;

wiederholte verdächtige KI-Nutzung.

7.2 Verbindliche Sicherheitsgrenze

Der Jailbreak-Schutz allein ist nicht die Sicherheitsgrenze.

Selbst wenn das Modell eine manipulierte Anweisung akzeptiert, müssen:

RBAC;

Serverzuordnung;

Tool-Allowlist;

Dateipfade;

Bestätigungspflichten;

Ressourcenlimits;

Host-Isolation

die unerlaubte Aktion weiterhin blockieren.

7.3 Benutzer-Flags

Verdächtige Vorgänge können einen Benutzer markieren.

In der Benutzerverwaltung soll der Betreiber sehen:

dass ein Sicherheitsflag vorliegt;

wie schwerwiegend es ist;

welcher Typ von Verstoß erkannt wurde;

ob mehrere Vorfälle existieren;

wann der letzte Vorfall war.

Mögliche Adminaktionen:

Flag prüfen;

Flag zurücksetzen;

AI-Zugriff deaktivieren;

zusätzliche AI-Rolle entziehen;

Limit reduzieren;

Benutzer sperren.

Eine einzelne unklare Formulierung soll nicht sofort automatisch Rollen entziehen.

8. Zielpunkt 6 — Audit-Protokoll

KI-, Hoster- und Provisionierungsaktionen müssen nachvollziehbar sein.

Das Audit-Protokoll soll anzeigen können:

welcher Benutzer eine Aktion ausgelöst hat;

ob die Aktion direkt oder über die KI ausgeführt wurde;

ob eine externe Integration die Aktion ausgelöst hat;

welcher Server betroffen war;

welche Art von Aktion ausgeführt wurde;

ob eine Bestätigung erfolgt ist;

ob die Aktion erfolgreich war;

zu welchem zusammengehörigen Vorgang die Aktion gehört.

Nicht im Audit gespeichert werden dürfen:

Passwörter;

API-Keys;

vollständige Tokens;

komplette private Chats;

komplette Logdateien;

vollständige sensible Konfigurationen.

Beispiel:

AI Assistant im Auftrag von Maik
hat auf Server 42 eine Configänderung vorgeschlagen.
Die Änderung wurde von Maik bestätigt und erfolgreich angewendet.

9. Zielpunkt 7 — Hoster- und Shop-Integration

MSM soll von einem Serverhoster oder Serverhaus einfach in dessen eigene Website oder Shoplösung integriert werden können.

MSM übernimmt dabei nicht den Shop, die Zahlung oder Rechnungsstellung.

Der externe Shop entscheidet:

welcher Kunde etwas gekauft hat;

welches Produkt gekauft wurde;

ob der Vertrag aktiv, gesperrt oder beendet ist.

MSM übernimmt:

den technischen Benutzerzugang;

die Erstellung des Servers;

die Einrichtung;

die Zuordnung zum Kunden;

die Rechte;

den Betriebsstatus;

die Verwaltung über das Panel.

9.1 Gewünschter Kundenablauf

Der Kunde:

besucht die Website des Hosters;

erstellt dort seinen Kundenaccount;

kauft beispielsweise einen Minecraft-Server;

sieht den Server in seinem Kundenbereich;

klickt auf „Server verwalten“;

wird zum MSM-Panel weitergeleitet;

ist dort direkt angemeldet;

sieht ausschließlich seine erlaubten Server und Funktionen.

Der Kunde soll:

kein zweites MSM-Passwort benötigen;

sich nicht zusätzlich im Panel registrieren müssen;

nicht merken müssen, dass Shop und Panel getrennte Systeme sind;

trotzdem technisch einen geschützten MSM-Benutzer und klare Rechte besitzen.

9.2 Self-Hosted bleibt erhalten

Die Hoster-Funktion ist eine Erweiterung und kein Ersatz.

Ein normaler Self-Hosted-Betreiber soll MSM weiterhin verwenden können:

mit normaler lokaler Anmeldung;

mit Social Login oder OIDC;

mit panelweiten Einstellungen;

ohne Shop;

ohne Billing-System;

ohne externe Provisionierungs-API.

Die Hoster-Funktionen können deaktiviert bleiben.

10. Zielpunkt 8 — Gemeinsame Servererstellungslogik

Dies ist eine der wichtigsten Anforderungen.

Es darf am Ende nicht mehrere voneinander abweichende Arten geben, einen Server zu erstellen.

Folgende Zugänge sollen dieselbe zentrale Logik verwenden:

Servererstellung über das normale Panel;

Servererstellung durch die KI;

Servererstellung durch eine externe Hoster- oder Billing-Integration;

spätere Automationen;

administrative Wiederholungs- oder Wiederherstellungsaktionen.

10.1 Gleiches Ergebnis

Unabhängig vom Einstiegspunkt müssen dieselben Regeln gelten:

Blueprint wird geprüft;

Host wird ausgewählt;

Ressourcen werden geprüft;

Ports werden vergeben;

Server wird erstellt;

Installation wird ausgeführt;

Einstellungen werden angewendet;

notwendige Dienste werden vorbereitet;

Benutzerrechte werden zugewiesen;

Healthcheck wird durchgeführt;

Fehler werden nachvollziehbar behandelt;

der tatsächliche Status wird gemeldet.

Die KI oder Billing-Machine darf nicht eine vereinfachte Sonderlogik verwenden, die Schutzmechanismen umgeht.

10.2 Unterschiedliche Auslöser, gleiche Fachlogik

Unterschiedlich ist nur, wer den Vorgang auslöst:

ein eingeloggter Benutzer;

die KI im Auftrag des Benutzers;

ein externer Hoster-Shop;

das interne System.

Die eigentliche Servererstellung bleibt dieselbe.

11. Zielpunkt 9 — Produkte, Blueprints und automatische Einrichtung

Der Hoster soll in MSM definieren können, welches Shopprodukt welcher technischen Serverart entspricht.

Beispiel:

Shopprodukt:
Minecraft Premium 8 GB

MSM-Ergebnis:
- Minecraft-Blueprint
- 8 GB RAM
- festgelegtes CPU-Limit
- festgelegter Speicher
- automatischer Host
- automatisch vergebene Ports
- tägliche Backups
- Kundenrechte
- optionales AI-Paket

11.1 Externe Shopdaten

Der externe Shop soll möglichst wenig interne MSM-Struktur kennen müssen.

Er soll keine internen Node-IDs, Installationspfade oder Portnummern verwalten müssen.

Er übermittelt hauptsächlich:

externe Kunden-ID;

externe Service-ID;

Produktkennung;

gewünschten Vertragszustand;

erlaubte Kundenoptionen;

grundlegende Kundendaten.

MSM löst daraus das technische Ziel auf.

11.2 Blueprint-System

Das vorhandene Blueprint-System bleibt die Grundlage der Serverarten.

Blueprints bestimmen weiterhin:

Installationsart;

Runtime;

Ports;

Konfiguration;

Startverhalten;

Updates;

Healthchecks;

Mods;

Backups;

besondere Anforderungen.

Die Hoster-Integration verbindet ein Verkaufsprodukt mit einem passenden Blueprint und einem Ressourcenpaket.

12. Zielpunkt 10 — Vollständiger Service-Lifecycle

Die Hoster-Anbindung darf nicht nur das erstmalige Erstellen unterstützen.

Ein gemieteter Service muss über seinen gesamten Lebenszyklus verwaltet werden können.

Gewünschte Zustände und Aktionen:

Bestellung erhalten;

Benutzer anlegen oder zuordnen;

Server erstellen;

installieren;

konfigurieren;

bereitstellen;

aktiv betreiben;

Ressourcen erhöhen oder reduzieren;

Produkt wechseln;

sperren;

wieder entsperren;

kündigen;

nach einer Frist beenden;

sichern;

löschen;

bei Fehlern erneut versuchen oder kontrolliert zurückrollen.

12.1 Zahlungssperre

Wenn der externe Shop meldet, dass ein Service gesperrt werden soll:

wird der Server kontrolliert gesperrt;

der Benutzer verliert nicht automatisch seinen gesamten Panelaccount;

der Service bleibt nachvollziehbar;

eine spätere Entsperrung ist möglich;

eine Kündigungs- oder Löschfrist kann berücksichtigt werden.

12.2 Kein sofortiges unkontrolliertes Löschen

Eine Kündigung soll nicht automatisch in derselben Sekunde alle Daten vernichten.

Der Hoster soll Fristen und Verhalten definieren können, beispielsweise:

sofort sperren;

sieben Tage aufbewahren;

letztes Backup erstellen;

danach löschen.

13. Zielpunkt 11 — Benutzeranlage und externe Identitäten

Das Panel und der Shop besitzen getrennte Datenbanken.

MSM soll niemals das Passwort des Kunden aus dem Shop übernehmen oder synchronisieren.

13.1 Eindeutige externe Identität

Ein Kunde wird nicht nur anhand seiner E-Mail-Adresse erkannt.

Die feste Zuordnung soll auf einer externen Kunden- oder Benutzer-ID des jeweiligen Hosters beruhen.

Dadurch bleiben folgende Fälle sicher:

Kunde ändert seine E-Mail;

zwei Hoster verwenden dasselbe MSM;

ein Benutzer hat dieselbe E-Mail in verschiedenen Systemen;

ein bestehender MSM-Account wird mit einem Hosteraccount verbunden.

13.2 Bestehender MSM-Account

Wenn ein Benutzer bereits einen MSM-Account besitzt und anschließend beim Hoster mit derselben bestätigten E-Mail einen Server kauft, kann eine sichere Verknüpfung angeboten werden.

Die E-Mail allein darf aber keine unsichere automatische Kontoübernahme ermöglichen.

14. Zielpunkt 12 — Ein-Klick-Handoff

Der Hoster soll aus seinem Kundenbereich einen sicheren, kurzlebigen Link erzeugen können.

Der Kunde klickt auf „Server verwalten“ und wird direkt in sein MSM-Dashboard weitergeleitet.

14.1 Gewünschtes Verhalten

Der Link:

gilt nur kurz;

kann nur einmal verwendet werden;

ist einem bestimmten Benutzer oder Service zugeordnet;

meldet den Benutzer im MSM an;

führt nur auf eine erlaubte interne MSM-Seite;

kann nicht beliebig wiederverwendet werden;

erscheint nicht im Audit oder in Logs im Klartext.

14.2 Alternative Anmeldung

Zusätzlich kann der Hoster den bereits vorhandenen Custom-OIDC-Login verwenden.

Dann kann der Kunde sich im Panel über den Account des Hosters anmelden.

Handoff und OIDC sollen gemeinsam möglich sein:

Handoff für den direkten Klick aus dem Shop;

OIDC für einen normalen erneuten Login im Panel.

15. Zielpunkt 13 — Einfache externe API

Die externe API soll einfach zu integrieren sein.

Ein Hoster soll nicht zahlreiche voneinander abhängige Einzelaufrufe programmieren müssen, nur um einen Server bereitzustellen.

15.1 Gewünschtes API-Ergebnis

Der Hoster soll einen Service mit seinem gewünschten Zustand an MSM übermitteln können.

Beispiele:

Service soll aktiv sein.
Service soll gesperrt sein.
Service soll auf das 16-GB-Paket wechseln.
Service soll beendet werden.

MSM kümmert sich um die notwendigen technischen Schritte.

15.2 Wiederholbare Aufrufe

Wenn der Shop denselben Auftrag wegen eines Netzwerkfehlers erneut sendet, darf kein zweiter identischer Server entstehen.

Der Vorgang muss eindeutig erkannt und sicher wiederholbar sein.

15.3 Status

Die API soll zurückgeben können:

Auftrag angenommen;

Validierung läuft;

Host wird ausgewählt;

Server wird erstellt;

Installation läuft;

Konfiguration läuft;

Rechte werden zugewiesen;

Healthcheck läuft;

Server ist bereit;

Server ist gesperrt;

Fehler ist aufgetreten;

Vorgang wurde zurückgerollt.

Der Hoster kann den Status abfragen und zusätzlich Ereignisse per Webhook erhalten.

16. Zielpunkt 14 — Hoster-Webhooks

MSM soll den externen Shop über wichtige Änderungen informieren können.

Beispiele:

Provisionierung gestartet;

Server bereit;

Provisionierung fehlgeschlagen;

Server gesperrt;

Server entsperrt;

Ressourcen geändert;

Kündigung vorgemerkt;

Server beendet;

Handoff verwendet;

AI-Paket oder Entitlement geändert.

Webhooks sollen:

signiert sein;

wiederholt werden können;

einen Zustellungsstatus besitzen;

keine Secrets enthalten;

eindeutig einem Ereignis und Service zugeordnet sein.

17. Zielpunkt 15 — Credentials für Self-Hosted und Hoster

Aktuell können bestimmte Zugangsdaten panelweit hinterlegt werden, beispielsweise:

GitHub Personal Access Token;

Steam-Account.

Das ist für Self-Hosted sinnvoll.

Für einen Hoster darf jedoch nicht jeder Kundenserver automatisch fremde oder zentrale Betreiberzugänge verwenden.

17.1 Gewünschtes Endergebnis

Credentials können auf unterschiedlichen Ebenen existieren:

panelweit;

benutzerbezogen;

serverbezogen.

Ein Server kann ein bestimmtes Credential verwenden, ohne dessen Klartext anzuzeigen oder zu kopieren.

17.2 Self-Hosted-Verhalten

Im Self-Hosted-Modus:

panelweite GitHub- und Steam-Zugangsdaten können weiterhin als Standard verwendet werden;

bestehende Installationen funktionieren weiter;

der Betreiber muss nicht für jeden Server einen eigenen Account anlegen.

17.3 Hoster-Verhalten

Im Hoster-Modus:

ein Kunde kann für seinen Server notwendige Zugangsdaten selbst hinterlegen;

ein Server kann einem bestimmten Benutzer-Credential zugeordnet werden;

ein anderer Server kann ein anderes Credential verwenden;

Kunden können keine Credentials anderer Kunden sehen oder verwenden;

der Betreiber kann festlegen, ob ein zentraler Fallback erlaubt ist;

sensible Werte werden nach dem Speichern nicht mehr im Klartext angezeigt.

17.4 Serveroberfläche

Wenn ein Server ein bestimmtes Credential benötigt, soll in dessen Oberfläche ein verständlicher Bereich erscheinen.

Beispiele:

Steam-Konto erforderlich;

GitHub-Zugriff erforderlich;

AI-Provider-Key erforderlich.

Der Benutzer kann dort:

ein vorhandenes eigenes Credential auswählen;

ein neues hinterlegen;

die Zuordnung ändern;

den Status prüfen;

das Credential rotieren oder entfernen.

18. Zielpunkt 16 — Mod- und Dateisicherheit

Die KI kann bei Mods und Dateien helfen, aber externe Inhalte dürfen nicht ungeprüft in Serververzeichnisse geschrieben werden.

Gewünschte Schutzmaßnahmen:

isolierter Downloadbereich;

Dateigrößenlimit;

Prüfung des Dateityps;

Prüfung von Archiven;

Schutz vor Path Traversal;

Schutz vor Zip- und Tar-Bombs;

Prüfsumme;

kontrollierte Übernahme;

Snapshot vor Änderungen;

verständlicher Fehler bei Captcha- oder Downloadblockaden.

Die KI darf keinen Captcha-Schutz umgehen.

Stattdessen fordert sie den Benutzer auf, die Datei selbst herunterzuladen und im Chat oder Serverbereich bereitzustellen.

19. Zielpunkt 17 — Datenschutz

Die Datenschutzerklärung und die sichtbaren Datenschutzhinweise müssen für v4.0 aktualisiert werden.

Sie müssen verständlich erklären:

welche Daten an externe KI-Anbieter gesendet werden können;

wann lokale Modelle verwendet werden;

wie Chatverläufe gespeichert werden;

wie Memory funktioniert;

wie lange Daten aufbewahrt werden;

wie Anhänge verarbeitet werden;

wie AI-Verbrauch und Kosten erfasst werden;

wie Sicherheitsflags entstehen;

wie externe Hosteridentitäten gespeichert werden;

wie Benutzer ihre KI-Daten löschen können.

Benutzer sollen die KI-Daten löschen können, soweit keine zwingenden Sicherheits-, Abrechnungs- oder gesetzlichen Aufbewahrungsgründe entgegenstehen.

20. Was MSM v4.0 ausdrücklich nicht werden soll

MSM v4.0 soll nicht:

ein eigener vollständiger Webshop werden;

Zahlungen und Rechnungen ersetzen;

Shop-Passwörter speichern;

Server nur noch im Hoster-Modus betreiben können;

Self-Hosted-Nutzer zu einer Cloud zwingen;

der KI Root- oder Superadminrechte geben;

beliebigen KI-generierten Code ausführen;

Nutzerrechte nur im Frontend prüfen;

jedem Kunden automatisch panelweite Credentials geben;

für Panel, KI und Billing drei verschiedene Servererstellungswege besitzen;

Server bei wiederholten API-Aufrufen doppelt erstellen;

lange Logs vollständig an externe Modelle senden;

sensible Daten in Audit-Logs schreiben;

eine einzelne unklare Nachricht sofort als sicheren Jailbreak-Beweis behandeln;

Kubernetes nur als unfertiges Beispiel ohne vollständigen Multi-Host-Betrieb betrachten.

21. Gewünschtes Gesamterlebnis

21.1 Self-Hosted-Betreiber

Ein Self-Hosted-Betreiber installiert MSM und kann es weiterhin wie bisher verwenden.

Zusätzlich kann er:

weitere Hosts anbinden;

Kubernetes- und Multi-Host-Funktionen nutzen;

eine KI konfigurieren;

eigene API-Keys oder lokale Modelle verwenden;

Server per Chat konfigurieren;

Fehler erklären lassen;

Skills erstellen;

panelweite Steam- oder GitHub-Zugänge weiterverwenden.

21.2 Hoster

Ein Hoster:

betreibt ein zentrales MSM-System;

verbindet mehrere Hosts;

ordnet Shopprodukte MSM-Produkten und Blueprints zu;

bindet seine Website über eine einfache API an;

erstellt Kunden und Server automatisch;

weist Rechte und AI-Pakete zu;

sieht Provisionierungs- und Verbrauchsstatus;

lässt Kunden direkt aus dem Shop ins Panel wechseln;

kann OIDC verwenden;

verwaltet Sperrungen, Tarifwechsel und Kündigungen;

schützt seine zentralen Credentials vor Kunden.

21.3 Kunde

Ein Kunde:

kauft einen Server beim Hoster;

klickt auf „Server verwalten“;

landet direkt in MSM;

sieht nur seinen Server;

kann nur erlaubte Aktionen ausführen;

kann die KI zur Einrichtung und Diagnose verwenden;

kann eigene notwendige Credentials hinterlegen;

muss kein zweites Passwort verwalten;

erhält eine verständliche Oberfläche statt interner Infrastrukturdetails.

21.4 KI

Die KI:

kennt den erlaubten Serverkontext;

versteht Logs und Configs;

schlägt sichere Änderungen vor;

verwendet vorhandene MSM-Funktionen;

beachtet Benutzerrechte;

kann aus bestätigten Abläufen Skills bilden;

führt keine beliebigen Programme aus;

protokolliert relevante Aktionen;

kann keine fremden Server kontrollieren;

verhält sich unabhängig davon gleich, ob der Server manuell oder über einen Hoster erstellt wurde.

22. Verbindliche Kernergebnisse

MSM v4.0 erreicht sein Ziel, wenn folgende Ergebnisse vorhanden sind:

Ein vollständiges Kubernetes- und Multi-Host-Zielsystem ist nutzbar.

Ein zentrales Panel kann mehrere Hosts und deren Server steuern.

Serveraufgaben und Zustände werden zuverlässig über ein gemeinsames Ereignis- und Aufgabensystem verteilt.

Die KI kann Server einrichten, konfigurieren und diagnostizieren.

Die KI verwendet ausschließlich erlaubte MSM-Funktionen.

Die KI besitzt exakt die Rechte des handelnden Benutzers.

Lange Chats, Anhänge, Memory und Skills funktionieren.

Benutzer können mehrere Rollen gleichzeitig besitzen.

KI-Limits lassen sich täglich, wöchentlich und monatlich pro Rolle konfigurieren.

Alle neuen Zahlenfelder verwenden den MSM NumberStepper.

Verdächtige KI-Nutzung kann erkannt, markiert und administrativ geprüft werden.

KI- und Hosteraktionen sind im Audit nachvollziehbar.

Ein Hoster kann MSM über eine einfache API mit seinem Shop verbinden.

Kunden müssen kein zweites MSM-Passwort anlegen.

Ein Ein-Klick-Handoff führt sicher zum richtigen Kundenserver.

Custom OIDC kann zusätzlich als Hoster-Login verwendet werden.

Panel, KI und Billing verwenden dieselbe Servererstellungs- und Lifecycle-Logik.

Shopprodukte werden intern auf Blueprints und Ressourcenpakete abgebildet.

Server können erstellt, gesperrt, geändert und beendet werden.

Wiederholte API-Aufrufe erzeugen keine doppelten Server.

Self-Hosted bleibt vollständig erhalten.

Panel-, User- und Server-Credentials können sicher getrennt werden.

Kundenserver greifen nicht unkontrolliert auf Betreiber-Credentials zu.

Die Datenschutzerklärung deckt KI, Memory, Anhänge und externe Integrationen ab.

Keine der neuen Funktionen umgeht Guardian, Lifecycle, RBAC oder Host-Isolation.

23. Abgrenzung zur technischen Planung

Dieses Dokument beschreibt das gewünschte Produkt und Endergebnis.

Es entscheidet noch nicht endgültig:

welche Datenbanktabellen angelegt werden;

welche konkreten Klassen entstehen;

wie viele API-Endpunkte verwendet werden;

welches Queue- oder Pub/Sub-Produkt eingesetzt wird;

wie Kubernetes intern aufgebaut wird;

welche Reihenfolge einzelne Migrationen besitzen;

wie viele Pull Requests notwendig sind;

wie einzelne Services oder Dateien heißen;

ob bestimmte Aufgaben über Worker, Jobs oder Controller umgesetzt werden.

Diese Entscheidungen werden anschließend in einem getrennten technischen Implementierungsplan auf Grundlage dieses Zielbilds getroffen.

Der technische Plan darf dieses Zielbild konkretisieren, aber nicht stillschweigend verändern.