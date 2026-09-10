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
            val.startsWith('data:') &&
            !val.startsWith('data:image/')
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
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, (match) => (isDangerousCss(match) ? '' : match))
    .replace(/\bon\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    .replace(/\bstyle\s*=\s*["']([^"']*)["']/gi, (match, val) => (isDangerousCss(val) ? '' : match))
    .replace(/(?:href|src|xlink:href)\s*=\s*["']?\s*(?:javascript|vbscript|data:text\/html|data:application\/javascript|data:text\/javascript):[^"'\s>]+/gi, '')

  return clean
}

export function getSafeAttachmentUrl(url?: string | null): string | null {
  if (!url) return null
  const trimmed = url.trim()
  const lower = trimmed.toLowerCase().replace(/[\s\x00-\x1f\x7f-\x9f]/g, '')
  if (
    lower.startsWith('javascript:') ||
    lower.startsWith('vbscript:') ||
    lower.startsWith('data:text/html') ||
    lower.startsWith('data:text/javascript') ||
    lower.startsWith('data:application/javascript')
  ) {
    return null
  }
  if (
    lower.startsWith('data:image/') ||
    lower.startsWith('data:audio/') ||
    lower.startsWith('data:video/') ||
    lower.startsWith('data:application/') ||
    lower.startsWith('data:text/plain') ||
    lower.startsWith('blob:') ||
    lower.startsWith('/api/') ||
    lower.startsWith('http://') ||
    lower.startsWith('https://')
  ) {
    return trimmed
  }
  return null
}
