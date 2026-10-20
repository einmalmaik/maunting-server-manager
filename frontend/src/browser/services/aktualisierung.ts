/**
 * Updates unter Windows und Android (`aktualisieren.rs`): kurz nach dem Start
 * fragt der Browser einmal bei GitHub, ob es eine neue Version gibt.
 *
 * Unter Windows lädt er sie dann **still im Hintergrund** und prüft Paketgröße
 * und SHA-256 aus dem signierten Velopack-Feed; erst wenn nur der Neustart
 * fehlt, erscheint das `UpdateBadge` in der Kopfleiste. Unter Android bleibt
 * es bei der Meldung mit Klick, weil dort erst der System-Dialog die APK
 * installiert. Ein Entwicklungsbau fragt nie.
 */
import { useEffect } from 'react'

import i18n from '@/i18n'
import { toast } from '@/stores/toastStore'

import { useEinstellungenStore } from './einstellungenStore'
import { istTauri, nativ } from './nativ'
import { istAndroid } from './plattform'

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

export async function herunterladen(): Promise<void> {
  try {
    await nativ.updateHerunterladen()
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
        .then((info) => {
          if (!info?.verfuegbar || !info.neue_version) return
          const version = info.neue_version
          if (istAndroid()) {
            // Android installiert erst nach ausdrücklichem Klick.
            toast.success(i18n.t('browser.update.da', { version }), {
              label: i18n.t('browser.update.installieren'),
              ausfuehren: () => void herunterladen(),
            })
            return
          }
          // Windows: still vorbereiten. Das Badge meldet erst den Neustart.
          void herunterladen()
        })
        // Ohne Netz oder ohne Antwort von GitHub: beim nächsten Start wieder.
        .catch(() => null)
    }, NACH_DEM_START_MS)
    return () => window.clearTimeout(zeit)
  }, [suchen])
}
