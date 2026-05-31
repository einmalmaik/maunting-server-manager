import React from 'react'

interface SwitchProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean
  onCheckedChange?: (checked: boolean) => void
}

export const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ checked, disabled, className = '', onCheckedChange, onClick, ...props }, ref) => {
    return (
      <button
        ref={ref}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={(event) => {
          onClick?.(event)
          if (!event.defaultPrevented && !disabled) {
            onCheckedChange?.(!checked)
          }
        }}
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 ${
          checked ? 'bg-primary' : 'bg-surface-variant'
        } ${className}`}
        {...props}
      >
        <span
          className={`pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg transition-transform ${
            checked ? 'translate-x-4' : 'translate-x-0.5'
          }`}
          aria-hidden="true"
        />
      </button>
    )
  },
)

Switch.displayName = 'Switch'
