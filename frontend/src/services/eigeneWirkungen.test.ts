/**
 * Beweis für die Durchsicht vom 21.10.2026: eine Bearbeitung aus einem
 * Zweitgerät trägt ihren neuen Text (`new_text`) mit sich.
 *
 * Die Liste lag bis dahin als offenes JSON in `localStorage` — bei
 * eingerichtetem PIN also lesbar, obwohl der Verlauf selbst versiegelt war. Sie
 * geht jetzt durch dieselbe Versiegelung wie die Nachrichtenablage.
 */

import { generateAesGcmKey } from '@msdis/shield/aead'
import { beforeEach, describe, expect, it } from 'vitest'

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { MESSENGER_KEYS, leereOfflineAblage } from '@/lib/offlineAblage'
import { setzeInhaltsSchluessel, setzeSiegelAktiv } from './lokaleVersiegelung'
import {
  eigeneWirkungenFuer,
  leereEigeneWirkungen,
  merkeEigeneWirkung,
  merkeWirkungAusKlartext,
  vergissEigeneWirkungen,
} from './eigeneWirkungen'

const KONTO = 7
const MAILBOX = 'dm-7-9'
const ZEIT = '2026-10-21T10:00:00Z'

function roh(): string | null {
  return localStorage.getItem(MESSENGER_KEYS.EIGENE_WIRKUNGEN)
}

const bearings = JSON.stringify({
  type: 'edit_message',
  mailbox: MAILBOX,
  actor_id: KONTO,
  new_text: 'Arzttermin Donnerstag',
})

beforeEach(() => {
  leereOfflineAblage()
  setzeAngemeldetesKonto(KONTO)
  setzeSiegelAktiv(false)
  setzeInhaltsSchluessel(null)
  leereEigeneWirkungen()
})

describe('eigene Wirkungen', () => {
  it('legt beim Start eine Wirkung aus dem Klartext ihres Gesprächs ab', async () => {
    // Der Weg für `holeGeraeteSteuerung` ohne offenes Gespräch: der Cursor
    // wandert weiter, und ohne diese Ablage war eine Bearbeitung oder Löschung
    // aus dem Zweitgerät weg, bevor der Chat je geöffnet wurde.
    await merkeWirkungAusKlartext(bearings, { id: 12, created_at: ZEIT })

    expect(await eigeneWirkungenFuer(MAILBOX)).toEqual([
      { mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT },
    ])
  })

  it('legt nichts ab, was zu keinem Gespräch gehört', async () => {
    await merkeWirkungAusKlartext('kein json', { id: 1, created_at: ZEIT })
    await merkeWirkungAusKlartext(JSON.stringify({ type: 'x' }), { id: 2, created_at: ZEIT })
    expect(roh()).toBe('[]')
  })

  it('legt den Text einer Bearbeitung ohne PIN im Klartext ab', async () => {
    // Ohne Siegel ändert sich nichts — das ist die Zusage der Ablage.
    await merkeEigeneWirkung({ mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT })

    expect(roh()).toContain('Arzttermin Donnerstag')
    expect(await eigeneWirkungenFuer(MAILBOX)).toEqual([
      { mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT },
    ])
  })

  it('legt den Text bei eingerichtetem PIN nur versiegelt ab', async () => {
    setzeSiegelAktiv(true)
    setzeInhaltsSchluessel(await generateAesGcmKey())

    await merkeEigeneWirkung({ mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT })

    const gespeichert = roh() ?? ''
    // Der neue Text der Nachricht steht nicht mehr im Browserprofil.
    expect(gespeichert).not.toContain('Arzttermin')
    expect(gespeichert).not.toContain('edit_message')
    // Die Mailbox bleibt als Klarfeld: ohne sie ließe sich die Ablage nicht
    // filtern, ohne jede Zeile zu entschlüsseln.
    expect(gespeichert).toContain(MAILBOX)
    // Mit Schlüssel ist sie wieder da.
    expect(await eigeneWirkungenFuer(MAILBOX)).toEqual([
      { mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT },
    ])
  })

  it('schreibt im verschlossenen Zustand gar nichts', async () => {
    setzeSiegelAktiv(true)
    setzeInhaltsSchluessel(null)

    await expect(
      merkeEigeneWirkung({ mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT }),
    ).rejects.toThrow('gesperrt')
    expect(roh() ?? '[]').toBe('[]')
  })

  it('überschreibt bei zwei gleichzeitigen Aufrufen keine Wirkung', async () => {
    // Lesen, ändern, schreiben ohne Klammer verliert den ersten Eintrag: der
    // zweite Aufruf liest den Stand vor dem ersten Schreiben.
    await Promise.all([
      merkeEigeneWirkung({ mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT }),
      merkeEigeneWirkung({
        mailbox: MAILBOX,
        payload: JSON.stringify({ type: 'delete_message', mailbox: MAILBOX, actor_id: KONTO }),
        zeitpunkt: '2026-10-21T10:01:00Z',
      }),
    ])

    expect((await eigeneWirkungenFuer(MAILBOX)).map((w) => w.zeitpunkt)).toEqual([
      ZEIT,
      '2026-10-21T10:01:00Z',
    ])
  })

  it('lässt die Wirkungen anderer Gespräche beim Aufräumen stehen', async () => {
    await merkeEigeneWirkung({ mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT })
    await merkeEigeneWirkung({
      mailbox: 'dm-7-11',
      payload: JSON.stringify({ type: 'delete_message', mailbox: 'dm-7-11', actor_id: KONTO }),
      zeitpunkt: ZEIT,
    })

    await vergissEigeneWirkungen(MAILBOX)

    expect(await eigeneWirkungenFuer(MAILBOX)).toEqual([])
    expect(await eigeneWirkungenFuer('dm-7-11')).toHaveLength(1)
  })

  it('liest Zeilen aus der Zeit vor dem Siegel weiter', async () => {
    // Die Umstellung bricht mitten im Betrieb ab: offene und versiegelte
    // Zeilen liegen nebeneinander.
    setzeSiegelAktiv(true)
    setzeInhaltsSchluessel(await generateAesGcmKey())
    localStorage.setItem(
      MESSENGER_KEYS.EIGENE_WIRKUNGEN,
      JSON.stringify([{ mailbox: MAILBOX, payload: bearings, zeitpunkt: ZEIT }]),
    )

    expect((await eigeneWirkungenFuer(MAILBOX)).map((w) => w.payload)).toEqual([bearings])
  })
})
