/**
 * Lightweight client-side image compression utility.
 * Downscales images to a maximum dimension of 1600px with JPEG quality 0.85 if size > 500KB.
 * Eliminates main-thread blocking on large images.
 * Safe fallback for jsdom and test environments.
 */

export interface CompressedImageResult {
  dataUrl: string
  name: string
  mimeType?: string
  width?: number
  height?: number
  sizeBytes?: number
}

const MAX_DIMENSION = 1600
const COMPRESSION_THRESHOLD_BYTES = 500 * 1024 // 500 KB
const JPEG_QUALITY = 0.85

export async function compressImageFile(
  file: File,
  maxDimension: number = MAX_DIMENSION,
  quality: number = JPEG_QUALITY
): Promise<CompressedImageResult> {
  // If not an image or SVG/GIF, or smaller than threshold, read directly without canvas
  if (
    !file.type.startsWith('image/') ||
    file.type === 'image/svg+xml' ||
    file.type === 'image/gif' ||
    file.size <= COMPRESSION_THRESHOLD_BYTES
  ) {
    const dataUrl = await readFileAsDataUrl(file)
    return {
      dataUrl,
      name: file.name,
      mimeType: file.type || 'image/jpeg',
      sizeBytes: file.size,
    }
  }

  // Check if browser environment supports canvas and DOM
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    const dataUrl = await readFileAsDataUrl(file)
    return { dataUrl, name: file.name, mimeType: file.type, sizeBytes: file.size }
  }

  try {
    let width = 0
    let height = 0
    let source: ImageBitmap | HTMLImageElement | null = null

    if (typeof createImageBitmap === 'function') {
      try {
        const bitmap = await createImageBitmap(file)
        width = bitmap.width
        height = bitmap.height
        source = bitmap
      } catch {
        source = null
      }
    }

    if (!source) {
      source = await loadImageElement(file)
      width = source.naturalWidth || source.width
      height = source.naturalHeight || source.height
    }

    if (width === 0 || height === 0) {
      const dataUrl = await readFileAsDataUrl(file)
      return { dataUrl, name: file.name, mimeType: file.type, sizeBytes: file.size }
    }

    // Calculate scaled dimensions keeping aspect ratio
    let targetWidth = width
    let targetHeight = height

    if (width > maxDimension || height > maxDimension) {
      if (width > height) {
        targetWidth = maxDimension
        targetHeight = Math.round((height * maxDimension) / width)
      } else {
        targetHeight = maxDimension
        targetWidth = Math.round((width * maxDimension) / height)
      }
    }

    const canvas = document.createElement('canvas')
    canvas.width = targetWidth
    canvas.height = targetHeight
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      const dataUrl = await readFileAsDataUrl(file)
      return { dataUrl, name: file.name, mimeType: file.type, sizeBytes: file.size }
    }

    ctx.drawImage(source, 0, 0, targetWidth, targetHeight)

    if ('close' in source && typeof (source as any).close === 'function') {
      ;(source as any).close()
    }

    const compressedDataUrl = canvas.toDataURL('image/jpeg', quality)
    return {
      dataUrl: compressedDataUrl,
      name: file.name.replace(/\.[^/.]+$/, '') + '.jpg',
      mimeType: 'image/jpeg',
      width: targetWidth,
      height: targetHeight,
      sizeBytes: Math.round((compressedDataUrl.length * 3) / 4),
    }
  } catch {
    const dataUrl = await readFileAsDataUrl(file)
    return {
      dataUrl,
      name: file.name,
      mimeType: file.type,
      sizeBytes: file.size,
    }
  }
}

export function readFileAsDataUrl(file: File | Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

function loadImageElement(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const url = URL.createObjectURL(file)
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = (e) => {
      URL.revokeObjectURL(url)
      reject(e)
    }
    img.src = url
  })
}
