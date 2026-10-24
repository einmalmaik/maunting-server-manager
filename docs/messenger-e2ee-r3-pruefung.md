# R3-Prüfunterlage: E2EE-Messenger

Stand: 24.10.2026. Basis: Branch `fix/messenger`, Commit `e4c8aba9`.
Dieses Dokument ist eine **Prüfunterlage**, kein Audit und keine Freigabe.
Es beschreibt, was der Code tut, welche Tests es belegen und welche Fragen
ein menschlicher Krypto-/Privacy-Prüfer beantworten muss. Es ersetzt keine
unabhängige Prüfung, keine Laufzeitprobe und keine rechtliche Bewertung.

## 1. Zweck und Grenzen

Der Messenger soll Nachrichten Ende-zu-Ende verschlüsseln, mehrere Geräte je
Konto unterstützen und auf dem Server nur Chiffrat und die für die Zustellung
nötigen Metadaten halten. Dieses Dossier sammelt die dafür relevanten Pfade
und offenen Punkte. Nicht enthalten sind:

- eine unabhängige Code- oder Protokollprüfung,
- eine bestandene Laufzeitprobe in Web, Desktop oder Android in dieser Runde,
- eine Aussage über WAL, Replikate, Anbieter-Snapshots oder physische
  Löschung,
- eine Freigabe für den Produktivbetrieb.

## 2. Datenfluss

### 2.1 Senden einer Direktnachricht

1. `frontend/src/hooks/useMessengerSenden.ts` baut die Nutzlast und ruft
   `baueVersandFuer` (`useKonversation.ts`) auf.
2. `baueZustellungen` (`frontend/src/services/ratchet/versand.ts`) erzeugt je
   Zielgerät einen eigenen Ratchet-Umschlag. Ein Sitzungsaufbau (`dr-init`)
   reist als eigenes Steuerpaket vor der Nachricht.
3. Ist am Zielgerät eine `inbox_hash` veröffentlicht, wandert der Auftrag mit
   `ziel_inbox` weiter; sonst bleibt er auf dem alten Weg.
4. `stelleZu` (`frontend/src/services/nachrichtVersand.ts`) bündelt je
   Gesprächs-Mailbox und ruft `sendeZustellungen`
   (`frontend/src/services/e2eeZustellung.ts`) auf.
5. Der Server (`backend/routers/social/zustellung.py`,
   `backend/services/social/zustellung.py`) prüft in dieser Reihenfolge:
   Mailbox-Zugang, Gerätebeleg des Absenders, Zielgerät vorhanden und
   freigegeben, Schreibrecht des Absenders, Quoten, Idempotenz. Erst danach
   legt er Chiffretextobjekt und Zustellauftrag an.
6. Was der Server ablehnt (`inbox_voll`, unbekanntes Ziel) oder was das Netz
   nicht erreicht, geht für **dieses eine Ziel** über die alte geteilte
   Mailbox. Einen stillen Gesamt-Rückfall gibt es nicht.

Gruppennachrichten gehen je Gerät über das Gerätepostfach, wenn **alle**
Zielgeräte die Fähigkeit anbieten; sonst als ein Umschlag in die geteilte
Mailbox. Steuerpakete laufen weiterhin über die geteilte Mailbox.

### 2.2 Empfangen ohne offenen Chat

1. `frontend/src/hooks/useMessengerKern.ts` startet den Hintergrundlauf
   `verarbeitePostfach` (`frontend/src/services/gespraech/postfach.ts`).
2. Der Lauf holt die offenen Zustellungen der eigenen Inbox mit einem
   Gerätebeleg (`holeZustellungen`).
3. Jeder Eintrag wird seinem Gespräch zugeordnet: Ratchet-Kopf über das
   Absenderkonto, Gruppenkopf über die Schlüsselkennung, Hybrid-Umschlag durch
   Entschlüsseln und Prüfen der Bindung.
4. Erst nach dem lokalen Verarbeiten und Speichern quittiert das Gerät
   (`quittiereZustellungen`). Was nicht zugeordnet oder nicht verarbeitet werden
   kann, bleibt liegen und kommt beim nächsten Lauf wieder.
