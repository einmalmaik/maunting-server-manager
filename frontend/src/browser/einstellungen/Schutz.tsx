/**
 * Datenschutz und Sicherheit: Schild samt Ausnahmen, Vergessen beim
 * Schließen und was Seiten erlaubt oder verboten bekamen. Schild und
 * Vergessen liest Rust (`konfig.rs`), die Rechte liegen im Seitenprofil
 * (`browserdaten.rs`).
 */
import { useCallback, useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Kurzinfo } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { useGeraetKonfig } from '../services/geraetKonfig'
import { nativ, type Seitenrecht } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { Abschnitt, Schalterzeile } from './bausteine'

function fehlertext(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

function Schild() {
  const { t } = useTranslation()
  const konfig = useGeraetKonfig((s) => s.konfig)
  const aendern = useGeraetKonfig((s) => s.aendern)
  const [listen, setListen] = useState<{ name: string; alter_sekunden: number | null }[]>([])

  useEffect(() => {
    void nativ.schildStand().then((s) => s && setListen(s.listen)).catch(() => null)
  }, [])

  const setzen = (felder: Parameters<typeof aendern>[0]) => aendern(felder).catch((e) => toast.error(fehlertext(e)))
  const ausnahmen = konfig?.schild_ausnahmen ?? []

  return (
    <Abschnitt titel={t('browser.einstellungen.kategorie.schutz')}>
      <Schalterzeile
        name={t('browser.einstellungen.schild')}
        hinweis={t('browser.einstellungen.schildHinweis')}
        an={konfig?.schild_aktiv ?? true}
        aendern={(v) => void setzen({ schild_aktiv: v })}
      />
      {listen.length > 0 && (
        <ul className="-mt-2 text-label-sm text-on-surface-variant">
          {listen.map((l) => (
            <li key={l.name}>
              {l.alter_sekunden === null
                ? t('browser.einstellungen.listeEingebaut', { name: l.name })
                : t('browser.einstellungen.listeStand', { name: l.name, stunden: Math.round(l.alter_sekunden / 3600) })}
            </li>
          ))}
        </ul>
      )}
      {ausnahmen.length > 0 && (
        <div>
          <p className="mb-1 text-body-sm text-on-surface">{t('browser.einstellungen.ausnahmen')}</p>
          <ul className="flex flex-col gap-1">
            {ausnahmen.map((host) => (
              <li key={host} className="flex items-center justify-between rounded-lg bg-surface-container px-3 py-1.5 text-body-sm">
                <span className="truncate">{host}</span>
                <Kurzinfo text={t('browser.einstellungen.ausnahmeEntfernen')} seite="ende">
                  <button
                    type="button"
                    onClick={() => void setzen({ schild_ausnahmen: ausnahmen.filter((h) => h !== host) })}
                    aria-label={t('browser.einstellungen.ausnahmeEntfernenName', { host })}
                    className="flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high hover:text-status-destructive"
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                </Kurzinfo>
              </li>
            ))}
          </ul>
        </div>
      )}
      <Schalterzeile
        name={t('browser.einstellungen.vergessen')}
        hinweis={t(istAndroid() ? 'browser.einstellungen.vergessenHinweisAndroid' : 'browser.einstellungen.vergessenHinweis')}
        an={konfig?.vergessen_beim_schliessen ?? false}
        aendern={(v) => void setzen({ vergessen_beim_schliessen: v })}
      />
    </Abschnitt>
  )
}

function Seitenrechte() {
  const { t } = useTranslation()
  const [rechte, setRechte] = useState<Seitenrecht[] | null>(null)
  const [fehler, setFehler] = useState(false)

  const laden = useCallback(() => {
    setFehler(false)
    nativ
      .seitenrechte()
      .then((r) => setRechte(r ?? []))
      .catch(() => setFehler(true))
  }, [])
  useEffect(laden, [laden])

  const zuruecksetzen = async (r: Seitenrecht) => {
    try {
      await nativ.seitenrechtZuruecksetzen(r.art, r.herkunft)
      setRechte((alt) => alt?.filter((x) => !(x.art === r.art && x.herkunft === r.herkunft)) ?? null)
    } catch (e) {
      toast.error(fehlertext(e))
    }
  }

  return (
    <Abschnitt titel={t('browser.einstellungen.rechte')}>
      {fehler ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-body-sm text-on-surface-variant">{t('browser.einstellungen.rechteFehler')}</p>
          <Button variant="secondary" size="sm" onClick={laden}>
            {t('common.retry')}
          </Button>
        </div>
      ) : rechte === null ? null : rechte.length === 0 ? (
        <p className="text-body-sm text-on-surface-variant">{t('browser.einstellungen.rechteLeer')}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rechte.map((r) => {
            const recht = t(`browser.einstellungen.rechtName.${r.art}`)
            return (
              <li key={`${r.art} ${r.herkunft}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-surface-container px-3 py-2 text-body-sm">
                <span className="min-w-0 flex-1 truncate text-on-surface">{r.herkunft}</span>
                <span className="text-on-surface-variant">{recht}</span>
                <span className={r.erlaubt ? 'text-status-success' : 'text-status-warning'}>
                  {r.erlaubt ? t('browser.einstellungen.rechtErlaubt') : t('browser.einstellungen.rechtBlockiert')}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={t('browser.einstellungen.rechtZuruecksetzenName', { recht, herkunft: r.herkunft })}
                  onClick={() => void zuruecksetzen(r)}
                >
                  {t('browser.einstellungen.rechtZuruecksetzen')}
                </Button>
              </li>
            )
          })}
        </ul>
      )}
    </Abschnitt>
  )
}

export function Schutz() {
  return (
    <>
      <Schild />
      {/* Android gibt Seiten keine Rechte; es gibt nichts zu listen. */}
      {!istAndroid() && <Seitenrechte />}
    </>
  )
}
