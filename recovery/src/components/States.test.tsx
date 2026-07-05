/**
 * DecryptButton + SuccessState + ErrorState component tests
 * (VAL-UI-005, VAL-UI-006, VAL-UI-007, VAL-UI-009).
 */

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { LanguageProvider } from '@/lib/useLanguage';
import { DecryptButton } from './DecryptButton';
import { SuccessState } from './SuccessState';
import { ErrorState } from './ErrorState';

afterEach(() => {
  cleanup();
});

describe('VAL-UI-005: DecryptButton loading state', () => {
  it('renders the decrypt label by default', () => {
    render(
      <LanguageProvider>
        <DecryptButton onClick={() => {}} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('decrypt-button').textContent).toContain('Entschlüsseln');
  });

  it('shows a spinner and is disabled when loading', () => {
    render(
      <LanguageProvider>
        <DecryptButton onClick={() => {}} loading />
      </LanguageProvider>,
    );
    const btn = screen.getByTestId('decrypt-button') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId('decrypt-spinner')).toBeDefined();
    expect(btn.textContent).toContain('Entschlüssele');
    expect(btn.getAttribute('aria-busy')).toBe('true');
  });

  it('calls onClick when clicked (not loading)', () => {
    const onClick = vi.fn();
    render(
      <LanguageProvider>
        <DecryptButton onClick={onClick} />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByTestId('decrypt-button'));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('does not call onClick when loading', () => {
    const onClick = vi.fn();
    render(
      <LanguageProvider>
        <DecryptButton onClick={onClick} loading />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByTestId('decrypt-button'));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('German loading text contains umlauts', () => {
    render(
      <LanguageProvider>
        <DecryptButton onClick={() => {}} loading />
      </LanguageProvider>,
    );
    // "Entschlüssele …" contains ü and …
    expect(screen.getByTestId('decrypt-button').textContent).toMatch(/ü/);
  });
});

describe('VAL-UI-006: SuccessState', () => {
  it('renders a success title with umlauts', () => {
    render(
      <LanguageProvider>
        <SuccessState decryptedBytes={1024} onRetry={() => {}} />
      </LanguageProvider>,
    );
    const title = screen.getByTestId('success-state').textContent ?? '';
    // "Entschlüsselung erfolgreich" contains ü
    expect(title).toMatch(/ü/);
    expect(title).toContain('erfolgreich');
  });

  it('shows the decrypted size', () => {
    render(
      <LanguageProvider>
        <SuccessState decryptedBytes={2048} onRetry={() => {}} />
      </LanguageProvider>,
    );
    const text = screen.getByTestId('success-state').textContent ?? '';
    expect(text).toContain('2.0 KB');
  });

  it('calls onRetry when retry button is clicked', () => {
    const onRetry = vi.fn();
    render(
      <LanguageProvider>
        <SuccessState decryptedBytes={100} onRetry={onRetry} />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByTestId('success-retry'));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe('VAL-UI-007 + VAL-UI-009: ErrorState German message', () => {
  it('renders a German error message with umlauts', () => {
    render(
      <LanguageProvider>
        <ErrorState onRetry={() => {}} />
      </LanguageProvider>,
    );
    const msg = screen.getByTestId('error-message').textContent ?? '';
    // "Falsches Passwort oder ungültige Datei…" contains ä and ü
    expect(msg).toMatch(/[äü]/);
  });

  it('renders a custom error message key', () => {
    render(
      <LanguageProvider>
        <ErrorState messageKey="state.error.empty" onRetry={() => {}} />
      </LanguageProvider>,
    );
    const msg = screen.getByTestId('error-message').textContent ?? '';
    // "Die ausgewählte Datei ist leer oder ungültig." contains ä and ü
    expect(msg).toContain('leer');
  });

  it('calls onRetry when retry button is clicked', () => {
    const onRetry = vi.fn();
    render(
      <LanguageProvider>
        <ErrorState onRetry={onRetry} />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByTestId('error-retry'));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('has role="alert" for accessibility', () => {
    render(
      <LanguageProvider>
        <ErrorState onRetry={() => {}} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('error-state').getAttribute('role')).toBe('alert');
  });
});
