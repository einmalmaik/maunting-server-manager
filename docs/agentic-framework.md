# Agentic Framework — Konzept „Jarvis-Prinzip" (v3: Gehirn und Worker)

Stand: 18.08.2026 · Status: Konzept, nicht umgesetzt · Ersetzt v2 (Mund/Gehirn als zwei Luna-Instanzen)

**Was sich gegenüber v2 geändert hat:** Die beiden Rollen sind neu geschnitten. Der v2-„Mund" und die
Orchestrierung verschmelzen zum **Gehirn** (das Modell, mit dem der Nutzer dauerhaft redet — Charakter
und Dirigent zugleich). Die v2-Arbeitsrolle wird vervielfacht zu **Workern** (intern: Sub-Agenten;
im UI und in Übersetzungen heißt es immer „Worker"). Grundlage sind die Betreiber-Entscheidungen vom
18.08.2026. Unverändert aus v2: **keine lokale Inferenz** (kein GPU-Server, Serverressourcen gehören den
Game-Servern — die Latenzlösung ist vollständig cloud-basiert) und das bestehende Lauf-Fundament.

## 1. Ziel

Die KI soll sich wie ein menschlicher Assistent verhalten:

- **A — Sofortreaktion:** Wenige Sekunden (Ziel < 5 s) nach jeder Eingabe eine natürliche, KI-erzeugte,
  kontextbezogene Reaktion — im Chat und in der Sprachsitzung. Keine statischen Floskeln (Betreiber-Veto).
- **B — Parallelität:** Arbeit läuft parallel zum Gespräch, nicht blockierend davor. Der Mensch redet
  weiter, während Worker arbeiten — auch mehrere gleichzeitig.
- **C — Proaktive Rückmeldung:** Ergebnisse werden geliefert, sobald sie vorliegen **und** das Gespräch
  Ruhe hat — nie mitten in eine Interaktion hinein. Der technische Prozess wird nicht verbalisiert;
  der Transparenz-Modus (Werkzeug-Chips in der Worker-Ansicht) bleibt Anzeigeoption.
- **D — Selbsterkannte Langläufer:** Bei langwierigen Aufgaben sagt die KI das selbst, fragt den
  Meldekanal ab und arbeitet über Stunden weiter, auch über Prozessneustarts hinweg.
- **E — Kanäle:** Benachrichtigung über gewählte Kanäle (Chat, E-Mail, offene Sprachsitzung), auch
  mehrfach parallel; mehrere fertige Aufträge werden gebündelt geliefert.
- **F — Die KI entscheidet selbst,** was Smalltalk ist und was Arbeit, was schnell geht und was lange
  dauert — per Prompt und Werkzeugbeschreibung, nie hartcodiert.

## 2. Randbedingungen

1. **Keine lokale Inferenz.** Kein GPU-Server, keine CPU-Reserven. Jede Latenzlösung ist cloud-basiert.
   Die Sofortreaktion entsteht strukturell: Das Gehirn hat fast keine Werkzeuge und damit fast nie eine
   Werkzeugrunde — es antwortet in der kürzestmöglichen Zeit seines Modells.
2. **Latenzoptimiertes Modell als Standard-Gehirn**, z. B. GPT-5.6 Luna (0,20 $/1,20 $ je 1 Mio. Token,
   positioniert für „high-volume, latency-sensitive tasks"; Fast mode/`service_tier` als Messvariante).
   Modelle und Denkstufen kommen **immer aus dem Katalog**, nie aus dem Code — das konkrete Modell ist
   Betreiber-Konfiguration, Luna nur der Startvorschlag. TTFT muss gemessen werden, nicht angenommen
   (Abschnitt 10).
3. **Kein Fremd-Framework** (LangGraph u. ä. geprüft und verworfen). Grundlage bleibt das eigene
   Lauf-Framework: `ai_runs` + `ai_run_broker`; der Lauf überlebt den Browser, geparkte Läufe sind
   Datenbankzeilen ohne Ressourcen.
4. **Neustart-Invariante:** `running`-Läufe werden nach Neustart `failed/process_restart`, nie
   fortgesetzt; `waiting_*`-Läufe überleben unangetastet.
5. **Werkzeug-Zeitgrenze** 60 s je Aufruf; **Rückflussdeckel** 16.000 Zeichen.
6. **Projektgesetze:** KISS vor Cleverness, RBAC-Wahrheit nur im Backend, Vorschlags-/Bestätigungsfluss
   wird nie umgangen, keine Secrets in Logs/Toasts/Mails, Datenminimierung. **Die KI kann — egal in
   welcher Rolle — immer nur das, was der Benutzer selbst kann.**
7. **Chat = Realtime.** Beides ist dasselbe System mit denselben Modellen und Läufen; der Sprachmodus ist
   nur die Stimmschicht davor (heute schon so gebaut: die Sprachbrücke startet denselben Lauf wie der
   Chat). Jede Regel in diesem Dokument gilt für beide identisch.

## 3. Das Rollenmodell

### Das Gehirn (intern: Orchestrator)

Das Modell, mit dem der Nutzer dauerhaft redet — in Chat und Sprachsitzung. Es ist der **Charakter**:
kreativ, einfühlsam, menschlich. Und es ist der **Dirigent**: Es erledigt selbst keine Arbeit, sondern
deklariert sie als Aufträge an Worker.

- **Werkzeuge — genau zwei Gruppen, sonst nichts:**
  1. Die **Memory-Werkzeuge** (persönliche und Team-Erinnerungen) — Charakterwissen muss sofort
     verfügbar sein, ohne Worker-Umweg.
  2. Die **Worker-Werkzeuge**: `worker_start(auftrag, kanal, titel)`, `worker_cancel(worker_id)`.
     Werkzeugart `delegation` (läuft sofort im Handler, kein Vorschlag/Klick — es entsteht keine
     Außenwirkung, nur MSM-interne Orchestrierung).
- **Kein Serverwissen, keine Skills, kein Serverzugriff.** Das Gehirn weiß nicht, dass es Server gibt.
  Es kann strukturell keine Außenwirkung entfalten — das ist eine Sicherheitsinvariante, kein Zufall.
- **Smalltalk bleibt beim Gehirn:** „Guten Morgen", persönliche und Charakterfragen beantwortet es
  selbst, ohne Worker. Alles, was Arbeit erfordert, wird deklariert. Die Grenze steht im Prompt und in
  den Werkzeugbeschreibungen („Nicht nutzen, wenn …"-Muster), nie im Code.
- **Es spricht immer, als käme alles von ihm.** Worker-Ergebnisse liefert es menschlich und in eigener
  Stimme — nie „hier liegen Nachrichten vor", nie Prozessbeschreibung.
- **Zwischenmeldungen:** Läuft ein Worker hörbar/sichtbar lange (Schwelle ~4-8 s im Voice-Modus
  niedriger als im Chat), erzeugt das Gehirn aus Werkzeugstatus, Laufzeit und Gesprächskontext einen
  natürlichen Zwischensatz. Gedeckelt (max. 2-3 je Auftrag), kein erfundener Fortschritt: Eingabe ist
  nur der echte Werkzeugstatus.
- **Lageblock:** Das Gehirn bekommt den Lageblock (Uhr, Autonomiezustand) plus eine Worker-Zeile
  (laufende Aufträge: Titel, Zustand, Alter) als späte `system`-Nachricht — damit beantwortet es
  „Wie weit bist du?" ohne Werkzeugrunde, und das Prompt-Caching bleibt heil.
- Der Kunde wählt Modell und Denkstufe des Gehirns wie heute (aus dem Katalog).

### Die Worker (intern: Sub-Agenten)

Jeder Auftrag ist ein eigener, unbeaufsichtigter Lauf — das heutige Arbeitsmodell, nur vervielfacht.

- **~97 % der Werkzeuge:** alle Server-, Panel- und Skill-Werkzeuge; dazu das servereigene Gedächtnis
  (`server_shared`) als Arbeitswissen. Persönliche/Team-Memories haben Worker **nicht** — die gehören
  dem Charakter.
- **Rechte:** Der Worker läuft unter Identität und Rechten des Auftraggebers
  (`ActorContext.for_user(origin='ai', correlation_id=worker_id)`); Rechte werden bei jedem Segmentstart
  und jedem Wecken neu geprüft — Wegfall heißt `cancelled` plus Meldung. Voller Vorschlags-/
  Bestätigungsfluss, `immer_bestaetigen` gilt wortgleich; Autonomie (`autonomy_allows`) ersetzt höchstens
  den Klick.
- **Laufzeit:** Worker dürfen lange brauchen — Minuten bis Stunden. Langläufigkeit entsteht durch
  Parken und Wecken (`waiting_wake` + `wake_at`), nie durch Checkpoints mitten im Segment. Weckgründe:
  `timer` (Werkzeug `wait_until(minuten, grund)`, nur Workern angeboten) und `execution`
  (`finish_lifecycle_task` weckt bei Erfolg **und** Fehlschlag). Rundenbudgets, Schleifenerkennung und
  60-s-Grenze gelten je Lauf wie heute.
- **Neustart:** Ein rennendes Worker-Segment wird `failed/process_restart`; der Startabgleich sät maximal
  **einen** automatischen Wiederanlauf mit Prüfauftrag („prüfe den Stand im Verlauf, wiederhole nichts
  blind") — die persistierte Unterhaltung ist der Checkpoint. Danach ehrlicher `failed`-Endzustand mit
  Bericht. Kontingent-Erschöpfung: höchstens ein automatischer Park-Retry auf den Fensterreset
  (`grund=kontingent`), dann ehrlicher Endzustand.
- **Rückfragen (ersetzt das bisherige „ask_user wird abgewiesen"):** Der Worker weiß per Prompt, dass der
  Nutzer ihn nie direkt sieht. Braucht er eine Entscheidung, ruft er sein Frage-Werkzeug: Der Lauf parkt
  als `waiting_user`, die Frage geht als Meldung **mit Worker-ID** an das Gehirn, das sie menschlich
  stellt. Die Nutzerantwort wird über die ID genau diesem Worker zugestellt und weckt ihn — dieselbe
  Mechanik wie das bestehende Bestätigungs-Wecken, nur über die Meldestelle geroutet.
- **Modell und Denkstufe legt der Betreiber fest** (Provider-Einstellungen, aus dem Katalog) — er zahlt.
  Der Kunde stellt Worker nicht ein.
- **Keine Worker-Tiefe > 1:** Ein Worker darf keine Worker starten („ein Auftrag, der Aufträge anlegt,
  wäre ein Auftrag ohne Ende" — bestehende Regel, jetzt: nur das Gehirn deklariert).
- **Fenster:** Jeder Worker bekommt eine eigene Unterhaltung `kind='worker'` (mehrere je Benutzer
  erlaubt — **Änderung gegenüber v2**, wo der Unique-Index genau ein Hintergrundfenster erzwang; für
  `primary`/`guardian` bleibt die Eindeutigkeit als partieller Index bestehen). Die Kappe liegt jetzt im
  Handler und beim Betreiber-Deckel.
- **UI-Lebenszyklus:** Im Chat sieht man laufende Worker (Titel, Zustand, Fortschritt), kann ihren
  Verlauf öffnen und lesen — dort leben die Werkzeug-Chips des Transparenz-Modus — aber **nicht
  hineinschreiben**. Nach Abschluss verschwindet der Worker aus der Liste. Wichtig: Verschwinden heißt
  aufräumen, nicht vernichten — Unterhaltung und Audit-Einträge bleiben nach Aufbewahrungsregel
  bestehen, weil ausgeführte Remote-Befehle auditierbar bleiben müssen (Sicherheitsregel, nicht
  verhandelbar). Das Ergebnis steht ohnehin als Nachricht im Dauerchat.

### Diagramm

```
Nutzer ◄──Chat/Voice──► GEHIRN (Orchestrator, Charakter)
                          │  Werkzeuge: Memories + worker_start/worker_cancel
                          │  Smalltalk selbst · Arbeit wird deklariert
                          │
              worker_start(auftrag, kanal, titel)
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
      WORKER 1        WORKER 2        WORKER n     (je: eigener Lauf, 97 % der Tools,
      Server prüfen   Kalender        …             Nutzerrechte, Vorschlagsfluss,
          │               │               │          waiting_wake, überlebt Neustarts)
          └───────────────┴───────────────┘
                          │  Ergebnisse, Rückfragen (mit Worker-ID)
                          ▼
                    MELDESTELLE (schwärzt, sammelt, bündelt)
                          │  wartet auf Gesprächsruhe
                          ▼
        GEHIRN liefert menschlich · Chat-Nachricht · E-Mail · Voice · Glocke
```

## 4. Die Meldestelle und die Ruhe-Regel

Ein Choke-Point `ai_meldestelle.py`: `melden(db, user_id, meldung, kanaele, worker_id)` schwärzt jeden
Text (`redact_sensitive_text`) und stellt zu. Neu in v3: Sie ist auch **Sammelstelle und Puffer**.

- **Nie ins Gespräch grätschen:** Fertige Ergebnisse und Rückfragen werden pending gehalten, bis das
  Gespräch Ruhe hat. Ruhe heißt: kein aktiver Zug **und** keine laufende Nutzereingabe **und** eine
  kurze Karenz. Feste Sekunden allein sind wackelig (manche tippen langsam) — das Frontend liefert das
  Tipp-Signal (Eingabefeld nicht leer/kürzliche Eingaben), die Karenz beträgt fest 15 s. Einen
  Betreiber-Regler dafür gibt es bewusst nicht; wer den Wert verschieben will, setzt die
  Panel-Einstellung `ai_meldung_karenz_sekunden` (3-120 s, sonst gilt wieder die Vorgabe). Im
  Voice-Modus gilt der VAD-Zustand „bereit" — nie sprechen, während der Mensch spricht oder ein Zug
  läuft; Barge-in bricht auch Zwischenmeldungen und Abschlussansagen ab.
- **Bündelung:** Werden mehrere Worker fertig, während der Nutzer beschäftigt ist, liefert das Gehirn
  sie in **einer** Wortmeldung gebündelt, nicht als Salve.
- **Kanäle:** Chat (Standard, nie abwählbar — persistierte Nachricht im Dauerchat plus Briefing für den
  nächsten Gehirn-Zug), E-Mail (wählbar, über den bestehenden Ausgangskorb mit allen Toren), offene
  Sprachsitzung (implizit — das Gehirn spricht die Meldung, nie einziger Kanal), Glocke/Panel (Toast +
  Nachladen im offenen Tab). `email` heißt **zusätzlich**, nie ausschließlich.
- **Zustellgarantie:** Marke wird vor dem Versand committet — kein Doppelversand, kein Verlust beim
  Absturz. Gemeldet wird bei jedem Endzustand: Ein gescheiterter Worker sagt, was er geschafft hat und
  woran er scheiterte, plus Kosten-Hinweis („dieser Auftrag hat N Anfragen verbraucht").
- **Die Meldung ist das Ergebnis, nie der Prozess.** Werkzeugschritte sieht nur, wer die Worker-Ansicht
  öffnet.

## 5. Provider-Zweiteilung

Die Provider-/Modellkonfiguration bekommt statt „Standard-Modell" zwei Einträge:

| Rolle | Wer konfiguriert | Was |
|---|---|---|
| Gehirn (Orchestrator) | Modell: Betreiber · Denkstufe: **Kunde** (wie heute, aus dem Katalog) | latenzoptimiertes Chat-Modell, z. B. Luna |
| Worker | **Betreiber** (Modell + feste Denkstufe + Deckel) | Arbeitsmodell; darf langsam und gründlich sein |

- **Deckel beim Betreiber** (er zahlt): max. N Worker gleichzeitig je Benutzer, Rundenbudget je Worker.
  Ohne Deckel wird „schau die Server nach, mach den Kalender, und noch drei Sachen" zum unsichtbaren
  Dauerverbraucher.
- **Fallback:** Ist keine Worker-Rolle konfiguriert, gilt der heutige Ein-Modell-Betrieb (Gehirn =
  Worker-Modell, ein Lauf, heutiges Verhalten). Kein Hard-Stop.
- **Buchung:** Jeder Lauf bucht regulär (`reserve_ai_usage`). Neu zu wissen: Ein Chat-Zug kostet jetzt
  einen Gehirn-Lauf plus Worker-Läufe je Auftrag; eine Voice-Äußerung STT + Gehirn (+ Worker). Die
  Request-Rate steigt — `requests_per_minute`-Limits müssen das einpreisen (ein 5/min-Limit zerreißt
  schon heute Gespräche).

## 6. Die Abläufe

**Smalltalk:** „Guten Morgen" → das Gehirn antwortet direkt, eine Runde, kein Worker, keine Werkzeuge.
Schnellstmöglicher Fall.

**Kurze Sachfrage:** „Wie viele Server laufen?" → Gehirn reagiert sofort im Charakter („Moment, ich
schaue kurz nach") und ruft `worker_start`. Der Worker liest (eine oder zwei Runden) und meldet. Das
Gehirn liefert das Ergebnis, sobald Ruhe ist — meist nach Sekunden. **Ehrlicher Trade-off:** End-to-End
ist das eine Runde mehr als heute; gewonnen wird die sofortige Reaktion und dass das Gespräch nie
blockiert. Die Stille war das Problem, nicht die Gesamtdauer.

**Langläufer:** „Prüf heute Nacht die Backups aller Server" → Gehirn erkennt den Langläufer (Prompt-
Kriterien: Wartezeiten, Zeitpunkte, unbestimmte Dauer), fragt den Meldekanal ab, deklariert den Auftrag
(„Das dauert länger — ich melde mich."). Der Worker arbeitet in Segmenten, parkt (`waiting_wake`), wird
geweckt, holt Freigaben per E-Mail-Freigabe (weckt bei Zustimmung und Ablehnung), überlebt Neustarts.
Am Ende: Meldestelle → Chat + Mail + gesprochener Zwischenruf, wenn eine Sprachsitzung offen ist.

**Mehrere Aufträge:** „Schau nach den Servern — und kümmere dich um den Kalender" → zwei Worker,
unabhängig, unterschiedlich schnell fertig. Ergebnisse werden gebündelt geliefert, Kanäle je Auftrag
(„die Serverliste auch per E-Mail" → `kanal` des einen Workers).

**Rückfrage:** Worker braucht eine Entscheidung → parkt, Frage läuft mit Worker-ID über die Meldestelle,
das Gehirn stellt sie menschlich in der nächsten Ruhephase. Die Antwort weckt genau diesen Worker.
Erinnerung nach 24 h, wenn keine Antwort kommt (Ausbaustufe).

**Der Nutzer redet einfach weiter:** Jede neue Äußerung geht an das Gehirn — die Worker sind davon
vollständig unberührt (`vorgaenger_abloesen` wirkt nur je Unterhaltung, `aktiver_lauf` je Fenster).
„Stopp den Kalender-Auftrag" → `worker_cancel`. „Bleib dran / hol das in den Vordergrund" bleibt als
verbaler Rückweg Promptregel.

## 7. Sicherheitsinvarianten

| Invariante | Umsetzung |
|---|---|
| Rollentrennung | Das Gehirn hat keinerlei Server-Werkzeuge — die schnelle, dauerpräsente Instanz kann strukturell keine Außenwirkung entfalten. Nur Worker fassen die Außenwelt an. |
| RBAC | Jede Rolle kann nur, was der Benutzer kann. Worker: `ActorContext.for_user(origin='ai')`, `_resolve_server`/`_require_tool_permission` dreifach, Rechte-Neuprüfung bei jedem Segmentstart und Wecken. |
| Approval | Vorschlagsfluss wörtlich unverändert; `worker_start` startet nur einen Lauf und führt nichts aus; `immer_bestaetigen` gilt im Worker exakt wie überall. |
| Redaction | Ein Choke-Point (`melden()`): jede Meldung, Rückfrage, Mail und gesprochene Ansage ist geschwärzt; Gehirn-Ausgaben durchlaufen dieselbe SSE-Schwärzung wie jeder Modelltext. |
| Kontingente/Kosten | Jeder Lauf bucht regulär; Betreiber-Deckel (N Worker, Rundenbudget, feste Worker-Denkstufe); max. 1 Wiederanlauf, max. 1 Kontingent-Park-Retry je Worker. |
| Audit | Worker-Verläufe und Audit-Einträge überleben das UI-Aufräumen nach Aufbewahrungsregel — Remote-Befehle bleiben nachvollziehbar. |
| Datenminimierung | Gedächtnisblock raus aus geparktem `state_json`; Meldungen tragen geschwärzten Kurztext plus Verweis; Worker sehen keine persönlichen Memories. |
| Destruktives | Nie ohne Bestätigung — im Worker exakt wie überall. |

## 8. Was bewusst nicht gebaut wird

- **Keine lokale Inferenz** — bewusste Entscheidung (keine Hardware, keine Ressourcenkonkurrenz mit
  Game-Servern). Die Latenzlösung ist die Rollentrennung. Lokal bleibt Ausbaustufe (rein konfigurativ,
  wenn später GPU-Hardware existiert — z. B. beim Smart-Home-Ausbau).
- **Kein Fremd-Framework, keine Job-Tabelle neben `AiRun`** (ein Worker ist Lauf + Unterhaltung +
  Rahmen im `state_json` — keine zweite Zustandswahrheit), **keine Checkpoints mitten im Segment**
  (die persistierte Unterhaltung ist der Checkpoint).
- **Keine Worker-Tiefe > 1** — nur das Gehirn deklariert.
- **Keine spekulativen Parallel-Anfragen** an mehrere Anbieter für dieselbe Frage.
- **Kein Web-Push, keine Discord/Telegram/SMS-Kanäle, kein Wake-Word, keine Dauer-Sprachpräsenz** in v1.

## 9. Ausbaustufen (nach belegtem Bedarf)

1. Meldungszentrale mit Gelesen-Flag und Aufbewahrungsregel statt Glocken-Polling.
2. Erinnerungsmeldung nach 24 h für unbeantwortete Worker-Rückfragen und Freigaben.
3. Komplexitäts-Einstufung vor der Worker-Wahl (Regex/Klassifikator wählt Worker-Modell-Tier oder
   Tokenbudget je Auftrag — Kostenhebel).
4. Lokale Inferenz für die Gehirn-Rolle, sobald Hardware existiert — die Provider-Zweiteilung ist
   bewusst so gebaut, dass das ein reiner Konfigurationswechsel ist.
5. Web-Push / benutzerbezogene Webhooks nach eigener Bewertung.
6. Mehr als ein gleichzeitiger Langläufer-Typ je Worker (z. B. wiederkehrende Aufträge aus `ai_tasks`
   auf Worker-Fenster umziehen) — getrennte Entscheidung, nicht Teil dieses Vorhabens.

## 10. Offene Punkte / was zuerst gemessen oder entschieden werden muss

- **TTFT des Gehirns** (Luna, Standard vs. Fast mode) unter Produktionslast, getrennt für Chat und
  Voice (inkl. Cloud-STT-Vorlauf 1-2 s). Erst die Messung zeigt, ob „< 5 s" zuverlässig hält.
- **Ruhe-Karenz:** Startwert 15 s, feste Vorgabe; ein Betreiber-Regler ist offen. Ob die
  Tipp-Signal-Definition im Frontend (Eingabefeld nicht leer/kürzliche Eingaben) im Alltag reicht,
  muss beobachtet werden.
- **server_shared-Zuordnung bestätigen:** Empfehlung ist Worker (Arbeitswissen); persönliche/Team-
  Memories bleiben beim Gehirn. Vom Betreiber noch nicht final bestätigt.
- **Worker-Aufbewahrung:** Wie lange bleiben abgeschlossene Worker-Unterhaltungen lesbar (Vorschlag:
  bestehende Aufbewahrungsregeln übernehmen)?
- **Ton:** Doppelquittung (Gehirn reagiert doppelt) und Fehldelegation (Worker für Smalltalk bzw.
  Smalltalk-Antwort auf Arbeitsfragen) am Qualitätsprüfstand messen — kostet Komfort, nie Sicherheit.
- **Frage-Routing-Kante:** Antwortet der Nutzer auf eine Worker-Frage erst nach Stunden und hat
  dazwischen zehn andere Dinge besprochen, muss die Zuordnung über die Worker-ID robust bleiben
  (Meldung referenzieren, nicht „letzte Frage gewinnt").
- **Prozesslokalität** von Broker und Meldestelle (In-Memory) bleibt Betriebsgrenze bei mehreren
  Backend-Workern — Fallback: Polling plus persistierte Chat-Nachricht; in docs/self-hosting.md
  dokumentieren.

## 11. Anforderungsabdeckung

| Anforderung | Erfüllt durch |
|---|---|
| A — Sofortreaktion | Gehirn ohne Werkzeugrunden (nur Memories/Delegation) → kürzestmögliche Antwortzeit des Modells; latenzoptimiertes Modell; Messung entscheidet |
| B — Parallelität | N Worker in eigenen Fenstern; `vorgaenger_abloesen`/`aktiver_lauf` wirken je Unterhaltung; das Gespräch blockiert nie |
| C — Proaktiv, ohne Prozess | Meldestelle sammelt und bündelt, Ruhe-Regel, Gehirn liefert in eigener Stimme; Chips nur in der Worker-Ansicht |
| D — Langläufer | Prompt-Kriterien + Kanalfrage; `waiting_wake` + Wecken; Neustart-Re-Seed max. 1 |
| E — Kanäle | `kanal` je Auftrag (chat immer, email zusätzlich, Voice implizit); Bündelung; Doppelversand-Marke |
| F — KI entscheidet | Smalltalk-vs.-Arbeit im Gehirn-Prompt; Langläufer-Kriterien in Werkzeugbeschreibung; nichts hartcodiert |

## 12. Betroffene Bausteine (Übersicht)

| Baustein | Änderung |
|---|---|
| Provider-Registry / Einstellungen | zwei Rollen: Gehirn (Kunde: Denkstufe) und Worker (Betreiber: Modell, feste Denkstufe, Deckel) |
| ai_conversation.py | dritte Art `worker` in `ARTEN`; Eindeutigkeit nur noch für `primary`/`guardian` (partieller Index) |
| ai_run.py | `waiting_wake` in `WARTEND`, Spalte `wake_at` |
| ai_tool_registry.py | Werkzeugart `delegation`; `worker_start`, `worker_cancel`, `wait_until`, Worker-Frage-Werkzeug; Memory-Werkzeuge nur im Gehirn-Angebot, Rest nur im Worker-Angebot |
| ai_prompt.py | getrennte Promptblöcke: GEHIRN (Charakter, Smalltalk-Grenze, Liefern in eigener Stimme) und WORKER („der Nutzer sieht dich nie", Rückfragen nur per Werkzeug) |
| ai_stream_service.py | Gehirn-Lauf ohne Server-Werkzeuge; Zwischenmeldungs-Trigger; Parkstellen (`timer`/`execution`); Abschlusshaken → Meldestelle |
| ai_run_service.py | Weckpfad `waiting_wake`, Wiederanlauf im Startabgleich, Rechteprüfung beim Wecken, Frage-Routing über Worker-ID |
| operation_task_service.py | `finish_lifecycle_task` weckt den Lauf (Erfolg und Fehlschlag) |
| scheduler_service.py | zweiter Handgriff im bestehenden 60-s-Takt für fällige `wake_at` |
| ai_meldestelle.py (neu) | `melden()` — schwärzender Meldepunkt, Sammel-/Pufferstelle, Ruhe-Regel, Bündelung, Frage-Routing |
| ai_voice_bridge.py | spricht ausschließlich Gehirn-Ausgaben; Ruhe = VAD „bereit"; Barge-in bricht alles ab |
| Frontend | Worker-Liste im Chat (einsehbar, nicht beschreibbar, räumt sich auf), Tipp-Signal für die Ruhe-Regel, Nachladen bei Zustellung; Singra/UI und Design-DNA beachten |
| Rechte | `ai.background.use` (Worker-Nutzung je Rolle abschaltbar) + `permissionDetails`-Zusage |
| Tests | Gehirn hat nie Server-Werkzeuge; Worker starten keine Worker; Wecken nur mit Rechten; Frage-Routing über Worker-ID; Neustart-Re-Seed genau einmal; Schwärzung aller Meldewege; Audit überlebt UI-Aufräumen |

Entfällt gegenüber v2: die Mund-Rolle als eigene Instanz (verschmolzen ins Gehirn), das eine
Hintergrundfenster mit Unique-Index-Kappe (ersetzt durch N Worker-Fenster mit Handler- und
Betreiber-Deckel), „ask_user wird in unbeaufsichtigten Läufen abgewiesen" (ersetzt durch das
Frage-Werkzeug mit Weckroutung).
