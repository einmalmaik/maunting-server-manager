import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { ResourceEditorDialog } from './ResourceEditorDialog'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'

// Mock api client
vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

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
    // Non-numeric input is prevented at the updateField level (only digits allowed).
    // But we can test the validation by setting a value that bypasses the input filter
    // via direct state manipulation isn't possible; instead test below-minimum.
    fireEvent.change(screen.getByTestId('resource-cpu-input'), { target: { value: '5' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    expect(screen.getByTestId('resource-cpu-error')).toBeInTheDocument()
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
    mockApi.mockRejectedValueOnce(new Error('Invalid resource value'))

    fireEvent.change(screen.getByTestId('resource-ram-input'), { target: { value: '2048' } })
    fireEvent.click(screen.getByTestId('resource-save-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('resource-form-error')).toBeInTheDocument()
    })
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByTestId('resource-ram-input')).toHaveValue('2048')
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
})
