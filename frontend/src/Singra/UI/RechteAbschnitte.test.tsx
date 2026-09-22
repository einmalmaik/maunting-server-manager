import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { RechteAbschnitte, type RechteAbschnittDefinition, type RechteZeile } from './RechteAbschnitte'

const RECHTE: RechteZeile[] = [
  { key: 'send_messages', kategorie: 'chat', titel: 'Nachrichten senden', beschreibung: 'Darf schreiben.' },
  { key: 'invite_members', kategorie: 'members', titel: 'Einladen', beschreibung: 'Darf einladen.' },
  { key: 'join_group_calls', kategorie: 'calls', titel: 'Anruf betreten', beschreibung: 'Darf mittelefonieren.' },
  { key: 'pin_messages', kategorie: 'moderation', titel: 'Anheften', beschreibung: 'Darf anheften.' },
  { key: 'manage_roles', kategorie: 'administration', titel: 'Rollen verwalten', beschreibung: 'Darf Rollen setzen.' },
]

const ABSCHNITTE: RechteAbschnittDefinition[] = [
  { titel: 'Chat und Mitglieder', symbol: 'chat', kategorien: ['chat', 'members'] },
  { titel: 'Sprach- und Videoanrufe', symbol: 'anruf', kategorien: ['calls'] },
  { titel: 'Moderation', symbol: 'moderation', kategorien: ['moderation', 'administration'] },
]

function zeichne(ueberschreibung: Partial<Parameters<typeof RechteAbschnitte>[0]> = {}) {
  const onToggle = vi.fn()
  const ergebnis = render(
    <RechteAbschnitte
      rechte={RECHTE}
      abschnitte={ABSCHNITTE}
      gesetzt={new Set(['send_messages'])}
      onToggle={onToggle}
      zeilenBeschriftung={(titel) => `${titel} erlauben`}
      {...ueberschreibung}
    />,
  )
  return { onToggle, ...ergebnis }
}

/** Der Kasten eines Abschnitts, gefunden über seine Überschrift. */
function abschnittMit(titel: string): HTMLElement {
  const ueberschrift = screen.getByText(titel)
  const kasten = ueberschrift.closest('div')?.parentElement
  if (!kasten) throw new Error(`Abschnitt ${titel} hat keinen Kasten`)
  return kasten
}

describe('RechteAbschnitte', () => {
  it('legt jedes Recht in den Abschnitt seiner Kategorie', () => {
    zeichne()

    const chat = abschnittMit('Chat und Mitglieder')
    expect(within(chat).getByText('Nachrichten senden')).toBeInTheDocument()
    // 'members' gehört in denselben Abschnitt wie 'chat'.
    expect(within(chat).getByText('Einladen')).toBeInTheDocument()
    expect(within(chat).queryByText('Anruf betreten')).not.toBeInTheDocument()

    const moderation = abschnittMit('Moderation')
    expect(within(moderation).getByText('Anheften')).toBeInTheDocument()
    // 'administration' wird von der Moderation mit eingesammelt.
    expect(within(moderation).getByText('Rollen verwalten')).toBeInTheDocument()
  })

  it('hält die Reihenfolge der Abschnitte und der Rechte darin', () => {
    zeichne()

    const ueberschriften = screen
      .getAllByText(/Chat und Mitglieder|Sprach- und Videoanrufe|Moderation/)
      .map((el) => el.textContent)
    expect(ueberschriften).toEqual(['Chat und Mitglieder', 'Sprach- und Videoanrufe', 'Moderation'])

    // Innerhalb des Abschnitts gilt die Reihenfolge der übergebenen Liste,
    // nicht die der Kategorien.
    const chat = abschnittMit('Chat und Mitglieder')
    const titelImChat = within(chat)
      .getAllByRole('switch')
      .map((s) => s.getAttribute('aria-label'))
    expect(titelImChat).toEqual(['Nachrichten senden erlauben', 'Einladen erlauben'])
  })

  it('zeichnet einen Abschnitt ohne Rechte gar nicht', () => {
    // Genau der Fall des Standardrechte-Reiters: 'manage_roles' wird dort nicht
    // angeboten. Bliebe der Abschnitt stehen, stünde eine leere Überschrift da.
    zeichne({ rechte: RECHTE.filter((r) => r.kategorie !== 'moderation' && r.kategorie !== 'administration') })

    expect(screen.queryByText('Moderation')).not.toBeInTheDocument()
    expect(screen.getByText('Chat und Mitglieder')).toBeInTheDocument()
  })

  it('zeigt den Stand jedes Rechts am Schalter', () => {
    zeichne()

    expect(screen.getByRole('switch', { name: 'Nachrichten senden erlauben' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(screen.getByRole('switch', { name: 'Einladen erlauben' })).toHaveAttribute(
      'aria-checked',
      'false',
    )
  })

  it('meldet Schlüssel und neuen Stand beim Umlegen', () => {
    const { onToggle } = zeichne()

    fireEvent.click(screen.getByRole('switch', { name: 'Einladen erlauben' }))
    expect(onToggle).toHaveBeenCalledWith('invite_members', true)

    fireEvent.click(screen.getByRole('switch', { name: 'Nachrichten senden erlauben' }))
    expect(onToggle).toHaveBeenLastCalledWith('send_messages', false)
  })

  it('schaltet nichts, solange es gesperrt ist', () => {
    const { onToggle } = zeichne({ disabled: true })

    const schalter = screen.getByRole('switch', { name: 'Einladen erlauben' })
    expect(schalter).toBeDisabled()
    fireEvent.click(schalter)
    expect(onToggle).not.toHaveBeenCalled()
  })
})
