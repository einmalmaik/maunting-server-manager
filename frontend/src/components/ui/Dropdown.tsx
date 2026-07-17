import { forwardRef, useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'
import { cx } from '@/utils/classNames'

export interface DropdownOption {
  value: string
  label: string
  hint?: string
  icon?: ReactNode
  disabled?: boolean
}

interface DropdownProps {
  value: string | null
  onChange: (value: string) => void
  options: DropdownOption[]
  placeholder?: string
  disabled?: boolean
  className?: string
  buttonClassName?: string
  align?: 'start' | 'end'
  'aria-label'?: string
  'data-testid'?: string
}

export const Dropdown = forwardRef<HTMLDivElement, DropdownProps>(
  (
    {
      value,
      onChange,
      options,
      placeholder = 'Auswählen',
      disabled = false,
      className = '',
      buttonClassName = '',
      align = 'start',
      'aria-label': ariaLabel,
      'data-testid': testId,
    },
    ref,
  ) => {
    const [open, setOpen] = useState(false)
    const [menuStyle, setMenuStyle] = useState<CSSProperties | null>(null)
    const rootRef = useRef<HTMLDivElement | null>(null)
    const menuRef = useRef<HTMLDivElement | null>(null)
    const listId = useId()
    const selected = options.find((option) => option.value === value)

    useEffect(() => {
      if (!open) return
      const onClick = (event: MouseEvent) => {
        const target = event.target as Node
        if (
          rootRef.current &&
          !rootRef.current.contains(target) &&
          menuRef.current &&
          !menuRef.current.contains(target)
        ) {
          setOpen(false)
        }
      }
      const onKey = (event: KeyboardEvent) => {
        if (event.key === 'Escape') setOpen(false)
      }
      document.addEventListener('mousedown', onClick)
      document.addEventListener('keydown', onKey)
      return () => {
        document.removeEventListener('mousedown', onClick)
        document.removeEventListener('keydown', onKey)
      }
    }, [open])

    useEffect(() => {
      if (!open) return
      const updatePosition = () => {
        const rect = rootRef.current?.getBoundingClientRect()
        if (!rect) return
        setMenuStyle({
          position: 'fixed',
          top: rect.bottom + 8,
          left: align === 'end' ? undefined : rect.left,
          right: align === 'end' ? Math.max(0, window.innerWidth - rect.right) : undefined,
          width: rect.width,
          zIndex: 100,
        })
      }
      updatePosition()
      window.addEventListener('resize', updatePosition)
      window.addEventListener('scroll', updatePosition, true)
      return () => {
        window.removeEventListener('resize', updatePosition)
        window.removeEventListener('scroll', updatePosition, true)
      }
    }, [align, open])

    return (
      <div ref={ref} className={cx('relative', className)}>
        <div ref={rootRef}>
          <button
            type="button"
            disabled={disabled}
            aria-label={ariaLabel}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-controls={listId}
            data-testid={testId}
            onClick={() => !disabled && setOpen((current) => !current)}
            className={cx(
              'msm-input flex h-10 items-center justify-between gap-2 px-3 text-left disabled:cursor-not-allowed disabled:opacity-50',
              open && 'border-primary ring-2 ring-primary/25',
              buttonClassName,
            )}
          >
            <span className={cx('min-w-0 flex-1 truncate', !selected && 'text-on-surface-variant')}>
              {selected ? (
                <span className="inline-flex min-w-0 items-center gap-2">
                  {selected.icon}
                  <span className="truncate">{selected.label}</span>
                </span>
              ) : (
                placeholder
              )}
            </span>
            <ChevronDown
              className={cx('h-4 w-4 shrink-0 text-on-surface-variant transition-transform', open && 'rotate-180')}
              aria-hidden="true"
            />
          </button>

          {open && menuStyle
            ? createPortal(
                <div
                  ref={menuRef}
                  style={menuStyle}
                  className="overflow-hidden rounded-lg border border-outline-variant bg-surface-container-high shadow-panel"
                >
                  <ul id={listId} role="listbox" className="max-h-64 overflow-y-auto p-1">
                    {options.map((option) => {
                      const active = option.value === value
                      return (
                        <li key={option.value}>
                          <button
                            type="button"
                            role="option"
                            aria-selected={active}
                            disabled={option.disabled}
                            onClick={() => {
                              onChange(option.value)
                              setOpen(false)
                            }}
                            className={cx(
                              'flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40',
                              active
                                ? 'bg-primary/10 text-primary'
                                : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
                            )}
                          >
                            {option.icon}
                            <span className="min-w-0 flex-1 truncate">
                              {option.label}
                              {option.hint && (
                                <span className="ml-2 text-xs text-on-surface-variant/60">{option.hint}</span>
                              )}
                            </span>
                            {active && <Check className="h-4 w-4 shrink-0 text-secondary" aria-hidden="true" />}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                </div>,
                document.body,
              )
            : null}
        </div>
      </div>
    )
  },
)

Dropdown.displayName = 'Dropdown'