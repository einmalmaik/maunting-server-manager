import React, { forwardRef, useRef, useImperativeHandle } from 'react'

export interface ChatInputBarProps {
  value: string
  onChange: (value: string) => void
  onSubmit: (e?: React.FormEvent) => void
  placeholder?: string
  disabled?: boolean
  maxLength?: number
  maxHeight?: number
  minHeight?: number
  leftActions?: React.ReactNode
  rightActions?: React.ReactNode
  className?: string
  textareaClassName?: string
  onKeyDown?: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void
  'aria-label'?: string
}

export interface ChatInputBarRef {
  textarea: HTMLTextAreaElement | null
  focus: () => void
  resetHeight: () => void
}

/**
 * ChatInputBar - Projektübergreifende, hochwertige Input-Leiste (Design-DNA)
 * für Chat, Messenger und KI-Assistent.
 * Bietet dynamisch mitwachsende Textarea, dezente Oberflächenverläufe, saubere Fokus-Zustände
 * und Slots für Anhänge, Sprachaufnahme und Sende-Buttons.
 */
export const ChatInputBar = forwardRef<ChatInputBarRef, ChatInputBarProps>(
  (
    {
      value,
      onChange,
      onSubmit,
      placeholder = 'Nachricht schreiben …',
      disabled = false,
      maxLength = 16000,
      maxHeight = 160,
      minHeight = 36,
      leftActions,
      rightActions,
      className = '',
      textareaClassName = '',
      onKeyDown,
      'aria-label': ariaLabel = 'Nachricht',
    },
    ref
  ) => {
    const internalTextareaRef = useRef<HTMLTextAreaElement>(null)

    useImperativeHandle(ref, () => ({
      get textarea() {
        return internalTextareaRef.current
      },
      focus: () => {
        internalTextareaRef.current?.focus()
      },
      resetHeight: () => {
        if (internalTextareaRef.current) {
          internalTextareaRef.current.style.height = `${minHeight}px`
        }
      },
    }))

    const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange(e.target.value)
      const target = e.target
      target.style.height = 'auto'
      target.style.height = `${Math.min(Math.max(target.scrollHeight, minHeight), maxHeight)}px`
    }

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (onKeyDown) {
        onKeyDown(e)
        if (e.defaultPrevented) return
      }

      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault()
        onSubmit(e)
      }
    }

    return (
      <div
        className={`flex items-end gap-2 rounded-2xl border border-outline-variant/50 bg-surface-container-low p-2 transition-all focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/20 ${
          disabled ? 'opacity-60 pointer-events-none' : ''
        } ${className}`}
      >
        {leftActions && (
          <div className="flex items-center gap-1 shrink-0">
            {leftActions}
          </div>
        )}

        <textarea
          ref={internalTextareaRef}
          value={value}
          onChange={handleTextChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          maxLength={maxLength}
          aria-label={ariaLabel}
          rows={1}
          style={{ minHeight: `${minHeight}px`, maxHeight: `${maxHeight}px` }}
          className={`flex-1 resize-none border-0 bg-transparent py-1.5 text-sm leading-6 text-on-surface placeholder:text-on-surface-variant/60 focus:outline-none focus:ring-0 ${textareaClassName}`}
        />

        {rightActions && (
          <div className="flex items-center gap-1 shrink-0">
            {rightActions}
          </div>
        )}
      </div>
    )
  }
)

ChatInputBar.displayName = 'ChatInputBar'
