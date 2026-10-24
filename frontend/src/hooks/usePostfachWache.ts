/**
 * Das Gerätepostfach im Hintergrund leeren — auch ohne offenen Messenger.
 *
 * Bis 10/2026 lag dieser Lauf in `useMessengerKern` und lief nur, solange die
 * Messenger-Seite montiert war. Wer den Messenger in Pontis schloss, bekam
 * keine Zustellung, keine Zahl und keinen Ton; die erste Zustellquittung ging
 * erst beim Öffnen des Chats hinaus, unmittelbar vor der Lesequittung.
 *
 * Der Lauf ist hier herausgelöst, damit ihn die Messenger-Seite und die
 * Pontis-Wache teilen. `verarbeitePostfach` selbst sorgt dafür, dass nie zwei
 * Läufe gleichzeitig dasselbe Postfach entschlüsseln.
 */

import { useEffect, useRef } from 'react'

import type { ChatGroupItem } from '@/api/social'
import {
  deriveBlindMailboxId,
  deriveGroupBlindMailboxId,
  getCachedBlindMailboxId,
  getCachedGroupBlindMailboxId,
} from '@/services/e2eeCrypto'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import {
  verarbeitePostfach,
  type PostfachGespraech,
} from '@/services/gespraech/postfach'
import { ladeGruppenzustand } from '@/services/gruppenKonfig'
import { rechteAusGruppe } from '@/services/gruppenRollen'
import { sendeAusstehendeZustellungen } from '@/services/ratchetSitzung'

type Uebersetzer = (schluessel: string, werte?: Record<string, unknown>) => string

export interface PostfachFreund {
  user_id?: number
  friend_user_id?: number
  id: number
}

export interface PostfachWacheOptionen {
  eigeneId: number
  freunde: readonly PostfachFreund[]
  gruppen: readonly ChatGroupItem[]
  identitaet: E2eeIdentity
  /** PIN-Schloss: gesperrt gibt die Ablage nichts heraus, also kein Lauf. */
  gesperrt: boolean
  t: Uebersetzer
  markAsRead: (mailbox: string) => void
  setVerfallSekunden: (sekunden: number) => void
  gruppenrechteVon?: (konto: number) => Set<string>
  istBlockiert?: (userId: number) => boolean
  beiNeuerNachricht?: (
    gespraech: PostfachGespraech,
    nachrichten: readonly { clientUuid?: string; senderId?: number }[],
  ) => void
}

