/**
 * i18n tests (VAL-UI-010).
 *
 * Asserts that the German (de) and English (en) locale files share identical
 * key sets so no string is missing in either language, and that switching
 * locale changes the produced text.
 */

import { describe, it, expect } from 'vitest';
import { de } from './de';
import { en } from './en';
import { translate, translationKeys, type Language } from './index';

describe('VAL-UI-010: i18n key parity', () => {
  const deKeys = Object.keys(de).sort();
  const enKeys = Object.keys(en).sort();

  it('de and en have the same number of keys', () => {
    expect(deKeys.length).toBe(enKeys.length);
  });

  it('every de key exists in en', () => {
    for (const key of deKeys) {
      expect(enKeys, `en is missing key "${key}"`).toContain(key);
    }
  });

  it('every en key exists in de', () => {
    for (const key of enKeys) {
      expect(deKeys, `de is missing key "${key}"`).toContain(key);
    }
  });

  it('translationKeys matches de keys', () => {
    expect([...translationKeys].sort()).toEqual(deKeys);
  });
});

describe('VAL-UI-010: translate switches locale', () => {
  it('returns German text for de', () => {
    expect(translate('de', 'decrypt.button')).toBe('Entschlüsseln');
  });

  it('returns English text for en', () => {
    expect(translate('en', 'decrypt.button')).toBe('Decrypt');
  });

  it('German text contains umlauts', () => {
    const germanText = translate('de', 'decrypt.button');
    expect(germanText).toMatch(/[äöüß]/);
  });

  it('salt hint mentions panel_settings / backup.salt', () => {
    const deHint = translate('de', 'salt.hint');
    const enHint = translate('en', 'salt.hint');
    expect(deHint).toContain('panel_settings');
    expect(deHint).toContain('backup.salt');
    expect(enHint).toContain('panel_settings');
    expect(enHint).toContain('backup.salt');
  });

  it('every locale has values for all keys (no empty strings)', () => {
    const langs: Language[] = ['de', 'en'];
    for (const lang of langs) {
      for (const key of translationKeys) {
        const value = translate(lang, key);
        expect(value, `${lang}.${key} is empty`).not.toBe('');
        expect(value, `${lang}.${key} fell back to key`).not.toBe(key);
      }
    }
  });
});
