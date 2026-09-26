import React from 'react'

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  error?: string
  /** Festbreitenschrift für Code (SQL, JSON, Funktionskörper). */
  mono?: boolean
}

/**
 * Mehrzeiliges Textfeld der Design-DNA — Gegenstück zu `Input`.
 *
 * Tab fügt in Code-Feldern (`mono`) zwei Leerzeichen ein, statt den Fokus
 * weiterzugeben; Escape gibt den Fokus wieder frei, damit das Feld keine
 * Tastaturfalle ist.
 */
export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className = '', label, error, mono = false, id, onKeyDown, ...props }, ref) => {
    const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      onKeyDown?.(event)
      if (event.defaultPrevented || !mono) return
      if (event.key === 'Escape') {
        event.currentTarget.blur()
        return
      }
      if (event.key === 'Tab' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault()
        const target = event.currentTarget
        const { selectionStart, selectionEnd, value } = target
        const next = `${value.slice(0, selectionStart)}  ${value.slice(selectionEnd)}`
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
        setter?.call(target, next)
        target.dispatchEvent(new Event('input', { bubbles: true }))
        target.selectionStart = target.selectionEnd = selectionStart + 2
      }
    }

    return (
      <div className="flex flex-col gap-1.5 w-full">
        {label && (
          <label htmlFor={id} className="text-sm font-medium text-foreground">
            {label}
          </label>
        )}
        <textarea
          id={id}
          ref={ref}
          spellCheck={mono ? false : props.spellCheck}
          className={`
            msm-input min-h-24 py-2 leading-relaxed
            disabled:cursor-not-allowed disabled:opacity-50
            ${mono ? 'font-mono text-xs' : 'text-sm'}
            ${error ? 'border-status-destructive focus:ring-status-destructive' : ''}
            ${className}
          `}
          aria-invalid={error ? true : undefined}
          onKeyDown={handleKeyDown}
          {...props}
        />
        {error && <span className="text-xs text-status-destructive">{error}</span>}
      </div>
    )
  },
)
Textarea.displayName = 'Textarea'
