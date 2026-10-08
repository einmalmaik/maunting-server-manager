import { useTranslation } from 'react-i18next'

import { useVerlaufStore } from '../services/verlaufStore'
import { Eintragsliste } from './Eintragsliste'

export function LesezeichenPanel() {
  const { t } = useTranslation()
  const lesezeichen = useVerlaufStore((s) => s.lesezeichen)
  const entfernen = useVerlaufStore((s) => s.lesezeichenEntfernen)
  return <Eintragsliste eintraege={lesezeichen} leer={t('browser.lesezeichen.leer')} entfernen={(e) => entfernen(e.url)} />
}
