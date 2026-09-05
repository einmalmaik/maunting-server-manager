import React from 'react'

interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'prefix'> {
  label?: string
  error?: string
  prefix?: React.ReactNode
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className = '', label, error, prefix, id, ...props }, ref) => {
    return (
      <div className="flex flex-col gap-1.5 w-full">
        {label && (
          <label htmlFor={id} className="text-sm font-medium text-foreground">
            {label}
          </label>
        )}
        {prefix ? (
          <div
            className={`
              flex items-stretch w-full rounded-md border border-outline-variant bg-surface-container-high
              focus-within:ring-2 focus-within:ring-primary focus-within:border-transparent focus-within:bg-surface-container
              transition-all duration-200
              ${error ? 'border-status-destructive focus-within:ring-status-destructive' : ''}
            `}
          >
            <span className="flex items-center px-3 text-sm text-on-surface-variant font-mono bg-surface-container-low/60 rounded-l-md border-r border-outline-variant select-none">
              {prefix}
            </span>
            <input
              id={id}
              ref={ref}
              className={`
                flex-1 bg-transparent px-3 py-2 text-sm text-on-surface placeholder-on-surface-variant outline-none min-w-0
                disabled:cursor-not-allowed disabled:opacity-50
                ${className}
              `}
              {...props}
            />
          </div>
        ) : (
          <input
            id={id}
            ref={ref}
            className={`
              msm-input h-10
              disabled:cursor-not-allowed disabled:opacity-50
              ${error ? 'border-status-destructive focus:ring-status-destructive' : ''}
              ${className}
            `}
            {...props}
          />
        )}
        {error && (
          <span className="text-xs text-destructive">{error}</span>
        )}
      </div>
    )
  }
)
Input.displayName = 'Input'
