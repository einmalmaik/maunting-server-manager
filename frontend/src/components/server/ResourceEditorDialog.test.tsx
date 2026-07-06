import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { ResourceEditorDialog } from './ResourceEditorDialog'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'

// Mock api client, preserving real exports (e.g. SanitizedApiError) so
// tests can simulate the exact error type the real client throws.
vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, api: vi.fn() }
})

// Mock useHostInterfaces is not needed here; ResourceEditorDialog doesn't use it.

const mockApi = vi.mocked(client.api)

function setMockResponse(
  impl: (path: string, options?: RequestInit) => unknown,
) {
  mockApi.mockImplementation(async (path: string, options?: RequestInit) => {
    return impl(path, options) as any
  })
}

/** Renders the dialog with sensible defaults. */
function renderDialog(overrides?: Partial<Parameters<typeof ResourceEditorDialog>[0]>) {
  const onClose = vi.fn()
  const onSaved = vi.fn()
  const props = {
    onClose,
    serverId: 42,
    cpuLimit: 100 as number | null,
    ramLimit: 4096 as number | null,
    diskLimit: 50 as number | null,
    lifecycleBusy: false,
    onSaved,
    ...overrides,
  }
  const result = render(<ResourceEditorDialog {...props} />)
  return { ...result, onClose, onSaved, props }
}

