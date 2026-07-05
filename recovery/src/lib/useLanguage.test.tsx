/**
 * Tests for the `useLanguage` context (VAL-UI-010).
 *
 * Verifies that the translator switches text when the language changes and
 * that no localStorage / sessionStorage is used (VAL-CROSS-003: the recovery
 * app stores nothing persistently).
 */

// @vitest-environment jsdom

import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { LanguageProvider, useLanguage } from './useLanguage';

function Probe() {
  const { language, setLanguage, t } = useLanguage();
  return (
    <div>
      <span data-testid="lang">{language}</span>
      <span data-testid="text">{t('decrypt.button')}</span>
      <button onClick={() => setLanguage('en')}>to-en</button>
    </div>
  );
}

afterEach(() => {
  cleanup();
});

describe('useLanguage', () => {
  it('defaults to German', () => {
    render(
      <LanguageProvider>
        <Probe />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('lang').textContent).toBe('de');
    expect(screen.getByTestId('text').textContent).toBe('Entschlüsseln');
  });

  it('switches to English', () => {
    render(
      <LanguageProvider>
        <Probe />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByText('to-en'));
    expect(screen.getByTestId('lang').textContent).toBe('en');
    expect(screen.getByTestId('text').textContent).toBe('Decrypt');
  });

  it('does not persist to localStorage or sessionStorage', () => {
    render(
      <LanguageProvider>
        <Probe />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByText('to-en'));
    // The recovery app must NOT write anything to persistent storage.
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
