/**
 * Popover für Seiteninformationen und Site-Reset (Spuren auf dieser Seite löschen).
 *
 * Öffnet sich beim Klick auf das Schloss-Icon der Adresszeile.
 * Zeigt den Verschlüsselungsstatus (HTTPS / HTTP), die aktuelle Herkunft (Origin)
 * und bietet einen direkten Ein-Klick-Button zum Leeren von Cookies, LocalStorage,
 * SessionStorage und Cache dieser Domain samt anschließendem Neuladen.
 */
import { useState, type RefObject } from 'react'
import { Globe, Lock, LockOpen, RefreshCw, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Ankerfenster, Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { nativ } from '../services/nativ'
import type { Tab } from '../services/tabsStore'

interface SeitenSicherheitPopoverProps {
  offen: boolean
  onSchliessen: () => void
  anker: RefObject<HTMLElement | null>
  tab?: Tab | null
}

export function SeitenSicherheitPopover({
  offen,
  onSchliessen,
  anker,
  tab,
}: SeitenSicherheitPopoverProps) {
  const { t } = useTranslation()
  const [laedt, setLaedt] = useState(false)

  if (!tab || !tab.url) return null

  let herkunft = ''
  let host = ''
  let sicher = false
  let unsicher = false

  try {
    const parsed = new URL(tab.url)
    herkunft = parsed.origin
    host = parsed.hostname
    sicher = parsed.protocol === 'https:'
    unsicher = parsed.protocol === 'http:'
  } catch {
    herkunft = tab.url
    host = tab.url
  }

  const zuruecksetzen = async () => {
    if (laedt || !tab) return
    setLaedt(true)
    try {
      await nativ.tabSeiteZuruecksetzen(tab.id)
      toast.success(t('browser.adresse.seiteZurueckgesetzt'))
      onSchliessen()
    } catch {
      toast.error(t('common.error'))
    } finally {
      setLaedt(false)
    }
  }

  return (
    <Ankerfenster
      offen={offen}
      onSchliessen={onSchliessen}
      anker={anker}
      label={t('browser.adresse.sicherheitTitel')}
      ausrichtung="start"
      className="w-80 p-3"
    >
      <div className="flex flex-col gap-3">
        {/* Status-Header */}
        <div className="flex items-start gap-2.5">
          <div
            className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
              sicher
                ? 'bg-status-success/15 text-status-success'
                : unsicher
                ? 'bg-status-warning/15 text-status-warning'
                : 'bg-surface-container-highest text-on-surface-variant'
            }`}
          >
            {sicher ? (
              <Lock className="h-4 w-4" aria-hidden="true" />
            ) : unsicher ? (
              <LockOpen className="h-4 w-4" aria-hidden="true" />
            ) : (
              <Globe className="h-4 w-4" aria-hidden="true" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-title-sm font-medium text-on-surface">
              {host || herkunft}
            </h3>
            <p className="mt-0.5 text-label-sm text-on-surface-variant">
              {sicher
                ? t('browser.adresse.sicherHinweis')
                : unsicher
                ? t('browser.adresse.unsicherHinweis')
                : herkunft}
            </p>
          </div>
        </div>

        <div className="h-px bg-outline-variant/50" />

        {/* Site-Reset Aktion */}
        <div className="flex flex-col gap-1.5">
          <Button
            variant="destructive"
            size="sm"
            aria-label={t('browser.adresse.seiteZuruecksetzen')}
            className="w-full justify-start gap-2 text-label-md"
            disabled={laedt}
            onClick={() => void zuruecksetzen()}
          >
            {laedt ? (
              <RefreshCw className="h-4 w-4 animate-spin shrink-0" aria-hidden="true" />
            ) : (
              <Trash2 className="h-4 w-4 shrink-0" aria-hidden="true" />
            )}
            <span>{t('browser.adresse.seiteZuruecksetzen')}</span>
          </Button>
          <p className="px-0.5 text-label-sm text-on-surface-variant">
            {t('browser.adresse.seiteZuruecksetzenHinweis')}
          </p>
        </div>
      </div>
    </Ankerfenster>
  )
}
