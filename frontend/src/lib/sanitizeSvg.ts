/**
 * Client-seitige SVG-Sanitization zum Schutz vor XSS-Angriffen.
 *
 * Blockiert und neutralisiert:
 * - <script> Tags
 * - Inline Event-Handler (onload, onerror, onclick, etc.)
 * - Gefaehrliche URI-Schemata (javascript:, vbscript:, data:text/html)
 * - foreignObject, iframe, embed, object
 * - XML-Entitaeten (XXE)
 */

const DANGEROUS_ELEMENTS = [
  'script',
  'foreignobject',
  'iframe',
  'object',
  'embed',
  'applet',
  'meta',
  'link',
  'form',
  'input',
  'button',
  'base',
  'set',
  'animate',
  'animatetransform',
  'handler',
  'feimage',
  'use',
]

function cleanCssText(css: string): string {
  const noComments = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const noEscapes = noComments.replace(/\\([0-9a-fA-F]{1,6}\s?|.)/g, (_match, hexOrChar) => {
    const trimmed = hexOrChar.trim()
    try {
      const cp = parseInt(trimmed, 16)
      if (!isNaN(cp) && cp >= 32 && cp <= 126) {
        return String.fromCharCode(cp)
      }
    } catch {
      // fallback
    }
    return hexOrChar
  })
  return noEscapes.toLowerCase().replace(/[\s\x00-\x1f\x7f-\x9f]/g, '')
}

function isDangerousCss(css: string): boolean {
  const clean = cleanCssText(css)
  return (
    clean.includes('javascript:') ||
    clean.includes('vbscript:') ||
    clean.includes('expression(') ||
    clean.includes('behavior:') ||
    clean.includes('@import') ||
    clean.includes('-moz-binding') ||
    clean.includes('url(data:text/html') ||
    clean.includes('url(data:application/javascript') ||
    clean.includes('url(data:text/javascript')
  )
}

