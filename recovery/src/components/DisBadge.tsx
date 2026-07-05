/**
 * DisBadge - "Powered by DIS - Defensive Integration Shield".
 *
 * Follows the MauntingStudios Design-DNA `DisBadge.tsx` pattern: a small,
 * round, calm pill with the DIS logo image, "Powered by DIS" text, and a
 * link to the DIS page. Rendered with Design-DNA tokens only (no raw hex
 * colors). Uses the `dis-logo.png` asset from the design-dna.
 */

import { useLanguage } from '@/lib/useLanguage';

export interface DisBadgeProps {
  /** Logo image size in pixels. */
  size?: number;
  /** Show or hide the "Powered by DIS" text. */
  showText?: boolean;
  /** Override the logo source (tests / custom assets). */
  logoSrc?: string;
}

export function DisBadge({ size = 24, showText = true, logoSrc = '/dis-logo.png' }: DisBadgeProps) {
  const { t } = useLanguage();

  return (
    <a
      href="https://dis.mauntingstudios.de"
      target="_blank"
      rel="noopener noreferrer"
      aria-label={t('dis.badge.aria')}
      title={t('dis.badge.title')}
      className="msm-dis-badge-link inline-flex items-center gap-2 rounded-full border border-ring/15 bg-ring/10 px-2 py-0.5 backdrop-blur-sm transition-all hover:bg-ring/25 hover:border-ring/25"
      data-testid="dis-badge"
    >
      <span
        className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-ring/20 bg-black/40 shadow-sm"
        style={{ width: size, height: size }}
      >
        <img
          src={logoSrc}
          alt=""
          aria-hidden="true"
          width={size}
          height={size}
          className="h-full w-full rounded-full object-cover"
          loading="eager"
          decoding="async"
        />
      </span>
      {showText ? (
        <span className="text-[9px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">
          {t('dis.badge')}
        </span>
      ) : null}
    </a>
  );
}
