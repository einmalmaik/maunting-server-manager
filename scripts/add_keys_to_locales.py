import json
import os

frontend_dir = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend"
de_path = os.path.join(frontend_dir, "src", "locales", "de.json")
en_path = os.path.join(frontend_dir, "src", "locales", "en.json")

with open(de_path, "r", encoding="utf-8") as f:
    de = json.load(f)
with open(en_path, "r", encoding="utf-8") as f:
    en = json.load(f)

# Helper to set nested dict key
def set_nested(d, path, val):
    parts = path.split(".")
    curr = d
    for p in parts[:-1]:
        if p not in curr or not isinstance(curr[p], dict):
            curr[p] = {}
        curr = curr[p]
    curr[parts[-1]] = val

new_entries = {
    # 1. mss.vault.errors
    "mss.vault.errors.biometricsNotSupported": (
        "Biometrische Authentifizierung wird auf diesem Gerät oder Browser nicht unterstützt.",
        "Biometric authentication is not supported on this device or browser."
    ),
    "mss.vault.errors.wrongMasterPassword": (
        "Falsches Master-Passwort. Bitte überprüfe deine Eingabe.",
        "Incorrect master password. Please check your input."
    ),
    "mss.vault.errors.biometricsFailed": (
        "Biometrische Authentifizierung fehlgeschlagen.",
        "Biometric authentication failed."
    ),
    "mss.vault.errors.biometricsActivationFailed": (
        "Biometrie-Aktivierung fehlgeschlagen",
        "Biometric activation failed"
    ),
    "mss.vault.errors.biometricsNotSupportedShort": (
        "Biometrie wird auf diesem Gerät oder Browser nicht unterstützt.",
        "Biometrics is not supported on this device or browser."
    ),
    "mss.vault.errors.biometricsKeyLoadFailed": (
        "Biometrischer Schlüssel konnte nicht geladen werden.",
        "Biometric key could not be loaded."
    ),
    "mss.vault.errors.biometricsUnlockFailed": (
        "Biometrisches Entsperren fehlgeschlagen.",
        "Biometric unlock failed."
    ),
    "mss.vault.errors.setupFailed": (
        "Einrichten fehlgeschlagen",
        "Setup failed"
    ),
    "mss.vault.errors.notSetup": (
        "Es wurde noch kein Tresor eingerichtet. Bitte richte zuerst ein Master-Passwort ein.",
        "No vault has been set up yet. Please set up a master password first."
    ),
    "mss.vault.errors.keysMissing": (
        "Tresor-Schlüsseldaten fehlen oder konnten nicht geladen werden.",
        "Vault key data is missing or could not be loaded."
    ),
    "mss.vault.errors.unlockFailed": (
        "Entsperren fehlgeschlagen",
        "Unlock failed"
    ),
    "mss.vault.errors.locked": (
        "Tresor ist gesperrt",
        "Vault is locked"
    ),
    "mss.vault.errors.attachmentTooLarge": (
        "Dateianhang \"{{name}}\" überschreitet das Limit von 500 KB.",
        "File attachment \"{{name}}\" exceeds the 500 KB limit."
    ),
    "mss.vault.errors.attachmentsTotalTooLarge": (
        "Die Gesamtgröße aller Dateianhänge überschreitet das Limit von 500 KB.",
        "The total size of all file attachments exceeds the 500 KB limit."
    ),
    "mss.vault.errors.emptyPassword": (
        "Master-Passwort darf nicht leer sein.",
        "Master password cannot be empty."
    ),
    "mss.vault.errors.invalidSalt": (
        "Ungültiger KDF-Salt: Mindestens 16 Bytes erforderlich.",
        "Invalid KDF salt: At least 16 bytes required."
    ),
    "mss.vault.errors.decryptionFailed": (
        "Tresor-Eintrag konnte nicht entschlüsselt werden (Authentifizierungsfehler oder falscher Schlüssel)",
        "Vault entry could not be decrypted (authentication error or incorrect key)"
    ),
    "mss.vault.errors.invalidBase32Char": (
        "Ungültiges Base32-Zeichen '{{char}}' an Position {{pos}}",
        "Invalid Base32 character '{{char}}' at position {{pos}}"
    ),
    "mss.vault.errors.invalidTotpSecret": (
        "Ungültiges Base32 TOTP-Secret",
        "Invalid Base32 TOTP secret"
    ),

    # 2. profile.messengerLock.errors
    "profile.messengerLock.errors.noAccount": (
        "Kein Konto angemeldet.",
        "No account logged in."
    ),
    "profile.messengerLock.errors.pinTooShort": (
        "Der PIN braucht mindestens {{count}} Zeichen.",
        "The PIN requires at least {{count}} characters."
    ),
    "profile.messengerLock.errors.alreadySetUp": (
        "Für dieses Konto ist bereits ein PIN eingerichtet.",
        "A PIN is already set up for this account."
    ),
    "profile.messengerLock.errors.storageUnreachable": (
        "Der Schlüsselspeicher dieses Geräts ist nicht erreichbar. Der Messenger lässt sich hier gerade nicht entsperren.",
        "This device's keystore is unreachable. The messenger cannot be unlocked right now."
    ),
    "profile.messengerLock.errors.noPinOnDevice": (
        "Auf diesem Gerät ist kein PIN hinterlegt.",
        "No PIN is stored on this device."
    ),
    "profile.messengerLock.errors.wrongPin": (
        "Falscher PIN.",
        "Incorrect PIN."
    ),
    "profile.messengerLock.errors.bioFailed": (
        "Biometrische Bestätigung fehlgeschlagen.",
        "Biometric verification failed."
    ),
    "profile.messengerLock.errors.currentPinWrong": (
        "Der bisherige PIN stimmt nicht.",
        "The current PIN is incorrect."
    ),
    "profile.messengerLock.errors.setupFirst": (
        "Erst einen PIN einrichten.",
        "Set up a PIN first."
    ),
    "profile.messengerLock.errors.pinIncorrect": (
        "Der PIN stimmt nicht.",
        "The PIN is incorrect."
    ),

    # 3. calls
    "calls.nobodyAnswered": (
        "Niemand hat abgenommen.",
        "Nobody answered."
    ),
    "calls.callMissed": (
        "Anruf verpasst.",
        "Call missed."
    ),
    "calls.moderatorMuted": (
        "Ein Moderator hat dich stummgeschaltet. Nur die Moderation kann das aufheben.",
        "A moderator muted you. Only moderation can unmute you."
    ),
    "calls.screenshareFailed": (
        "Die Bildschirmfreigabe konnte nicht gestartet werden.",
        "Screen sharing could not be started."
    ),
    "calls.callTransferred": (
        "Der Anruf wurde auf ein anderes Gerät übertragen.",
        "The call was transferred to another device."
    ),
    "calls.joinedOnOtherDevice": (
        "Du bist auf einem anderen Gerät einem anderen Anruf beigetreten.",
        "You joined another call on another device."
    ),
    "calls.callEnded": (
        "Der Anruf wurde beendet.",
        "The call ended."
    ),
    "calls.callRejected": (
        "Der Anruf wurde abgelehnt.",
        "The call was rejected."
    ),
    "calls.callerHungUp": (
        "Der Anrufer hat aufgelegt.",
        "The caller hung up."
    ),
    "calls.groupCallEnded": (
        "Der Gruppenanruf wurde beendet.",
        "The group call ended."
    ),
    "calls.participantInvited": (
        "{{username}} wurde eingeladen.",
        "{{username}} was invited."
    ),
    "calls.participantInviteFailed": (
        "{{username}} konnte nicht eingeladen werden.",
        "{{username}} could not be invited."
    ),
    "calls.participantRemoved": (
        "{{username}} wurde aus dem Anruf entfernt.",
        "{{username}} was removed from the call."
    ),
    "calls.wrongKeyLength": (
        "Raumschlüssel hat die falsche Länge",
        "Room key has incorrect length"
    ),

    # 4. profile.friends
    "profile.friends.sendError": (
        "Fehler beim Senden.",
        "Error sending request."
    ),
    "profile.friends.acceptError": (
        "Fehler beim Annehmen.",
        "Error accepting request."
    ),
    "profile.friends.rejected": (
        "Anfrage abgelehnt.",
        "Request rejected."
    ),
    "profile.friends.rejectError": (
        "Fehler beim Ablehnen.",
        "Error rejecting request."
    ),
    "profile.friends.removeError": (
        "Fehler beim Entfernen.",
        "Error removing friend."
    ),
    "profile.friends.unblocked": (
        "Blockierung von {{username}} aufgehoben",
        "Unblocked {{username}}"
    ),
    "profile.friends.unmuted": (
        "Stummschaltung aufgehoben",
        "Unmuted"
    ),

    # 5. mss.einstellungen
    "mss.einstellungen.updateAvailableVersion": (
        "Version v{{version}} ist verfügbar.",
        "Version v{{version}} is available."
    ),
    "mss.einstellungen.updateCheckError": (
        "Konnte nicht nach Updates suchen.",
        "Could not check for updates."
    ),

    # 6. chat.errors
    "chat.errors.noMailbox": (
        "Für dieses Gespräch steht noch keine Mailbox fest.",
        "No mailbox has been established for this conversation yet."
    ),
    "chat.errors.indexedDbUnavailable": (
        "IndexedDB nicht verfügbar",
        "IndexedDB unavailable"
    ),
    "chat.errors.noE2eeIdentity": (
        "Kein angemeldetes Konto: dieses Gerät hat keine E2EE-Identität",
        "No account logged in: this device has no E2EE identity"
    ),
    "chat.errors.groupDbBlocked": (
        "Gruppenschlüssel-Datenbank blockiert: bitte andere Panel-Tabs schließen",
        "Group key database blocked: please close other panel tabs"
    ),
    "chat.errors.biometricFailed": (
        "Biometrische Bestätigung fehlgeschlagen.",
        "Biometric verification failed."
    ),
    "chat.errors.chunkMissing": (
        "Stück {{index}} fehlt",
        "Chunk {{index}} is missing"
    ),

    # 7. auth.errors
    "auth.errors.refreshUnavailable": (
        "Refresh temporär nicht möglich",
        "Refresh temporarily unavailable"
    ),

    # 8. ai.actionProposal
    "ai.actionProposal.userDeclined": (
        "Der Benutzer hat die Ausführung dieser Aktion abgelehnt.",
        "The user declined the execution of this action."
    ),

    # 9. pwa
    "pwa.updateTitle": (
        "Update verfügbar",
        "Update available"
    ),
    "pwa.updateMessage": (
        "Eine neue Version ist verfügbar. Jetzt aktualisieren?",
        "A new version is available. Update now?"
    ),
    "pwa.updateConfirm": (
        "Aktualisieren",
        "Update"
    ),
    "pwa.updateCancel": (
        "Später",
        "Later"
    ),

    # 10. calendar
    "calendar.titleRequired": (
        "Bitte gib einen Termintitel an",
        "Please enter an event title"
    ),
    "calendar.created": (
        "Termin erfolgreich erstellt",
        "Event created successfully"
    ),
    "calendar.updated": (
        "Termin aktualisiert",
        "Event updated"
    ),
    "calendar.deleteConfirmTitle": (
        "Termin löschen",
        "Delete event"
    ),
    "calendar.deleteConfirmMessage": (
        "Möchtest du diesen Termin wirklich unwiderruflich aus deinem Kalender löschen?",
        "Do you really want to permanently delete this event from your calendar?"
    ),
    "calendar.deleted": (
        "Termin gelöscht",
        "Event deleted"
    ),

    # 11. profile.mailboxes & calendars
    "profile.mailboxes.emailAndNameRequired": (
        "Bitte E-Mail und Bezeichnung angeben.",
        "Please provide email and name."
    ),
    "profile.mailboxes.hostRequired": (
        "Bitte mindestens IMAP-Host (für Empfang) oder SMTP-Host (für Versand) konfigurieren.",
        "Please configure at least IMAP host (for receiving) or SMTP host (for sending)."
    ),
    "profile.calendars.nameAndUrlRequired": (
        "Bitte Bezeichnung und CalDAV-URL angeben.",
        "Please provide name and CalDAV URL."
    ),

    # 12. databaseManager & panelDatabase
    "databaseManager.databaseDeleted": (
        "Datenbank gelöscht",
        "Database deleted"
    ),
    "databaseManager.userCreated": (
        "Datenbank-User erstellt",
        "Database user created"
    ),
    "databaseManager.passwordRotated": (
        "Passwort rotiert",
        "Password rotated"
    ),
    "databaseManager.userDeleted": (
        "Datenbank-User gelöscht",
        "Database user deleted"
    ),
    "databaseManager.rowUpdated": (
        "Zeile erfolgreich aktualisiert",
        "Row updated successfully"
    ),
    "databaseManager.rowsDeleted": (
        "{{count}} Zeile(n) gelöscht",
        "{{count}} row(s) deleted"
    ),
    "databaseManager.rowInserted": (
        "Zeile erfolgreich eingefügt",
        "Row inserted successfully"
    ),
    "databaseManager.deleteTableConfirm": (
        "Tabelle {{schema}}.{{name}} wirklich löschen? Alle Daten gehen verloren.",
        "Really delete table {{schema}}.{{name}}? All data will be lost."
    ),
    "databaseManager.enterUsernamePrompt": (
        "Benutzername für den neuen Datenbank-User:",
        "Username for the new database user:"
    ),
    "panelDatabase.importExecuted": (
        "Panel-DB-Import ausgeführt",
        "Panel DB import executed"
    ),
    "panelDatabase.rowUpdated": (
        "Zeile erfolgreich aktualisiert",
        "Row updated successfully"
    ),
    "panelDatabase.rowsDeleted": (
        "{{count}} Zeile(n) gelöscht",
        "{{count}} row(s) deleted"
    ),
    "panelDatabase.rowInserted": (
        "Zeile erfolgreich eingefügt",
        "Row inserted successfully"
    ),
}

for path, (de_val, en_val) in new_entries.items():
    set_nested(de, path, de_val)
    set_nested(en, path, en_val)

with open(de_path, "w", encoding="utf-8") as f:
    json.dump(de, f, ensure_ascii=False, indent=2)
    f.write("\n")

with open(en_path, "w", encoding="utf-8") as f:
    json.dump(en, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"Added {len(new_entries)} keys to de.json and en.json successfully.")
