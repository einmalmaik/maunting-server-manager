import { useTranslation } from 'react-i18next'

export type DeviceType = 'web' | 'desktop' | 'mobile'

interface DeviceBadgeProps {
  deviceType?: DeviceType | string | null
  className?: string
  showLabel?: boolean
}

/**
 * Crisp SVG Device Badge for Web, Desktop (MSS), and Mobile.
 * Follows MauntingStudios Design-DNA guidelines.
 */
export function DeviceBadge({ deviceType = 'web', className = '', showLabel = false }: DeviceBadgeProps) {
  const { t } = useTranslation()

  const type = (deviceType || 'web').toLowerCase()

  if (type === 'desktop') {
    return (
      <span
        className={`inline-flex items-center gap-1.5 text-xs text-primary/90 font-medium ${className}`}
        title={t('social.device.desktop')}
        aria-label={t('social.device.desktop')}
      >
        <svg
          className="w-3.5 h-3.5 shrink-0"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <rect width="20" height="14" x="2" y="3" rx="2" />
          <line x1="8" x2="16" y1="21" y2="21" />
          <line x1="12" x2="12" y1="17" y2="21" />
        </svg>
        {showLabel && <span>{t('social.device.desktop')}</span>}
      </span>
    )
  }

  if (type === 'mobile') {
    return (
      <span
        className={`inline-flex items-center gap-1.5 text-xs text-tertiary/90 font-medium ${className}`}
        title={t('social.device.mobile')}
        aria-label={t('social.device.mobile')}
      >
        <svg
          className="w-3.5 h-3.5 shrink-0"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <rect width="14" height="20" x="5" y="2" rx="2" ry="2" />
          <path d="M12 18h.01" />
        </svg>
        {showLabel && <span>{t('social.device.mobile')}</span>}
      </span>
    )
  }

  // Default: Web-Panel
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs text-on-surface-variant/80 font-medium ${className}`}
      title={t('social.device.web')}
      aria-label={t('social.device.web')}
    >
      <svg
        className="w-3.5 h-3.5 shrink-0"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="10" />
        <line x1="2" x2="22" y1="12" y2="12" />
        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
      </svg>
      {showLabel && <span>{t('social.device.web')}</span>}
    </span>
  )
}
