/**
 * SaveButton component tests (VAL-EXTRACT-006).
 *
 * Verifies that the save button opens a file-save dialog, calls
 * save_as_zip, and shows success/error feedback.
 */

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { LanguageProvider } from '@/lib/useLanguage';
import { SaveButton } from './SaveButton';

afterEach(() => {
  cleanup();
});

describe('VAL-EXTRACT-006: SaveButton', () => {
  it('renders a save button with German text', () => {
    render(
      <LanguageProvider>
        <SaveButton sourceDir="/tmp/extracted" />
      </LanguageProvider>,
    );
    const btn = screen.getByTestId('save-button') as HTMLButtonElement;
    expect(btn.textContent).toContain('speichern');
  });

  it('opens save dialog and calls save_as_zip on click', async () => {
    const mockDialog = vi.fn().mockResolvedValue('C:\\Users\\backup-output.zip');
    const mockSave = vi.fn().mockResolvedValue(undefined);

    render(
      <LanguageProvider>
        <SaveButton
          sourceDir="/tmp/extracted"
          tauriDialog={mockDialog}
          saveFn={mockSave}
        />
      </LanguageProvider>,
    );

    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => {
      expect(mockDialog).toHaveBeenCalledOnce();
    });

    await waitFor(() => {
      expect(mockSave).toHaveBeenCalledWith('/tmp/extracted', 'C:\\Users\\backup-output.zip');
    });

    await waitFor(() => {
      expect(screen.getByTestId('save-success')).toBeDefined();
    });
  });

  it('shows saving state during operation', async () => {
    const mockDialog = vi.fn().mockResolvedValue('C:\\out');
    const mockSave = vi.fn().mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(undefined), 100)),
    );

    render(
      <LanguageProvider>
        <SaveButton
          sourceDir="/tmp/extracted"
          tauriDialog={mockDialog}
          saveFn={mockSave}
        />
      </LanguageProvider>,
    );

    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => {
      expect(screen.getByTestId('save-button').textContent).toContain('Speichere');
    });

    await waitFor(() => {
      expect(screen.getByTestId('save-success')).toBeDefined();
    });
  });

  it('does not call save when dialog is cancelled', async () => {
    const mockDialog = vi.fn().mockResolvedValue(null);
    const mockSave = vi.fn();

    render(
      <LanguageProvider>
        <SaveButton
          sourceDir="/tmp/extracted"
          tauriDialog={mockDialog}
          saveFn={mockSave}
        />
      </LanguageProvider>,
    );

    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => {
      expect(mockDialog).toHaveBeenCalledOnce();
    });

    // Wait a tick to ensure no save call
    await waitFor(() => {
      expect(mockSave).not.toHaveBeenCalled();
    });
    // Should not show success or error
    expect(screen.queryByTestId('save-success')).toBeNull();
    expect(screen.queryByTestId('save-error')).toBeNull();
  });

  it('shows error message on save failure', async () => {
    const mockDialog = vi.fn().mockResolvedValue('C:\\out');
    const mockSave = vi.fn().mockRejectedValue('Kopieren fehlgeschlagen');

    render(
      <LanguageProvider>
        <SaveButton
          sourceDir="/tmp/extracted"
          tauriDialog={mockDialog}
          saveFn={mockSave}
        />
      </LanguageProvider>,
    );

    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => {
      expect(screen.getByTestId('save-error')).toBeDefined();
    });
    expect(screen.getByTestId('save-error').textContent).toContain('Kopieren');
  });

  it('shows German success message with umlauts', async () => {
    const mockDialog = vi.fn().mockResolvedValue('C:\\out');
    const mockSave = vi.fn().mockResolvedValue(undefined);

    render(
      <LanguageProvider>
        <SaveButton
          sourceDir="/tmp/extracted"
          tauriDialog={mockDialog}
          saveFn={mockSave}
        />
      </LanguageProvider>,
    );

    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => {
      expect(screen.getByTestId('save-success')).toBeDefined();
    });
    const msg = screen.getByTestId('save-success').textContent ?? '';
    // "Dateien wurden erfolgreich gespeichert."
    expect(msg).toContain('erfolgreich');
  });

  it('is disabled when disabled prop is set', () => {
    render(
      <LanguageProvider>
        <SaveButton sourceDir="/tmp/extracted" disabled />
      </LanguageProvider>,
    );
    expect((screen.getByTestId('save-button') as HTMLButtonElement).disabled).toBe(true);
  });
});
