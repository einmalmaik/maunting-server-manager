/**
 * Die Tonwiedergabe des Anrufs.
 *
 * LiveKit spielt eingehenden Ton nicht von selbst ab — eine Spur muss an ein
 * `<audio>`-Element gehängt werden, sonst kommt sie zwar an, ist aber nirgends
 * hörbar. `@livekit/components-react` hat dafür den `RoomAudioRenderer`; das
 * Paket ist in MSM bewusst nicht dabei (siehe `livekitRaum.ts`), also steht der
 * Ersatz hier.
 *
 * Die eigene Spur wird nie angehängt: man hörte sich sonst mit der Verzögerung
 * des Servers selbst reden.
 *
 * Nebenwirkung, die zählt: erst durch diese Elemente wirken
 * `participant.setVolume` und damit „Taub schalten" und die Lautstärke je
 * Teilnehmer — beides greift auf angehängten Elementen.
 */

import { useEffect, useRef } from 'react'
import { RoomEvent, Track, setzeLautsprecher } from '@/services/livekitRaum'
import type { RemoteAudioTrack, RemoteTrack, RemoteTrackPublication, Room } from 'livekit-client'

const TON_QUELLEN: Track.Source[] = [Track.Source.Microphone, Track.Source.ScreenShareAudio]

function istTon(spur: RemoteTrack): spur is RemoteAudioTrack {
  return spur.kind === Track.Kind.Audio
}

export function RemoteAudio({ room }: { room: Room | null }) {
  const behaelter = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const ziel = behaelter.current
    if (!room || !ziel) return

    /** Element je Spur-Kennung, damit ein Abmelden genau eines wieder findet. */
    const elemente = new Map<string, HTMLAudioElement>()

    const haengeAn = (spur: RemoteTrack, veroeffentlichung: RemoteTrackPublication) => {
      if (!istTon(spur)) return
      if (!TON_QUELLEN.includes(veroeffentlichung.source)) return
      if (elemente.has(veroeffentlichung.trackSid)) return

      const element = spur.attach()
      element.autoplay = true
      // Ohne `playsInline` blendet iOS Safari einen Vollbildspieler ein.
      element.setAttribute('playsinline', '')
      ziel.appendChild(element)
      elemente.set(veroeffentlichung.trackSid, element)

      // Erst jetzt gibt es ein Element, auf dem `setSinkId` etwas bewirkt.
      void setzeLautsprecher(room)
    }

    const loese = (spur: RemoteTrack, veroeffentlichung: RemoteTrackPublication) => {
      const element = elemente.get(veroeffentlichung.trackSid)
      if (!element) return
      elemente.delete(veroeffentlichung.trackSid)
      spur.detach(element)
      element.remove()
    }

    // Was schon läuft, bevor dieses Bauteil da war — beim Wiederaufbau des
    // Overlays (Vollbild, Neurendern) ist das der Normalfall.
    room.remoteParticipants.forEach((teilnehmer) => {
      teilnehmer.trackPublications.forEach((veroeffentlichung) => {
        if (veroeffentlichung.track) haengeAn(veroeffentlichung.track, veroeffentlichung)
      })
    })

    room.on(RoomEvent.TrackSubscribed, haengeAn)
    room.on(RoomEvent.TrackUnsubscribed, loese)

    return () => {
      room.off(RoomEvent.TrackSubscribed, haengeAn)
      room.off(RoomEvent.TrackUnsubscribed, loese)
      elemente.forEach((element) => element.remove())
      elemente.clear()
    }
  }, [room])

  // Versteckt, aber im Baum: ein `display: none`-Elternteil hält Chromium nicht
  // vom Abspielen ab, und außerhalb des Layouts kann keine Größenänderung des
  // Anruffensters die Elemente treffen.
  return <div ref={behaelter} aria-hidden="true" className="sr-only" />
}
