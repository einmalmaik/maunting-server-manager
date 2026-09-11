import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ChatMediaImage,
  ChatMediaFile,
  formatFileSize,
  triggerDownload,
  chatMediaBlobCache,
} from './ChatMediaAttachments'
import * as socialApi from '@/api/social'

vi.mock('@/api/social', () => ({
  getChatMediaSignedUrl: vi.fn(),
  downloadAndDecryptChatAttachment: vi.fn(),
}))

describe('formatFileSize', () => {
  it('formats bytes, kilobytes, and megabytes accurately', () => {
    expect(formatFileSize(500)).toBe('500 B')
    expect(formatFileSize(2048)).toBe('2.0 KB')
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB')
  })

  it('safely handles zero, negative, undefined, and NaN inputs without NaN MB', () => {
    expect(formatFileSize(undefined)).toBe('0 B')
    expect(formatFileSize(0)).toBe('0 B')
    expect(formatFileSize(-100)).toBe('0 B')
    expect(formatFileSize(NaN)).toBe('0 B')
  })
})

describe('triggerDownload', () => {
  it('creates an anchor element and clicks it with converted blob for data URLs', () => {
    const clickSpy = vi.fn()
    const appendChildSpy = vi.spyOn(document.body, 'appendChild')
    const removeChildSpy = vi.spyOn(document.body, 'removeChild')

    const origCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag) => {
      const el = origCreateElement(tag)
      if (tag === 'a') {
        el.click = clickSpy
      }
      return el
    })

    const sampleDataUrl = 'data:text/plain;base64,SGVsbG8gV29ybGQ='
    triggerDownload(sampleDataUrl, 'hello.txt')

    expect(appendChildSpy).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    expect(removeChildSpy).toHaveBeenCalled()
  })

  it('handles base64 data URLs with whitespace or newlines correctly', () => {
    const clickSpy = vi.fn()
    const origCreateElement = Document.prototype.createElement
    vi.spyOn(document, 'createElement').mockImplementation(function (this: Document, tag: string) {
      const el = origCreateElement.call(this, tag)
      if (tag === 'a') el.click = clickSpy
      return el
    })

    const dataUrlWithSpaces = 'data:text/plain;base64, SGVsbG8g\n V29ybGQ= '
    expect(() => triggerDownload(dataUrlWithSpaces, 'clean.txt')).not.toThrow()
    expect(clickSpy).toHaveBeenCalled()
  })
})

