/**
 * Puristischer Lesemodus: Lesbarkeits-Heuristik, Bereinigung und Voll-Extraktion.
 * Läuft entweder direkt im DOM eines WebView2-Tabs (via Runtime.evaluate)
 * oder lokal auf geparsten DOM-Dokumenten (Unit-Tests & Offline-Verarbeitung).
 */
import { nativ } from './nativ'

export interface ExtrahierterArtikel {
  titel: string
  autor: string | null
  datum: string | null
  publiziertAm?: string | null
  inhaltHtml: string
  reintext: string
  wortanzahl: number
  lesedauerMinuten: number
  url: string
}

/** Erlaubte Tags im gereinigten Artikel-HTML */
const ERLAUBTE_TAGS = new Set([
  'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'ul', 'ol', 'li', 'blockquote', 'pre', 'code',
  'img', 'figure', 'figcaption', 'strong', 'em',
  'b', 'i', 'a', 'hr', 'table', 'thead', 'tbody',
  'tr', 'th', 'td', 'mark', 'del', 'sub', 'sup', 'span',
])

/** Tags, die samt Inhalt vollständig verworfen werden */
const ZU_ENTFERNENDE_TAGS = new Set([
  'script', 'style', 'iframe', 'noscript', 'nav',
  'footer', 'header', 'form', 'svg', 'button',
  'input', 'select', 'textarea', 'aside', 'canvas',
  'audio', 'video', 'embed', 'object',
])

const STOERUNG_MUSTER =
  /(?:^|[-_ ])(ad|advert(?:isement)?|sponsored?|banner|cookie|comments?|social|share|promo|popup|modal|sidebar|newsletter)(?:[-_ ]|$)/i

/** Störende Fußnoten-, Bearbeiten- und Referenzelemente (z. B. Wikipedia, Dokumentationen) */
export const CLUTTER_SELEKTOREN = [
  '#toc',
  '.toc',
  '.vector-toc',
  'nav#mw-panel-toc',
  '.mw-table-of-contents',
  '.toctitle',
  '#toctitle',
  '#mw-toc-heading',
  'sup.reference',
  '.mw-editsection',
  '.mw-jump-link',
  '.reference',
  'ol.references',
  '.references',
  'div.reflist',
  '.reflist',
  '.printfooter',
  '.catlinks',
  '.navbox',
  '.vertical-navbox',
  '.noprint',
  '.mw-references-wrap',
  '.mw-indicators',
  '.mw-cite-backlink',
  '#siteSub',
]

/**
 * Heuristische Schnellprüfung: Prüft, ob ein Dokument genügend Fließtext
 * oder ein explizites <article>-Element besitzt, um als lesbar zu gelten.
 */
export function pruefeObLesbar(doc: Document): boolean {
  if (doc.querySelector('article')) return true

  const ps = doc.querySelectorAll('p')
  let laenge = 0
  for (let i = 0; i < ps.length; i++) {
    const text = ps[i].textContent?.trim() || ''
    // Nur Absätze mit substanziellem Text zählen
    if (text.length > 20) {
      laenge += text.length
      if (laenge > 350) return true
    }
  }
  return false
}

/**
 * Filtert störende Kind-Elemente rekursiv aus dem übergebenen Wurzel-Element.
 */
