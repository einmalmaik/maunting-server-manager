import { create } from 'zustand'

/** Imperatives Confirm-Dialog-Pattern: ein zentraler Store haelt genau einen
 * offenen Dialog samt seinem `resolve`-Callback. `confirm({...})` gibt ein
 * Promise<boolean> zurueck und ersetzt damit `window.confirm` ueberall — mit
 * MSM-Styling statt nativer Browser-UI.
 *
 * KISS: nur eine aktive Anfrage zur Zeit. Wer eine zweite startet, ueberschreibt
 * die erste (die erste resolved automatisch mit `false` = "abgebrochen").
 */
export interface ConfirmOptions {
  /** Optionale Ueberschrift (Bestaetigungsdialog ohne Titel ist ok). */
  title?: string
  /** Kernfrage. Pflicht. */
  message: string
  /** Beschriftung des Bestaetigen-Buttons. Default: t('common.confirm'). */
  confirmText?: string
  /** Beschriftung des Abbrechen-Buttons. Default: t('common.cancel'). */
  cancelText?: string
  /** Wenn true: Bestaetigen-Button in destruktivem Stil (rot). */
  danger?: boolean
}

interface PendingConfirm extends ConfirmOptions {
  resolve: (ok: boolean) => void
}

interface ConfirmState {
  pending: PendingConfirm | null
  request: (opts: ConfirmOptions) => Promise<boolean>
  resolve: (ok: boolean) => void
}

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  pending: null,
  request: (opts) =>
    new Promise<boolean>((resolve) => {
      // Vorhandene Anfrage abbrechen, falls jemand parallel ein zweites
      // confirm() startet — andernfalls bliebe das alte Promise haengen.
      const prev = get().pending
      if (prev) prev.resolve(false)
      set({ pending: { ...opts, resolve } })
    }),
  resolve: (ok) => {
    const p = get().pending
    if (!p) return
    set({ pending: null })
    p.resolve(ok)
  },
}))

/** Convenience-Helper: `const ok = await confirm({ message: '...' })`. */
export function confirm(opts: ConfirmOptions): Promise<boolean> {
  return useConfirmStore.getState().request(opts)
}
