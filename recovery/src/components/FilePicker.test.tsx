/**
 * FilePicker component tests (VAL-UI-002).
 *
 * The Tauri dialog + fs APIs are mocked so the component can be tested in
 * jsdom without a running Tauri runtime.
 */

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { LanguageProvider } from '@/lib/useLanguage';
import { FilePicker } from './FilePicker';

const ENC_BYTES = new Uint8Array([0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00]);

afterEach(() => {
  cleanup();
});

describe('VAL-UI-002: FilePicker', () => {
  it('renders a file picker button and placeholder', () => {
    render(
      <LanguageProvider>
        <FilePicker fileName={null} onFileSelected={() => {}} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('filepicker-button')).toBeDefined();
    expect(screen.getByTestId('filepicker-placeholder').textContent).toContain('Keine Datei');
  });

  it('opens dialog with .enc filter and surfaces selected file', async () => {
    const openMock = vi.fn().mockResolvedValue('C:\\backups\\panel-2026.enc');
    const readFileMock = vi.fn().mockResolvedValue(ENC_BYTES);
    const onFile = vi.fn();

    render(
      <LanguageProvider>
        <FilePicker
          fileName={null}
          onFileSelected={onFile}
          tauriDialog={openMock}
          tauriFs={readFileMock}
        />
      </LanguageProvider>,
    );

    await fireEvent.click(screen.getByTestId('filepicker-button'));

    expect(openMock).toHaveBeenCalledOnce();
    const opts = openMock.mock.calls[0][0];
    expect(opts.filters[0].extensions).toContain('enc');

    await waitFor(() => {
      expect(readFileMock).toHaveBeenCalledWith('C:\\backups\\panel-2026.enc');
      expect(onFile).toHaveBeenCalledWith('panel-2026.enc', ENC_BYTES);
    });
  });

  it('does nothing when dialog is cancelled (null)', async () => {
    const openMock = vi.fn().mockResolvedValue(null);
    const readFileMock = vi.fn();
    const onFile = vi.fn();

    render(
      <LanguageProvider>
        <FilePicker
          fileName={null}
          onFileSelected={onFile}
          tauriDialog={openMock}
          tauriFs={readFileMock}
        />
      </LanguageProvider>,
    );

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(readFileMock).not.toHaveBeenCalled();
      expect(onFile).not.toHaveBeenCalled();
    });
  });

  it('shows the selected file name when provided', () => {
    render(
      <LanguageProvider>
        <FilePicker fileName="backup.enc" onFileSelected={() => {}} />
      </LanguageProvider>,
    );
    const sel = screen.getByTestId('filepicker-selected');
    expect(sel.textContent).toContain('backup.enc');
  });
});
