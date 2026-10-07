export interface SearchEngine {
  id: string
  name: string
  searchUrl: string
  suggestUrl?: string
  icon: string
  description: string
}

export const SEARCH_ENGINES: Record<string, SearchEngine> = {
  google: {
    id: 'google',
    name: 'Google',
    searchUrl: 'https://www.google.com/search?q=%s',
    icon: '🌐',
    description: 'Standard-Suchmaschine weltweit',
  },
  ecosia: {
    id: 'ecosia',
    name: 'Ecosia',
    searchUrl: 'https://www.ecosia.org/search?q=%s',
    icon: '🌳',
    description: 'Pflanzt Bäume mit deinen Suchanfragen',
  },
  duckduckgo: {
    id: 'duckduckgo',
    name: 'DuckDuckGo',
    searchUrl: 'https://duckduckgo.com/?q=%s',
    icon: '🦆',
    description: 'Privatsphäre ohne Tracking',
  },
  brave: {
    id: 'brave',
    name: 'Brave Search',
    searchUrl: 'https://search.brave.com/search?q=%s',
    icon: '🦁',
    description: 'Unabhängiger Suchindex ohne Nutzerprofiling',
  },
  bing: {
    id: 'bing',
    name: 'Microsoft Bing',
    searchUrl: 'https://www.bing.com/search?q=%s',
    icon: '🔍',
    description: 'Microsoft Websuche',
  },
  searxng: {
    id: 'searxng',
    name: 'Eigenes SearXNG (MSM)',
    searchUrl: '%s/search?q=%q',
    icon: '🛡️',
    description: 'Dein selbstgehosteter Meta-Such-Dienst',
  },
}

/**
 * Prüft, ob eine Eingabe eine direkte Web-Adresse (URL/Domain/IP) ist
 * oder als Suchbegriff behandelt werden soll.
 */
export function istUrl(eingabe: string): boolean {
  const getrimmt = eingabe.trim()
  if (!getrimmt) return false

  // Hat Schema (http://, https://, file://, about:)
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//i.test(getrimmt) || getrimmt.startsWith('about:')) {
    return true
  }

  // Leerzeichen enthalten -> mit hoher Wahrscheinlichkeit Suchbegriff
  if (/\s/.test(getrimmt)) {
    return false
  }

  // Lokale Adressen (localhost, 127.0.0.1, IP-Adressen)
  if (/^(localhost|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(:\d+)?(\/.*)?$/i.test(getrimmt)) {
    return true
  }

  // Domain mit gültiger TLD (z.B. example.com, heise.de)
  if (/^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(\/.*)?$/i.test(getrimmt)) {
    return true
  }

  return false
}

/**
 * Baut die vollständige Ziel-URL aus einer Omnibox-Eingabe.
 */
export function baueZielUrl(
  eingabe: string,
  engineId: string = 'google',
  searxngCustomUrl?: string
): string {
  const getrimmt = eingabe.trim()
  if (!getrimmt) return 'about:blank'

  if (istUrl(getrimmt)) {
    if (getrimmt.startsWith('about:')) return getrimmt
    if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//i.test(getrimmt)) {
      return `https://${getrimmt}`
    }
    return getrimmt
  }

  // Suchmaschine nutzen
  if (engineId === 'searxng' && searxngCustomUrl) {
    const basis = searxngCustomUrl.replace(/\/+$/, '')
    return `${basis}/search?q=${encodeURIComponent(getrimmt)}`
  }

  const engine = SEARCH_ENGINES[engineId] || SEARCH_ENGINES.google
  return engine.searchUrl.replace('%s', encodeURIComponent(getrimmt))
}
