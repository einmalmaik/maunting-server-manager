import { afterEach, expect, it, vi } from 'vitest'
import { downloadChatMedia, getChatMediaSignedUrl } from './social'
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

it('keeps legacy attachments usable without a secret mailbox', async () => {
  const proof = vi.spyOn(mailbox, 'nachweisKopf')
  vi.spyOn(client, 'api').mockResolvedValue({ signed_url: '/social/media/test/download' })
  vi.spyOn(client, 'apiStream').mockResolvedValue(new Response('legacy-ciphertext'))
  const { signed_url } = await getChatMediaSignedUrl('test')
  expect(await downloadChatMedia(signed_url)).toBe('legacy-ciphertext')
  expect(proof).not.toHaveBeenCalled()
})
