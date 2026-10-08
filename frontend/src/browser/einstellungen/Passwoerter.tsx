/**
 * Was die Leiste über der Seite anbietet (`FormularLeiste.tsx`). Ohne
 * Kopplung gibt es keinen Tresor und damit nichts davon.
 */
import { useTranslation } from 'react-i18next'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { istAndroid } from '../services/plattform'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import { Abschnitt, Schalterzeile } from './bausteine'

export function Passwoerter() {
  const { t } = useTranslation()
  const gekoppelt = istGekoppelt(useSitzung((s) => s.stand))
  const ausfuellen = useEinstellungenStore((s) => s.ausfuellen)
  const erzeugen = useEinstellungenStore((s) => s.erzeugen)
  const zahlungen = useEinstellungenStore((s) => s.zahlungen)
  const setzen = useEinstellungenStore((s) => s.setzen)
  return (
    <Abschnitt titel={t('browser.einstellungen.kategorie.passwoerter')}>
      {!gekoppelt && <p className="text-body-sm text-on-surface-variant">{t('browser.einstellungen.brauchtKopplung')}</p>}
      <Schalterzeile name={t('browser.einstellungen.ausfuellen')} an={ausfuellen} aendern={(v) => setzen({ ausfuellen: v })} />
      <Schalterzeile
        name={t('browser.einstellungen.erzeugen')}
        hinweis={t('browser.einstellungen.erzeugenHinweis')}
        an={erzeugen}
        aendern={(v) => setzen({ erzeugen: v })}
      />
      <Schalterzeile
        name={t('browser.einstellungen.zahlungen')}
        hinweis={t(istAndroid() ? 'browser.einstellungen.zahlungenHinweisAndroid' : 'browser.einstellungen.zahlungenHinweis')}
        an={zahlungen}
        aendern={(v) => setzen({ zahlungen: v })}
      />
    </Abschnitt>
  )
}
