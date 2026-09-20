import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import {
  ChatMediaImage,
  ChatMediaFile,
  formatFileSize,
  triggerDownload,
  chatMediaBlobCache,
} from './ChatMediaAttachments'
import * as socialApi from '@/api/social'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

vi.mock('@/api/social', () => ({
  getChatMediaSignedUrl: vi.fn(),
  ladeAnhangHerunter: vi.fn(),
}))

/** Absender und Mailbox: die Bindung, gegen die DIS den Anhang geprueft hat. */
const BINDUNG = { absenderId: 1, blindMailboxId: 'mailbox-fuer-den-test' }

/** Ein vollstaendiger Zeiger auf einen Blob im Medienspeicher. */
function zeiger(mediaId: string) {
  return { mediaId, paketSchluessel: 'cGFrZXRzY2hsdWVzc2Vs', fileId: `anhang-${mediaId}` }
}

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
        bindung={BINDUNG}
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
        attachment={{ ...zeiger('media-cached-1'), name: 'cached.jpg' }}
        bindung={BINDUNG}
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

    vi.mocked(socialApi.ladeAnhangHerunter).mockResolvedValue(decryptedData)

    render(
      <ChatMediaImage
        attachment={{ ...zeiger('media-remote-1'), name: 'remote.png' }}
        bindung={BINDUNG}
        onViewImage={onViewImage}
      />
    )

    expect(screen.getByText(i18n.t('social.attachment.imageLoading'))).toBeInTheDocument()

    await waitFor(() => {
      expect(socialApi.ladeAnhangHerunter).toHaveBeenCalledWith(zeiger('media-remote-1'), BINDUNG)
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
    vi.mocked(socialApi.ladeAnhangHerunter).mockRejectedValueOnce(new Error('Network error'))

    render(
      <ChatMediaImage
        attachment={{ ...zeiger('media-error-1'), name: 'error.png' }}
        bindung={BINDUNG}
        onViewImage={onViewImage}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(i18n.t('social.attachment.imageFailed'))).toBeInTheDocument()
    })

    const retryBtn = screen.getByText(i18n.t('common.retry'))
    expect(retryBtn).toBeInTheDocument()

    // Setup success for retry
    vi.mocked(socialApi.ladeAnhangHerunter).mockResolvedValueOnce('data:image/png;base64,retry-success')

    fireEvent.click(retryBtn)

    await waitFor(() => {
      const img = screen.getByAltText('error.png') as HTMLImageElement
      expect(img).toBeInTheDocument()
      expect(img.src).toBe('data:image/png;base64,retry-success')
    })
  })

  it('versucht es erneut, sobald der Paketschluessel nachkommt', async () => {
    // Hier stand bis 09/2026 derselbe Fall mit `userBId: 0` — damals fehlte die
    // zweite Benutzerkennung, aus der sich der Schluessel ableitete. Heute fehlt
    // der Schluessel selbst, und ohne ihn wird gar nicht erst geladen.
    const onViewImage = vi.fn()
    vi.mocked(socialApi.ladeAnhangHerunter).mockResolvedValue('data:image/png;base64,context-success')

    const { rerender } = render(
      <ChatMediaImage
        attachment={{ mediaId: 'media-context-update', name: 'context.png' }}
        bindung={BINDUNG}
        onViewImage={onViewImage}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(i18n.t('social.attachment.imageFailed'))).toBeInTheDocument()
    })
    expect(socialApi.ladeAnhangHerunter).not.toHaveBeenCalled()

    rerender(
      <ChatMediaImage
        attachment={{ ...zeiger('media-context-update'), name: 'context.png' }}
        bindung={BINDUNG}
        onViewImage={onViewImage}
      />
    )

    await waitFor(() => {
      expect(socialApi.ladeAnhangHerunter).toHaveBeenCalledWith(
        zeiger('media-context-update'),
        BINDUNG
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
        bindung={BINDUNG}
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
    vi.mocked(socialApi.ladeAnhangHerunter).mockResolvedValue(decryptedData)

    render(
      <ChatMediaFile
        attachment={{
          name: 'contract.pdf',
          sizeBytes: 2048,
          mimeType: 'application/pdf',
          ...zeiger('file-media-999'),
        }}
        bindung={BINDUNG}
      />
    )

    const card = screen.getByText('contract.pdf').closest('a')
    expect(card).toBeInTheDocument()

    fireEvent.click(card!)

    await waitFor(() => {
      expect(socialApi.ladeAnhangHerunter).toHaveBeenCalledWith(zeiger('file-media-999'), BINDUNG)
    })
  })

  it('oeffnet einen Altbestand-Anhang ohne Paketschluessel gar nicht erst', async () => {
    render(
      <ChatMediaFile
        attachment={{
          name: 'alt.pdf',
          sizeBytes: 2048,
          mimeType: 'application/pdf',
          mediaId: 'altbestand-1',
        }}
        bindung={BINDUNG}
      />
    )

    fireEvent.click(screen.getByText('alt.pdf').closest('a')!)

    await waitFor(() => {
      expect(socialApi.ladeAnhangHerunter).not.toHaveBeenCalled()
    })
  })
})
