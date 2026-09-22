/**
 * Ein Berechtigungsschalter ohne Namen ist für einen Screenreader nur ein
 * "Schalter, aus" — bei rund 90 Rechten hintereinander also unbenutzbar,
 * obwohl die Auswahl selbst funktioniert. Diese Datei hält fest, dass jeder
 * Schalter seinen sichtbaren Titel im zugänglichen Namen trägt.
 *
 * Und sie hält fest, dass dieser Titel übersetzt wird. Die Rechtetexte standen
 * bis zuletzt fest verdrahtet auf Deutsch im Quelltext — in einem Panel, das
 * elf Sprachen ausliefert, las ein englischsprachiger Administrator die
 * Erklärung zu `system.secrets.rotate` also gar nicht.
 *
 * Seit dem 22.09.2026 rendert der Editor über `RechteAbschnitte`, dasselbe
 * Bauteil wie der Messenger. Der frühere Test „erklärt das Recht beim
 * Überfahren" ist ersatzlos weg, und zwar nicht, weil die Erklärung
 * verschwunden wäre, sondern weil sie jetzt **immer** dasteht: auf einem Gerät
 * ohne Maus gab es sie vorher überhaupt nicht. Der Nachfolgetest prüft genau
 * das.
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

  it('gibt jedem Berechtigungsschalter einen vorlesbaren Namen', () => {
    zeichne(new Set<string>(['ai.usage.read.all']))

    expect(screen.getByRole('switch', { name: 'KI-Chat verwenden erlauben' })).toHaveAttribute(
      'aria-checked',
      'false',
    )
    expect(
      screen.getByRole('switch', { name: 'Gesamte KI-Nutzung einsehen erlauben' }),
    ).toHaveAttribute('aria-checked', 'true')
    expect(
      screen.getByRole('switch', { name: 'Frisch aus dem Katalog erlauben' }),
    ).toBeInTheDocument()
  })

  it('schaltet die Berechtigung bei einem Klick genau einmal um', () => {
    const onChange = vi.fn()
    zeichne(new Set<string>(), onChange)

    fireEvent.click(screen.getByRole('switch', { name: 'KI-Chat verwenden erlauben' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenLastCalledWith(new Set(['ai.chat.use']))
  })

  it('nimmt ein gesetztes Recht beim Klick wieder weg', () => {
    const onChange = vi.fn()
    zeichne(new Set<string>(['ai.chat.use']), onChange)

    fireEvent.click(screen.getByRole('switch', { name: 'KI-Chat verwenden erlauben' }))

    expect(onChange).toHaveBeenLastCalledWith(new Set<string>())
  })

  it('zeigt Rechtetitel, Gruppentitel und Knöpfe in der gewählten Sprache', async () => {
    await i18n.changeLanguage('en')
    zeichne()

    expect(screen.getByRole('switch', { name: 'Allow Use AI chat' })).toBeInTheDocument()
    expect(screen.getByText('AI permissions')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Select all' })).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText(i18n.t('permissionEditor.searchPlaceholder')),
    ).toBeInTheDocument()
    // Kein deutscher Rest: der alte fest verdrahtete Titel darf nicht mehr auftauchen.
    expect(screen.queryByText('KI-Chat verwenden')).toBeNull()
  })

  it('zeigt die Erklärung ohne Zutun — nicht erst beim Überfahren', async () => {
    await i18n.changeLanguage('en')
    zeichne()

    // Kein mouseEnter, kein Klick: die Beschreibung steht in der Zeile.
    expect(
      screen.getByText(/Role limits and tool permissions are checked in the backend/),
    ).toBeInTheDocument()
  })

  it('zeigt die rohe Kennung neben dem Titel', () => {
    // Sie steht in Fehlermeldungen, im Prüfprotokoll und in der Hoster-API.
    zeichne()

    expect(screen.getByText('ai.usage.read.all')).toBeInTheDocument()
  })

  it('filtert über die Suche und blendet leere Gruppen aus', () => {
    zeichne()

    fireEvent.change(screen.getByPlaceholderText(i18n.t('permissionEditor.searchPlaceholder')), {
      target: { value: 'usage' },
    })

    expect(
      screen.getByRole('switch', { name: 'Gesamte KI-Nutzung einsehen erlauben' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: 'KI-Chat verwenden erlauben' })).toBeNull()
  })

  it('meldet eine leere Suche als leer statt als leere Abschnitte', () => {
    zeichne()

    fireEvent.change(screen.getByPlaceholderText(i18n.t('permissionEditor.searchPlaceholder')), {
      target: { value: 'gibtesnicht' },
    })

    expect(screen.getByText(i18n.t('permissionEditor.empty'))).toBeInTheDocument()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('sperrt alle Schalter, solange gesperrt ist', () => {
    render(
      <PermissionEditor
        permissions={permissions}
        selected={new Set<string>()}
        onChange={vi.fn()}
        disabled
      />,
    )

    for (const schalter of screen.getAllByRole('switch')) {
      expect(schalter).toBeDisabled()
    }
  })
})
