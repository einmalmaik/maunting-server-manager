/**
 * DecryptButton - triggers DIS decryption with a loading spinner state.
 *
 * Shows a spinner and disables itself while decryption is in progress
 * (VAL-UI-005). The loading label comes from i18n so it carries umlauts
 * ("Entschlüssele …") in German.
 */

import { useLanguage } from '@/lib/useLanguage';

export interface DecryptButtonProps {
  onClick: () => void;
  loading?: boolean;
  disabled?: boolean;
}

export function DecryptButton({ onClick, loading = false, disabled = false }: DecryptButtonProps) {
  const { t } = useLanguage();
  const isDisabled = disabled || loading;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      className="msm-btn-primary inline-flex h-12 items-center justify-center gap-2 px-6 text-sm"
      data-testid="decrypt-button"
    >
      {loading ? (
        <span
          className="inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent"
          aria-hidden="true"
          data-testid="decrypt-spinner"
        />
      ) : null}
      {loading ? t('decrypt.button.loading') : t('decrypt.button')}
    </button>
  );
}
