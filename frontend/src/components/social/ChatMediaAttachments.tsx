import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText, Download, Loader2, AlertCircle, RefreshCw } from 'lucide-react'
import { getSafeAttachmentUrl } from '@/lib/sanitizeSvg'
import { ladeAnhangHerunter } from '@/api/social'
import type { MedienZeiger } from '@/services/medienKrypto'
import { toast } from '@/stores/toastStore'

export const chatMediaBlobCache = new Map<string, string>()

/**
 * Woran ein Anhang hängt: wer ihn geschickt hat und in welche Mailbox.
 *
 * Beides reist **nicht** mit dem Anhang, sondern kommt aus dem Gespräch. DIS
 * bindet es in die gebundenen Daten jedes Stücks, also scheitert ein Blob, den
 * jemand in ein anderes Gespräch umhängt, schon am Tag. Das hier ersetzt den
 * früheren `AttachmentCryptoContext`, aus dem sich der Schlüssel ableiten ließ.
 */
export interface MedienBindungsKontext {
  absenderId: number
  blindMailboxId: string
}

export interface ImageAttachment {
  dataUrl?: string
  name?: string
  mediaId?: string
  paketSchluessel?: string
  fileId?: string
}

export interface FileAttachment {
  name: string
  sizeBytes: number
  mimeType: string
  dataUrl?: string
  mediaId?: string
  paketSchluessel?: string
  fileId?: string
}

/**
 * Eine Sprachnotiz. Bis 09/2026 reiste sie als data-URL **im
 * Nachrichten-JSON** und blähte damit jeden Umschlag um ein Vielfaches auf.
 * Jetzt liegt sie als Anhang im Medienspeicher wie jedes Bild, und im Umschlag
 * steht nur noch, wo sie liegt und womit sie aufgeht.
 */
export interface AudioAttachment {
  mediaId?: string
  paketSchluessel?: string
  fileId?: string
  durationSeconds: number
  mimeType: string
  /**
   * Die eigene Aufnahme, solange sie noch hochgeladen wird. Bleibt **lokal**:
   * in den Nachrichten-Payload geht ausschließlich der Zeiger auf den Blob.
   */
  dataUrl?: string
}

/**
 * Eine Videonotiz.
 *
 * Vorher trug sie einen `mediaKey` für ein eigenes AES-GCM — zu dem es nie
 * einen hochgeladenen Blob gab. Die Aufnahme wurde verschlüsselt, der Umschlag
 * verworfen und eine lokale Blob-URL verschickt, die beim Empfänger ins Leere
 * zeigte. Jetzt geht sie denselben Weg wie jeder andere Anhang.
 */
export interface VideoNoteAttachment {
  mediaId?: string
  paketSchluessel?: string
  fileId?: string
  durationSeconds: number
  width: number
  height: number
  mimeType: string
  thumbnailDataUrl?: string
}

/** Liefert den Zeiger, wenn der Anhang vollständig auf einen Blob verweist. */
export function medienZeiger(
  anhang: { mediaId?: string; paketSchluessel?: string; fileId?: string }
): MedienZeiger | null {
  if (!anhang.mediaId || !anhang.paketSchluessel || !anhang.fileId) return null
  return {
    mediaId: anhang.mediaId,
    paketSchluessel: anhang.paketSchluessel,
    fileId: anhang.fileId,
  }
}

/**
 * Holt einen Anhang und gibt eine anzeigbare URL zurück, `null` bei Fehlschlag.
 *
 * Der gemeinsame Weg für Ton und Videonotiz. Das Bild hat seinen eigenen, weil
 * es zusätzlich mit der lokalen data-URL des Absenders umgehen muss.
 */
export async function holeAnhangUrl(
  anhang: { mediaId?: string; paketSchluessel?: string; fileId?: string },
  bindung: MedienBindungsKontext
): Promise<string | null> {
  const zeiger = medienZeiger(anhang)
  if (!zeiger) return null
  const zwischengespeichert = chatMediaBlobCache.get(zeiger.mediaId)
  if (zwischengespeichert) return zwischengespeichert
  try {
    const klartext = await ladeAnhangHerunter(zeiger, bindung)
    const sicher = getSafeAttachmentUrl(klartext)
    if (!sicher) return null
    chatMediaBlobCache.set(zeiger.mediaId, sicher)
    return sicher
  } catch {
    return null
  }
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
  bindung: MedienBindungsKontext
  onViewImage: (url: string) => void
  isSelf?: boolean
}

export function ChatMediaImage({ attachment, bindung, onViewImage }: ChatMediaImageProps) {
  const { t } = useTranslation()

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

  // Einzelwerte statt des Objekts: eine bei jedem Rendern neu gebaute Referenz
  // triebe den Effekt unten in eine Schleife.
  const absenderId = bindung.absenderId
  const blindMailboxId = bindung.blindMailboxId
  const paketSchluessel = attachment.paketSchluessel
  const fileId = attachment.fileId

  const loadKey = `${mediaId || ''}:${absenderId}:${blindMailboxId}:${fileId ?? ''}`

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

      const zeiger = medienZeiger({ mediaId, paketSchluessel, fileId })
      if (!zeiger) {
        // Altbestand: ein Anhang aus der Zeit vor dem Paketschlüssel. Sein
        // Schlüssel ließ sich aus den Benutzerkennungen ableiten, der Weg
        // dorthin ist geschlossen — auch lesend.
        setError(true)
        hasLoadedRef.current = null
        return
      }

      const decrypted = await ladeAnhangHerunter(zeiger, { absenderId, blindMailboxId })
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
  }, [mediaId, absenderId, blindMailboxId, paketSchluessel, fileId])

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
        <span className="text-[11px] opacity-75 font-medium">{t('social.attachment.imageLoading')}</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-status-destructive/20 bg-status-destructive/5 my-1 p-3 flex flex-col items-center justify-center min-h-[90px] w-56 sm:w-64 space-y-2 text-center">
        <div className="flex items-center gap-1.5 text-xs text-status-destructive font-medium">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{t('social.attachment.imageFailed')}</span>
        </div>
        <button
          type="button"
          onClick={() => void loadMedia()}
          className="px-2.5 py-1 text-[11px] font-medium rounded-lg bg-surface-container-high hover:bg-surface-container-highest transition-colors border border-outline-variant/30 flex items-center gap-1 cursor-pointer"
        >
          <RefreshCw className="w-3 h-3" />
          <span>{t('common.retry')}</span>
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
        alt={attachment.name || t('social.attachment.imageAlt')}
        onClick={() => onViewImage(effectiveUrl)}
        className="max-h-60 w-auto object-cover rounded-lg hover:opacity-95 transition-opacity"
      />
    </div>
  )
}

export interface ChatMediaFileProps {
  attachment: FileAttachment
  bindung: MedienBindungsKontext
  isSelf?: boolean
}

export function ChatMediaFile({ attachment, bindung, isSelf = false }: ChatMediaFileProps) {
  const { t } = useTranslation()

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
          const zeiger = medienZeiger(attachment)
          if (!zeiger) {
            // Altbestand aus der Zeit der ableitbaren Kanalschlüssel.
            toast.error(t('social.attachment.legacyFormat'))
            return
          }
          decrypted = await ladeAnhangHerunter(zeiger, bindung)
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
          toast.error(t('social.attachment.decryptBadFormat'))
        }
      } catch {
        toast.error(t('social.attachment.decryptFailed'))
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

    toast.error(t('social.attachment.blocked'))
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
