/**
 * Die Bestätigungskarte für allgemeine Desktop-Aktionen bei inaktivem autonomem Modus.
 *
 * Gemäß Maunting Studios Grundsatz („Sicherheit braucht Vertrauen“):
 * Jede einzelne Aktion (Screen-Reading, Dateien, Programme, Eingaben)
 * erfordert eine manuelle, transparente Bestätigung durch den Benutzer.
 *
 * Nur zwei eindeutige Optionen: „Ja / Bestätigen“ oder „Nein / Ablehnen“.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { listen } from '@tauri-apps/api/event'
import { AppWindow, Eye, FileText, MousePointer, ShieldAlert, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { ergebnisMelden } from './desktopJobs'
import {
  desktopAktionAblehnen,
  desktopAktionBestaetigen,
  type DesktopAktionAnfrage,
} from './tauri'

export const EREIGNIS_AKTION = 'mss:aktion-anfrage'

function iconFuerWerkzeug(werkzeug: string) {
  switch (werkzeug) {
    case 'desktop_system':
      return Eye
    case 'desktop_dateien':
      return FileText
    case 'desktop_launch_app':
      return AppWindow
    case 'desktop_steuern':
      return MousePointer
    default:
      return Wrench
  }
}

export function DesktopAktionKarte({
  offenerAuftragId,
}: {
  offenerAuftragId: string | null
}) {
  const { t } = useTranslation()
  const [anfrage, setAnfrage] = useState<DesktopAktionAnfrage | null>(null)
  const [busy, setBusy] = useState(false)

  const rueckfallId = useRef<string | null>(null)
  useEffect(() => {
    rueckfallId.current = offenerAuftragId
  }, [offenerAuftragId])

  useEffect(() => {
    const abmelden = listen<DesktopAktionAnfrage>(EREIGNIS_AKTION, (ereignis) => {
      const auftragId = ereignis.payload.auftrag_id || rueckfallId.current
      if (!auftragId) return
      setAnfrage({
        auftrag_id: auftragId,
        werkzeug: ereignis.payload.werkzeug,
        titel: ereignis.payload.titel,
        beschreibung: ereignis.payload.beschreibung,
        argumente: ereignis.payload.argumente || {},
      })
    })
    return () => {
      void abmelden.then((weg) => weg())
    }
  }, [])

  const entscheiden = useCallback(
    async (bestaetigt: boolean) => {
      if (!anfrage || busy) return
      setBusy(true)
      try {
        if (bestaetigt) {
          const ergebnis = await desktopAktionBestaetigen(anfrage.auftrag_id)
          await ergebnisMelden(anfrage.auftrag_id, true, ergebnis)
        } else {
          await desktopAktionAblehnen(anfrage.auftrag_id)
          await ergebnisMelden(
            anfrage.auftrag_id,
            false,
            {
              abgewiesen: true,
              grund: 'Der Benutzer hat die Ausführung dieser Aktion abgelehnt.',
            },
            'DESKTOP_ACTION_REJECTED',
          )
        }
      } catch (fehler) {
        const text = fehler instanceof Error ? fehler.message : String(fehler)
        await ergebnisMelden(
          anfrage.auftrag_id,
          false,
          { fehler: text },
          'DESKTOP_TOOL_FAILED',
        )
      } finally {
        setBusy(false)
        setAnfrage(null)
      }
    },
    [anfrage, busy],
  )

  if (!anfrage) return null

  const Icon = iconFuerWerkzeug(anfrage.werkzeug)

  return (
    <div
      className="msm-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={anfrage.titel}
    >
      <div className="msm-card w-full max-w-lg p-6 shadow-panel">
        <div className="flex items-start gap-3">
          <div className="rounded-lg bg-primary/10 p-2.5 text-primary shrink-0">
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="font-headline text-lg font-semibold text-on-surface">
                {anfrage.titel}
              </h2>
              <span className="inline-flex items-center gap-1 rounded-full bg-status-warning/10 px-2 py-0.5 text-xs font-medium text-status-warning">
                <ShieldAlert className="h-3 w-3" aria-hidden="true" />
                {t('mss.aktion.freigabeErforderlich', 'Bestätigung erforderlich')}
              </span>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-on-surface-variant">
              {anfrage.beschreibung}
            </p>
          </div>
        </div>

        <div className="mt-6 flex items-center justify-end gap-3 border-t border-outline-variant/30 pt-4">
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => void entscheiden(false)}
          >
            {t('mss.aktion.ablehnen', 'Nein, ablehnen')}
          </Button>
          <Button
            variant="primary"
            autoFocus
            disabled={busy}
            onClick={() => void entscheiden(true)}
          >
            {t('mss.aktion.bestaetigen', 'Ja, ausführen')}
          </Button>
        </div>
      </div>
    </div>
  )
}
