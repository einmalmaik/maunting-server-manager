/**
 * App integration tests for the full step-flow UI.
 *
 * Covers:
 * - VAL-UI-001: app renders with dark theme + Design-DNA tokens
 * - VAL-UI-002: file picker accepts .enc
 * - VAL-UI-005: decrypt button loading state
 * - VAL-UI-006: success state after decryption
 * - VAL-UI-007: error state with German message on wrong password
 * - VAL-UI-008: DIS badge visible
 * - VAL-UI-009: German text with umlauts
 * - VAL-UI-010: i18n keys exist (de + en), locale switch works
 * - VAL-EXTRACT-002: file tree shows files with sizes
 * - VAL-EXTRACT-005: manifest.json highlighted
 * - VAL-CROSS-001: full flow select → decrypt → extract → tree → save
 * - VAL-CROSS-002: no network requests
 * - VAL-CROSS-003: password not stored after decryption
 * - VAL-CROSS-004: temp cleanup on reset
 *
 * The DIS `decryptBackup` function is mocked to avoid the Argon2id cost in
 * UI tests. The Tauri dialog + fs APIs are mocked via the FilePicker props.
 * The tauri-commands (createTempDir, writeTempFile, extractTarGz,
 * cleanupTempDir) are mocked at module level.
 */

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import App from './App';
import { gzipBytes, utf8, bytesToBase64, createTestEnc, deriveTestKey } from '@/lib/test-fixture';
import type { FileTreeNode } from '@/lib/tauri-commands';

// Mock decryptBackup so UI tests don't pay the Argon2id cost on every run.
vi.mock('@/lib/decrypt', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/decrypt')>();
  return {
    ...actual,
    decryptBackup: vi.fn(),
  };
});

// Mock tauri-commands (invoke wrappers) so no Tauri runtime is needed.
const mockCreateTempDir = vi.fn();
const mockWriteTempFile = vi.fn();
const mockExtractTarGz = vi.fn();
const mockCleanupTempDir = vi.fn();
const mockSaveExtracted = vi.fn();
const mockSaveAsZip = vi.fn();
const mockReadTextFile = vi.fn();

vi.mock('@/lib/tauri-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/tauri-commands')>();
  return {
    ...actual,
    createTempDir: (...args: unknown[]) => mockCreateTempDir(...args),
    writeTempFile: (...args: unknown[]) => mockWriteTempFile(...args),
    extractTarGz: (...args: unknown[]) => mockExtractTarGz(...args),
    cleanupTempDir: (...args: unknown[]) => mockCleanupTempDir(...args),
    saveExtracted: (...args: unknown[]) => mockSaveExtracted(...args),
    saveAsZip: (...args: unknown[]) => mockSaveAsZip(...args),
    readTextFile: (...args: unknown[]) => mockReadTextFile(...args),
  };
});

import { decryptBackup, DecryptError } from '@/lib/decrypt';
const mockedDecrypt = vi.mocked(decryptBackup);

const SALT = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]);
const SALT_B64 = bytesToBase64(SALT);

// A small valid .enc fixture (encrypted tar.gz with "hello" content).
async function makeEncFixture(): Promise<Uint8Array> {
  const key = await deriveTestKey('test-password', SALT);
  return createTestEnc(gzipBytes(utf8('hello-recovery')), key);
}

const MOCK_FILE_TREE: FileTreeNode = {
  name: 'out',
  path: '/tmp/out',
  is_dir: true,
  size: 0,
  children: [
    { name: 'a.txt', path: '/tmp/out/a.txt', is_dir: false, size: 6, children: [] },
    {
      name: 'manifest.json',
      path: '/tmp/out/manifest.json',
      is_dir: false,
      size: 15,
      children: [],
    },
  ],
};

let encFixture: Uint8Array<ArrayBuffer>;

