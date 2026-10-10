/**
 * Updates unter Windows und Android (`aktualisieren.rs`): kurz nach dem Start
 * fragt der Browser einmal bei GitHub, ob es eine neue Version gibt, und sagt
 * es per Meldung. Installiert wird nur auf Klick.
 */
import { useEffect } from 'react'

import i18n from '@/i18n'
import { toast } from '@/stores/toastStore'

import { useEinstellungenStore } from './einstellungenStore'
import { istTauri, nativ } from './nativ'

/** Erst nach dem Start fragen, damit der Kaltstart ungestört bleibt. */
export const NACH_DEM_START_MS = 20_000

export function updatesMoeglich(): boolean {
  return istTauri()
}

export async function installieren(): Promise<void> {
  try {
    await nativ.updateInstallieren()
  } catch {
    toast.error(i18n.t('browser.update.fehler'))
  }
}

export function useAktualisierung(): void {
  const suchen = useEinstellungenStore((s) => s.updatesSuchen)

  useEffect(() => {
    if (!suchen || !updatesMoeglich()) return
    const zeit = window.setTimeout(() => {
      void nativ
        .updatePruefen()
        .then((version) => {
          if (!version) return
          toast.success(i18n.t('browser.update.da', { version }), {
            label: i18n.t('browser.update.installieren'),
            ausfuehren: () => void installieren(),
          })
        })
        // Ohne Netz oder ohne Antwort von GitHub: beim nächsten Start wieder.
        .catch(() => null)
    }, NACH_DEM_START_MS)
    return () => window.clearTimeout(zeit)
  }, [suchen])
}
