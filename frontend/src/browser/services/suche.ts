/** Ob die Suchleiste offen ist (`kopf/Suchleiste.tsx`). */
import { create } from 'zustand'

interface SucheZustand {
  offen: boolean
  /** Hochgezählt bei jedem Öffnen, damit ein zweites Strg+F das Feld markiert. */
  anstoss: number
  oeffnen: () => void
  schliessen: () => void
}

export const useSuche = create<SucheZustand>()((set) => ({
  offen: false,
  anstoss: 0,
  oeffnen: () => set((s) => ({ offen: true, anstoss: s.anstoss + 1 })),
  schliessen: () => set({ offen: false }),
}))
