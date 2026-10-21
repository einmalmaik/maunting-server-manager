import { afterEach, expect, it, vi } from 'vitest'
import { downloadChatMedia, fetchAlleE2eeEnvelopes, getChatMediaSignedUrl } from './social'
import * as client from './client'
import * as mailbox from '@/services/mailboxNachweis'

vi.mock('@/services/mailboxPush', () => ({ eigenerPushAbdruck: vi.fn() }))

afterEach(() => vi.restoreAllMocks())

it('sends mailbox proof in both request headers, never in the signed URL', async () => {
  vi.spyOn(mailbox, 'nachweisKopf').mockReturnValue({ 'X-Mailbox-Token': 'synthetic-proof' })
  const api = vi.spyOn(client, 'api').mockResolvedValue({ signed_url: '/social/media/test/download?token=signature' })
  const stream = vi.spyOn(client, 'apiStream').mockResolvedValue(new Response('ciphertext'))
  const { signed_url } = await getChatMediaSignedUrl('test', 900, 'mailbox')
  expect(api).toHaveBeenCalledWith('/social/media/test/signed-url?ttl=900', {
    headers: { 'X-Mailbox-Token': 'synthetic-proof' },
  })
  expect(await downloadChatMedia(signed_url, 'mailbox')).toBe('ciphertext')
  expect(stream).toHaveBeenCalledWith(signed_url, {
    method: 'GET', headers: { Accept: '*/*', 'X-Mailbox-Token': 'synthetic-proof' },
  })
  expect(signed_url).not.toContain('synthetic-proof')
})

it('liest eine Mailbox Seite für Seite, bis nichts mehr kommt', async () => {
  const seite = (von: number, bis: number) =>
    Array.from({ length: bis - von + 1 }, (_, i) => ({
      id: von + i,
      blind_mailbox_id: 'mailbox',
      ciphertext_envelope: `env-${von + i}`,
      client_uuid: null,
      created_at: '2026-10-21T10:00:00Z',
    }))
  const api = vi
    .spyOn(client, 'api')
    .mockResolvedValueOnce(seite(1, 200))
    .mockResolvedValueOnce(seite(201, 202))

  const alle = await fetchAlleE2eeEnvelopes('mailbox')

  expect(alle).toHaveLength(202)
  expect(alle.at(-1)?.id).toBe(202)
  expect(api).toHaveBeenCalledTimes(2)
  // `since_id` muss auf der höchsten gelesenen Kennung stehen, nicht auf einer
  // Anzahl: Umschläge werden gelöscht, eine Zählung überspränge dann welche.
  expect(String(api.mock.calls[0][0])).toContain('limit=200')
  expect(String(api.mock.calls[1][0])).toContain('since_id=200')
})

it('keeps legacy attachments usable without a secret mailbox', async () => {
  const proof = vi.spyOn(mailbox, 'nachweisKopf')
  vi.spyOn(client, 'api').mockResolvedValue({ signed_url: '/social/media/test/download' })
  vi.spyOn(client, 'apiStream').mockResolvedValue(new Response('legacy-ciphertext'))
  const { signed_url } = await getChatMediaSignedUrl('test')
  expect(await downloadChatMedia(signed_url)).toBe('legacy-ciphertext')
  expect(proof).not.toHaveBeenCalled()
})