5. Die Zustellquittung an den Absender entsteht nur nach dem lokalen Commit und
   nennt die logischen Nachrichtenkennungen. Ein Wecksignal, ein Umschlag oder
   ein Zeitpunkt erzeugt keine Quittung.

### 2.3 Verlauf an ein neues Gerät

Es gibt zwei Wege:

- **Kopplungscode:** `device_pairings.verlauf_blob` trägt einen gegen den
  Geräteschlüssel des neuen Geräts versiegelten Verlauf (höchstens 25 MiB).
  Das neue Gerät holt ihn genau einmal; danach wird das Feld geleert.
- **Bereits freigegebenes Gerät:** `verlaufsAbgleich.ts` schickt den Verlauf
  als Umschlag über die eigene Geräte-Mailbox, in Teilen, mit Signatur und
  Zielbindung. Anhänge reisen als eigene Pakete aus der lokalen Ablage mit
  (`verlaufsMedien.ts`), damit sie nicht vom 30-Tage-Ablauf der Serverkopie
  abhängen.

Die Anhangpakete tragen ein 24-Stunden-Fenster; ältere verwirft der Empfänger.
Der Umschlag selbst liegt im Relais weiterhin bis zu 30 Tage.

### 2.4 Anhänge

`ladeAnhangHoch` verschlüsselt jede Datei mit einem eigenen Zufallsschlüssel
(`medienKrypto.ts`) und lädt nur den Chiffratblock hoch. Der Schlüssel reist im
verschlüsselten Nachrichtentext. Der Server speichert einen generischen Namen
(`anhang.bin`) und `application/octet-stream`; Dateiname und Typ stehen im
versiegelten Manifest. Empfangene Anhänge werden dauerhaft in der
kontogebundenen Ablage gespeichert, nicht nur im Arbeitsspeicher.

Seit 23.10.2026 gilt für neue Uploads eine Serverfrist von 30 Tagen
(`MEDIEN_AUFBEWAHRUNG_TAGE`). `expires_at` wird beim Upload aus `created_at`
fortgeschrieben. Ältere Zeilen behalten ihren gespeicherten Ablauf von bis zu
90 Tagen und laufen ohne Migration aus; der Aufräumlauf löscht nur, was seine
gespeicherte Frist überschritten hat. Die lokale Verfallsfrist im Chat (bis
90 Tage) ist davon getrennt: sie betrifft den Verlauf auf den Geräten und
verlängert die Serverkopie nicht.

## 3. Speicherinventar

| Tabelle | Felder | Zweck | Frist / Löschung |
| --- | --- | --- | --- |
| `user_e2ee_devices` | `user_id`, `device_id`, öffentliche Schlüssel, `is_approved`, `approved_by`, `approval_signature`, `auth_family`, Label, Zeiten | Geräteverzeichnis und Freigabekette | Bis Geräteentfernung oder Kontolöschung |
| `e2ee_device_inboxes` | `device_id`, `inbox_hash`, `erstellt_am`, `ablauf_am` | Opake Zustelladresse je Gerät | 30 Tage, bei jedem Start erneuert; stündlicher Lauf |
| `e2ee_deliveries` | `id`, `payload_id`, `inbox_hash`, `laufende_seq`, `angenommen_am`, `ablauf_am`, `quittiert_am` (nicht mehr geschrieben) | Offener Zustellauftrag | Quittung oder 30 Tage |
| `e2ee_delivery_payloads` | `id`, `ciphertext`, `ciphertext_sha256`, `groesse`, `angenommen_am`, `ablauf_am` | Chiffratobjekt ohne Ziel und Absender | Letzte Referenz oder 30 Tage |
| `e2ee_delivery_receipts` | `inbox_hash`, `delivery_id`, `request_hash`, `ergebnis`, `angenommen_am`, `ablauf_am` | Idempotenzrest nach der Quittung, ohne Inhalt | Ursprüngliche Frist, höchstens 30 Tage |
| `e2ee_blind_envelopes` | `blind_mailbox_id`, `ciphertext_envelope`, `ciphertext_sha256`, `client_uuid`, `created_at` | Alter Relaisbestand, Gruppen, Steuerpakete, Verlauf | 30 Tage; stündlicher Lauf |
| `e2ee_blind_mailboxes` | `mailbox_id`, `auth_verifier` | Besitznachweis einer Mailbox | Bis Mailbox nicht mehr gebraucht wird |
| `chat_media` | Kennung, Mailbox, Chat/Gruppe, Uploader, `ciphertext_blob`, generischer Name/Typ, Größe, SHA-256, Zeiten | Verschlüsselter Anhang für den Erstdownload | Neue Uploads 30 Tage ab Upload; ältere Zeilen bis zu ihrem gespeicherten Ablauf (höchstens 90 Tage); zusätzlich mit der Nachricht löschbar |
| `device_pairings` | `code_hash`, `family`, `verlauf_blob`, `verlauf_abgelegt_am`, `expires_at`, `redeemed_at` | Kopplungscode und Verlaufs-Erstabgleich | Code 10 Minuten; Blob nach Abholung oder 10 Minuten nach Einlösen. Zeilen nicht im Panel-Backup: nach einem Restore beginnt eine Kopplung neu |
| `webpush`/`push_subscriptions` | Endpunkt, Schlüssel, Sitzungsfamilie | Wecken bei geschlossener App | Bis Abmeldung, Widerruf oder 404/410 |

