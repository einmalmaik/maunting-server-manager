import { describe, expect, it } from 'vitest'
import { neueBezugstafel } from './nachrichtBezug'

describe('Messenger Security (K-1 & K-2 Verification)', () => {
  describe('K-2: Nachrichtenaenderung und -loeschung Autorisierungspruefung', () => {
    it('erlaubt dem Autor, seine eigene Nachricht zu bearbeiten', () => {
      const aenderungen = neueBezugstafel<{ newText: string; editedAt: string }>()
      const aliceId = 10
      const payload = { target_id: 101, new_text: 'Korrigierter Text' }
      aenderungen.merke(payload, { newText: 'Korrigierter Text', editedAt: '2026-09-21T00:00:00Z' }, aliceId)

      const msg = { id: 101, senderId: aliceId }
      const aenderung = aenderungen.finde(msg, (urheber) => urheber === undefined || Number(urheber) === Number(msg.senderId))

      expect(aenderung).toBeDefined()
      expect(aenderung?.newText).toBe('Korrigierter Text')
    })

    it('weist den Versuch eines Angreifers ab, eine fremde Nachricht zu bearbeiten', () => {
      const aenderungen = neueBezugstafel<{ newText: string; editedAt: string }>()
      const aliceId = 10
      const malloryId = 99
      const payload = { target_id: 101, new_text: 'Gefaelschter Text von Mallory' }
      aenderungen.merke(payload, { newText: 'Gefaelschter Text von Mallory', editedAt: '2026-09-21T00:00:00Z' }, malloryId)

      const msg = { id: 101, senderId: aliceId }
      const aenderung = aenderungen.finde(msg, (urheber) => urheber === undefined || Number(urheber) === Number(msg.senderId))

      expect(aenderung).toBeUndefined()
    })

    it('erlaubt einem Gruppenmoderator/Admin, fremde Nachrichten zu loeschen', () => {
      const loeschungen = neueBezugstafel<{ deletedAt: string }>()
      const aliceId = 10
      const moderatorId = 2
      const activeGroup = {
        owner_user_id: 1,
        members: [
          { user_id: 1, username: 'owner', role: 'owner' },
          { user_id: moderatorId, username: 'mod', role: 'moderator' },
          { user_id: aliceId, username: 'alice', role: 'member' },
        ],
      }
      const payload = { target_id: 101 }
      loeschungen.merke(payload, { deletedAt: '2026-09-21T00:00:00Z' }, moderatorId)

      const msg = { id: 101, senderId: aliceId }
      const loeschung = loeschungen.finde(msg, (urheber) => {
        if (urheber === undefined) return true
        if (Number(urheber) === Number(msg.senderId)) return true
        if (activeGroup?.members) {
          const member = activeGroup.members.find((x) => Number(x.user_id) === Number(urheber))
          if (member) {
            return (
              Number(activeGroup.owner_user_id) === Number(urheber) ||
              member.role === 'admin' ||
              member.role === 'moderator' ||
              Boolean(member.can_pin_messages)
            )
          }
        }
        return false
      })

      expect(loeschung).toBeDefined()
    })

    it('verhindert, dass ein normales Gruppenmitglied fremde Nachrichten loescht', () => {
      const loeschungen = neueBezugstafel<{ deletedAt: string }>()
      const aliceId = 10
      const malloryId = 99
      const activeGroup = {
        owner_user_id: 1,
        members: [
          { user_id: 1, username: 'owner', role: 'owner' },
          { user_id: aliceId, username: 'alice', role: 'member' },
          { user_id: malloryId, username: 'mallory', role: 'member' },
        ],
      }
      const payload = { target_id: 101 }
      loeschungen.merke(payload, { deletedAt: '2026-09-21T00:00:00Z' }, malloryId)

      const msg = { id: 101, senderId: aliceId }
      const loeschung = loeschungen.finde(msg, (urheber) => {
        if (urheber === undefined) return true
        if (Number(urheber) === Number(msg.senderId)) return true
        if (activeGroup?.members) {
          const member = activeGroup.members.find((x) => Number(x.user_id) === Number(urheber))
          if (member) {
            return (
              Number(activeGroup.owner_user_id) === Number(urheber) ||
              member.role === 'admin' ||
              member.role === 'moderator' ||
              Boolean(member.can_pin_messages)
            )
          }
        }
        return false
      })

      expect(loeschung).toBeUndefined()
    })
  })
})
