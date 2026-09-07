import React, { useState, useRef, useEffect, useCallback } from 'react'
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
  triggerAriaLabel = 'Benutzermenü öffnen',
  avatarSize = 'sm',
  triggerVariant = 'avatar',
  status,
  onStatusChange,
}: ProfileDropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const initialPlacement = placement.startsWith('top') ? 'top' : 'bottom'
  const initialAlign = placement.endsWith('right') ? 'right' : 'left'
  const [computedPlacement, setComputedPlacement] = useState<'top' | 'bottom'>(initialPlacement)
  const [computedAlign, setComputedAlign] = useState<'left' | 'right'>(initialAlign)
  const [computedMaxHeight, setComputedMaxHeight] = useState<number>(480)

  const updatePosition = useCallback(() => {
    if (!containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    // Test environments without layout engine (jsdom)
    if (rect.width === 0 && rect.height === 0 && rect.top === 0 && rect.bottom === 0) {
      setComputedPlacement(initialPlacement)
      setComputedAlign(initialAlign)
      setComputedMaxHeight(480)
      return
    }

    const spaceBelow = window.innerHeight - rect.bottom
    const spaceAbove = rect.top
    const totalSpaceToRightFromLeft = window.innerWidth - rect.left
    const totalSpaceToLeftFromRight = rect.right

    // Vertical collision detection (dropdown height ~300px)
    let chosenPlacement: 'top' | 'bottom' = 'bottom'
    if (placement.startsWith('top')) {
      if (spaceAbove >= 200 || spaceAbove >= spaceBelow) {
        chosenPlacement = 'top'
      } else {
        chosenPlacement = 'bottom'
      }
    } else {
      if (spaceBelow >= 200 || spaceBelow >= spaceAbove) {
        chosenPlacement = 'bottom'
      } else {
        chosenPlacement = 'top'
      }
    }
    setComputedPlacement(chosenPlacement)

    const availableHeight = chosenPlacement === 'top' ? spaceAbove - 16 : spaceBelow - 16
    setComputedMaxHeight(Math.max(120, Math.min(Math.floor(availableHeight), window.innerHeight - 32)))

    // Horizontal collision detection (dropdown width ~260px)
    if (placement.endsWith('right')) {
      // Default: align right edge of dropdown with right edge of trigger (right-0).
      // Needs space to the left of trigger's right edge.
      if (totalSpaceToLeftFromRight >= 260 || totalSpaceToLeftFromRight >= totalSpaceToRightFromLeft) {
        setComputedAlign('right')
      } else {
        setComputedAlign('left')
      }
    } else {
      // Default: align left edge of dropdown with left edge of trigger (left-0).
      // Needs space to the right of trigger's left edge.
      if (totalSpaceToRightFromLeft >= 260 || totalSpaceToRightFromLeft >= totalSpaceToLeftFromRight) {
        setComputedAlign('left')
      } else {
        setComputedAlign('right')
      }
    }
  }, [placement, initialPlacement, initialAlign])

  const toggleDropdown = () => {
    if (!isOpen) {
      updatePosition()
    }
    setIsOpen((prev) => !prev)
  }

  const renderStatusDot = (size: 'xs' | 'sm' | 'md') => {
    if (!status) return null
    const sizeClasses = size === 'xs' ? 'w-1.5 h-1.5' : size === 'sm' ? 'w-2 h-2' : 'w-2.5 h-2.5'
    const colorClasses =
      status === 'online'
        ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
        : status === 'away'
        ? 'bg-amber-500 shadow-[0_0_6px_rgba(245,158,11,0.6)]'
        : 'bg-on-surface-variant/50'

    return (
      <span
        className={`absolute bottom-0 right-0 inline-block rounded-full ring-2 ring-surface ${sizeClasses} ${colorClasses}`}
        aria-label={status === 'online' ? 'Online' : status === 'away' ? 'Abwesend' : 'Unsichtbar'}
      />
    )
  }

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
      updatePosition()
      window.addEventListener('resize', updatePosition)
      window.addEventListener('scroll', updatePosition, true)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [isOpen, updatePosition])

  const placementClass = computedPlacement === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'
  const alignClass = computedAlign === 'right' ? 'right-0 left-auto' : 'left-0 right-auto'

  return (
    <div
      className={`relative inline-block text-left shrink-0 ${triggerVariant === 'full' ? 'w-full flex-1 min-w-0' : ''} ${className}`}
      ref={containerRef}
    >
      {/* Trigger Button */}
      {triggerVariant === 'avatar' ? (
        <button
          type="button"
          onClick={toggleDropdown}
          aria-expanded={isOpen}
          aria-haspopup="menu"
          aria-label={triggerAriaLabel}
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
          onClick={toggleDropdown}
          aria-expanded={isOpen}
          aria-haspopup="menu"
          aria-label={triggerAriaLabel}
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
                {user?.username || 'Benutzer'}
              </p>
            </div>
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
          style={{ maxHeight: `${computedMaxHeight}px` }}
          className={`absolute w-64 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-2xl border border-outline-variant bg-surface-container-high shadow-2xl z-50 animate-fade-in ${placementClass} ${alignClass}`}
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
                  {user?.username || 'Benutzer'}
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
                  <span className="text-[10px] uppercase font-bold text-on-surface-variant tracking-wider">
                    Status
                  </span>
                  <span className="text-[10px] text-primary capitalize font-medium">
                    {status === 'invisible' ? 'Unsichtbar' : status === 'away' ? 'Abwesend' : 'Online'}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-1 bg-surface-container-high p-1 rounded-xl border border-outline-variant/30">
                  <button
                    type="button"
                    onClick={() => onStatusChange('online')}
                    className={`py-1 px-1.5 rounded-lg text-[10px] font-semibold flex items-center justify-center gap-1 transition-all ${
                      status === 'online'
                        ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 shadow-sm'
                        : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
                    Online
                  </button>
                  <button
                    type="button"
                    onClick={() => onStatusChange('away')}
                    className={`py-1 px-1.5 rounded-lg text-[10px] font-semibold flex items-center justify-center gap-1 transition-all ${
                      status === 'away'
                        ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40 shadow-sm'
                        : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
                    Abwesend
                  </button>
                  <button
                    type="button"
                    onClick={() => onStatusChange('invisible')}
                    className={`py-1 px-1.5 rounded-lg text-[10px] font-semibold flex items-center justify-center gap-1 transition-all ${
                      status === 'invisible'
                        ? 'bg-surface-container-highest text-on-surface border border-outline-variant/60 shadow-sm'
                        : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-on-surface-variant/50 shrink-0" />
                    Unsichtbar
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