describe('ChatMediaImage Component', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    chatMediaBlobCache.clear()
  })

  it('renders direct safe dataUrl without network requests', () => {
    const onViewImage = vi.fn()
    const safeDataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    render(
      <ChatMediaImage
        attachment={{ dataUrl: safeDataUrl, name: 'direct.png' }}
        cryptoContext={{ userAId: 1, userBId: 2 }}
        onViewImage={onViewImage}
      />
    )

    const img = screen.getByAltText('direct.png') as HTMLImageElement
    expect(img).toBeInTheDocument()
    expect(img.src).toBe(safeDataUrl)

    fireEvent.click(img)
    expect(onViewImage).toHaveBeenCalledWith(safeDataUrl)
    expect(socialApi.getChatMediaSignedUrl).not.toHaveBeenCalled()
  })

  it('renders instantly from in-memory cache if mediaId was previously cached', () => {
    const onViewImage = vi.fn()
    const cachedUrl = 'data:image/jpeg;base64,cached-image-bytes'
    chatMediaBlobCache.set('media-cached-1', cachedUrl)

    render(
      <ChatMediaImage
        attachment={{ mediaId: 'media-cached-1', name: 'cached.jpg' }}
        cryptoContext={{ userAId: 1, userBId: 2 }}
        onViewImage={onViewImage}
      />
    )

    const img = screen.getByAltText('cached.jpg') as HTMLImageElement
    expect(img).toBeInTheDocument()
    expect(img.src).toBe(cachedUrl)
    expect(socialApi.getChatMediaSignedUrl).not.toHaveBeenCalled()
  })

  it('downloads and decrypts remote attachment if only mediaId is present', async () => {
    const onViewImage = vi.fn()
    const decryptedData = 'data:image/png;base64,decrypted-content'

    vi.mocked(socialApi.getChatMediaSignedUrl).mockResolvedValue({
      media_id: 'media-remote-1',
      signed_url: 'https://bucket.example.test/media/blob.e2ee',
      expires_at: '2026-09-12T00:00:00Z',
    })
    vi.mocked(socialApi.downloadAndDecryptChatAttachment).mockResolvedValue(decryptedData)

    render(
      <ChatMediaImage
        attachment={{ mediaId: 'media-remote-1', name: 'remote.png' }}
        cryptoContext={{ userAId: 1, userBId: 2 }}
        onViewImage={onViewImage}
      />
    )

    expect(screen.getByText('Bild wird geladen …')).toBeInTheDocument()

    await waitFor(() => {
      expect(socialApi.getChatMediaSignedUrl).toHaveBeenCalledWith('media-remote-1')
      expect(socialApi.downloadAndDecryptChatAttachment).toHaveBeenCalledWith(
        'https://bucket.example.test/media/blob.e2ee',
        expect.objectContaining({ userAId: 1, userBId: 2 })
      )
    })

    await waitFor(() => {
      const img = screen.getByAltText('remote.png') as HTMLImageElement
      expect(img).toBeInTheDocument()
      expect(img.src).toBe(decryptedData)
    })

    // Blob cache should be populated
    expect(chatMediaBlobCache.get('media-remote-1')).toBe(decryptedData)
  })

  it('displays error state on failure and retries on click', async () => {
    const onViewImage = vi.fn()
    vi.mocked(socialApi.getChatMediaSignedUrl).mockRejectedValueOnce(new Error('Network error'))

    render(
      <ChatMediaImage
        attachment={{ mediaId: 'media-error-1', name: 'error.png' }}
        cryptoContext={{ userAId: 1, userBId: 2 }}
        onViewImage={onViewImage}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Bild konnte nicht geladen werden')).toBeInTheDocument()
    })

    const retryBtn = screen.getByText('Erneut versuchen')
    expect(retryBtn).toBeInTheDocument()

    // Setup success for retry
    vi.mocked(socialApi.getChatMediaSignedUrl).mockResolvedValueOnce({
      media_id: 'media-error-1',
      signed_url: 'https://bucket.example.test/media/retry.e2ee',
      expires_at: '2026-09-12T00:00:00Z',
    })
    vi.mocked(socialApi.downloadAndDecryptChatAttachment).mockResolvedValueOnce('data:image/png;base64,retry-success')

    fireEvent.click(retryBtn)

    await waitFor(() => {
      const img = screen.getByAltText('error.png') as HTMLImageElement
      expect(img).toBeInTheDocument()
      expect(img.src).toBe('data:image/png;base64,retry-success')
    })
  })

  it('re-attempts decryption if cryptoContext updates with valid participant ID', async () => {
    const onViewImage = vi.fn()
    vi.mocked(socialApi.getChatMediaSignedUrl).mockResolvedValue({
      media_id: 'media-context-update',
      signed_url: 'https://bucket.example.test/media/context.e2ee',
      expires_at: '2026-09-12T00:00:00Z',
    })
    vi.mocked(socialApi.downloadAndDecryptChatAttachment).mockResolvedValue('data:image/png;base64,context-success')

    const { rerender } = render(
      <ChatMediaImage
        attachment={{ mediaId: 'media-context-update', name: 'context.png' }}
        cryptoContext={{ userAId: 1, userBId: 0 }}
        onViewImage={onViewImage}
      />
    )

    // Partner ID becomes available
    rerender(
      <ChatMediaImage
        attachment={{ mediaId: 'media-context-update', name: 'context.png' }}
        cryptoContext={{ userAId: 1, userBId: 42 }}
        onViewImage={onViewImage}
      />
    )

    await waitFor(() => {
      expect(socialApi.downloadAndDecryptChatAttachment).toHaveBeenCalledWith(
        'https://bucket.example.test/media/context.e2ee',
        expect.objectContaining({ userAId: 1, userBId: 42 })
      )
    })
  })
})

describe('ChatMediaFile Component', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    chatMediaBlobCache.clear()
  })

  it('renders direct file attachment and triggers download on click', () => {
    const appendChildSpy = vi.spyOn(document.body, 'appendChild')
    const safeDataUrl = 'data:application/pdf;base64,JVBERi0xLjQK'

    render(
      <ChatMediaFile
        attachment={{
          name: 'report.pdf',
          sizeBytes: 15360,
          mimeType: 'application/pdf',
          dataUrl: safeDataUrl,
        }}
        cryptoContext={{ userAId: 1, userBId: 2 }}
      />
    )

    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(screen.getByText('15.0 KB')).toBeInTheDocument()

    const card = screen.getByText('report.pdf').closest('a')
    expect(card).toBeInTheDocument()

    fireEvent.click(card!)
    expect(appendChildSpy).toHaveBeenCalled()
  })

  it('downloads and decrypts file when only mediaId is present', async () => {
    const decryptedData = 'data:application/pdf;base64,JVBERi0xLjQK'
    vi.mocked(socialApi.getChatMediaSignedUrl).mockResolvedValue({
      media_id: 'file-media-999',
      signed_url: 'https://bucket.example.test/media/file.e2ee',
      expires_at: '2026-09-12T00:00:00Z',
    })
    vi.mocked(socialApi.downloadAndDecryptChatAttachment).mockResolvedValue(decryptedData)

    render(
      <ChatMediaFile
        attachment={{
          name: 'contract.pdf',
          sizeBytes: 2048,
          mimeType: 'application/pdf',
          mediaId: 'file-media-999',
        }}
        cryptoContext={{ userAId: 1, userBId: 2 }}
      />
    )

    const card = screen.getByText('contract.pdf').closest('a')
    expect(card).toBeInTheDocument()

    fireEvent.click(card!)

    await waitFor(() => {
      expect(socialApi.getChatMediaSignedUrl).toHaveBeenCalledWith('file-media-999')
      expect(socialApi.downloadAndDecryptChatAttachment).toHaveBeenCalledWith(
        'https://bucket.example.test/media/file.e2ee',
        expect.objectContaining({ userAId: 1, userBId: 2 })
      )
    })
  })
})
