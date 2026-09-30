/**
 * Sagt auf Notizen und Kalender, warum dieses Gerät die Einträge der anderen
 * nicht lesen kann — und was zu tun ist.
 *
 * Bis 5.0.3 stand hier nichts. Ein Gerät, das nie freigegeben wurde, schrieb
 * mit eigenem Schlüssel, und in Web und Desktop-App erschienen die Einträge
 * des jeweils anderen als `sv-note-v1:…`. Beim Betreiber lag der
 * Geräteschlüssel hinter dem Messenger-PIN: das Gerät hatte sich nie gemeldet,
 * stand in keiner Freigabeliste, und niemand sah, warum.
 *
 * Gemahnt wird nur, wenn dieses Gerät den Schlüssel des Kontos nicht hat.
 * Dann eine von drei Lagen, in dieser Reihenfolge geprüft:
 * - `gesperrt`: der Geräteschlüssel ist mit dem Messenger-PIN versiegelt.
 * - `freigabe`: das Gerät ist gemeldet, aber nicht freigegeben.
 * - `schluessel`: freigegeben, der gemeinsame Schlüssel ist angefragt und noch
 *   nicht da.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { KeyRound } from 'lucide-react'

import { holeKontoschluessel } from '@/api/notizschluessel'
import { getE2eeGeraete } from '@/api/social'
import { geraetVeroeffentlichen } from '@/services/e2eeGeraet'
import { MessengerVerschlossenError } from '@/services/lokaleVersiegelung'
import { useMessengerSperre } from '@/services/messengerSperre'
import {
  eigenerAbdruck,
  eigenerIstKontoschluessel,
  kontoAbdruckVergessen,
  kontoschluesselAbgleichen,
} from '@/services/notesCalendarCrypto'
import { useAuthStore } from '@/stores/authStore'

export type NotizschluesselLage = 'gesperrt' | 'freigabe' | 'schluessel' | null

export async function notizschluesselLage(kontoId: number): Promise<NotizschluesselLage> {
  // Zuerst: hat dieses Gerät den Schlüssel des Kontos? Dann ist alles gut,
  // gleich ob der Messenger gerade (wieder) gesperrt ist — der sperrt sich
  // nach Frist von selbst, und ein freigegebenes Gerät soll dann nicht mahnen.
  const stand = await kontoschluesselAbgleichen(kontoId)
  if (stand === 'passt' || stand === 'uebernommen') return null
  if ((await eigenerIstKontoschluessel(kontoId)) === true) return null

  // Der Abgleich sagt `unbekannt` auch, wenn er nur nicht prüfen konnte — etwa
  // mit gesperrtem Messenger, der bei jedem Start der App gesperrt ist. Dann
  // entscheidet der Server: nicht erreichbar heisst schweigen, und steht dort
  // der eigene Abdruck, fehlt diesem Gerät nichts. Nur ein Hinweis, keine
  // Vertrauensfrage; wem der Schlüssel gehört, prüft der Abgleich.
  let konto: string | null
  try {
    konto = (await holeKontoschluessel()).abdruck
  } catch {
    return null
  }
  if (konto && konto === (await eigenerAbdruck(kontoId))) return null

  let kennung: string
  try {
    kennung = (await geraetVeroeffentlichen()).kennung
  } catch (e) {
    // Andere Fehler (offline, Server) sind kein Grund zu mahnen.
    return e instanceof MessengerVerschlossenError ? 'gesperrt' : null
  }
  try {
    // Frisch vom Server, nicht aus dem Gedächtnis der Sitzung: die Freigabe
    // kommt von einem anderen Gerät, während diese Seite offen ist.
    const liste = await getE2eeGeraete(kontoId, true)
    if (liste.find((g) => g.device_id === kennung)?.is_approved === false) return 'freigabe'
  } catch {
    return null
  }
  return stand === 'wartet' ? 'schluessel' : null
}

export function NotizschluesselHinweis() {
  const { t } = useTranslation()
  const kontoId = useAuthStore((s) => s.user?.id ?? null)
  const messengerOffen = useMessengerSperre((s) => s.entsperrt)
  const [lage, setLage] = useState<NotizschluesselLage>(null)

  // Zwei Prüfungen können sich überholen; es zählt die zuletzt begonnene.
  const letzte = useRef(0)
  const pruefen = useCallback(async () => {
    if (kontoId === null) return
    const nr = ++letzte.current
    const neu = await notizschluesselLage(kontoId).catch(() => null)
    if (nr === letzte.current) setLage(neu)
  }, [kontoId])

  useEffect(() => {
    void pruefen()
  }, [pruefen, messengerOffen])

  useEffect(() => {
    if (kontoId === null) return
    const beiEreignis = (e: Event) => {
      const art = (e as CustomEvent<{ entity?: string }>).detail?.entity
      if (art === 'e2ee_devices') {
        // Vielleicht gerade freigegeben: dann noch einmal nach dem Schlüssel
        // fragen, statt aus der Frist zu antworten.
        kontoAbdruckVergessen(kontoId)
        void pruefen()
      } else if (art === 'notes' || art === 'note') {
        void pruefen()
      }
    }
    // Der Schlüssel ist angekommen: sofort, nicht erst im nächsten Takt.
    const schluesselDa = () => void pruefen()
    window.addEventListener('msm:sync-event', beiEreignis)
    window.addEventListener('msm:notes-key-updated', schluesselDa)
    return () => {
      window.removeEventListener('msm:sync-event', beiEreignis)
      window.removeEventListener('msm:notes-key-updated', schluesselDa)
    }
  }, [kontoId, pruefen])

  // Solange das Gerät selbst nichts Neues weiß, reicht eine Frage je Minute.
  useEffect(() => {
    if (lage === null) return
    const takt = setInterval(() => void pruefen(), 60_000)
    return () => clearInterval(takt)
  }, [lage, pruefen])

  if (lage === null) return null
  const imPanel = typeof window !== 'undefined' && !('__TAURI_INTERNALS__' in window)

  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-2xl border border-status-warning/30 bg-status-warning/10 p-3 text-sm"
    >
      <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-status-warning" aria-hidden="true" />
      <div className="min-w-0 space-y-1">
        <p className="font-medium text-on-surface">{t(`notes.schluesselLage.${lage}.titel`)}</p>
        <p className="text-on-surface-variant">{t(`notes.schluesselLage.${lage}.text`)}</p>
        {lage === 'gesperrt' && (
          <Link to="/chat" className="inline-block font-medium text-primary hover:underline">
            {t('notes.schluesselLage.gesperrt.link')}
          </Link>
        )}
        {lage === 'freigabe' && imPanel && (
          <Link to="/profile?tab=devices" className="inline-block font-medium text-primary hover:underline">
            {t('notes.schluesselLage.freigabe.link')}
          </Link>
        )}
      </div>
    </div>
  )
}

