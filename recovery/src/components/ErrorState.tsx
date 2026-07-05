/**
 * ErrorState - shown when DIS decryption fails.
 *
 * Displays a clear German error message (VAL-UI-007) and offers a retry
 * action that returns the user to the input step. The message is selected
 * from i18n based on the error category so wrong-password / corrupt-file and
 * empty-file each get a specific, actionable German text.
 */

import { useLanguage } from '@/lib/useLanguage';
import type { TranslationKey } from '@/i18n';

export interface ErrorStateProps {
  /** i18n key of the German error message to display. */
  messageKey?: TranslationKey;
  onRetry: () => void;
}

export function ErrorState({ messageKey = 'state.error.default', onRetry }: ErrorStateProps) {
  const { t } = useLanguage();

  return (
    <div
      className="msm-card flex flex-col items-center gap-4 p-8 text-center"
      role="alert"
      data-testid="error-state"
    >
      <div
        className="flex size-12 items-center justify-center rounded-full border border-destructive/30 bg-destructive/10"
        aria-hidden="true"
      >
        <svg
          className="size-6 text-destructive"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v4m0 4h.01M4.93 19h14.14c1.54 0 2.5-1.67 1.73-3L13.73 4c-.77-1.33-2.69-1.33-3.46 0L3.2 16c-.77 1.33.19 3 1.73 3z"
          />
        </svg>
      </div>
      <h2 className="text-lg font-semibold text-foreground">{t('state.error.title')}</h2>
      <p className="max-w-md text-sm text-muted-foreground" data-testid="error-message">
        {t(messageKey)}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="msm-btn-secondary inline-flex h-10 items-center justify-center px-4 text-sm"
        data-testid="error-retry"
      >
        {t('state.error.retry')}
      </button>
    </div>
  );
}
