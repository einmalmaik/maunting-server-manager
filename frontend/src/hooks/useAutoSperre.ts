/**
 * Meldet die Ereignisse an, aus denen die automatische Sperre ihren Takt zieht.
 *
 * Der Block stand bis 09/2026 wörtlich in `DesktopApp` und in Teilen noch
 * einmal in `VaultView`, beide Male fest auf den Tresor verdrahtet. Seit der
 * Messenger einen PIN hat, gibt es zwei Bewacher, und zwei Abschriften wären
 * die Sorte Duplikat, bei dem irgendwann nur eine repariert wird.
 *
 * Der Haken liegt in `hooks/` und nicht unter `desktop/`: die Web-Oberfläche
 * braucht ihn genauso. Ein Browser-Tab, der offen liegen bleibt, ist derselbe
 * Fall wie ein Fenster, das jemand wegklickt.
 */

import { useEffect } from 'react'

import { PRUEFTAKT_MS, type AutoSperrQuelle } from '@/services/autoSperre'

/** Womit ein Mensch zeigt, dass er noch da ist. */
const AKTIVITAETS_EREIGNISSE = [
  'mousemove',
  'mousedown',
  'keydown',
  'touchstart',
  'scroll',
  'pointerdown',
] as const

export function useAutoSperre(quelle: AutoSperrQuelle, aktiv: boolean): void {
  useEffect(() => {
    if (!aktiv) return

    const beiAktivitaet = () => quelle.merkeAktivitaet()

    AKTIVITAETS_EREIGNISSE.forEach((ereignis) =>
      window.addEventListener(ereignis, beiAktivitaet, { passive: true }),
    )

    const takt = setInterval(() => quelle.pruefeFrist(), PRUEFTAKT_MS)

    const beiFensterwechsel = () => {
      if (quelle.sperrtBeiFensterwechsel() && quelle.istEntsperrt() && !quelle.istBeschaeftigt()) {
        quelle.sperre()
      }
    }

    const beiSichtwechsel = () => {
      if (document.hidden) beiFensterwechsel()
      else if (quelle.istEntsperrt()) quelle.pruefeFrist()
    }

    // Zurück am Fenster wird sofort geprüft und nicht erst beim nächsten Takt:
    // wer nach zwei Stunden zurückkommt, soll nicht zehn Sekunden lang einen
    // offenen Messenger sehen, der eigentlich längst zu sein müsste.
    const beiFokus = () => {
      if (quelle.istEntsperrt()) quelle.pruefeFrist()
    }

    window.addEventListener('blur', beiFensterwechsel)
    window.addEventListener('pagehide', beiFensterwechsel)
    window.addEventListener('focus', beiFokus)
    document.addEventListener('visibilitychange', beiSichtwechsel)

    // In der App meldet das Fenster seinen Abgang über Tauri, nicht über das
    // DOM: ein Fenster, das in den Hintergrund rutscht, löst dort kein `blur`
    // aus, das hier ankäme.
    let tauriAbmelden: (() => void) | undefined
    if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
      import('@tauri-apps/api/event')
        .then(({ listen }) => listen('tauri://blur', beiFensterwechsel))
        .then((abmelden) => {
          tauriAbmelden = abmelden
        })
        .catch(() => {})
    }

    return () => {
      AKTIVITAETS_EREIGNISSE.forEach((ereignis) =>
        window.removeEventListener(ereignis, beiAktivitaet),
      )
      clearInterval(takt)
      window.removeEventListener('blur', beiFensterwechsel)
      window.removeEventListener('pagehide', beiFensterwechsel)
      window.removeEventListener('focus', beiFokus)
      document.removeEventListener('visibilitychange', beiSichtwechsel)
      tauriAbmelden?.()
    }
  }, [quelle, aktiv])
}
