/**
 * Speicher und Upload-Stand des Tresors, für „Dateien“ und „Fotos“ gleich.
 *
 * Der Speicher kommt aus den Rollen des Kontos. Ohne Rolle mit Speicher bleibt
 * das Hochladen zu; der Server wiese es ohnehin ab. Nach jedem Upload-Schub
 * wird neu gefragt, damit die Anzeige stimmt.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HardDrive } from 'lucide-react'
import { ProgressBar } from '@/Singra/UI'
import { formatBytes } from '@/components/server/fileHelpers'
import { useTresorUploads } from './tresorDateien'
import { speicherAbfragen, type TresorSpeicher } from './tresorBlobApi'

export function useTresorSpeicher(): { speicher: TresorSpeicher | null; ohneSpeicher: boolean } {
  const [speicher, setSpeicher] = useState<TresorSpeicher | null>(null)
  const anzahlUploads = useTresorUploads((s) => Object.keys(s.je).length)
  useEffect(() => {
    if (anzahlUploads > 0) return
    let aktiv = true
    speicherAbfragen()
      .then((stand) => aktiv && setSpeicher(stand))
      .catch(() => aktiv && setSpeicher(null))
    return () => {
      aktiv = false
    }
  }, [anzahlUploads])
  return { speicher, ohneSpeicher: speicher?.quote === 0 }
}

/** So meldet das Backend „ohne Grenze“ (`MAX_QUOTE` in `vault_blob_service.py`, der Owner). */
const OHNE_GRENZE = 1024 ** 5

export function TresorSpeicherAnzeige({ speicher }: { speicher: TresorSpeicher | null }) {
  const { t } = useTranslation()
  if (!speicher) return null
  return (
    <div className="flex items-start gap-2">
      <HardDrive className="mt-0.5 h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden />
      {speicher.quote === 0 ? (
        <p className="text-label-sm text-on-surface-variant">{t('mss.vault.dateien.keinSpeicher')}</p>
      ) : speicher.quote >= OHNE_GRENZE ? (
        // Ein Balken gegen 1 PiB stünde immer bei null und nennte „1024 TB“.
        <p className="text-label-sm text-on-surface-variant">
          {t('mss.vault.dateien.speicherOhneGrenze', { belegt: formatBytes(speicher.belegt) })}
        </p>
      ) : (
        <div className="min-w-0 flex-1">
          <ProgressBar
            value={(speicher.belegt / speicher.quote) * 100}
            heat
            ariaLabel={t('mss.vault.dateien.speicher')}
            hint={t('mss.vault.dateien.speicherBelegt', { belegt: formatBytes(speicher.belegt), quote: formatBytes(speicher.quote) })}
          />
        </div>
      )}
    </div>
  )
}

/**
 * Alle laufenden Uploads in einer Zeile. Die Kachel eines Fotos von 2019 liegt
 * weit unten in der Galerie; ohne diese Zeile sah man nach dem Verschlüsseln
 * nicht, dass noch etwas hochgeht, und lud doppelt hoch oder schloss die App.
 */
export function TresorUploadStand() {
  const { t } = useTranslation()
  const je = useTresorUploads((s) => s.je)
  const laufend = Object.values(je)
  if (laufend.length === 0) return null
  const gesendet = laufend.reduce((n, u) => n + u.gesendet, 0)
  const gesamt = laufend.reduce((n, u) => n + u.gesamt, 0)
  const speicherVoll = laufend.some((u) => u.fehler === 'speicherVoll')
  return (
    <div role="status" className="rounded-lg border border-outline-variant/20 bg-surface-container-low px-3 py-2">
      {speicherVoll ? (
        <p className="text-label-sm text-status-destructive">{t('mss.vault.dateien.uploadStandVoll', { count: laufend.length })}</p>
      ) : (
        <ProgressBar
          value={gesamt > 0 ? (gesendet / gesamt) * 100 : null}
          ariaLabel={t('mss.vault.dateien.wirdHochgeladen')}
          hint={t('mss.vault.dateien.uploadStand', { count: laufend.length })}
        />
      )}
    </div>
  )
}
