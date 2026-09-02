/**
 * Ein Berechtigungshäkchen ohne Namen ist für einen Screenreader nur ein
 * "Kontrollkästchen, nicht aktiviert" — bei rund 90 Rechten hintereinander also
 * unbenutzbar, obwohl die Auswahl selbst funktioniert. Diese Datei hält fest,
 * dass jedes Häkchen seinen sichtbaren Titel als zugänglichen Namen trägt.
 *
 * Und sie hält fest, dass dieser Titel übersetzt wird. Die Rechtetexte standen
 * bis zuletzt fest verdrahtet auf Deutsch im Quelltext — in einem Panel, das
 * elf Sprachen ausliefert, las ein englischsprachiger Administrator die
 * Erklärung zu `system.secrets.rotate` also gar nicht.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PermissionEditor } from './PermissionEditor'
import i18n from '@/i18n'
import type { PermissionDef } from '@/types/permissions'

// 'ai.brandneu.use' steht bewusst in keiner Sprachdatei: So ist belegt, dass der
// Name dem angezeigten Titel folgt und bei einem unbekannten Schlüssel auf den
// Text aus dem Backend-Katalog zurückfällt — ein Recht, das das Backend neu
// ausliefert, bleibt damit ansagbar und zeigt nie einen rohen Schlüssel.
const permissions: PermissionDef[] = [
  { key: 'ai.chat.use', group: 'panel', label: 'ai.chat.use' },
  { key: 'ai.usage.read.all', group: 'panel', label: 'ai.usage.read.all' },
  { key: 'ai.brandneu.use', group: 'panel', label: 'Frisch aus dem Katalog' },
]

function zeichne(selected = new Set<string>(), onChange = vi.fn()) {
  return render(
    <PermissionEditor permissions={permissions} selected={selected} onChange={onChange} />,
  )
}

describe('PermissionEditor', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('gibt jedem Berechtigungshäkchen einen vorlesbaren Namen', () => {
    zeichne(new Set<string>(['ai.usage.read.all']))

    expect(screen.getByRole('checkbox', { name: 'KI-Chat verwenden' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Gesamte KI-Nutzung einsehen' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Frisch aus dem Katalog' })).toBeInTheDocument()
  })

  it('schaltet die Berechtigung bei einem Klick genau einmal um', () => {
    // Wächter gegen die naheliegende, aber falsche Reparatur per <label htmlFor>:
    // die ließe den Umschalter des umschließenden <div> zweimal laufen.
    const onChange = vi.fn()
    zeichne(new Set<string>(), onChange)

    fireEvent.click(screen.getByRole('checkbox', { name: 'KI-Chat verwenden' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenLastCalledWith(new Set(['ai.chat.use']))
  })

  it('zeigt Rechtetitel, Gruppentitel und Knöpfe in der gewählten Sprache', async () => {
    await i18n.changeLanguage('en')
    zeichne()

    expect(screen.getByRole('checkbox', { name: 'Use AI chat' })).toBeInTheDocument()
    expect(screen.getByText('AI permissions')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Select all' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search permission...')).toBeInTheDocument()
    // Kein deutscher Rest: der alte fest verdrahtete Titel darf nicht mehr auftauchen.
    expect(screen.queryByText('KI-Chat verwenden')).toBeNull()
  })

  it('erklärt das Recht beim Überfahren — in derselben Sprache', async () => {
    await i18n.changeLanguage('en')
    zeichne()

    fireEvent.mouseEnter(screen.getByText('Use AI chat').closest('div')!)

    expect(
      screen.getByText(/Role limits and tool permissions are checked in the backend/),
    ).toBeInTheDocument()
  })
})
