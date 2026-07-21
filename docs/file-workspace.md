# Datei-Arbeitsbereich

Der Dateimanager arbeitet ausschließlich relativ zum Installationsverzeichnis des ausgewählten Game-Servers. Browser, Panel und Node-Agent erhalten keinen allgemeinen Host-Dateisystemzugriff. RBAC, CSRF-Prüfung, Pfadvalidierung und Symlink-Grenzen im Backend beziehungsweise Node-Agent bleiben für jede Operation autoritativ.

## Konfliktschutz

Beim Lesen erhält der Editor eine opake Inhaltsrevision. Jeder weitere Speichervorgang sendet diese als `expected_revision`. Wenn die Datei zwischenzeitlich durch den Game-Server, einen anderen Nutzer oder einen weiteren Browser-Tab geändert wurde, antwortet der Schreibpfad mit `409 Conflict`; der lokale Editorpuffer bleibt erhalten. Panel und Node-Agent schreiben über eine temporäre Datei und `os.replace`.

Das Anlegen einer neuen Datei setzt zusätzlich `create_only`. Existiert der Zielname bereits, lehnen Panel und Node-Agent den Vorgang atomar mit `409 Conflict` ab; die vorhandene Datei wird nicht als leere Datei überschrieben.

## Verschlüsselter Versionsverlauf

Vor dem Überschreiben einer bestehenden, editierbaren Textdatei sichert das Panel den bisherigen Inhalt. Die Snapshots liegen unter `<panel_config_dir>/.msm-file-history` und damit außerhalb aller sichtbaren Game-Server-Verzeichnisse, Downloads, Archive und Dateizählungen.

- Höchstens 3 Versionen pro relativem Dateipfad; beim vierten Stand wird die älteste Version automatisch entfernt.
- Versionsfähig sind Inhalte bis einschließlich 512 KiB; größere Editor-Dateien werden nicht versioniert. Eine Wiederherstellung wird abgelehnt, wenn der aktuelle Inhalt nicht vorher reversibel gesichert werden kann.
- Unveränderte aufeinanderfolgende Inhalte werden nicht doppelt gespeichert.
- Inhalte werden vor der Verschlüsselung mit `gzip` komprimiert.
- Verschlüsselung und Entschlüsselung laufen ausschließlich über die bestehende DIS-Fassade. Die AAD bindet Ciphertext an Server, relativen Pfad und Versions-ID.
- Ist DIS nicht verfügbar, schlägt der betroffene Schreib- oder Restore-Vorgang geschlossen fehl. Es gibt keinen Klartext-Fallback.
- Eine Wiederherstellung sichert zuerst den aktuellen Inhalt und schreibt danach über denselben revisionsgeschützten lokalen beziehungsweise Node-Agent-Pfad.

Versionsmetadaten enthalten Zeit, Größe, Revision und die interne ID des auslösenden Nutzers. Dateiinhalt, Host-Pfad und Zugangsdaten werden nicht in Logs, URLs, Toasts oder Browser-Storage persistiert.
