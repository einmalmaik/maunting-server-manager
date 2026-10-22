/**
 * Die Häkchen an einer Zeile — eine Regel, zwei Aufrufstellen.
 *
 * Am 22.10.2026 blieb die Lesebestätigung bei einem Haken stehen: der Abruf
 * las nur noch Neues, damit lagen die Quittungsumschläge hinter dem Lesestand,
 * die Partnerzähler standen nach jedem Chatwechsel auf 0. Gespeichert war die
 * Zeile danach als „gelesen, aber nicht gelesen" (`status: 'read'`,
 * `isRead: false`) — und die Blase zeigt das Häkchen an `isRead`.
 */

import { describe, expect, it } from 'vitest'

import { hakenFuer } from './quittung'

const OHNE = { maxPartnerReadId: 0, maxPartnerDeliveredId: 0 }

describe('hakenFuer', () => {
  it('zeigt einen Haken, solange niemand quittiert hat', () => {
    expect(hakenFuer({ id: 358, isSelf: true }, OHNE)).toEqual({
      isRead: false,
      isDelivered: false,
      status: 'sent',
    })
  })

  it('zeigt zwei graue Haken nach der Zustellquittung', () => {
    expect(hakenFuer({ id: 358, isSelf: true }, { maxPartnerReadId: 0, maxPartnerDeliveredId: 358 })).toEqual({
      isRead: false,
      isDelivered: true,
      status: 'delivered',
    })
  })

  it('zeigt zwei blaue Haken nach der Lesequittung', () => {
    expect(hakenFuer({ id: 358, isSelf: true }, { maxPartnerReadId: 358, maxPartnerDeliveredId: 0 })).toEqual({
      isRead: true,
      isDelivered: true,
      status: 'read',
    })
  })

  it('nimmt einen schon gelesenen Stand nicht zurück, wenn die Zähler fehlen', () => {
    // Der gemeldete Fehler. Ein Chatwechsel setzte die Zähler auf 0; die Zeile
    // wusste aber noch, dass sie gelesen war. Bis 22.10.2026 wurde sie trotzdem
    // auf „gesendet" heruntergeschrieben.
    const gespeichert = { id: 358, isSelf: true, status: 'read' as const, isRead: false, isDelivered: false }
    expect(hakenFuer(gespeichert, OHNE)).toEqual({
      isRead: true,
      isDelivered: true,
      status: 'read',
    })
  })

  it('nimmt auch eine Zustellung nicht zurück', () => {
    const gespeichert = { id: 358, isSelf: true, status: 'delivered' as const }
    expect(hakenFuer(gespeichert, OHNE)).toEqual({
      isRead: false,
      isDelivered: true,
      status: 'delivered',
    })
  })

  it('lässt eine gescheiterte eigene Nachricht gescheitert', () => {
    // Ein roter Ausrufezeichen-Haken darf nicht zu „gesendet" werden, nur weil
    // ein Durchlauf ohne Quittung vorbeikommt.
    expect(hakenFuer({ id: 0, isSelf: true, status: 'failed' }, OHNE)).toEqual({
      isRead: false,
      isDelivered: false,
      status: 'failed',
    })
  })

  it('rührt fremde Nachrichten nicht an', () => {
    // Quittungen gelten für die eigenen Zeilen; was der Partner geschrieben
    // hat, bekommt kein Häkchen.
    expect(
      hakenFuer(
        { id: 400, isSelf: false, status: 'sent' },
        { maxPartnerReadId: 500, maxPartnerDeliveredId: 500 },
      ),
    ).toEqual({ isRead: false, isDelivered: false, status: 'sent' })
  })
})
