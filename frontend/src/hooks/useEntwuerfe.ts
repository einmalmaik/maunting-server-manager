/**
 * Entwürfe: entprellt schreiben, versiegelt ablegen.
 *
 * Ein Entwurf ist ungesendeter Klartext und damit das Empfindlichste, was
 * hier anfällt. Er geht deshalb in die versiegelte IndexedDB, nicht in den
 * localStorage neben die Stummschaltungen. Die Chatliste bekommt nur eine
 * gekürzte Vorschau.
 *
 * Bis 09/2026 stand das in `Messenger.tsx`, das Verwerfen dreimal.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ladeAlleEntwuerfe, speichereEntwurf } from '@/services/messengerLocalStore'

const VORSCHAU_ZEICHEN = 80
const ENTPRELLUNG_MS = 600

export function useEntwuerfe() {
  /** Chats mit ungesendetem Text, für die Vorschau in der Liste. */
  const [vorschau, setVorschau] = useState<Record<string, string>>({})
  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wartend = useRef<{ mid: string; text: string } | null>(null)

  const schreibe = useCallback(() => {
    const offen = wartend.current
    if (!offen) return
    wartend.current = null
    void speichereEntwurf(offen.mid, offen.text).catch(() => {})
    setVorschau((prev) => {
      const kurz = offen.text.trim().slice(0, VORSCHAU_ZEICHEN)
      if ((prev[offen.mid] || '') === kurz) return prev
      const neu = { ...prev }
      if (kurz) neu[offen.mid] = kurz
      else delete neu[offen.mid]
      return neu
    })
  }, [])

  const merke = useCallback(
    (mid: string, text: string) => {
      wartend.current = { mid, text }
      if (uhr.current) clearTimeout(uhr.current)
      uhr.current = setTimeout(schreibe, ENTPRELLUNG_MS)
    },
    [schreibe],
  )

  /**
   * Der Entwurf ist verschickt oder geleert, also keiner mehr. Die wartende
   * Entprellung muss mit weg, sonst schreibt sie den Text gleich zurück.
   */
  const verwirf = useCallback((mid: string) => {
    if (uhr.current) clearTimeout(uhr.current)
    wartend.current = null
    void speichereEntwurf(mid, '').catch(() => {})
    setVorschau((prev) => {
      if (!prev[mid]) return prev
      const neu = { ...prev }
      delete neu[mid]
      return neu
    })
  }, [])

  /**
   * Am Telefon reißt ein Anruf oder ein Zurückwischen das Getippte weg, bevor
   * die Entprellung greift. `beforeunload` läuft auf iOS nicht zuverlässig,
   * deshalb diese beiden.
   */
  useEffect(() => {
    const sichern = () => schreibe()
    document.addEventListener('visibilitychange', sichern)
    window.addEventListener('pagehide', sichern)
    return () => {
      document.removeEventListener('visibilitychange', sichern)
      window.removeEventListener('pagehide', sichern)
      sichern()
    }
  }, [schreibe])

  /** Die Vorschauen für die Chatliste einmal beim Öffnen der Seite. */
  useEffect(() => {
    ladeAlleEntwuerfe()
      .then((alle) => {
        const kurz: Record<string, string> = {}
        for (const [mid, text] of Object.entries(alle)) {
          const gekuerzt = text.trim().slice(0, VORSCHAU_ZEICHEN)
          if (gekuerzt) kurz[mid] = gekuerzt
        }
        setVorschau(kurz)
      })
      .catch(() => {})
  }, [])

  return { vorschau, merke, verwirf }
}
