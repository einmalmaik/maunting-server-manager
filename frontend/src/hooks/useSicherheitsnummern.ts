/**
 * Die Sicherheitsnummern eines Kontakts, je Gerät eine.
 *
 * Geladen wird nur, solange der Dialog offen ist, und immer frisch vom Server:
 * wer vergleicht, will den heutigen Stand sehen, nicht einen gemerkten. Ein
 * Gerät ohne lesbaren Schlüssel bekommt eine leere Nummer statt einer falschen.
 * Bis 09/2026 stand das in `Messenger.tsx`.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { SicherheitsnummerGeraet } from '@/components/social/modals/SafetyNumberDialog'
import { geraeteVon, sicherheitsnummer } from '@/services/e2eeGeraet'

export function useSicherheitsnummern(peerId: number | undefined) {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(false)
  const [geraete, setGeraete] = useState<SicherheitsnummerGeraet[]>([])
  const [laedt, setLaedt] = useState(false)

  useEffect(() => {
    if (!offen || !peerId) {
      setGeraete([])
      return
    }
    let aktiv = true
    setLaedt(true)
    geraeteVon(peerId)
      .then(async (liste) => {
        const ergebnisse: SicherheitsnummerGeraet[] = []
        for (const g of liste) {
          let num = ''
          if (g.public_key) {
            try {
              num = await sicherheitsnummer(g.public_key)
            } catch {
              num = ''
            }
          }
          ergebnisse.push({
            id: g.device_id,
            label: g.label || t('profile.e2eeDevices.unnamed'),
            number: num,
          })
        }
        if (aktiv) {
          setGeraete(ergebnisse)
          setLaedt(false)
        }
      })
      .catch(() => {
        if (aktiv) {
          setGeraete([])
          setLaedt(false)
        }
      })
    return () => {
      aktiv = false
    }
  }, [offen, peerId, t])

  return { offen, setOffen, geraete, laedt }
}
