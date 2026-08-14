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
}

let _nextId = 0

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  addToast: (message, type = 'error') => {
    // Dieselbe Nachricht steht nur einmal im Stapel. Sonst türmt ein Poll im
    // Sekundentakt oder eine doppelt gemeldete 429-Sperre identische Toasts auf.
    if (get().toasts.some((t) => t.message === message && t.type === type)) return
    const id = ++_nextId
    set((s) => ({ toasts: [...s.toasts, { id, message, type }] }))
    if (type === 'success') {
      setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
      }, 5000)
    }
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

export const toast = {
  error: (msg: string) => useToastStore.getState().addToast(msg, 'error'),
  success: (msg: string) => useToastStore.getState().addToast(msg, 'success'),
}
