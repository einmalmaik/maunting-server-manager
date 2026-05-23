# Agenten-Regeln: Beispiele für guten und schlechten Code

Stand: 2026-05-06  
Ergänzt die Root-`AGENTS.md`. Diese Datei ist zu lesen, wenn Codequalität, Wartbarkeit, Scope, Abstraktion, Fehlerbehandlung, Tests, Dependencies oder Refactoring betroffen sind.

---

## 1. Leitbild

Guter Code ist nicht "clever". Guter Code bleibt verständlich, sicher und änderbar, wenn in einem Jahr ein anderer Entwickler oder Agent denselben Flow anfassen muss.

Schlechter Code ist in Singra Vault ein Sicherheitsrisiko.

---

## 2. Was ist schlechter Code?

Schlechter Code hat:

- unklare Verantwortlichkeiten
- falschen Scope
- globalen Zustand ohne zwingenden Grund
- lokale Security-Policies, obwohl zentrale Regeln nötig sind
- unnötige Abstraktion
- versteckte Seiteneffekte
- Copy-Paste-Logik
- schwache Typen oder `any`
- unklare Namen
- fehlende Tests
- unsaubere Fehlerbehandlung
- unnötige Dependencies
- implizite Runtime-Annahmen
- Logs oder Fehlermeldungen mit sensiblen Details
- "temporäre" Fallbacks in Security-Pfaden
- UI-Code, der Fachlogik entscheidet
- Services, die UI-Texte, Toasts oder Routing kennen

---

## 3. Was ist guter und wartbarer Code?

Guter und wartbarer Code hat:

- klare Verantwortlichkeit
- passenden Scope
- explizite Datenflüsse
- starke Typen
- kleine, gut benannte Einheiten
- sichere Defaults
- minimale öffentliche API
- gezielte Tests
- nachvollziehbare Fehlerbehandlung
- dokumentierte Security-Entscheidungen
- wenige, gut begründete Dependencies
- einfache Kontrollflüsse
- erkennbare Fehlerpfade
- wenig magische globale Zustände
- keine unnötige Generalisierung

Wartbarer Code ist Code, bei dem ein neuer Entwickler oder Agent schnell beantworten kann:

- Was ist die Aufgabe dieser Einheit?
- Welche Inputs sind erlaubt?
- Welche Outputs sind möglich?
- Welche Fehler sind erwartet?
- Welche Security-Invariante schützt dieser Code?
- Wo wird Plaintext erzeugt, genutzt und gelöscht?
- Welche Tests beweisen das Verhalten?

---

## 4. Lokaler Scope vs. zentrale Policy

### Schlecht: lokale UI-Policy

```ts
function UnlockPanel() {
  function canUnlockWithMasterPassword(state: VaultState): boolean {
    return state.hasMasterPassword;
  }

  return <button disabled={!canUnlockWithMasterPassword(state)}>Entsperren</button>;
}
```

Warum schlecht:

- ignoriert `device_key_required`
- versteckt Security-Policy in UI-Code
- erzeugt Drift zwischen Web und Tauri
- ist schwer zentral zu testen
- andere Unlock-Flows können andere Regeln nutzen

### Gut: zentrale Policy-Funktion

```ts
export function canUseMasterPasswordUnlock(policy: UnlockPolicy): boolean {
  return policy.hasMasterPassword && !policy.deviceKeyRequired;
}
```

Warum gut:

- zentrale Entscheidung
- testbar
- gleiche Logik für Web und Tauri
- kein Master-only-Fallback
- klare fachliche Benennung

---

## 5. Globaler Scope vs. lokaler UI-Code

### Schlecht: globale Funktion ohne echten Bedarf

```ts
export function formatSettingsHeadline(username: string): string {
  return `Einstellungen für ${username}`;
}
```

Warum schlecht:

- verschmutzt die globale API
- erzeugt unnötige Importfläche
- suggeriert Wiederverwendbarkeit, die nicht existiert
- erhöht Kopplung

### Gut: lokaler UI-Code

```ts
function SettingsHeader({ username }: { username: string }) {
  const headline = `Einstellungen für ${username}`;
  return <h1>{headline}</h1>;
}
```

Warum gut:

- Verantwortung bleibt dort, wo sie gebraucht wird
- keine unnötige API
- weniger Kopplung
- leichter zu ändern

---

## 6. Unnötige Abstraktion vs. expliziter Orchestrator

### Schlecht: Pipeline ohne echtes Problem

```ts
type PipelineStep<T> = {
  name: string;
  run: (input: T) => Promise<T>;
};

class VaultUnlockPipeline<T> {
  constructor(private steps: PipelineStep<T>[]) {}

  async execute(input: T): Promise<T> {
    let current = input;
    for (const step of this.steps) {
      current = await step.run(current);
    }
    return current;
  }
}
```

