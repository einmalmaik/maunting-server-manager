import { useTranslation } from 'react-i18next'

import { Dropdown, type DropdownOption } from '@/Singra/UI'

export type PresenceStatus = 'online' | 'away' | 'invisible'

interface StatusDotProps {
  status?: PresenceStatus | string | null
  className?: string
  size?: 'sm' | 'md' | 'lg'
}

export function StatusDot({ status = 'invisible', className = '', size = 'md' }: StatusDotProps) {
  const { t } = useTranslation()
  const sizeClasses = {
    sm: 'w-2 h-2',
    md: 'w-2.5 h-2.5',
    lg: 'w-3 h-3',
  }[size]

  if (status === 'online') {
    return (
      <span
        className={`inline-block rounded-full bg-status-success ring-2 ring-surface shadow-[0_0_8px_rgba(16,185,129,0.5)] ${sizeClasses} ${className}`}
        title={t('social.status.online')}
        aria-label={t('social.status.online')}
      />
    )
  }

  if (status === 'away') {
    return (
      <span
        className={`inline-block rounded-full bg-status-warning ring-2 ring-surface shadow-[0_0_8px_rgba(245,158,11,0.5)] ${sizeClasses} ${className}`}
        title={t('social.status.away')}
        aria-label={t('social.status.away')}
      />
    )
  }

  // Invisible / Offline
  return (
    <span
      className={`inline-block rounded-full bg-on-surface-variant/40 ring-2 ring-surface ${sizeClasses} ${className}`}
      title={t('social.status.offline')}
      aria-label={t('social.status.offline')}
    />
  )
}

interface StatusSwitcherProps {
  currentStatus: PresenceStatus
  onChange: (newStatus: PresenceStatus) => void
  disabled?: boolean
  className?: string
}

export function StatusSwitcher({ currentStatus, onChange, disabled = false, className = '' }: StatusSwitcherProps) {
  const { t } = useTranslation()
  const options: DropdownOption[] = [
    {
      value: 'online',
      label: t('social.status.online'),
      icon: <StatusDot status="online" size="sm" />,
    },
    {
      value: 'away',
      label: t('social.status.away'),
      icon: <StatusDot status="away" size="sm" />,
    },
    {
      value: 'invisible',
      label: t('social.status.invisible'),
      icon: <StatusDot status="invisible" size="sm" />,
    },
  ]

  return (
    <div className={`status-switcher ${className}`}>
      <Dropdown
        value={currentStatus}
        onChange={(val: string) => onChange(val as PresenceStatus)}
        options={options}
        disabled={disabled}
      />
    </div>
  )
}
