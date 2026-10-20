# Messenger-Beta: Tiefen-Diagnose & Architektur-Konzept

**Stand:** 20.10.2026 · **Umfang:** Analyse **und** Umsetzung der Phasen 0–3 sowie der Basis von Phase 6
(Branch `fix/messenger`). Phasen 4 und 5 sind weiterhin Vorschlag; der Umsetzungsstand steht in
Abschnitt 6 und am Ende.
**Betroffene Clients:** Web-Frontend/Panel, MSS-App (Windows/Android, Tauri-Hülle um `frontend/`), Pondys Browser (`frontend/src/browser`).

---

## 0. Kernbefund in drei Sätzen

1. Die Sicherheitswarnung und die Geräteliste unter *Profil → Geräte* lesen **zwei verschiedene Wahrheiten**: die Warnung prüft eine clientseitige Signaturkette, die Liste zeigt `is_approved` des Servers. Beide können dauerhaft auseinanderfallen.
2. Eigene Nachrichten synchronisieren nicht, weil das **Chatgeheimnis** eines Direktchats an neue eigene Geräte nur bis zur ersten Zustellung an die Gegenseite verteilt wird — danach nie wieder. Das neue Gerät liest die alte Mailbox, alle anderen schreiben in die neue.
3. Push bei geschlossener App existiert **nur für Browser** (WebPush + Service Worker). MSS hat weder Service Worker im Tauri-Fenster noch FCM (Android) noch WNS (Windows).

---

## 1. Problem A — Geister-Warnung & „Unbekanntes Gerät"

### A.1 Zwei Wahrheiten

| | Quelle | Kriterium |
|---|---|---|
| Warnung `ownUnverifiedDeviceWarning` | `vertrauteGeraete()` — `frontend/src/services/e2eeGeraet.ts:773` | Signaturkette im `localStorage` (`msm_vertraute_geraete:<userId>`) |
| Liste *Profil → Geräte* | `getE2eeGeraete(id, true)` — `frontend/src/components/social/E2eeGeraeteCard.tsx:83` | `is_approved` des Servers |

Die Karte zeigt ausschließlich `is_approved === false` als „Wartet" (`E2eeGeraeteCard.tsx:340`) und sonst kein Abzeichen. Der Zustand „Server sagt freigegeben, Vertrauenskette sagt keine gültige Unterschrift" ist damit **unsichtbar** — und genau er löst die Warnung aus: `offen.length > 0` → `melde({typ:'unbestaetigt'})` → Systemzeile (`frontend/src/pages/Messenger.tsx:1496-1501`).

### A.2 Wie eine Zeile „freigegeben, aber nicht vertraut" entsteht

`_auffrischen` in `backend/services/e2ee_device_service.py:210`:

```python
neuer_schluessel = bestand.public_key_jwk != public_key_jwk or (
    bool(signatur) and (bestand.signing_public_key_jwk or "") != signatur)
if neuer_schluessel:
    bestand.approved_by = None
    bestand.approval_signature = None
    andere = _freigegebene(db, user.id).filter(UserE2eeDevice.id != bestand.id).count()
    if andere > 0:
        bestand.is_approved = False
```

`approved_by` und `approval_signature` werden **immer** gelöscht, `is_approved` aber **nur**, wenn es noch ein anderes freigegebenes Gerät gibt. Bei einem Konto mit einem einzigen freigegebenen Gerät bleibt `is_approved = true` stehen → die Liste zeigt „freigegeben", die Signaturkette findet nie wieder eine gültige Freigabe. `vertrauteGeraete` schreibt nur *vertraute* Fingerabdrücke zurück, die Zeile bleibt dauerhaft `offen` → **Warnung bei jedem Öffnen**.

Ausgelöst wird das durch den Signaturschlüssel, der im Client nachwächst: `vollstaendig()` (`frontend/src/services/e2eeGeraet.ts:252`) münzt ein neues ECDSA-Paar, sobald `signaturPaar` fehlt (Altbestand, gescheitertes Schreiben, verschlüsselte Ablage nach PIN-Reset). Gleiches Muster, wenn `eigenesGeraet()` wegen unlesbarer IndexedDB eine neue Kennung erzeugt: die alte Zeile bleibt „freigegeben" stehen.

Weitere Divergenzquellen:
- **Altbestand:** `20260923_06` setzte `is_approved` per Default auf `true`, `approved_by`/`approval_signature` sind `NULL`. Nach einem Schlüsselwechsel ist so eine Zeile dauerhaft unbestätigt.
- **Verschluckter Fehler:** `geraetVeroeffentlichen()` läuft als `.catch(() => {})` (`frontend/src/services/e2eeIdentity.ts:63`). Ein `409 FremdeSitzungError` (andere Refresh-Familie) heißt: lokal neuer Schlüssel, Server alter Schlüssel → eigene Warnung, sichtbar nirgends.

