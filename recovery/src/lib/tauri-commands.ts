/**
 * Tauri command wrappers for the MSM Backup Recovery app.
 *
 * Provides typed bindings for the Rust backend commands defined in
 * `src-tauri/src/commands.rs`. Each function is a thin `invoke` wrapper so
 * the frontend gets full type safety and tests can mock the `invoke` import
 * without touching the component layer.
 *
 * Commands:
 * - `extractTarGz`   – extract a tar.gz to a temp dir, return the file tree
 * - `saveExtracted`  – copy extracted files to a user-chosen directory
 * - `readTextFile`   – read a UTF-8 text file for preview
 * - `createTempDir`  – create + track a unique temp directory
 * - `writeTempFile`  – write raw bytes to a file inside a temp dir
 * - `cleanupTempDir` – recursively delete a temp directory
 */

import { invoke } from '@tauri-apps/api/core';
import { writeFile } from '@tauri-apps/plugin-fs';
import { join } from '@tauri-apps/api/path';

/** Recursive file-tree node (mirrors the Rust `FileTree` struct). */
export interface FileTreeNode {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  children: FileTreeNode[];
}

/**
 * Extract a `.tar.gz` archive into `outputDir` and return the file tree.
 * Throws a string error from Rust on corrupt input (VAL-EXTRACT-007).
 */
export async function extractTarGz(
  tarGzPath: string,
  outputDir: string,
): Promise<FileTreeNode> {
  return invoke<FileTreeNode>('extract_tar_gz', {
    tarGzPath,
    outputDir,
  });
}

/**
 * Copy all extracted files from `sourceDir` to `targetDir`.
 * Throws a string error on failure.
 */
export async function saveExtracted(
  sourceDir: string,
  targetDir: string,
): Promise<void> {
  await invoke('save_extracted', { sourceDir, targetDir });
}

/**
 * Save the extracted backup files as a ZIP archive.
 * Throws a string error from Rust on failure.
 */
export async function saveAsZip(
  sourceDir: string,
  zipPath: string,
): Promise<void> {
  await invoke('save_as_zip', { sourceDir, zipPath });
}

/**
 * Read a UTF-8 text file for preview. Throws if the file is missing, too
 * large, or not valid UTF-8.
 */
export async function readTextFile(path: string): Promise<string> {
  return invoke<string>('read_text_file', { path });
}

/**
 * Create a unique temp directory for a recovery session. The directory is
 * tracked by the Rust backend for automatic cleanup on app exit
 * (VAL-CROSS-004).
 */
export async function createTempDir(): Promise<string> {
  return invoke<string>('create_temp_dir');
}

/**
 * Write raw bytes to a file inside a temp directory. Returns the full path
 * of the written file.
 *
 * Uses the `@tauri-apps/plugin-fs` `writeFile` command, which serialises the
 * `Uint8Array` as binary (Vec<u8>) over IPC. The previous implementation
 * converted the entire buffer to a JS `Array<number>` via `Array.from`, which
 * boxed every byte and was catastrophically slow + memory-hungry for large
 * backups (50 MB => ~400 MB of boxed numbers).
 */
export async function writeTempFile(
  dirPath: string,
  filename: string,
  data: Uint8Array,
): Promise<string> {
  const filePath = await join(dirPath, filename);
  await writeFile(filePath, data);
  return filePath;
}

/**
 * Recursively delete a temp directory. Called by the frontend when the user
 * starts a new session. The Rust exit handler is a safety net.
 */
export async function cleanupTempDir(dirPath: string): Promise<void> {
  await invoke('cleanup_temp_dir', { dirPath });
}
