import { useTranslation } from 'react-i18next'

import { Button, confirm } from '@/Singra/UI'

import { useVerlaufStore, type Eintrag } from '../services/verlaufStore'
import { Eintragsliste } from './Eintragsliste'

function tag(zeit: number): string {
  return new Date(zeit).toDateString()
}

export function VerlaufPanel() {
  const { t, i18n } = useTranslation()
  const verlauf = useVerlaufStore((s) => s.verlauf)
  const entfernen = useVerlaufStore((s) => s.verlaufEntfernen)
  const leeren = useVerlaufStore((s) => s.verlaufLeeren)

  const datum = (e: Eintrag, vorher: Eintrag | undefined) =>
    vorher && tag(vorher.zeit) === tag(e.zeit) ? null : (
      <p className="px-2 pb-1 pt-3 text-label-sm text-on-surface-variant">
        {new Date(e.zeit).toLocaleDateString(i18n.language, { weekday: 'long', day: 'numeric', month: 'long' })}
      </p>
    )

  const allesLoeschen = async () => {
    const ja = await confirm({
      title: t('browser.verlauf.leerenTitel'),
      message: t('browser.verlauf.leerenText'),
      confirmText: t('browser.verlauf.leeren'),
      danger: true,
    })
    if (ja) leeren()
  }

  return (
    <Eintragsliste
      eintraege={verlauf}
      leer={t('browser.verlauf.leer')}
      entfernen={(e) => entfernen(e.url, e.zeit)}
      ueberschrift={datum}
      kopf={
        verlauf.length > 0 && (
          <Button variant="ghost" size="sm" className="self-end" onClick={() => void allesLoeschen()}>
            {t('browser.verlauf.leeren')}
          </Button>
        )
      }
    />
  )
}
