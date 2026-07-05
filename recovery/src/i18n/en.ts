/**
 * English locale for the MSM Backup Recovery app.
 *
 * Keys MUST match the German locale (`de.ts`) exactly so the i18n parity
 * test (VAL-UI-010) passes.
 */

import type { LocaleKeys } from './de';

export const en: Record<LocaleKeys, string> = {
  'app.title': 'MSM Backup Recovery',
  'app.subtitle': 'Offline decryption for MSM backups',

  'step.input.heading': 'Decrypt backup',
  'step.input.description':
    'Select a .enc backup file and enter the backup password and salt.',

  'filepicker.label': 'Backup file (.enc)',
  'filepicker.button': 'Choose file',
  'filepicker.placeholder': 'No file selected',
  'filepicker.selected': 'Selected file',
  'filepicker.hint': 'Select an encrypted MSM backup file (.enc).',

  'password.label': 'Backup password',
  'password.placeholder': 'Enter backup password',
  'password.hint':
    'The password is not stored and is cleared from memory after decryption.',

  'salt.label': 'Salt',
  'salt.placeholder': 'Enter salt (Base64)',
  'salt.hint':
    'The salt for decryption is found in MSM under panel_settings as backup.salt. Salt is not sensitive and can be entered freely.',

  'decrypt.button': 'Decrypt',
  'decrypt.button.loading': 'Decrypting …',

  'state.success.title': 'Decryption successful',
  'state.success.description':
    'The backup file was decrypted successfully. The tar.gz file is ready.',
  'state.success.size': 'Decrypted size',
  'state.success.retry': 'Decrypt another file',

  'state.error.title': 'Decryption failed',
  'state.error.default':
    'Wrong password or invalid file. Please check your input and try again.',
  'state.error.empty': 'The selected file is empty or invalid.',
  'state.error.retry': 'Try again',

  'validation.passwordRequired': 'Please enter a backup password.',
  'validation.saltRequired': 'Please enter the salt.',
  'validation.fileRequired': 'Please select a .enc file.',

  'dis.badge': 'Powered by DIS',
  'dis.badge.title': 'Defensive Integration Shield',
  'dis.badge.aria': 'Powered by DIS - Defensive Integration Shield',

  'language.label': 'Language',
  'language.de': 'German',
  'language.en': 'English',

  'footer.offline': 'This app works completely offline.',
};