Die Einträge sind also **nicht** verwaist; sie sind real und serverseitig freigegeben — nur ohne Unterschrift über den aktuellen Schlüssel.

### A.3 Warum das Web als „Unbenanntes/Unbekanntes Gerät" erscheint

`user_e2ee_devices` hat **kein** Client-/User-Agent-Feld, nur `label` (`backend/models/user_e2ee_device.py:69`). Der Web-Client ruft `geraetVeroeffentlichen()` **ohne Argument** (`e2eeIdentity.ts:63`) → `label = ''`. `_auffrischen` überschreibt nur mit nicht-leerem Label, und `_name_der_einladung()` füllt nur bei gekoppelten Geräten (über `DevicePairing.family`) — ein normaler Browser-Login hat keine Familie. Die Karte fällt auf `t('profile.e2eeDevices.unnamed')` = **„Unbenanntes Gerät"** zurück (`E2eeGeraeteCard.tsx:333`). Die MSS-App übergibt dagegen eine `bezeichnung` (`frontend/src/desktop/auth.ts:111`).

### A.4 Verifikation (ohne Code, vor jedem Fix)

```sql
SELECT device_id, label, is_approved, approved_by,
       approval_signature IS NOT NULL AS sig, auth_family, last_seen_at
FROM user_e2ee_devices WHERE user_id = <id>;
```

Eine Zeile mit `is_approved = true` und ohne `sig`/`approved_by` ist der Geist.
In der Browser-Konsole zusätzlich: eigenes `device_id` und `sha256(public_key)` gegen `localStorage['msm_vertraute_geraete:<id>']` vergleichen.

---

## 2. Problem B — Self-Sync fehlt, Verläufe driften

### B.1 Der Sendeweg ist grundsätzlich richtig gebaut

`baueZustellungen()` (`frontend/src/services/ratchetSitzung.ts:623`) fächert je Fremdgerät **und je eigenem Zweitgerät** einen Umschlag auf. Gelesen wird aus **beiden** Mailboxen (Geheimnis-Kennung + Altkennung), `holeGeraeteSteuerung` holt die Geheimnisse vorher (`frontend/src/hooks/useKonversation.ts:387-402`). `logischeUuid()` schneidet den Gerätezusatz ab, damit alle Geräte dieselbe Nachrichtenkennung führen (`ratchetSitzung.ts:333`).

### B.2 Kernfehler: das Chatgeheimnis erreicht neue eigene Geräte nie

`dmZiele` (`frontend/src/services/gruppenSchluessel.ts:1048`):

```ts
if (eintrag && erzeuge && !eintrag.verteiltAm) {
  erreicht = await verteileDmGeheimnis(eigeneId, peerId, eintrag.geheimnis) // Gegenstelle + eigene Geräte
  if (erreicht > 0) { /* merkeDmVerteilung */ }   // erreicht zählt NUR die Gegenstelle
}
```

Sobald die **Gegenstelle** einmal erreicht ist, wird `verteiltAm` gesetzt — `verteileDmGeheimnis` (das auch die eigenen Zweitgeräte bedient) läuft nie wieder. Ein danach gekoppeltes, neu installiertes oder zurückgesetztes Gerät kennt das Geheimnis nicht und liest nur die Altkennung, während jedes migrierte Gerät **ausschließlich** in die Geheimnis-Mailbox schreibt.

Folge: einseitige Unsichtbarkeit. Gerät B schreibt in die Alt-Mailbox → A liest beide → A sieht B. A schreibt nur in die neue → B sieht nichts, auch nicht die für B selbst gebaute Kopie.

### B.3 Warum die Verläufe auseinanderdriften („MSS zeigt 29. September")

1. **Kein Nachladen über das Fenster hinaus:** `fetchE2eeEnvelopes` liefert ohne `since_id` die neuesten 100 Umschläge, Aufbewahrung 30 Tage (`get_blind_envelopes`, `backend/services/social_service.py:2069`; Route `backend/routers/social.py:839`).
2. **Die Geräte-Mailbox ist genauso begrenzt:** `holeGeraeteSteuerung` liest mit `since = geraeteStand` (RAM, nach Neuladen `0`) → wieder nur die neuesten 100. Ein Geheimnis weiter hinten wird nie wieder gesehen.
3. **Getrennte lokale Ablagen:** MSS läuft unter `tauri.localhost`, das Panel unter der Panel-Domain → zwei IndexedDB-Bestände. Gemeinsame Wahrheit ist nur die Server-Mailbox — und die liest B wegen B.2 nicht.
4. **Kein Steuerkanal zu eigenen Geräten:** Quittungen, Bearbeiten, Löschen gehen laut Kommentar bewusst nur an die Gegenseite (`useKonversation.ts:709`): „Ein Umschlag an das eigene Konto ist in einer Chat-Mailbox verboten … die eigenen übrigen Geräte erfahren von einer Wirkung also erst über den Gerätekanal — wäre eine eigene Runde wert." Diese Runde fehlt. Folge: Häkchen bleiben auf „Gesendet" (`Messenger.tsx:1852`), Bearbeiten/Löschen erreicht das Zweitgerät nie.

