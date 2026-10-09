/**
 * Was statt einer Seite steht, die der Jugend- und Suchtschutz sperrt
 * (`schild/sperre.rs`): ruhig, mit dem Grund und einem Weg zurück. Die
 * Einstellungen sind einen Klick entfernt; lockern geht dort nur über die
 * Hürde.
 */
import { ShieldBan } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Zustandsflaeche } from '@/Singra/UI'

import { seitenHost } from '../services/geraetKonfig'
import { useTabsStore, type Tab } from '../services/tabsStore'

export function Sperrseite({ tab }: { tab: Tab }) {
  const { t } = useTranslation()
  const aktion = useTabsStore((s) => s.aktion)
  const startseite = useTabsStore((s) => s.startseite)
  const einstellungen = useTabsStore((s) => s.einstellungen)
  const host = seitenHost(tab.url) ?? tab.url
  const text =
    tab.gesperrt === 'eigene'
      ? t('browser.schutz.gesperrtEigene', { host })
      : tab.gesperrt === 'adresse'
        ? t('browser.schutz.gesperrtAdresse', { host })
        : t('browser.schutz.gesperrtText', { host, kategorie: t(`browser.schutz.kategorie.${tab.gesperrt}`, { defaultValue: '' }) })

  return (
    <div className="flex h-full items-center justify-center p-6">
      <Zustandsflaeche art="leer" ansagen icon={<ShieldBan className="h-10 w-10" />} titel={t('browser.schutz.gesperrtTitel')} text={text}>
        <div className="flex flex-wrap justify-center gap-2">
          <Button type="button" variant="secondary" onClick={() => (tab.zurueck || tab.vorher ? aktion('zurueck', tab.id) : startseite(tab.id))}>
            {tab.zurueck || tab.vorher ? t('browser.nav.zurueck') : t('browser.schutz.zurStartseite')}
          </Button>
          <Button type="button" variant="ghost" onClick={() => einstellungen('jugendschutz')}>
            {t('browser.schutz.einstellungen')}
          </Button>
        </div>
      </Zustandsflaeche>
    </div>
  )
}
