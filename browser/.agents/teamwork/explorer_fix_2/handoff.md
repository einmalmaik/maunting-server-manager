# Handoff Report: Explorer Fix 2 — Schluesselfach Compartment Separation & Session Token Isolation

**Agent:** Explorer Fix 2 (`teamwork_preview_explorer`)  
**Target File:** `browser/AUDIT_REPORT_PONTIS.md` (Section 2.3.3)  
**Task:** Formulate exact line-by-line replacement recommendations for the Worker to accurately reflect the 3-compartment whitelist in `pruefe_fach()` and the stronger architectural isolation of `session_refresh_token`.

---

## 1. Observation

### 1.1 Quellcode-Befund in `crates/schluesselfach/src/biometrie.rs`
In `crates/schluesselfach/src/biometrie.rs` (Zeilen 36–73) sind exakt **drei** Compartments deklariert und in `pruefe_fach()` zugelassen:
```rust
// Zeilen 42-57:
pub const FACH_TRESOR: &str = "vault_biometric_key";
pub const FACH_MESSENGER: &str = "messenger_biometric_key";
pub const FACH_MESSENGER_GERAET: &str = "messenger_device_secret";

// Zeilen 59-73:
/// Fächernamen, die von außen kommen dürfen.
///
/// Der Name geht in eine Ressource des Betriebssystems — in den Credential Store
/// oder in einen Keystore-Alias. Eine freie Zeichenkette aus dem Frontend hätte
/// hier nichts verloren: sie wäre ein Weg, in fremde Einträge derselben
/// Anwendung zu schreiben. Drei feste Namen, alles andere wird abgewiesen.
///
/// Die Android-Seite prüft dasselbe noch einmal in `Schluesselfach.pruefeFach`.
/// Doppelt, weil beide Seiten für sich stehen müssen.
fn pruefe_fach(fach: &str) -> Result<(), String> {
    match fach {
        FACH_TRESOR | FACH_MESSENGER | FACH_MESSENGER_GERAET => Ok(()),
        _ => Err(format!("Unbekanntes Schlüsselfach: {fach}")),
    }
}
```

`pruefe_fach()` sichert in `biometrie.rs` alle nach außen gerichteten IPC-Funktionen ab:
- `speichere_biometrie_geheimnis` (Zeile 237)
- `loesche_biometrie_geheimnis` (Zeile 259)
- `entsperre_mit_biometrie` (Zeile 311)
Zusätzlich verhindert Zeile 312–314, dass `FACH_MESSENGER_GERAET` allein entsperren kann:
```rust
if fach == FACH_MESSENGER_GERAET {
    return Err("Das Gerätegeheimnis entsperrt nichts allein.".to_string());
}
```

### 1.2 Quellcode-Befund in `crates/schluesselfach/src/geheimnisse.rs`
In `crates/schluesselfach/src/geheimnisse.rs` (Zeilen 22–28) wird das Refresh-Token getrennt definiert:
```rust
// Zeilen 22-23 (Windows):
#[cfg(not(target_os = "android"))]
const KONTO: &str = "refresh_token";

// Zeilen 25-28 (Android):
/// Das Fach des Tokens unter Android. Nur hier benutzt, nie von der Oberfläche
/// erreichbar (`biometrie::pruefe_fach` kennt es nicht).
#[cfg(target_os = "android")]
const FACH_SITZUNG: &str = "session_refresh_token";
```
Der Quelltextkommentar in Zeile 25–26 hebt explizit hervor:
*„Nur hier benutzt, nie von der Oberfläche erreichbar (`biometrie::pruefe_fach` kennt es nicht).“*

### 1.3 Quellcode-Befund in `crates/schluesselfach/src/befehle.rs`
In `crates/schluesselfach/src/befehle.rs` sind die Befehlspfade strikt getrennt:
1. **Refresh-Token-Befehle** (Zeilen 13–26):
   - `refresh_token_speichern(app: AppHandle, token: String)`
   - `refresh_token_laden(app: AppHandle)`
   - `refresh_token_loeschen(app: AppHandle)`
   Diese Befehle akzeptieren **keinen** variablen Fachnamen. Sie routen ausschließlich und fest verdrahtet über `crate::geheimnisse`.
2. **Biometrie-Befehle** (Zeilen 52–76):
   - `biometrie_speichern(app: AppHandle, geheimnis: String, fach: Option<String>)`
   - `biometrie_entsperren(app: AppHandle, nachricht: Option<String>, fach: Option<String>)`
   - `biometrie_loeschen(app: AppHandle, fach: Option<String>)`
   Übergeben wird hier `fach: Option<String>` (Default: `FACH_TRESOR`), das umgehend durch `biometrie::pruefe_fach()` validiert wird. Übergibt ein Frontend-Aufrufer `"session_refresh_token"`, schlägt der Aufruf sofort fehl (`"Unbekanntes Schlüsselfach: session_refresh_token"`).

