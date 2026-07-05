/**
 * German locale for the MSM Backup Recovery app.
 *
 * All user-visible strings are keyed here. Keys MUST match the English locale
 * (`en.ts`) exactly so the i18n parity test (VAL-UI-010) passes.
 *
 * German text uses proper umlauts (ä, ö, ü, ß) as literal UTF-8 characters.
 */

export const de = {
  'app.title': 'MSM Backup Recovery',
  'app.subtitle': 'Offline-Entschlüsselung für MSM Backups',

  'step.input.heading': 'Backup entschlüsseln',
  'step.input.description':
    'Wählen Sie eine .enc-Backup-Datei aus und geben Sie das Backup-Passwort sowie den Salt ein.',

  'filepicker.label': 'Backup-Datei (.enc)',
  'filepicker.button': 'Datei auswählen',
  'filepicker.placeholder': 'Keine Datei ausgewählt',
  'filepicker.selected': 'Ausgewählte Datei',
  'filepicker.hint': 'Wählen Sie eine verschlüsselte MSM-Backup-Datei (.enc) aus.',
  'filepicker.drop.hint': 'Alternativ .enc-Datei hierher ziehen',
  'filepicker.drop.invalid': 'Bitte nur .enc-Dateien ablegen.',

  'password.label': 'Backup-Passwort',
  'password.placeholder': 'Backup-Passwort eingeben',
  'password.hint': 'Das Passwort wird nicht gespeichert und nach der Entschlüsselung aus dem Speicher gelöscht.',

  'salt.label': 'Salt',
  'salt.placeholder': 'Salt (Base64) eingeben',
  'salt.hint':
    'Der Salt ist in der MSM-Datenbank in der Tabelle panel_settings unter dem Schlüssel backup.salt gespeichert. Er ist nicht in den MSM-UI-Einstellungen sichtbar. Salt ist nicht sensibel und kann frei eingegeben werden. Im Disaster-Recovery-Fall kann der Salt per SQL ausgelesen werden: SELECT value FROM panel_settings WHERE key = \'backup.salt\'.',

  'decrypt.button': 'Entschlüsseln',
  'decrypt.button.loading': 'Entschlüssele …',

  'progress.decrypting': 'Backup wird entschlüsselt …',
  'progress.deriving': 'Schlüssel wird abgeleitet …',
  'progress.extracting': 'Dateien werden extrahiert …',

  'state.success.title': 'Entschlüsselung erfolgreich',
  'state.success.description':
    'Die Backup-Datei wurde erfolgreich entschlüsselt und extrahiert. Unten sehen Sie die enthaltenen Dateien.',
  'state.success.size': 'Entschlüsselte Größe',
  'state.success.retry': 'Neue Datei entschlüsseln',

  'state.error.title': 'Entschlüsselung fehlgeschlagen',
  'state.error.default':
    'Falsches Passwort oder ungültige Datei. Bitte überprüfen Sie Ihre Eingaben und versuchen Sie es erneut.',
  'state.error.empty': 'Die ausgewählte Datei ist leer oder ungültig.',
  'state.error.corruptFrame': 'Die Datei hat ein ungültiges Frame-Format und konnte nicht entschlüsselt werden.',
  'state.error.extraction': 'Das Entpacken der tar.gz-Datei ist fehlgeschlagen. Die Datei könnte beschädigt sein.',
  'state.error.retry': 'Erneut versuchen',

  'validation.passwordRequired': 'Bitte geben Sie ein Backup-Passwort ein.',
  'validation.saltRequired': 'Bitte geben Sie den Salt ein.',
  'validation.fileRequired': 'Bitte wählen Sie eine .enc-Datei aus.',

  'tree.heading': 'Dateien im Backup',
  'tree.aria': 'Backup-Inhaltsbaum',
  'tree.empty': 'Keine Dateien gefunden.',
  'tree.manifest': 'Manifest',

  'preview.empty': 'Wählen Sie eine Datei aus dem Baum, um den Inhalt anzuzeigen.',
  'preview.loading': 'Datei wird geladen …',
  'preview.error': 'Datei konnte nicht gelesen werden.',
  'preview.binary': 'Diese Datei ist binär und kann nicht als Text angezeigt werden.',

  'save.button': 'Extrahierte Dateien speichern',
  'save.button.saving': 'Speichere …',
  'save.dialog.title': 'ZIP-Datei speichern',
  'save.success': 'Dateien wurden erfolgreich als ZIP gespeichert.',
  'save.error': 'Speichern fehlgeschlagen. Bitte versuchen Sie es erneut.',

  'dis.badge': 'Powered by DIS',
  'dis.badge.title': 'Defensive Integration Shield',
  'dis.badge.aria': 'Powered by DIS - Defensive Integration Shield',

  'language.label': 'Sprache',
  'language.de': 'Deutsch',
  'language.en': 'English',

  'footer.offline': 'Diese App funktioniert vollständig offline.',
} as const;

export type LocaleKeys = keyof typeof de;