### B.4 Die Brücke zu Problem A

`baueZustellungen` holt eigene Geräte über `eigenesVerzeichnis()` → `verzeichnisVon()` → `holeGeraete()` → **`vertrauteGeraete()`-Filter** (`frontend/src/services/e2eeGeraet.ts:971`). Ein Gerät, das die Warnung aus A auslöst, ist aus dieser Liste entfernt — es bekommt keine Nachricht, kein Chatgeheimnis, keinen Gruppenschlüssel. Für die Gegenseite filtert zusätzlich `geraete(..., nur_bestaetigt=True)` (`backend/routers/social.py:348`).

Der Satz „Es bekommt nichts" ist also korrekt. Die Liste zeigt es nur als freigegeben an. **A und B sind derselbe Fehler von zwei Seiten.**

---

## 3. Problem C — Benachrichtigungen bei geschlossener App

### 3.1 Ist-Zustand (vollständig — aber nur im Browser)

Vorhanden: VAPID-Schlüsselpaar und Verschlüsselung (`backend/services/webpush_service.py`), Konto- und Mailbox-Zustellung, `push`-Listener in `frontend/public/sw.js` (Vordergrund-Erkennung, Echo-Schutz, Zero-Knowledge-Text), Abo über `frontend/src/services/pushAbo.ts` und `frontend/src/services/mailboxPush.ts`, gestartet aus `frontend/src/components/notifications/ServerIncidentNotifier.tsx:199`.

### 3.2 Warum es in MSS trotzdem nicht ankommt

1. **Kein Service Worker im Tauri-Fenster:** `abonniere()` bricht bei `getRegistration() === undefined` ab — der im Code dokumentierte Normalfall im Tauri-Fenster (`pushAbo.ts:86`). Ohne Abo keine Adresse, also nichts zuzustellen.
2. **Android hat kein FCM:** Im `smart-system/src-tauri/gen/android/app/src/main/AndroidManifest.xml` stehen nur `INTERNET`, `POST_NOTIFICATIONS` und AlarmManager-Receiver für Kalendererinnerungen — kein Firebase, kein Dienst, kein `google-services`.
3. **Windows hat kein WNS:** `benachrichtigung_senden` (`frontend/src/lib/benachrichtigung.ts`) läuft **im** WebView-Prozess: nur solange die App lebt und die WebSocket-Verbindung steht.
4. **Server unterdrückt Push bei irgendeiner offenen Verbindung:** `prepare_push_dispatch` → `has_active_subscribers(recipient)` → `None` (`backend/services/notification_service.py:294`). Ein im Tray laufendes MSS-Fenster ohne Messenger-Tab verhindert die OS-Meldung.
5. **Geheimnis-Mailboxen:** dort ist `target_recipient_id = None`, Zustellung nur über `sende_an_mailbox` — also ausschließlich über Mailbox-Push-Abos. Ohne Push-Abo ist der Kanal zu.

### 3.3 Wie WhatsApp/Snapchat das lösen

- **Android:** FCM (`google-services.json`, Firebase-Messaging-SDK). Der Push-Dienst des OS hält die Verbindung, nicht die App. Eine *high-priority* Data-Message weckt den Prozess auch im Doze-Stand; dieser entschlüsselt, holt Inhalte nach und zeigt die Meldung über `NotificationManager`/Channel.
- **iOS (Vergleich):** APNs, gleiches Prinzip.
- **Windows:** WNS bzw. der Web-Push-Dienst des Browsers. WNS verlangt Paketidentität (MSIX / Sparse Package) und eine registrierte App; das OS hält den Kanal, die App wird per COM-Aktivator/BackgroundTask geweckt.
- **Gemeinsam:** Der Inhalt reist nie im Klartext mit. Es geht ein Wecksignal mit Metadaten hinaus; der Klartext entsteht erst auf dem Gerät.

**Was fehlt:** eine pro **E2EE-Gerät** registrierte OS-Zustelladresse (nicht pro Konto, nicht pro Browser), ein Weckpfad außerhalb des WebView-Prozesses, und ein Server, der je Gerät entscheidet statt über `has_active_subscribers`.

---

## 4. Architektur-Vorschlag Background-Push

Ein Kanal je Plattform, eine gemeinsame Serverschnittstelle:

