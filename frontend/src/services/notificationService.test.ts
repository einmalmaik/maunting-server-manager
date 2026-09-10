import { describe, it, expect } from 'vitest'
import { NotificationService } from './notificationService'

describe('NotificationService', () => {
  describe('isOutgoingEcho', () => {
    it('returns true when sender and current user ID are identical', () => {
      expect(NotificationService.isOutgoingEcho(1, 1)).toBe(true)
      expect(NotificationService.isOutgoingEcho('10', 10)).toBe(true)
      expect(NotificationService.isOutgoingEcho(42, '42')).toBe(true)
    })

    it('returns false when sender and current user ID differ', () => {
      expect(NotificationService.isOutgoingEcho(1, 2)).toBe(false)
      expect(NotificationService.isOutgoingEcho('10', '20')).toBe(false)
    })

    it('returns false on null or undefined IDs', () => {
      expect(NotificationService.isOutgoingEcho(null, 1)).toBe(false)
      expect(NotificationService.isOutgoingEcho(1, null)).toBe(false)
      expect(NotificationService.isOutgoingEcho(undefined, undefined)).toBe(false)
      expect(NotificationService.isOutgoingEcho('invalid', 1)).toBe(false)
    })
  })

  describe('shouldNotify (Direct Messages)', () => {
    it('strictly suppresses notification for sender (Outgoing Echo Prevention)', () => {
      const result = NotificationService.shouldNotify({
        recipientId: 2,
        currentUserId: 1,
        senderUserId: 1,
        isRead: false,
      })
      expect(result).toBe(false)
    })

    it('suppresses notification for control messages (read_receipt, delivery_receipt, typing)', () => {
      expect(
        NotificationService.shouldNotify({
          recipientId: 2,
          currentUserId: 2,
          senderUserId: 1,
          isControl: true,
        })
      ).toBe(false)

      expect(
        NotificationService.shouldNotify({
          recipientId: 2,
          currentUserId: 2,
          senderUserId: 1,
          controlType: 'read_receipt',
        })
      ).toBe(false)

      expect(
        NotificationService.shouldNotify({
          recipientId: 2,
          currentUserId: 2,
          senderUserId: 1,
          controlType: 'delivery_receipt',
        })
      ).toBe(false)
    })

    it('suppresses notification when active foreground connection is present', () => {
      expect(
        NotificationService.shouldNotify({
          recipientId: 2,
          currentUserId: 2,
          senderUserId: 1,
          hasActiveForegroundConnection: true,
        })
      ).toBe(false)
    })

    it('suppresses notification when message is already read', () => {
      expect(
        NotificationService.shouldNotify({
          recipientId: 2,
          currentUserId: 2,
          senderUserId: 1,
          isRead: true,
        })
      ).toBe(false)
    })

    it('suppresses notification when current user is neither sender nor recipient', () => {
      expect(
        NotificationService.shouldNotify({
          recipientId: 2,
          currentUserId: 3,
          senderUserId: 1,
          isRead: false,
        })
      ).toBe(false)
    })

    it('triggers notification for valid recipient with unread message', () => {
      expect(
        NotificationService.shouldNotify({
          recipientId: 2,
          currentUserId: 2,
          senderUserId: 1,
          isRead: false,
        })
      ).toBe(true)
    })
  })

  describe('shouldNotifyGroup (Group Messages)', () => {
    const groupMembers = [10, 20, 30]

    it('suppresses notification for sender in group', () => {
      expect(
        NotificationService.shouldNotifyGroup({
          groupMemberIds: groupMembers,
          currentUserId: 10,
          senderUserId: 10,
        })
      ).toBe(false)
    })

    it('suppresses notification for non-members', () => {
      expect(
        NotificationService.shouldNotifyGroup({
          groupMemberIds: groupMembers,
          currentUserId: 99,
          senderUserId: 10,
        })
      ).toBe(false)
    })

    it('suppresses notification for control messages in group', () => {
      expect(
        NotificationService.shouldNotifyGroup({
          groupMemberIds: groupMembers,
          currentUserId: 20,
          senderUserId: 10,
          isControl: true,
        })
      ).toBe(false)

      expect(
        NotificationService.shouldNotifyGroup({
          groupMemberIds: groupMembers,
          currentUserId: 20,
          senderUserId: 10,
          controlType: 'read_receipt',
        })
      ).toBe(false)
    })

    it('suppresses notification when active foreground connection is present in group', () => {
      expect(
        NotificationService.shouldNotifyGroup({
          groupMemberIds: groupMembers,
          currentUserId: 20,
          senderUserId: 10,
          hasActiveForegroundConnection: true,
        })
      ).toBe(false)
    })

    it('notifies legitimate group member for incoming message', () => {
      expect(
        NotificationService.shouldNotifyGroup({
          groupMemberIds: groupMembers,
          currentUserId: 20,
          senderUserId: 10,
        })
      ).toBe(true)
    })
  })

  describe('sanitizePushPayload & preparePushDispatch', () => {
    it('sanitizes plaintext and sensitive keys in E2EE / privacy mode', () => {
      const sanitized = NotificationService.sanitizePushPayload({
        senderName: 'Alice',
        isE2ee: true,
        privacyMode: true,
        extraData: {
          text: 'Super geheimer Text',
          message_text: 'Geheim',
          content: 'Secret content',
          ciphertext_envelope: 'ey...',
          token: 'token123',
          secret: 'shh',
          channel_id: 'chat_123',
        },
      })

      expect(sanitized.text).toBeUndefined()
      expect(sanitized.message_text).toBeUndefined()
      expect(sanitized.content).toBeUndefined()
      expect(sanitized.ciphertext_envelope).toBeUndefined()
      expect(sanitized.token).toBeUndefined()
      expect(sanitized.secret).toBeUndefined()
      expect(sanitized.channel_id).toBe('chat_123')
      expect(sanitized.title).toBe('Neue Nachricht: Alice')
      expect(sanitized.body).toBe('Du hast eine neue verschlüsselte Nachricht erhalten.')
      expect(sanitized.is_e2ee).toBe(true)
      expect(sanitized.privacy_filtered).toBe(true)
    })

    it('preparePushDispatch returns null when sender is target (Echo)', () => {
      const dispatch = NotificationService.preparePushDispatch({
        targetUserId: 1,
        senderUserId: 1,
        title: 'Hallo',
      })
      expect(dispatch).toBeNull()
    })

    it('preparePushDispatch returns null for control messages', () => {
      const dispatch = NotificationService.preparePushDispatch({
        targetUserId: 2,
        senderUserId: 1,
        title: 'Quittung',
        isControl: true,
        controlType: 'read_receipt',
      })
      expect(dispatch).toBeNull()
    })

    it('preparePushDispatch returns null when client is in foreground', () => {
      const dispatch = NotificationService.preparePushDispatch({
        targetUserId: 2,
        senderUserId: 1,
        title: 'Hallo',
        hasActiveForegroundConnection: true,
      })
      expect(dispatch).toBeNull()
    })

    it('preparePushDispatch returns sanitized payload for valid push dispatch', () => {
      const dispatch = NotificationService.preparePushDispatch({
        targetUserId: 2,
        senderUserId: 1,
        title: 'Nachricht',
        isE2ee: true,
        privacyMode: true,
        extraData: {
          text: 'Verschwiegen',
          msg_id: 'm123',
        },
      })

      expect(dispatch).not.toBeNull()
      expect(dispatch?.text).toBeUndefined()
      expect(dispatch?.msg_id).toBe('m123')
      expect(dispatch?.target_user_id).toBe(2)
      expect(dispatch?.sender_user_id).toBe(1)
      expect(dispatch?.is_e2ee).toBe(true)
    })

    it('sanitizes title in E2EE mode to prevent cleartext message preview leaks', () => {
      const sanitized = NotificationService.sanitizePushPayload({
        title: 'Geheime Nachricht mit PIN: 1234',
        senderName: 'Bob',
        isE2ee: true,
        privacyMode: true,
      })

      expect(sanitized.title).toBe('Neue Nachricht: Bob')
      expect(sanitized.title).not.toContain('1234')
      expect(sanitized.body).toBe('Du hast eine neue verschlüsselte Nachricht erhalten.')
    })

    it('recognizes worker/ai prefixes as outgoing echo', () => {
      expect(NotificationService.isOutgoingEcho('worker:42', 42)).toBe(true)
      expect(NotificationService.isOutgoingEcho('ai:42', '42')).toBe(true)
      expect(NotificationService.isOutgoingEcho('actor:42', 42)).toBe(true)
      expect(NotificationService.isOutgoingEcho('user:42', 42)).toBe(true)
      expect(NotificationService.isOutgoingEcho('worker:42', 99)).toBe(false)
    })

    it('dispatchPush delegates to preparePushDispatch', () => {
      const res = NotificationService.dispatchPush({
        targetUserId: 10,
        senderUserId: 5,
        title: 'Push Alert',
        isE2ee: true,
      })
      expect(res).not.toBeNull()
      expect(res?.target_user_id).toBe(10)
    })
  })
})

