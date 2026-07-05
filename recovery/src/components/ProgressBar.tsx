/**
 * ProgressBar – animated indeterminate progress bar shown during decryption.
 *
 * Argon2id key derivation and frame-by-frame AES-GCM decryption happen inside
 * @msdis/shield without per-frame progress callbacks, so a determinate bar is
 * not feasible without adding complexity to the decrypt logic. Instead this
 * component shows a calm animated bar that communicates "work in progress"
 * without misleading the user with a fake percentage.
 *
 * The bar uses only Design-DNA tokens (no raw hex colors) and a CSS keyframe
 * animation defined in styles.css.
 */

import { useLanguage } from '@/lib/useLanguage';

export interface ProgressBarProps {
  /** Optional label shown above the bar. Defaults to the i18n progress text. */
  label?: string;
}

export function ProgressBar({ label }: ProgressBarProps) {
  const { t } = useLanguage();

  return (
    <div
      className="flex flex-col gap-2"
      role="progressbar"
      aria-valuetext={t('progress.decrypting')}
      data-testid="progress-bar"
    >
      <p className="text-sm text-muted-foreground" data-testid="progress-label">
        {label ?? t('progress.decrypting')}
      </p>
      <div className="msm-progress-track" aria-hidden="true">
        <div className="msm-progress-fill" />
      </div>
    </div>
  );
}
