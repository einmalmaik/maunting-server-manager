# TEST READY: Privacy-Focused WhatsApp-Alternative Communication Suite

## Executive Summary

> **Korrektur vom 20.09.2026.** Dieses Dokument behauptete, alle Suiten
> arbeiteten „without artificial mock bypasses, rigorously asserting all privacy
> invariants". Für zwei der hier geführten Suiten war das falsch: Sie
> definierten ihren Prüfgegenstand selbst und prüften dann diese Definition.
>
> - `insertableStreamsE2ee.test.ts` beschrieb auf 484 Zeilen ein eigenes
>   RTP-Rahmenverfahren mit 21-Byte-Trailer und belegte dessen Sicherheit. MSM
>   schreibt kein solches Verfahren; Anrufe laufen über LiveKit.
> - `circularVideoNotes.test.ts` prüfte eine Gestenfunktion, die es im
>   Produktionscode nicht mehr gab, und ein Verschlüsselungsformat
>   `sv-blob-v1:`, das der Client so nie erzeugt hat.
>
> Beide sind ersetzt (siehe Abschnitte 3 und 4). Die übrigen Suiten in diesem
> Dokument wurden bei dieser Gelegenheit **nicht** geprüft — die Aussagen über
> sie stammen unverändert aus der ursprünglichen Fassung und sind entsprechend
> zu behandeln.

Die Prüfung, ob eine Testdatei ihren Gegenstand überhaupt importiert, ist billig
und hätte beides gefunden:

```bash
grep -c "^import.*from '@/" frontend/src/test/e2e/*.ts
```

---

## Verification Results Summary

Gemessen am 20.09.2026, je Datei einzeln:

- `frontend/src/services/livekitRaum.test.ts`: **9 / 9**
- `frontend/src/test/e2e/circularVideoNotes.test.ts`: **7 / 7**
- `frontend/src/components/social/CircularVideoNoteRecorder.test.tsx`: **16 / 16**
- `frontend/src/lib/videoSettings.test.ts`: **12 / 12**
- `frontend/src/utils/localePersistence.test.ts`: **8 / 8**
- `backend/tests/test_chat_media_security.py`: **30 / 30**
- Typprüfung: `node node_modules/typescript/bin/tsc --noEmit` ohne Befund
  (`npx tsc` greift hier zu einer anderen Fassung und meldet stillen Erfolg).

Die früher genannte Gesamtzahl von 66 E2E-Tests ist damit hinfällig; sie zählte
die beiden ersetzten Suiten mit.

---

## Test Inventory & Artifacts

### 1. WebRTC & Media Deterministic Test Harness
- **Path**: `frontend/src/test/fakeWebRtc.ts`
- **Features Provided**:
  - `FakeRTCPeerConnection`: Full WebRTC peer connection mock with insertable streams via `createEncodedStreams()` (`ReadableStream` and `WritableStream`), SDP offer/answer negotiation, ICE candidate exchange, state tracking (`connectionState`, `signalingState`, `iceConnectionState`), and deterministic simulation helpers.
  - `FakeRTCRtpSender` & `FakeRTCRtpReceiver`: Per-sender and per-receiver `createEncodedStreams()` and `replaceTrack()` support.
  - `FakeMediaStreamTrack`: Audio and video track mock with `enabled`, `muted`, `readyState`, constraints, and settings.
  - `FakeMediaStream`: Track collection, audio/video track filtering, cloning.
  - `FakeMediaRecorder`: Full recording lifecycle (`start`, `stop`, `pause`, `resume`, `requestData`), MIME type support validation, chunk dispatch via `ondataavailable`.
  - `installFakeWebRtc()`: Global installer replacing `RTCPeerConnection`, `MediaRecorder`, `MediaStream`, `MediaStreamTrack`, and `navigator.mediaDevices` (`getUserMedia`, `getDisplayMedia`, `enumerateDevices`) with clean `restore()` function.

---

### 2. Frontend Calling E2E Test Suite (Tiers 1-4)
- **Path**: `frontend/src/test/e2e/webrtcCalling.test.ts`
- **Coverage (17 Tests)**:
  - **Tier 1 (Happy Path)**:
    - 1.1 Outgoing call initiation: transitions `idle` -> `outgoing` -> `connecting` -> `active`.
    - 1.2 Incoming call reception: banner accept -> `connecting` -> `active`.
    - 1.3 Mode toggle: dynamic addition/removal of video tracks on peer connection.
    - 1.4 Device selection: camera/mic/speaker selection using Design-DNA `Dropdown` with **ZERO** native `<select>` elements in DOM.
    - 1.5 Mute/Camera controls: toggle track enabled states without dropping peer connection.
    - 1.6 Screen sharing: acquires display media, replaces video sender track, reverts on stop.
    - 1.7 Signaling exchange: offer, answer, and ICE candidate exchange.
    - 1.8 Call teardown: clean track disposal, peer connection close, leave message dispatch.
  - **Tier 2 (Boundaries & Errors)**:
    - 2.1 Camera/mic permission denied (`NotAllowedError`) handles error gracefully.
    - 2.2 Device enumeration with zero speakers handles absence of `setSinkId` cleanly.
    - 2.3 Remote peer abrupt disconnect triggers auto-cleanup.
    - 2.4 Rapid mute/unmute control toggling preserves deterministic final state.
    - 2.5 Extraneous signals received after call closed are safely ignored.
  - **Tier 3 (Pairwise Combinations)**:
    - 3.1 Mode (Audio/Video) x Device Switch x Mute State.
    - 3.2 Call Direction x Screen Share x Teardown Trigger.
  - **Tier 4 (Real-World Workloads)**:
    - 4.1 Complete 1-on-1 Blind Call Session with mid-call device swap and clean hangup.
    - 4.2 Presentation Workload with screen share toggle, display media stream, and camera fallback.

