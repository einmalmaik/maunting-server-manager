# Phase 3: Server-Erstellung & Node-UI im Frontend

Dieses Dokument spezifiziert die Frontend-Erweiterungen. Es regelt die Visualisierung und Verwaltung von Nodes sowie die Auswahl des Ziel-Nodes bei der Server-Erstellung.

---

## 1. Design-DNA & UI-Komponenten (Verbindliche Regeln)

> [!IMPORTANT]
> **Einhaltung der Design-DNA**:
> Jegliche Frontend-Arbeit muss den Design-Richtlinien aus `C:\Users\einma\AppData\Local\Singra\workspace\maunting-design-dna` entsprechen.
> Es sind ausschließlich die wiederverwendbaren UI-Komponenten aus `frontend/src/Singra/UI/` zu verwenden. Keine redundante Neuerstellung von Buttons, Cards, Modals oder Progressbars!

---

## 2. Neue UI-Bereiche und Seiten

### 2.1 Node-Verwaltung (`/admin/nodes` -> `AdminNodesPage`)
Eine neue Einstellungsseite für Admins/Owner:
- **Übersichts-Tabelle/Cards**: Listet alle registrierten Nodes auf.
  - Name, IP/Host-Adresse.
  - Status (Online-Badge in Grün, Offline-Badge in Rot, Unknown-Badge in Grau).
  - Ressourcen-Auslastung: Visualisierung von CPU und RAM über Fortschrittsbalken (`Singra/UI/ProgressBar`).
  - Server-Anzahl (wie viele Game-Server laufen auf diesem Node).
- **Aktionen**:
  - *Node hinzufügen* (Button + Modal-Dialog): Felder für `Name`, `Host-URL` und den verschlüsselten `Agent-Token`.
  - *Node bearbeiten* (Modal): Erlaubt das Ändern des Namens und der Host-URL.
  - *Node löschen*: Nur aktiv, wenn Server-Anzahl auf dem Node = 0 ist (KISS & Sicherheit).
  - *Health-Check*: Manueller Trigger, um den Node-Status sofort zu prüfen.

### 2.2 Server-Erstellung erweitern
Im Dialog "Server erstellen" (`frontend/src/pages/CreateServer`):
- Abfrage der verfügbaren Nodes aus der API.
- **Node-Auswahl (Dropdown)**:
  - Wird **nur** angezeigt, wenn in der Datenbank mehr als 1 Node vorhanden ist. Bei Single-Node-Installationen bleibt das Feld unsichtbar und wählt automatisch den Default-Node (ID 1) aus (KISS/UX-Minimalismus).
  - Zeigt im Dropdown: `Node Name (CPU-Auslastung%, Freier RAM MB)`.

### 2.3 Server-Übersicht & Detailseite
- **Server-Liste**: Spalte "Node" hinzufügen (bzw. ein dezentes Badge), um auf einen Blick zu sehen, auf welchem Node der jeweilige Server läuft.
- **Server-Details**: Unter "System-Informationen" den Node-Namen anzeigen.

---

## 3. State Management

- Das Frontend nutzt **Zustand** für globale Stores.
- Implementiere einen `useNodeStore` in `frontend/src/stores/nodeStore.js` (oder analog zu bestehenden Stores) zur Verwaltung des Node-States (laden, hinzufügen, löschen).
- API-Aufrufe erfolgen über den bestehenden Axios-Client.

---

## 4. Test- und Verifizierungsschritte

1. **Komponenten-Check**:
   - Die neu erstellten Seiten und Modals müssen responsive sein und sich nahtlos in das MSM-Layout einfügen.
   - Prüfe die Browser-Konsole auf Hook- und Importpfadfehler (gemäß den Projekt-Rules).
2. **Funktions-Check**:
   - Füge einen simulierten Node hinzu.
   - Erstelle einen Server auf dem neuen Node und verifiziere, dass die API den korrekten `node_id` sendet.