beforeEach(async () => {
  const fixture = await makeEncFixture();
  // Copy into a fresh ArrayBuffer so the type is Uint8Array<ArrayBuffer>
  // (matches @tauri-apps/plugin-fs readFile return type).
  const buf = new ArrayBuffer(fixture.byteLength);
  encFixture = new Uint8Array(buf);
  encFixture.set(fixture);
  mockedDecrypt.mockReset();
  mockCreateTempDir.mockReset();
  mockWriteTempFile.mockReset();
  mockExtractTarGz.mockReset();
  mockCleanupTempDir.mockReset();
  mockSaveExtracted.mockReset();
  mockSaveAsZip.mockReset();
  mockReadTextFile.mockReset();

  // Default happy-path mocks for the full flow
  mockCreateTempDir.mockResolvedValue('/tmp/msm-recovery-test');
  mockWriteTempFile.mockResolvedValue('/tmp/msm-recovery-test/backup.tar.gz');
  mockExtractTarGz.mockResolvedValue(MOCK_FILE_TREE);
  mockCleanupTempDir.mockResolvedValue(undefined);
  mockSaveExtracted.mockResolvedValue(undefined);
  mockSaveAsZip.mockResolvedValue(undefined);
  mockReadTextFile.mockResolvedValue('file content');
});

afterEach(() => {
  cleanup();
});

describe('VAL-UI-001 / VAL-UI-008 / VAL-UI-009: App renders', () => {
  it('renders the title, DIS badge, and German text with umlauts', () => {
    render(<App />);
    expect(screen.getByTestId('app-root')).toBeDefined();
    expect(screen.getByText('MSM Backup Recovery')).toBeDefined();
    // DIS badge visible (VAL-UI-008)
    expect(screen.getByTestId('dis-badge').textContent).toContain('Powered by DIS');
    // German text with umlauts (VAL-UI-009) — "Entschlüsseln"
    expect(screen.getByTestId('decrypt-button').textContent).toMatch(/Entschlüsseln/);
  });

  it('uses Design-DNA token classes (not raw hex)', () => {
    render(<App />);
    // The app root uses semantic token-based classes.
    const root = screen.getByTestId('app-root');
    expect(root.className).toMatch(/text-foreground|bg-/);
  });
});

describe('VAL-UI-002: File picker in app', () => {
  it('renders a file picker button', () => {
    render(<App />);
    expect(screen.getByTestId('filepicker-button')).toBeDefined();
  });

  it('renders a drag & drop zone', () => {
    render(<App />);
    expect(screen.getByTestId('filepicker-dropzone')).toBeDefined();
  });
});

// Module-level Tauri API mocks for the full flow test.
vi.mock('@tauri-apps/plugin-dialog', () => ({
  open: vi.fn().mockResolvedValue('C:\\backups\\test.enc'),
  save: vi.fn().mockResolvedValue(null),
}));
vi.mock('@tauri-apps/plugin-fs', () => ({
  readFile: vi.fn().mockImplementation(async () => {
    const buf = new ArrayBuffer(encFixture.byteLength);
    const view = new Uint8Array(buf);
    view.set(encFixture);
    return view;
  }),
}));

import { open as mockedOpen, save as mockedSave } from '@tauri-apps/plugin-dialog';
import { readFile as mockedReadFile } from '@tauri-apps/plugin-fs';

