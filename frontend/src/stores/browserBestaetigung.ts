import { create } from 'zustand'

/**
 * Eine Passkey-Bestätigung, die gerade im Browser läuft (nur App).
 *
 * `passkeyNachweis` legt sie an und wartet; der Dialog zeigt die Zahl, die
 * der Mensch im Browser antippt, und bietet Abbrechen an.
 */
export interface OffeneBestaetigung {
  zahl: number
  adresse: string
  abbrechen: () => void
}

interface BrowserBestaetigungState {
  offen: OffeneBestaetigung | null
  setzen: (offen: OffeneBestaetigung | null) => void
}

export const useBrowserBestaetigung = create<BrowserBestaetigungState>((set) => ({
  offen: null,
  setzen: (offen) => set({ offen }),
}))
