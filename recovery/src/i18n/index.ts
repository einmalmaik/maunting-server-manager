/**
 * i18n system for the MSM Backup Recovery app.
 *
 * KISS: a minimal type-safe dictionary lookup, no external i18n library.
 * Locales (`de`, `en`) share identical keys (enforced by the parity test in
 * `i18n.test.ts` → VAL-UI-010).
 */

import { de, type LocaleKeys } from './de';
import { en } from './en';

export type Language = 'de' | 'en';

export const locales = { de, en } as const;

export type TranslationKey = LocaleKeys;

/** All keys that exist in every locale. */
export const translationKeys: readonly TranslationKey[] = Object.keys(de) as TranslationKey[];

/**
 * Returns the translated string for `key` in `lang`.
 *
 * Falls back to German (the primary locale) if the key is somehow missing from
 * the selected locale, and to the raw key as a last resort. This keeps the UI
 * legible during development without crashing.
 */
export function translate(lang: Language, key: TranslationKey): string {
  const dict = locales[lang] ?? de;
  return dict[key] ?? de[key] ?? key;
}

/** Re-export locale objects for tests / parity checks. */
export { de, en };
