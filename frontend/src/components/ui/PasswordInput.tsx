import React, { useState, useEffect, useRef } from 'react'
import { Eye, EyeOff } from 'lucide-react'

interface PasswordInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
  error?: string
}

export const PasswordInput = React.forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className = '', label, error, ...props }, ref) => {
    const [showPassword, setShowPassword] = useState(false)
    const timerRef = useRef<NodeJS.Timeout | null>(null)

    const handleToggle = () => {
      setShowPassword((prev) => {
        const next = !prev
        if (next) {
          // Start a 30 second timer to hide the password automatically
          if (timerRef.current) {
            clearTimeout(timerRef.current)
          }
          timerRef.current = setTimeout(() => {
            setShowPassword(false)
          }, 30000)
        } else {
          // Clear timer when toggled off manually
          if (timerRef.current) {
            clearTimeout(timerRef.current)
            timerRef.current = null
          }
        }
        return next
      })
    }

    // Clean up timer on unmount
    useEffect(() => {
      return () => {
        if (timerRef.current) {
          clearTimeout(timerRef.current)
        }
      }
    }, [])

    return (
      <div className="flex flex-col gap-1.5 w-full">
        {label && (
          <label htmlFor={props.id} className="text-sm font-medium text-foreground text-on-surface-variant">
            {label}
          </label>
        )}
        <div className="relative w-full">
          <input
            ref={ref}
            type={showPassword ? 'text' : 'password'}
            className={`
              msm-input h-10 pr-10
              disabled:cursor-not-allowed disabled:opacity-50
              ${error ? 'border-status-destructive focus:ring-status-destructive' : ''}
              ${className}
            `}
            {...props}
          />
          <button
            type="button"
            onClick={handleToggle}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface focus:outline-none transition-colors"
            title={showPassword ? 'Passwort verbergen' : 'Passwort anzeigen'}
          >
            {showPassword ? (
              <EyeOff className="w-4 h-4" />
            ) : (
              <Eye className="w-4 h-4" />
            )}
          </button>
        </div>
        {error && (
          <span className="text-xs text-destructive">{error}</span>
        )}
      </div>
    )
  }
)
PasswordInput.displayName = 'PasswordInput'
