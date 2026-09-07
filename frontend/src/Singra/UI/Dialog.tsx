import React, { createContext, useContext, useEffect, useRef } from 'react'
import { X } from 'lucide-react'

interface DialogContextValue {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const DialogContext = createContext<DialogContextValue | null>(null)

export interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: React.ReactNode
}

export function Dialog({ open, onOpenChange, children }: DialogProps) {
  return (
    <DialogContext.Provider value={{ open, onOpenChange }}>
      {open ? children : null}
    </DialogContext.Provider>
  )
}

export interface DialogContentProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
  className?: string
  overlayClassName?: string
  showCloseButton?: boolean
}

const FOCUSSABLE =
  'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function DialogContent({
  children,
  className = '',
  overlayClassName = '',
  showCloseButton = true,
  ...props
}: DialogContentProps) {
  const ctx = useContext(DialogContext)
  if (!ctx) {
    throw new Error('DialogContent must be used within a Dialog')
  }

  const dialogRef = useRef<HTMLDivElement>(null)
  const previousFocus = useRef<HTMLElement | null>(
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null
  )

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        ctx.onOpenChange(false)
        return
      }

      if (e.key === 'Tab' && dialogRef.current) {
        const focusable = Array.from(
          dialogRef.current.querySelectorAll<HTMLElement>(FOCUSSABLE)
        )
        if (focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]

        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }

    const el = dialogRef.current
    if (el && !el.contains(document.activeElement)) {
      el.querySelector<HTMLElement>(FOCUSSABLE)?.focus()
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      if (previousFocus.current && previousFocus.current.isConnected) {
        previousFocus.current.focus()
      }
    }
  }, [ctx])

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-[fadeIn_.15s_ease-out] ${overlayClassName}`}
      onClick={() => ctx.onOpenChange(false)}
      role="dialog"
      aria-modal="true"
    >
      <div
        ref={dialogRef}
        className={`relative w-full max-w-lg bg-surface-container-low border border-outline-variant/30 rounded-2xl shadow-2xl overflow-hidden flex flex-col ${className}`}
        onClick={(e) => e.stopPropagation()}
        {...props}
      >
        {children}
        {showCloseButton && (
          <button
            type="button"
            onClick={() => ctx.onOpenChange(false)}
            className="absolute top-4 right-4 p-1.5 text-on-surface-variant hover:text-on-surface rounded-xl hover:bg-surface-container-high transition-colors z-10"
            aria-label="Schließen"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>
    </div>
  )
}

export function DialogHeader({
  className = '',
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`p-6 border-b border-outline-variant/30 bg-gradient-to-r from-surface-container-low via-surface-container to-surface-container-low ${className}`}
      {...props}
    >
      {children}
    </div>
  )
}

export function DialogTitle({
  className = '',
  children,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={`font-headline text-title-lg font-black text-primary tracking-tight ${className}`}
      {...props}
    >
      {children}
    </h3>
  )
}

export function DialogDescription({
  className = '',
  children,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p
      className={`font-body text-xs text-on-surface-variant mt-1 leading-relaxed ${className}`}
      {...props}
    >
      {children}
    </p>
  )
}

export function DialogFooter({
  className = '',
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`p-4 border-t border-outline-variant/30 bg-surface-container-lowest flex items-center justify-end gap-2 ${className}`}
      {...props}
    >
      {children}
    </div>
  )
}
