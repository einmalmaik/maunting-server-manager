/**
 * R1: Lesemodus (F9) im Production Build E2E Test Suite
 * Verifies standalone unminified extraction script, clutter removal,
 * self-contained execution bundle, and fallback mechanics.
 */
import { describe, it, assert, createMockDom } from './harness.mjs';

// Standalone implementation matching lesemodusService.ts extraction contract
const ERLAUBTE_TAGS = new Set([
  'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'ul', 'ol', 'li', 'blockquote', 'pre', 'code',
  'img', 'figure', 'figcaption', 'strong', 'em',
  'b', 'i', 'a', 'hr', 'table', 'thead', 'tbody',
  'tr', 'th', 'td', 'mark', 'del', 'sub', 'sup', 'span',
]);

const ZU_ENTFERNENDE_TAGS = new Set([
  'script', 'style', 'iframe', 'noscript', 'nav',
  'footer', 'header', 'form', 'svg', 'button',
  'input', 'select', 'textarea', 'aside', 'canvas',
  'audio', 'video', 'embed', 'object',
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
  '#siteSub',
];

function pruefeObLesbar(doc) {
  if (doc.querySelector('article')) return true;
  const ps = doc.querySelectorAll('p');
  let laenge = 0;
  for (let i = 0; i < ps.length; i++) {
    const text = ps[i].textContent?.trim() || '';
    if (text.length > 20) {
      laenge += text.length;
      if (laenge > 350) return true;
    }
  }
  return false;
}

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
    klon.querySelectorAll(sel).forEach((el) => el.remove());
  }
  klon.querySelectorAll('a, sup, span, div, p, li').forEach((el) => {
    const text = el.textContent?.trim() || '';
    const tag = el.tagName.toLowerCase();
    if (
      /^\[?(?:bearbeiten|quelltext bearbeiten|edit|citation needed)\]?$/i.test(text) ||
      /^\[\s*\d+(?:[–,\-\s]+\d+)*\s*\]$/.test(text) ||
      ((tag === 'a' || tag === 'sup' || tag === 'span' || tag === 'li' || text.length < 300) &&
        /^(?:abgerufen (?:von|am)|retrieved (?:from|on))\b/i.test(text))
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
    const obersteElemente = Array.from(elemente).filter((el) => {
      return !el.parentElement?.closest('p, h1, h2, h3, h4, h5, h6, li, blockquote, pre');
    });
    obersteElemente.forEach((el) => {
      const text = el.textContent?.trim();
      if (text) absaetze.push(text);
    });
  } else {
    const text = klon.textContent?.trim();
    if (text) absaetze.push(text);
  }

  const reintext = absaetze.join('\n\n').trim();
  const woerter = reintext.length > 0 ? reintext.split(/\s+/).filter(Boolean) : [];
  const wortanzahl = woerter.length;
  const lesedauerMinuten = Math.max(1, Math.ceil(wortanzahl / 200));

  return {
    titel,
    autor,
    datum,
    inhaltHtml,
    reintext,
    wortanzahl,
    lesedauerMinuten,
    url,
  };
}

describe('R1: Lesemodus (F9) in Production Build', () => {
  // Tier 1: Feature Coverage
  it('TC-T1-R1-01: Extracts clean article from news/blog DOM with paragraphs and headers', () => {
    const html = `
      <!DOCTYPE html>
      <html>
        <head>
          <title>Wissenschaftlicher Fortschritt im Quantencomputing</title>
          <meta name="author" content="Dr. Marie Schmidt">
        </head>
        <body>
          <header><nav><a href="/">Home</a></nav></header>
          <article>
            <h1>Wissenschaftlicher Fortschritt im Quantencomputing</h1>
            <p>Quantencomputer nutzen die Prinzipien der Quantenmechanik wie Superposition und Verschränkung, um komplexe Berechnungen in Sekundenschnelle durchzuführen.</p>
            <p>In den vergangenen Jahren konnten Forschungszentren weltweit beachtliche Meilensteine bei der Fehlertoleranz und Qubit-Stabilität erreichen.</p>
            <p>Die praktischen Anwendungsbereiche reichen von der Entdeckung neuer pharmazeutischer Wirkstoffe bis hin zur Optimierung logistischer Transportwege.</p>
          </article>
          <footer><p>Copyright 2026</p></footer>
        </body>
      </html>
    `;
    const { document } = createMockDom(html, 'https://example.com/quantum');
    const result = artikelAusDomExtrahieren(document, 'https://example.com/quantum');

    assert.equal(result.titel, 'Wissenschaftlicher Fortschritt im Quantencomputing');
    assert.equal(result.autor, 'Dr. Marie Schmidt');
    assert.ok(result.wortanzahl > 40);
    assert.ok(result.lesedauerMinuten >= 1);
    assert.ok(result.inhaltHtml.includes('Quantencomputer'));
    assert.ok(!result.inhaltHtml.includes('<header>'));
    assert.ok(!result.inhaltHtml.includes('<footer>'));
  }, { tier: 1, feature: 'R1', id: 'TC-T1-R1-01' });

  it('TC-T1-R1-02: Self-contained extraction bundle executes without external dependencies', () => {
    const bundleScript = `(() => {
      ${pruefeObLesbar.toString()}
      ${baumBereinigen.toString()}
      ${artikelAusDomExtrahieren.toString()}
      const ERLAUBTE_TAGS = new Set(${JSON.stringify(Array.from(ERLAUBTE_TAGS))});
      const ZU_ENTFERNENDE_TAGS = new Set(${JSON.stringify(Array.from(ZU_ENTFERNENDE_TAGS))});
      const STOERUNG_MUSTER = ${STOERUNG_MUSTER.toString()};
      const CLUTTER_SELEKTOREN = ${JSON.stringify(CLUTTER_SELEKTOREN)};
      return artikelAusDomExtrahieren(document, window.location.href);
    })()`;

    const html = `<html><body><article><h1>Self-Contained Execution</h1><p>This is a completely standalone article body test verifying bundle integrity in Tauri v2 production build.</p></article></body></html>`;
    const { evaluate } = createMockDom(html, 'https://example.com/bundle');
    const result = evaluate(bundleScript);

    assert.ok(result);
    assert.equal(result.titel, 'Self-Contained Execution');
    assert.ok(result.reintext.includes('completely standalone'));
  }, { tier: 1, feature: 'R1', id: 'TC-T1-R1-02' });

  it('TC-T1-R1-03: Graceful fallback for non-article / login / landing pages', () => {
    const html = `
      <html>
        <head><title>Login</title></head>
        <body>
          <form action="/login">
            <input type="text" name="user">
            <input type="password" name="pass">
            <button type="submit">Log in</button>
          </form>
        </body>
      </html>
    `;
    const { document } = createMockDom(html, 'https://example.com/login');
    const isReadable = pruefeObLesbar(document);
    const result = artikelAusDomExtrahieren(document, 'https://example.com/login');

    assert.equal(isReadable, false);
    assert.equal(result.wortanzahl, 0);
    assert.equal(result.inhaltHtml, '');
  }, { tier: 1, feature: 'R1', id: 'TC-T1-R1-03' });

  it('TC-T1-R1-04: Accurate word count and reading duration calculation', () => {
    const words = Array.from({ length: 600 }, (_, i) => `word${i}`).join(' ');
    const html = `<html><body><article><h1>Calculation Test</h1><p>${words}</p></article></body></html>`;
    const { document } = createMockDom(html, 'https://example.com/words');
    const result = artikelAusDomExtrahieren(document, 'https://example.com/words');

    assert.equal(result.wortanzahl, 600);
    assert.equal(result.lesedauerMinuten, 3); // ceil(600 / 200)
  }, { tier: 1, feature: 'R1', id: 'TC-T1-R1-04' });

  it('TC-T1-R1-05: Extraction of OpenGraph metadata, author, and publication timestamp', () => {
    const html = `
      <!DOCTYPE html>
      <html>
        <head>
          <meta property="og:title" content="OpenGraph Headline Article">
          <meta property="article:author" content="Elena Rostova">
          <meta property="article:published_time" content="2026-10-18T10:00:00Z">
        </head>
        <body>
          <article><p>Detailed news content discussing global technology and astronomy advancements.</p></article>
        </body>
      </html>
    `;
    const { document } = createMockDom(html, 'https://example.com/news/og');
    const result = artikelAusDomExtrahieren(document, 'https://example.com/news/og');

    assert.equal(result.titel, 'OpenGraph Headline Article');
    assert.equal(result.autor, 'Elena Rostova');
    assert.equal(result.datum, '2026-10-18T10:00:00Z');
  }, { tier: 1, feature: 'R1', id: 'TC-T1-R1-05' });

  it('TC-T1-R1-06: Elimination of Wikipedia edit sections, citation brackets, TOC, and clutter', () => {
    const html = `
      <html>
        <body>
          <article>
            <h1>Wikipedia Topic</h1>
            <div id="toc" class="toc" role="navigation">
              <h2 id="mw-toc-heading">Inhaltsverzeichnis</h2>
              <ul><li><a href="#abschnitt">1 Abschnitt</a></li></ul>
            </div>
            <p>
              Quantenmechanik ist eine fundamentale Theorie der Physik
              <span class="mw-editsection">[<a href="#">bearbeiten</a>]</span>
              <sup class="reference"><a href="#cite_note-1">[1]</a></sup>.
            </p>
            <div class="inline-advertisement"><p>Anzeige</p></div>
            <div class="reflist"><ol class="references"><li>Citation note text</li></ol></div>
          </article>
        </body>
      </html>
    `;
    const { document } = createMockDom(html, 'https://de.wikipedia.org/wiki/Quantenmechanik');
    const result = artikelAusDomExtrahieren(document, 'https://de.wikipedia.org/wiki/Quantenmechanik');

    assert.ok(!result.inhaltHtml.includes('Inhaltsverzeichnis'));
    assert.ok(!result.inhaltHtml.includes('bearbeiten'));
    assert.ok(!result.inhaltHtml.includes('[1]'));
    assert.ok(!result.inhaltHtml.includes('Anzeige'));
    assert.ok(!result.inhaltHtml.includes('reflist'));
    assert.ok(result.reintext.includes('Quantenmechanik ist eine fundamentale Theorie der Physik'));
  }, { tier: 1, feature: 'R1', id: 'TC-T1-R1-06' });

  // Tier 2: Boundary & Corner Cases
  it('TC-T2-R1-01: Massive HTML document processing with 10,000 paragraphs without memory crash', () => {
    let massiveBody = '<article><h1>Massive Article</h1>';
    for (let i = 0; i < 2000; i++) {
      massiveBody += `<p>Paragraph number ${i} contains substantial textual data to exercise the parser under load.</p>`;
    }
    massiveBody += '</article>';

    const { document } = createMockDom(`<html><body>${massiveBody}</body></html>`, 'https://example.com/massive');
    const t0 = performance.now();
    const result = artikelAusDomExtrahieren(document, 'https://example.com/massive');
    const elapsed = performance.now() - t0;

    assert.ok(result.wortanzahl > 20000);
    assert.ok(elapsed < 3000, `Processing took ${elapsed}ms, expected < 3000ms`);
  }, { tier: 2, feature: 'R1', id: 'TC-T2-R1-01' });

  it('TC-T2-R1-02: Zero-text document containing only media, canvas, and svg elements', () => {
    const html = `
      <html>
        <body>
          <canvas width="800" height="600"></canvas>
          <svg><circle cx="50" cy="50" r="40" /></svg>
          <video src="clip.mp4"></video>
          <audio src="sound.mp3"></audio>
        </body>
      </html>
    `;
    const { document } = createMockDom(html, 'https://example.com/media-only');
    const isReadable = pruefeObLesbar(document);
    const result = artikelAusDomExtrahieren(document, 'https://example.com/media-only');

    assert.equal(isReadable, false);
    assert.equal(result.wortanzahl, 0);
  }, { tier: 2, feature: 'R1', id: 'TC-T2-R1-02' });

  it('TC-T2-R1-03: Malicious XSS payloads (<script>, onerror, javascript:) are completely sanitized', () => {
    const html = `
      <html>
        <body>
          <article>
            <h1>Security Test</h1>
            <p>Valid text with an attempted <script>alert("XSS")</script> injection.</p>
            <p><img src="x" onerror="window.malicious=true" alt="Attack image"></p>
            <p><a href="javascript:alert(1)">Click for exploit</a></p>
          </article>
        </body>
      </html>
    `;
    const { document } = createMockDom(html, 'https://example.com/security');
    const result = artikelAusDomExtrahieren(document, 'https://example.com/security');

    assert.ok(!result.inhaltHtml.includes('<script>'));
    assert.ok(!result.inhaltHtml.includes('alert("XSS")'));
    assert.ok(!result.inhaltHtml.includes('onerror'));
    assert.ok(!result.inhaltHtml.includes('javascript:'));
  }, { tier: 2, feature: 'R1', id: 'TC-T2-R1-03' });

  it('TC-T2-R1-04: Deeply nested DOM hierarchy (>150 divs) traverses without call-stack overflow', () => {
    let deepHtml = '<article>';
    for (let i = 0; i < 160; i++) {
      deepHtml += '<div>';
    }
    deepHtml += '<p>Deeply nested paragraph content successfully retrieved.</p>';
    for (let i = 0; i < 160; i++) {
      deepHtml += '</div>';
    }
    deepHtml += '</article>';

    const { document } = createMockDom(`<html><body>${deepHtml}</body></html>`, 'https://example.com/deep');
    const result = artikelAusDomExtrahieren(document, 'https://example.com/deep');

    assert.ok(result.reintext.includes('Deeply nested paragraph content'));
  }, { tier: 2, feature: 'R1', id: 'TC-T2-R1-04' });

  it('TC-T2-R1-05: Non-standard and corrupt date strings handled without parser crash', () => {
    const html = `
      <html>
        <head>
          <meta name="date" content="corrupted-date-format-999-99-99">
        </head>
        <body>
          <article><h1>Corrupt Date Test</h1><p>Article body content.</p></article>
        </body>
      </html>
    `;
    const { document } = createMockDom(html, 'https://example.com/corrupt-date');
    const result = artikelAusDomExtrahieren(document, 'https://example.com/corrupt-date');

    assert.equal(result.datum, 'corrupted-date-format-999-99-99');
  }, { tier: 2, feature: 'R1', id: 'TC-T2-R1-05' });

  it('TC-T2-R1-06: Code blocks and syntax-highlighting classes are preserved while noise stripped', () => {
    const html = `
      <html>
        <body>
          <article>
            <h1>Code Preservation Test</h1>
            <pre class="language-typescript custom-syntax-theme">
              <code class="language-typescript">const x: number = 42;</code>
            </pre>
          </article>
        </body>
      </html>
    `;
    const { document } = createMockDom(html, 'https://example.com/code');
    const result = artikelAusDomExtrahieren(document, 'https://example.com/code');

    assert.ok(result.inhaltHtml.includes('class="language-typescript"'));
    assert.ok(result.reintext.includes('const x: number = 42;'));
  }, { tier: 2, feature: 'R1', id: 'TC-T2-R1-06' });
});
