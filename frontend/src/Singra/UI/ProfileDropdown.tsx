import React, { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Avatar } from './Avatar'
import { useAnkerLage } from './Ankerlage'

export interface ProfileDropdownItem {
  key: string
  label: string
  icon?: React.ReactNode
  onClick: () => void
  tone?: 'default' | 'danger'
}

export interface ProfileDropdownUser {
  username?: string | null
  email?: string | null
  avatar_url?: string | null
}

export interface ProfileDropdownProps {
  user?: ProfileDropdownUser | null
  items: ProfileDropdownItem[]
  placement?: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left'
  className?: string
  triggerAriaLabel?: string
  avatarSize?: 'xs' | 'sm' | 'md'
  triggerVariant?: 'avatar' | 'full'
  status?: 'online' | 'away' | 'invisible'
  onStatusChange?: (status: 'online' | 'away' | 'invisible') => void
}

/**
 * Barrierefreies, reduziertes Profil-Dropdown der MauntingStudios Design-DNA.
 * Zeigt Profilbild mit Statusleuchte, den Benutzernamen (und dezent die E-Mail)
 * sowie umschaltbare Präsenz (Online, Abwesend, Unsichtbar).
 */
export function ProfileDropdown({
  user,
  items,
  placement = 'bottom-right',
  className = '',
  triggerAriaLabel,
  avatarSize = 'sm',
  triggerVariant = 'avatar',
  status,
  onStatusChange,
}: ProfileDropdownProps) {
  const { t } = useTranslation()
  const ausloeserName = triggerAriaLabel ?? t('common.openUserMenu')
  const anzeigename = user?.username || t('messenger.userFallback')
  const statusText = status ? t(`social.status.${status}`) : ''

  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const menueRef = useRef<HTMLDivElement>(null)
  const lage = useAnkerLage(isOpen, containerRef, menueRef, {
    seite: placement.startsWith('top') ? 'oben' : 'unten',
    ausrichtung: placement.endsWith('right') ? 'ende' : 'start',
  })

  const renderStatusDot = (size: 'xs' | 'sm' | 'md') => {
    if (!status) return null
    const sizeClasses = size === 'xs' ? 'w-1.5 h-1.5' : size === 'sm' ? 'w-2 h-2' : 'w-2.5 h-2.5'
    const colorClasses =
      status === 'online'
        ? 'bg-status-success shadow-[0_0_6px_rgba(16,185,129,0.6)]'
        : status === 'away'
        ? 'bg-status-warning shadow-[0_0_6px_rgba(245,158,11,0.6)]'
        : 'bg-on-surface-variant/50'

    return (
      <span
        role="img"
        className={`absolute bottom-0 right-0 inline-block rounded-full ring-2 ring-surface ${sizeClasses} ${colorClasses}`}
        aria-label={statusText}
      />
    )
  }

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const ziel = e.target as Node
      // Das Menü hängt per Portal an body, also außerhalb von containerRef.
      if (!containerRef.current?.contains(ziel) && !menueRef.current?.contains(ziel)) {
        setIsOpen(false)
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleKeyDown)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen])

  return (
    <div
      className={`relative inline-block text-left shrink-0 ${triggerVariant === 'full' ? 'w-full flex-1 min-w-0' : ''} ${className}`}
      ref={containerRef}
    >
      {/* Trigger Button */}
      {triggerVariant === 'avatar' ? (
        <button
          type="button"
          onClick={() => setIsOpen((offen) => !offen)}
          aria-expanded={isOpen}
          aria-haspopup="menu"
          aria-label={ausloeserName}
          className="flex items-center gap-2 rounded-xl p-1 transition-all hover:bg-surface-container-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <div className="relative inline-flex shrink-0">
            <Avatar
              src={user?.avatar_url}
              name={user?.username}
              size={avatarSize}
            />
            {renderStatusDot(avatarSize)}
          </div>
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setIsOpen((offen) => !offen)}
          aria-expanded={isOpen}
          aria-haspopup="menu"
          aria-label={ausloeserName}
          className="flex min-w-0 w-full items-center gap-2.5 rounded-xl p-1.5 text-left transition-all hover:bg-surface-container-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <div className="relative inline-flex shrink-0">
            <Avatar
              src={user?.avatar_url}
              name={user?.username}
              size={avatarSize}
            />
            {renderStatusDot(avatarSize)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <p className="truncate text-xs font-semibold text-on-surface">
                {anzeigename}
              </p>
            </div>
            {user?.email && (
              <p className="truncate text-label-sm text-on-surface-variant font-mono">
                {user.email}
              </p>
            )}
          </div>
        </button>
      )}

      {/* Dropdown Popup */}
      {isOpen && createPortal(
        <div
          ref={menueRef}
          role="menu"
          style={lage}
          className="w-64 max-w-[calc(100vw-1rem)] max-h-[calc(100dvh-1rem)] overflow-y-auto rounded-2xl border border-outline-variant bg-surface-container-high shadow-2xl animate-fade-in"
        >
          {/* Header mit Avatar & Benutzername & Statusumschalter */}
          <div className="border-b border-outline-variant/30 p-3.5 bg-surface-container">
            <div className="flex items-center gap-3">
              <div className="relative inline-flex shrink-0">
                <Avatar
                  src={user?.avatar_url}
                  name={user?.username}
                  size="md"
                />
                {renderStatusDot('md')}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-on-surface">
                  {anzeigename}
                </p>
                {user?.email && (
                  <p className="truncate text-xs text-on-surface-variant font-mono">
                    {user.email}
                  </p>
                )}
              </div>
            </div>

            {/* Status-Umschalter unten links im Profil */}
            {onStatusChange && (
              <div className="mt-3 pt-2.5 border-t border-outline-variant/20">
                <div className="flex items-center justify-between gap-1 mb-1.5">
                  <span className="text-label-sm uppercase font-bold text-on-surface-variant tracking-wider">
                    {t('social.status.titel')}
                  </span>
                  <span className="text-label-sm text-primary capitalize font-medium">
                    {statusText}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-1 bg-surface-container-high p-1 rounded-xl border border-outline-variant/30">
                  <button
                    type="button"
                    onClick={() => onStatusChange('online')}
                    aria-pressed={status === 'online'}
                    className={`py-1 px-1.5 rounded-lg text-label-sm font-semibold flex items-center justify-center gap-1 transition-all ${
                      status === 'online'
                        ? 'bg-status-success/20 text-status-success border border-status-success/40 shadow-sm'
                        : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-status-success shrink-0" />
                    {t('social.status.online')}
                  </button>
                  <button
                    type="button"
                    onClick={() => onStatusChange('away')}
                    aria-pressed={status === 'away'}
                    className={`py-1 px-1.5 rounded-lg text-label-sm font-semibold flex items-center justify-center gap-1 transition-all ${
                      status === 'away'
                        ? 'bg-status-warning/20 text-status-warning border border-status-warning/40 shadow-sm'
                        : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-status-warning shrink-0" />
                    {t('social.status.away')}
                  </button>
                  <button
                    type="button"
                    onClick={() => onStatusChange('invisible')}
                    aria-pressed={status === 'invisible'}
                    className={`py-1 px-1.5 rounded-lg text-label-sm font-semibold flex items-center justify-center gap-1 transition-all ${
                      status === 'invisible'
                        ? 'bg-surface-container-highest text-on-surface border border-outline-variant/60 shadow-sm'
                        : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-on-surface-variant/50 shrink-0" />
                    {t('social.status.invisible')}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Menü-Aktionen */}
          <div className="py-1">
            {items.map((item, idx) => {
              const isDanger = item.tone === 'danger'
              const isFirstDanger = isDanger && items[idx - 1]?.tone !== 'danger'

              return (
                <React.Fragment key={item.key}>
                  {isFirstDanger && (
                    <div className="border-t border-outline-variant/30 my-1" />
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setIsOpen(false)
                      item.onClick()
                    }}
                    className={`flex w-full items-center gap-2.5 px-3.5 py-2.5 text-xs font-medium transition-colors ${
                      isDanger
                        ? 'text-status-destructive hover:bg-error-container/20'
                        : 'text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    {item.icon && (
                      <span
                        className={`shrink-0 ${isDanger ? 'text-status-destructive' : 'text-primary'}`}
                        aria-hidden="true"
                      >
                        {item.icon}
                      </span>
                    )}
                    <span className="truncate">{item.label}</span>
                  </button>
                </React.Fragment>
              )
            })}
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
