/**
 * Die Kopplung mit MSM. Welche Module zu sehen sind, steht unter Design
 * (`Anordnung.tsx`), zusammen mit den übrigen Einträgen.
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Button, confirm } from '@/Singra/UI'
import { abmelden } from '@/desktop/auth'

import { istGekoppelt, useSitzung } from '../services/sitzung'
import { Abschnitt, Aktionszeile } from './bausteine'

function Kopplung() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const stand = useSitzung((s) => s.stand)
  const adresse = useSitzung((s) => s.adresse)

  if (!istGekoppelt(stand)) {
    return (
      <Aktionszeile name={t('browser.einstellungen.nichtGekoppelt')}>
        <Button variant="secondary" size="sm" onClick={() => navigate('/koppeln')}>
          {t('browser.leiste.koppeln')}
        </Button>
      </Aktionszeile>
    )
  }

  const trennen = async () => {
    const ja = await confirm({
      title: t('browser.einstellungen.trennenTitel'),
      message: t('browser.einstellungen.trennenText'),
      confirmText: t('browser.einstellungen.trennen'),
      danger: true,
    })
    if (ja) await abmelden()
  }

  return (
    <Aktionszeile
      name={stand === 'an' ? t('browser.einstellungen.gekoppeltMit', { adresse }) : t('browser.einstellungen.gekoppeltOffline', { adresse })}
    >
      <Button variant="secondary" size="sm" onClick={() => void trennen()}>
        {t('browser.einstellungen.trennen')}
      </Button>
    </Aktionszeile>
  )
}

export function Dienste() {
  const { t } = useTranslation()
  return (
    <Abschnitt titel={t('browser.einstellungen.kopplung')}>
      <Kopplung />
    </Abschnitt>
  )
}