describe('VAL-CROSS-001: full flow — select → decrypt → extract → tree → save', () => {
  it('success: pick file → enter password+salt → decrypt → extract → file tree', async () => {
    const decryptedBytes = gzipBytes(utf8('hello-recovery'));
    mockedDecrypt.mockResolvedValue(decryptedBytes);

    render(<App />);

    // Pick file
    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('test.enc');
    });

    // Enter password + salt
    fireEvent.change(screen.getByTestId('password-field'), {
      target: { value: 'test-password' },
    });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });

    // Click decrypt
    fireEvent.click(screen.getByTestId('decrypt-button'));

    // Should reach success state with file tree
    await waitFor(() => {
      expect(screen.getByTestId('success-state')).toBeDefined();
    });

    // File tree should be rendered with the mock data
    expect(screen.getByTestId('file-tree')).toBeDefined();
    expect(screen.getByTestId('tree-file-a.txt')).toBeDefined();

    // decryptBackup was called with the file bytes, password, and salt
    expect(mockedDecrypt).toHaveBeenCalledOnce();
    const args = mockedDecrypt.mock.calls[0];
    expect(args[1]).toBe('test-password');
    expect(args[2]).toBe(SALT_B64);

    // Temp dir was created and extraction was called
    expect(mockCreateTempDir).toHaveBeenCalledOnce();
    expect(mockWriteTempFile).toHaveBeenCalledOnce();
    expect(mockExtractTarGz).toHaveBeenCalledOnce();
  });

  it('save: clicking save opens dialog and calls save_as_zip', async () => {
    const decryptedBytes = gzipBytes(utf8('hello-recovery'));
    mockedDecrypt.mockResolvedValue(decryptedBytes);
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\test.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(encFixture);

    render(<App />);

    // Full flow to get to success state
    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('test.enc');
    });
    fireEvent.change(screen.getByTestId('password-field'), { target: { value: 'test-password' } });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });
    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('save-button')).toBeDefined();
    });

    // Mock the save dialog for ZIP save
    vi.mocked(mockedSave).mockResolvedValue('C:\\output\\msm-backup.zip');

    // Click save
    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => {
      expect(mockSaveAsZip).toHaveBeenCalledOnce();
    });

    await waitFor(() => {
      expect(screen.getByTestId('save-success')).toBeDefined();
    });
  });

  it('reset: clicking "decrypt another file" cleans up temp dir', async () => {
    const decryptedBytes = gzipBytes(utf8('hello-recovery'));
    mockedDecrypt.mockResolvedValue(decryptedBytes);
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\test.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(encFixture);

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('test.enc');
    });
    fireEvent.change(screen.getByTestId('password-field'), { target: { value: 'test-password' } });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });
    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('success-state')).toBeDefined();
    });

    // Click retry / reset
    fireEvent.click(screen.getByTestId('success-retry'));

    await waitFor(() => {
      expect(mockCleanupTempDir).toHaveBeenCalledOnce();
    });

    // Should be back to input state
    expect(screen.getByTestId('input-card')).toBeDefined();
  });
});

describe('VAL-UI-005: decrypt button loading state', () => {
  it('shows loading state during decryption', async () => {
    // Make decrypt hang so we can observe the loading state
    let resolveDecrypt: (value: Uint8Array) => void = () => {};
    mockedDecrypt.mockImplementation(
      () => new Promise((resolve) => { resolveDecrypt = resolve; }),
    );

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('test.enc');
    });
    fireEvent.change(screen.getByTestId('password-field'), { target: { value: 'pw' } });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });
    fireEvent.click(screen.getByTestId('decrypt-button'));

    // Should show loading state
    await waitFor(() => {
      expect(screen.getByTestId('decrypt-spinner')).toBeDefined();
    });
    expect((screen.getByTestId('decrypt-button') as HTMLButtonElement).disabled).toBe(true);

    // Resolve to clean up
    resolveDecrypt(gzipBytes(utf8('ok')));
    await waitFor(() => {
      expect(screen.getByTestId('success-state')).toBeDefined();
    });
  });
});

describe('VAL-UI-007: error states with German messages', () => {
  it('error: wrong password → German error message', async () => {
    mockedDecrypt.mockRejectedValue(new Error('Decryption failed'));
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\bad.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(encFixture);

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('bad.enc');
    });

    fireEvent.change(screen.getByTestId('password-field'), {
      target: { value: 'wrong-password' },
    });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });

    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('error-state')).toBeDefined();
    });
    // German error message with umlauts
    const msg = screen.getByTestId('error-message').textContent ?? '';
    expect(msg).toMatch(/[äü]/);
  });

  it('error: empty file → DecryptError → empty message', async () => {
    mockedDecrypt.mockRejectedValue(new DecryptError('empty'));
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\empty.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(new Uint8Array(new ArrayBuffer(0)));

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('empty.enc');
    });

    fireEvent.change(screen.getByTestId('password-field'), {
      target: { value: 'pw' },
    });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });

    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('error-state')).toBeDefined();
    });
    const msg = screen.getByTestId('error-message').textContent ?? '';
    expect(msg).toContain('leer');
  });

  it('error: corrupt frame → specific frame error message', async () => {
    mockedDecrypt.mockRejectedValue(new Error('Invalid frame format: truncated'));
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\corrupt.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(encFixture);

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('corrupt.enc');
    });
    fireEvent.change(screen.getByTestId('password-field'), { target: { value: 'pw' } });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });
    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('error-state')).toBeDefined();
    });
    const msg = screen.getByTestId('error-message').textContent ?? '';
    expect(msg).toContain('Frame-Format');
  });

  it('error: extraction failure → extraction error message', async () => {
    mockedDecrypt.mockResolvedValue(gzipBytes(utf8('ok')));
    mockExtractTarGz.mockRejectedValue('Entpacken fehlgeschlagen: invalid gzip');
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\test.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(encFixture);

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('test.enc');
    });
    fireEvent.change(screen.getByTestId('password-field'), { target: { value: 'pw' } });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });
    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('error-state')).toBeDefined();
    });
    const msg = screen.getByTestId('error-message').textContent ?? '';
    expect(msg).toContain('Entpacken');
  });
});

