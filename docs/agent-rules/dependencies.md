# SAgenten-Regeln: Dependencies

Stand: 2026-05-06  
Ergänzt die Root-`AGENTS.md`. Diese Datei ist zu lesen, wenn neue Libraries, Major-Updates, Crypto/Auth/Storage/Logging/Telemetry-Abhängigkeiten, Build-Tooling oder transitive Dependency-Flächen betroffen sind.

---

## 1. Grundsatz

Jede Dependency ist ein Supply-Chain-Risiko.

Eine neue Bibliothek ist nur erlaubt, wenn sie einen klaren Sicherheits-, Wartbarkeits- oder Plattformnutzen hat. Komfort allein reicht nicht.

Neue Dependencies sind besonders kritisch, wenn sie berühren:

- Plaintext
- Vault Keys
- Device Keys
- Masterpasswort
- Auth Tokens
- Storage
- Sync
- Backup
- Recovery
- Passkeys/WebAuthn
- Crypto
- Logging
- Telemetrie
- Error Reporting
- Build- und Packaging-Prozess

---

## 2. Harte Verbote

Verboten:

- unmaintained Crypto-, Auth-, Storage- oder Secret-Libraries
- Libraries mit unklarem Sicherheitsmodell
- Libraries, die sensible Daten an externe Dienste senden
- Telemetrie-, Analytics- oder Error-Reporting-Libraries im Vault-Pfad ohne Datenschutzentscheidung
- Dependencies, die Web/Tauri-Pfade durch Polyfills oder globale Side Effects unklar machen
- Komfort-Libraries für triviale Logik
- neue Crypto/Auth/Storage/Secret-Libraries ohne ADR oder Security-Doku
- Major-Updates in Security-Pfaden ohne Changelog-, Test- und Runtime-Prüfung
- direkte Nutzung von Crypto-Libraries aus Fachlogik heraus
- Pakete, die globale Prototypen, globale Error-Handler oder globale Storage-Verhalten verändern

---

## 3. Pflichtprüfung vor neuer Dependency

Vor jeder neuen Dependency dokumentieren:

| Kriterium | Muss beantwortet werden |
|---|---|
| Zweck | Welches konkrete Projektproblem löst sie? |
| Notwendigkeit | Warum reicht vorhandener Code oder eine Plattform-API nicht? |
| Security | Berührt sie Plaintext, Keys, Auth, Storage, Sync oder Recovery? |
| Wartung | Wie aktiv wird sie gepflegt? |
| Advisories | Gibt es Security Advisories oder offene CVEs? |
| Transitive Fläche | Wie groß ist die transitive Dependency-Fläche? |
| API | Ist die API klein, verständlich und schwer falsch zu benutzen? |
| Plattform | Läuft sie in Web und Tauri zuverlässig? |
| Bundle | Ist Größe und Angriffsfläche vertretbar? |
| Lizenz | Ist die Lizenz kompatibel mit Projekt und Distribution? |
| Kapselung | Wird sie hinter Adapter/Fassade isoliert? |
| Entfernbarkeit | Wie kann sie wieder entfernt oder ersetzt werden? |
| Alternativen | Welche bessere oder sicherere Alternative wurde geprüft? |

Eine Dependency ohne beantwortete Prüfung darf nicht eingeführt werden.

---

## 4. Bewertungsschema

Einstufung:

### Niedriges Risiko

- reine Dev-Dependency
- kein Zugriff auf Runtime-Daten
- keine Netzwerk-, Storage-, Crypto- oder Auth-Berührung
- kleine transitive Fläche
- gut wartbar
- leicht entfernbar

Trotzdem: dokumentieren, warum sie gebraucht wird.

### Mittleres Risiko

- Runtime-Dependency
- UI-nahe Nutzung
- keine sensiblen Daten
- begrenzte transitive Fläche
- kleine API

Erforderlich: gezielte Tests und Bundle-/Runtime-Prüfung.

### Hohes Risiko

- Crypto
- Auth
- Storage
- Secret Handling
- Telemetrie
- Error Reporting
- Sync
- Recovery
- Build-/Packaging-Supply-Chain
- globale Polyfills oder Side Effects

Erforderlich: ADR oder Security-Doku, Alternativenvergleich, Adapter, Tests, Runtime-Prüfung.

---

## 5. Dependency-Kapselung

Regeln:

- Fachlogik importiert riskante Libraries nicht direkt.
- Crypto-, Storage-, Auth-, Telemetry- und Error-Reporting-Libraries werden hinter einer Fassade oder einem Adapter gekapselt.
- Adapter haben kleine APIs.
- Adapter haben Tests für Erfolg, Fehler und Missbrauch.
- Migration muss möglich bleiben.
- Die restliche Codebasis soll nicht von Library-spezifischen Typen abhängig werden, wenn diese Typen nicht Teil des fachlichen Modells sind.

Schlecht:

```ts
import { encrypt } from "random-crypto-helper";

export async function saveItem(item: VaultItem) {
  return encrypt(JSON.stringify(item), item.password);
}
```

Warum schlecht: Fachlogik hängt direkt an unklarer Crypto-Library, Sicherheitsmodell unbekannt, Migration schwer.

