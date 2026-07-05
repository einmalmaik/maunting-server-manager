/**
 * DisBadge - "Powered by DIS - Defensive Integration Shield".
 *
 * Small, calm pill that marks the recovery app as powered by the Defensive
 * Integration Shield (@msdis/shield). Rendered with Design-DNA tokens only
 * (no raw hex colors). Uses no external logo asset so it works fully offline.
 */

import { useLanguage } from '@/lib/useLanguage';

export function DisBadge() {
  const { t } = useLanguage();

  return (
    <span
      className="msm-dis-badge"
      role="img"
      aria-label={t('dis.badge.aria')}
      title={t('dis.badge.title')}
      data-testid="dis-badge"
    >
      <span className="msm-dis-badge-dot" aria-hidden="true" />
      {t('dis.badge')}
    </span>
  );
}