Die Fristen stehen im Code: `FRIST_TAGE = 30` in
`backend/services/social/zustellung.py`, `E2EE_ENVELOPE_RETENTION_DAYS = 30` in
`backend/services/social/grenzen.py`, `MEDIEN_AUFBEWAHRUNG_TAGE = 30` in
`backend/services/chat_media_service.py`, `FRIST_MINUTEN = 10` in
`backend/services/device_pairing_service.py`. Für Anhänge gilt die Frist je
Zeile: neue Uploads bekommen 30 Tage, ältere behalten ihren gespeicherten
Ablauf.

## 4. Schlüsselwege

| Schlüssel | Wo entsteht er | Wo liegt er | Wofür |
| --- | --- | --- | --- |
| Geräte-Verschlüsselung (RSA-OAEP) | Gerät, `geraeteAblage.ts` | IndexedDB `msm_e2ee_keystore`, Store `devices`, Eintrag `konto:<id>`; optional mit Messenger-PIN versiegelt | Verschlüsseln an dieses Gerät |
| Geräte-Signatur (ECDSA P-256) | Gerät, `absenderSignatur.ts` | dieselbe Zeile | Absenderbeglaubigung, Gerätefreigabe, Transporbelege |
| Zustelladresse | Gerät, `inboxAdresse.ts`: `sha256("msm:device-inbox:v1:" + privater Geräteschlüssel)` | nur als Hash auf dem Server | Adressierung des Gerätepostfachs |
| Ratchet-Sitzung | beide Geräte aus dem gemeinsamen Startgeheimnis | lokal, je Gerät und Gegenüber | Direktnachrichten |
| Gruppen-Schlüssel | Gerät, das die erste Nachricht schreibt | lokal, je Gruppe und Generation | Gruppenachrichten |
| Medien-Schlüssel | sendendes Gerät, je Datei | im verschlüsselten Nachrichtentext | Anhang entschlüsseln |
| Verlaufs-Blob | Quellgerät, versiegelt gegen das Zielgerät | `device_pairings.verlauf_blob` | Verlauf an ein neues Gerät |
| Notiz-/Kalenderschlüssel | Konto, nicht je Gerät | Gerätespeicher, Weitergabe an freigegebene Geräte | Notizen und Termine |

Der private Geräteschlüssel verlässt das Gerät nicht. Veröffentlicht werden nur
öffentliche Schlüssel, die Zustelladresse als Hash und die Freigabeunterschrift.
Der Server kann die Zustelladresse nicht nachrechnen.

## 5. Vertrauensbindungen

1. **Erstes Gerät eines Kontos** wird automatisch freigegeben und beglaubigt
   sich selbst (`_selbst_beglaubigen`, `freigabe_daten`).
