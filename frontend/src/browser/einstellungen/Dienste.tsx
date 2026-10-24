/**
 * Die Kopplung mit MSM. Welche Module zu sehen sind, steht unter Design
 * (`Anordnung.tsx`), zusammen mit den übrigen Einträgen.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
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

function Wetterdienst() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<{ sidecar_laeuft: boolean; bereit: boolean } | null>(null)
  useEffect(() => {
    let weg = false
    api<{ sidecar_laeuft: boolean; bereit: boolean }>('/browser/wetter/konfig').then(
      (antwort) => {
        if (!weg) setStatus(antwort)
      },
      () => {
        if (!weg) setStatus({ sidecar_laeuft: false, bereit: false })
      },
    )
    return () => {
      weg = true
    }
  }, [])
  const text =
    status === null
      ? t('browser.einstellungen.wetterWirdGeprueft')
      : status.bereit
        ? t('browser.einstellungen.wetterBereit')
        : status.sidecar_laeuft
          ? t('browser.einstellungen.wetterWirdVorbereitet')
          : t('browser.einstellungen.wetterNichtErreichbar')
  return (
    <Abschnitt titel={t('browser.einstellungen.wetterDienst')}>
      <Aktionszeile name={t('browser.einstellungen.wetterSidecar')} hinweis={t('browser.einstellungen.wetterSidecarHinweis')}>
        <span className={status?.bereit ? 'text-status-success' : 'text-on-surface-variant'}>{text}</span>
      </Aktionszeile>
    </Abschnitt>
  )
}

export function Dienste() {
  const { t } = useTranslation()
  return (
    <>
      <Abschnitt titel={t('browser.einstellungen.kopplung')}>
        <Kopplung />
      </Abschnitt>
      <Wetterdienst />
    </>
  )
}
