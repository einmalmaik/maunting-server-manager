import React from 'react'

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
  error?: string
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className = '', label, error, ...props }, ref) => {
    return (
      <div className="flex flex-col gap-1.5 w-full">
        {label && (
          <label htmlFor={props.id} className="text-sm font-medium text-foreground">
            {label}
          </label>
        )}
        <input
          ref={ref}
          className={`
            msm-input h-10
            disabled:cursor-not-allowed disabled:opacity-50
            ${error ? 'border-status-destructive focus:ring-status-destructive' : ''}
            ${className}
          `}
          {...props}
        />
        {error && (
          <span className="text-xs text-destructive">{error}</span>
        )}
      </div>
    )
  }
)
Input.displayName = 'Input'
