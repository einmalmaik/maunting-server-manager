/**
 * FilePicker - selects an encrypted MSM backup (.enc) file.
 *
 * Uses the Tauri file dialog (`@tauri-apps/plugin-dialog`) with an `.enc`
 * extension filter, then reads the file bytes via `@tauri-apps/plugin-fs`.
 * The chosen file name and raw bytes are surfaced to the parent through
 * `onFileSelected`. Also supports drag & drop of `.enc` files directly onto
 * the drop zone (M2 polish).
 *
 * In non-Tauri / test contexts the Tauri APIs are injected through the
 * `tauriDialog` / `tauriFs` props so component tests can mock them without
 * requiring a running Tauri runtime.
 */

import { useState, useCallback } from 'react';
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
  const [dragOver, setDragOver] = useState(false);

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

  /**
   * Drag & drop handler for .enc files (M2 polish). Reads the dropped file
   * directly from the browser File API – no Tauri dialog needed.
   */
  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      if (disabled) return;

      const files = e.dataTransfer?.files;
      if (!files || files.length === 0) return;

      const file = files[0];
      const lowerName = file.name.toLowerCase();
      if (!lowerName.endsWith('.enc')) {
        setError(t('filepicker.drop.invalid'));
        return;
      }

      try {
        const buf = await file.arrayBuffer();
        const bytes = new Uint8Array(buf);
        onFileSelected(file.name, bytes);
        setError(null);
      } catch {
        setError(t('state.error.default'));
      }
    },
    [disabled, onFileSelected, t],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  return (
    <div className="flex flex-col gap-2" data-testid="filepicker">
      <label className="text-sm font-medium text-foreground">{t('filepicker.label')}</label>
      <div
        className={
          'msm-dropzone flex flex-col items-center gap-2 rounded-lg border-2 border-dashed p-4 transition-colors ' +
          (dragOver ? 'border-ring bg-ring/5' : 'border-border')
        }
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        data-testid="filepicker-dropzone"
        data-drag-over={dragOver ? 'true' : undefined}
      >
        <button
          type="button"
          onClick={handlePick}
          disabled={disabled}
          className="msm-btn-secondary inline-flex h-11 items-center justify-center gap-2 px-4 text-sm"
          data-testid="filepicker-button"
        >
          {t('filepicker.button')}
        </button>
        <p className="text-xs text-muted-foreground/70" data-testid="filepicker-drop-hint">
          {t('filepicker.drop.hint')}
        </p>
      </div>
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