---

### 3. Anruf-Verschlüsselung: was MSM selbst tut
- **Pfad**: `frontend/src/services/livekitRaum.test.ts`
- **Ersetzt** `frontend/src/test/e2e/insertableStreamsE2ee.test.ts` (gelöscht am 20.09.2026).
- **Warum ersetzt**: Die alte Datei definierte `encryptRtpPayload`,
  `decryptRtpPayload`, `deriveNonce` und `buildAad` selbst und prüfte dann diese
  Definitionen. Kein einziger Import aus dem Produktionscode. Das geprüfte
  Rahmenverfahren existiert in MSM nicht: Medien werden von LiveKit
  verschlüsselt (`ExternalE2EEKeyProvider` plus Worker), und MSMs eigener Anteil
  ist die Schlüsselverwaltung darum herum.
- **Abdeckung (9 Tests)**, alle gegen `services/livekitRaum.ts`:
  - **Das Tor**: Ein Browser ohne E2EE-Unterstützung bekommt keine Verbindung,
    sondern `E2eeNichtUnterstuetzt` — der Anruf wird nicht still unverschlüsselt
    geführt. Ein Fehler beim Prüfen gilt als „kann nicht", nicht als Zustimmung.
  - **Die Reihenfolge**: Schlüssel setzen und E2EE einschalten geschieht
    *vor* `connect`. Sonst gäbe es ein Fenster, in dem Frames unverschlüsselt
    hinausgehen. Geprüft als exakte Abfolge, nicht als Vorhandensein.
  - **Schlüsselwechsel** erreicht den Anbieter (Nachzügler mit eigenem Schlüssel).
  - **Der Raum**: `videoCodec: 'vp8'` bleibt, weil LiveKit-E2EE nur dafür in
    allen unterstützten Browsern geprüft ist; Auflösung, Bildrate und Bitrate
    kommen aus der Profilwahl statt aus einem festen Preset;
    `degradationPreference: 'balanced'`.

---

### 4. Runde Videonotizen
- **Pfade**: `frontend/src/test/e2e/circularVideoNotes.test.ts` (die Kette) und
  `frontend/src/components/social/CircularVideoNoteRecorder.test.tsx` (die Komponente)
- **Warum neu geschrieben**: Die alte Fassung prüfte eine Gestenfunktion, die im
  Produktionscode nicht mehr existierte, ein Verschlüsselungsformat
  `sv-blob-v1:`, das der Client nie erzeugt hat, und Objektliterale, die zwei
  Zeilen über der Behauptung entstanden. Ihr „Validator" gab am Ende unbedingt
  `false` zurück; die Prüfungen darüber waren toter Code, und der Test stellte
  nur Fragen, auf die `false` die richtige Antwort war. Die Komponente wurde
  darin nie gerendert — deshalb fiel keiner der im September 2026 gemeldeten
  Fehler auf.
- **Die Kette (7 Tests)**, gegen `services/medienKrypto.ts` und `lib/videoNotiz.ts`:
  - Aufnahme, data-URL, echte Verschlüsselung, Entschlüsselung: bitgenau zurück.
  - Das Aufnahmeformat reist mit (H.264/MP4 oder VP9/WebM, je nach Gerät).
  - Kein rohes Video, kein Dateiname und kein Typ im Blob.
  - Was der Größenwächter durchlässt, bleibt hochgerechnet unter
    `MAX_MEDIA_BYTES`; vor der Anhebung auf 60 MB lag dieselbe Notiz darüber.
  - Wächter und Prüfung im Sendepfad stehen nicht auf derselben Zahl.
  - Die Bitrate ist so gerechnet, dass eine volle Minute hineinpasst.
- **Die Komponente (16 Tests)**, gegen `CircularVideoNoteRecorder.tsx`:
  - Das Mikrofon kommt aus `lib/audioSettings.ts`, nicht aus `audio: true`.
  - Der Blob entsteht nach `onstop`, nicht nach einer Frist; die Kamera läuft
    bis dahin weiter; alle Stücke sind enthalten.
  - Das Häkchen sendet beim ersten Tippen, ohne vorherige Geste.
  - Eine abgelehnte Kamera führt zu Abbruch mit Meldung, nicht zu einem leeren
    Vollbild.
  - Die Zeitgrenze beendet genau einmal und schreibt der Notiz ihre echte Dauer
    zu, nicht 1 s.

