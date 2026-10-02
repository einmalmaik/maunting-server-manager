import React, { createContext, useContext, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useZurueckSchliesst } from './useZurueckSchliesst'

interface DialogContextValue {
  open: boolean
  onOpenChange: (open: boolean) => void
  escapeSchliesst: boolean
}

const DialogContext = createContext<DialogContextValue | null>(null)

export interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: React.ReactNode
  /** Aus, wo Escape dem Inhalt gehört (Suche im Editor). Zurück schließt trotzdem. */
  escapeSchliesst?: boolean
}

export function Dialog({ open, onOpenChange, children, escapeSchliesst = true }: DialogProps) {
  return (
    <DialogContext.Provider value={{ open, onOpenChange, escapeSchliesst }}>
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

/** Liegt `overlay` (das Element mit `aria-modal`) über allen anderen offenen Dialogen? */
export function istObersterDialog(overlay: Element | null | undefined): boolean {
  const dialoge = document.querySelectorAll('[aria-modal="true"]')
  return !!overlay && overlay === dialoge[dialoge.length - 1]
}

/** Hält Tab und Umschalt+Tab in `rahmen`: vom letzten Ziel geht es zum ersten und umgekehrt. */
export function tabImRahmen(e: KeyboardEvent, rahmen: HTMLElement) {
  if (e.key !== 'Tab') return
  const ziele = Array.from(rahmen.querySelectorAll<HTMLElement>(FOCUSSABLE))
  if (ziele.length === 0) return
  const erstes = ziele[0]
  const letztes = ziele[ziele.length - 1]
  if (e.shiftKey && document.activeElement === erstes) {
    e.preventDefault()
    letztes.focus()
  } else if (!e.shiftKey && document.activeElement === letztes) {
    e.preventDefault()
    erstes.focus()
  }
}

export function DialogContent({
  children,
  className = '',
  overlayClassName = '',
  showCloseButton = true,
  // Der Name gehört an das Element mit `role="dialog"`, nicht an die Karte darin.
  'aria-labelledby': labelledBy,
  'aria-describedby': describedBy,
  'aria-label': ariaLabel,
  ...props
}: DialogContentProps) {
  const { t } = useTranslation()

  const ctx = useContext(DialogContext)
  if (!ctx) {
    throw new Error('DialogContent must be used within a Dialog')
  }

  // Der Wert des Providers ist bei jedem Neuzeichnen des Aufrufers neu. Hinge
  // der Effekt daran, sprang der Fokus bei jedem Tastendruck an den Auslöser
  // und dann aufs erste Feld (bis 02.10.2026). Die Handler lesen deshalb hier.
  const ctxRef = useRef(ctx)
  ctxRef.current = ctx

  // Der Inhalt existiert nur, solange der Dialog offen ist: Zurück schließt ihn.
  useZurueckSchliesst(true, () => ctxRef.current.onOpenChange(false))

  const dialogRef = useRef<HTMLDivElement>(null)
  const previousFocus = useRef<HTMLElement | null>(
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null
  )

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!istObersterDialog(dialogRef.current?.parentElement)) return
      if (e.key === 'Escape') {
        // Ein offenes Popover im Dialog (Auswahl, Kalender, Menü) hat Escape
        // schon in der Capture-Phase genommen und schließt nur sich selbst.
        if (!ctxRef.current.escapeSchliesst || e.defaultPrevented) return
        e.preventDefault()
        ctxRef.current.onOpenChange(false)
        return
      }

      if (dialogRef.current) tabImRahmen(e, dialogRef.current)
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
  }, [])

  return createPortal(
    <div
      className={`msm-modal-overlay animate-fade-in ${overlayClassName}`}
      onClick={() => ctx.onOpenChange(false)}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      aria-label={ariaLabel}
    >
      <div
        ref={dialogRef}
        className={`msm-card relative w-full max-w-lg overflow-hidden flex flex-col ${className}`}
        onClick={(e) => e.stopPropagation()}
        {...props}
      >
        {children}
        {showCloseButton && (
          <button
            type="button"
            onClick={() => ctx.onOpenChange(false)}
            className="absolute top-4 right-4 p-1.5 text-on-surface-variant hover:text-on-surface rounded-xl hover:bg-surface-container-high transition-colors z-10"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>
    </div>,
    document.body,
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
      className={`font-headline text-headline-md text-primary ${className}`}
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