function baumBereinigen(el: Element, url: string): void {
  const kinder = Array.from(el.children)
  for (const kind of kinder) {
    const tag = kind.tagName.toLowerCase()

    if (ZU_ENTFERNENDE_TAGS.has(tag)) {
      kind.remove()
      continue
    }

    const klasse = kind.getAttribute('class') || ''
    const id = kind.getAttribute('id') || ''
    if (STOERUNG_MUSTER.test(klasse) || STOERUNG_MUSTER.test(id)) {
      kind.remove()
      continue
    }

    if (kind.matches && kind.matches(CLUTTER_SELEKTOREN.join(', '))) {
      kind.remove()
      continue
    }

    // Kindelemente rekursiv säubern
    baumBereinigen(kind, url)

    // Nicht erlaubte Tags wie div, section auflösen; span nur mit ID als Anker behalten
    if (!ERLAUBTE_TAGS.has(tag) || (tag === 'span' && !kind.getAttribute('id'))) {
      const kindId = kind.getAttribute('id')
      const elTag = el.tagName.toLowerCase()
      if (kindId && !STOERUNG_MUSTER.test(kindId)) {
        if (/^(h[1-6]|p)$/.test(elTag) && !el.getAttribute('id')) {
          el.setAttribute('id', kindId)
        } else {
          const ankerSpan = kind.ownerDocument.createElement('span')
          ankerSpan.setAttribute('id', kindId)
          el.insertBefore(ankerSpan, kind)
        }
      }
      while (kind.firstChild) {
        el.insertBefore(kind.firstChild, kind)
      }
      kind.remove()
      continue
    }

    // Attribute säubern
    const attribute = Array.from(kind.attributes)
    for (const attr of attribute) {
      const name = attr.name.toLowerCase()
      if (name === 'id') {
        if (STOERUNG_MUSTER.test(attr.value)) {
          kind.removeAttribute('id')
        }
      } else if (tag === 'a' && (name === 'href' || name === 'title')) {
        if (name === 'href') {
          try {
            const wert = attr.value.trim()
            if (wert.startsWith('#')) {
              kind.setAttribute('href', wert)
            } else {
              const aufgeloest = new URL(wert, url).href
              if (aufgeloest.startsWith('http://') || aufgeloest.startsWith('https://')) {
                kind.setAttribute('href', aufgeloest)
                kind.setAttribute('target', '_blank')
                kind.setAttribute('rel', 'noopener noreferrer')
              } else {
                kind.removeAttribute('href')
              }
            }
          } catch {
            kind.removeAttribute('href')
          }
        }
      } else if (tag === 'img' && (name === 'src' || name === 'alt' || name === 'title')) {
        if (name === 'src') {
          try {
            const aufgeloest = new URL(attr.value, url).href
            kind.setAttribute('src', aufgeloest)
            kind.setAttribute('loading', 'lazy')
          } catch {
            kind.removeAttribute('src')
          }
        }
      } else if ((tag === 'code' || tag === 'pre') && name === 'class') {
        // Syntax-Klassen (z. B. language-typescript) behalten
        if (!attr.value.startsWith('language-')) {
          kind.removeAttribute('class')
        }
      } else {
        kind.removeAttribute(name)
      }
    }
  }
}

/**
 * Extrahiert bereinigte Artikel-Informationen aus einem DOM-Dokument.
 */
