import { create } from 'zustand'

export type ToastTyp = 'error' | 'success' | 'warning' | 'info'

/** Eine Handlung am Toast, etwa „Rückgängig“ nach dem Verschieben in den Papierkorb. */
export interface ToastAktion {
  label: string
  ausfuehren: () => void
}

export interface Toast {
  id: number
  message: string
  type: ToastTyp
  aktion?: ToastAktion
}

interface ToastState {
  toasts: Toast[]
  addToast: (message: string, type?: ToastTyp, aktion?: ToastAktion) => void
  removeToast: (id: number) => void
  clearAll: () => void
}

let _nextId = 0
export const MAX_TOASTS = 5
export const AUTO_DISMISS_SUCCESS_MS = 5000
export const AUTO_DISMISS_ERROR_MS = 20000
export const AUTO_DISMISS_INFO_MS = 5000
// Eine Warnung ist kein Fehler, aber auch kein Beifall: sie steht laenger als
// eine Erfolgsmeldung und kuerzer als ein Fehler.
export const AUTO_DISMISS_WARNING_MS = 10000
// Wer „Rückgängig“ antippen soll, muss den Knopf erst finden. 5 s reichten im
// Emulator nicht, um nach dem Papierkorb den Daumen zum Toast zu bringen.
export const AUTO_DISMISS_AKTION_MS = 10000

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  addToast: (message, type = 'error', aktion) => {
    // Dieselbe Nachricht steht nur einmal im Stapel. Sonst türmt ein Poll im
    // Sekundentakt oder eine doppelt gemeldete 429-Sperre identische Toasts auf.
    if (get().toasts.some((t) => t.message === message && t.type === type)) return
    const id = ++_nextId
    set((s) => {
      // Maximal 5 Toasts gleichzeitig im Stapel behalten, um Überflutung zu verhindern.
      const base = s.toasts.length >= MAX_TOASTS ? s.toasts.slice(s.toasts.length - (MAX_TOASTS - 1)) : s.toasts
      return { toasts: [...base, { id, message, type, aktion }] }
    })

    const grund =
      type === 'error' ? AUTO_DISMISS_ERROR_MS
      : type === 'warning' ? AUTO_DISMISS_WARNING_MS
      : AUTO_DISMISS_SUCCESS_MS
    const timeout = aktion ? Math.max(grund, AUTO_DISMISS_AKTION_MS) : grund
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, timeout)
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  // Für das Ende einer Sitzung. Der Stapel hängt an keiner Seite, sondern am
  // Wurzelelement der Anwendung: eine Meldung wie „Server prod-eu-1 gestoppt"
  // stünde sonst nach dem Abmelden weiter über der Anmeldeseite.
  clearAll: () => set({ toasts: [] }),
}))

export const toast = {
  error: (msg: string, aktion?: ToastAktion) => useToastStore.getState().addToast(msg, 'error', aktion),
  success: (msg: string, aktion?: ToastAktion) => useToastStore.getState().addToast(msg, 'success', aktion),
  warning: (msg: string, aktion?: ToastAktion) => useToastStore.getState().addToast(msg, 'warning', aktion),
  info: (msg: string, aktion?: ToastAktion) => useToastStore.getState().addToast(msg, 'info', aktion),
}
