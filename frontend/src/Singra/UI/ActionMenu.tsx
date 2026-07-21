import {
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown } from 'lucide-react'
import { cx } from '@/utils/classNames'

export interface ActionMenuItem {
  key: string
  label: string
  icon?: ReactNode
  disabled?: boolean
  destructive?: boolean
  separatorBefore?: boolean
  onSelect: () => void
}

interface ActionMenuProps {
  label: string
  items: ActionMenuItem[]
  icon?: ReactNode
  disabled?: boolean
  align?: 'start' | 'end'
  compact?: boolean
}

/** Compact action menu for dense operational toolbars. */
export function ActionMenu({
  label,
  items,
  icon,
  disabled = false,
  align = 'start',
  compact = false,
}: ActionMenuProps) {
  const [open, setOpen] = useState(false)
  const [style, setStyle] = useState<CSSProperties | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const initialFocusRef = useRef<'first' | 'last'>('first')
  const menuId = useId()

  useEffect(() => {
    if (!open) return
    const update = () => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      setStyle({
        position: 'fixed',
        top: rect.bottom + 6,
        left: align === 'start' ? rect.left : undefined,
        right: align === 'end' ? Math.max(8, window.innerWidth - rect.right) : undefined,
        minWidth: Math.max(190, rect.width),
        zIndex: 110,
      })
    }
    const dismiss = (event: MouseEvent) => {
      const target = event.target as Node
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setOpen(false)
        triggerRef.current?.focus()
        return
      }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const menuItems: HTMLButtonElement[] = menuRef.current
        ? Array.from<HTMLButtonElement>(menuRef.current.querySelectorAll('[role="menuitem"]:not(:disabled)'))
        : []
      if (!menuItems.length) return
      event.preventDefault()
      const currentIndex = menuItems.indexOf(document.activeElement as HTMLButtonElement)
      const nextIndex = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? menuItems.length - 1
          : event.key === 'ArrowUp'
            ? (currentIndex - 1 + menuItems.length) % menuItems.length
            : (currentIndex + 1) % menuItems.length
      menuItems[nextIndex].focus()
    }
    update()
    const focusFrame = window.requestAnimationFrame(() => {
      const menuItems: HTMLButtonElement[] = menuRef.current
        ? Array.from<HTMLButtonElement>(menuRef.current.querySelectorAll('[role="menuitem"]:not(:disabled)'))
        : []
      const initialItem = initialFocusRef.current === 'last' ? menuItems[menuItems.length - 1] : menuItems[0]
      initialItem?.focus()
    })
    document.addEventListener('mousedown', dismiss)
    document.addEventListener('keydown', onKeyDown)
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      document.removeEventListener('mousedown', dismiss)
      document.removeEventListener('keydown', onKeyDown)
      window.cancelAnimationFrame(focusFrame)
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [align, open])

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-haspopup="menu"
        aria-controls={menuId}
        aria-expanded={open}
        onClick={() => {
          initialFocusRef.current = 'first'
          setOpen((value) => !value)
        }}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
          event.preventDefault()
          initialFocusRef.current = event.key === 'ArrowUp' ? 'last' : 'first'
          setOpen(true)
        }}
        className={cx(
          'msm-btn-secondary inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-45',
          compact ? 'h-11 px-3 text-xs sm:h-8 sm:px-2.5' : 'h-11 px-3 text-sm sm:h-9',
        )}
      >
        {icon}
        <span>{label}</span>
        <ChevronDown className={cx('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} aria-hidden />
      </button>
      {open && style
        ? createPortal(
            <div
              ref={menuRef}
              id={menuId}
              role="menu"
              style={style}
              className="rounded-lg border border-outline-variant bg-surface-container-high/98 p-1.5 shadow-panel backdrop-blur-xl"
            >
              {items.map((item) => (
                <div key={item.key} className={item.separatorBefore ? 'mt-1 border-t border-outline-variant pt-1' : ''}>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={item.disabled}
                    onClick={() => {
                      item.onSelect()
                      setOpen(false)
                    }}
                    className={cx(
                      'flex min-h-11 w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-9',
                      item.destructive
                        ? 'text-status-error hover:bg-status-error/10'
                        : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
                    )}
                  >
                    <span className="flex h-4 w-4 shrink-0 items-center justify-center" aria-hidden>
                      {item.icon}
                    </span>
                    <span>{item.label}</span>
                  </button>
                </div>
              ))}
            </div>,
            document.body,
          )
        : null}
    </>
  )
}
