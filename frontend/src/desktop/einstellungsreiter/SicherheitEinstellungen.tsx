/**
 * Gemeinsame Sicherheits-Einstellungen (Messenger-PIN und Tresor-Sicherheit).
 *
 * Geteilt zwischen der Desktop-App (MSS) und dem Browser (MSB): Darstellung,
 * Kartenreihenfolge, Texte, Validierung und Store-Aktionen kommen aus denselben
 * Komponenten (`MessengerSicherheitTab` und `TresorSicherheitTab`).
 */
import { useTranslation } from 'react-i18next'

import { MessengerSicherheitTab } from '@/pages/profile/MessengerSicherheitTab'
import { TresorSicherheitTab } from '../vault/TresorSicherheitTab'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'

const GRUPPENTITEL = 'font-headline text-title-md font-semibold text-on-surface'

export interface SicherheitEinstellungenProps {
  offline?: boolean
  social?: boolean
  tresorAn?: boolean
}

export function SicherheitEinstellungen({
  offline = false,
  social: socialProp,
  tresorAn: tresorAnProp,
}: SicherheitEinstellungenProps = {}) {
  const { t } = useTranslation()
  const publicSettings = usePublicSettingsStore()
  const social = socialProp ?? publicSettings.social_enabled
  const tresorAn = tresorAnProp ?? publicSettings.vault_enabled

  return (
    <>
      {/* Zwei Gruppen mit eigener Überschrift: sonst beginnt beim
          Scrollen mitten in den Messenger-Karten plötzlich der Tresor. */}
      {social && (
        <section aria-labelledby="sicherheit-messenger" className="flex flex-col gap-3">
          <h2 id="sicherheit-messenger" className={GRUPPENTITEL}>
            {t('mss.einstellungen.sicherheit.messenger')}
          </h2>
          <MessengerSicherheitTab />
        </section>
      )}
      {tresorAn && (
        <section aria-labelledby="sicherheit-tresor" className="flex flex-col gap-3 pt-2">
          <h2 id="sicherheit-tresor" className={GRUPPENTITEL}>
            {t('mss.einstellungen.sicherheit.tresor')}
          </h2>
          <TresorSicherheitTab offline={offline} />
        </section>
      )}
    </>
  )
}
