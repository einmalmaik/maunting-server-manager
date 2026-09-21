import { useEffect, useState } from 'react'

import {
  beobachteChatHintergrund,
  ladeChatHintergrund,
  type ChatHintergrundBereich,
  type ChatHintergrundKonfiguration,
} from './speicher'
import { vorlageMitId, type HintergrundId } from './vorlagen'

/**
 * Der gewählte Hintergrund dieses Bereichs, lebend.
 *
 * Lebend heisst: wer im Chat die Vorlage wechselt, ändert damit auch das
 * Guardian-Fenster nebenan und den zweiten Tab — ohne Neuladen, ohne dass eine
 * Seite die andere kennen müsste.
 */
export function useChatHintergrund(
  bereich: ChatHintergrundBereich,
): ChatHintergrundKonfiguration {
  const [konfiguration, setzeKonfiguration] = useState(() => ladeChatHintergrund(bereich))

  useEffect(() => {
    // Beim Bereichswechsel zuerst neu lesen: sonst zeigte die Fläche bis zur
    // ersten Änderung den Hintergrund des vorherigen Bereichs.
    setzeKonfiguration(ladeChatHintergrund(bereich))
    return beobachteChatHintergrund(bereich, setzeKonfiguration)
  }, [bereich])

  return konfiguration
}

/**
 * Die Fläche einer einzelnen Vorlage — dieselbe im Chat wie auf der
 * Auswahlkachel. Positioniert sich selbst über dem nächsten `relative`-Kasten.
 */
export function HintergrundFlaeche({
  vorlage,
  eigenesBild,
}: {
  vorlage: HintergrundId
  eigenesBild?: string
}) {
  if (vorlage === 'custom') {
    if (!eigenesBild) return null
    return (
      <img
        src={eigenesBild}
        alt=""
        aria-hidden="true"
        className="absolute inset-0 h-full w-full object-cover"
      />
    )
  }

  const gefunden = vorlageMitId(vorlage)
  if (!gefunden) return null
  return <div className={`absolute inset-0 ${gefunden.klassen}`} style={gefunden.stil} />
}

/**
 * Die Hintergrundschicht eines Chatbereichs.
 *
 * **Einbau.** Der Kasten darüber braucht `relative`, die Schicht ist sein
 * erstes Kind. Sie liegt absolut und mit `z-0` darin — und damit über allem,
 * was im Fluss und ohne eigene Positionierung darunter steht. Jedes Element,
 * das *über* dem Hintergrund stehen soll (Verlauf, Kopfzeile, Eingabe), braucht
 * deshalb selbst `relative` oder eine Position. Das ist die Bauart, die der
 * Messenger seit jeher benutzt; sie kommt ohne `isolate` aus und sperrt damit
 * keine Überlagerung in einen Stapelkontext ein.
 *
 * Die Schicht nimmt keine Klicks (`pointer-events-none`) und keinen
 * Vorlesefokus (`aria-hidden`): sie ist Dekoration, kein Inhalt.
 */
export function ChatHintergrund({
  bereich,
  className = '',
}: {
  bereich: ChatHintergrundBereich
  className?: string
}) {
  const konfiguration = useChatHintergrund(bereich)

  return (
    <div
      className={`pointer-events-none absolute inset-0 z-0 overflow-hidden ${className}`}
      aria-hidden="true"
      data-chat-hintergrund={konfiguration.preset}
    >
      <HintergrundFlaeche
        vorlage={konfiguration.preset}
        eigenesBild={konfiguration.customDataUrl}
      />

      {/* Abdunkeln, damit der Text auch auf einem hellen Foto lesbar bleibt. */}
      {konfiguration.dimLevel > 0 && (
        <div
          className="absolute inset-0 bg-black"
          style={{ opacity: konfiguration.dimLevel / 100 }}
          data-chat-hintergrund-abdunkeln={konfiguration.dimLevel}
        />
      )}
    </div>
  )
}
