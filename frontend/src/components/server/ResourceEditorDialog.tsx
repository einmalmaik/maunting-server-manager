import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Cpu, MemoryStick, HardDrive, Info } from 'lucide-react'
import { api, SanitizedApiError } from '@/api/client'
import { toast } from '@/stores/toastStore'

/**
 * Resource-Limit-Editor für Server-Detail (CPU / RAM / Disk).
 *
 * KISS-Prinzip: kleines, fokussiertes Modal, das nur geänderte Felder
 * als PATCH sendet. Leere Felder bedeuten null = unbegrenzt.
 *
 * Backend bleibt alleinige Wahrheitsquelle für Berechtigungen
 * (server.resources.manage). Dieses Komponente ist reine UX.
 *
 * Lifecycle: Nur gemountet wenn `open` true ist (conditional render im
 * Parent). useState-Initializer erfassen die Startwerte beim Oeffnen —
 * Polling aktualisiert die Props nicht neu im Formular, weil die
 * Initializer nur beim Mount laufen. Das schuetzt dirty-Edits vor
 * Poll-Resets (VAL-UI-015).
 */

interface ResourceEditorDialogProps {
  onClose: () => void
  serverId: number
  cpuLimit: number | null
  ramLimit: number | null
  diskLimit: number | null
  /** True waehrend transienter Lifecycle-States (starting/stopping/...). */
  lifecycleBusy: boolean
  onSaved: () => void
}

interface FormState {
  cpu: string
  ram: string
  disk: string
}

function limitToString(v: number | null): string {
  return v != null ? String(v) : ''
}

