import { create } from 'zustand'

/** Imperatives Prompt-Dialog-Pattern: ein zentraler Store haelt genau einen
 * offenen Dialog samt seinem `resolve`-Callback. `prompt({...})` gibt ein
 * Promise<string | null> zurueck und ersetzt damit `window.prompt` ueberall —
 * mit MSM-Styling statt nativer Browser-UI.
 *
 * KISS: nur eine aktive Anfrage zur Zeit. Wer eine zweite startet, ueberschreibt
 * die erste (die erste resolved automatisch mit `null` = "abgebrochen").
 */
export interface PromptOptions {
  /** Optionale Ueberschrift. */
  title?: string
  /** Erklaerender Text. Pflicht. */
  message: string
  /** Placeholder fuer das Eingabefeld. */
  placeholder?: string
  /** Vorbelegung des Eingabefelds. */
  defaultValue?: string
  /** Beschriftung des Bestaetigen-Buttons. Default: t('common.confirm'). */
  confirmText?: string
  /** Beschriftung des Abbrechen-Buttons. Default: t('common.cancel'). */
  cancelText?: string
  /** Wenn true: Bestaetigen-Button in destruktivem Stil (rot). */
  danger?: boolean
  /** Wenn gesetzt, ist der Bestaetigen-Button erst freigegeben, wenn die
   * Eingabe exakt diesem Wert entspricht (Type-to-confirm-Muster fuer
   * destruktive Aktionen). */
  expectedValue?: string
}

interface PendingPrompt extends PromptOptions {
  resolve: (value: string | null) => void
}

interface PromptState {
  pending: PendingPrompt | null
  request: (opts: PromptOptions) => Promise<string | null>
  resolve: (value: string | null) => void
}

export const usePromptStore = create<PromptState>((set, get) => ({
  pending: null,
  request: (opts) =>
    new Promise<string | null>((resolve) => {
      // Vorhandene Anfrage abbrechen, falls jemand parallel ein zweites
      // prompt() startet — andernfalls bliebe das alte Promise haengen.
      const prev = get().pending
      if (prev) prev.resolve(null)
      set({ pending: { ...opts, resolve } })
    }),
  resolve: (value) => {
    const p = get().pending
    if (!p) return
    set({ pending: null })
    p.resolve(value)
  },
}))

/** Convenience-Helper: `const name = await prompt({ message: '...' })`.
 * Liefert den getrimmten Wert oder `null` bei Abbruch. */
export function prompt(opts: PromptOptions): Promise<string | null> {
  return usePromptStore.getState().request(opts)
}
