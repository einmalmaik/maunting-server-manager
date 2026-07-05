/**
 * FilePicker - selects an encrypted MSM backup (.enc) file.
 *
 * Uses the Tauri file dialog (`@tauri-apps/plugin-dialog`) with an `.enc`
 * extension filter, then reads the file bytes via `@tauri-apps/plugin-fs`.
 * The chosen file name and raw bytes are surfaced to the parent through
 * `onFileSelected`.
 *
 * In non-Tauri / test contexts the Tauri APIs are injected through the
 * `tauriDialog` / `tauriFs` props so component tests can mock them without
 * requiring a running Tauri runtime.
 */

import { useState } from 'react';
import { open as defaultOpen, type OpenDialogOptions } from '@tauri-apps/plugin-dialog';
import { readFile as defaultReadFile } from '@tauri-apps/plugin-fs';
import { useLanguage } from '@/lib/useLanguage';

/** Minimal dialog open signature (swapped for tests). */
export type DialogOpen = (options?: OpenDialogOptions) => Promise<string | null>;
/** Minimal fs readFile signature (swapped for tests). */
export type FsReadFile = (path: string) => Promise<Uint8Array>;

export interface FilePickerProps {
  /** Currently selected file name (controlled by parent). */
  fileName: string | null;
  /** Called with the file name and raw bytes after a successful pick + read. */
  onFileSelected: (name: string, bytes: Uint8Array) => void;
  /** Override the Tauri dialog opener (tests). */
  tauriDialog?: DialogOpen;
  /** Override the Tauri fs reader (tests). */
  tauriFs?: FsReadFile;
  disabled?: boolean;
}

export function FilePicker({
  fileName,
  onFileSelected,
  tauriDialog,
  tauriFs,
  disabled = false,
}: FilePickerProps) {
  const { t } = useLanguage();
  const [error, setError] = useState<string | null>(null);

  const handlePick = async () => {
    setError(null);
    const openFn = tauriDialog ?? defaultOpen;
    const readFn = tauriFs ?? defaultReadFile;

    try {
      const selected = await openFn({
        title: t('filepicker.label'),
        multiple: false,
        directory: false,
        filters: [{ name: 'MSM Backup (.enc)', extensions: ['enc'] }],
      });

      if (!selected) {
        return; // user cancelled
      }

      const bytes = await readFn(selected);
      const name = selected.split(/[\\/]/).pop() ?? selected;
      onFileSelected(name, bytes);
    } catch {
      setError(t('state.error.default'));
    }
  };

  return (
    <div className="flex flex-col gap-2" data-testid="filepicker">
      <label className="text-sm font-medium text-foreground">{t('filepicker.label')}</label>
      <button
        type="button"
        onClick={handlePick}
        disabled={disabled}
        className="msm-btn-secondary inline-flex h-11 items-center justify-center gap-2 px-4 text-sm"
        data-testid="filepicker-button"
      >
        {t('filepicker.button')}
      </button>
      {fileName ? (
        <p className="text-sm text-muted-foreground" data-testid="filepicker-selected">
          <span className="text-foreground">{t('filepicker.selected')}:</span> {fileName}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground/60" data-testid="filepicker-placeholder">
          {t('filepicker.placeholder')}
        </p>
      )}
      <p className="text-xs text-muted-foreground/70">{t('filepicker.hint')}</p>
      {error ? (
        <p className="text-sm text-destructive" role="alert" data-testid="filepicker-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
