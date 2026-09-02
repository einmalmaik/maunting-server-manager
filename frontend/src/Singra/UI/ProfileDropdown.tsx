import React, { useState, useRef, useEffect } from 'react'
import { Avatar } from './Avatar'

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
}

const placementClasses = {
  'bottom-right': 'top-full right-0 mt-2',
  'bottom-left': 'top-full left-0 mt-2',
  'top-right': 'bottom-full right-0 mb-2',
  'top-left': 'bottom-full left-0 mb-2',
}

/**
 * Barrierefreies, reduziertes Profil-Dropdown der MauntingStudios Design-DNA.
 * Zeigt ausschließlich das Profilbild, den Benutzernamen (und dezent die E-Mail) ohne Rollennamen.
 */
export function ProfileDropdown({
  user,
  items,
  placement = 'bottom-right',
  className = '',
  triggerAriaLabel = 'Benutzermenü öffnen',
  avatarSize = 'sm',
  triggerVariant = 'avatar',
}: ProfileDropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
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
      className={`relative inline-block text-left ${triggerVariant === 'full' ? 'w-full flex-1 min-w-0' : ''} ${className}`}
      ref={containerRef}
    >
      {/* Trigger Button */}
      {triggerVariant === 'avatar' ? (
        <button
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          aria-expanded={isOpen}
          aria-haspopup="menu"
          aria-label={triggerAriaLabel}
          className="flex items-center gap-2 rounded-xl p-1 transition-all hover:bg-surface-container-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <Avatar
            src={user?.avatar_url}
            name={user?.username}
            size={avatarSize}
          />
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          aria-expanded={isOpen}
          aria-haspopup="menu"
          aria-label={triggerAriaLabel}
          className="flex min-w-0 w-full items-center gap-2.5 rounded-xl p-1.5 text-left transition-all hover:bg-surface-container-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <Avatar
            src={user?.avatar_url}
            name={user?.username}
            size={avatarSize}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-semibold text-on-surface">
              {user?.username || 'Benutzer'}
            </p>
            {user?.email && (
              <p className="truncate text-[11px] text-on-surface-variant font-mono">
                {user.email}
              </p>
            )}
          </div>
        </button>
      )}

      {/* Dropdown Popup */}
      {isOpen && (
        <div
          role="menu"
          className={`absolute w-60 overflow-hidden rounded-2xl border border-outline-variant bg-surface-container-high shadow-2xl z-50 animate-fade-in ${placementClasses[placement]}`}
        >
          {/* Header mit Avatar & Benutzername (ohne Rollennamen) */}
          <div className="border-b border-outline-variant/30 p-3.5 bg-surface-container">
            <div className="flex items-center gap-3">
              <Avatar
                src={user?.avatar_url}
                name={user?.username}
                size="md"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-on-surface">
                  {user?.username || 'Benutzer'}
                </p>
                {user?.email && (
                  <p className="truncate text-xs text-on-surface-variant font-mono">
                    {user.email}
                  </p>
                )}
              </div>
            </div>
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
                        ? 'text-status-error hover:bg-error-container/20'
                        : 'text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    {item.icon && (
                      <span
                        className={`shrink-0 ${isDanger ? 'text-status-error' : 'text-primary'}`}
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
        </div>
      )}
    </div>
  )
}
