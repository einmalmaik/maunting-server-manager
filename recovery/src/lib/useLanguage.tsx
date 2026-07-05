/**
 * Language context for the MSM Backup Recovery app.
 *
 * KISS: the active locale lives in React state only (no localStorage, no
 * persistence). This keeps the recovery app stateless and avoids any
 * persistent storage surface (VAL-CROSS-003: nothing sensitive is ever
 * persisted). The language preference resets to German on each app launch.
 */

import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import { translate, type Language, type TranslationKey } from '@/i18n';

export type { Language };

export interface LanguageContextValue {
  language: Language;
  setLanguage: (lang: Language) => void;
  toggle: () => void;
  /** Shorthand translator bound to the active language. */
  t: (key: TranslationKey) => string;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

export interface LanguageProviderProps {
  children: ReactNode;
  initial?: Language;
}

export function LanguageProvider({ children, initial = 'de' }: LanguageProviderProps) {
  const [language, setLanguage] = useState<Language>(initial);

  const value = useMemo<LanguageContextValue>(
    () => ({
      language,
      setLanguage,
      toggle: () => setLanguage((prev) => (prev === 'de' ? 'en' : 'de')),
      t: (key: TranslationKey) => translate(language, key),
    }),
    [language],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

/** Returns the active language context. Falls back to German defaults outside a provider. */
export function useLanguage(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (!ctx) {
    return {
      language: 'de',
      setLanguage: () => undefined,
      toggle: () => undefined,
      t: (key: TranslationKey) => translate('de', key),
    };
  }
  return ctx;
}