export function artikelAusDomExtrahieren(doc: Document, url: string): ExtrahierterArtikel {
  // 1. Titel
  const ogTitel = doc.querySelector('meta[property="og:title"]')?.getAttribute('content')
  const twitterTitel = doc.querySelector('meta[name="twitter:title"]')?.getAttribute('content')
  const articleH1 = doc.querySelector('article h1')?.textContent
  const firstH1 = doc.querySelector('h1')?.textContent
  const docTitel = doc.title
  const titel = (ogTitel || twitterTitel || articleH1 || firstH1 || docTitel || '').trim()

  // 2. Autor
  const metaAutor =
    doc.querySelector('meta[name="author"]')?.getAttribute('content') ||
    doc.querySelector('meta[property="article:author"]')?.getAttribute('content')
  const relAutor = doc.querySelector('[rel="author"]')?.textContent
  const classAutor = doc.querySelector('.byline, [class*="author"], [itemprop="author"]')?.textContent
  const roherAutor = (metaAutor || relAutor || classAutor || '').trim()
  const autor = roherAutor && roherAutor.length < 100 ? roherAutor : null

  // 3. Datum
  const timeEl = doc.querySelector('time')
  const zeitAttr = timeEl?.getAttribute('datetime') || timeEl?.textContent
  const metaZeit =
    doc.querySelector('meta[property="article:published_time"]')?.getAttribute('content') ||
    doc.querySelector('meta[name="date"]')?.getAttribute('content')
  const datum = (zeitAttr || metaZeit || '').trim() || null

  // 4. Inhaltsbereich finden
  const kandidat =
    doc.querySelector('article') ||
    doc.querySelector('[role="main"]') ||
    doc.querySelector('main') ||
    doc.querySelector('.post-content, .article-content, .entry-content, #content') ||
    doc.body

  const klon = kandidat ? (kandidat.cloneNode(true) as Element) : doc.createElement('div')

  // Störende Fußnoten-, Bearbeiten- und Referenzelemente vorab entfernen (Wikipedia, Dokumentationen etc.)
  for (const sel of CLUTTER_SELEKTOREN) {
    klon.querySelectorAll(sel).forEach((el) => el.remove())
  }
  klon.querySelectorAll('a, sup, span, div, p, li').forEach((el) => {
    const text = el.textContent?.trim() || ''
    const tag = el.tagName.toLowerCase()
    if (
      /^\[?(?:bearbeiten|quelltext bearbeiten|edit|citation needed)\]?$/i.test(text) ||
      /^\[\s*\d+(?:[–,\-\s]+\d+)*\s*\]$/.test(text) ||
      ((tag === 'a' || tag === 'sup' || tag === 'span' || tag === 'li' || text.length < 300) &&
        /^(?:abgerufen (?:von|am)|retrieved (?:from|on))\b/i.test(text))
    ) {
      el.remove()
    }
  })

  baumBereinigen(klon, url)

  // Wenn der Titel bereits als erstes H1 im Klon steht, doppelte Überschrift entfernen
  const erstesH1 = klon.querySelector('h1')
  if (erstesH1) {
    const h1Text = erstesH1.textContent?.trim() || ''
    if (h1Text && (h1Text === titel || titel.startsWith(h1Text) || h1Text.startsWith(titel))) {
      erstesH1.remove()
    }
  }

  const inhaltHtml = klon.innerHTML.trim()

  // 5. Reintext erzeugen (ohne Duplikate durch verschachtelte Elemente wie li > p oder blockquote > p)
  const absaetze: string[] = []
  const elemente = klon.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, blockquote, pre')
  if (elemente.length > 0) {
    const obersteElemente = Array.from(elemente).filter((el) => {
      return !el.parentElement?.closest('p, h1, h2, h3, h4, h5, h6, li, blockquote, pre')
    })
    obersteElemente.forEach((el) => {
      const text = el.textContent?.trim()
      if (text) absaetze.push(text)
    })
  } else {
    const text = klon.textContent?.trim()
    if (text) absaetze.push(text)
  }

  const reintext = absaetze.join('\n\n').trim()

  // 6. Wortanzahl & Lesedauer
  const woerter = reintext.length > 0 ? reintext.split(/\s+/).filter(Boolean) : []
  const wortanzahl = woerter.length
  const lesedauerMinuten = Math.max(1, Math.ceil(wortanzahl / 200))

  return {
    titel,
    autor,
    datum,
    publiziertAm: datum,
    inhaltHtml,
    reintext,
    wortanzahl,
    lesedauerMinuten,
    url,
  }
}

/**
 * JS-Funktion zur Injektion in den WebView2-Tab via Runtime.evaluate.
 * Vollständig eigenständig und unabhängig von Minification/Bundling.
 * Gibt { ok: true, artikel: ExtrahierterArtikel } oder { ok: false, fehler: string } zurück.
 */