2. **Weitere Geräte** warten. Ein freigegebenes Gerät unterschreibt
   `msm:device-approval:v2:<konto>:<gerät>:<hash(pub)>:<hash(sign>)`. Die
   Unterschrift deckt Kennung und beide öffentlichen Schlüssel.
3. **Kontakte prüfen die Kette selbst** (`vertrauteGeraete`,
   `geraeteVertrauen.ts`). `is_approved` allein ist ein Wort des Servers und
   reicht für fremde Konten nicht.
4. **Brücke:** Eine gültige Freigabe eines serverseitig freigegebenen Geräts,
   das dieser Client noch nicht verankert hat, wird für fremde Konten als Ziel
   angenommen, aber bei jedem Abruf erneut als unbestätigt gewarnt. Sie wird
   nicht in die Vertrauensliste geschrieben.
5. **Eigenes Konto:** Die Kette bleibt streng. Das eigene wartende Gerät wird
   nur angenommen, wenn Kennung und öffentlicher Schlüssel zur lokalen Ablage
   passen.
6. **Sicherheitsnummer:** Der Mensch vergleicht sie beim ersten Kontakt und
   nach Warnungen. Ohne Vergleich bleibt ein Erstkontakt vertrauenswürdig, weil
   es keinen besseren Anker gibt.
7. **Geräteentfernung** sperrt die Sitzungsfamilie (`auth_family`) und wirkt
   ab der nächsten Anfrage. Der Notizschlüssel wird dabei nicht erneuert; das
   steht so in der Datenschutzerklärung.
8. **Wiederfreigabe** nach Verlust aller Geräte verlangt Passwort oder zweiten
   Faktor (`wieder_freigeben(..., mit_nachweis=True)`). Ein selbst freigegebenes
   Gerät ist nicht beglaubigt; Kontakte senden erst nach Vergleich der
   Sicherheitsnummer dorthin.

## 6. Was seit dem 23.10.2026 gebaut wurde

- **Serververtrag für gerätebezogene Zustellung:** vier Tabellen, Migration
  `20261023_02`, drei Routen (`POST/GET /api/social/e2ee/deliveries`,
  `POST /api/social/e2ee/deliveries/ack`), Dienst mit Annahme, Abruf,
  Quittung, Idempotenz und Aufräumen.
- **Client des neuen Wegs:** Zustelladresse aus dem Geräteschlüssel, drei
  Beleg-Zeichenketten, Aufrufe, Veröffentlichung der Adresse mit der
  Gerätemeldung.
- **Umschaltung des Direktchats:** Versand je Zielgerät über die Inbox, mit
  zielgenauem Rückfall auf die alte Mailbox.
- **Hintergrundlauf:** Lesen, Zuordnen, Verarbeiten, Speichern, danach
  Quittieren; ohne offenen Chat.
- **Quittungen:** logische Nachrichtenkennungen statt bloßer Zählergrenzen;
  Zustellquittung erst nach lokalem Commit; keine Quittung aus Wecksignalen.
- **Verlauf und Anhänge an ein neues Gerät:** vollständiger Verlauf, Teile à
  200 Nachrichten, Anhänge als geprüfte Pakete mit 24-Stunden-Fenster,
  Quittung je Teil.
- **Medien dauerhaft:** empfangene und gesendete Anhänge liegen in der
  kontogebundenen Ablage, nicht nur im RAM.
- **Betrieb:** Zustellpuffer aus dem Panel-Backup ausgeschlossen; Fristen des
  gerätebezogenen Transports an den stündlichen Aufräumlauf gehängt;
  abgelaufene Zustellungen werden schon beim Lesen nicht mehr ausgeliefert.
- **Gruppen, Hintergrund und Schlüssel:** Gruppen fan-out je Gerät mit einem
  Chiffretextobjekt; der Hintergrundlauf liest die Rechte des verarbeiteten
  Gesprächs (nicht des offenen), und die Schlüsselverteilung fragt Geräte
  frisch ab. Beides am 24.10.2026 in der Laufzeitprobe gefunden und behoben.
