/**
 * `alert`, `confirm`, `prompt`, Rechte und HTTP-Anmeldung einer Seite, als
 * Dialog des Browsers statt im Stil von Edge. Jeder nennt die Herkunft, damit
 * eine Seite sich nicht als Browser ausgeben kann.
 */
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Input } from '@/Singra/UI'
import { PasswordInput } from '@/components/ui/PasswordInput'

import { herkunftsName, useFrageDesTabs, useRueckfragen, type Rueckfrage } from '../services/rueckfragen'
import { useAktiverTab } from '../services/tabsStore'

/** Längere Texte einer Seite werden gekürzt; ein Dialog ist kein Leseort. */
const TEXT_MAX = 2000

export function SeitenDialoge() {
  const tab = useAktiverTab()
  const frage = useFrageDesTabs(tab?.id)
  if (!frage || frage.art === 'kontextmenue') return null
  // `key`: jede Frage bekommt frische Eingabefelder.
  return <Frage key={frage.nr} frage={frage} />
}

function Frage({ frage }: { frage: Exclude<Rueckfrage, { art: 'kontextmenue' }> }) {
  const { t } = useTranslation()
  const antworten = useRueckfragen((s) => s.antworten)
  const [text, setText] = useState(frage.art === 'dialog' ? frage.vorgabe : '')
  const [benutzer, setBenutzer] = useState('')
  const [passwort, setPasswort] = useState('')
  const herkunft = herkunftsName(frage.herkunft)
  const titelId = useId()
  const textId = useId()

  // Was nur bestätigt, schließt ohne Eingabe; Escape und Zurück heißen „nein“.
  const nein = () => {
    if (frage.art === 'dialog') antworten(frage.nr, { art: 'dialog', ok: false, text: null })
    else if (frage.art === 'recht') antworten(frage.nr, { art: 'recht', erlauben: false })
    else antworten(frage.nr, { art: 'anmeldung', benutzer: null, passwort: null })
  }
  const ja = () => {
    if (frage.art === 'dialog') antworten(frage.nr, { art: 'dialog', ok: true, text: frage.dialog === 'prompt' ? text : null })
    else if (frage.art === 'recht') antworten(frage.nr, { art: 'recht', erlauben: true })
    else antworten(frage.nr, { art: 'anmeldung', benutzer, passwort })
  }

  let titel: string
  let beschreibung: string
  let jaText = t('browser.dialog.ok')
  let neinText: string | null = t('common.cancel')
  if (frage.art === 'dialog') {
    titel = frage.dialog === 'beforeunload' ? t('browser.dialog.verlassenTitel') : t('browser.dialog.titel', { herkunft })
    beschreibung = frage.dialog === 'beforeunload' ? t('browser.dialog.verlassenText') : frage.text.slice(0, TEXT_MAX)
    if (frage.dialog === 'alert') neinText = null
    if (frage.dialog === 'beforeunload') jaText = t('browser.dialog.verlassen')
  } else if (frage.art === 'recht') {
    titel = t('browser.recht.titel', { herkunft })
    beschreibung = t(`browser.recht.art.${frage.recht}`, { defaultValue: t('browser.recht.art.sonstiges') })
    jaText = t('browser.recht.erlauben')
    neinText = t('browser.recht.blockieren')
  } else {
    titel = t('browser.anmeldung.titel', { herkunft })
    beschreibung = frage.bereich ? t('browser.anmeldung.bereich', { bereich: frage.bereich.slice(0, 200) }) : t('browser.anmeldung.text')
    jaText = t('browser.anmeldung.anmelden')
  }

  return (
    <Dialog open onOpenChange={(offen) => !offen && nein()}>
      <DialogContent className="max-w-md" aria-labelledby={titelId} aria-describedby={textId}>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            ja()
          }}
        >
          <DialogHeader className="px-5 py-4 pr-14">
            <DialogTitle id={titelId} className="text-title-md">{titel}</DialogTitle>
            <DialogDescription id={textId} className="whitespace-pre-wrap break-words text-body-sm">{beschreibung}</DialogDescription>
          </DialogHeader>
          {frage.art === 'dialog' && frage.dialog === 'prompt' && (
            <div className="px-5 py-4">
              <Input aria-label={t('browser.dialog.eingabe')} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
            </div>
          )}
          {frage.art === 'anmeldung' && (
            <div className="flex flex-col gap-3 px-5 py-4">
              <Input label={t('browser.anmeldung.benutzer')} value={benutzer} onChange={(e) => setBenutzer(e.target.value)} autoComplete="off" autoFocus />
              <PasswordInput label={t('browser.anmeldung.passwort')} value={passwort} onChange={(e) => setPasswort(e.target.value)} autoComplete="off" />
            </div>
          )}
          <DialogFooter>
            {neinText && (
              <Button type="button" variant="ghost" onClick={nein}>
                {neinText}
              </Button>
            )}
            <Button type="submit" autoFocus={frage.art !== 'anmeldung' && !(frage.art === 'dialog' && frage.dialog === 'prompt')}>
              {jaText}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