/** Ein Anstoß während eines Laufs wird genau einmal nachgeholt. */
export function usePostfachWache({
  eigeneId,
  freunde,
  gruppen,
  identitaet,
  gesperrt,
  t,
  markAsRead,
  setVerfallSekunden,
  gruppenrechteVon,
  istBlockiert,
  beiNeuerNachricht,
}: PostfachWacheOptionen): void {
  const aktuell = useRef({
    t,
    markAsRead,
    setVerfallSekunden,
    gruppenrechteVon,
    istBlockiert,
    beiNeuerNachricht,
  })
  aktuell.current = {
    t,
    markAsRead,
    setVerfallSekunden,
    gruppenrechteVon,
    istBlockiert,
    beiNeuerNachricht,
  }

  useEffect(() => {
    if (!eigeneId || gesperrt) return
    if (identitaet.state !== 'ready') return

    let aktiv = true
    let laeuft = false
    let geplant: ReturnType<typeof setTimeout> | null = null

    const gespraeche = async (): Promise<PostfachGespraech[]> => {
      const liste: PostfachGespraech[] = []
      for (const f of freunde) {
        const peerId = Number(f.user_id ?? f.friend_user_id ?? f.id)
        if (!Number.isInteger(peerId) || peerId <= 0) continue
        const mailbox =
          getCachedBlindMailboxId(eigeneId, peerId) ??
          (await deriveBlindMailboxId(eigeneId, peerId))
        if (!mailbox) continue
        liste.push({
          mailbox,
          drKontext: { eigeneId, peerId },
          gruppenKontext: null,
          contact: { userId: peerId, isFriend: true },
          group: null,
        })
      }
      for (const g of gruppen) {
        const mailbox =
          getCachedGroupBlindMailboxId(g.id) ?? (await deriveGroupBlindMailboxId(g.id))
        const mitglieder = (g.members ?? []).map((m) => Number(m.user_id))
        if (!mitglieder.includes(eigeneId)) mitglieder.push(eigeneId)
        liste.push({
          mailbox,
          drKontext: null,
          gruppenKontext: {
            groupId: g.id,
            blindMailboxId: mailbox,
            eigeneId,
            mitglieder,
            istEigentuemer: Number(g.owner_user_id) === eigeneId,
          },
          contact: null,
          group: g,
        })
      }
      return liste
    }

    const lauf = async () => {
      if (!aktiv || laeuft) return
      laeuft = true
      try {
        const liste = await gespraeche()
        if (!aktiv) return
        await sendeAusstehendeZustellungen(eigeneId).catch(() => undefined)
        const optionen = aktuell.current
        await verarbeitePostfach({
          eigeneId,
          decryptionKeys: identitaet.decryptionKeys ?? [],
          gespraeche: liste,
          identitaet,
          t: optionen.t,
          markAsRead: optionen.markAsRead,
          setVerfallSekunden: optionen.setVerfallSekunden,
          istBlockiert: optionen.istBlockiert,
          beiNeuerNachricht: optionen.beiNeuerNachricht,
          gruppenrechteVon: optionen.gruppenrechteVon ?? (() => new Set<string>()),
          /*
           * Die Rechte **dieses** Gesprächs, nicht die des sichtbaren.
           * `gruppenrechteVon` der Seite hängt am offenen Gruppenchat; ohne
           * offenen Chat wäre die Menge leer, und gültige Gruppenmitteilungen
           * fielen als „darf nicht schreiben" heraus.
           */
          rechteFuerGruppe: async (group) => {
            let zustand = null
            try {
              const mailbox =
                getCachedGroupBlindMailboxId(group.id) ??
                (await deriveGroupBlindMailboxId(group.id))
              const mitglieder = (group.members ?? []).map((m) => Number(m.user_id))
              if (!mitglieder.includes(eigeneId)) mitglieder.push(eigeneId)
              const lesung = await ladeGruppenzustand({
                groupId: group.id,
                blindMailboxId: mailbox,
                eigeneId,
                mitglieder,
                istEigentuemer: Number(group.owner_user_id) === eigeneId,
              })
              if (lesung.art === 'zustand') zustand = lesung.zustand
            } catch {
              // Ohne Rollenblock gilt die Mitgliederzeile; ohne die bleiben
              // die Standardrechte.
            }
            return (konto: number) => rechteAusGruppe(group, konto, zustand)
          },
        })
      } catch {
        // Netz, Ablage, ein einzelnes Gespraech: der naechste Takt versucht es
        // erneut. Ein Fehler hier darf den Chat nicht aufhalten.
      } finally {
        laeuft = false
      }
    }

    /**
     * Ein Sync-Ereignis der geteilten Mailbox ist ein guter Anlass, sofort
     * auch das Gerätepostfach zu leeren. Kurz entprellt, weil ein Stapel
     * mehrere Ereignisse in derselben Sekunde liefert.
     */
    const anstossen = () => {
      if (geplant) return
      geplant = setTimeout(() => {
        geplant = null
        void lauf()
      }, 250)
    }
    const beiSync = (ereignis: Event) => {
      const detail = (ereignis as CustomEvent<{ type?: string }>).detail
      if (detail?.type === 'e2ee_blind_message') anstossen()
    }

    void lauf()
    // Der Takt ist grob: das Postfach ist die Zustellung, nicht die Anzeige.
    const takt = setInterval(() => void lauf(), 10_000)
    window.addEventListener('online', lauf)
    window.addEventListener('msm:sync-event', beiSync)
    return () => {
      aktiv = false
      if (geplant) clearTimeout(geplant)
      clearInterval(takt)
      window.removeEventListener('online', lauf)
      window.removeEventListener('msm:sync-event', beiSync)
    }
  }, [eigeneId, gesperrt, identitaet, freunde, gruppen])
}
