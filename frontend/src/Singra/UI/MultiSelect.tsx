import { forwardRef, useEffect, useId, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'

export interface MultiSelectOption {
  value: string
  label: string
  disabled?: boolean
}

interface MultiSelectProps {
  options: MultiSelectOption[]
  values: string[]
  onChange: (values: string[]) => void
  placeholder: string
  disabled?: boolean
  className?: string
  'aria-label': string
}

/**
 * Rendert eine kompakte, zugängliche Mehrfachauswahl ohne zusätzliche
 * UI-Dependency. Die Auswahlreihenfolge folgt stabil der Optionsliste, damit
 * identische Rollen-Sets keine unnötigen API-Updates durch Sortierdrift erzeugen.
 */
export const MultiSelect = forwardRef<HTMLDivElement, MultiSelectProps>(
  function MultiSelect(
    {
      options,
      values,
      onChange,
      placeholder,
      disabled = false,
      className = '',
      'aria-label': ariaLabel,
    },
    forwardedRef,
  ) {
    const [open, setOpen] = useState(false)
    const rootRef = useRef<HTMLDivElement | null>(null)
    const listboxId = useId()
    const selected = useMemo(() => new Set(values), [values])
    const selectedLabels = options
      .filter((option) => selected.has(option.value))
      .map((option) => option.label)

    /** Verknüpft internen und optionalen externen Ref mit demselben Element. */
    const setRootRef = (node: HTMLDivElement | null) => {
      rootRef.current = node
      if (typeof forwardedRef === 'function') forwardedRef(node)
      else if (forwardedRef) forwardedRef.current = node
    }

    /** Schließt das Menü bei Klick außerhalb oder Escape und hält Fokus lokal. */
    useEffect(() => {
      if (!open) return undefined

      const handlePointerDown = (event: PointerEvent) => {
        if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
      }
      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key === 'Escape') {
          setOpen(false)
          rootRef.current?.querySelector<HTMLButtonElement>('[data-multiselect-trigger]')?.focus()
        }
      }

      document.addEventListener('pointerdown', handlePointerDown)
      document.addEventListener('keydown', handleKeyDown)
      return () => {
        document.removeEventListener('pointerdown', handlePointerDown)
        document.removeEventListener('keydown', handleKeyDown)
      }
    }, [open])

    /** Aktiviert oder entfernt genau eine Option und gibt ein stabiles Set aus. */
    const toggle = (value: string) => {
      const next = new Set(values)
      if (next.has(value)) next.delete(value)
      else next.add(value)
      onChange(options.filter((option) => next.has(option.value)).map((option) => option.value))
    }

    /** Bewegt den Fokus innerhalb der Optionsliste mit Pfeil-, Home- und End-Tasten. */
    const moveOptionFocus = (event: React.KeyboardEvent<HTMLButtonElement>) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      event.preventDefault()
      const buttons = Array.from(
        rootRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? [],
      )
      if (buttons.length === 0) return
      const current = buttons.indexOf(event.currentTarget)
      const target = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? buttons.length - 1
          : event.key === 'ArrowDown'
            ? (current + 1) % buttons.length
            : (current - 1 + buttons.length) % buttons.length
      buttons[target]?.focus()
    }

    const summary = selectedLabels.length === 0
      ? placeholder
      : selectedLabels.length <= 2
        ? selectedLabels.join(', ')
        : `${selectedLabels[0]} +${selectedLabels.length - 1}`

    return (
      <div ref={setRootRef} className={`relative min-w-0 ${className}`}>
        <button
          type="button"
          data-multiselect-trigger
          aria-label={ariaLabel}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listboxId}
          disabled={disabled}
          onClick={() => setOpen((current) => !current)}
          className="flex min-h-10 w-full min-w-0 items-center justify-between gap-2 rounded-lg border border-outline-variant/60 bg-surface-container-high/45 px-3 py-2 text-left text-sm text-on-surface transition-colors hover:border-primary/40 hover:bg-surface-container-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className={`truncate ${selectedLabels.length === 0 ? 'text-on-surface-variant' : ''}`}>
            {summary}
          </span>
          <span className="flex shrink-0 items-center gap-1.5">
            {selectedLabels.length > 0 && (
              <span className="rounded-full border border-primary/25 bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] text-primary">
                {selectedLabels.length}
              </span>
            )}
            <ChevronDown
              aria-hidden="true"
              className={`h-4 w-4 text-on-surface-variant transition-transform ${open ? 'rotate-180' : ''}`}
            />
          </span>
        </button>

        {open && (
          <div
            id={listboxId}
            role="listbox"
            aria-label={ariaLabel}
            aria-multiselectable="true"
            className="absolute left-0 right-0 z-50 mt-2 max-h-64 overflow-y-auto rounded-xl border border-outline-variant/70 bg-surface-container-high/95 p-1.5 shadow-2xl backdrop-blur-xl"
          >
            {options.length === 0 ? (
              <p className="px-3 py-4 text-center text-xs text-on-surface-variant">
                Keine Rollen verfügbar
              </p>
            ) : (
              options.map((option) => {
                const isSelected = selected.has(option.value)
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    disabled={option.disabled}
                    onKeyDown={moveOptionFocus}
                    onClick={() => toggle(option.value)}
                    className={`flex min-h-10 w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/70 disabled:cursor-not-allowed disabled:opacity-45 ${
                      isSelected
                        ? 'bg-primary/10 text-primary'
                        : 'text-on-surface hover:bg-surface-container-highest'
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className={`grid h-4 w-4 shrink-0 place-items-center rounded border ${
                        isSelected
                          ? 'border-primary bg-primary text-on-primary'
                          : 'border-outline-variant bg-surface-container'
                      }`}
                    >
                      {isSelected && <Check className="h-3 w-3 stroke-[3]" />}
                    </span>
                    <span className="min-w-0 break-words">{option.label}</span>
                  </button>
                )
              })
            )}
          </div>
        )}
      </div>
    )
  },
)
