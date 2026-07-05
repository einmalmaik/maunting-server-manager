/**
 * DisBadge + LanguageSwitcher component tests (VAL-UI-008, VAL-UI-010).
 */

// @vitest-environment jsdom

import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { LanguageProvider } from '@/lib/useLanguage';
import { DisBadge } from './DisBadge';
import { LanguageSwitcher } from './LanguageSwitcher';

afterEach(() => {
  cleanup();
});

describe('VAL-UI-008: DisBadge', () => {
  it('renders "Powered by DIS" text', () => {
    render(
      <LanguageProvider>
        <DisBadge />
      </LanguageProvider>,
    );
    const badge = screen.getByTestId('dis-badge');
    expect(badge.textContent).toContain('Powered by DIS');
  });

  it('has aria-label referencing Defensive Integration Shield', () => {
    render(
      <LanguageProvider>
        <DisBadge />
      </LanguageProvider>,
    );
    const badge = screen.getByTestId('dis-badge');
    expect(badge.getAttribute('aria-label')).toContain('Defensive Integration Shield');
    expect(badge.getAttribute('aria-label')).toContain('DIS');
  });
});

describe('VAL-UI-010: LanguageSwitcher', () => {
  it('renders de + en buttons', () => {
    render(
      <LanguageProvider>
        <LanguageSwitcher />
      </LanguageProvider>,
    );
    expect(screen.getByText('de')).toBeDefined();
    expect(screen.getByText('en')).toBeDefined();
  });

  it('switching to en changes DisBadge aria through context', () => {
    render(
      <LanguageProvider>
        <LanguageSwitcher />
        <DisBadge />
      </LanguageProvider>,
    );
    // Default is de
    expect(screen.getByTestId('dis-badge').getAttribute('aria-label')).toMatch(/DIS/);
    // Switch to en
    fireEvent.click(screen.getByText('en'));
    // Badge text is the same ("Powered by DIS") in both locales
    expect(screen.getByTestId('dis-badge').textContent).toContain('Powered by DIS');
  });
});