Gut:

```ts
export async function saveItem(item: PlainVaultItem, key: VaultContentKey) {
  const sealed = await vaultCryptoService.sealItem({
    plaintext: encodeVaultItem(item),
    key,
    context: { itemId: item.id, version: item.version },
  });

  return vaultStorageService.saveSealedItem(sealed);
}
```

Warum gut: Fachlogik nutzt Projekt-Services, Crypto ist gekapselt, Kontext ist explizit.

---

## 6. Komfort-Libraries

Keine Bibliothek für triviale Logik.

Schlecht:

```ts
import leftPad from "left-pad";

export function normalizeCode(code: string) {
  return leftPad(code, 6, "0");
}
```

Warum schlecht: externe Supply-Chain für triviale Logik, unnötige Auditfläche, kein Sicherheitsnutzen.

Gut:

```ts
export function normalizeRecoveryCode(code: string): string {
  return code.trim().padStart(6, "0");
}
```

Warum gut: verständlich, testbar, keine zusätzliche Angriffsfläche.

---

## 7. Bestehende Dependencies

Eine bestehende Dependency darf nicht blind weitergetragen werden, nur weil sie schon im Projekt ist.

Wenn eine bestehende Bibliothek berührt wird, prüfen:

- Wird sie noch benötigt?
- Gibt es eine sicherere Plattform-API?
- Gibt es eine kleinere Alternative?
- Gibt es offene CVEs oder Advisories?
- Ist die Nutzung korrekt gekapselt?
- Wird sie an mehr Stellen importiert als nötig?
- Ist sie noch kompatibel mit Web und Tauri?
- Hat sich die API unsicher verändert?
- Gibt es neue transitive Abhängigkeiten?
- Muss ein Adapter angepasst werden?

---

## 8. Updates

Vor Minor-/Patch-Updates in normalen Pfaden:

- Changelog prüfen
- Tests ausführen
- Runtime öffnen, wenn UI/Build/Runtime betroffen ist

Vor Major-Updates oder Updates in Security-Pfaden:

- Changelog prüfen
- Breaking Changes prüfen
- Security Advisories prüfen
- Migrationshinweise prüfen
- betroffene Adapter prüfen
- gezielte Tests ergänzen
- `npm run test` vollständig ausführen
- Runtime-Prüfung durchführen
- Risiko und Restrisiko dokumentieren

Keine Massenupdates, wenn nur eine gezielte Änderung nötig ist.

---

## 9. Telemetrie, Analytics und Error Reporting

Besonders kritisch.

Regeln:

- Keine Telemetrie im Vault-Pfad ohne Datenschutzentscheidung.
- Keine entschlüsselten Items, Kategorien, Keys, Tokens oder Recovery-Daten an externe Dienste.
- Keine vollständigen URLs, wenn sie Tokens oder State enthalten könnten.
- Keine produktiven Stacktraces, wenn sie Secrets enthalten könnten.
- Keine Session-Replay- oder Screen-Recording-Library im Vault-Kontext.
- Error-Reporting nur mit Sanitizing-Fassade und Tests.
- Opt-out/Opt-in-Regeln müssen dokumentiert sein, falls Telemetrie existiert.

---

## 10. Build- und Tooling-Dependencies

Build-Tools können Supply-Chain- und Runtime-Risiken erzeugen.

Prüfen:

- Verändert das Tool Importpfade?
- Erzeugt es doppelte Modulidentität?
- Verändert es Tree-Shaking?
- Fügt es globale Polyfills ein?
- Läuft es in Web und Tauri?
- Leakt es Env-Variablen in Client-Bundles?
- Greift es auf Netzwerk, Dateisystem oder Secrets zu?
- Schreibt es generierte Dateien mit sensiblen Daten?

---

## 11. ADR-Vorlage für riskante Dependencies

```md
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

- Berührt Plaintext/Keys/Auth/Storage/Sync/Recovery?
- Advisories/CVEs geprüft?
- Maintainer-Aktivität geprüft?
- Transitive Dependencies geprüft?
- Web/Tauri-Kompatibilität geprüft?

## Nutzung im Projekt

- Import nur in: <Adapter/Fassade>
- Tests: <Liste>
- Runtime-Prüfung: <Liste>

## Exit-Plan

<Wie wird die Dependency ersetzt oder entfernt?>
```

---

## 12. Dependency-Review-Checkliste

- [ ] Löst die Dependency ein echtes Projektproblem?
- [ ] Reicht vorhandener Code oder Plattform-API wirklich nicht?
- [ ] Berührt sie Plaintext, Keys, Auth, Storage, Sync oder Recovery?
- [ ] Security Advisories/CVEs geprüft?
- [ ] Maintainer-Aktivität geprüft?
- [ ] Transitive Dependency-Fläche geprüft?
- [ ] Web/Tauri-Kompatibilität geprüft?
- [ ] Lizenz geprüft?
- [ ] API klein und schwer falsch zu nutzen?
- [ ] Hinter Adapter/Fassade gekapselt?
- [ ] Tests ergänzt?
- [ ] Runtime geprüft, wenn betroffen?
- [ ] Alternative dokumentiert?
- [ ] Exit-Plan vorhanden?