### 1.4 Quellcode-Befund in Android `Schluesselfach.kt`
In `browser/src-tauri/gen/android/app/src/main/java/com/mauntingstudios/secure_browser/Schluesselfach.kt` (Zeilen 51–61):
```kotlin
/** Fächer, die von aussen kommen dürfen. Alles andere wird abgewiesen. */
private val ERLAUBTE_FAECHER = setOf(
    "vault_biometric_key",
    "messenger_biometric_key",
    "messenger_device_secret",
    "session_refresh_token",
)

/** Diese Fächer werden ohne Abfrage gelesen. Siehe Klassenkommentar. */
private val FAECHER_OHNE_ABFRAGE = setOf("messenger_device_secret", "session_refresh_token")
```
Auf Android-Ebene ist `session_refresh_token` in `ERLAUBTE_FAECHER` eingetragen, weil das Rust-Backend via `android_ruf` (`geheimnisse.rs`, Zeilen 119 & 128) direkt mit dem Kotlin-Plugin kommuniziert. Für das Frontend bleibt der Zugriff jedoch versperrt, da jeder IPC-Aufruf zwingend den Rust-Host passiert und dort in `pruefe_fach` gestoppt wird.

### 1.5 Ist-Zustand in `browser/AUDIT_REPORT_PONTIS.md` (Zeilen 190–196)
```markdown
#### 2.3.3 Strikte Trennung der Fächer (Compartments)
In `crates/schluesselfach/src/biometrie.rs` (Zeilen 36–73) erzwingt `pruefe_fach()` eine feste Positivliste zulässiger Fächer:
1. `vault_biometric_key`: Master-Passwort des Passwort-Tresors.
2. `messenger_biometric_key`: Zugangs-PIN für den internen Messenger.
3. `messenger_device_secret`: 32 kryptografisch zufällige Bytes, die in die lokale Schlüsselableitung eingehen. Selbst wenn eine kompromittierte SQLCipher-Datenbank kopiert wird, kann sie ohne diesen im OS-Keyring geschützten Schlüssel nicht offline per Brute-Force angegriffen werden.
4. `session_refresh_token`: Refresh-Token zur Kopplung mit dem Maunting Server Manager.
```

---

## 2. Logic Chain

1. **Beobachtung 1.1:** `pruefe_fach()` in `biometrie.rs` enthält genau drei Konstanten (`FACH_TRESOR`, `FACH_MESSENGER`, `FACH_MESSENGER_GERAET`). Der Code-Kommentar formuliert unmissverständlich: *„Drei feste Namen, alles andere wird abgewiesen.“*
2. **Beobachtung 1.2:** `session_refresh_token` existiert nicht in `biometrie.rs`, sondern als `const FACH_SITZUNG` in `geheimnisse.rs` (bzw. als `KONTO = "refresh_token"` auf Windows). Der Entwickler-Kommentar belegt die Absicht: *„nie von der Oberfläche erreichbar (`biometrie::pruefe_fach` kennt es nicht)“*.
3. **Beobachtung 1.3:** Die Tauri-IPC-Befehle spiegeln diese Trennung architektonisch wider: Generische Befehle mit anpassbarem Fach (`biometrie_*`) leiten direkt in `pruefe_fach()`. Token-Befehle (`refresh_token_*`) sind isolierte Einzweck-Handler ohne wählbaren Fach-Parameter.
4. **Schlussfolgerung:** Die bisherige Darstellung in Section 2.3.3 von `AUDIT_REPORT_PONTIS.md` war doppelt ungenau:
   - Sie behauptete fälschlich, `pruefe_fach()` enthalte 4 Fächer inklusive `session_refresh_token`.
   - Sie verkannte die noch stärkere Sicherheitsarchitektur von Pontis: Indem `session_refresh_token` **nicht** in `pruefe_fach()` steht, kann ein potenziell kompromittiertes Webview-Frontend über manipulierte `fach`-Parameter niemals auf Session-Tokens zugreifen oder diese überschreiben.

---

## 3. Caveats

- **Android-Pluginschnittstelle:** Das Kotlin-Plugin `Schluesselfach.kt` kennt `session_refresh_token` in `ERLAUBTE_FAECHER`. Dies ist kein Widerspruch, sondern die notwendige Brücke für den internen Aufruf aus Rusts `geheimnisse.rs` via `android_ruf`. Das Frontend hat keinen direkten Zugriff auf das Kotlin-Plugin; alle Aufrufe laufen über den Rust-Tauri-Host.
- **Keine Code-Änderung erforderlich:** Die Implementierung in Rust und Kotlin ist bereits vollständig korrekt und defensiv aufgebaut. Die Korrektur betrifft ausschließlich die Dokumentation in `AUDIT_REPORT_PONTIS.md`.
- **ASCII-Diagramm in 2.3:** Das Diagramm in Zeilen 147–177 bleibt unverändert gültig, da `pruefe_fach` weiterhin das zentrale Gatekeeper-Element für alle Biometrie-IPC-Aufrufe der Oberfläche darstellt.

---

## 4. Conclusion & Handlungsanweisung für den Worker

### 4.1 Arbeitsauftrag an den Worker
Ersetze in `browser/AUDIT_REPORT_PONTIS.md` den Block in Zeilen 190–196 durch die nachfolgend spezifizierte Fassung.