| Plattform | Transport | Registrierung | Weckpfad |
|---|---|---|---|
| Web/PWA | WebPush (vorhanden) | `pushAbo` + `mailboxPush` | Service Worker (vorhanden) |
| Android | FCM HTTP v1 (Service-Account-JSON in `.env`) | `POST /social/e2ee/device-push` mit `device_id` + Token | `FirebaseMessagingService` → NotificationChannel |
| Windows | WNS über Sparse-MSIX-Identität **oder** (schneller) Tray-Prozess mit eigener WS-Verbindung + Autostart | gleiche Route, `kind: "wns"` | COM-Aktivator → Toast; Fallback Tray |

Server-seitig:

- Neue Tabelle `device_push_subscriptions(device_id, family, kind, endpoint/token, p256dh, auth, created_at, last_error)` — 1:1 zu `user_e2ee_devices`, Kaskade bei Geräteentfernung.
- `webpush_service` bekommt einen zweiten Versender für FCM/WNS. Allowlist und kurze Timeouts (AGENTS.md 18) gelten analog.
- `relay_blind_envelope` schickt zusätzlich ein **Wecksignal je Zielgerät** — ohne Absender, ohne Inhalt, nur `mailbox_id` + `message_id`. Ersetzt `has_active_subscribers` als Entscheidungsgrundlage.
- Jede Entzugsaktion (Gerät entfernen, Logout, Reset, Replay-Sperre) löscht die Push-Zeile mit.

---

## 5. Konzept: eine Geräteverwaltung für alle Plattformen

Die beiden Apps teilen sich bereits eine Sicherheitsseite: `frontend/src/desktop/einstellungsreiter/SicherheitEinstellungen.tsx` wird von MSS (`Einstellungen → Sicherheit`) **und** vom Pondys Browser (`MSS-Dienste → Sicherheit`) gerendert. Der günstigste, regressionsärmste Weg ist deshalb **eine** neue Sektion dort — kein Nachbau je Client.

1. Neue Gruppe „Geräte" in `SicherheitEinstellungen` (nur bei `social_enabled`): Inhalt = vorhandene `E2eeGeraeteCard` plus `AiDevicePairingCard` — exakt die Zusammensetzung aus `frontend/src/pages/profile/DevicesTab.tsx`. `DevicesTab` wird danach nur noch ein Wrapper.
2. Koppeln bleibt, wo es ist (MSS-Wizard → `/auth/devices/redeem`), und bekommt in der Sektion einen Einstieg „Neues Gerät koppeln".
3. **Nachweis-Reihenfolge (Sicherheitsauflage, AGENTS.md 21/41/53):** Koppeln verlangt `frischer_nachweis_fehlt(...)` mit Zweck `device_pairing` (`backend/routers/auth.py:611`) — das ist bereits so. Die UI bietet die Wege in dieser Reihenfolge an: **Passkey (nur wenn auf diesem Gerät verfügbar) → Authenticator/TOTP → Passwort → Backup-Code**. `useZweitfaktor({ backup: true })` (`frontend/src/components/auth/ZweitfaktorNachweis.tsx:34`) kann das; `webauthnVerfuegbar()` (`frontend/src/services/passkeyService.ts:196`) entscheidet, ob Passkey angeboten wird.
4. **In der App ist ein Passkey nie lokal:** Die RP-ID ist der Panel-Host (AGENTS.md 53). Im Tauri-Fenster läuft der Nachweis über `passkeyNachweis('device_pairing')` → Browser-Bestätigung mit Zahl; im Pondys Browser ist die RP-ID die Panel-Domain, dort geht es direkt.
5. Freigeben/Entfernen braucht **kein** neues Backend: beides ist bereits unterschriftsbasiert (`gebeGeraetFrei`/`entferneGeraet`). Die Sektion markiert das eigene Gerät als „dieses Gerät" und zeigt dafür keinen Knopf.

### Nebenbefund: zwei Gerätelisten ohne gemeinsame Kennung

`/auth/devices` (gekoppelte Sitzungen, `device_pairing_service`) und `/social/e2ee/devices` (Messenger-Schlüssel) sind zwei Wahrheiten. „Prüfe Profil → Geräte" meint die zweite. Wer in MSS ein Gerät entkoppelt, sieht es in der Messenger-Liste weiter als „freigegeben". Vorschlag: eine Zeile je Gerät mit beiden Fakten, verknüpft über `auth_family` ↔ `family`.

---

## 6. Schritt-für-Schritt-Plan

### Phase 0 — Diagnose am echten System (**umgesetzt**)

`backend/scripts/diagnose_messenger_geraete.py` (nur lesend):

```
python backend/scripts/diagnose_messenger_geraete.py                  # alle Konten mit Geräten
python backend/scripts/diagnose_messenger_geraete.py --konto anna    # ein Konto
python backend/scripts/diagnose_messenger_geraete.py --konto 42 --json --nur-probleme
```