Warum schlecht, wenn der Flow nur wenige stabile Schritte hat:

- abstrahiert ein Problem, das nicht existiert
- erschwert Debugging
- versteckt die fachliche Reihenfolge
- macht Security-Schritte weniger sichtbar
- lädt zu generischen Erweiterungen ein

### Gut: expliziter Orchestrator

```ts
export async function unlockVault(input: UnlockInput): Promise<UnlockResult> {
  const policy = await unlockPolicyService.load(input.accountId);
  assertUnlockAllowed(policy, input);

  const key = await vaultKeyService.deriveForUnlock(input, policy);
  return vaultOpenService.openWithKey({ accountId: input.accountId, key });
}
```

Warum gut:

- Reihenfolge ist lesbar
- Security-Checks sind sichtbar
- fachliche Einheiten bleiben testbar
- keine unnötige Framework-Schicht
- Fehlerpfade können gezielt typisiert werden

---

## 7. Fragiler Quickfix vs. sichere Architekturänderung

### Schlecht: mehrere Sicherheitsprobleme in einem Helper

```ts
export let currentVaultKey: string | null = null;

export async function unlock(anyInput: any) {
  currentVaultKey = localStorage.getItem("vault_key");

  if (!currentVaultKey) {
    currentVaultKey = anyInput.password;
  }

  console.log("unlock with", currentVaultKey);
  return true;
}
```

Warum schlecht:

- globaler Key-State
- `any`
- Key-Material in `localStorage`
- Master-only-Fallback
- Secret-Logging
- kein Fehlerpfad
- vermischt Storage, Auth und Vault-Key-Handling
- Erfolg wird ohne echte Prüfung zurückgegeben

### Gut: klarer Vertrag ohne Storage-Bypass

```ts
export async function unlockWithMasterPassword(
  input: MasterPasswordUnlockInput,
): Promise<UnlockResult> {
  const policy = await unlockPolicyService.loadForAccount(input.accountId);

  if (policy.deviceKeyRequired) {
    return { ok: false, reason: "DEVICE_KEY_REQUIRED" };
  }

  const vaultKey = await vaultKeyService.deriveFromMasterPassword(input);
  return vaultSessionService.open({ accountId: input.accountId, vaultKey });
}
```

Warum gut:

- kein globaler Key-State
- kein Storage-Bypass
- `device_key_required` wird explizit beachtet
- Ergebnis ist typisiert
- Tests können beide Pfade prüfen
- Fachlogik ist von UI getrennt

---

## 8. Fehler verschlucken vs. sicherer Fehlerpfad

### Schlecht

```ts
try {
  await activateDeviceKey();
} catch (error) {
  console.log(error);
  return true;
}
```

Warum schlecht:

- möglicher Secret-Leak
- Erfolg trotz Fehler
- Device-Key-Invariante gebrochen
- kein sicherer Runtime-State
- keine auditierbare Klassifikation

### Gut

```ts
try {
  return await activateDeviceKey();
} catch (error) {
  const safeError = classifyDeviceKeyActivationError(error);
  await vaultRuntimeState.clearSensitiveState();
  securityEventService.record(safeError.event);
  return { ok: false, reason: safeError.reason };
}
```

Warum gut:

- kein Secret-Leak
- sicherer Zustand
- typisiertes Ergebnis
- auditierbares Security Event
- UI kann sicher reagieren

---

## 9. Drift falsch behandeln vs. Quarantäne respektieren

### Schlecht

```ts
if (hasDrift) {
  await saveNewBaseline(remoteState);
  return decryptAll(remoteState.items);
}
```

Warum schlecht:

- übernimmt untrusted Remote State
- entfernt Beweise
- entschlüsselt potenziell manipulierte Items
- macht Drift unsichtbar
- bricht Quarantäne-Logik

### Gut

```ts
if (integrity.categoryDrift) {
  await vaultRuntimeState.clearSensitiveState();
  return { ok: false, reason: "CATEGORY_DRIFT_BLOCKED" };
}

const safeItems = integrity.items.filter((item) => !item.quarantined);
return decryptAllowedItemsOnly(safeItems);
```

Warum gut:

- blockiert strukturellen Drift
- entschlüsselt quarantined Items nicht
- erzwingt sicheren Zustand
- lässt Audit-/Recovery-Flow möglich
- reduziert Schaden auf erlaubte Items

---

## 10. Dependency aus Bequemlichkeit vs. eigener minimaler Code

### Schlecht

```ts
import leftPad from "left-pad";

export function normalizeCode(code: string) {
  return leftPad(code, 6, "0");
}
```