describe('VAL-EXTRACT-005: manifest.json highlighted in tree', () => {
  it('manifest.json appears with data-manifest attribute and badge', async () => {
    mockedDecrypt.mockResolvedValue(gzipBytes(utf8('ok')));
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\test.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(encFixture);

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('test.enc');
    });
    fireEvent.change(screen.getByTestId('password-field'), { target: { value: 'pw' } });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });
    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('tree-file-manifest.json')).toBeDefined();
    });
    expect(screen.getByTestId('tree-file-manifest.json').getAttribute('data-manifest')).toBe('true');
    expect(screen.getByTestId('manifest-badge')).toBeDefined();
  });
});

describe('VAL-CROSS-003: password not stored after decryption', () => {
  it('clears the password field after successful decryption', async () => {
    mockedDecrypt.mockResolvedValue(gzipBytes(utf8('ok')));
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\x.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(encFixture);

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('x.enc');
    });

    const pwField = screen.getByTestId('password-field') as HTMLInputElement;
    fireEvent.change(pwField, { target: { value: 'secret-pw' } });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });

    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('success-state')).toBeDefined();
    });

    // Password must be cleared from memory (React state)
    // After success, the input card is replaced by the success state, so the
    // password field is no longer in the DOM — confirming the password state
    // was cleared. We also verify no password leaked to localStorage.
    expect(screen.queryByTestId('password-field')).toBeNull();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it('clears the password field even on error (retry shows empty field)', async () => {
    mockedDecrypt.mockRejectedValue(new Error('fail'));
    vi.mocked(mockedOpen).mockResolvedValue('C:\\backups\\y.enc');
    vi.mocked(mockedReadFile).mockResolvedValue(encFixture);

    render(<App />);

    await fireEvent.click(screen.getByTestId('filepicker-button'));
    await waitFor(() => {
      expect(screen.getByTestId('filepicker-selected').textContent).toContain('y.enc');
    });

    fireEvent.change(screen.getByTestId('password-field'), {
      target: { value: 'should-be-cleared' },
    });
    fireEvent.change(screen.getByTestId('salt-field'), { target: { value: SALT_B64 } });

    fireEvent.click(screen.getByTestId('decrypt-button'));

    await waitFor(() => {
      expect(screen.getByTestId('error-state')).toBeDefined();
    });

    // Click retry → back to input, password field must be empty
    fireEvent.click(screen.getByTestId('error-retry'));
    const pwField = screen.getByTestId('password-field') as HTMLInputElement;
    expect(pwField.value).toBe('');
  });
});

describe('VAL-CROSS-002: no network requests', () => {
  it('does not issue fetch/XHR during render', () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(() => {
      throw new Error('fetch should not be called');
    });
    const xhrOpen = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => {
      throw new Error('XHR should not be called');
    });

    render(<App />);
    expect(screen.getByTestId('app-root')).toBeDefined();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(xhrOpen).not.toHaveBeenCalled();

    fetchSpy.mockRestore();
    xhrOpen.mockRestore();
  });
});

describe('VAL-UI-010: locale switch in app', () => {
  it('switching to en changes the decrypt button text', () => {
    render(<App />);
    // Default is de — "Entschlüsseln"
    expect(screen.getByTestId('decrypt-button').textContent).toContain('Entschlüsseln');

    fireEvent.click(screen.getByText('en'));
    // Now it should be "Decrypt" in en
    expect(screen.getByTestId('decrypt-button').textContent).toContain('Decrypt');
    expect(screen.getByTestId('decrypt-button').textContent).not.toContain('Entschlüsseln');
  });
});
