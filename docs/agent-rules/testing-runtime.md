# Agenten-Regeln: Testing und Runtime

Stand: 2026-05-06  
Ergänzt die Root-`AGENTS.md`. Diese Datei ist zu lesen, wenn Tests, Runtime-Pfade, Contexts, Routing, Settings, Web/Tauri, Premium/Core-Importe, Auth, Vault, Device Key, Passkeys, Recovery, Quarantäne oder Integrity betroffen sind.

---

## 1. Grundsatz

Tests sind nicht Dekoration. Für Singra Vault sind Tests Teil der Sicherheitsarchitektur.

Ein grüner TypeScript-Check reicht nicht. Ein grüner Build reicht nicht. Eine Änderung gilt erst als fertig, wenn die betroffenen Invarianten getestet und runtime-kritische Pfade wirklich geöffnet wurden.

Nicht ausgeführte Checks müssen ehrlich genannt werden.

---

## 2. Pflicht-Tests

Immer:

- gezielte Tests für den betroffenen Bereich ausführen
- am Ende `npm run test` vollständig laufen lassen
- Timeouts nicht als Erfolg werten
- neue Security-Entscheidungen mit positiven und negativen Tests abdecken
- Tests nicht löschen oder abschwächen, nur damit der Build grün wird
- bei nicht ausführbaren Tests Grund und Restrisiko nennen

Bei Änderungen an Vault/Auth/DeviceKey/Passkey/Recovery/Quarantäne/Integrity zusätzlich testen, falls betroffen:

- `device_key_required` verhindert Master-only-Unlock
- Kategorie-Drift blockiert Vault
- Item-Drift quarantined nur betroffene Items
- quarantined Items werden nicht entschlüsselt
- Lock löscht Plaintext-Zugriff
- Lock löscht Vault-Key-Runtime-State
- Logout löscht Account- und Vault-State
- Passkey-Origin/RP-ID-Mismatch
- Passkey-Ablehnung und Abbruch
- PRF-Nichtunterstützung
- Recovery-Fehler ohne Auto-Rebaseline
- Offline-Zustand ohne untrusted Remote Trust
- keine Secrets in serialisierten Fehlern
- keine Secrets in Logs, Toasts, URLs oder Fixtures

---

## 3. Gute Tests

Gute Tests prüfen Invarianten.

Guter Test:

- benennt die Security-Regel
- prüft Erfolg und Fehlerpfad
- prüft sicheren Runtime-State
- nutzt fachliche Inputs
- vermeidet fragile Implementierungsdetails
- verhindert Regressionen
- ist verständlich

Schlechter Test:

- prüft nur "funktioniert"
- testet nur Happy Path
- nutzt `toBeTruthy()` statt konkreter Ergebnisse
- ignoriert Runtime-State
- mockt genau die Policy weg, die geprüft werden müsste
- bestätigt eine Implementierung, aber keine Invariante

Schlecht:

```ts
it("unlocks", async () => {
  expect(await unlockVault(input)).toBeTruthy();
});
```

Warum schlecht: keine konkrete Invariante, kein Fehlerfall, kein sicherer Runtime-State.

Gut:

```ts
it("blocks master-password unlock when device key is required", async () => {
  const result = await unlockWithMasterPassword({
    accountId,
    password: "correct-password",
  });

  expect(result).toEqual({
    ok: false,
    reason: "DEVICE_KEY_REQUIRED",
  });
  expect(vaultRuntimeState.hasVaultKey()).toBe(false);
});
```

Warum gut: prüft konkrete Security-Invariante und sicheren Runtime-State.

---

## 4. Runtime-Prüfung

Runtime-Prüfung ist Pflicht, wenn Änderungen berühren:

- `vite.config.ts`
- Premium/Core-Importpfade
- React Contexts
- Provider
- Hooks (`useXxx`)
- Routing
- Layouts
- Settings-Seiten
- Tauri/Web unterschiedliche Laufzeitpfade
- Auth-, Vault-, Device-Key-, Passkey-, Recovery-, Quarantäne- oder Integrity-Flows
- deutsche UI-Texte
- Build- oder Bundle-Konfiguration

Pflicht-Check:

1. Dev-Server starten oder vorhandenen Dev-Server nutzen.
2. Betroffene Route im Browser oder in Tauri wirklich öffnen.
3. Mindestens `/vault/settings` öffnen, wenn Contexts, Settings, Premium/Core-Importe oder Vault-Pfade betroffen sind.
4. Zusätzlich die konkret geänderte Seite öffnen.
5. Browser- oder Tauri-Konsole prüfen.
6. Erst wenn Route rendert und Konsole sauber bleibt, gilt die Änderung als verifiziert.

---

## 5. Premium/Core-Regressionen

Diese Fehler erkennt TypeScript oft nicht, weil sie erst durch doppelte Modulidentitäten zur Laufzeit entstehen.

Besonders prüfen auf:

