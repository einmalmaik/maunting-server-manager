# MSM v1.8.0 — Support-Widget & Hilfe im Panel

Du kannst Besuchern und dir selbst jetzt **Support-Chat direkt im Panel** anbieten — ohne den Code zu verbiegen. Der Fokus liegt auf **Singra** (Maunting Discord-Bot), aber Crisp, tawk.to und andere Anbieter sind ebenfalls vorgesehen.

## Highlights

### Neuer Einstellungs-Tab „Support-Widget“
- Unter **Einstellungen → Support-Widget** schaltest du das Chat-Widget ein und wählst den Anbieter.
- Das Widget erscheint im Panel, sobald alles hinterlegt und aktiviert ist.
- Speichern lädt das Widget neu — kein kompletter Browser-Neustart nötig.

### Singra (nativ)
- **Installations-ID** aus dem Singra-Widget-Panel einmal ins MSM-Panel kopieren — fertig.
- **Webhook-URL** mit einem Klick kopieren und in Singra eintragen.
- **Webhook-Secret** in Singra rotieren und im MSM-Panel speichern (oder per Server-Umgebung setzen).
- So kann Singra Ereignisse sicher an dein Panel melden — ohne freiliegende Secrets in der URL.

### Weitere Anbieter
- **Crisp** und **tawk.to** mit den üblichen IDs aus dem jeweiligen Dashboard.
- **Sonstiger Anbieter**: nur wenn nötig ein HTML-Snippet — nicht der Standardweg.

### Sprache DE / EN
- Der Sprachumschalter und die Panel-Standardsprache greifen sauberer in den Einstellungen zusammen.
- Deutsch und Englisch bleiben die beiden festen Panel-Sprachen (kein unnötiges Sprachen-Menü).

### MSM Backup Recovery (Desktop)
Die **MSM Backup Recovery** App bleibt **v0.2.0** — unverändert, aber wie gewohnt an diesem **Latest**-Release angehängt (Windows-Installer, Linux AppImage/deb/rpm), damit du sie direkt vom aktuellen Release laden kannst.

## Upgrade

```bash
sudo bash update.sh --force
```

Danach Panel neu laden (Hard-Reload, z. B. Strg+F5), falls der neue Einstellungs-Tab noch nicht sichtbar ist. `msm-panel` muss den neuen Backend-Code geladen haben (Update-Skript bzw. Neustart des Dienstes).

**Support-Widget einrichten (Singra):**  
Einstellungen → Support-Widget → aktivieren → Anbieter Singra → Installations-ID speichern → Webhook-URL in Singra → Secret aus Singra im MSM speichern.

## Verifikation (kurz)

- Einstellungen zeigen den Tab **Support-Widget**.
- Bei aktiviertem Widget und gültiger Konfiguration erscheint der Chat-Button im Panel.
- Panel-Update-Anzeige zeigt nach Update **v1.8.0** (ggf. einmal Hard-Reload / `git fetch --tags`).
- GitHub **Latest Release** ist **v1.8.0** und listet die Recovery-Downloads.

— Maunting Studios
