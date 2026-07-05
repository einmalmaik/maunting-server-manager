/**
 * SuccessState - shown after successful DIS decryption + extraction.
 *
 * Displays the file tree of extracted contents, a file preview panel, and a
 * save button that copies the extracted files to a user-chosen directory
 * via the Rust `save_extracted` command.
 *
 * VAL-UI-006: success state with file tree
 * VAL-EXTRACT-002: file tree with folders, files, sizes
 * VAL-EXTRACT-003: text file preview
 * VAL-EXTRACT-004: JSON file preview formatted
 * VAL-EXTRACT-005: manifest.json highlighted
 * VAL-EXTRACT-006: save button
 */

import { useState } from 'react';
import { useLanguage } from '@/lib/useLanguage';
import type { FileTreeNode } from '@/lib/tauri-commands';
import { FileTree } from './FileTree';
import { FilePreview } from './FilePreview';
import { SaveButton } from './SaveButton';

export interface SuccessStateProps {
  /** Decrypted output size in bytes. */
  decryptedBytes: number;
  /** Root file tree node from the Rust `extract_tar_gz` command. */
  fileTree: FileTreeNode;
  /** Path to the temp extraction directory (source for save). */
  extractedDir: string;
  /** Called when the user clicks "decrypt another file". */
  onRetry: () => void;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${bytes} B`;
}

export function SuccessState({
  decryptedBytes,
  fileTree,
  extractedDir,
  onRetry,
}: SuccessStateProps) {
  const { t } = useLanguage();
  const [selectedFile, setSelectedFile] = useState<FileTreeNode | null>(null);

  return (
    <div
      className="msm-card flex flex-col gap-5 p-6"
      role="status"
      data-testid="success-state"
    >
      {/* Header */}
      <div className="flex flex-col items-center gap-3 text-center">
        <div
          className="flex size-12 items-center justify-center rounded-full border border-success/30 bg-success/10"
          aria-hidden="true"
        >
          <svg
            className="size-6 text-success"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2.5}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <h2 className="text-lg font-semibold text-foreground">{t('state.success.title')}</h2>
        <p className="max-w-md text-sm text-muted-foreground">
          {t('state.success.description')}
        </p>
        <p className="text-sm text-muted-foreground">
          <span className="text-foreground">{t('state.success.size')}:</span>{' '}
          {formatSize(decryptedBytes)}
        </p>
      </div>

      {/* File tree + preview side by side (stack on narrow screens) */}
      <div className="grid gap-4 md:grid-cols-2">
        <FileTree
          tree={fileTree}
          selectedPath={selectedFile?.path ?? null}
          onFileSelect={setSelectedFile}
        />
        <FilePreview file={selectedFile} />
      </div>

      {/* Actions */}
      <div className="flex flex-col items-center gap-3 pt-2">
        <SaveButton sourceDir={extractedDir} />
        <button
          type="button"
          onClick={onRetry}
          className="msm-btn-secondary inline-flex h-10 items-center justify-center px-4 text-sm"
          data-testid="success-retry"
        >
          {t('state.success.retry')}
        </button>
      </div>
    </div>
  );
}