- **Wirkungen und Löschen:** Wirkungs-Outbox mit 10-Sekunden-Nachschlag;
  Server-Löschauftrag als dauerhafte Wiederholung. Auch der Verfall nutzt
  diesen Auftrag: lokal wird zuerst getilgt, der Serverteil liegt davor als
  Auftrag vor und wird ohne Netz nachgeholt (`raeumeVerfalleneZeilen`).
- **Anhangsfrist und Backup-Lücke (24.10.2026):** neue Uploads liegen 30 Tage
  auf dem Server, der Altbestand behält seinen gespeicherten Ablauf von bis zu
  90 Tagen und läuft ohne Migration aus. `device_pairings` steht jetzt im
  Backup-Ausschluss; nach einem Restore sind offene Kopplungen fort. Die
  Datenschutzerklärung trennt die 30-Tage-Serverkopie von der lokalen
  Verfallsfrist bis 90 Tage (Fassung 3.44).

## 7. Belege in Tests

Die folgenden Dateien waren Teil der Abnahme vom 24.10.2026. Frontend und
Backend liefen vollständig; die verbliebenen Fehlschläge stammen aus der
Umgebung und sind im Plan §0.2 genannt. Ein grüner Lauf nach weiteren
Änderungen bleibt Pflicht.

| Datei | Was sie prüft |
| --- | --- |
| `backend/tests/test_e2ee_zustellung.py` | Annahme, Abruf, Quittung, falscher/fehlender Beleg, fehlende Mailboxberechtigung, wartendes Ziel, Konflikt, fremdes Gerät, Idempotenzrest, Aufräumen, keine Auslieferung nach Fristablauf |
| `backend/tests/test_migration_e2ee_zustellung.py` | Migration hin und zurück, keine Fremdschlüssel auf `users` |
| `backend/tests/test_panel_backup_zustellbestand.py` | `pg_dump` schließt die sieben Puffer nur als Daten aus; Schema bleibt; neue Tabelle mit Frist macht den Test rot |
| `backend/tests/test_chat_media_security.py` | neue Uploads bekommen `expires_at = created_at + 30 Tage`; Altbestand behält 90 Tage; Cleanup löscht nur Abgelaufenes |
| `backend/tests/test_e2ee_device_approval.py` | Freigabe, Schlüsselwechsel, fremde Sitzung, Entfernen, Wiederfreigabe mit Nachweis, Selbstbeglaubigung |
| `backend/tests/test_e2ee_envelope_retention.py` | 30-Tage-Frist des alten Relaisbestands |
| `backend/tests/test_review_regressions.py` | Aufräumlauf läuft im Thread, schließt die Sitzung, ruft die Zustellbereinigung |
| `frontend/src/services/e2eeZustellung.test.ts` | zeichengleiche Belegketten, Adressableitung, drei Aufrufe |
| `frontend/src/services/nachrichtVersand.test.ts` | Inbox zuerst, zielgenauer Rückfall, keine Doppelspur, Kennungsräume |
| `frontend/src/services/geraet/verlaufsAbgleich.test.ts` | Verlaufsteile, Signatur, Zielbindung, Anhänge, 24-Stunden-Fenster |
| `frontend/src/services/e2eeGeraet.test.ts` | Geräteidentität, Vertrauenskette, Brücke, Warnungen, eigener Schlüssel je Konto |
| `frontend/src/services/medienSicherung.test.ts` | Anhänge dauerhaft ablegen, RAM ist keine Sicherung, zweites Gerät holt nach |
| `frontend/src/pages/Privacy.test.tsx` | Gerätezustellung und Fristen stehen in der Erklärung; Fassung und Datum stimmen |
| `frontend/src/pages/docs/SelfHostingDocs.test.tsx` | sichtbare Betriebsdoku nennt Bestand, Fristen und Backup-Ausschluss |

## 8. Fragen für einen menschlichen Prüfer

### Kryptographie und Schlüssel

