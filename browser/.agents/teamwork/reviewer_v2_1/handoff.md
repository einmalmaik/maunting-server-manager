# Review- und Prüfbericht: Präzisions-Updates in AUDIT_REPORT_PONTIS.md

**Prüfer:** Reviewer V2 (reviewer_critic)  
**Datum:** 17. Oktober 2026  
**Zielobjekt:** `browser/AUDIT_REPORT_PONTIS.md`  
**Prüfverzeichnis:** `browser/.agents/teamwork/reviewer_v2_1`  
**Status:** Abgeschlossen  

---

## Review Summary

**Verdict**: **APPROVE**

Die drei geforderten Präzisions-Updates in `AUDIT_REPORT_PONTIS.md` wurden lückenlos und mit 100%iger Übereinstimmung zur tatsächlichen Codebasis umgesetzt:
1. **Abschnitt 2.3.3**: Beschreibt exakt die Positivliste von drei Fächern in `pruefe_fach()` sowie die bewusste architektonische Auslassung und Kapselung von `session_refresh_token` (`FACH_SITZUNG`) als privates Backend-Geheimnis.
2. **Abschnitt 3.1**: Alle 14 Zeilen der Vergleichstabelle verwenden konsistente und gültige Pfadpräfixe (`src-tauri/...` und `frontend/...`).
3. **Abschnitt 6**: Sämtliche Reproduktionsbefehle für Backend, Frontend und `schluesselfach` wurden unabhängig live ausgeführt und sind zu 100 % fehlerfrei lauffähig.

---

## 1. Observation (Direkte Beobachtungen)

### 1.1 Beobachtung zu Abschnitt 2.3.3 (Schlüsselfach Positivliste & Refresh-Token-Isolation)
- In `crates/schluesselfach/src/biometrie.rs`:
  - Zeile 42: `pub const FACH_TRESOR: &str = "vault_biometric_key";`
  - Zeile 45: `pub const FACH_MESSENGER: &str = "messenger_biometric_key";`
  - Zeile 57: `pub const FACH_MESSENGER_GERAET: &str = "messenger_device_secret";`
  - Zeilen 68–73:
    ```rust
    fn pruefe_fach(fach: &str) -> Result<(), String> {
        match fach {
            FACH_TRESOR | FACH_MESSENGER | FACH_MESSENGER_GERAET => Ok(()),
            _ => Err(format!("Unbekanntes Schlüsselfach: {fach}")),
        }
    }
    ```
- In `crates/schluesselfach/src/geheimnisse.rs`:
  - Zeile 23: `const KONTO: &str = "refresh_token";` (Windows Credential Manager Account)
  - Zeilen 25–28:
    ```rust
    /// Das Fach des Tokens unter Android. Nur hier benutzt, nie von der Oberfläche
    /// erreichbar (`biometrie::pruefe_fach` kennt es nicht).
    #[cfg(target_os = "android")]
    const FACH_SITZUNG: &str = "session_refresh_token";
    ```
- In `crates/schluesselfach/src/befehle.rs`:
  - Zeilen 14–26: `refresh_token_speichern`, `refresh_token_laden`, `refresh_token_loeschen` akzeptieren keinen Fach-Parameter und delegieren direkt an `geheimnisse::*`.
  - Zeilen 53–60: `biometrie_speichern`, `biometrie_entsperren` und `biometrie_loeschen` akzeptieren `fach: Option<String>` und validieren über `pruefe_fach()`.
- Im Bericht `AUDIT_REPORT_PONTIS.md`:
  - Zeilen 190–203 dokumentieren exakt diese Dreiteilung (`vault_biometric_key`, `messenger_biometric_key`, `messenger_device_secret`) und die gezielte Nicht-Aufnahme von `session_refresh_token` (`FACH_SITZUNG`).

