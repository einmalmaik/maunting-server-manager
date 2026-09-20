/**
 * Der erste Test, der den Rekorder wirklich rendert.
 *
 * Vorher gab es `test/e2e/circularVideoNotes.test.ts`, und die Datei prüfte
 * ihre eigenen Kopien von Gestenrechnung und Verschlüsselung — die Komponente
 * kam darin nicht vor. Deshalb fiel keiner der vier Fehler auf, die hier
 * festgehalten sind: das verlorene letzte Stück, der Sendeknopf unter der
 * Wischgeste, das Mikrofon ohne die Einstellungen aus dem Profil und die
 * Aufnahme ohne Rücksicht auf den Anhangdeckel.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { saveAudioSettings } from '@/lib/audioSettings'
import { videoNotizBitraten } from '@/lib/videoNotiz'
import { videoNotizBudgetBytes } from '@/lib/videoNotiz'
import { maxAnhangBytes } from '@/services/medienKrypto'
import { installFakeWebRtc, type FakeWebRtcSetup } from '@/test/fakeWebRtc'
import { CircularVideoNoteRecorder } from './CircularVideoNoteRecorder'

const t = (key: string) => i18n.t(key)

let rtc: FakeWebRtcSetup

/** Wartet, bis `getUserMedia` durch ist und der Rekorder steht. */
async function rekorder() {
  await waitFor(() => expect(rtc.recorders.length).toBe(1))
  return rtc.recorders[0]
}

/** Ein Klick, dessen Folgen abgewartet werden — das Beenden ist asynchron. */
async function klick(element: HTMLElement) {
  await act(async () => {
    fireEvent.click(element)
  })
}

/** Ein Stück Aufnahme, wie der Rekorder es liefert. */
async function stueck(recorder: (typeof rtc.recorders)[number], bytes: number) {
  await act(async () => {
    recorder.ondataavailable?.({ data: new Blob([new Uint8Array(bytes)]) })
  })
}

beforeEach(() => {
  localStorage.clear()
  rtc = installFakeWebRtc()
})

afterEach(() => {
  rtc.restore()
  vi.restoreAllMocks()
})

describe('Aufnahme starten', () => {
  it('nimmt die Audioeinstellungen aus dem Profil statt `audio: true`', async () => {
    // Die Invariante dieser Datei. Hier stand `audio: true`, und damit war jede
    // Einstellung aus dem Profil für Videonotizen wirkungslos — anders als bei
    // Sprachnachricht, Anruf und Mikrofontest.
    saveAudioSettings({ preferredMicId: 'usb-mic', echoCancellation: false })

    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={vi.fn()} />)
    await rekorder()

    const constraints = rtc.getUserMediaCalls[0]
    expect(constraints?.audio).toMatchObject({
      deviceId: { ideal: 'usb-mic' },
      echoCancellation: false,
      noiseSuppression: true,
      autoGainControl: true,
    })
  })

  it('fragt ein quadratisches Bild von der Frontkamera an', async () => {
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={vi.fn()} />)
    await rekorder()

    const video = rtc.getUserMediaCalls[0]?.video as MediaTrackConstraints
    expect(video.facingMode).toBe('user')
    expect(video.width).toEqual(video.height)
  })

  it('gibt dem Rekorder eine Bitrate mit, statt den Browser wählen zu lassen', async () => {
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={vi.fn()} />)
    const recorder = await rekorder()

    // Ohne Vorgabe wählt der Browser selbst, und bei einer langen Notiz lag er
    // über dem, was der Upload annimmt. Die Zahl kommt aus dem Anhangdeckel
    // zurückgerechnet, nicht aus der Luft.
    expect(recorder.videoBitsPerSecond).toBe(videoNotizBitraten().video)
    expect(recorder.audioBitsPerSecond).toBe(videoNotizBitraten().audio)
  })
})

