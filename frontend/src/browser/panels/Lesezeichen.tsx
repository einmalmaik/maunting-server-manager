import { ArrowDown, ArrowUp, Pencil, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { prompt } from '@/Singra/UI'

import { useVerlaufStore } from '../services/verlaufStore'
import { Eintragsliste } from './Eintragsliste'

export function LesezeichenPanel() {
  const { t } = useTranslation()
  const lesezeichen = useVerlaufStore((s) => s.lesezeichen)
  const { lesezeichenEntfernen, lesezeichenUmbenennen, lesezeichenVerschieben } = useVerlaufStore.getState()

  const umbenennen = async (url: string, titel: string) => {
    const neu = await prompt({
      title: t('browser.lesezeichen.umbenennenTitel'),
      message: t('browser.lesezeichen.umbenennenText'),
      defaultValue: titel,
      confirmText: t('browser.lesezeichen.umbenennen'),
    })
    if (neu !== null) lesezeichenUmbenennen(url, neu)
  }

  return (
    <Eintragsliste
      eintraege={lesezeichen}
      leer={t('browser.lesezeichen.leer')}
      entfernen={(e) => lesezeichenEntfernen(e.url)}
      verschieben={lesezeichenVerschieben}
      aktionen={(e, i) => [
        { key: 'umbenennen', label: t('browser.lesezeichen.umbenennen'), icon: <Pencil className="h-4 w-4" />, onSelect: () => void umbenennen(e.url, e.titel) },
        { key: 'hoch', label: t('browser.lesezeichen.nachOben'), icon: <ArrowUp className="h-4 w-4" />, disabled: i === 0, onSelect: () => lesezeichenVerschieben(i, i - 1) },
        {
          key: 'runter',
          label: t('browser.lesezeichen.nachUnten'),
          icon: <ArrowDown className="h-4 w-4" />,
          disabled: i === lesezeichen.length - 1,
          onSelect: () => lesezeichenVerschieben(i, i + 1),
        },
        {
          key: 'weg',
          label: t('browser.liste.entfernen'),
          icon: <Trash2 className="h-4 w-4" />,
          destructive: true,
          separatorBefore: true,
          onSelect: () => lesezeichenEntfernen(e.url),
        },
      ]}
    />
  )
}