Je Gerät: `is_approved`, `approved_by`, Unterschrift vorhanden, `auth_family`, Label und ein
**Urteil** (`gedeckt`, `erstes-geraet`, `ohne-unterschrift`, `wartend`), das den Teil der Regel
aus `vertrauteGeraete` nachbaut, den die Datenbank beantworten kann. Für `ohne-unterschrift`
nennt die Ausgabe den erwarteten Abdruck `sha256(public_key)` neben dem `localStorage`-Schlüssel
`msm_vertraute_geraete:<userId>` — der Vergleich bleibt Handarbeit im Browser, weil die
Vertrauensliste nur dort liegt.

**Bis 20.10.2026 hiess dieses Urteil `geisterwarnung`** und behauptete, das Gerät bekomme nichts.
Das war falsch: ein freigegebenes Gerät ohne Unterschrift ist beim ersten Gerät eines Kontos und
nach `wieder_freigeben` der Normalfall, und der Client hält es für vertraut, solange sein
Fingerabdruck in der eigenen Liste steht. Aus der Datenbank ist ein Schlüsselwechsel gar nicht
ablesbar — `_auffrischen` setzt dabei seit 10/2026 `is_approved` zurück. Das Skript nennt jetzt
den Kandidaten zum Gegenprüfen, statt eine Zustellung zu behaupten
(`test_diagnose_messenger_geraete.py`).

Was das Skript **nicht** kann (und nicht soll): die Kopplungssicht (`/auth/devices`) prüfen und
Server-Logs lesen. Dafür bleiben die Schritte 3 und 4 der alten Fassung:
`GET /social/e2ee/devices/<id>?include_unapproved=true` gegen `GET /auth/devices` halten, und im
Journal nach `409 FremdeSitzung` sowie `410`/`503` bei WebPush sehen.