export const BROWSER_EXTRAKTION_CODE = `(() => {
  try {
    const ERLAUBTE_TAGS = new Set([
      'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'ul', 'ol', 'li', 'blockquote', 'pre', 'code',
      'img', 'figure', 'figcaption', 'strong', 'em',
      'b', 'i', 'a', 'hr', 'table', 'thead', 'tbody',
      'tr', 'th', 'td', 'mark', 'del', 'sub', 'sup', 'span'
    ]);

    const ZU_ENTFERNENDE_TAGS = new Set([
      'script', 'style', 'iframe', 'noscript', 'nav',
      'footer', 'header', 'form', 'svg', 'button',
      'input', 'select', 'textarea', 'aside', 'canvas',
      'audio', 'video', 'embed', 'object'
    ]);

    const STOERUNG_MUSTER =
      /(?:^|[-_ ])(ad|advert(?:isement)?|sponsored?|banner|cookie|comments?|social|share|promo|popup|modal|sidebar|newsletter)(?:[-_ ]|$)/i;

    const CLUTTER_SELEKTOREN = [
      '#toc',
      '.toc',
      '.vector-toc',
      'nav#mw-panel-toc',
      '.mw-table-of-contents',
      '.toctitle',
      '#toctitle',
      '#mw-toc-heading',
      'sup.reference',
      '.mw-editsection',
      '.mw-jump-link',
      '.reference',
      'ol.references',
      '.references',
      'div.reflist',
      '.reflist',
      '.printfooter',
      '.catlinks',
      '.navbox',
      '.vertical-navbox',
      '.noprint',
      '.mw-references-wrap',
      '.mw-indicators',
      '.mw-cite-backlink',
      '#siteSub'
    ];

    function baumBereinigen(el, url) {
      const kinder = Array.from(el.children);
      for (const kind of kinder) {
        const tag = kind.tagName.toLowerCase();

        if (ZU_ENTFERNENDE_TAGS.has(tag)) {
          kind.remove();
          continue;
        }

        const klasse = kind.getAttribute('class') || '';
        const id = kind.getAttribute('id') || '';
        if (STOERUNG_MUSTER.test(klasse) || STOERUNG_MUSTER.test(id)) {
          kind.remove();
          continue;
        }

        if (kind.matches && kind.matches(CLUTTER_SELEKTOREN.join(', '))) {
          kind.remove();
          continue;
        }

        baumBereinigen(kind, url);

        if (!ERLAUBTE_TAGS.has(tag) || (tag === 'span' && !kind.getAttribute('id'))) {
          const kindId = kind.getAttribute('id');
          const elTag = el.tagName.toLowerCase();
          if (kindId && !STOERUNG_MUSTER.test(kindId)) {
            if (/^(h[1-6]|p)$/.test(elTag) && !el.getAttribute('id')) {
              el.setAttribute('id', kindId);
            } else {
              const ankerSpan = kind.ownerDocument.createElement('span');
              ankerSpan.setAttribute('id', kindId);
              el.insertBefore(ankerSpan, kind);
            }
          }
          while (kind.firstChild) {
            el.insertBefore(kind.firstChild, kind);
          }
          kind.remove();
          continue;
        }

        const attribute = Array.from(kind.attributes);
        for (const attr of attribute) {
          const name = attr.name.toLowerCase();
          if (name === 'id') {
            if (STOERUNG_MUSTER.test(attr.value)) {
              kind.removeAttribute('id');
            }
          } else if (tag === 'a' && (name === 'href' || name === 'title')) {
            if (name === 'href') {
              try {
                const wert = attr.value.trim();
                if (wert.startsWith('#')) {
                  kind.setAttribute('href', wert);
                } else {
                  const aufgeloest = new URL(wert, url).href;
                  if (aufgeloest.startsWith('http://') || aufgeloest.startsWith('https://')) {
                    kind.setAttribute('href', aufgeloest);
                    kind.setAttribute('target', '_blank');
                    kind.setAttribute('rel', 'noopener noreferrer');
                  } else {
                    kind.removeAttribute('href');
                  }
                }
              } catch {
                kind.removeAttribute('href');
              }
            }
          } else if (tag === 'img' && (name === 'src' || name === 'alt' || name === 'title')) {
            if (name === 'src') {
              try {
                const aufgeloest = new URL(attr.value, url).href;
                kind.setAttribute('src', aufgeloest);
                kind.setAttribute('loading', 'lazy');
              } catch {
                kind.removeAttribute('src');
              }
            }
          } else if ((tag === 'code' || tag === 'pre') && name === 'class') {
            if (!attr.value.startsWith('language-')) {
              kind.removeAttribute('class');
            }
          } else {
            kind.removeAttribute(name);
          }
        }
      }
    }

    function artikelAusDomExtrahieren(doc, url) {
      const ogTitel = doc.querySelector('meta[property="og:title"]')?.getAttribute('content');
      const twitterTitel = doc.querySelector('meta[name="twitter:title"]')?.getAttribute('content');
      const articleH1 = doc.querySelector('article h1')?.textContent;
      const firstH1 = doc.querySelector('h1')?.textContent;
      const docTitel = doc.title;
      const titel = (ogTitel || twitterTitel || articleH1 || firstH1 || docTitel || '').trim();

      const metaAutor =
        doc.querySelector('meta[name="author"]')?.getAttribute('content') ||
        doc.querySelector('meta[property="article:author"]')?.getAttribute('content');
      const relAutor = doc.querySelector('[rel="author"]')?.textContent;
      const classAutor = doc.querySelector('.byline, [class*="author"], [itemprop="author"]')?.textContent;
      const roherAutor = (metaAutor || relAutor || classAutor || '').trim();
      const autor = roherAutor && roherAutor.length < 100 ? roherAutor : null;

      const timeEl = doc.querySelector('time');
      const zeitAttr = timeEl?.getAttribute('datetime') || timeEl?.textContent;
      const metaZeit =
        doc.querySelector('meta[property="article:published_time"]')?.getAttribute('content') ||
        doc.querySelector('meta[name="date"]')?.getAttribute('content');
      const datum = (zeitAttr || metaZeit || '').trim() || null;

      const kandidat =
        doc.querySelector('article') ||
        doc.querySelector('[role="main"]') ||
        doc.querySelector('main') ||
        doc.querySelector('.post-content, .article-content, .entry-content, #content') ||
        doc.body;

      const klon = kandidat ? kandidat.cloneNode(true) : doc.createElement('div');

      for (const sel of CLUTTER_SELEKTOREN) {
        klon.querySelectorAll(sel).forEach(el => el.remove());
      }
      klon.querySelectorAll('a, sup, span, div, p, li').forEach(el => {
        const text = el.textContent?.trim() || '';
        const tag = el.tagName.toLowerCase();
        if (
          /^\\[?(?:bearbeiten|quelltext bearbeiten|edit|citation needed)\\]?$/i.test(text) ||
          /^\\[\\s*\\d+(?:[–,\\-\\s]+\\d+)*\\s*\\]$/.test(text) ||
          ((tag === 'a' || tag === 'sup' || tag === 'span' || tag === 'li' || text.length < 300) &&
            /^(?:abgerufen (?:von|am)|retrieved (?:from|on))\\b/i.test(text))
        ) {
          el.remove();
        }
      });

      baumBereinigen(klon, url);

      const erstesH1 = klon.querySelector('h1');
      if (erstesH1) {
        const h1Text = erstesH1.textContent?.trim() || '';
        if (h1Text && (h1Text === titel || titel.startsWith(h1Text) || h1Text.startsWith(titel))) {
          erstesH1.remove();
        }
      }

      const inhaltHtml = klon.innerHTML.trim();

      const absaetze = [];
      const elemente = klon.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, blockquote, pre');
      if (elemente.length > 0) {
        const obersteElemente = Array.from(elemente).filter(el => {
          return !el.parentElement?.closest('p, h1, h2, h3, h4, h5, h6, li, blockquote, pre');
        });
        obersteElemente.forEach(el => {
          const text = el.textContent?.trim();
          if (text) absaetze.push(text);
        });
      } else {
        const text = klon.textContent?.trim();
        if (text) absaetze.push(text);
      }

      const reintext = absaetze.join('\\n\\n').trim();
      const woerter = reintext.length > 0 ? reintext.split(/\\s+/).filter(Boolean) : [];
      const wortanzahl = woerter.length;
      const lesedauerMinuten = Math.max(1, Math.ceil(wortanzahl / 200));

      return {
        titel,
        autor,
        datum,
        publiziertAm: datum,
        inhaltHtml,
        reintext,
        wortanzahl,
        lesedauerMinuten,
        url
      };
    }

    const tabUrl = (window.location && window.location.href && window.location.href !== 'about:blank')
      ? window.location.href
      : (document.baseURI || (window.location && window.location.href) || '');
    const artikel = artikelAusDomExtrahieren(document, tabUrl);
    return { ok: true, artikel };
  } catch (err) {
    return { ok: false, fehler: String(err && err.message ? err.message : err) };
  }
})()`;

