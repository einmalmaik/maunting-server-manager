/**
 * tauri-commands tests.
 *
 * Verifies that the frontend command wrappers call `invoke` with the correct
 * command name and arguments. The `@tauri-apps/api/core` invoke function is
 * mocked so no Tauri runtime is needed.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock @tauri-apps/api/core invoke
const mockInvoke = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => mockInvoke(...args),
}));

// Mock @tauri-apps/api/path join (used by writeTempFile)
const mockJoin = vi.fn();
vi.mock('@tauri-apps/api/path', () => ({
  join: (...args: unknown[]) => mockJoin(...args),
}));

// Mock @tauri-apps/plugin-fs writeFile (used by writeTempFile)
const mockWriteFile = vi.fn();
vi.mock('@tauri-apps/plugin-fs', () => ({
  writeFile: (...args: unknown[]) => mockWriteFile(...args),
}));

import {
  extractTarGz,
  saveExtracted,
  saveAsZip,
  readTextFile,
  createTempDir,
  writeTempFile,
  cleanupTempDir,
  type FileTreeNode,
} from './tauri-commands';

beforeEach(() => {
  mockInvoke.mockReset();
  mockJoin.mockReset();
  mockWriteFile.mockReset();
});

describe('extractTarGz', () => {
  it('calls invoke with extract_tar_gz command and args', async () => {
    const mockTree: FileTreeNode = {
      name: 'root',
      path: '/tmp/root',
      is_dir: true,
      size: 0,
      children: [],
    };
    mockInvoke.mockResolvedValue(mockTree);

    const result = await extractTarGz('/tmp/backup.tar.gz', '/tmp/out');

    expect(mockInvoke).toHaveBeenCalledWith('extract_tar_gz', {
      tarGzPath: '/tmp/backup.tar.gz',
      outputDir: '/tmp/out',
    });
    expect(result).toEqual(mockTree);
  });

  it('propagates errors from Rust', async () => {
    mockInvoke.mockRejectedValue('Entpacken fehlgeschlagen: invalid gzip');
    await expect(extractTarGz('/bad', '/out')).rejects.toThrow();
  });
});

describe('saveExtracted', () => {
  it('calls invoke with save_extracted command and args', async () => {
    mockInvoke.mockResolvedValue(undefined);
    await saveExtracted('/tmp/src', '/tmp/dst');
    expect(mockInvoke).toHaveBeenCalledWith('save_extracted', {
      sourceDir: '/tmp/src',
      targetDir: '/tmp/dst',
    });
  });
});

describe('saveAsZip', () => {
  it('calls invoke with save_as_zip command and args', async () => {
    mockInvoke.mockResolvedValue(undefined);
    await saveAsZip('/tmp/src', '/tmp/output.zip');
    expect(mockInvoke).toHaveBeenCalledWith('save_as_zip', {
      sourceDir: '/tmp/src',
      zipPath: '/tmp/output.zip',
    });
  });
});

describe('readTextFile', () => {
  it('calls invoke with read_text_file command and path', async () => {
    mockInvoke.mockResolvedValue('file content');
    const result = await readTextFile('/tmp/file.txt');
    expect(mockInvoke).toHaveBeenCalledWith('read_text_file', { path: '/tmp/file.txt' });
    expect(result).toBe('file content');
  });
});

describe('createTempDir', () => {
  it('calls invoke with create_temp_dir command', async () => {
    mockInvoke.mockResolvedValue('/tmp/msm-recovery-abc');
    const result = await createTempDir();
    expect(mockInvoke).toHaveBeenCalledWith('create_temp_dir');
    expect(result).toBe('/tmp/msm-recovery-abc');
  });
});

describe('writeTempFile', () => {
  it('joins the path and writes the bytes via plugin-fs writeFile', async () => {
    mockJoin.mockResolvedValue('/tmp/dir/backup.tar.gz');
    mockWriteFile.mockResolvedValue(undefined);
    const data = new Uint8Array([1, 2, 3]);
    const result = await writeTempFile('/tmp/dir', 'backup.tar.gz', data);
    expect(mockJoin).toHaveBeenCalledWith('/tmp/dir', 'backup.tar.gz');
    expect(mockWriteFile).toHaveBeenCalledWith('/tmp/dir/backup.tar.gz', data);
    expect(result).toBe('/tmp/dir/backup.tar.gz');
  });

  it('passes the Uint8Array through without boxing to a plain array', async () => {
    mockJoin.mockResolvedValue('/tmp/dir/backup.tar.gz');
    mockWriteFile.mockResolvedValue(undefined);
    const data = new Uint8Array(4);
    await writeTempFile('/tmp/dir', 'backup.tar.gz', data);
    // The data argument passed to writeFile must remain a Uint8Array (not
    // Array.from'd into a plain Array<number>).
    expect(mockWriteFile.mock.calls[0][1]).toBeInstanceOf(Uint8Array);
  });

  it('does not call invoke (bypasses the slow IPC array encoding)', async () => {
    mockJoin.mockResolvedValue('/tmp/dir/backup.tar.gz');
    mockWriteFile.mockResolvedValue(undefined);
    await writeTempFile('/tmp/dir', 'backup.tar.gz', new Uint8Array([1]));
    expect(mockInvoke).not.toHaveBeenCalled();
  });
});

describe('cleanupTempDir', () => {
  it('calls invoke with cleanup_temp_dir command and path', async () => {
    mockInvoke.mockResolvedValue(undefined);
    await cleanupTempDir('/tmp/msm-recovery-abc');
    expect(mockInvoke).toHaveBeenCalledWith('cleanup_temp_dir', {
      dirPath: '/tmp/msm-recovery-abc',
    });
  });
});

describe('FileTreeNode type', () => {
  it('has the expected shape', () => {
    const node: FileTreeNode = {
      name: 'test',
      path: '/tmp/test',
      is_dir: false,
      size: 100,
      children: [],
    };
    expect(node.name).toBe('test');
    expect(node.is_dir).toBe(false);
    expect(node.size).toBe(100);
    expect(node.children).toEqual([]);
  });
});