### 1.2 Beobachtung zu Abschnitt 3.1 (Konsistenz der Pfadzitate)
- Tabelle in `AUDIT_REPORT_PONTIS.md`, Zeilen 254–270 („Fundstelle im Pontis-Code“):
  1. `src-tauri/src/lib.rs`, `src-tauri/Cargo.toml`
  2. `src-tauri/Cargo.toml:28–34`, `src-tauri/src/schild/mod.rs:110–128`
  3. `src-tauri/src/schild/listen.rs:26–57`
  4. `src-tauri/src/tabs/desktop/webview2.rs:176–221`
  5. `src-tauri/src/tabs/desktop/webview2.rs:381–419`
  6. `src-tauri/src/tabs/ruhe.rs:99–104`, `src-tauri/src/tabs/desktop/schlaf.rs:128–140`
  7. `frontend/src/browser/services/workspacesStore.ts:131–144`
  8. `src-tauri/src/tabs/desktop.rs:67`, `src-tauri/src/tabs/desktop/ohne_edge.rs:24–36`
  9. `frontend/src/locales/de.json:4924`
  10. `src-tauri/src/schild/verfolgung.rs:10–47`, `frontend/src/browser/services/linkReiniger.ts:86–152`
  11. `frontend/src/browser/kopf/SeitenSicherheitPopover.tsx:53–65`, `src-tauri/src/tabs/desktop.rs:432–478`
  12. `frontend/src/browser/kopf/VertikaleTableiste.tsx:64–105`
  13. `frontend/src/browser/seite/SnapZonenOverlay.tsx:31–53`, `src-tauri/src/tabs/desktop.rs:360–400`
  14. `frontend/src/browser/seite/LesemodusAnsicht.tsx:148–175`, `frontend/src/browser/services/lesemodusService.ts:64–78`
- Alle 14 Einträge verwenden ausschließlich `src-tauri/...` und `frontend/...`. Keine fehlenden Präfixe, keine relativen Brüche.

### 1.3 Beobachtung zu Abschnitt 6 (Reproduktionsbefehle & Testergebnisse)
1. **Rust Backend Tests (`browser/src-tauri`):**
   - Befehl: `cargo test --lib -- tests::rechte_decken_jeden_befehl tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval tests::das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung tabs::weg::tests schild::tests::filtert_nach_listen_und_ausnahmen schild::verfolgung::tests::nimmt_kampagnen_und_klickkennungen_heraus tabs::ruhe::tests`
   - Tatsächliches Ergebnis: `test result: ok. 22 passed; 0 failed; 0 ignored; 0 measured; 149 filtered out; finished in 0.01s` (Exit-Code 0).
2. **Frontend Tests (`frontend`):**
   - Befehl: `npm test -- src/browser/kopf/VertikaleTableiste.test.tsx src/browser/seite/LesemodusAnsicht.test.tsx src/browser/kopf/SeitenSicherheitPopover.test.tsx src/browser/services/tabsStore.test.ts`
   - Tatsächliches Ergebnis: `Test Files 4 passed (4)`, `Tests 52 passed (52)`, `Duration 16.05s` (Exit-Code 0).
3. **Schlüsselfach Unsafe-Prüfung (`crates/schluesselfach`):**
   - Befehl: `git grep "unsafe" -- src/`
   - Tatsächliches Ergebnis: Exit-Code 1, 0 Fundstellen.
4. **Schlüsselfach Komponententests (`crates/schluesselfach`):**
   - Befehl: `cargo test`
   - Tatsächliches Ergebnis: `test result: ok. 4 passed; 0 failed` (Exit-Code 0).
5. **Code-Inspektionen in Abschnitt 6.3:**
   - `browser/src-tauri/src/tabs/desktop.rs`, Zeile 67 zitiert exakt `const BROWSER_ARGUMENTE: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection";`.
   - `browser/src-tauri/src/tabs/desktop/schlaf.rs`, Zeilen 133–137 zitiert exakt `NATIV.with(|n| n.borrow_mut().remove(id))` und `super::herunterladen::schliessen(id, tab.controller);`.

---

## 2. Logic Chain (Schlussfolgerungskette)

1. **Bezug zu Abschnitt 2.3.3:**
   - Aus Beobachtung 1.1 geht hervor, dass `pruefe_fach()` in `biometrie.rs` exakt 3 Konstanten (`FACH_TRESOR`, `FACH_MESSENGER`, `FACH_MESSENGER_GERAET`) akzeptiert und jeden anderen Bezeichner mit einem Fehler abweist.
   - `session_refresh_token` (`FACH_SITZUNG`) ist ausschließlich in `geheimnisse.rs` deklariert und besitzt eine gesonderte IPC-Verwaltung (`befehle.rs`), die nicht über `biometrie_speichern` / `biometrie_entsperren` ansprechbar ist.
   - Der Bericht in Abschnitt 2.3.3 bildet diesen Mechanismus und seine Sicherheitsimplikationen buchstabengetreu und architektonisch fundiert ab.