Warum schlecht:

- externe Supply-Chain für triviale Logik
- unnötige Auditfläche
- kein Sicherheitsnutzen
- zusätzliche transitive Risiken möglich

### Gut

```ts
export function normalizeRecoveryCode(code: string): string {
  return code.trim().padStart(6, "0");
}
```

Warum gut:

- verständlich
- testbar
- keine zusätzliche Angriffsfläche
- fachlich benannt

---

## 11. Direkte Crypto-Library-Nutzung vs. Fassade

### Schlecht

```ts
import { encrypt } from "random-crypto-helper";

export async function saveVaultItem(item: VaultItem, password: string) {
  const encrypted = encrypt(JSON.stringify(item), password);
  return storage.save(encrypted);
}
```

Warum schlecht:

- unklare Crypto-Library
- Fachlogik nutzt Crypto direkt
- Passwort statt klarer Key-Typ
- keine Authenticated Data
- keine dokumentierte Kapselung
- Migration schwer

### Gut

```ts
export async function saveVaultItem(
  item: PlainVaultItem,
  key: VaultContentKey,
): Promise<SaveVaultItemResult> {
  const sealed = await vaultCryptoService.sealItem({
    plaintext: encodeVaultItem(item),
    key,
    context: {
      itemId: item.id,
      schemaVersion: item.schemaVersion,
    },
  });

  return vaultStorageService.saveSealedItem(sealed);
}
```

Warum gut:

- Crypto hinter Service gekapselt
- Key-Typ fachlich eindeutig
- Kontext ist explizit
- Storage erhält nur versiegelte Daten
- Tests können Crypto- und Storage-Grenze getrennt prüfen

---

## 12. Schlechter Test vs. Invariantentest

### Schlecht

```ts
it("unlocks", async () => {
  expect(await unlockVault(input)).toBeTruthy();
});
```

Warum schlecht:

- prüft keine Invariante
- kein Fehlerfall
- kein Runtime-State
- kein konkretes Ergebnis
- kann bei unsicherem Verhalten grün bleiben

### Gut

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

Warum gut:

- prüft konkrete Security-Invariante
- prüft sicheren Runtime-State
- verhindert Master-only-Fallback
- Ergebnis ist typisiert

---

## 13. Copy-Paste-Policy vs. zentrale Regel

### Schlecht

```ts
// Web unlock
const canUnlock = hasPassword && !isLocked;

// Tauri unlock
const canUnlock = hasPassword && deviceKey !== null;

// Recovery unlock
const canUnlock = hasPassword || hasRecoverySecret;
```

Warum schlecht:

- drei unterschiedliche Policies
- Drift entsteht unbemerkt
- Tests müssen Verhalten mehrfach absichern
- Recovery kann Security-Regeln umgehen

### Gut

```ts
const decision = unlockPolicyService.evaluate({
  accountId,
  method,
  runtime,
  recoveryState,
});
```

Warum gut:

- zentrale Policy
- Runtime explizit
- Recovery explizit
- testbar
- neue Unlock-Methoden müssen durch dieselbe Entscheidung

---

## 14. Versteckter Side Effect vs. sichtbare Operation

### Schlecht

```ts
function useVaultItems() {
  const [items, setItems] = useState([]);

  useEffect(() => {
    syncAndMaybeRebaseline();
    loadAndDecryptEverything().then(setItems);
  }, []);

  return items;
}
```

Warum schlecht:

- Hook synchronisiert, rebaselined und entschlüsselt heimlich
- Side Effects sind aus UI heraus schwer erkennbar
- Quarantäne kann umgangen werden
- Tests müssen versteckte Lifecycle-Pfade erraten

### Gut

```ts
function useVaultItems() {
  return useVaultItemsQuery({
    decryptPolicy: "allowed-items-only",
    integrityRequired: true,
  });
}
```

Warum gut:

- Absicht ist sichtbar
- Entschlüsselungs-Policy ist explizit
- Integrity ist explizit
- Hook bleibt UI-nah

---

## 15. Review-Fragen für jeden Codevorschlag

Vor Übernahme eines Codevorschlags prüfen:

- Verstehe ich den Datenfluss?
- Ist die Verantwortung dieser Datei klar?
- Ist der Scope richtig?
- Wird eine Security-Policy lokal versteckt?
- Gibt es globalen Zustand ohne zwingenden Grund?
- Gibt es `any`, unklare Typen oder magische Strings?
- Gibt es neue Seiteneffekte?
- Gibt es neue Dependencies?
- Werden Secrets irgendwo sichtbar?
- Gibt es Tests für die betroffene Invariante?
- Muss Runtime geöffnet werden?
- Ist der Code in einem Jahr noch verständlich?