export function sanitizeSvg(rawSvg: string): string {
  if (!rawSvg || typeof rawSvg !== 'string') return ''

  // 1. XXE / DTD Protection
  if (/<!(?:entity|doctype)\b/i.test(rawSvg)) {
    return ''
  }

  // 2. Parse SVG in DOMParser if in browser environment
  if (typeof DOMParser !== 'undefined') {
    try {
      const parser = new DOMParser()
      const doc = parser.parseFromString(rawSvg, 'image/svg+xml')

      // Check for parser errors
      if (doc.querySelector('parsererror')) {
        return ''
      }

      // Check root is SVG
      const root = doc.documentElement
      if (!root || root.tagName.toLowerCase() !== 'svg') {
        return ''
      }

      // Recursively remove dangerous elements and attributes
      const sanitizeNode = (node: Element) => {
        const tagName = node.tagName.toLowerCase()
        if (DANGEROUS_ELEMENTS.includes(tagName)) {
          node.remove()
          return
        }

        if (tagName === 'style') {
          const styleContent = node.textContent || ''
          if (isDangerousCss(styleContent)) {
            node.remove()
            return
          }
        }

        if (tagName === 'image' || tagName === 'use') {
          const href = (
            node.getAttribute('href') ||
            node.getAttribute('xlink:href') ||
            node.getAttribute('src') ||
            ''
          ).toLowerCase().replace(/[\s\x00-\x1f\x7f-\x9f]/g, '')
          if (
            href.startsWith('http:') ||
            href.startsWith('https:') ||
            href.startsWith('//') ||
            (tagName === 'image' && href.startsWith('data:') && !href.startsWith('data:image/'))
          ) {
            node.remove()
            return
          }
        }

        // Check attributes
        const attributesToRemove: string[] = []
        for (let i = 0; i < node.attributes.length; i++) {
          const attr = node.attributes[i]
          const name = attr.name.toLowerCase()
          const val = attr.value.toLowerCase().replace(/[\s\x00-\x1f\x7f-\x9f]/g, '')

          if (name.startsWith('on')) {
            attributesToRemove.push(attr.name)
          } else if (
            val.includes('javascript:') ||
            val.includes('vbscript:') ||
            val.includes('data:text/html') ||
            val.includes('data:text/javascript') ||
            val.includes('data:application/javascript')
          ) {
            attributesToRemove.push(attr.name)
          } else if (name === 'style' && isDangerousCss(attr.value)) {
            attributesToRemove.push(attr.name)
          } else if (
            (name === 'href' || name === 'xlink:href' || name === 'src') &&
            (
              val.startsWith('http:') ||
              val.startsWith('https:') ||
              val.startsWith('//') ||
              (val.startsWith('data:') && !val.startsWith('data:image/'))
            )
          ) {
            attributesToRemove.push(attr.name)
          }
        }

        for (const attrName of attributesToRemove) {
          node.removeAttribute(attrName)
        }

        // Process children
        const children = Array.from(node.children)
        for (const child of children) {
          sanitizeNode(child)
        }
      }

      sanitizeNode(root)
      return root.outerHTML
    } catch {
      return ''
    }
  }

  // Fallback regex sanitizer (fuer Umgebungen ohne DOMParser wie Node-Tests)
  let clean = rawSvg
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
    .replace(/<foreignobject\b[^<]*(?:(?!<\/foreignobject>)<[^<]*)*<\/foreignobject>/gi, '')
    .replace(/<(?:set|animate|animatetransform|handler|feimage|use)\b[^<]*(?:(?!<\/(?:set|animate|animatetransform|handler|feimage|use)>)<[^<]*)*<\/(?:set|animate|animatetransform|handler|feimage|use)>/gi, '')
    .replace(/<(?:set|animate|animatetransform|handler|feimage|use)\b[^>]*\/?>/gi, '')
    .replace(/<image\b[^>]*\b(?:href|xlink:href|src)\s*=\s*["']?\s*(?:https?:|\/\/)[^>]*\/?>/gi, '')
    .replace(/<image\b[^>]*\b(?:href|xlink:href|src)\s*=\s*["']?\s*(?:https?:|\/\/)[^<]*(?:(?!<\/image>)<[^<]*)*<\/image>/gi, '')
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, (match) => (isDangerousCss(match) ? '' : match))
    .replace(/\bon\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    .replace(/\bstyle\s*=\s*["']([^"']*)["']/gi, (match, val) => (isDangerousCss(val) ? '' : match))
    .replace(/(?:href|src|xlink:href)\s*=\s*(?:"(?:javascript|vbscript|data:text\/html|data:application\/javascript|data:text\/javascript|https?:|\/\/)[^"]*"|'(?:javascript|vbscript|data:text\/html|data:application\/javascript|data:text\/javascript|https?:|\/\/)[^']*'|(?:javascript|vbscript|data:text\/html|data:application\/javascript|data:text\/javascript|https?:|\/\/)[^\s>]+)/gi, '')

  return clean
}

export function getSafeAttachmentUrl(url?: string | null): string | null {
  if (!url) return null
  const trimmed = url.trim()
  const lowerHeader = trimmed.slice(0, 128).toLowerCase().replace(/[\s\x00-\x1f\x7f-\x9f]/g, '')
  if (
    lowerHeader.startsWith('javascript:') ||
    lowerHeader.startsWith('vbscript:') ||
    lowerHeader.startsWith('data:text/html') ||
    lowerHeader.startsWith('data:text/javascript') ||
    lowerHeader.startsWith('data:application/javascript') ||
    lowerHeader.startsWith('data:application/xhtml') ||
    lowerHeader.startsWith('data:text/xml') ||
    lowerHeader.startsWith('//')
  ) {
    return null
  }
  if (
    lowerHeader.startsWith('data:image/') ||
    lowerHeader.startsWith('data:audio/') ||
    lowerHeader.startsWith('data:video/') ||
    lowerHeader.startsWith('data:application/') ||
    lowerHeader.startsWith('data:text/') ||
    lowerHeader.startsWith('data:;base64,') ||
    lowerHeader.startsWith('data:base64,') ||
    lowerHeader.startsWith('blob:') ||
    lowerHeader.startsWith('/api/')
  ) {
    return trimmed
  }
  if (lowerHeader.startsWith('http://') || lowerHeader.startsWith('https://')) {
    try {
      const parsed = new URL(trimmed)
      if (parsed.pathname.startsWith('/api/')) {
        const isSameOrigin = typeof window !== 'undefined' && window.location && parsed.origin === window.location.origin
        const isPanelTest = parsed.hostname === 'panel.test' || parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1'
        let isApiOrigin = false
        if (typeof window !== 'undefined') {
          const msmUrl = (window as { __MSM_API_URL?: string }).__MSM_API_URL
          if (msmUrl && parsed.origin === msmUrl.replace(/\/+$/, '')) {
            isApiOrigin = true
          }
        }
        if (typeof import.meta !== 'undefined') {
          const envApi = (import.meta.env?.VITE_API_URL as string | undefined)?.trim()
          if (envApi && parsed.origin === envApi.replace(/\/+$/, '')) {
            isApiOrigin = true
          }
        }
        if (isSameOrigin || isPanelTest || isApiOrigin) {
          return trimmed
        }
      }
    } catch {
      // Ungültige URL
    }
    return null
  }
  return null
}