export const BROWSER_LESBAR_CODE = `(() => {
  try {
    if (document.querySelector('article')) return true;
    const ps = document.querySelectorAll('p');
    let laenge = 0;
    for (let i = 0; i < ps.length; i++) {
      const text = (ps[i].textContent || '').trim();
      if (text.length > 20) {
        laenge += text.length;
        if (laenge > 350) return true;
      }
    }
    return false;
  } catch {
    return false;
  }
})()`;

/**
 * Führt die Schnellprüfung im Tab via DevTools-Protokoll aus.
 */
export async function istSeiteLesbarPruefen(tabId: string): Promise<boolean> {
  try {
    const res = await nativ.tabProtokoll(tabId, 'Runtime.evaluate', {
      expression: BROWSER_LESBAR_CODE,
      returnByValue: true,
      silent: true,
    })
    if (res?.exceptionDetails) {
      console.warn('[Lesemodus] Schnellprüfung Ausnahme im Tab:', res.exceptionDetails)
      return false
    }
    const result = res?.result as { value?: unknown } | undefined
    return Boolean(result?.value)
  } catch {
    return false
  }
}

/** Cache für laufende Extraktionen, um redundante parallele Aufrufe zu bündeln */
const laufendeExtraktionen = new Map<string, Promise<ExtrahierterArtikel>>()