1. Ist die Ableitung der Zustelladresse aus dem **privaten** JWK-String
   (`sha256("msm:device-inbox:v1:" + privateKeyJwk)`) unbedenklich, oder wird
   dadurch ein Schlüssel in einem zweiten Kontext verwendet? Der Hash ist
   öffentlich; der private Schlüssel bleibt geheim.
2. Sind die Signaturzeichenketten (`relay_beweis_daten`,
   `lese_beweis_daten`, `ack_beweis_daten`, `freigabe_daten`,
   `entfernen_daten`) kanonisch genug gegen Umordnung, Trennzeicheninjektion
   und Unicode-Normalisierung? Kennungen sind UUIDs bzw. 64-Hex, die Mailbox
   ist eine abgeleitete Kennung.
3. Deckt die Freigabeunterschrift alle sicherheitsrelevanten Felder ab? Sie
   deckt Kennung und beide öffentlichen Schlüssel, **nicht** die
   Zustelladresse (`inbox_hash`). Ein bösartiger Server könnte damit Nachrichten
   an eine andere Inbox lotsen (Zustellbarkeit, nicht Vertraulichkeit).
4. Ist die Ratchet-Implementierung aus DIS vollständig geprüft, insbesondere
   Header-Authentisierung, Skipped-Key-Grenze, Replay und Zustandsfortschritt?
5. Ist die Gruppenverschlüsselung (ein symmetrischer Schlüssel je Generation,
   Absenderbeglaubigung per ECDSA) gegen ein Mitglied geschützt, das den
   Gruppenschlüssel kennt und eine Nachricht eines anderen erzeugen will?
6. Ist die Bindung der Gruppen-Mailbox an das Gruppengeheimnis
   (`gruppenZiele`, `dmZiele`) gegen ein untergeschobenes Geheimnis geprüft?
7. Werden Medien mit einem eigenen Schlüssel und einer AAD aus Absender,
   Mailbox und Datei-ID gebunden? Was passiert beim Umhängen in ein anderes
   Gespräch?
8. Ist der Verlaufs-Blob wirklich nur für das Zielgerät zu öffnen, und prüft
   der Empfänger Ziel, Signatur und Freigabekette, bevor er ihn übernimmt?

### Protokoll und Zustellung

9. Reicht die Autorisierung im Annahmepfad (Mailbox-Zugang, Absenderbeleg,
   Ziel freigegeben, Schreibrecht) für jedes Ziel, auch für eigene Geräte und
   Gruppen? Die Reihenfolge „Berechtigung vor Idempotenz“ ist getestet.
10. Ist die Idempotenz wirklich belastbar? Der Client wählt je Sendeversuch
    neue `delivery_id`/`payload_id`; nach einer verlorenen HTTP-Antwort kann
    dieselbe Nachricht erneut als zweiter Auftrag ankommen. Die
    Nachrichten-Deduplizierung im Client fängt das in der Anzeige ab, der
    Server hält aber beide Kopien bis zur Quittung.
11. Kann ein Gerät die Quittung eines anderen Geräts auslösen? Der Beleg ist
    an Inbox und Signaturschlüssel gebunden; ein fremdes Gerät desselben
    Kontos wird abgewiesen.
12. Was passiert, wenn ein Umschlag nach dem lokalen Commit, aber vor der
    Quittung verloren geht? Er bleibt liegen und wird erneut verarbeitet; die
    Client-Deduplizierung muss das aushalten.
13. Ist der Rückfall auf die alte Mailbox ein Sicherheitsproblem? Der Inhalt
    bleibt E2EE; geändert wird die Metadatenlage (geteilte Mailbox statt
    Geräte-Inbox). Nach einer teilweise erfolgreichen Annahme mit verlorener
    Antwort kann der Rückfall dieselbe Nachricht ein zweites Mal in die alte
    Mailbox legen.
14. Ist die Frist beim Lesen und beim Aufräumen deckungsgleich? Seit dieser
    Runde liefert `lesen` nichts mit `ablauf_am < jetzt`; der stündliche Lauf
    löscht danach. Ein Restore kann abgelaufene Zeilen bis zum ersten Lauf
    halten, aber nicht mehr ausliefern.
