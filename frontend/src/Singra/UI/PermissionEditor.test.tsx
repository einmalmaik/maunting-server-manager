/**
 * Ein Berechtigungshäkchen ohne Namen ist für einen Screenreader nur ein
 * "Kontrollkästchen, nicht aktiviert" — bei rund 90 Rechten hintereinander also
 * unbenutzbar, obwohl die Auswahl selbst funktioniert. Diese Datei hält fest,
 * dass jedes Häkchen seinen sichtbaren Titel als zugänglichen Namen trägt.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PermissionEditor } from './PermissionEditor'
import type { PermissionDef } from '@/types/permissions'

// 'ai.brandneu.use' steht bewusst NICHT in PERMISSION_DETAILS: So ist belegt,
// dass der Name dem angezeigten Titel folgt und nicht der Detailtabelle — ein
// Recht, das das Backend neu ausliefert, bleibt damit ebenfalls ansagbar.
const permissions: PermissionDef[] = [
  { key: 'ai.chat.use', group: 'panel', label: 'ai.chat.use' },
  { key: 'ai.usage.read.all', group: 'panel', label: 'ai.usage.read.all' },
  { key: 'ai.brandneu.use', group: 'panel', label: 'Frisch aus dem Katalog' },
]

describe('PermissionEditor', () => {
  it('gibt jedem Berechtigungshäkchen einen vorlesbaren Namen', () => {
    render(
      <PermissionEditor
        permissions={permissions}
        selected={new Set<string>(['ai.usage.read.all'])}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getByRole('checkbox', { name: 'KI-Chat verwenden' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Gesamte KI-Nutzung einsehen' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Frisch aus dem Katalog' })).toBeInTheDocument()
  })

  it('schaltet die Berechtigung bei einem Klick genau einmal um', () => {
    // Wächter gegen die naheliegende, aber falsche Reparatur per <label htmlFor>:
    // die ließe den Umschalter des umschließenden <div> zweimal laufen.
    const onChange = vi.fn()
    render(
      <PermissionEditor
        permissions={permissions}
        selected={new Set<string>()}
        onChange={onChange}
      />,
    )

    fireEvent.click(screen.getByRole('checkbox', { name: 'KI-Chat verwenden' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenLastCalledWith(new Set(['ai.chat.use']))
  })
})
