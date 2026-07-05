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

import {
  extractTarGz,
  saveExtracted,
  readTextFile,
  createTempDir,
  writeTempFile,
  cleanupTempDir,
  type FileTreeNode,
} from './tauri-commands';

beforeEach(() => {
  mockInvoke.mockReset();
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
  it('calls invoke with write_temp_file command and args', async () => {
    mockInvoke.mockResolvedValue('/tmp/msm-recovery-abc/backup.tar.gz');
    const data = new Uint8Array([1, 2, 3]);
    const result = await writeTempFile('/tmp/dir', 'backup.tar.gz', data);
    expect(mockInvoke).toHaveBeenCalledWith('write_temp_file', {
      dirPath: '/tmp/dir',
      filename: 'backup.tar.gz',
      data: [1, 2, 3],
    });
    expect(result).toBe('/tmp/msm-recovery-abc/backup.tar.gz');
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
