/**
 * SaveButton – saves extracted files to a user-chosen directory.
 *
 * Opens a Tauri directory picker dialog, then calls the Rust `save_extracted`
 * command to copy all files from the temp extraction directory to the chosen
 * target. Shows loading, success, and error states with German i18n text.
 *
 * VAL-EXTRACT-006: Save button saves extracted files to chosen directory
 */

import { useState } from 'react';
import { open as defaultOpen } from '@tauri-apps/plugin-dialog';
import { saveExtracted } from '@/lib/tauri-commands';
import { useLanguage } from '@/lib/useLanguage';

/** Minimal dialog open signature (swapped for tests). */
export type DialogOpenDir = () => Promise<string | null>;
/** Minimal save_extracted signature (swapped for tests). */
export type SaveFn = (sourceDir: string, targetDir: string) => Promise<void>;

export interface SaveButtonProps {
  /** Path to the temp extraction directory (source for the copy). */
  sourceDir: string;
  /** Override the Tauri directory dialog (tests). */
  tauriDialog?: DialogOpenDir;
  /** Override the save_extracted call (tests). */
  saveFn?: SaveFn;
  /** Disable the button (e.g. during other operations). */
  disabled?: boolean;
}

export function SaveButton({
  sourceDir,
  tauriDialog,
  saveFn,
  disabled = false,
}: SaveButtonProps) {
  const { t } = useLanguage();
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSave = async () => {
    setStatus('saving');
    setErrorMsg(null);

    const openFn = tauriDialog ?? defaultOpen;
    const doSave = saveFn ?? saveExtracted;

    try {
      const targetDir = await openFn({
        title: t('save.dialog.title'),
        directory: true,
        multiple: false,
      });

      if (!targetDir) {
        // User cancelled – return to idle without error
        setStatus('idle');
        return;
      }

      await doSave(sourceDir, targetDir);
      setStatus('saved');
    } catch (err) {
      setErrorMsg(typeof err === 'string' ? err : t('save.error'));
      setStatus('error');
    }
  };

  const isSaving = status === 'saving';
  const isDisabled = disabled || isSaving;

  return (
    <div className="flex flex-col gap-2" data-testid="save-button-container">
      <button
        type="button"
        onClick={handleSave}
        disabled={isDisabled}
        className="msm-btn-primary inline-flex h-11 items-center justify-center gap-2 px-4 text-sm"
        data-testid="save-button"
      >
        {isSaving ? (
          <span
            className="inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent"
            aria-hidden="true"
          />
        ) : (
          <svg
            className="size-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.8}
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"
            />
          </svg>
        )}
        {isSaving ? t('save.button.saving') : t('save.button')}
      </button>

      {status === 'saved' ? (
        <p className="text-sm text-success" data-testid="save-success" role="status">
          {t('save.success')}
        </p>
      ) : null}

      {status === 'error' ? (
        <p className="text-sm text-destructive" data-testid="save-error" role="alert">
          {errorMsg ?? t('save.error')}
        </p>
      ) : null}
    </div>
  );
}