describe('Senden', () => {
  it('sendet beim Tippen auf das Häkchen, ohne vorherige Geste', async () => {
    // Der gemeldete Fehler: Die Wischerkennung hing am Vollbildrahmen, der
    // Finger auf dem Häkchen galt als Gestenbeginn, die Knopfzeile verschwand
    // darunter und gesendet wurde erst beim Loslassen.
    const onComplete = vi.fn()
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
    await rekorder()

    await klick(screen.getByRole('button', { name: t('social.videoNote.send') }))

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1))
  })

  it('wartet auf den Abschluss des Rekorders, bevor der Blob entsteht', async () => {
    // Hier stand eine Frist von 200 ms. `stop()` schreibt den letzten Block
    // erst danach, und je länger die Aufnahme, desto wahrscheinlicher kam er zu
    // spät: heraus kam eine Datei ohne Abschluss.
    const onComplete = vi.fn()
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
    const recorder = await rekorder()

    const reihenfolge: string[] = []
    const echtesStop = recorder.stop.bind(recorder)
    recorder.stop = () => {
      reihenfolge.push('stop')
      echtesStop()
      reihenfolge.push('onstop')
    }
    onComplete.mockImplementation(() => reihenfolge.push('onComplete'))

    await klick(screen.getByRole('button', { name: t('social.videoNote.send') }))
    await waitFor(() => expect(onComplete).toHaveBeenCalled())

    expect(reihenfolge).toEqual(['stop', 'onstop', 'onComplete'])
  })

  it('stoppt die Kamera erst nach dem Abschluss, nicht daneben', async () => {
    // Eine beendete Spur kann den letzten Block nicht mehr liefern. Vorher
    // liefen `recorder.stop()` und `track.stop()` im selben Atemzug.
    const onComplete = vi.fn()
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
    const recorder = await rekorder()

    let spurenLiefenNochBeimAbschluss = false
    const echtesStop = recorder.stop.bind(recorder)
    recorder.stop = () => {
      spurenLiefenNochBeimAbschluss = recorder.stream
        .getTracks()
        .every((spur) => spur.readyState === 'live')
      echtesStop()
    }

    await klick(screen.getByRole('button', { name: t('social.videoNote.send') }))
    await waitFor(() => expect(onComplete).toHaveBeenCalled())

    expect(spurenLiefenNochBeimAbschluss).toBe(true)
    expect(recorder.stream.getTracks().every((spur) => spur.readyState === 'ended')).toBe(true)
  })

  it('gibt das Aufnahmeformat weiter, das der Rekorder wirklich benutzt', async () => {
    // Vorher stand hier fest `video/webm`, obwohl der Rekorder etwas anderes
    // liefern kann. Die Anzeige beim Empfänger hängt daran.
    const onComplete = vi.fn()
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
    const recorder = await rekorder()

    await klick(screen.getByRole('button', { name: t('social.videoNote.send') }))
    await waitFor(() => expect(onComplete).toHaveBeenCalled())

    expect(onComplete.mock.calls[0][0].mimeType).toBe(recorder.mimeType)
  })

  it('nimmt alle gelieferten Stücke in den Blob auf', async () => {
    const onComplete = vi.fn()
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
    const recorder = await rekorder()

    await stueck(recorder, 1024)
    await stueck(recorder, 2048)

    await klick(screen.getByRole('button', { name: t('social.videoNote.send') }))
    await waitFor(() => expect(onComplete).toHaveBeenCalled())

    // Die beiden Stücke plus der Abschluss, den der Rekorder beim Stoppen gibt.
    expect(onComplete.mock.calls[0][0].blob.size).toBeGreaterThanOrEqual(3072)
  })
})

