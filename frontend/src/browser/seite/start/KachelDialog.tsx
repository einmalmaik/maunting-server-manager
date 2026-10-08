/**
 * Neue Kachel oder eine bestehende ändern: Adresse und Name. Angenommen wird
 * nur eine Webadresse (`https://`, `http://` auf dem eigenen Rechner oder eine
 * Domain); Zugangsdaten in der Adresse lehnt der Dialog ab, sie lägen sonst im
 * Klartext in den Einstellungen.
 */
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, Input } from '@/Singra/UI'

import type { Schnellzugriff } from '../../services/einstellungenStore'
import { baueZielUrl, istAdresse } from '../../services/searchEngines'

const ADRESSE_MAX = 2048
export const NAME_MAX = 80

/** Die Adresse, die gespeichert wird, oder `null`, wenn die Eingabe keine Webadresse ist. */
export function kachelAdresse(eingabe: string): string | null {
  const text = eingabe.trim()
  if (!istAdresse(text) || text.length > ADRESSE_MAX) return null
  const ziel = baueZielUrl(text, 'duckduckgo')
  if (!ziel) return null
  try {
    const u = new URL(ziel)
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return null
    if (u.username || u.password) return null
    return u.href
  } catch {
    return null
  }
}

export function KachelDialog({
  vorlage,
  schliessen,
  speichern,
}: {
  vorlage?: Schnellzugriff
  schliessen: () => void
  speichern: (kachel: Schnellzugriff) => void
}) {
  const { t } = useTranslation()
  const [adresse, setAdresse] = useState(vorlage?.url ?? '')
  const [name, setName] = useState(vorlage?.titel ?? '')
  const [fehler, setFehler] = useState<string | null>(null)
  const titelId = useId()

  const absenden = () => {
    const url = kachelAdresse(adresse)
    if (!url) {
      setFehler(t('browser.start.adresseUngueltig'))
      return
    }
    speichern({ url, titel: name.trim().slice(0, NAME_MAX) || new URL(url).hostname.replace(/^www\./, '') })
  }

  return (
    <Dialog open onOpenChange={(offen) => !offen && schliessen()}>
      <DialogContent className="max-w-md" aria-labelledby={titelId}>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            absenden()
          }}
        >
          <DialogHeader className="px-5 py-4 pr-14">
            <DialogTitle id={titelId} className="text-title-md">
              {vorlage ? t('browser.start.bearbeitenTitel') : t('browser.start.hinzufuegenTitel')}
            </DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-3 px-5 py-4">
            <Input
              label={t('browser.start.adresse')}
              value={adresse}
              onChange={(e) => {
                setAdresse(e.target.value)
                setFehler(null)
              }}
              placeholder="example.com"
              error={fehler ?? undefined}
              autoComplete="off"
              spellCheck={false}
              autoFocus
            />
            <Input label={t('browser.start.name')} value={name} onChange={(e) => setName(e.target.value)} maxLength={NAME_MAX} autoComplete="off" />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={schliessen}>
              {t('common.cancel')}
            </Button>
            <Button type="submit">{t('common.save')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