/**
 * Extrahiert den vollständigen Artikel aus einem Tab.
 */
export async function artikelExtrahieren(tabId: string, url: string): Promise<ExtrahierterArtikel> {
  const cacheKey = `${tabId}:${url}`
  const laufend = laufendeExtraktionen.get(cacheKey)
  if (laufend) {
    return laufend
  }

  const extraktion = (async () => {
    let protokollVersucht = false
    try {
      protokollVersucht = true
      const res = await nativ.tabProtokoll(tabId, 'Runtime.evaluate', {
        expression: BROWSER_EXTRAKTION_CODE,
        returnByValue: true,
        awaitPromise: true,
      })

      if (res !== null && res !== undefined) {

        if (res.exceptionDetails) {
          const fehlerBeschreibung =
            (res.exceptionDetails as any)?.exception?.description ||
            (res.exceptionDetails as any)?.text ||
            'CDP Runtime.evaluate Ausnahme im Tab'
          console.error('[Lesemodus] CDP Runtime.evaluate Ausnahme im Tab:', res.exceptionDetails)
          throw new Error(fehlerBeschreibung)
        }

        const result = res.result as
          | {
              value?:
                | { ok: true; artikel: ExtrahierterArtikel }
                | { ok: false; fehler: string }
                | ExtrahierterArtikel
            }
          | undefined

        const val = result?.value
        if (val && typeof val === 'object') {
          if ('ok' in val) {
            if (val.ok && val.artikel) {
              return val.artikel
            }
            if (!val.ok) {
              console.warn('[Lesemodus] Extraktions-Skript meldete Fehler:', val.fehler)
              throw new Error(val.fehler || 'Für diese Seite konnte kein lesbarer Artikeltext extrahiert werden.')
            }
          } else if ('inhaltHtml' in val) {
            return val as ExtrahierterArtikel
          }
        }

        throw new Error('Für diese Seite konnte kein lesbarer Artikeltext extrahiert werden.')
      }
    } catch (err) {
      if (protokollVersucht) {
        console.warn('[Lesemodus] Extraktion über Tab-Protokoll fehlgeschlagen:', err)
        throw err
      }
      throw err
    }

    if (typeof document !== 'undefined') {
      const istHostFenster =
        document.getElementById('root') !== null ||
        document.querySelector('[data-msb-fenster]') !== null ||
        (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window)

      if (!istHostFenster) {
        return artikelAusDomExtrahieren(document, url)
      }
    }

    throw new Error('Für diese Seite konnte kein lesbarer Artikeltext extrahiert werden.')
  })()

  laufendeExtraktionen.set(cacheKey, extraktion)
  try {
    return await extraktion
  } finally {
    laufendeExtraktionen.delete(cacheKey)
  }
}
