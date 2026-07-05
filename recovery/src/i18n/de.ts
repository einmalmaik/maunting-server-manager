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

  'password.label': 'Backup-Passwort',
  'password.placeholder': 'Backup-Passwort eingeben',
  'password.hint': 'Das Passwort wird nicht gespeichert und nach der Entschlüsselung aus dem Speicher gelöscht.',

  'salt.label': 'Salt',
  'salt.placeholder': 'Salt (Base64) eingeben',
  'salt.hint':
    'Der Salt für die Entschlüsselung befindet sich in MSM unter panel_settings als backup.salt. Salt ist nicht sensibel und kann frei eingegeben werden.',

  'decrypt.button': 'Entschlüsseln',
  'decrypt.button.loading': 'Entschlüssele …',

  'state.success.title': 'Entschlüsselung erfolgreich',
  'state.success.description':
    'Die Backup-Datei wurde erfolgreich entschlüsselt. Die tar.gz-Datei ist bereit.',
  'state.success.size': 'Entschlüsselte Größe',
  'state.success.retry': 'Neue Datei entschlüsseln',

  'state.error.title': 'Entschlüsselung fehlgeschlagen',
  'state.error.default':
    'Falsches Passwort oder ungültige Datei. Bitte überprüfen Sie Ihre Eingaben und versuchen Sie es erneut.',
  'state.error.empty': 'Die ausgewählte Datei ist leer oder ungültig.',
  'state.error.retry': 'Erneut versuchen',

  'validation.passwordRequired': 'Bitte geben Sie ein Backup-Passwort ein.',
  'validation.saltRequired': 'Bitte geben Sie den Salt ein.',
  'validation.fileRequired': 'Bitte wählen Sie eine .enc-Datei aus.',

  'dis.badge': 'Powered by DIS',
  'dis.badge.title': 'Defensive Integration Shield',
  'dis.badge.aria': 'Powered by DIS - Defensive Integration Shield',

  'language.label': 'Sprache',
  'language.de': 'Deutsch',
  'language.en': 'English',

  'footer.offline': 'Diese App funktioniert vollständig offline.',
} as const;

export type LocaleKeys = keyof typeof de;