- `must be used within a ...Provider`
- Hook-/Context-Fehler
- `Invalid hook call`
- doppelte Context-Instanzen
- doppelte `/@fs/` vs `/src/` Modulpfade
- Premium/Core-Importe mit unterschiedlicher Modulidentität
- Barrels, die andere Pfade erzeugen als direkte Imports

Pflicht bei betroffenen Änderungen:

```md
- [ ] /vault/settings geöffnet
- [ ] geänderte Route geöffnet
- [ ] Konsole auf Provider-/Hook-/Context-Fehler geprüft
- [ ] keine doppelte Modulidentität sichtbar
```

---

## 6. Tauri/Web-Runtime

Wenn ein Flow Web und Tauri betrifft:

- beide Pfade explizit prüfen oder nicht geprüften Pfad als Restrisiko nennen
- keine Web-Fallbacks für Tauri-Security bauen
- keine Tauri-only APIs in gemeinsame Fachlogik importieren
- Origin/RP-ID bei Passkey/WebAuthn prüfen
- Storage-Annahmen je Plattform prüfen
- Fehlermeldungen je Oberfläche prüfen

---

## 7. Testdaten und Fixtures

Regeln:

- keine echten Secrets
- keine produktionsnahen Vault-Daten
- keine echten Masterpasswörter
- keine echten Device Keys
- keine echten Recovery Secrets
- keine echten Auth Tokens
- keine personenbezogenen echten Nutzerdaten
- keine Fixtures, die später als echte Daten missverstanden werden können

Testdaten müssen klar künstlich sein.

Schlecht:

```ts
const fixture = {
  email: "real-user@example.com",
  masterPassword: "MeinEchtesPasswort123!",
};
```

Gut:

```ts
const fixture = {
  email: "test-user@example.invalid",
  masterPasswordLabel: "synthetic-test-secret-not-real",
};
```

---

## 8. Fehlerfälle testen

Security-relevante Fehler dürfen nicht nur geloggt werden.

Zu testen:

- Fehler führt zu sicherem Zustand
- Secret-State wird gelöscht
- typisierter Fehlergrund wird zurückgegeben
- kein sensibler Wert wird serialisiert
- kein unsicherer Retry
- kein Erfolg trotz Fehler
- UI zeigt handlungsfähige, aber nicht zu detaillierte Fehlermeldung

---

## 9. Abschlussbericht

Jede Übergabe muss konkret sein.

Vorlage:

```md
## Verifikation

- Geänderte Dateien:
  - <Liste>

- Sicherheitsinvarianten:
  - <welche wurden berührt?>
  - <wie wurden sie geprüft?>

- Tests:
  - [ ] npm run test
  - [ ] gezielte Tests: <Liste>

- Runtime:
  - [ ] /vault/settings geöffnet
  - [ ] geänderte Route geöffnet
  - [ ] Konsole sauber
  - [ ] Web geprüft, falls betroffen
  - [ ] Tauri geprüft, falls betroffen

- Security:
  - [ ] keine Secrets in Logs/Toasts/URLs/Fixtures/Diffs
  - [ ] Lock/Logout/Key-State geprüft, falls betroffen
  - [ ] Drift/Quarantäne geprüft, falls betroffen
  - [ ] device_key_required geprüft, falls betroffen

- Dependencies:
  - [ ] keine neue Dependency
  - [ ] oder Bewertung/ADR dokumentiert

- Nicht geprüft:
  - <ehrlich nennen>

- Restrisiken:
  - <konkret oder "keine bekannten">
```

---

## 10. Nicht akzeptabel

Nicht akzeptabel:

- "Tests nicht ausgeführt, sollte aber passen."
- "TypeScript ist grün, daher fertig."
- "Nur kleiner UI-Fix", obwohl Context/Hook/Provider betroffen ist.
- "Runtime nicht geprüft", obwohl Routing/Context/Settings betroffen ist.
- "Timeout, aber vermutlich okay."
- "Fehler geloggt und ignoriert."
- "Test angepasst", ohne die Invariante weiter zu prüfen.
- "Mock hinzugefügt", der echte Security-Policy umgeht.

---

## 11. Review-Checkliste

- [ ] Wurde der betroffene Flow verstanden?
- [ ] Sind Security-Invarianten als Tests abgedeckt?
- [ ] Gibt es positive und negative Pfade?
- [ ] Wird sicherer Runtime-State geprüft?
- [ ] Wurde `npm run test` ausgeführt?
- [ ] Wurde ein Timeout korrekt als Fehlschlag behandelt?
- [ ] Wurde Runtime geöffnet, wenn nötig?
- [ ] Wurde `/vault/settings` geöffnet, wenn nötig?
- [ ] Ist die Konsole sauber?
- [ ] Gibt es keine Secrets in Testdaten, Logs, Toasts oder Diffs?
- [ ] Sind nicht geprüfte Punkte ehrlich genannt?
