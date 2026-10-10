/**
 * Erweiterungen verwalten (nur Windows): aus dem Chrome Web Store
 * installieren, im Entwicklermodus einen entpackten Ordner laden, schalten,
 * entfernen. Vor jeder Installation steht die Rückfrage mit den Rechten.
 */
import { useEffect, useId, useState } from 'react'
import { open as ordnerWaehlen } from '@tauri-apps/plugin-dialog'
import { Puzzle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, confirm, Input, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { erweiterungen, fehlerText, useErweiterungen, type Erweiterung, type Vorschau } from '../erweiterungen/erweiterungen'
import { Rechte, Rueckfrage } from '../erweiterungen/Rueckfrage'
import { Abschnitt, Aktionszeile, Schalterzeile } from './bausteine'

function Eintrag({ e, gesperrt }: { e: Erweiterung; gesperrt: boolean }) {
  const { t } = useTranslation()
  const nameId = useId()
  const [rechte, setRechte] = useState(false)

  const ausfuehren = (lauf: Promise<unknown>) => void lauf.catch((f) => toast.error(fehlerText(t, f)))
  const entfernen = async () => {
    const ja = await confirm({
      title: t('browser.erweiterungen.entfernenTitel', { name: e.angaben.name }),
      message: t('browser.erweiterungen.entfernenText'),
      confirmText: t('browser.erweiterungen.entfernen'),
      danger: true,
    })
    if (ja) ausfuehren(erweiterungen.entfernen(e.id))
  }

  return (
    <li className="flex flex-col gap-2 rounded-xl bg-surface-container p-3">
      <div className="flex items-center gap-3">
        {e.angaben.symbol ? (
          <img src={e.angaben.symbol} alt="" className="h-8 w-8 shrink-0" />
        ) : (
          <Puzzle className="h-8 w-8 shrink-0 text-on-surface-variant" aria-hidden="true" />
        )}
        <div className="min-w-0 flex-1">
          <p id={nameId} className="truncate text-body-sm text-on-surface">
            {e.angaben.name}
          </p>
          <p className="truncate text-label-sm text-on-surface-variant">
            {t(`browser.erweiterungen.herkunft.${e.herkunft}`, { version: e.angaben.version })}
            {e.an && !e.laeuft && ` · ${t('browser.erweiterungen.angehalten')}`}
          </p>
        </div>
        <Switch aria-labelledby={nameId} checked={e.an} disabled={gesperrt && !e.an} onCheckedChange={(an) => ausfuehren(erweiterungen.schalten(e.id, an))} />
      </div>
      {e.angaben.beschreibung && <p className="text-label-sm text-on-surface-variant">{e.angaben.beschreibung}</p>}
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" onClick={() => setRechte((r) => !r)} aria-expanded={rechte}>
          {t('browser.erweiterungen.rechte')}
        </Button>
        {e.angaben.optionen && (
          <Button variant="ghost" size="sm" disabled={!e.laeuft} onClick={() => ausfuehren(erweiterungen.optionen(e.id))}>
            {t('browser.erweiterungen.optionen')}
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={() => ausfuehren(erweiterungen.anheften(e.id, !e.angeheftet))}>
          {e.angeheftet ? t('browser.erweiterungen.losloesen') : t('browser.erweiterungen.anheften')}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => void entfernen()}>
          {t('browser.erweiterungen.entfernen')}
        </Button>
      </div>
      {rechte && <Rechte angaben={e.angaben} />}
    </li>
  )
}

export function Erweiterungen() {
  const { t, i18n } = useTranslation()
  const stand = useErweiterungen((s) => s.stand)
  const [eingabe, setEingabe] = useState('')
  const [pruefung, setPruefung] = useState(false)
  const [vorschau, setVorschau] = useState<Vorschau | null>(null)

  useEffect(() => {
    void useErweiterungen.getState().laden()
  }, [])

  if (!stand) return null
  const gesperrt = stand.jugendschutz

  const pruefen = async (quelle: string, entpackt: boolean) => {
    setPruefung(true)
    try {
      setVorschau(await erweiterungen.pruefen(quelle, entpackt, i18n.language.slice(0, 2)))
      if (!entpackt) setEingabe('')
    } catch (e) {
      toast.error(fehlerText(t, e))
    }
    setPruefung(false)
  }
  const ordnerLaden = async () => {
    const ordner = await ordnerWaehlen({ directory: true, multiple: false }).catch(() => null)
    if (typeof ordner === 'string') await pruefen(ordner, true)
  }
  const entwicklermodus = (an: boolean) => void erweiterungen.entwicklermodus(an).catch((e) => toast.error(fehlerText(t, e)))

  return (
    <>
      {gesperrt && <p className="msm-card p-4 text-body-sm text-on-surface">{t('browser.erweiterungen.jugendschutz')}</p>}
      <Abschnitt titel={t('browser.erweiterungen.ausDemStoreTitel')}>
        <p className="-mt-2 text-label-sm text-on-surface-variant">{t('browser.erweiterungen.storeHinweis')}</p>
        <form
          className="flex items-start gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (eingabe.trim()) void pruefen(eingabe, false)
          }}
        >
          <div className="min-w-0 flex-1">
            <Input
              aria-label={t('browser.erweiterungen.eingabe')}
              placeholder="https://chromewebstore.google.com/detail/…"
              value={eingabe}
              onChange={(e) => setEingabe(e.target.value)}
              disabled={gesperrt || pruefung}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <Button type="submit" variant="secondary" disabled={gesperrt || pruefung || !eingabe.trim()}>
            {pruefung ? t('browser.erweiterungen.wirdGeprueft') : t('browser.erweiterungen.installieren')}
          </Button>
        </form>
      </Abschnitt>
      <Abschnitt titel={t('browser.erweiterungen.installiertTitel')}>
        {stand.eintraege.length === 0 ? (
          <p className="text-body-sm text-on-surface-variant">{t('browser.erweiterungen.leer')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {stand.eintraege.map((e) => (
              <Eintrag key={e.id} e={e} gesperrt={gesperrt} />
            ))}
          </ul>
        )}
        <p className="text-label-sm text-on-surface-variant">{t('browser.erweiterungen.grenzen')}</p>
      </Abschnitt>
      <Abschnitt titel={t('browser.erweiterungen.entwicklerTitel')}>
        <Schalterzeile
          name={t('browser.erweiterungen.entwicklermodus')}
          hinweis={t('browser.erweiterungen.entwicklerHinweis')}
          an={stand.entwicklermodus}
          aendern={(an) => (!an || !gesperrt) && entwicklermodus(an)}
        />
        {stand.entwicklermodus && (
          <Aktionszeile name={t('browser.erweiterungen.ordnerLaden')} hinweis={t('browser.erweiterungen.ordnerHinweis')}>
            <Button variant="secondary" size="sm" disabled={gesperrt || pruefung} onClick={() => void ordnerLaden()}>
              {t('browser.erweiterungen.ordnerWaehlen')}
            </Button>
          </Aktionszeile>
        )}
      </Abschnitt>
      {vorschau && <Rueckfrage vorschau={vorschau} fertig={() => setVorschau(null)} />}
    </>
  )
}