2. **Bezug zu Abschnitt 3.1:**
   - Aus Beobachtung 1.2 folgt, dass sämtliche Fundstellen in der Tabelle konsistent mit `src-tauri/...` bzw. `frontend/...` beginnen.
   - Alle zitierten Dateien und Zeilenbereiche wurden stichprobenartig und vollständig gegen das Dateisystem abgeglichen; die Inhalte decken die behaupteten technischen Features (z. B. Adblock-Integration, Deep-Sleep, Clean Copy, Snap-Assist, Lesemodus).

3. **Bezug zu Abschnitt 6:**
   - Aus Beobachtung 1.3 folgt, dass die im Bericht angegebenen Kommandos, relativen Pfade (`cd browser/src-tauri`, `cd frontend`, `cd crates/schluesselfach`), Test-Filter und Soll-Ergebnisse (22 passed für Backend, 52 passed für Frontend, 4 passed für Schluesselfach, 0 Treffer für unsafe) empirisch reproduziert wurden.
   - Es gibt keine manipulierten, statisch gefälschten oder unerreichbaren Testläufe.

4. **Integritätsprüfung:**
   - Es wurden keine Fassaden-Implementierungen, keine Mock-Abkürzungen zur Umgehung von Prüfungen und keine Hardcodierungen von Testergebnissen im Quellcode festgestellt.

---

## 3. Caveats (Einschränkungen & Annahmen)

- **Plattformkontext:** Die Tests wurden in der Windows-Arbeitsumgebung des Projekts ausgeführt. Da die Desktop-Webview-Controller von Pontis über `#[cfg(windows)]` an Win32/COM gebunden sind, sind diese spezifischen Integrationstests unter Linux/macOS nicht ausführbar, was in Abschnitt 4.2.1 des Berichts aber auch ausdrücklich als Plattformgrenze benannt wird.
- Keine weiteren Einschränkungen.

---

## 4. Conclusion (Abschließendes Urteil)

**VERDICT: APPROVE**

Der Bericht `AUDIT_REPORT_PONTIS.md` genügt höchsten Ansprüchen an technische Präzision, Belegbarkeit und Adversarial Integrity:
- Abschnitt 2.3.3 reflektiert die tatsächliche Sicherheitsgrenze von `pruefe_fach()` und `session_refresh_token` exakt.
- Abschnitt 3.1 wahrt eine durchgängig einheitliche und syntaktisch saubere Pfadnotation.
- Abschnitt 6 liefert verifizierte, unmittelbar ausführbare und empirisch bestätigte Reproduktionsanweisungen.

---

## 5. Verification Method (Unabhängige Verifikationsmethode)

Zur eigenständigen Nachprüfung durch den Orchestrator oder Dritte können folgende Befehle in der PowerShell aufgerufen werden:

```powershell
# 1. Backend-Kern-Tests verifizieren (aus browser/src-tauri):
cd C:\Users\einma\.gemini\antigravity\worktrees\maunting-server-manager\browser_competitiveness_security_audit\browser\src-tauri
cargo test --lib -- tests::rechte_decken_jeden_befehl tests::die_oberflaeche_laedt_keine_fremden_bilder_und_kein_eval tests::das_token_liegt_nie_im_klartext_und_nicht_in_der_sicherung tabs::weg::tests schild::tests::filtert_nach_listen_und_ausnahmen schild::verfolgung::tests::nimmt_kampagnen_und_klickkennungen_heraus tabs::ruhe::tests
# Erwartung: "test result: ok. 22 passed; 0 failed"

# 2. Frontend-Kern-Tests verifizieren (aus frontend):
cd ../../frontend
npm test -- src/browser/kopf/VertikaleTableiste.test.tsx src/browser/seite/LesemodusAnsicht.test.tsx src/browser/kopf/SeitenSicherheitPopover.test.tsx src/browser/services/tabsStore.test.ts
# Erwartung: "4 passed (4)", "52 passed (52)"

# 3. Schluesselfach Unsafe- und Sicherheitsprüfungen (aus crates/schluesselfach):
cd ../crates/schluesselfach
git grep "unsafe" -- src/
# Erwartung: Exit-Code 1 (keine Treffer)
cargo test
# Erwartung: "test result: ok. 4 passed; 0 failed"
```
