import { create } from 'zustand'

export interface Toast {
  id: number
  message: string
  type: 'error' | 'success'
}

interface ToastState {
  toasts: Toast[]
  addToast: (message: string, type?: 'error' | 'success') => void
  removeToast: (id: number) => void
  clearAll: () => void
}

let _nextId = 0
export const MAX_TOASTS = 5
export const AUTO_DISMISS_SUCCESS_MS = 5000
export const AUTO_DISMISS_ERROR_MS = 20000

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  addToast: (message, type = 'error') => {
    // Dieselbe Nachricht steht nur einmal im Stapel. Sonst türmt ein Poll im
    // Sekundentakt oder eine doppelt gemeldete 429-Sperre identische Toasts auf.
    if (get().toasts.some((t) => t.message === message && t.type === type)) return
    const id = ++_nextId
    set((s) => {
      // Maximal 5 Toasts gleichzeitig im Stapel behalten, um Überflutung zu verhindern.
      const base = s.toasts.length >= MAX_TOASTS ? s.toasts.slice(s.toasts.length - (MAX_TOASTS - 1)) : s.toasts
      return { toasts: [...base, { id, message, type }] }
    })

    const timeout = type === 'success' ? AUTO_DISMISS_SUCCESS_MS : AUTO_DISMISS_ERROR_MS
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
  error: (msg: string) => useToastStore.getState().addToast(msg, 'error'),
  success: (msg: string) => useToastStore.getState().addToast(msg, 'success'),
}
