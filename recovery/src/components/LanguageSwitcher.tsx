/**
 * LanguageSwitcher - toggles between German (de) and English (en).
 *
 * Small pill with two buttons. Uses the shared `useLanguage` context so every
 * component re-renders with the new locale on switch (VAL-UI-010).
 */

import { useLanguage, type Language } from '@/lib/useLanguage';

const LANGS: Language[] = ['de', 'en'];

export function LanguageSwitcher() {
  const { language, setLanguage } = useLanguage();

  return (
    <div
      className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary/60 p-1"
      role="group"
      aria-label="language"
      data-testid="language-switcher"
    >
      {LANGS.map((lang) => (
        <button
          key={lang}
          type="button"
          onClick={() => setLanguage(lang)}
          aria-pressed={language === lang}
          className={
            'rounded-full px-3 py-1 text-xs uppercase tracking-wide transition-colors ' +
            (language === lang
              ? 'bg-primary/15 text-foreground'
              : 'text-muted-foreground hover:text-foreground')
          }
        >
          {lang}
        </button>
      ))}
    </div>
  );
}
