/**
 * Liest Direktchats mit laufendem Funken mit, die gerade nicht offen sind.
 *
 * Dasselbe Muster wie `ErwaehnungsWache`: eine kleine Komponente je Chat mit
 * `useKonversation`, gelesen nur, wenn in genau dieser Mailbox etwas Neues
 * liegt. Entschlüsselt wird über denselben Lesepfad wie im offenen Chat —
 * dasselbe Schloss, dieselbe Klartextablage —, also ohne einen
 * Ratchet-Schlüssel ein zweites Mal zu verbrauchen.
 *
 * **Nur Chats mit Funken.** Jede beobachtete Mailbox kostet bei neuer Post
 * einen Abruf. Ein Freund ohne Funken braucht keine Wache: sein erster
 * Augenblick kommt ohnehin mit dem Ungelesen-Zähler, und beim Öffnen zählt er.
 */

import { useEffect, useMemo, useRef, type MutableRefObject } from 'react'

import { useKonversation } from '@/hooks/useKonversation'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import { werteAugenblickeAus } from '@/services/funkenLesung'
import { dmZiele } from '@/services/gruppenSchluessel'
import { istOffen, siegelAktiv } from '@/services/lokaleVersiegelung'
import { useFunkenStore } from '@/stores/funkenStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

interface WacheProps {
  partnerId: number
  eigeneId: number
  identitaetRef: MutableRefObject<E2eeIdentity>
}

function DirektWache({ partnerId, eigeneId, identitaetRef }: WacheProps) {
  const ziel = useMemo(() => ({ art: 'direkt' as const, peerId: partnerId }), [partnerId])
  const konversation = useKonversation({
    ziel,
    eigeneId,
    identitaetRef,
    // Ein Sitzungsbruch gehört in das Gespräch, in dem er auftritt; die Wache
    // schweigt, wie die Erwähnungswache.
    meldeSitzungsbruch: () => {},
    meldeAufbauAbgelehnt: () => {},
  })
  const mid = konversation.blindMailboxId
  const liesRef = useRef(konversation.liesUmschlaege)
  liesRef.current = konversation.liesUmschlaege

  useEffect(() => {
    if (!mid || !eigeneId) return
    let lebt = true
    let laeuft = false
    let nochmal: ReturnType<typeof setTimeout> | undefined
    let versuche = 0
    // Die Post kommt nicht unter `mid` an: seit dem Chatgeheimnis liegt ein
    // Direktchat in einer Mailbox aus dem Geheimnis, `mid` ist nur noch die
    // Gesprächskennung. Gehört wird auf alle, aus denen auch gelesen wird.
    let kennungen = new Set([mid])
    const frageKennungen = async () => {
      const ziele = await dmZiele(eigeneId, partnerId, mid).catch(() => null)
      if (lebt && ziele) kennungen = new Set([mid, ...ziele.lesen])
    }
    const ungelesen = (zaehler: Record<string, number>) => {
      let summe = 0
      for (const k of kennungen) summe += zaehler[k] || 0
      return summe
    }

    const pruefe = async () => {
      if (!lebt || laeuft) return
      if (siegelAktiv() && !istOffen()) return
      laeuft = true
      clearTimeout(nochmal)
      try {
        const gelesen = await liesRef.current().catch(() => null)
        if (!lebt) return
        if (!gelesen) {
          // `null` heißt meist: die Identität lädt noch. Ohne zweiten Versuch
          // bliebe ein Augenblick, der beim Start schon dalag, ungezählt.
          if (versuche++ < 5) nochmal = setTimeout(() => void pruefe(), 3000)
          return
        }
        versuche = 0
        const ereignisse = await werteAugenblickeAus(gelesen, { mid, ich: eigeneId, partner: partnerId })
        if (lebt && ereignisse.length) await useFunkenStore.getState().nimmAuf(partnerId, ereignisse)
        await frageKennungen()
      } finally {
        laeuft = false
      }
    }

    const aufNeue = (e: Event) => {
      const neueMid = (e as CustomEvent<{ mid?: string }>).detail?.mid
      if (neueMid && kennungen.has(neueMid)) void pruefe()
    }
    window.addEventListener('msm:mailbox-neu', aufNeue)

    let letzterStand = 0
    const abbestellen = useMessengerNotificationStore.subscribe((zustand) => {
      const jetzt = ungelesen(zustand.unreadCounts)
      if (jetzt > letzterStand) void pruefe()
      letzterStand = jetzt
    })

    // Liegt beim Anhängen schon Ungelesenes da, kam es, während die Seite zu
    // war. Sonst nicht lesen: jede Wache fragte sonst beim Start ihre Mailbox ab.
    void frageKennungen().then(() => {
      if (!lebt) return
      letzterStand = ungelesen(useMessengerNotificationStore.getState().unreadCounts)
      if (letzterStand > 0) void pruefe()
    })

    return () => {
      lebt = false
      clearTimeout(nochmal)
      window.removeEventListener('msm:mailbox-neu', aufNeue)
      abbestellen()
    }
  }, [mid, eigeneId, partnerId])

  return null
}

export interface FunkenWacheProps {
  /** Die gerade geöffnete Gegenseite — die liest der Messenger selbst. */
  aktiverPartner: number | null
  eigeneId: number
  identitaetRef: MutableRefObject<E2eeIdentity>
  /** Aus, solange der Messenger gesperrt ist. */
  aktiv: boolean
}

export function FunkenWache({ aktiverPartner, eigeneId, identitaetRef, aktiv }: FunkenWacheProps) {
  const akten = useFunkenStore((s) => s.akten)
  const freunde = useFunkenStore((s) => s.freunde)
  const partner = useMemo(() => {
    const jetzt = Date.now()
    return Object.values(akten)
      .filter((akte) => {
        if (akte.partnerId === aktiverPartner || !freunde[akte.partnerId]) return false
        // Etwas, das noch läuft oder gerettet werden kann. Eine leere oder
        // vergessene Akte braucht niemanden, der mitliest.
        const kern = useFunkenStore.getState().kernVon(akte.partnerId, jetzt)
        return Boolean(kern && (kern.stand > 0 || kern.zyklusStart !== null || kern.verloren > 0))
      })
      .map((akte) => akte.partnerId)
      .sort((a, b) => a - b)
    // `akten` ändert sich mit jedem Augenblick; die Liste nur, wenn ein Funke
    // dazukommt oder erlischt.
  }, [akten, freunde, aktiverPartner])

  if (!aktiv || !eigeneId) return null
  return (
    <>
      {partner.map((id) => (
        <DirektWache key={id} partnerId={id} eigeneId={eigeneId} identitaetRef={identitaetRef} />
      ))}
    </>
  )
}