export function ResourceEditorDialog({
  onClose,
  serverId,
  cpuLimit,
  ramLimit,
  diskLimit,
  lifecycleBusy,
  onSaved,
}: ResourceEditorDialogProps) {
  const { t } = useTranslation()

  // Startwerte beim Oeffnen erfassen (nur beim Mount, nicht bei Poll-Updates)
  const initialRef = useRef({ cpu: cpuLimit, ram: ramLimit, disk: diskLimit })
  const [form, setForm] = useState<FormState>(() => ({
    cpu: limitToString(cpuLimit),
    ram: limitToString(ramLimit),
    disk: limitToString(diskLimit),
  }))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const dialogRef = useRef<HTMLDivElement>(null)
  const cpuInputRef = useRef<HTMLInputElement>(null)
  const ramInputRef = useRef<HTMLInputElement>(null)
  const diskInputRef = useRef<HTMLInputElement>(null)

  // Focus-Management: beim Oeffnen ersten Input fokussieren,
  // beim Schliessen Focus auf den Ausloeser zurueckgeben.
  // setTimeout(0) ist zuverlaessiger als requestAnimationFrame in echten
  // Browsern, da es nach der aktuellen Task feuert und die DOM-Updates
  // bereits verarbeitet sind (VAL-UI-020).
  useEffect(() => {
    const previousActive = document.activeElement as HTMLElement | null
    const timeoutId = setTimeout(() => cpuInputRef.current?.focus(), 0)
    return () => {
      clearTimeout(timeoutId)
      previousActive?.focus()
    }
  }, [])

  // Keyboard-Handler auf Window-Ebene: Escape schliesst (wenn nicht saving),
  // Tab/Shift+Tab bleiben innerhalb des Dialogs gefangen (VAL-UI-020).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !saving) {
        e.preventDefault()
        onClose()
        return
      }
      if (e.key === 'Tab') {
        const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        )
        if (!focusable || focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        const inside = dialogRef.current?.contains(document.activeElement)
        if (e.shiftKey) {
          if (document.activeElement === first || !inside) {
            e.preventDefault()
            last.focus()
          }
        } else {
          if (document.activeElement === last || !inside) {
            e.preventDefault()
            first.focus()
          }
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, saving])

  const updateField = (field: keyof FormState, value: string) => {
    // Allow any typed text so invalid input remains visible long enough to
    // show localized validation feedback (VAL-UI-008 / VAL-UI-017).
    // Validation runs on blur and on save; invalid values block PATCH.
    setForm((prev) => ({ ...prev, [field]: value }))
    setErrors((prev) => ({ ...prev, [field]: '' }))
    setFormError(null)
  }

  // Per-field validation: returns a localized error string or '' if valid.
  // Empty string means "unlimited" and is always valid.
  const validateField = (field: keyof FormState, value: string): string => {
    if (value === '') return ''
    if (!/^\d+$/.test(value)) {
      return t('serverDetail.resourceEditor.errors.integer')
    }
    const v = parseInt(value, 10)
    if (field === 'cpu') {
      if (v < 10) return t('serverDetail.resourceEditor.errors.cpuMin')
      if (v > 3200) return t('serverDetail.resourceEditor.errors.cpuMax')
    } else if (field === 'ram') {
      if (v < 512) return t('serverDetail.resourceEditor.errors.ramMin')
    } else if (field === 'disk') {
      if (v < 1) return t('serverDetail.resourceEditor.errors.diskMin')
    }
    return ''
  }

  const handleBlur = (field: keyof FormState) => {
    // Read from DOM ref for reliability in real browsers where controlled
    // input state might be out of sync (VAL-UI-008 / VAL-UI-017).
    const ref = field === 'cpu' ? cpuInputRef : field === 'ram' ? ramInputRef : diskInputRef
    const domValue = ref.current?.value ?? ''
    const err = validateField(field, domValue)
    setErrors((prev) => ({ ...prev, [field]: err }))
  }

  // Read current values from DOM refs. Controlled inputs can get out of
  // sync with React state when browser automation or edge cases bypass
  // onChange (VAL-UI-007). DOM refs always reflect the actual input values.
  const getFieldValues = (): FormState => ({
    cpu: cpuInputRef.current?.value ?? '',
    ram: ramInputRef.current?.value ?? '',
    disk: diskInputRef.current?.value ?? '',
  })

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (saving) return

    if (lifecycleBusy) {
      setFormError(t('serverDetail.resourceEditor.lifecycleBusy'))
      return
    }

    // Read from DOM refs for reliability in real browsers (VAL-UI-007 /
    // VAL-UI-008 / VAL-UI-017).
    const values = getFieldValues()

    // Validate using DOM values
    const errs: Record<string, string> = {}
    const cpuErr = validateField('cpu', values.cpu)
    if (cpuErr) errs.cpu = cpuErr
    const ramErr = validateField('ram', values.ram)
    if (ramErr) errs.ram = ramErr
    const diskErr = validateField('disk', values.disk)
    if (diskErr) errs.disk = diskErr
    setErrors(errs)
    if (Object.keys(errs).length > 0) return

    // Build payload using DOM values
    const init = initialRef.current
    const body: Record<string, number | null> = {}
    const cpuNum = values.cpu === '' ? null : parseInt(values.cpu, 10)
    if (cpuNum !== init.cpu) body.cpu_limit_percent = cpuNum
    const ramNum = values.ram === '' ? null : parseInt(values.ram, 10)
    if (ramNum !== init.ram) body.ram_limit_mb = ramNum
    const diskNum = values.disk === '' ? null : parseInt(values.disk, 10)
    if (diskNum !== init.disk) body.disk_limit_gb = diskNum

    if (Object.keys(body).length === 0) {
      // No-op: keine geaenderten Felder -> Dialog schliessen ohne PATCH
      onClose()
      return
    }

    setSaving(true)
    setFormError(null)
    try {
      await api(`/servers/${serverId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
      toast.success(t('serverDetail.resourceEditor.saved'))
      onSaved()
      onClose()
    } catch (err: unknown) {
      // Allowlist approach: only messages from the API client's sanitized
      // HTTP-response path (SanitizedApiError) may be displayed directly.
      // The backend is the authority for sanitizing those (no host paths,
      // socket paths, sensitive data, stack traces — VAL-API-010). Everything
      // else — arbitrary Error objects, thrown strings, non-Error values,
      // fetch TypeErrors, unknown client/runtime failures — gets a safe
      // localized fallback so no raw err.message / internals can leak.
      const safeFallback = t('serverDetail.resourceEditor.errors.saveFailed')
      const displayMsg =
        err instanceof SanitizedApiError && err.message ? err.message : safeFallback
      setFormError(displayMsg)
      toast.error(displayMsg)
      // Dialog bleibt offen, eingegebene Werte bleiben erhalten
    } finally {
      setSaving(false)
    }
  }

  const fieldLabel = (label: string, icon: ReactNode) => (
    <span className="inline-flex items-center gap-1.5">
      {icon}
      {label}
    </span>
  )

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 overflow-y-auto"
      onClick={() => !saving && onClose()}
      role="dialog"
      aria-modal="true"
      aria-labelledby="resource-editor-title"
    >
      <div
        ref={dialogRef}
        className="msm-card w-full max-w-lg p-6 my-8"
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          id="resource-editor-title"
          className="font-headline text-headline-md text-primary mb-1"
        >
          {t('serverDetail.resourceEditor.title')}
        </h2>
        <p className="font-body-md text-sm text-on-surface-variant mb-6">
          {t('serverDetail.resourceEditor.description')}
        </p>

        {lifecycleBusy && (
          <div className="mb-4 p-3 rounded-md border border-status-warning/30 bg-status-warning/5 flex items-start gap-2">
            <Info className="w-4 h-4 text-status-warning shrink-0 mt-0.5" />
            <p className="font-body-md text-sm text-status-warning">
              {t('serverDetail.resourceEditor.lifecycleBusy')}
            </p>
          </div>
        )}

        <form onSubmit={handleSave} className="space-y-4">
          {/* CPU */}
          <div>
            <label
              className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider"
              htmlFor="resource-cpu"
            >
              {fieldLabel(t('serverDetail.resourceEditor.cpu'), <Cpu className="w-3.5 h-3.5" />)}
            </label>
            <input
              id="resource-cpu"
              ref={cpuInputRef}
              type="text"
              inputMode="numeric"
              className="msm-input"
              value={form.cpu}
              onChange={(e) => updateField('cpu', e.target.value)}
              onBlur={() => handleBlur('cpu')}
              aria-invalid={!!errors.cpu || undefined}
              aria-describedby={errors.cpu ? 'resource-cpu-error' : 'resource-cpu-hint'}
              data-testid="resource-cpu-input"
              disabled={saving}
            />
            <p id="resource-cpu-hint" className="font-body-md text-xs text-on-surface-variant mt-1">
              {t('serverDetail.resourceEditor.cpuHint')}
            </p>
            {errors.cpu && (
              <p id="resource-cpu-error" role="alert" className="font-body-md text-xs text-status-error mt-1" data-testid="resource-cpu-error">
                {errors.cpu}
              </p>
            )}
          </div>

          {/* RAM */}
          <div>
            <label
              className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider"
              htmlFor="resource-ram"
            >
              {fieldLabel(t('serverDetail.resourceEditor.ram'), <MemoryStick className="w-3.5 h-3.5" />)}
            </label>
            <input
              id="resource-ram"
              ref={ramInputRef}
              type="text"
              inputMode="numeric"
              className="msm-input"
              value={form.ram}
              onChange={(e) => updateField('ram', e.target.value)}
              onBlur={() => handleBlur('ram')}
              aria-invalid={!!errors.ram || undefined}
              aria-describedby={errors.ram ? 'resource-ram-error' : 'resource-ram-hint'}
              data-testid="resource-ram-input"
              disabled={saving}
            />
            <p id="resource-ram-hint" className="font-body-md text-xs text-on-surface-variant mt-1">
              {t('serverDetail.resourceEditor.ramHint')}
            </p>
            {errors.ram && (
              <p id="resource-ram-error" role="alert" className="font-body-md text-xs text-status-error mt-1" data-testid="resource-ram-error">
                {errors.ram}
              </p>
            )}
          </div>

          {/* Disk */}
          <div>
            <label
              className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider"
              htmlFor="resource-disk"
            >
              {fieldLabel(t('serverDetail.resourceEditor.disk'), <HardDrive className="w-3.5 h-3.5" />)}
            </label>
            <input
              id="resource-disk"
              ref={diskInputRef}
              type="text"
              inputMode="numeric"
              className="msm-input"
              value={form.disk}
              onChange={(e) => updateField('disk', e.target.value)}
              onBlur={() => handleBlur('disk')}
              aria-invalid={!!errors.disk || undefined}
              aria-describedby={errors.disk ? 'resource-disk-error' : 'resource-disk-hint'}
              data-testid="resource-disk-input"
              disabled={saving}
            />
            <p id="resource-disk-hint" className="font-body-md text-xs text-on-surface-variant mt-1">
              {t('serverDetail.resourceEditor.diskHint')}
            </p>
            {errors.disk && (
              <p id="resource-disk-error" role="alert" className="font-body-md text-xs text-status-error mt-1" data-testid="resource-disk-error">
                {errors.disk}
              </p>
            )}
          </div>

          {formError && (
            <div
              role="alert"
              className="p-3 rounded-md border border-status-error/30 bg-status-error/5"
              data-testid="resource-form-error"
            >
              <p className="font-body-md text-sm text-status-error">
                {formError}
              </p>
            </div>
          )}

          <div className="flex gap-3 pt-2">
            <button
              type="button"
              className="msm-btn-secondary flex-1 py-2"
              onClick={() => !saving && onClose()}
              disabled={saving}
              data-testid="resource-cancel-btn"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              className="msm-btn-primary flex-1 py-2 disabled:opacity-50"
              disabled={saving || lifecycleBusy}
              aria-busy={saving || undefined}
              data-testid="resource-save-btn"
            >
              {saving
                ? t('serverDetail.resourceEditor.saving')
                : t('common.save')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
