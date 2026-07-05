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
  'filepicker.drop.hint': 'Or drag and drop a .enc file here',
  'filepicker.drop.invalid': 'Please drop only .enc files.',

  'password.label': 'Backup password',
  'password.placeholder': 'Enter backup password',
  'password.hint':
    'The password is not stored and is cleared from memory after decryption.',

  'salt.label': 'Salt',
  'salt.placeholder': 'Enter salt (Base64)',
  'salt.hint':
    'The salt is stored in the MSM database in the panel_settings table under the key backup.salt. It is not visible in the MSM UI settings. Salt is not sensitive and can be entered freely. In a disaster recovery case, the salt can be read via SQL: SELECT value FROM panel_settings WHERE key = \'backup.salt\'.',

  'decrypt.button': 'Decrypt',
  'decrypt.button.loading': 'Decrypting …',

  'progress.decrypting': 'Decrypting backup …',
  'progress.extracting': 'Extracting files …',

  'state.success.title': 'Decryption successful',
  'state.success.description':
    'The backup file was decrypted and extracted successfully. The contained files are shown below.',
  'state.success.size': 'Decrypted size',
  'state.success.retry': 'Decrypt another file',

  'state.error.title': 'Decryption failed',
  'state.error.default':
    'Wrong password or invalid file. Please check your input and try again.',
  'state.error.empty': 'The selected file is empty or invalid.',
  'state.error.corruptFrame': 'The file has an invalid frame format and could not be decrypted.',
  'state.error.extraction': 'Extracting the tar.gz file failed. The file may be corrupt.',
  'state.error.retry': 'Try again',

  'validation.passwordRequired': 'Please enter a backup password.',
  'validation.saltRequired': 'Please enter the salt.',
  'validation.fileRequired': 'Please select a .enc file.',

  'tree.heading': 'Files in backup',
  'tree.aria': 'Backup content tree',
  'tree.empty': 'No files found.',
  'tree.manifest': 'Manifest',

  'preview.empty': 'Select a file from the tree to view its content.',
  'preview.loading': 'Loading file …',
  'preview.error': 'File could not be read.',
  'preview.binary': 'This file is binary and cannot be displayed as text.',

  'save.button': 'Save extracted files',
  'save.button.saving': 'Saving …',
  'save.dialog.title': 'Choose target directory',
  'save.success': 'Files were saved successfully.',
  'save.error': 'Save failed. Please try again.',

  'dis.badge': 'Powered by DIS',
  'dis.badge.title': 'Defensive Integration Shield',
  'dis.badge.aria': 'Powered by DIS - Defensive Integration Shield',

  'language.label': 'Language',
  'language.de': 'German',
  'language.en': 'English',

  'footer.offline': 'This app works completely offline.',
};
