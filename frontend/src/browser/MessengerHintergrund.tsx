/**
 * Pontis hält das Gerätepostfach offen, auch wenn der Messenger zu ist.
 *
 * Ohne diesen Hintergrund erfuhr ein geschlossener Messenger weder von einer
 * neuen Nachricht (keine Zahl, kein Ton) noch quittierte er ihre Zustellung —
 * der Absender blieb auf einem Haken, bis jemand den Chat öffnete.
 *
 * Die Wache läuft nur außerhalb von `/chat`: dort verarbeitet der Kern der
 * Messenger-Seite dasselbe Postfach. Kontakte werden bewusst schlank geladen
 * (nur Freunde und Gruppen); Teams, Stories und öffentliche Profile gehören
 * nicht zum Zustellen.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getFriends, getGroups, type ChatGroupItem } from '@/api/social'
import { meldeChat } from '@/services/benachrichtigung/chatMeldung'
import { IDENTITY_LOADING, resolveIdentity, type E2eeIdentity } from '@/services/e2eeIdentity'
import { istGesperrt, useMessengerSperre } from '@/services/messengerSperre'
import { usePostfachWache, type PostfachFreund } from '@/hooks/usePostfachWache'
import { useAuthStore } from '@/stores/authStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

export function MessengerHintergrund() {
  const { t } = useTranslation()
  const userId = useAuthStore((s) => s.user?.id)
  const gesperrt = useMessengerSperre(istGesperrt)
  const [identitaet, setIdentitaet] = useState<E2eeIdentity>(IDENTITY_LOADING)
  const [freunde, setFreunde] = useState<PostfachFreund[]>([])
  const [gruppen, setGruppen] = useState<ChatGroupItem[]>([])

  useEffect(() => {
    // Ein Kontowechsel darf nicht für einen Moment mit dem Schlüssel des
    // vorigen Kontos laufen; gesperrt gibt es ebenfalls nichts zu öffnen.
    setIdentitaet(IDENTITY_LOADING)
    if (!userId || gesperrt) return
    let aktiv = true
    resolveIdentity(userId)
      .then((next) => {
        if (aktiv) setIdentitaet(next)
      })
      .catch(() => {})
    return () => {
      aktiv = false
    }
  }, [userId, gesperrt])

  useEffect(() => {
    setFreunde([])
    setGruppen([])
    if (!userId || gesperrt) return
    let aktiv = true

    const laden = async () => {
      try {
        const [frischFreunde, frischGruppen] = await Promise.all([
          getFriends(),
          getGroups(),
        ])
        if (!aktiv) return
        setFreunde(frischFreunde)
        setGruppen(frischGruppen)
      } catch {
        // Netz weg: die letzte Liste bleibt; der nächste Takt versucht es neu.
      }
    }

    void laden()
    const takt = setInterval(() => void laden(), 60_000)
    window.addEventListener('online', laden)
    return () => {
      aktiv = false
      clearInterval(takt)
      window.removeEventListener('online', laden)
    }
  }, [userId, gesperrt])

  usePostfachWache({
    eigeneId: userId ?? 0,
    freunde,
    gruppen,
    identitaet,
    gesperrt,
    t,
    markAsRead: (mailbox) => useMessengerNotificationStore.getState().markAsRead(mailbox),
    // Im Hintergrund gibt es keine Verfall-Oberfläche; der Stand selbst liegt
    // bereits in der Ablage und wird beim Öffnen gelesen.
    setVerfallSekunden: () => {},
    istBlockiert: (id) => useMessengerNotificationStore.getState().isBlocked(id),
    beiNeuerNachricht: (gespraech, nachrichten) => {
      for (const msg of nachrichten) {
        meldeChat({
          mailbox: gespraech.mailbox,
          senderId: msg.senderId,
          clientUuid: msg.clientUuid ?? null,
          istGruppe: Boolean(gespraech.group),
          t,
        })
      }
    },
  })

  return null
}