### 4.2 Exakte Zeilen-Referenz in `browser/AUDIT_REPORT_PONTIS.md`
- **StartLine:** 190
- **EndLine:** 196

### 4.3 TargetContent (Aktueller Text):
```markdown
#### 2.3.3 Strikte Trennung der Fächer (Compartments)
In `crates/schluesselfach/src/biometrie.rs` (Zeilen 36–73) erzwingt `pruefe_fach()` eine feste Positivliste zulässiger Fächer:
1. `vault_biometric_key`: Master-Passwort des Passwort-Tresors.
2. `messenger_biometric_key`: Zugangs-PIN für den internen Messenger.
3. `messenger_device_secret`: 32 kryptografisch zufällige Bytes, die in die lokale Schlüsselableitung eingehen. Selbst wenn eine kompromittierte SQLCipher-Datenbank kopiert wird, kann sie ohne diesen im OS-Keyring geschützten Schlüssel nicht offline per Brute-Force angegriffen werden.
4. `session_refresh_token`: Refresh-Token zur Kopplung mit dem Maunting Server Manager.
```

### 4.4 ReplacementContent (Vorgeschlagener Ersatz):
```markdown
#### 2.3.3 Strikte Trennung der Fächer (Compartments) & Isolation des Session-Tokens
In `crates/schluesselfach/src/biometrie.rs` (Zeilen 36–73) erzwingt die Validierungsfunktion `pruefe_fach()` eine strikte Positivliste, die Frontend-Aufrufe über die generischen Biometrie-IPC-Befehle (`biometrie_speichern`, `biometrie_entsperren`, `biometrie_loeschen`) auf exakt **drei** Fächer beschränkt:
1. `vault_biometric_key` (`FACH_TRESOR`): Master-Passwort des Passwort-Tresors.
2. `messenger_biometric_key` (`FACH_MESSENGER`): Zugangs-PIN für den internen Messenger.
3. `messenger_device_secret` (`FACH_MESSENGER_GERAET`): 32 kryptografisch zufällige Bytes, die in die lokale Schlüsselableitung eingehen. Selbst wenn eine kompromittierte SQLCipher-Datenbank kopiert wird, kann sie ohne diesen im OS-Keyring geschützten Schlüssel nicht offline per Brute-Force angegriffen werden. (Wird über `lies_geraetegeheimnis` ohne UI-Abfrage gelesen, darf jedoch niemals allein entsperren).

Jeder vom Frontend übermittelte abweichende Fachname wird von `pruefe_fach()` mit `Unbekanntes Schlüsselfach: <fach>` zurückgewiesen.

**Architektonische Isolation von `session_refresh_token`:**
Das Refresh-Token zur Kopplung mit dem Maunting Server Manager (`FACH_SITZUNG = "session_refresh_token"` unter Android, `KONTO = "refresh_token"` unter Windows) ist in der Positivliste von `pruefe_fach()` bewusst **nicht** enthalten (`crates/schluesselfach/src/geheimnisse.rs`, Zeilen 25–28: *„nie von der Oberfläche erreichbar (`biometrie::pruefe_fach` kennt es nicht)“*).
Dies begründet eine noch stärkere Sicherheitsgrenze:
- Das Frontend kann das Refresh-Token niemals über die generischen Biometrie-IPC-Funktionen adressieren, auslesen oder manipulieren.
- Stattdessen wird das Token über separate, fest verdrahtete Host-Befehle verwaltet (`refresh_token_speichern`, `refresh_token_laden`, `refresh_token_loeschen` in `crates/schluesselfach/src/befehle.rs`), die keinen variablen Fach-Parameter akzeptieren und intern direkt an `geheimnisse.rs` delegieren.
```

---

## 5. Verification Method

Zur eigenständigen Verifikation der Ergebnisse durch den Worker und Reviewer:

1. **Inspektion von `pruefe_fach()`:**
   - Datei: `crates/schluesselfach/src/biometrie.rs`
   - Zeilen: 36–73
   - Überprüfen, dass nur `FACH_TRESOR`, `FACH_MESSENGER` und `FACH_MESSENGER_GERAET` im Match-Block vorhanden sind.

2. **Inspektion der Ausschluss-Dokumentation für `session_refresh_token`:**
   - Datei: `crates/schluesselfach/src/geheimnisse.rs`
   - Zeilen: 25–28
   - Überprüfen des Kommentars: `nie von der Oberfläche erreichbar (biometrie::pruefe_fach kennt es nicht)`.

3. **Inspektion der getrennten Tauri-Befehle:**
   - Datei: `crates/schluesselfach/src/befehle.rs`
   - Zeilen 13–26 (`refresh_token_*`) und Zeilen 52–76 (`biometrie_*`).

4. **Ausführung der bestehenden Token-Sicherheitstests:**
   Im Arbeitsverzeichnis `browser/src-tauri`:
   ```powershell
   cargo test --lib -- tests::das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung
   ```
   *Erwartetes Ergebnis:* `test result: ok. 1 passed; 0 failed`.
