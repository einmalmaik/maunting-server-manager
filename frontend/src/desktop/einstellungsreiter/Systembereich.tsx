import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'

/** Die drei Stufen, in der Reihenfolge, in der sie mehr erlauben. */
const SYSTEMBEREICHE = ['aus', 'lesen', 'schreiben'] as const
type Systembereichswert = (typeof SYSTEMBEREICHE)[number]

/**
 * Wie weit die KI in Windows selbst greifen darf.
 *
 * Der Wert liegt am **Konto**, nicht in `konfig.json`: das Panel muss ihn
 * kennen, wenn es einen Aufräumauftrag zusammenstellt, und ein Wert, den nur
 * dieser Rechner kennt, wäre für einen Auftrag aus dem Panel unsichtbar.
 * Deshalb hier eine Panelabfrage und kein Rust-Command.
 *
 * Ausserhalb dieses Bereichs — im eigenen Profil, auf Datenlaufwerken —
 * arbeitet die KI ohne diese Einstellung; die Bestätigungsfrage dort hängt
 * allein am autonomen Modus. Diese Stufen gelten nur für das, was Windows
 * selbst gehört.
 */
export function Systembereich() {
  const { t } = useTranslation()
  const [wert, setWert] = useState<Systembereichswert | null>(null)
  const [sendet, setSendet] = useState(false)

  useEffect(() => {
    void api<{ systembereich: Systembereichswert }>('/ai/settings/desktop')
      .then((daten) => setWert(daten.systembereich))
      // Kein Recht, kein Panel, keine Anmeldung: dann gibt es hier nichts zu
      // entscheiden, und ein Fehlertoast wäre nur Lärm.
      .catch(() => setWert(null))
  }, [])

  async function waehlen(neu: Systembereichswert) {
    if (sendet || neu === wert) return
    const vorher = wert
    setWert(neu)
    setSendet(true)
    try {
      await api('/ai/settings/desktop', {
        method: 'PUT',
        body: JSON.stringify({ systembereich: neu }),
      })
    } catch {
      setWert(vorher)
      toast.error(t('mss.systembereich.fehler'))
    } finally {
      setSendet(false)
    }
  }

  if (wert === null) {
    return null
  }

  return (
    <div className="border-t border-outline-variant/40 pt-4">
      <p className="mb-3 text-sm text-on-surface">{t('mss.systembereich.titel')}</p>
      <div className="flex flex-wrap gap-2">
        {SYSTEMBEREICHE.map((stufe) => (
          <button
            key={stufe}
            disabled={sendet}
            onClick={() => void waehlen(stufe)}
            className={`rounded-lg border px-3.5 py-2 text-sm transition-colors ${
              wert === stufe
                ? 'border-primary/40 bg-primary/10 text-primary'
                : 'border-outline-variant/40 bg-surface-container-low/40 text-on-surface-variant hover:text-on-surface'
            }`}
          >
            {t(`mss.systembereich.stufe.${stufe}`)}
          </button>
        ))}
      </div>
    </div>
  )
}
