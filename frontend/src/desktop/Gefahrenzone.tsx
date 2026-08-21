/**
 * Deinstallation aus der App heraus — in zwei Schritten, nicht in einem.
 *
 * Erst räumt sie die lokalen Spuren ab (Konfiguration, Stimmaufnahmen,
 * Tresor-Eintrag, Autostart) und **zeigt einzeln**, was davon geklappt hat.
 * Erst danach darf der Windows-Uninstaller starten. Ein Knopf, der beides
 * zusammen täte, ließe niemanden nachlesen, ob seine Aufnahmen wirklich weg
 * sind — und genau das ist hier die Frage, die zählt.
 *
 * Der Sandbox-Ordner bleibt. Er gehört dem Benutzer, nicht der App.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import {
  deinstallationAufraeumen,
  deinstallationStarten,
  type Aufraeumbericht,
} from './tauri'

type Schritt = 'ruhe' | 'gefragt' | 'aufgeraeumt'

export function Gefahrenzone() {
  const { t } = useTranslation()
  const [schritt, setSchritt] = useState<Schritt>('ruhe')
  const [bericht, setBericht] = useState<Aufraeumbericht | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

  async function aufraeumen() {
    setFehler(null)
    try {
      setBericht(await deinstallationAufraeumen())
      setSchritt('aufgeraeumt')
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e))
    }
  }

  async function starten() {
    setFehler(null)
    try {
      await deinstallationStarten()
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="msm-card flex flex-col gap-4 border-status-destructive/40 p-5">
      <div>
        <h2 className="text-sm font-medium text-status-destructive">
          {t('mss.gefahrenzone.titel')}
        </h2>
        <p className="mt-1 text-xs text-on-surface-variant">
          {t('mss.gefahrenzone.erklaerung')}
        </p>
      </div>

      {schritt === 'ruhe' && (
        <div>
          <Button variant="destructive" onClick={() => setSchritt('gefragt')}>
            {t('mss.gefahrenzone.deinstallieren')}
          </Button>
        </div>
      )}

      {schritt === 'gefragt' && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-on-surface">{t('mss.gefahrenzone.sicherheitsfrage')}</p>
          <div className="flex gap-2">
            <Button variant="destructive" onClick={() => void aufraeumen()}>
              {t('mss.gefahrenzone.allesEntfernen')}
            </Button>
            <Button variant="secondary" onClick={() => setSchritt('ruhe')}>
              {t('mss.gefahrenzone.abbrechen')}
            </Button>
          </div>
        </div>
      )}

      {schritt === 'aufgeraeumt' && bericht && (
        <div className="flex flex-col gap-3">
          <ul className="text-sm">
            <Zeile ok={bericht.konfiguration_entfernt} text={t('mss.gefahrenzone.konfiguration')} />
            <Zeile ok={bericht.sprachdaten_entfernt} text={t('mss.gefahrenzone.stimmaufnahmen')} />
            <Zeile ok={bericht.tresor_geleert} text={t('mss.gefahrenzone.anmeldung')} />
            <Zeile ok={bericht.autostart_entfernt} text={t('mss.gefahrenzone.autostart')} />
          </ul>
          {bericht.sandbox_bleibt && (
            <p className="text-xs text-on-surface-variant">
              {t('mss.gefahrenzone.sandboxBleibt', { pfad: bericht.sandbox_bleibt })}
            </p>
          )}
          {bericht.fehler.length > 0 && (
            <ul className="text-xs text-status-destructive">
              {bericht.fehler.map((text, i) => (
                <li key={i}>{text}</li>
              ))}
            </ul>
          )}
          <div>
            <Button variant="destructive" onClick={() => void starten()}>
              {t('mss.gefahrenzone.uninstallerStarten')}
            </Button>
          </div>
        </div>
      )}

      {fehler && <p className="msm-field-error">{fehler}</p>}
    </section>
  )
}

function Zeile({ ok, text }: { ok: boolean; text: string }) {
  return (
    <li className={ok ? 'text-on-surface' : 'text-status-destructive'}>
      {ok ? '✓' : '✗'} {text}
    </li>
  )
}
