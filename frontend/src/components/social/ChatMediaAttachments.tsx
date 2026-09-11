import React, { useState, useEffect, useCallback } from 'react'
import { FileText, Download, Loader2, AlertCircle, RefreshCw } from 'lucide-react'
import { getSafeAttachmentUrl } from '@/lib/sanitizeSvg'
import {
  getChatMediaSignedUrl,
  downloadAndDecryptChatAttachment,
} from '@/api/social'
import type { AttachmentCryptoContext } from '@/services/e2eeCrypto'
import { toast } from '@/stores/toastStore'

export const chatMediaBlobCache = new Map<string, string>()

export interface ImageAttachment {
  dataUrl?: string
  name?: string
  mediaId?: string
}

export interface FileAttachment {
  name: string
  sizeBytes: number
  mimeType: string
  dataUrl?: string
  mediaId?: string
}

export function formatFileSize(bytes?: number): string {
  if (bytes === undefined || bytes === null || isNaN(bytes) || bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Triggers browser download of a URL (supporting base64 data URLs via Blob conversion to prevent browser truncation).
 */
export function triggerDownload(url: string, filename: string): void {
  let blobUrl: string | null = null
  let finalHref = url

  if (url.startsWith('data:')) {
    try {
      const commaIdx = url.indexOf(',')
      if (commaIdx !== -1) {
        const mime = url.slice(5, commaIdx).split(';')[0] || 'application/octet-stream'
        const base64Data = url.slice(commaIdx + 1).trim().replace(/\s/g, '')
        const bstr = atob(base64Data)
        let n = bstr.length
        const u8arr = new Uint8Array(n)
        while (n--) {
          u8arr[n] = bstr.charCodeAt(n)
        }
        const blob = new Blob([u8arr], { type: mime })
        blobUrl = URL.createObjectURL(blob)
        finalHref = blobUrl
      }
    } catch {
      finalHref = url
    }
  }

  const downloadLink = document.createElement('a')
  downloadLink.href = finalHref
  downloadLink.download = filename
  document.body.appendChild(downloadLink)
  downloadLink.click()
  document.body.removeChild(downloadLink)

  if (blobUrl) {
    setTimeout(() => URL.revokeObjectURL(blobUrl!), 2000)
  }
}

export interface ChatMediaImageProps {
  attachment: ImageAttachment
  cryptoContext: AttachmentCryptoContext
  onViewImage: (url: string) => void
  isSelf?: boolean
}

export function ChatMediaImage({ attachment, cryptoContext, onViewImage }: ChatMediaImageProps) {
  const directSafeUrl = getSafeAttachmentUrl(attachment.dataUrl)
  const mediaId = attachment.mediaId
  const cached = mediaId ? chatMediaBlobCache.get(mediaId) : null
  const [decryptedUrl, setDecryptedUrl] = useState<string | null>(directSafeUrl || cached || null)
  const [loading, setLoading] = useState<boolean>(!directSafeUrl && !cached && Boolean(mediaId))
  const [error, setError] = useState<boolean>(false)

  // Synchronize state whenever attachment data changes
  useEffect(() => {
    const safe = getSafeAttachmentUrl(attachment.dataUrl)
    const memCached = mediaId ? chatMediaBlobCache.get(mediaId) : null
    if (safe || memCached) {
      setDecryptedUrl(safe || memCached || null)
      setLoading(false)
      setError(false)
    } else if (mediaId) {
      setLoading(true)
      setError(false)
    }
  }, [attachment.dataUrl, mediaId])

  // Extract primitives to avoid infinite re-render loops from object reference recreation
  const groupId = cryptoContext.groupId
  const userAId = cryptoContext.userAId
  const userBId = cryptoContext.userBId
  const teamId = cryptoContext.teamId
  const sharedSecret = cryptoContext.sharedSecret

  const loadKey = `${mediaId || ''}:${groupId ?? ''}:${userAId ?? ''}:${userBId ?? ''}:${teamId ?? ''}`

  const loadMedia = useCallback(async () => {
    if (!mediaId) return
    setLoading(true)
    setError(false)
    try {
      const cachedItem = chatMediaBlobCache.get(mediaId)
      if (cachedItem) {
        setDecryptedUrl(cachedItem)
        setLoading(false)
        return
      }

      const activeCtx: AttachmentCryptoContext = {
        groupId,
        userAId,
        userBId,
        teamId,
        sharedSecret,
      }

      const { signed_url } = await getChatMediaSignedUrl(mediaId)
      const decrypted = await downloadAndDecryptChatAttachment(signed_url, activeCtx)
      const safe = getSafeAttachmentUrl(decrypted)
      if (safe) {
        chatMediaBlobCache.set(mediaId, safe)
        setDecryptedUrl(safe)
      } else {
        setError(true)
        hasLoadedRef.current = null
      }
    } catch {
      setError(true)
      hasLoadedRef.current = null
    } finally {
      setLoading(false)
    }
  }, [mediaId, groupId, userAId, userBId, teamId, sharedSecret])

  const hasLoadedRef = React.useRef<string | null>(null)

  useEffect(() => {
    if (!directSafeUrl && !cached && mediaId && hasLoadedRef.current !== loadKey) {
      hasLoadedRef.current = loadKey
      void loadMedia()
    }
  }, [directSafeUrl, cached, mediaId, loadKey, loadMedia])

  if (loading) {
    return (
      <div className="rounded-xl border border-black/10 my-1 p-6 flex flex-col items-center justify-center min-h-[140px] w-56 sm:w-64 bg-black/5 dark:bg-white/5 space-y-2">
        <Loader2 className="w-5 h-5 animate-spin text-primary opacity-80" />
        <span className="text-[11px] opacity-75 font-medium">Bild wird geladen …</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-destructive/20 bg-destructive/5 my-1 p-3 flex flex-col items-center justify-center min-h-[90px] w-56 sm:w-64 space-y-2 text-center">
        <div className="flex items-center gap-1.5 text-xs text-destructive font-medium">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>Bild konnte nicht geladen werden</span>
        </div>
        <button
          type="button"
          onClick={() => void loadMedia()}
          className="px-2.5 py-1 text-[11px] font-medium rounded-lg bg-surface-container-high hover:bg-surface-container-highest transition-colors border border-outline-variant/30 flex items-center gap-1 cursor-pointer"
        >
          <RefreshCw className="w-3 h-3" />
          <span>Erneut versuchen</span>
        </button>
      </div>
    )
  }

  const effectiveUrl = directSafeUrl || decryptedUrl
  if (!effectiveUrl) {
    return null
  }

  return (
    <div className="rounded-xl overflow-hidden border border-black/10 my-1 cursor-pointer">
      <img
        src={effectiveUrl}
        alt={attachment.name || 'Chat Anhang'}
        onClick={() => onViewImage(effectiveUrl)}
        className="max-h-60 w-auto object-cover rounded-lg hover:opacity-95 transition-opacity"
      />
    </div>
  )
}

export interface ChatMediaFileProps {
  attachment: FileAttachment
  cryptoContext: AttachmentCryptoContext
  isSelf?: boolean
}

export function ChatMediaFile({ attachment, cryptoContext, isSelf = false }: ChatMediaFileProps) {
  const [isDownloading, setIsDownloading] = useState(false)
  const safeName = attachment.name?.replace(/[\r\n"']/g, '') || 'attachment'
  const directSafeHref = getSafeAttachmentUrl(attachment.dataUrl)

  const handleDownload = async (e: React.MouseEvent) => {
    e.preventDefault()
    if (isDownloading) return

    // 1. If mediaId is present and no direct dataUrl
    if (attachment.mediaId && (!directSafeHref || directSafeHref === '#')) {
      setIsDownloading(true)
      try {
        let decrypted = chatMediaBlobCache.get(attachment.mediaId)
        if (!decrypted) {
          const { signed_url } = await getChatMediaSignedUrl(attachment.mediaId)
          decrypted = await downloadAndDecryptChatAttachment(signed_url, cryptoContext)
          const safe = getSafeAttachmentUrl(decrypted)
          if (safe) {
            chatMediaBlobCache.set(attachment.mediaId, safe)
            decrypted = safe
          }
        }
        const safe = getSafeAttachmentUrl(decrypted)
        if (safe) {
          triggerDownload(safe, safeName)
        } else {
          toast.error('Entschlüsselung des Dateianhangs fehlgeschlagen (ungültiges Format).')
        }
      } catch {
        toast.error('Entschlüsselung des Dateianhangs fehlgeschlagen.')
      } finally {
        setIsDownloading(false)
      }
      return
    }

    // 2. Direct dataUrl or blob href
    if (directSafeHref && directSafeHref !== '#') {
      triggerDownload(directSafeHref, safeName)
      return
    }

    toast.error('Unsicherer oder ungültiger Dateianhang blockiert.')
  }

  return (
    <a
      href={directSafeHref || '#'}
      download={safeName}
      target={directSafeHref && !directSafeHref.startsWith('data:') ? '_blank' : undefined}
      rel="noopener noreferrer"
      onClick={handleDownload}
      className={`flex items-center gap-2.5 p-2.5 rounded-xl border transition-colors cursor-pointer ${
        isSelf
          ? 'bg-black/15 border-white/20 text-white hover:bg-black/25'
          : 'bg-surface-container-low border-outline-variant/30 text-on-surface hover:bg-surface-container'
      }`}
    >
      <div className="p-2 rounded-lg bg-primary/20 text-primary shrink-0">
        <FileText className="w-4 h-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-xs truncate">{safeName}</p>
        <p className="text-[10px] opacity-75">{formatFileSize(attachment.sizeBytes)}</p>
      </div>
      {isDownloading ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin opacity-75 shrink-0" />
      ) : (
        <Download className="w-3.5 h-3.5 opacity-75 shrink-0" />
      )}
    </a>
  )
}
