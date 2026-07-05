import { forwardRef, type InputHTMLAttributes } from 'react'
import { Minus, Plus } from 'lucide-react'
import { cx } from '@/utils/classNames'

interface NumberStepperProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'value' | 'onChange' | 'size'> {
  value: string | number
  onValueChange: (value: string) => void
  min?: number
  max?: number
  step?: number
  size?: 'sm' | 'md'
}

function toNumber(value: string | number): number | null {
  if (value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function clamp(value: number, min?: number, max?: number): number {
  if (typeof min === 'number' && value < min) return min
  if (typeof max === 'number' && value > max) return max
  return value
}

export const NumberStepper = forwardRef<HTMLInputElement, NumberStepperProps>(
  ({ value, onValueChange, min, max, step = 1, size = 'md', disabled, className = '', ...props }, ref) => {
    const current = toNumber(value)
    const commit = (next: number) => onValueChange(String(clamp(next, min, max)))
    const adjust = (direction: -1 | 1) => {
      const base = current ?? (typeof min === 'number' ? min : 0)
      commit(base + direction * step)
    }

    return (
      <div
        className={cx(
          'flex w-full items-stretch overflow-hidden rounded-md border border-outline-variant bg-surface-container-high text-on-surface transition-colors focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/25',
          disabled && 'opacity-50',
          className,
        )}
      >
        <button
          type="button"
          onClick={() => adjust(-1)}
          disabled={disabled || (current != null && typeof min === 'number' && current <= min)}
          className={cx(
            'grid shrink-0 place-items-center border-r border-outline-variant text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-40',
            size === 'sm' ? 'w-8' : 'w-10',
          )}
          aria-label="Wert verringern"
        >
          <Minus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <input
          ref={ref}
          value={value}
          onChange={(event) => {
            const next = event.target.value
            if (next === '' || /^-?\d*$/.test(next)) onValueChange(next)
          }}
          onBlur={() => {
            const parsed = toNumber(value)
            if (parsed != null) onValueChange(String(clamp(parsed, min, max)))
          }}
          disabled={disabled}
          inputMode="numeric"
          className={cx(
            'min-w-0 flex-1 bg-transparent text-center text-sm outline-none placeholder:text-on-surface-variant disabled:cursor-not-allowed',
            size === 'sm' ? 'px-1.5 py-2' : 'px-3 py-2.5',
          )}
          {...props}
        />
        <button
          type="button"
          onClick={() => adjust(1)}
          disabled={disabled || (current != null && typeof max === 'number' && current >= max)}
          className={cx(
            'grid shrink-0 place-items-center border-l border-outline-variant text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-40',
            size === 'sm' ? 'w-8' : 'w-10',
          )}
          aria-label="Wert erhöhen"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
    )
  },
)

NumberStepper.displayName = 'NumberStepper'
