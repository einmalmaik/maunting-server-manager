/**
 * SuccessState - shown after a successful DIS decryption.
 *
 * M1 scope: confirms the backup was decrypted and shows the decrypted tar.gz
 * size. The full file-tree preview / extraction is added in M2
 * (`recovery-full-ui` + `rust-extraction`), so VAL-UI-006's file-tree
 * surface is completed there. This component provides the success transition
 * and retry action that the M1 step-flow requires.
 */

import { useLanguage } from '@/lib/useLanguage';

export interface SuccessStateProps {
  /** Decrypted output size in bytes. */
  decryptedBytes: number;
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

export function SuccessState({ decryptedBytes, onRetry }: SuccessStateProps) {
  const { t } = useLanguage();

  return (
    <div
      className="msm-card flex flex-col items-center gap-4 p-8 text-center"
      role="status"
      data-testid="success-state"
    >
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
      <button
        type="button"
        onClick={onRetry}
        className="msm-btn-secondary inline-flex h-10 items-center justify-center px-4 text-sm"
        data-testid="success-retry"
      >
        {t('state.success.retry')}
      </button>
    </div>
  );
}
