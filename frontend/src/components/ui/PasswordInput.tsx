import React, { useState, useEffect, useId, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, EyeOff } from 'lucide-react'

interface PasswordInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
  error?: string
}

export const PasswordInput = React.forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className = '', label, error, ...props }, ref) => {
    const { t } = useTranslation()
    const [showPassword, setShowPassword] = useState(false)
    const eigeneId = useId()
    const feldId = props.id ?? eigeneId
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
          <label htmlFor={feldId} className="text-sm font-medium text-foreground text-on-surface-variant">
            {label}
          </label>
        )}
        <div className="relative w-full">
          {/*
            Die drei `::`-Regeln nehmen dem Browser sein eigenes Auge (Edge und
            Chrome) und Safaris Schlüsselsymbol weg. Ohne sie stehen zwei Augen
            nebeneinander: unseres und seins — und das des Browsers hält sich
            nicht an die 30 Sekunden, nach denen hier wieder zugeht.
          */}
          <input
            ref={ref}
            type={showPassword ? 'text' : 'password'}
            className={`
              msm-input h-10 pr-10
              [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden
              disabled:cursor-not-allowed disabled:opacity-50
              ${error ? 'border-status-destructive focus:ring-status-destructive' : ''}
              ${className}
            `}
            {...props}
            id={feldId}
          />
          <button
            type="button"
            onClick={handleToggle}
            className="absolute right-0 top-1/2 -translate-y-1/2 inline-flex h-10 w-10 items-center justify-center rounded-md text-on-surface-variant hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary transition-colors"
            aria-label={t(showPassword ? 'common.hidePassword' : 'common.showPassword')}
            aria-pressed={showPassword}
          >
            {showPassword ? (
              <EyeOff className="w-4 h-4" />
            ) : (
              <Eye className="w-4 h-4" />
            )}
          </button>
        </div>
        {error && (
          <span className="text-xs text-status-destructive">{error}</span>
        )}
      </div>
    )
  }
)
PasswordInput.displayName = 'PasswordInput'
