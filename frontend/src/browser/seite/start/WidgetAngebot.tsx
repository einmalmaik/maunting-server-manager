/**
 * Am Handy, einmal: das Such-Widget auf den Startbildschirm legen. Den Dialog
 * dafür zeigt der Startbildschirm selbst (`SuchWidget.anheften`). Liegt das
 * Widget schon dort oder geht es nur von Hand, erscheint nichts.
 */
import { useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'

import { useEinstellungenStore } from '../../services/einstellungenStore'
import { nativ } from '../../services/nativ'

export function WidgetAngebot() {
  const { t } = useTranslation()
  const erledigt = useEinstellungenStore((s) => s.widgetAngeboten)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const [anheftbar, setAnheftbar] = useState(false)

  useEffect(() => {
    if (erledigt) return
    let aktiv = true
    nativ
      .widgetLage()
      .then((lage) => {
        if (!aktiv) return
        if (lage === 'liegt') setzen({ widgetAngeboten: true })
        setAnheftbar(lage === 'anheftbar')
      })
      .catch(() => undefined)
    return () => {
      aktiv = false
    }
  }, [erledigt, setzen])

  if (erledigt || !anheftbar) return null

  return (
    <div className="flex w-full items-start gap-3 rounded-lg border border-outline-variant bg-surface-container p-4 text-body-sm">
      <Search className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
      <div className="flex-1">
        <p className="font-medium text-on-surface">{t('browser.start.widgetTitel')}</p>
        <p className="mt-1 text-on-surface-variant">{t('browser.start.widgetText')}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setzen({ widgetAngeboten: true })
              void nativ.widgetAnheften().catch(() => undefined)
            }}
          >
            {t('browser.start.widgetAnheften')}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setzen({ widgetAngeboten: true })}>
            {t('browser.start.widgetSpaeter')}
          </Button>
        </div>
      </div>
    </div>
  )
}