### Phase 1 — Label & Vertrauensanzeige (**umgesetzt**, klein, risikolos)
- **Bezeichnung:** `services/geraeteBezeichnung.ts` bildet `Web-Frontend · Chrome/Windows`,
  `MSS-App · Windows`, `Pondys Browser · Android` (Produkt + Plattform, bewusst ohne
  Übersetzung — der Name steht in der Datenbank und wird jedem angezeigt).
  `geraetVeroeffentlichen()` nutzt sie **nur, wenn das Feld leer ist**: erst melden, die Antwort
  ansehen, und nur bei leerem Label nachtragen. Ein aus der Kopplung stammender Name
  („Arbeitsrechner") bleibt damit stehen — `_auffrischen` überschreibt ihn sonst.
- **Vertrauensanzeige:** `E2eeGeraeteCard` prüft die geladene Liste mit `vertrauteGeraete`
  (nur die freigegebene Teilmenge — ein wartendes Gerät darf nicht durch bloßes Ansehen in die
  Vertrauensliste rutschen) und zeigt bei einer Freigabe ohne Unterschrift „Ohne gültige
  Freigabe" samt Erklärung. Drei Zustände sind damit sichtbar: freigegeben & gedeckt,
  freigegeben & nicht gedeckt, wartend.
  Der Knopf „Freigabe erneuern" war hier bewusst offen und ist mit Phase 2 dazugekommen
  (`/social/e2ee/devices/self/reapprove`, frischer Nachweis).
- Tests: `services/geraeteBezeichnung.test.ts` (neu), `services/e2eeGeraet.test.ts`
  („Der Name in der Geräteliste"), `components/social/E2eeGeraeteCard.test.tsx`
  („ohne Unterschrift", „deckt alle Freigaben", „prüft nur freigegebene Geräte").
  Am Stand davor rot: `füllt ein leeres Label …` (`hochgeladen` hatte Länge 1) und beide
  Karten-Tests (kein Aufruf von `vertrauteGeraete`, kein Abzeichen).

### Phase 2 — Selbstheilung der Freigabe (**umgesetzt**)

**Server, `services/e2ee_device_service.py`:**
- `_auffrischen` lässt `is_approved` bei einem **ersetzten** Schlüssel nicht mehr stehen — auch
  nicht beim einzigen Gerät. `approval_signature` **und** `approved_by` fallen mit, der Eintrag ist
  ehrlich „wartend“. Damit gibt es den Zustand „freigegeben ohne Unterschrift“ nicht mehr neu; er
  bleibt nur als Altbestand, und den zeigt die Karte an (Phase 1).
- **Auch ein nachgereichter Signaturschlüssel ist ein Wechsel** (Korrektur nach der Prüfung vom
  20.10.2026). Er war kurz als Ausnahme gebaut, damit der Altbestand nach dem Update nicht
  nachbestätigen muss — und genau darin lag ein Loch: wer das Token dieses Geräts hat, legt unter
  derselben Kennung seinen eigenen Signaturschlüssel ab, bleibt freigegeben und unterschreibt
  damit Freigaben für weitere Geräte (Regel 21: ein Access-Token darf nie zu dauerhaftem Zugang
  werden). Der Preis ist eine einmalige Nachbestätigung für Geräte von vor 09/2026; sie läuft
  über die Unterschrift eines freigegebenen Geräts oder `wieder_freigeben`.
- `bestaetigen` hat **keine Ausnahme mehr** für das letzte Gerät. Bis 20.10.2026 wurde ein
  wartendes Gerät ohne jede Unterschrift freigegeben, sobald kein freigegebenes mehr existierte —
  damit holte sich jeder mit einem Access-Token wieder Zutritt (Regel 21).
- **Reparaturweg:** `wieder_freigeben(db, user, device_id)` gibt ein wartendes Gerät nur frei,
  wenn es **kein** freigegebenes Gerät mehr gibt; `approved_by`/`approval_signature` bleiben leer
  („freigegeben“ ≠ „beglaubigt“). Route: `POST /social/e2ee/devices/self/reapprove`, Zweck
  `e2ee_reapprove` (neu in `ZWECKE_ANGEMELDET`, `PasskeyZweck`, `auth.browserBestaetigung.purpose`).
  Der Nachweis läuft über den gemeinsamen Helfer `_geraete_nachweis` (2FA-Faktor, sonst Passwort,
  sonst `RESET`), hinter `auth_rate_limit`; `/self/reset` nutzt denselben Helfer.
- **Geprüft wird vor dem Verbrauch des Nachweises** (Korrektur 20.10.2026): `freigabe_hindernis`
  beantwortet „Gerät unbekannt“ und „es gibt noch ein freigegebenes Gerät“ mit 404/403, **bevor**
  Passkey oder Einmalcode eingelöst werden. Sonst verbrannte eine veraltete Geräteliste einen
  gültigen Nachweis für eine Antwort, die ohnehin abgelehnt wird.
- **Gelesen und geschrieben unter einer Kontosperre** (`SELECT … FOR UPDATE` auf die Kontozeile):
  Zwei wartende Geräte sahen sonst gleichzeitig „kein freigegebenes“ und gaben beide frei — die
  Unterschrift des ersten wäre umgangen. Test mit zwei eigenen Verbindungen, an einer Barriere
  gestartet; ohne die Sperre ist er rot.

**Karte, `E2eeGeraeteCard.tsx`:** Ist das eigene Gerät wartend und gibt es kein anderes
freigegebenes, bietet sie „Dieses Gerät wieder zulassen“ an — je Faktor (Passkey/TOTP/Passwort/
`RESET`) und mit dem Hilfetext, dass die Freigabe damit **nicht beglaubigt** ist. Gibt es ein
anderes freigegebenes Gerät, steht dort der Verweis auf dessen Unterschrift. Nebenbefund behoben:
Passwort- und Bestätigungsfeld waren nativ und sind jetzt `PasswordInput`/`Input` (Regel 4).

**Entscheidung vom 20.10.2026 — bewusst so gelassen:** Die Selbstfreigabe ist auf allen Clients
erreichbar (Panel, MSS, Pondys, Android; native Clients sind mit gültigem Bearer-Token von CSRF
befreit), aber nie mit dem bloßen Token: 2FA-Faktor, sonst Passwort, sonst bei Konten ohne Passwort
das Wort `RESET`, hinter `auth_rate_limit`, und nur wenn kein freigegebenes Gerät mehr existiert.
Bewusster Rest: Ein reiner Social-Login ohne Passwort und ohne 2FA bestätigt faktisch mit der
Sitzung. Das ist strenger als der Zustand davor (ein wartendes Gerät wurde ohne jede Eingabe
freigegeben, sobald kein freigegebenes mehr existierte) und bleibt so, damit der Fall „einziger
Nutzer, alles verloren" bedienbar ist. Verworfen: zusätzlicher OAuth-/Mail-Link, Passwort **und**
Faktor, Freigabe per Wiederherstellungsschlüssel. Wer das später verschärft, muss den Ausfallweg
mitdenken — sonst sperrt sich das einzige Gerät aus.

**Karte, Nachtrag:** Der Nachweis wird jetzt **innerhalb** von Laufanzeige und Fehlerbehandlung
geholt. Vorher stand `await faktor.nachweis(…)` beim Knopf davor: ein abgebrochener Passkey warf
ohne Meldung, und die Knöpfe blieben während der Abfrage bedienbar.

Tests: `test_e2ee_device_approval.py` — `test_schluesselwechsel_beim_einzigen_geraet_laesst_es_warten`,
`test_nachgereichter_signaturschluessel_ist_ein_wechsel`,
`test_wieder_freigeben_nur_ohne_anderes_freigegebenes_geraet`,
`test_wieder_freigeben_verlangt_frischen_nachweis`, `test_wieder_freigeben_mit_zweitem_faktor`,
`test_zwei_wartende_geraete_geben_sich_nicht_gegenseitig_frei`,
`test_abgelehnte_freigabe_verbraucht_keinen_code`;
`E2eeGeraeteCard.test.tsx` — „bietet einem einzigen wartenden Gerät den Weg zurück ohne Neuanfang“,
„verweist bei einem anderen freigegebenen Gerät auf dessen Unterschrift“, „meldet einen
abgebrochenen Nachweis, statt still zu scheitern“.
**Am Stand davor rot:** alle fünf Backend-Tests der ersten Fassung (Worktree auf `HEAD`: 5 failed /
15 passed) und beide Karten-Tests; die drei später ergänzten Tests wurden einzeln gegen den
jeweils reparierten Code geprüft (Sperre entfernt, Vorabprüfung entfernt, Nachweis zurück vor das
`try`) und waren dort rot.

### Phase 3 — Chatgeheimnis zuverlässig an eigene Geräte (**umgesetzt**)

- `DmGeheimnis.verteiltAn: string[]` merkt je **Gerätekennung**, wer das Geheimnis bekommen hat;
  `verteiltAm` bleibt die Marke für den Umzug (die Gegenstelle zählt weiter allein dafür).
- `verteileDmGeheimnis` liefert `{ gegenstelle, eigene }` statt einer Summe, und `anJedesGeraet`
  gibt die tatsächlich erreichten Kennungen zurück (vorher nur eine Zahl). Ein optionaler Filter
  schickt einen Nachtrag nur an die Geräte, denen noch etwas fehlt.
- `dmZiele` verteilt **bei jedem Versand** nach, solange `eigeneGeraeteOhne(eigeneId, verteiltAn)`
  eine unbekannte Kennung findet. Ein danach gekoppeltes, neu installiertes oder zurückgesetztes
  Gerät bekommt das Geheimnis damit mit der nächsten Nachricht — vorher lief die Verteilung genau
  einmal im Leben eines Chats, und das neue Gerät las eine Mailbox, in die niemand mehr schrieb.
- `merkeDmVerteilung` wächst nur (Liste wird vereinigt, `verteiltAm` nie zurückgenommen).
- **Weitergabe auf der grösseren Kennung (`DmGeheimnis.beleg`).** Erzeugen darf nur das kleinere
  Konto, und `nimmDmGeheimnis` prüft das am Absender. Das galt bis 20.10.2026 auch für den
  Nachtrag an ein eigenes Gerät: hatte die **grössere** Seite das Geheimnis von der kleineren
  bekommen, trug ihr Umschlag an das eigene Zweitgerät ihre eigene Kennung — das Gerät wies ihn
  ab, während der Absender es in `verteiltAn` vermerkte. Kein späterer Versand versuchte es je
  wieder, und das neue Gerät blieb für immer ohne Geheimnis. Jetzt merkt sich der Empfänger den
  **unveränderten Originalumschlag** (`beleg`) und gibt genau den weiter; die Unterschrift bleibt
  die der kleineren Kennung, und die Erzeugungsregel bleibt unangetastet. Fehlt der Beleg, wird
  nicht gesendet — lieber beim nächsten Versand erneut als das Gerät fälschlich als bedient
  vermerken.

Tests: `gruppenSchluessel.test.ts` — „holt ein eigenes Gerät nach, das erst nach der Zustellung
dazukommt“ und „holt ein eigenes Gerät auch auf der grösseren Kontokennung nach“ (**beide am
Stand davor rot**).

### Phase 4 — Steuerkanal für eigene Geräte (**offen**)
- Quittungen, Bearbeiten, Löschen, Reaktionen zusätzlich als Steuerumschlag in die **eigene Geräte-Mailbox** (`deriveUserDeviceMailboxId`), nicht in die Chat-Mailbox. `holeGeraeteSteuerung` verarbeitet bereits `dm_secret`/Gruppenschlüssel — ein weiterer `typ` genügt.
- Wirkung: Häkchen und Bearbeitungen stimmen auf allen eigenen Geräten.
- Tests: `useKonversation.test.ts` (Steuerversand enthält einen Auftrag an die Geräte-Mailbox), `ratchetSitzung.test.ts`.

### Phase 5 — Push (**offen**)

Begründung für den Aufschub: das braucht neue Infrastruktur (FCM-Service-Account, WNS-Kanal,
Tabelle + Route + Versender) und ist ohne Gerät/Release nicht am laufenden System prüfbar. Die
Phasen 2/3 beheben den Datenverlust; Push bleibt die Lücke „App zu, keine Meldung“.

- Tabelle + Route + FCM/WNS-Versand wie in Abschnitt 4; Android-Manifest mit `FirebaseMessagingService` und Channel; Windows-Tray als erster Schritt, WNS/MSIX als zweiter.
- `prepare_*_push` entscheidet **je Gerät** (`device_id` im Wecksignal) statt über `has_active_subscribers`.
- Entzug räumt die Push-Zeile mit ab (`austragen_familie` erweitern).
- Tests: `test_webpush_service.py` (Wecksignal ohne Inhalt, Entzug räumt ab), neu `test_device_push.py`; Laufzeitprobe: App beenden, Nachricht senden, Meldung innerhalb 5 s.

### Phase 6 — Geräteansicht in MSS und Pondys (**umgesetzt, Basis**)
- Neue Gruppe „Geräte“ in `SicherheitEinstellungen` (nur bei `social_enabled`), Inhalt aus
  `GeraeteKarten` in `pages/profile/DevicesTab.tsx`: `AiDevicePairingCard` (an `ai.chat.use`
  gebunden) und `E2eeGeraeteCard` (am Messenger). `DevicesTab` ist jetzt nur noch ein Wrapper —
  eine Zusammensetzung für Profil und Sicherheitsseite, kein Nachbau je Client.
- Kennnt der Aufrufer `social_enabled` schon, fragt `GeraeteKarten` `/settings/public` nicht noch
  einmal; sonst bleibt der bisherige Abruf.
- Tests: `SicherheitEinstellungen.test.tsx` — „rendert beide Bereiche bei aktiven Modulen“ prüft
  jetzt die Geräte-Gruppe mit (am alten Stand rot: „Geräte“ fehlt).
- **Offen:** eigener Einstieg „Neues Gerät koppeln“ in der Sektion. Koppeln bleibt vorerst beim
  MSS-Wizard (`/auth/devices/redeem`); der Weg dorthin ist von der Sektion aus nicht verlinkt.
  Die Nachweis-Reihenfolge Passkey → TOTP → Passwort → Backup-Code steckt bereits in
  `useZweitfaktor({ backup: true })` und wird von den Freigabe-/Reset-Knöpfen der Karte genutzt.

---

## 7. Risiken & offene Punkte

- **Phase 2 sperrt nicht mehr aus**: das einzige Gerät ist nach einem Schlüsselwechsel wartend und
  holt sich mit frischem Nachweis zurück (`/self/reapprove`). Was bleibt: wer **weder** Passwort
  noch 2FA noch `RESET`-Bestätigung hat, kommt nicht zurück — das ist gewollt.
- **Phase 3 erzeugt mehr Umschläge** in der Geräte-Mailbox (Fenster 100, 30 Tage), aber nur wenn
  tatsächlich ein eigenes Gerät fehlt. Danach ist die Liste vollständig und es wird nicht mehr
  nachgetragen. Offen bleibt die Ack-Bindung, wenn viele Geräte dazukommen.
- **WNS** braucht Paketidentität; der Tray-Weg ist der realistische erste Schritt.
- **iOS** ist nicht betrachtet (kein Client im Repo).
- **Nicht am laufenden System verifiziert:** welcher der Divergenzpfade aus A.2 die konkrete Geisterzeile erzeugt hat. Phase 0 klärt das vor jedem Codeänderungsschritt.

---

*Umsetzungsstand 20.10.2026: Phase 0, 1, 2, 3 und die Basis von Phase 6 sind im Branch `fix/messenger` umgesetzt. Phase 4 (Steuerkanal für eigene Geräte) und Phase 5 (Push bei geschlossener App) bleiben Vorschlag.*

### Verifikation vom 20.10.2026

- **Frontend:** `vitest run` über `gruppenSchluessel`, `e2eeGeraet`, `geraeteBezeichnung`,
  `E2eeGeraeteCard`, `SicherheitEinstellungen`, `DevicesTab`, `useKonversation` — 177 Tests grün;
  `tsc` ohne Fehler; `npm run build`, `check:i18n` (8180 Schlüssel, Parität de/en) und
  `check:classes` grün.
- **Backend:** `tests/test_e2ee_device_approval.py` (20), dazu Blind-Mailbox,
  Geräte-Mailbox, Passkey-Browser und Passkey-2FA — 105 Tests grün
  (`MSM_TEST_DATABASE_URL` auf den Wegwerf-Container, eigenes Redis-Fach: mit dem geteilten
  Fach 0 lief ein Rate-Limit-Zähler aus einem früheren Lauf in `test_abgelaufen_wird_abgelehnt`).
- **Alter Stand:** Worktree auf `HEAD` mit den neuen Testdateien — 8 Frontend-Tests und 5
  Backend-Tests rot. Ohne das wäre nicht bewiesen, dass die Tests den Fehler prüfen (Regel 31).
- **Nicht am laufenden System verifiziert:** Zwei echte Clients (Panel + MSS) gegen einen Server.
  Das bleibt die Laufzeitprobe für Phase 3: Gerät B nach der ersten Nachricht koppeln und
  nachsehen, dass die nächste Nachricht ankommt.