15. Ist die Quote je Inbox (50 Aufträge, 10 MiB) ausreichend gegen
    Speicherdruck, und kann ein Absender mit vielen Konten viele Inboxen
    gleichzeitig füllen?

### Metadaten und Datenschutz

16. Welche Metadaten sieht der Server? Gerätezuordnung, Inbox-Hash,
    Zustellkennung, Größe, Prüfsumme, Zeitpunkt, Annahmenummer, Chat-Mailbox
    im Absenderkontext, Push-Adresse und IP. Ist das mit der Erklärung
    vereinbar?
17. Kann der Server aus der zeitlichen Nähe von Zustellungen auf Gespräche
    schließen? Die Mailbox des Gesprächs steht nicht in der Zustellzeile, aber
    der Absender nennt sie in der signierten Anfrage.
18. Ist die Aussage „Kopien in Sicherungen können länger bestehen“ ausreichend
    ehrlich? Panel-Backups schließen die Puffer aus; alte Backups, WAL,
    Replikate und Anbieter-Snapshots nicht.
19. **Erledigt (24.10.2026):** Neue Uploads liegen 30 Tage auf dem Server,
    ältere Zeilen behalten ihren gespeicherten Ablauf von bis zu 90 Tagen. Der
    Plan verlangte genau diesen Übergang, ohne den Altbestand vor seiner
    beworbenen Frist zu löschen. Zu prüfen bleibt, ob die sichtbaren Texte die
    30-Tage-Serverkopie und die lokale 90-Tage-Frist überall trennen.
20. Ist der Verlaufs-Erstabgleich über die Kopplung (10 Minuten) und der
    Geräteweg (Umschlag bis 30 Tage, Paketfenster 24 Stunden) so beschrieben,
    wie er tatsächlich läuft?
21. Sieht der Server bei `chat_media` einen echten Dateinamen? Der eigene
    Client sendet `anhang.bin` und `application/octet-stream`; die API nimmt
    aber beliebige Namen an. Ein fremder oder alter Client könnte dort einen
    echten Namen ablegen.
22. Ist die Wiederherstellung eines Panel-Backups ohne die Puffer
    betrieblich akzeptabel? Geräte melden ihre Adresse beim nächsten Start
    neu an; offene Zustellungen, offene Kopplungen und nicht lokal gesicherte
    Anhänge sind fort. Ein Gerät, das sich nicht wieder meldet (alte Version,
    kein Start), empfängt nichts.

### Vertrauen, Betrieb und Grenzen

23. Ist die Brückenregel (signierte Freigabe eines serverseitig freigegebenen,
    aber lokal unbekannten Geräts) ein akzeptabler Kompromiss, oder öffnet sie
    einen Weg für einen bösartigen Server?
24. Ist der Erstkontakt (TOFU) plus Sicherheitsnummer für dieses Produkt
    ausreichend? Es gibt keinen Verzeichnisanker und keine zentrale
    Schlüsseltransparenz.
25. Schützt die PIN-Versiegelung auch die neuen Zustell- und
    Verlaufsschlüssel? Die Gerätezeile liegt in derselben versiegelten Ablage;
    der Verlaufs-Blob liegt serverseitig und ist gegen das Zielgerät
    versiegelt.
26. Was passiert bei Verlust aller Geräte? Der Verlauf ist fort; die
    Wiederfreigabe mit frischem Nachweis setzt das Konto neu auf, Kontakte
    sehen eine Warnung.
27. Ist die Webversion gegen einen Betreiber geschützt, der den ausgelieferten
    Code ändert? Nein. Nur Desktop- und Android-App verschieben diese Grenze.
28. Sind die Aufbewahrungsfristen und das Löschen physisch belegt? Nein.
    `DELETE` entfernt die Zeile aus der Anwendung; PostgreSQL hält die alte
    Zeilenversion bis `VACUUM`, und externe Kopien haben eigene Fristen.

## 9. Offene Punkte (Stand 24.10.2026; 7 und 9 erledigt)

1. **Kein unabhängiges Audit, keine Freigabe.** Dieses Dokument ist input für
   eine Prüfung, nicht ihr Ergebnis.
