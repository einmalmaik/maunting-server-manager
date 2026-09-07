import { Dropdown, type DropdownOption } from '@/Singra/UI'

export type PresenceStatus = 'online' | 'away' | 'invisible'

interface StatusDotProps {
  status?: PresenceStatus | string | null
  className?: string
  size?: 'sm' | 'md' | 'lg'
}

export function StatusDot({ status = 'invisible', className = '', size = 'md' }: StatusDotProps) {
  const sizeClasses = {
    sm: 'w-2 h-2',
    md: 'w-2.5 h-2.5',
    lg: 'w-3 h-3',
  }[size]

  if (status === 'online') {
    return (
      <span
        className={`inline-block rounded-full bg-emerald-500 ring-2 ring-surface shadow-[0_0_8px_rgba(16,185,129,0.5)] ${sizeClasses} ${className}`}
        title="Online"
      />
    )
  }

  if (status === 'away') {
    return (
      <span
        className={`inline-block rounded-full bg-amber-500 ring-2 ring-surface shadow-[0_0_8px_rgba(245,158,11,0.5)] ${sizeClasses} ${className}`}
        title="Abwesend"
      />
    )
  }

  // Invisible / Offline
  return (
    <span
      className={`inline-block rounded-full bg-on-surface-variant/40 ring-2 ring-surface ${sizeClasses} ${className}`}
      title="Unsichtbar / Offline"
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
  const options: DropdownOption[] = [
    {
      value: 'online',
      label: 'Online',
      icon: <StatusDot status="online" size="sm" />,
    },
    {
      value: 'away',
      label: 'Abwesend',
      icon: <StatusDot status="away" size="sm" />,
    },
    {
      value: 'invisible',
      label: 'Unsichtbar',
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
