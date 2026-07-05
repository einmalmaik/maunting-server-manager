/**
 * ProgressBar – progress bar shown during decryption and extraction.
 *
 * Supports two modes:
 * - Determinate: pass a `progress` value (0..1) to show a real percentage
 *   driven by the per-frame `onProgress` callback in `decryptBackup`.
 * - Indeterminate: omit `progress` to show a calm animated bar that
 *   communicates "work in progress" (used for extraction, which has no
 *   progress callback).
 *
 * The bar uses only Design-DNA tokens (no raw hex colors) and CSS keyframe
 * animations defined in styles.css.
 */

import { useLanguage } from '@/lib/useLanguage';

export interface ProgressBarProps {
  /** Optional label shown above the bar. Defaults to the i18n progress text. */
  label?: string;
  /** 0..1 for determinate mode; omitted/undefined for indeterminate mode. */
  progress?: number;
}

export function ProgressBar({ label, progress }: ProgressBarProps) {
  const { t } = useLanguage();
  const isDeterminate = progress != null;
  const percent = isDeterminate ? Math.round(progress * 100) : 0;

  return (
    <div
      className="flex flex-col gap-2"
      role="progressbar"
      aria-valuetext={isDeterminate ? `${percent}%` : t('progress.decrypting')}
      data-testid="progress-bar"
    >
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground" data-testid="progress-label">
          {label ?? t('progress.decrypting')}
        </p>
        {isDeterminate ? (
          <span className="text-sm font-mono text-muted-foreground" data-testid="progress-percent">
            {percent}%
          </span>
        ) : null}
      </div>
      <div className="msm-progress-track" aria-hidden="true">
        {isDeterminate ? (
          <div
            className="msm-progress-fill-determinate"
            style={{ width: `${Math.max(2, percent)}%` }}
          />
        ) : (
          <div className="msm-progress-fill" />
        )}
      </div>
    </div>
  );
}
