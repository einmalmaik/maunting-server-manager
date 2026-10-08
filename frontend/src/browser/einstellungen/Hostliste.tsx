/**
 * Eine Liste von Domains zum Ergänzen und Entfernen: „Immer wach“ unter
 * Leistung, eigene Sperren und Ausnahmen im Jugend- und Suchtschutz. Was
 * hineinkommt, geht durch `ausnahmeHost`: eine Domain, keine Adresse.
 */
import { useState } from 'react'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Input, Kurzinfo } from '@/Singra/UI'

import { ausnahmeHost } from '../services/leistung'
import { Abschnitt } from './bausteine'

export function Hostliste({
  titel,
  hinweis,
  eintraege,
  setzen,
  max,
}: {
  titel: string
  hinweis: string
  eintraege: string[]
  setzen: (liste: string[]) => void
  max: number
}) {
  const { t } = useTranslation()
  const [eingabe, setEingabe] = useState('')
  const [fehler, setFehler] = useState<string | null>(null)

  const hinzufuegen = () => {
    const host = ausnahmeHost(eingabe)
    if (!host) {
      setFehler(t('browser.einstellungen.ausnahmeUngueltig'))
      return
    }
    if (!eintraege.includes(host)) {
      if (eintraege.length >= max) {
        setFehler(t('browser.einstellungen.listeVoll', { max }))
        return
      }
      setzen([...eintraege, host])
    }
    setEingabe('')
  }

  return (
    <Abschnitt titel={titel}>
      <p className="-mt-2 text-label-sm text-on-surface-variant">{hinweis}</p>
      <form
        className="flex items-start gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          hinzufuegen()
        }}
      >
        <div className="min-w-0 flex-1">
          <Input
            aria-label={t('browser.einstellungen.ausnahmeSeite')}
            placeholder="example.com"
            value={eingabe}
            onChange={(e) => {
              setEingabe(e.target.value)
              setFehler(null)
            }}
            error={fehler ?? undefined}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <Button type="submit" variant="secondary">
          {t('browser.einstellungen.ausnahmeHinzufuegen')}
        </Button>
      </form>
      {eintraege.length > 0 && (
        <ul className="flex flex-col gap-1">
          {eintraege.map((host) => (
            <li key={host} className="flex items-center justify-between rounded-lg bg-surface-container px-3 py-1.5 text-body-sm">
              <span className="truncate">{host}</span>
              <Kurzinfo text={t('browser.einstellungen.eintragEntfernen')} seite="ende">
                <button
                  type="button"
                  onClick={() => setzen(eintraege.filter((h) => h !== host))}
                  aria-label={t('browser.einstellungen.eintragEntfernenName', { host })}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high hover:text-status-destructive"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </Kurzinfo>
            </li>
          ))}
        </ul>
      )}
    </Abschnitt>
  )
}