describe('Abbrechen', () => {
  it('meldet den Abbruch und sendet nichts', async () => {
    const onCancel = vi.fn()
    const onComplete = vi.fn()
    render(<CircularVideoNoteRecorder onCancel={onCancel} onComplete={onComplete} />)
    await rekorder()

    await klick(screen.getByRole('button', { name: t('common.cancel') }))

    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onComplete).not.toHaveBeenCalled()
  })

  it('zeigt keine Wischanleitung mehr an', async () => {
    // Die Geste ist weg, der Hinweis darf es nicht überleben.
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={vi.fn()} />)
    await rekorder()

    expect(screen.queryByText(/wischen|swipe/i)).not.toBeInTheDocument()
  })

  it('zeigt beide Knöpfe von Anfang an', async () => {
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={vi.fn()} />)
    await rekorder()

    expect(screen.getByRole('button', { name: t('common.cancel') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('social.videoNote.send') })).toBeInTheDocument()
  })
})

describe('Wenn die Kamera nein sagt', () => {
  it('meldet es und bricht ab, statt leer stehen zu bleiben', async () => {
    // Die alte E2E-Datei prüfte hier, ob `getUserMedia` einen
    // `NotAllowedError` wirft — also den Fake, nicht die Komponente. Die Frage
    // ist aber, was der Rekorder daraus macht.
    rtc.restore()
    rtc = installFakeWebRtc({ denyPermission: true })
    const onCancel = vi.fn()
    const onComplete = vi.fn()

    render(<CircularVideoNoteRecorder onCancel={onCancel} onComplete={onComplete} />)

    await waitFor(() => expect(onCancel).toHaveBeenCalledTimes(1))
    expect(onComplete).not.toHaveBeenCalled()
    expect(rtc.recorders).toHaveLength(0)
  })
})

describe('Zeitgrenze', () => {
  it('beendet nach 60 Sekunden von selbst, genau einmal', async () => {
    vi.useFakeTimers()
    try {
      const onComplete = vi.fn()
      render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(rtc.recorders).toHaveLength(1)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000)
      })

      // Genau einmal: Das Limit stand einmal im Zustandsaktualisierer, und weil
      // React den in der Entwicklung doppelt aufruft, ging jede auslaufende
      // Notiz zweimal raus.
      expect(onComplete).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('schreibt der ausgelaufenen Notiz ihre echte Dauer zu', async () => {
    // Der Intervall-Rückruf sah `elapsedSeconds` aus dem Render, in dem er
    // entstand, also 0. `Math.max(1, 0)` machte daraus eine Sekunde, und jede
    // volle Minute kam beim Empfänger als „1s" an.
    vi.useFakeTimers()
    try {
      const onComplete = vi.fn()
      render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000)
      })

      expect(onComplete.mock.calls[0][0].durationSeconds).toBe(60)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('Größenwächter', () => {
  it('beendet die Aufnahme, wenn die Stücke das Aufnahmebudget reißen', async () => {
    // Die Bitrate ist ein Mittelwert; ein bewegtes Bild zieht darüber. Ohne
    // diesen Wächter liefe die Notiz weiter und verschwände am Server.
    const onComplete = vi.fn()
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
    const recorder = await rekorder()

    await stueck(recorder, videoNotizBudgetBytes())

    await waitFor(() => expect(onComplete).toHaveBeenCalledTimes(1))
    expect(recorder.state).toBe('inactive')
  })

  it('gibt einen Blob weiter, den der Sendepfad auch annimmt', async () => {
    // Der Fehler, den die Laufzeitprüfung fand: Wächter und Prüfung im
    // Sendepfad standen auf derselben Zahl. Der Wächter schlägt erst an,
    // nachdem das Budget erreicht ist — der Blob lag also immer knapp darüber,
    // und `handleSendMessage` warf eine volle Minute Aufnahme wieder weg.
    const onComplete = vi.fn()
    render(<CircularVideoNoteRecorder onCancel={vi.fn()} onComplete={onComplete} />)
    const recorder = await rekorder()

    await stueck(recorder, videoNotizBudgetBytes())
    await waitFor(() => expect(onComplete).toHaveBeenCalled())

    expect(onComplete.mock.calls[0][0].blob.size).toBeLessThanOrEqual(maxAnhangBytes())
  })
})