2. **Keine Laufzeitprobe in dieser Runde.** Web, Desktop und Android wurden
   in dieser Runde nicht geprüft. Frühere Proben sind keine Freigabe.
3. **`inbox_hash` ist nicht Teil der Freigabeunterschrift.** Ein bösartiger
   Server kann die Zustelladresse in der Geräteliste austauschen. Das kostet
   Zustellbarkeit, nicht Vertraulichkeit, weil der Inhalt an den öffentlichen
   Schlüssel des Zielgeräts gebunden bleibt. Eine Prüfung sollte entscheiden,
   ob die Adresse in `freigabe_daten` gehört.
4. **`delivery_id` ist nicht die `client_uuid`.** Der Übergabeplan sah die
   logische Kennung als Zustellkennung vor. Der Client wählt je Versuch eine
   neue UUID; die Idempotenz des Servers greift nur bei Wiederholung derselben
   Kennung, nicht bei einem neuen Sendeversuch.
5. **Gruppen laufen je Gerät, wenn alle Geräte die Fähigkeit anbieten; sonst
   fällt die ganze Gruppe auf die geteilte Mailbox zurück. Steuerpakete
   bleiben auf dem alten Weg. Der Altweg ist an einer Stelle (`altweg()`)
   schaltbar, aber noch nicht geschlossen.**
6. **Der Verlaufs-Umschlag liegt bis zu 30 Tage im Relais**, obwohl die
   Anhangpakete nach 24 Stunden verfallen. Das 24-Stunden-Fenster ist eine
   Client-Regel, keine serverseitige Löschfrist.
7. **Erledigt (24.10.2026): Medien 30 Tage für neue Uploads.** `expires_at`
   wird beim Upload auf `created_at + 30 Tage` gesetzt. Zeilen aus der Zeit
   davor behalten ihren gespeicherten Ablauf (bis 90 Tage) und laufen ohne
   Migration aus; der Aufräumlauf löscht nur Abgelaufenes.
8. **Physische Löschung ist nicht nachgewiesen.** WAL, Replikate,
   Anbieter-Snapshots und alte Backups wurden nicht inventarisiert.
9. **Erledigt (24.10.2026): `device_pairings` ist aus dem Panel-Backup
   ausgeschlossen.** Das Schema bleibt, die Zeilen fallen. Nach einem Restore
   sind offene Kopplungen und der versiegelte Verlaufsblob fort; eine Kopplung
   wird neu begonnen. Offen bleibt, wie Altbackups aus der Zeit davor behandelt
   werden; sie werden nicht umgeschrieben.
10. **Die Zustelladresse ist an das Gerätegeheimnis gebunden.** Geht der
    private Geräteschlüssel verloren, ist es ein neues Gerät mit neuer
    Freigabe; eine Wiederherstellung der alten Adresse gibt es nicht.
11. **`e2ee_delivery_receipts.ergebnis = 2` wird nie geschrieben.** Ein
    Konflikt wirft eine Ausnahme, statt einen Rest anzulegen. Die Spalte ist
    derzeit nur für `1` belegt.
12. **`e2ee_deliveries.quittiert_am` ist ein Überrest des ersten Entwurfs.**
    Der Dienst löscht die Zeile und legt einen Rest an; die Spalte wird nicht
    mehr geschrieben.
13. **Der Aufräumlauf startet stündlich, nicht sofort beim Start.** Nach einem
    Restore können abgelaufene Zeilen bis zu einer Stunde liegen bleiben; der
    Lesepfad liefert sie seit dieser Runde nicht mehr aus.
14. **Der Rückfall auf die alte Mailbox kann nach einer verlorenen Antwort
    Duplikate erzeugen.** Der Empfänger dedupliziert logisch; der Server hält
    beide Kopien bis zur Frist.

## 10. Status

**Kein Audit. Keine Freigabe. Keine Aussage über Produktionsreife.**
Die Unterlage ist vollständig genug, um die Fragen aus Abschnitt 8 in einer
menschlichen Prüfung zu beantworten; sie beantwortet sie nicht selbst.
