/**
 * Suchen, was schon da ist — und bei gesetztem PIN eben nicht.
 *
 * Kein Index, keine Datenbank: die Ablage wird gestellt, geprüft wird das
 * Durchsehen. Der wichtigste Block ist der letzte. „Gesperrt" muss als
 * *gesperrt* herauskommen und nicht als leere Trefferliste, sonst liest der
 * Benutzer „nichts gefunden", wo „ich darf nicht nachsehen" die Wahrheit wäre.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./messengerLocalStore', () => ({
  listeLokaleMailboxen: vi.fn(async () => []),
  loadLocalMessages: vi.fn(async () => []),
}))

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { setzeInhaltsSchluessel, setzeSiegelAktiv } from './lokaleVersiegelung'
import { listeLokaleMailboxen, loadLocalMessages } from './messengerLocalStore'
import {
  leereSuchspeicher,
  sammleAnMich,
  sammleMarkierte,
  sucheImChat,
  sucheUeberall,
  vergissMailbox,
} from './verlaufSuche'

const A = 'mb-a'
const B = 'mb-b'

function zeile(teil: Record<string, unknown>) {
  return {
    id: 1,
    clientUuid: 'u-1',
    senderId: 11,
    isSelf: false,
    createdAt: '2026-09-20T10:00:00.000Z',
    text: '',
    ...teil,
  } as never
}

function ablage(nach: Record<string, unknown[]>) {
  vi.mocked(listeLokaleMailboxen).mockResolvedValue(Object.keys(nach))
  vi.mocked(loadLocalMessages).mockImplementation(async (mid: string) => (nach[mid] || []) as never)
}

beforeEach(() => {
  vi.clearAllMocks()
  leereSuchspeicher()
  localStorage.clear()
  setzeAngemeldetesKonto(42)
  setzeSiegelAktiv(false)
  setzeInhaltsSchluessel(null)
})

describe('Suche in einem Chat', () => {
  it('findet und schneidet den Auszug um den Treffer', async () => {
    ablage({ [A]: [zeile({ id: 1, text: 'Der Server ist wieder oben' })] })
    const { treffer } = await sucheImChat(A, 'server')
    expect(treffer).toHaveLength(1)
    expect(treffer[0].auszug).toBe('Der Server ist wieder oben')
    expect(treffer[0].auszug.slice(treffer[0].von, treffer[0].bis)).toBe('Server')
  })

  it('sucht ohne Rücksicht auf Groß- und Kleinschreibung', async () => {
    ablage({ [A]: [zeile({ text: 'BACKUP läuft' })] })
    expect((await sucheImChat(A, 'backup')).treffer).toHaveLength(1)
  })

  it('setzt Randmarken, wenn der Text länger ist als der Ausschnitt', async () => {
    const lang = `${'x'.repeat(80)} Nadel ${'y'.repeat(80)}`
    ablage({ [A]: [zeile({ text: lang })] })
    const { treffer } = await sucheImChat(A, 'Nadel')
    expect(treffer[0].auszug.startsWith('…')).toBe(true)
    expect(treffer[0].auszug.endsWith('…')).toBe(true)
    expect(treffer[0].auszug.slice(treffer[0].von, treffer[0].bis)).toBe('Nadel')
  })

  it('findet auch im Namen eines Anhangs und im Titel einer Notiz', async () => {
    ablage({
      [A]: [
        zeile({ id: 1, text: '', fileAttachment: { name: 'sicherung.zip' } }),
        zeile({ id: 2, text: '', noteAttachment: { title: 'Sicherung planen' } }),
      ],
    })
    expect((await sucheImChat(A, 'sicherung')).treffer).toHaveLength(2)
  })

  it('lässt Grabsteine und Systemzeilen außen vor', async () => {
    // Ein Treffer auf „Diese Nachricht wurde gelöscht" wäre grotesk.
    ablage({
      [A]: [
        zeile({ id: 1, text: 'gelöschter Treffer', isDeleted: true }),
        zeile({ id: 2, text: 'Systemtreffer', isSystem: true }),
      ],
    })
    expect((await sucheImChat(A, 'treffer')).treffer).toEqual([])
  })

  it('stellt das Neueste nach vorn', async () => {
    ablage({
      [A]: [
        zeile({ id: 1, text: 'Treffer alt', createdAt: '2026-09-18T10:00:00.000Z' }),
        zeile({ id: 2, text: 'Treffer neu', createdAt: '2026-09-20T10:00:00.000Z' }),
      ],
    })
    const { treffer } = await sucheImChat(A, 'treffer')
    expect(treffer.map((t) => t.id)).toEqual([2, 1])
  })

  it('liefert bei leerer Frage nichts, ohne die Platte anzufassen', async () => {
    ablage({ [A]: [zeile({ text: 'irgendwas' })] })
    expect((await sucheImChat(A, '   ')).treffer).toEqual([])
    expect(vi.mocked(loadLocalMessages)).not.toHaveBeenCalled()
  })
})

describe('Suche über alle Chats', () => {
  it('gruppiert nach Chat und stellt den Chat mit dem jüngsten Treffer nach vorn', async () => {
    ablage({
      [A]: [zeile({ id: 1, text: 'Treffer A', createdAt: '2026-09-18T10:00:00.000Z' })],
      [B]: [zeile({ id: 2, text: 'Treffer B', createdAt: '2026-09-20T10:00:00.000Z' })],
    })
    const { chats } = await sucheUeberall('treffer')
    expect(chats.map((c) => c.blindMailboxId)).toEqual([B, A])
  })

  it('lässt Chats ohne Treffer ganz weg', async () => {
    ablage({ [A]: [zeile({ text: 'Treffer' })], [B]: [zeile({ text: 'nichts dabei' })] })
    const { chats } = await sucheUeberall('treffer')
    expect(chats).toHaveLength(1)
  })
})

describe('Markiert und an mich', () => {
  it('sammelt nur Markiertes', async () => {
    ablage({
      [A]: [zeile({ id: 1, text: 'wichtig', istMarkiert: true }), zeile({ id: 2, text: 'egal' })],
    })
    const { chats } = await sammleMarkierte()
    expect(chats[0].treffer.map((t) => t.id)).toEqual([1])
  })

  it('lässt die Rechtefrage beim Aufrufer', async () => {
    // Die Prüfung für `@everyone` braucht die Gruppenantwort und damit das
    // Netz. Dieses Modul entscheidet sie nicht, es fragt.
    ablage({ [A]: [zeile({ id: 1, text: 'an alle' }), zeile({ id: 2, text: 'an niemanden' })] })
    const { chats } = await sammleAnMich(42, (m) => m.id === 1)
    expect(chats[0].treffer.map((t) => t.id)).toEqual([1])
  })

  it('übergeht eigene Nachrichten', async () => {
    ablage({ [A]: [zeile({ id: 1, text: 'von mir', isSelf: true })] })
    expect((await sammleAnMich(42, () => true)).chats).toEqual([])
  })

  it('liefert ohne eigene Kennung nichts', async () => {
    ablage({ [A]: [zeile({ id: 1, text: 'x' })] })
    expect((await sammleAnMich(0, () => true)).chats).toEqual([])
  })
})

describe('Gesperrt wird nicht gesucht', () => {
  beforeEach(() => {
    setzeSiegelAktiv(true)
    setzeInhaltsSchluessel(null)
  })

  it('meldet die Sperre, statt eine leere Liste zu zeigen', async () => {
    ablage({ [A]: [zeile({ text: 'Treffer' })] })
    const ergebnis = await sucheImChat(A, 'treffer')
    expect(ergebnis).toEqual({ treffer: [], gesperrt: true })
  })

  it('fasst die Ablage dabei gar nicht erst an', async () => {
    ablage({ [A]: [zeile({ text: 'Treffer' })] })
    await sucheImChat(A, 'treffer')
    await sucheUeberall('treffer')
    await sammleMarkierte()
    await sammleAnMich(42, () => true)
    expect(vi.mocked(loadLocalMessages)).not.toHaveBeenCalled()
    expect(vi.mocked(listeLokaleMailboxen)).not.toHaveBeenCalled()
  })

  it('meldet die Sperre auch in den drei Übersichten', async () => {
    expect((await sucheUeberall('x')).gesperrt).toBe(true)
    expect((await sammleMarkierte()).gesperrt).toBe(true)
    expect((await sammleAnMich(42, () => true)).gesperrt).toBe(true)
  })
})

describe('Zwischenspeicher', () => {
  it('liest eine Mailbox nur einmal je Sitzung', async () => {
    ablage({ [A]: [zeile({ text: 'Treffer' })] })
    await sucheImChat(A, 'tref')
    await sucheImChat(A, 'treff')
    expect(vi.mocked(loadLocalMessages)).toHaveBeenCalledTimes(1)
  })

  it('liest neu, nachdem eine Mailbox als geändert gemeldet wurde', async () => {
    ablage({ [A]: [zeile({ text: 'Treffer' })] })
    await sucheImChat(A, 'treffer')
    vergissMailbox(A)
    await sucheImChat(A, 'treffer')
    expect(vi.mocked(loadLocalMessages)).toHaveBeenCalledTimes(2)
  })

  it('wirft beim Leeren alles weg — das gehört an jedes Sperren', async () => {
    ablage({ [A]: [zeile({ text: 'Treffer' })] })
    await sucheImChat(A, 'treffer')
    leereSuchspeicher()
    await sucheImChat(A, 'treffer')
    expect(vi.mocked(loadLocalMessages)).toHaveBeenCalledTimes(2)
  })
})