---

### 5. Backend Blinded Signaling & Privacy Invariants E2E Test Suite (Tiers 1-4)
- **Path**: `backend/tests/test_webrtc_blind_signaling_e2e.py`
- **Coverage (15 Tests)**:
  - **Tier 1 (Happy Path & Invariants)**:
    - 1.1 Rendezvous pairing: initiator (count=1), receiver (count=2), `peer_joined` event.
    - 1.2 2-peer room cap: 3rd peer rejected with `RoomFullError` (code 4003).
    - 1.3 Opaque signal relay: blind forward of encrypted ciphertext envelope intact.
    - 1.4 Disconnect & room eviction: `peer_left` notification, session anti-replay tombstoning.
    - 1.5 Zero caller-callee records in database: verifies no `calls` or `call_logs` table exists in DB.
    - 1.6 Zero audit log entries: WebRTC signaling records **ZERO** entries in `audit_logs`.
    - 1.7 Blind token in JSON payload: token sent in first WebSocket frame, never in URL query string.
    - 1.8 ICE servers configuration format: STUN/TURN server URLs validated.
  - **Tier 2 (Boundaries & Errors)**:
    - 2.1 Room TTL cleanup: 120s TTL sweep evicts expired rooms.
    - 2.2 Relay before partner joins returns `False`.
    - 2.3 Relay to nonexistent session raises `SessionNotFoundError`.
    - 2.4 Relay from unknown peer raises `PeerNotInSessionError`.
  - **Tier 3 (Concurrency & State)**:
    - 3.1 Concurrency isolation: multiple rooms operate concurrently without cross-talk.
    - 3.2 Active session count tracking: accurately tracks creation and destruction.
  - **Tier 4 (Real-World Workload)**:
    - 4.1 Complete 1-on-1 Blind Call Session lifecycle from join to leave.

---

## How to Run the Tests

### Frontend

`--maxWorkers=3` ist auf diesem Rechner Pflicht. Ohne die Angabe meldet vitest
einen Lauf als erfolgreich, der gar nicht stattgefunden hat.

```bash
cd frontend && npx vitest run --maxWorkers=3 src/test/e2e/
```

Die Prüfungen zu Anruf-Verschlüsselung und Videonotiz liegen nicht mehr
vollständig unter `src/test/e2e/`, sondern bei dem Code, den sie prüfen:

```bash
cd frontend && npx vitest run --maxWorkers=3 src/services/livekitRaum.test.ts src/services/medienKrypto.test.ts src/components/social/CircularVideoNoteRecorder.test.tsx src/lib/videoSettings.test.ts src/utils/localePersistence.test.ts
```

### Backend

```bash
cd backend && venv\Scripts\pytest.exe tests\test_webrtc_blind_signaling_e2e.py tests\test_chat_media_security.py -v
```

### Frontend Production Build Verification
```bash
cd frontend
npm run build
```

---

## Invariant Verification Checklist
- [x] **Zero Metadata Invariant**: No caller-callee mappings, call logs, or IP records persisted in database.
- [x] **Zero Audit Log Invariant**: No entries created in `audit_logs` table for signaling actions.
- [x] **Design-DNA Compliance**: Zero native `<select>` elements in calling device picker; all selections use Design-DNA `Dropdown`.
- [ ] ~~**WebRTC Frame Cryptor Standard**: Nonce is 12B, Trailer is exactly 21B, AAD binds `SSRC || Timestamp || KeyEpoch`.~~
  **Zurückgezogen am 20.09.2026.** MSM implementiert kein eigenes
  Rahmenverfahren. Die Zahlen stammten aus `insertableStreamsE2ee.test.ts`, das
  sie selbst definierte. Medien verschlüsselt LiveKit; was MSM zusagt, steht
  unter Abschnitt 3.
- [ ] ~~**Eavesdropping Interception Security**: Intercepted ciphertext is unparseable and leaks zero plaintext.~~
  **Zurückgezogen.** Derselbe Ursprung. Die Aussage mag für LiveKits
  Verschlüsselung zutreffen, belegt war sie hier nicht.
- [x] **Anruf-Verschlüsselung ist nicht abschaltbar**: Ein Browser ohne
  E2EE-Unterstützung bekommt keine Verbindung, und der Schlüssel steht vor
  `connect`. Geprüft in `services/livekitRaum.test.ts`.
- [x] **Videonotizen**: Der Anhang verlässt den Client nur verschlüsselt, der
  rohe Inhalt taucht im Blob nicht auf, und was der Größenwächter durchlässt,
  nimmt der Server an. Die frühere Zusage „Gesture lock requires delta Y > 50px"
  ist gegenstandslos: Die Wischgeste wurde am 20.09.2026 entfernt, weil sie den
  Sendeknopf abfing.
- [x] **Room Cap & TTL**: Strict 2-peer room cap, 120s ephemeral room TTL.
