import { forwardRef, useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Search } from 'lucide-react'
import { cx } from '@/utils/classNames'

// Unter dieser Hoehe lohnt sich "nach unten" nicht mehr — dann sieht man eine
// Zeile und scrollt. Darueber bleibt es unten, auch wenn oben mehr Platz waere.
const MIN_MENUE_HOEHE = 180
// Obergrenze, damit eine lange Liste (27 native Blueprints) das Fenster nicht
// von oben bis unten ausfuellt.
const MAX_MENUE_HOEHE = 320

export interface DropdownOption {
  value: string
  label: string
  hint?: string
  icon?: ReactNode
  disabled?: boolean
}

interface DropdownProps {
  id?: string
  value: string | null
  onChange: (value: string) => void
  options: DropdownOption[]
  placeholder?: string
  searchable?: boolean
  searchPlaceholder?: string
  emptyText?: string
  disabled?: boolean
  className?: string
  buttonClassName?: string
  align?: 'start' | 'end'
  'aria-label'?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
  'data-testid'?: string
}

export const Dropdown = forwardRef<HTMLDivElement, DropdownProps>(
  (
    {
      value,
      id,
      onChange,
      options,
      placeholder = 'Auswählen',
      searchable = false,
      searchPlaceholder = 'Suchen …',
      emptyText = 'Keine Einträge',
      disabled = false,
      className = '',
      buttonClassName = '',
      align = 'start',
      'aria-label': ariaLabel,
      'aria-describedby': ariaDescribedBy,
      'aria-invalid': ariaInvalid,
      'data-testid': testId,
    },
    ref,
  ) => {
    const [open, setOpen] = useState(false)
    const [query, setQuery] = useState('')
    const [menuStyle, setMenuStyle] = useState<CSSProperties | null>(null)
    const rootRef = useRef<HTMLDivElement | null>(null)
    const menuRef = useRef<HTMLDivElement | null>(null)
    const listId = useId()
    const safeOptions = Array.isArray(options) ? options : []
    const selected = safeOptions.find((option) => option.value === value)

    const filteredOptions = searchable && query.trim()
      ? safeOptions.filter((option) =>
          option.label.toLowerCase().includes(query.toLowerCase().trim()) ||
          (option.hint && option.hint.toLowerCase().includes(query.toLowerCase().trim())),
        )
      : safeOptions

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
      // Die Optionen liegen per Portal an `document.body` und damit außerhalb
      // von `rootRef`. Deshalb hängen die Tasten am Dokument und die Liste wird
      // über `menuRef` eingesammelt — dasselbe Vorgehen wie in
      // Singra/UI/ActionMenu.tsx.
      const optionenSammeln = (): HTMLButtonElement[] =>
        menuRef.current
          ? Array.from<HTMLButtonElement>(menuRef.current.querySelectorAll('[role="option"]:not(:disabled)'))
          : []
      const onKey = (event: KeyboardEvent) => {
        if (event.key === 'Escape') {
          event.preventDefault()
          setOpen(false)
          // Ohne diese Zeile verliert man nach Escape seinen Platz im Formular.
          rootRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
          return
        }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
        const optionen = optionenSammeln()
        if (!optionen.length) return
        event.preventDefault()
        const aktuell = optionen.indexOf(document.activeElement as HTMLButtonElement)
        const naechste =
          event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? optionen.length - 1
              : event.key === 'ArrowUp'
                ? (aktuell - 1 + optionen.length) % optionen.length
                : (aktuell + 1) % optionen.length
        optionen[naechste].focus()
      }
      // Beim Öffnen wandert der Fokus in die Liste, auf die gewählte Option.
      // Erst im nächsten Frame — vorher steht das Menü noch nicht im DOM, weil
      // die Position im Effekt darunter gemessen wird.
      const fokusFrame = window.requestAnimationFrame(() => {
        const optionen = optionenSammeln()
        const gewaehlt = optionen.find((option) => option.getAttribute('aria-selected') === 'true')
        ;(gewaehlt ?? optionen[0])?.focus()
      })
      document.addEventListener('mousedown', onClick)
      document.addEventListener('keydown', onKey)
      return () => {
        document.removeEventListener('mousedown', onClick)
        document.removeEventListener('keydown', onKey)
        window.cancelAnimationFrame(fokusFrame)
      }
    }, [open])

    useEffect(() => {
      if (!open) return
      const updatePosition = () => {
        const rect = rootRef.current?.getBoundingClientRect()
        if (!rect) return
        // Nach unten ist die Regel, nicht die Ausnahme. Ein natives <select>
        // entscheidet das selbst und klappt in einem mittig stehenden Dialog
        // regelmaessig nach oben — das liest sich wie ein Fehler, obwohl es
        // Browserverhalten ist. Hier liegt die Entscheidung bei uns.
        //
        // Nach oben wird nur ausgewichen, wenn unten wirklich kein Platz ist
        // **und** oben mehr Platz waere. Und die Hoehe wird auf den tatsaechlich
        // verfuegbaren Raum begrenzt: eine Liste, die unten aus dem Fenster
        // laeuft, ist genauso unbrauchbar wie eine, die nach oben aufklappt.
        const RAND = 8
        const platzUnten = window.innerHeight - rect.bottom - RAND * 2
        const platzOben = rect.top - RAND * 2
        const nachOben = platzUnten < MIN_MENUE_HOEHE && platzOben > platzUnten
        const maxHeight = Math.max(
          MIN_MENUE_HOEHE,
          Math.min(MAX_MENUE_HOEHE, nachOben ? platzOben : platzUnten),
        )
        setMenuStyle({
          position: 'fixed',
          ...(nachOben
            ? { bottom: Math.max(RAND, window.innerHeight - rect.top + RAND) }
            : { top: rect.bottom + RAND }),
          left: align === 'end' ? undefined : rect.left,
          right: align === 'end' ? Math.max(0, window.innerWidth - rect.right) : undefined,
          width: rect.width,
          maxHeight,
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
      <div ref={ref} className={cx('relative min-w-0 w-full max-w-full', className)}>
        <div ref={rootRef}>
          <button
            id={id}
            type="button"
            disabled={disabled}
            aria-label={ariaLabel}
            aria-describedby={ariaDescribedBy}
            aria-invalid={ariaInvalid}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-controls={listId}
            data-testid={testId}
            onClick={() => !disabled && setOpen((current) => !current)}
            className={cx(
              'msm-input flex h-10 min-w-0 max-w-full items-center justify-between gap-2 px-3 text-left disabled:cursor-not-allowed disabled:opacity-50',
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
                  // Merkmal fuer Aussenklick-Waechter. Das Menue haengt per
                  // Portal an `document.body` und liegt damit ausserhalb jedes
                  // Elements, das es geoeffnet hat. Ein Popover, das sich bei
                  // einem Klick "ausserhalb" schliesst, haelt eine Option
                  // deshalb faelschlich fuer draussen — und schliesst sich weg,
                  // bevor die Auswahl ueberhaupt ankommt. Wer so einen Waechter
                  // baut, prueft zusaetzlich auf
                  // `closest('[data-msm-dropdown-menu]')`.
                  data-msm-dropdown-menu=""
                  className="flex flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-high shadow-panel"
                >
                  {searchable && (
                    <div className="border-b border-outline-variant/60 p-1.5">
                      <div className="relative">
                        <Search
                          className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-on-surface-variant/50"
                          aria-hidden="true"
                        />
                        <input
                          type="text"
                          autoFocus
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          placeholder={searchPlaceholder}
                          className="msm-input w-full py-1.5 pl-8 pr-2 text-xs"
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => e.stopPropagation()}
                        />
                      </div>
                    </div>
                  )}
                  <ul id={listId} role="listbox" className="min-h-0 flex-1 overflow-y-auto p-1">
                    {filteredOptions.length === 0 ? (
                      <li className="px-3 py-4 text-center text-xs text-on-surface-variant/60">
                        {emptyText}
                      </li>
                    ) : (
                      filteredOptions.map((option) => {
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
                                'flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-40',
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
                      })
                    )}
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
