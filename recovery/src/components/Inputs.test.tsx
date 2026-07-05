/**
 * PasswordInput + SaltInput component tests
 * (VAL-UI-003, VAL-UI-004, VAL-UI-009).
 */

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { LanguageProvider } from '@/lib/useLanguage';
import { PasswordInput } from './PasswordInput';
import { SaltInput } from './SaltInput';

afterEach(() => {
  cleanup();
});

describe('VAL-UI-003: PasswordInput is type=password', () => {
  it('renders an input with type="password"', () => {
    render(
      <LanguageProvider>
        <PasswordInput value="" onChange={() => {}} />
      </LanguageProvider>,
    );
    const field = screen.getByTestId('password-field') as HTMLInputElement;
    expect(field.type).toBe('password');
  });

  it('calls onChange when typing', () => {
    const onChange = vi.fn();
    render(
      <LanguageProvider>
        <PasswordInput value="abc" onChange={onChange} />
      </LanguageProvider>,
    );
    fireEvent.change(screen.getByTestId('password-field'), { target: { value: 'secret' } });
    expect(onChange).toHaveBeenCalledWith('secret');
  });

  it('disables when disabled prop is set', () => {
    render(
      <LanguageProvider>
        <PasswordInput value="" onChange={() => {}} disabled />
      </LanguageProvider>,
    );
    expect((screen.getByTestId('password-field') as HTMLInputElement).disabled).toBe(true);
  });
});

describe('VAL-UI-004 + VAL-UI-009: SaltInput with hint', () => {
  it('renders an input with type="text" (salt is not sensitive)', () => {
    render(
      <LanguageProvider>
        <SaltInput value="" onChange={() => {}} />
      </LanguageProvider>,
    );
    const field = screen.getByTestId('salt-field') as HTMLInputElement;
    expect(field.type).toBe('text');
  });

  it('shows a hint mentioning panel_settings and backup.salt', () => {
    render(
      <LanguageProvider>
        <SaltInput value="" onChange={() => {}} />
      </LanguageProvider>,
    );
    const hint = screen.getByTestId('salt-hint').textContent ?? '';
    expect(hint).toContain('panel_settings');
    expect(hint).toContain('backup.salt');
  });

  it('German hint contains umlauts', () => {
    render(
      <LanguageProvider>
        <SaltInput value="" onChange={() => {}} />
      </LanguageProvider>,
    );
    // German salt.hint: "Der Salt findet sich in MSM unter panel_settings als backup.salt. Salt ist nicht sensibel und kann frei eingegeben werden."
    const hint = screen.getByTestId('salt-hint').textContent ?? '';
    expect(hint).toMatch(/[äöüß]/);
  });
});
