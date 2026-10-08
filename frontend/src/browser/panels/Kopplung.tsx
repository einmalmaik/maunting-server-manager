/**
 * Kopplung mit dem MSM-Panel, mit denselben Schritten wie in MSS
 * (`desktop/Wizard.tsx`): erst die Adresse, dann der Code aus dem Panel.
 * Die Adresse zu speichern lädt die Oberfläche neu; die Tabs bleiben.
 *
 * `grund` nennt das Modul, das gerade die Kopplung braucht.
 */
import { useState } from 'react'
import { Link2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { abmelden } from '@/desktop/auth'
import { SchrittBackend, SchrittKopplung } from '@/desktop/Wizard'

import { MSM_EINTRAEGE, type PanelId } from '../leiste/module'
import { useGeraetKonfig } from '../services/geraetKonfig'
import { useSitzung } from '../services/sitzung'

export function KopplungPanel({ grund }: { grund?: PanelId }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const stand = useSitzung((s) => s.stand)
  const adresse = useSitzung((s) => s.adresse)
  const [adresseAendern, setAdresseAendern] = useState(false)
  const modul = MSM_EINTRAEGE.find((e) => e.id === grund)

  const gekoppelt = async () => {
    await useGeraetKonfig.getState().laden()
    useSitzung.getState().setzen('an')
    navigate(grund ? `/${grund}` : '/')
  }

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-4 flex items-start gap-3">
        <Link2 className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
        <div>
          <p className="text-title-sm text-on-surface">
            {modul ? t('browser.kopplung.titelModul', { modul: t(modul.name) }) : t('browser.kopplung.titel')}
          </p>
          <p className="mt-1 text-body-sm text-on-surface-variant">{t('browser.kopplung.erklaerung')}</p>
        </div>
      </div>

      {stand === 'abgelehnt' && (
        <div className="mb-4 rounded-md border border-status-warning/40 bg-status-warning/10 p-3 text-body-sm">
          <p>{t('browser.kopplung.abgelehnt')}</p>
          <Button variant="secondary" size="sm" className="mt-2" onClick={() => void abmelden()}>
            {t('browser.kopplung.vergessen')}
          </Button>
        </div>
      )}

      {!adresse || adresseAendern ? (
        <SchrittBackend stand={{ backend_url: adresse }} />
      ) : (
        <SchrittKopplung stand={{ backend_url: adresse }} onWeiter={gekoppelt} onZurueck={() => setAdresseAendern(true)} />
      )}
    </div>
  )
}