describe('ResourceEditorDialog', () => {
  beforeEach(async () => {
    mockApi.mockReset()
    await i18n.changeLanguage('en')
    useToastStore.setState({ toasts: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // VAL-UI-003: Resource editor opens as an accessible modal
  it('renders an accessible modal with role=dialog, aria-modal, and labelled title', () => {
    renderDialog()
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.getAttribute('aria-labelledby')).toBeTruthy()
    // Title is present and visible
    expect(screen.getByText(i18n.t('serverDetail.resourceEditor.title'))).toBeInTheDocument()
  })

  it('has labelled CPU, RAM, Disk inputs, primary save, and secondary cancel', () => {
    renderDialog()
    expect(screen.getByTestId('resource-cpu-input')).toBeInTheDocument()
    expect(screen.getByTestId('resource-ram-input')).toBeInTheDocument()
    expect(screen.getByTestId('resource-disk-input')).toBeInTheDocument()
    expect(screen.getByTestId('resource-save-btn')).toHaveAttribute('type', 'submit')
    expect(screen.getByTestId('resource-cancel-btn')).toHaveAttribute('type', 'button')
  })

  // VAL-UI-004: Dialog initializes from current resource values
  it('pre-fills CPU, RAM, and Disk with current configured values', () => {
    renderDialog({ cpuLimit: 200, ramLimit: 8192, diskLimit: 100 })
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('200')
    expect(screen.getByTestId('resource-ram-input')).toHaveValue('8192')
    expect(screen.getByTestId('resource-disk-input')).toHaveValue('100')
  })

  it('shows blank inputs for unlimited (null) values, not 0 or undefined', () => {
    renderDialog({ cpuLimit: null, ramLimit: null, diskLimit: null })
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('')
    expect(screen.getByTestId('resource-ram-input')).toHaveValue('')
    expect(screen.getByTestId('resource-disk-input')).toHaveValue('')
  })

  // VAL-UI-005: Cancel, Escape, backdrop, and no-op save do not mutate
  it('cancel button closes dialog without sending PATCH', () => {
    const { onClose } = renderDialog()
    fireEvent.click(screen.getByTestId('resource-cancel-btn'))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('backdrop click closes dialog without sending PATCH', () => {
    const { onClose } = renderDialog()
    // The overlay is the outer div with role=dialog
    const dialog = screen.getByRole('dialog')
    fireEvent.click(dialog)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('Escape closes dialog without sending PATCH', () => {
    const { onClose } = renderDialog()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('saving without changes closes dialog without sending PATCH (no-op)', () => {
    const { onClose } = renderDialog({ cpuLimit: 100, ramLimit: 4096, diskLimit: 50 })
    fireEvent.click(screen.getByTestId('resource-save-btn'))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(mockApi).not.toHaveBeenCalled()
  })

  // VAL-UI-006: Changed numeric limits send only changed fields
  it('sends only changed CPU field when only CPU is edited', async () => {
    const { onSaved, onClose } = renderDialog({ cpuLimit: 100, ramLimit: 4096, diskLimit: 50 })
    setMockResponse(() => ({ id: 42 }))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
    const call = mockApi.mock.calls[0]
    expect(call[0]).toBe('/servers/42')
    expect(call[1]?.method).toBe('PATCH')
    const body = JSON.parse(call[1]?.body as string)
    expect(body).toEqual({ cpu_limit_percent: 200 })
    expect(body.ram_limit_mb).toBeUndefined()
    expect(body.disk_limit_gb).toBeUndefined()
    expect(onSaved).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('sends only changed RAM and Disk fields when CPU is unchanged', async () => {
    renderDialog({ cpuLimit: 100, ramLimit: 4096, diskLimit: 50 })
    setMockResponse(() => ({ id: 42 }))

    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '8192' } })
    fireEvent.change(screen.getByTestId('resource-disk-input'), { target: { value: '100' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
    const body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body).toEqual({ ram_limit_mb: 8192, disk_limit_gb: 100 })
    expect(body.cpu_limit_percent).toBeUndefined()
  })

  // VAL-UI-007: Blank fields save as unlimited (null)
  it('clearing a field sends null for that changed field', async () => {
    renderDialog({ cpuLimit: 100, ramLimit: 4096, diskLimit: 50 })
    setMockResponse(() => ({ id: 42 }))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
    const body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body).toEqual({ cpu_limit_percent: null })
  })

  it('clearing all fields sends all three as null', async () => {
    renderDialog({ cpuLimit: 100, ramLimit: 4096, diskLimit: 50 })
    setMockResponse(() => ({ id: 42 }))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '' } })
    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '' } })
    fireEvent.change(screen.getByTestId('resource-disk-input'), { target: { value: '' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
    const body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body).toEqual({
      cpu_limit_percent: null,
      ram_limit_mb: null,
      disk_limit_gb: null,
    })
  })

  // VAL-UI-008: Invalid values are blocked client-side, no PATCH
  it('blocks non-numeric CPU input and shows validation error without PATCH', () => {
    renderDialog({ cpuLimit: 100 })
    // Non-numeric text is now allowed to remain in the input so the user
    // sees localized validation feedback (VAL-UI-008 / VAL-UI-017).
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: 'abc' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
    // The invalid text remains visible in the input
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('abc')
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks CPU below minimum (10) with validation error and no PATCH', () => {
    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '5' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks CPU above maximum (3200) with validation error and no PATCH', () => {
    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '5000' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks RAM below minimum (512) with validation error and no PATCH', () => {
    renderDialog({ ramLimit: 4096 })
    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '100' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-ram-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks Disk below minimum (1) with validation error and no PATCH', () => {
    renderDialog({ diskLimit: 50 })
    fireEvent.change(screen.getByTestId('resource-disk-input'), { target: { value: '0' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-disk-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  // VAL-UI-017: Non-numeric, decimal, and negative values for all fields
  it('blocks decimal CPU input with validation error and no PATCH', () => {
    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '3.5' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('3.5')
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks negative CPU input with validation error and no PATCH', () => {
    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '-100' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('-100')
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks non-numeric RAM input with validation error and no PATCH', () => {
    renderDialog({ ramLimit: 4096 })
    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: 'abc' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-ram-error')).toBeInTheDocument()
    expect(screen.getByTestId('resource-ram-input')).toHaveValue('abc')
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks decimal RAM input with validation error and no PATCH', () => {
    renderDialog({ ramLimit: 4096 })
    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '512.5' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-ram-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks negative RAM input with validation error and no PATCH', () => {
    renderDialog({ ramLimit: 4096 })
    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '-512' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-ram-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks non-numeric Disk input with validation error and no PATCH', () => {
    renderDialog({ diskLimit: 50 })
    fireEvent.change(screen.getByTestId('resource-disk-input'), { target: { value: 'abc' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-disk-error')).toBeInTheDocument()
    expect(screen.getByTestId('resource-disk-input')).toHaveValue('abc')
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks decimal Disk input with validation error and no PATCH', () => {
    renderDialog({ diskLimit: 50 })
    fireEvent.change(screen.getByTestId('resource-disk-input'), { target: { value: '1.5' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-disk-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('blocks negative Disk input with validation error and no PATCH', () => {
    renderDialog({ diskLimit: 50 })
    fireEvent.change(screen.getByTestId('resource-disk-input'), { target: { value: '-1' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-disk-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  // VAL-UI-008: Invalid typed text remains visible with validation feedback
  it('shows validation error on blur for non-numeric CPU input', () => {
    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: 'abc' } })
    fireEvent.blur(screen.getByTestId('resource-cpu-input'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('abc')
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('shows validation error on blur for decimal RAM input', () => {
    renderDialog({ ramLimit: 4096 })
    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '1.5' } })
    fireEvent.blur(screen.getByTestId('resource-ram-input'))

    expect(screen.getByTestId('resource-ram-error')).toBeInTheDocument()
  })

  it('clears validation error when user fixes invalid input', () => {
    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: 'abc' } })
    fireEvent.blur(screen.getByTestId('resource-cpu-input'))
    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()

    // User fixes the input
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    expect(screen.queryByTestId('resource-cpu-error')).not.toBeInTheDocument()
  })

  // VAL-UI-009: Save is pending-safe
  it('shows disabled/loading state while saving and prevents duplicate PATCH requests', async () => {
    let resolvePatch: (v: unknown) => void = () => {}
    const pendingPromise = new Promise((resolve) => {
      resolvePatch = resolve
    })
    setMockResponse(() => pendingPromise)

    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })

    // Click save multiple times
    const saveBtn = screen.getByTestId('resource-save-btn')
    fireEvent.click(saveBtn)
    fireEvent.click(saveBtn)
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
    expect(saveBtn).toBeDisabled()

    // Resolve the pending request
    await act(async () => {
      resolvePatch({ id: 42 })
      await pendingPromise
    })
  })

  // VAL-UI-010: Successful save refreshes display with calm feedback
  it('shows success toast and calls onSaved + onClose after successful save', async () => {
    const { onSaved, onClose } = renderDialog({ cpuLimit: 100 })
    setMockResponse(() => ({ id: 42 }))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success')).toBe(true)
    })
    expect(onSaved).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  // VAL-UI-011 / VAL-UI-022: Failed save keeps safe UI state
  it('keeps dialog open with entered values and shows error on PATCH failure', async () => {
    const { onClose } = renderDialog({ cpuLimit: 100 })
    mockApi.mockRejectedValueOnce(new Error('Validation failed'))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
    })
    // Dialog stays open (onClose not called)
    expect(onClose).not.toHaveBeenCalled()
    // Entered value retained
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('200')
    // Error toast appears
    expect(useToastStore.getState().toasts.some((t) => t.type === 'error')).toBe(true)
  })

  it('backend 422 keeps dialog open with retained values and form-level error', async () => {
    const { onClose } = renderDialog({ cpuLimit: 100, ramLimit: 4096 })
    // A 422 comes through the API client's sanitized HTTP-response path,
    // so it is a SanitizedApiError carrying the field validation message.
    mockApi.mockRejectedValueOnce(new client.SanitizedApiError('Invalid resource value'))

    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '2048' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
    })
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByTestId('resource-ram-input')).toHaveValue('2048')
    // The recognized sanitized validation message is displayed directly.
    expect(screen.getByTestId('resource-form-error').textContent).toMatch(/Invalid resource value/)
  })

  // Unknown/generic errors map to safe localized fallback (no raw err.message)
  it('shows safe localized fallback for generic HTTP error instead of raw message', async () => {
    renderDialog({ cpuLimit: 100 })
    mockApi.mockRejectedValueOnce(new Error('HTTP 500'))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
    })
    const errorEl = screen.getByTestId('resource-form-error')
    // Must NOT show raw "HTTP 500"
    expect(errorEl.textContent).not.toMatch(/HTTP 500/)
    // Must show the localized fallback
    expect(errorEl.textContent).toBeTruthy()
    expect(errorEl.textContent).not.toBe('')
  })

  it('shows safe localized fallback for network error instead of raw message', async () => {
    renderDialog({ cpuLimit: 100 })
    mockApi.mockRejectedValueOnce(new Error('Failed to fetch'))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
    })
    const errorEl = screen.getByTestId('resource-form-error')
    // Must NOT show raw "Failed to fetch"
    expect(errorEl.textContent).not.toMatch(/Failed to fetch/i)
    expect(errorEl.textContent).toBeTruthy()
  })

  it('still displays recognized sanitized backend error messages', async () => {
    renderDialog({ cpuLimit: 100 })
    // Simulate a recognized sanitized backend error message as produced by
    // the API client's HTTP-response path (SanitizedApiError).
    mockApi.mockRejectedValueOnce(new client.SanitizedApiError('Validation failed'))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
    })
    const errorEl = screen.getByTestId('resource-form-error')
    // Recognized sanitized backend messages may still be displayed
    expect(errorEl.textContent).toMatch(/Validation failed/)
  })

  // Safe error hardening: arbitrary unexpected client/runtime exceptions
  // must map to the generic localized saveFailed fallback. Only known
  // sanitized backend messages (SanitizedApiError) or field validation
  // messages may be displayed directly. No raw err.message, host paths,
  // socket paths, or internal details may surface in the UI.
  describe('safe error hardening for unexpected failures', () => {
    const fallback = () => i18n.t('serverDetail.resourceEditor.errors.saveFailed')

    async function triggerSaveError(rejection: unknown) {
      mockApi.mockRejectedValueOnce(rejection)
      fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
      fireEvent.click(screen.getByTestId('resource-save-btn'))
      await waitFor(() => {
        expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
      })
      return screen.getByTestId('resource-form-error')
    }

    it('maps an arbitrary unexpected Error to the localized saveFailed fallback', async () => {
      renderDialog({ cpuLimit: 100 })
      const sentinel = 'ZZUNEXPECTED-client-bomb-9382'
      const errorEl = await triggerSaveError(new Error(sentinel))
      expect(errorEl.textContent).toBe(fallback())
      // Raw unexpected message must not leak.
      expect(errorEl.textContent).not.toMatch(/ZZUNEXPECTED-client-bomb-9382/)
    })

    it('maps an unexpected stack-trace-like Error to fallback without leaking internals', async () => {
      renderDialog({ cpuLimit: 100 })
      const sentinel = 'boom at internalStep (anonymous:42): cannot read config'
      const errorEl = await triggerSaveError(new Error(sentinel))
      expect(errorEl.textContent).toBe(fallback())
      expect(errorEl.textContent).not.toMatch(/boom at internalStep/)
      expect(errorEl.textContent).not.toMatch(/anonymous:42/)
    })

    it('maps a thrown string to the localized saveFailed fallback', async () => {
      renderDialog({ cpuLimit: 100 })
      const sentinel = 'ZZSTRING-bomb-555'
      const errorEl = await triggerSaveError(sentinel as unknown as Error)
      expect(errorEl.textContent).toBe(fallback())
      expect(errorEl.textContent).not.toMatch(/ZZSTRING-bomb-555/)
    })

    it('maps a non-Error object value to the localized saveFailed fallback', async () => {
      renderDialog({ cpuLimit: 100 })
      const errorEl = await triggerSaveError({ weird: 'payload', tag: 'ZZOBJ-4321' } as unknown)
      expect(errorEl.textContent).toBe(fallback())
      expect(errorEl.textContent).not.toMatch(/ZZOBJ-4321/)
    })

    it('maps null rejection to the localized saveFailed fallback', async () => {
      renderDialog({ cpuLimit: 100 })
      const errorEl = await triggerSaveError(null)
      expect(errorEl.textContent).toBe(fallback())
    })

    it('maps a TypeError (fetch failure) to the localized saveFailed fallback', async () => {
      renderDialog({ cpuLimit: 100 })
      const sentinel = 'ZZTYPE-bomb-111'
      const errorEl = await triggerSaveError(new TypeError(sentinel))
      expect(errorEl.textContent).toBe(fallback())
      expect(errorEl.textContent).not.toMatch(/ZZTYPE-bomb-111/)
    })

    it('maps an empty-message Error to the localized saveFailed fallback', async () => {
      renderDialog({ cpuLimit: 100 })
      const errorEl = await triggerSaveError(new Error(''))
      expect(errorEl.textContent).toBe(fallback())
    })

    it('still shows a recognized SanitizedApiError message directly (not the fallback)', async () => {
      renderDialog({ cpuLimit: 100 })
      const msg = 'Ressourcen-Update konnte nicht angewendet werden'
      const errorEl = await triggerSaveError(new client.SanitizedApiError(msg))
      // Recognized sanitized backend message is displayed, not the fallback.
      expect(errorEl.textContent).toMatch(/Ressourcen-Update konnte nicht angewendet werden/)
      expect(errorEl.textContent).not.toBe(fallback())
    })

    it('shows an error toast with the safe fallback for unexpected errors', async () => {
      renderDialog({ cpuLimit: 100 })
      const sentinel = 'ZZLEAK-bomb-999'
      await triggerSaveError(new Error(sentinel))
      const errorToasts = useToastStore.getState().toasts.filter((t) => t.type === 'error')
      expect(errorToasts.length).toBeGreaterThan(0)
      const toastMsg = errorToasts[errorToasts.length - 1].message
      expect(toastMsg).toBe(fallback())
      // The toast must not leak the raw unexpected content either.
      expect(toastMsg).not.toMatch(/ZZLEAK-bomb-999/)
    })
  })

  // VAL-UI-012: Disk UI does not overclaim hard quota behavior
  it('disk hint describes soft-limit behavior without claiming hard quota or data deletion', () => {
    renderDialog()
    const diskInput = screen.getByTestId('resource-disk-input')
    const hintId = diskInput.getAttribute('aria-describedby')
    expect(hintId).toBeTruthy()
    const hint = document.getElementById(hintId!)
    expect(hint?.textContent).toBeTruthy()
    // Must NOT claim hard quota or destructive behavior (will delete / erases data)
    const hintText = hint!.textContent!.toLowerCase()
    expect(hintText).not.toMatch(/hard\s*quota/)
    expect(hintText).not.toMatch(/will\s+delete/)
    expect(hintText).not.toMatch(/erases?\s+data/)
    // Should mention "soft limit"
    expect(hintText).toMatch(/soft.?limit/)
  })

  // VAL-UI-015: Dirty dialog edits survive background polling
  // Simulated by the component design: props changing after mount don't reset the form.
  it('does not reset form values when props change after mount (polling stability)', () => {
    const { rerender } = renderDialog({ cpuLimit: 100, ramLimit: 4096, diskLimit: 50 })

    // User edits CPU
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('200')

    // Simulate poll: parent re-renders with updated props (new limits from server)
    rerender(
      <ResourceEditorDialog
        onClose={vi.fn()}
        serverId={42}
        cpuLimit={150}
        ramLimit={4096}
        diskLimit={50}
        lifecycleBusy={false}
        onSaved={vi.fn()}
      />,
    )

    // Form values must NOT be reset by the prop change
    expect(screen.getByTestId('resource-cpu-input')).toHaveValue('200')
    expect(screen.getByTestId('resource-ram-input')).toHaveValue('4096')
    expect(screen.getByTestId('resource-disk-input')).toHaveValue('50')
  })

  it('does not send PATCH prematurely during polling while dialog is dirty', () => {
    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })

    // No save clicked — no PATCH should have been sent
    expect(mockApi).not.toHaveBeenCalled()
  })

  // VAL-UI-017: Client validation covers all resource fields and boundaries
  it('accepts valid boundary values: CPU 10, 3200; RAM 512; Disk 1', async () => {
    renderDialog({ cpuLimit: 100, ramLimit: 4096, diskLimit: 50 })
    setMockResponse(() => ({ id: 42 }))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '10' } })
    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '512' } })
    fireEvent.change(screen.getByTestId('resource-disk-input'), { target: { value: '1' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
    const body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body).toEqual({ cpu_limit_percent: 10, ram_limit_mb: 512, disk_limit_gb: 1 })
  })

  it('accepts valid boundary value CPU 3200', async () => {
    renderDialog({ cpuLimit: 100 })
    setMockResponse(() => ({ id: 42 }))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '3200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
  })

  it('rejects zero CPU with validation error and no PATCH', () => {
    renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '0' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  // VAL-UI-023: Resource edit availability is explicit across lifecycle states
  it('shows lifecycle-busy warning when lifecycleBusy is true', () => {
    renderDialog({ lifecycleBusy: true })
    // The lifecycle warning should be visible
    const saveBtn = screen.getByTestId('resource-save-btn')
    expect(saveBtn).toBeDisabled()
    // Warning text is present
    expect(screen.getByText(i18n.t('serverDetail.resourceEditor.lifecycleBusy'))).toBeInTheDocument()
  })

  it('does not send PATCH when lifecycleBusy is true even with changed values', () => {
    renderDialog({ cpuLimit: 100, lifecycleBusy: true })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    // Save button is disabled during lifecycle-busy state
    const saveBtn = screen.getByTestId('resource-save-btn')
    expect(saveBtn).toBeDisabled()
    // Clicking the disabled button should not trigger a PATCH
    fireEvent.click(saveBtn)
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('allows saving when lifecycleBusy is false (running or stopped server)', async () => {
    renderDialog({ cpuLimit: 100, lifecycleBusy: false })
    setMockResponse(() => ({ id: 42 }))

    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
  })

  // VAL-UI-020: Modal keyboard focus behavior is accessible
  it('does not use native browser dialogs (prompt/alert/confirm)', () => {
    const spy = vi.spyOn(window, 'confirm').mockImplementation(() => false)
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('')

    renderDialog()
    // No native dialog should have been triggered
    expect(spy).not.toHaveBeenCalled()
    expect(alertSpy).not.toHaveBeenCalled()
    expect(promptSpy).not.toHaveBeenCalled()

    spy.mockRestore()
    alertSpy.mockRestore()
    promptSpy.mockRestore()
  })

  // VAL-UI-013: DE and EN copy is complete
  it('shows English labels when language is en', async () => {
    await i18n.changeLanguage('en')
    renderDialog()
    expect(screen.getByText(i18n.t('serverDetail.resourceEditor.title'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('common.save'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('common.cancel'))).toBeInTheDocument()
  })

  it('shows German labels when language is de', async () => {
    await i18n.changeLanguage('de')
    renderDialog()
    expect(screen.getByText(i18n.t('serverDetail.resourceEditor.title'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('common.save'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('common.cancel'))).toBeInTheDocument()
    // No raw translation keys
    const title = screen.getByText(i18n.t('serverDetail.resourceEditor.title'))
    expect(title.textContent).not.toMatch(/^serverDetail\./)
  })

  // No raw translation keys appear in EN
  it('does not show raw translation keys in EN', async () => {
    await i18n.changeLanguage('en')
    renderDialog()
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).not.toMatch(/serverDetail\.resourceEditor\./)
    expect(dialog.textContent).not.toMatch(/common\./)
  })

  // No raw translation keys appear in DE
  it('does not show raw translation keys in DE', async () => {
    await i18n.changeLanguage('de')
    renderDialog()
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).not.toMatch(/serverDetail\.resourceEditor\./)
    expect(dialog.textContent).not.toMatch(/common\./)
  })

  // Focus management: first input should be focused on open
  it('moves focus to the CPU input when dialog opens', async () => {
    renderDialog()
    await waitFor(() => {
      expect(screen.getByTestId('resource-cpu-input')).toHaveFocus()
    })
  })

  // Escape does not close while saving
  it('Escape does not close dialog while saving is in progress', async () => {
    let resolvePatch: (v: unknown) => void = () => {}
    const pendingPromise = new Promise((resolve) => {
      resolvePatch = resolve
    })
    setMockResponse(() => pendingPromise)

    const { onClose } = renderDialog({ cpuLimit: 100 })
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })

    // Try Escape while saving
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()

    // Cleanup
    await act(async () => {
      resolvePatch({ id: 42 })
      await pendingPromise
    })
  })

  // VAL-UI-020: Tab wraps from last focusable to first within dialog
  it('traps Tab: Tab from last focusable element wraps to first', async () => {
    renderDialog()
    await waitFor(() => {
      expect(screen.getByTestId('resource-cpu-input')).toHaveFocus()
    })

    // Focus the last focusable element (Save button)
    screen.getByTestId('resource-save-btn').focus()
    expect(screen.getByTestId('resource-save-btn')).toHaveFocus()

    // Tab should wrap to the first focusable element (CPU input)
    await act(async () => {
      fireEvent.keyDown(window, { key: 'Tab' })
    })
    expect(screen.getByTestId('resource-cpu-input')).toHaveFocus()
  })

  // VAL-UI-020: Shift+Tab wraps from first focusable to last within dialog
  it('traps Shift+Tab: Shift+Tab from first focusable wraps to last', async () => {
    renderDialog()
    await waitFor(() => {
      expect(screen.getByTestId('resource-cpu-input')).toHaveFocus()
    })

    // Focus is on the first focusable element (CPU input)
    expect(screen.getByTestId('resource-cpu-input')).toHaveFocus()

    // Shift+Tab should wrap to the last focusable element (Save button)
    await act(async () => {
      fireEvent.keyDown(window, { key: 'Tab', shiftKey: true })
    })
    expect(screen.getByTestId('resource-save-btn')).toHaveFocus()
  })

  // VAL-UI-020: Focus returns to the triggering element when dialog closes
  it('returns focus to the trigger element when dialog closes', async () => {
    // Create a trigger button and focus it before opening the dialog
    const trigger = document.createElement('button')
    trigger.textContent = 'Edit Resources'
    document.body.appendChild(trigger)
    trigger.focus()
    expect(trigger).toHaveFocus()

    const { unmount } = renderDialog()

    // Dialog is open, CPU input should be focused (after setTimeout)
    await waitFor(() => {
      expect(screen.getByTestId('resource-cpu-input')).toHaveFocus()
    })

    // Close the dialog (unmount)
    unmount()

    // Focus should return to the trigger button
    expect(trigger).toHaveFocus()

    document.body.removeChild(trigger)
  })

  // VAL-UI-020: Tab brings focus back into dialog if it escaped
  it('brings focus back into dialog when Tab is pressed outside dialog controls', async () => {
    renderDialog()
    await waitFor(() => {
      expect(screen.getByTestId('resource-cpu-input')).toHaveFocus()
    })

    // Move focus outside the dialog (simulates focus escaping)
    const outsideButton = document.createElement('button')
    outsideButton.textContent = 'Outside'
    document.body.appendChild(outsideButton)
    outsideButton.focus()
    expect(outsideButton).toHaveFocus()

    // Tab should bring focus back to the first dialog control
    await act(async () => {
      fireEvent.keyDown(window, { key: 'Tab' })
    })
    expect(screen.getByTestId('resource-cpu-input')).toHaveFocus()

    document.body.removeChild(outsideButton)
  })

  // VAL-UI-007: Clearing a field via DOM (bypassing onChange) still sends null.
  // This simulates real browser behavior where controlled input state can
  // get out of sync with the DOM.
  it('sends null when CPU field is cleared via DOM without triggering onChange', async () => {
    renderDialog({ cpuLimit: 150, ramLimit: 4096, diskLimit: 50 })
    setMockResponse(() => ({ id: 42 }))

    // Simulate browser automation clearing the field without firing onChange
    const cpuInput = screen.getByTestId('resource-cpu-input') as HTMLInputElement
    cpuInput.value = ''

    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })
    const body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body).toEqual({ cpu_limit_percent: null })
  })

  // VAL-UI-008: Validation error shows for CPU below minimum even when DOM
  // value bypasses onChange (real browser robustness).
  it('shows CPU below-minimum validation error even when DOM value bypasses onChange', () => {
    renderDialog({ cpuLimit: 100 })

    // Simulate browser automation typing '5' without triggering onChange
    const cpuInput = screen.getByTestId('resource-cpu-input') as HTMLInputElement
    cpuInput.value = '5'

    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
    expect(mockApi).not.toHaveBeenCalled()
  })

  // VAL-CROSS-012: Rootless Docker failure in UI shows sanitized localized
  // feedback, keeps old cards/API values, and exposes no cgroup/socket/
  // path/stack details.
  describe('rootless failure UI safety (VAL-CROSS-012)', () => {
    const sensitivePatterns = [
      /cgroup/i,
      /docker\.sock/i,
      /\/var\/run/i,
      /\/sys\/fs/i,
      /Traceback/i,
      /File "/i,
      /socket\s+path/i,
      /stack\s+trace/i,
    ]

    it('shows sanitized backend message for rootless failure with no sensitive details', async () => {
      const { onSaved, onClose } = renderDialog({ cpuLimit: 100, ramLimit: 4096, diskLimit: 50 })
      // Backend returns a sanitized 503 message for rootless failures.
      // The API client wraps it in SanitizedApiError.
      const rootlessMsg = 'Ressourcen-Update konnte nicht angewendet werden'
      mockApi.mockRejectedValueOnce(new client.SanitizedApiError(rootlessMsg))

      fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
      fireEvent.click(screen.getByTestId('resource-save-btn'))

      await waitFor(() => {
        expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
      })

      const errorEl = screen.getByTestId('resource-form-error')
      // Sanitized message is displayed (recognized SanitizedApiError)
      expect(errorEl.textContent).toMatch(/Ressourcen-Update konnte nicht angewendet werden/)

      // No sensitive markers leak into the UI
      const errorText = errorEl.textContent || ''
      for (const pattern of sensitivePatterns) {
        expect(errorText).not.toMatch(pattern)
      }

      // Dialog stays open with entered values (old values preserved)
      expect(screen.getByTestId('resource-cpu-input')).toHaveValue('200')
      expect(screen.getByTestId('resource-ram-input')).toHaveValue('4096')
      expect(screen.getByTestId('resource-disk-input')).toHaveValue('50')
      expect(screen.getByRole('dialog')).toBeInTheDocument()

      // No success callback fired after a failed save
      expect(onSaved).not.toHaveBeenCalled()
      // Dialog does not close after a failed save
      expect(onClose).not.toHaveBeenCalled()
      // No success toast appears — only error feedback
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success')).toBe(false)

      // API was called exactly once (the failed PATCH) and never retried
      expect(mockApi).toHaveBeenCalledTimes(1)

      // Toast shows the sanitized error, no sensitive markers
      const errorToasts = useToastStore.getState().toasts.filter((t) => t.type === 'error')
      expect(errorToasts.length).toBeGreaterThan(0)
      const toastMsg = errorToasts[errorToasts.length - 1].message
      expect(toastMsg).toMatch(/Ressourcen-Update konnte nicht angewendet werden/)
      for (const pattern of sensitivePatterns) {
        expect(toastMsg).not.toMatch(pattern)
      }
    })

    it('uses safe fallback and preserves old values when rootless error has sensitive payload', async () => {
      const { onSaved, onClose } = renderDialog({ cpuLimit: 150, ramLimit: 2048, diskLimit: 30 })
      // Simulate a rootless failure where an unexpected error contains
      // sensitive internals (cgroup path, socket path). Since this is NOT
      // a SanitizedApiError, the dialog must use the safe fallback and
      // must not display the raw sensitive content.
      const sensitivePayload = 'cgroup v2 controller not delegated at /sys/fs/cgroup/docker.sock'
      mockApi.mockRejectedValueOnce(new Error(sensitivePayload))

      fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '200' } })
      fireEvent.click(screen.getByTestId('resource-save-btn'))

      await waitFor(() => {
        expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
      })

      const errorEl = screen.getByTestId('resource-form-error')
      const fallback = i18n.t('serverDetail.resourceEditor.errors.saveFailed')
      // Safe fallback is shown, not the sensitive payload
      expect(errorEl.textContent).toBe(fallback)
      expect(errorEl.textContent).not.toMatch(/cgroup|docker\.sock|\/sys\/fs/i)

      // Old values preserved (dialog stays open with entered values)
      expect(screen.getByTestId('resource-cpu-input')).toHaveValue('200')
      expect(screen.getByTestId('resource-ram-input')).toHaveValue('2048')
      expect(screen.getByTestId('resource-disk-input')).toHaveValue('30')
      expect(screen.getByRole('dialog')).toBeInTheDocument()

      // No success callback fired after a failed save
      expect(onSaved).not.toHaveBeenCalled()
      // Dialog does not close after a failed save
      expect(onClose).not.toHaveBeenCalled()
      // No success toast appears — only error feedback
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success')).toBe(false)

      // API was called exactly once (the failed PATCH) and never retried
      expect(mockApi).toHaveBeenCalledTimes(1)

      // Toast also uses safe fallback, no sensitive markers
      const errorToasts = useToastStore.getState().toasts.filter((t) => t.type === 'error')
      expect(errorToasts.length).toBeGreaterThan(0)
      const toastMsg = errorToasts[errorToasts.length - 1].message
      expect(toastMsg).toBe(fallback)
      expect(toastMsg).not.toMatch(/cgroup|docker\.sock|\/sys\/fs/i)
    })
  })
})
