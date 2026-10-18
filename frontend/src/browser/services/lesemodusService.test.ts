import { describe, expect, it, vi } from 'vitest'
import {
  artikelAusDomExtrahieren,
  artikelExtrahieren,
  istSeiteLesbarPruefen,
  pruefeObLesbar,
} from './lesemodusService'

describe('lesemodusService', () => {
  describe('pruefeObLesbar (Heuristik)', () => {
    it('erkennt eine Seite mit <article>-Tag sofort als lesbar', () => {
      const doc = new DOMParser().parseFromString(
        '<!DOCTYPE html><html><body><article>Kurzer Text</article></body></html>',
        'text/html',
      )
      expect(pruefeObLesbar(doc)).toBe(true)
    })

    it('erkennt eine Seite mit ausreichend Fließtext (> 350 Zeichen in Absätzen) als lesbar', () => {
      const text1 = 'Dies ist ein langer informativer Absatz mit reichlich nützlichem Text für den Testfall. '.repeat(3)
      const text2 = 'Ein zweiter Absatz vertieft das Thema und liefert zusätzliche wertvolle Ausführungen. '.repeat(3)
      const doc = new DOMParser().parseFromString(
        `<!DOCTYPE html><html><body><div><p>${text1}</p><p>${text2}</p></div></body></html>`,
        'text/html',
      )
      expect(pruefeObLesbar(doc)).toBe(true)
    })

    it('erkennt kurze Seiten (< 350 Zeichen) ohne <article> nicht als lesbar', () => {
      const doc = new DOMParser().parseFromString(
        '<!DOCTYPE html><html><body><p>Nur ein sehr kurzer Text.</p></body></html>',
        'text/html',
      )
      expect(pruefeObLesbar(doc)).toBe(false)
    })
  })

  describe('artikelAusDomExtrahieren', () => {
    it('extrahiert Titel, Autor, Datum und bereinigt störende Elemente', () => {
      const html = `
        <!DOCTYPE html>
        <html>
          <head>
            <title>Dokumententitel</title>
            <meta property="og:title" content="OpenGraph Artikel-Titel" />
            <meta name="author" content="Max Mustermann" />
            <meta property="article:published_time" content="2026-10-17T12:00:00Z" />
          </head>
          <body>
            <header><nav>Navigation</nav></header>
            <article>
              <div class="ad-banner">Werbung hier</div>
              <p>Dies ist der erste Absatz mit einem <a href="/interner-link">Link</a> und <strong>wichtigem</strong> Inhalt.</p>
              <div class="social-share">Teilen auf Social Media</div>
              <p>Zweiter Absatz mit einem Code-Beispiel: <code class="language-typescript">const x = 42;</code>.</p>
              <script>console.log("böse")</script>
            </article>
            <footer>Footer Links</footer>
          </body>
        </html>
      `
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const artikel = artikelAusDomExtrahieren(doc, 'https://example.com/artikel')

      expect(artikel.titel).toBe('OpenGraph Artikel-Titel')
      expect(artikel.autor).toBe('Max Mustermann')
      expect(artikel.datum).toBe('2026-10-17T12:00:00Z')
      expect(artikel.url).toBe('https://example.com/artikel')

      // Skripte, Nav, Werbung und Social müssen entfernt sein
      expect(artikel.inhaltHtml).not.toContain('<script')
      expect(artikel.inhaltHtml).not.toContain('Werbung hier')
      expect(artikel.inhaltHtml).not.toContain('Teilen auf Social Media')
      expect(artikel.inhaltHtml).not.toContain('<nav')
      expect(artikel.inhaltHtml).not.toContain('<footer')

      // Relevanter Inhalt muss erhalten bleiben
      expect(artikel.inhaltHtml).toContain('Dies ist der erste Absatz')
      expect(artikel.inhaltHtml).toContain('href="https://example.com/interner-link"')
      expect(artikel.inhaltHtml).toContain('class="language-typescript"')

      // Reintext
      expect(artikel.reintext).toContain('Dies ist der erste Absatz')
      expect(artikel.reintext).toContain('Zweiter Absatz mit einem Code-Beispiel')
      expect(artikel.wortanzahl).toBeGreaterThan(10)
      expect(artikel.lesedauerMinuten).toBe(1)
    })

    it('fällt bei fehlenden Metadaten auf H1, rel=author und time zurück', () => {
      const html = `
        <!DOCTYPE html>
        <html>
          <body>
            <article>
              <h1>Artikel H1 Überschrift</h1>
              <span rel="author">Erika Musterfrau</span>
              <time datetime="2026-05-01">1. Mai 2026</time>
              <p>Ein informativer Absatz ohne OpenGraph-Tags im Kopfbereich.</p>
            </article>
          </body>
        </html>
      `
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const artikel = artikelAusDomExtrahieren(doc, 'https://example.com/artikel2')

      expect(artikel.titel).toBe('Artikel H1 Überschrift')
      expect(artikel.autor).toBe('Erika Musterfrau')
      expect(artikel.datum).toBe('2026-05-01')
      expect(artikel.reintext).toContain('Ein informativer Absatz ohne OpenGraph-Tags')
    })

    it('verdoppelt nicht den Text von verschachtelten Elementen wie blockquote > p oder li > p', () => {
      const html = `
        <!DOCTYPE html>
        <html>
          <body>
            <article>
              <h1>Verschachtelter Test</h1>
              <blockquote>
                <p>Ein wichtiges Zitat.</p>
              </blockquote>
              <ul>
                <li><p>Erster Punkt</p></li>
                <li><p>Zweiter Punkt</p></li>
              </ul>
            </article>
          </body>
        </html>
      `
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const artikel = artikelAusDomExtrahieren(doc, 'https://example.com/nesting')

      const zitatTreffer = artikel.reintext.match(/Ein wichtiges Zitat\./g)
      expect(zitatTreffer).toHaveLength(1)

      const punkt1Treffer = artikel.reintext.match(/Erster Punkt/g)
      expect(punkt1Treffer).toHaveLength(1)
    })

    it('entfernt erstes h1 auch bei partiellem Titel-Match mit Website-Suffix', () => {
      const html = `
        <!DOCTYPE html>
        <html>
          <head>
            <meta property="og:title" content="Spannende Neuigkeiten – Nachrichtenportal" />
          </head>
          <body>
            <article>
              <h1>Spannende Neuigkeiten</h1>
              <p>Der Artikel-Text beginnt hier.</p>
            </article>
          </body>
        </html>
      `
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const artikel = artikelAusDomExtrahieren(doc, 'https://example.com/suffix')

      expect(artikel.titel).toBe('Spannende Neuigkeiten – Nachrichtenportal')
      expect(artikel.inhaltHtml).not.toContain('<h1>Spannende Neuigkeiten</h1>')
      expect(artikel.inhaltHtml).toContain('Der Artikel-Text beginnt hier.')
    })

    it('entfernt keine berechtigten Klassen mit Wortbestandteil share (z. B. shareholder-report)', () => {
      const html = `
        <!DOCTYPE html>
        <html>
          <body>
            <article>
              <h1>Finanzbericht</h1>
              <div class="shareholder-report">
                <p>Der Bericht für alle Aktionäre bleibt erhalten.</p>
              </div>
            </article>
          </body>
        </html>
      `
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const artikel = artikelAusDomExtrahieren(doc, 'https://example.com/shareholder')

      expect(artikel.inhaltHtml).toContain('Der Bericht für alle Aktionäre bleibt erhalten.')
      expect(artikel.reintext).toContain('Der Bericht für alle Aktionäre bleibt erhalten.')
    })

    it('entfernt Wikipedia-Clutter wie Fußnoten, Bearbeiten-Links, [1], Printfooter und Referenzlisten', () => {
      const html = `
        <!DOCTYPE html>
        <html>
          <body>
            <article>
              <h1 id="haupt">Wikipedia Artikel</h1>
              <a class="mw-jump-link" href="#bodyContent">Springe zum Inhalt</a>
              <h2>
                <span class="mw-headline" id="Geschichte">Geschichte</span>
                <span class="mw-editsection"><span class="mw-editsection-bracket">[</span><a href="/edit">Quelltext bearbeiten</a><span class="mw-editsection-bracket">]</span></span>
              </h2>
              <p>
                Dies ist ein historischer Bericht über eine Entdeckung.<sup id="cite_ref-1" class="reference"><a href="#cite_note-1">[1]</a></sup>
                Ein weiterer Fakt folgt hier.<sup class="noprint">[citation needed]</sup>
                Und ein Zitat mit Mehrfachnummer.<a href="#note-2">[1, 2]</a>
              </p>
              <p>
                Mehr dazu unter dem <a href="/wiki/Albert_Einstein">Link zu Einstein</a>
                oder im Abschnitt <a href="#Geschichte">zur Geschichte</a>.
              </p>
              <div class="printfooter" data-nosnippet="">
                Abgerufen von „<a href="https://de.wikipedia.org/w/index.php?title=Test">https://de.wikipedia.org/w/index.php?title=Test</a>“
              </div>
              <div class="catlinks" id="catlinks">
                Kategorien: <a href="/wiki/Kategorie:Physik">Physik</a>
              </div>
              <table class="navbox"><tr><td>Navigationsleiste</td></tr></table>
              <div class="reflist">
                <ol class="references">
                  <li id="cite_note-1">Archiviert vom Original. Abgerufen am 17. Oktober 2026.</li>
                </ol>
              </div>
            </article>
          </body>
        </html>
      `
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const artikel = artikelAusDomExtrahieren(doc, 'https://de.wikipedia.org/wiki/Test')

      // Clutter darf weder im HTML noch im Reintext auftauchen
      expect(artikel.inhaltHtml).not.toContain('Springe zum Inhalt')
      expect(artikel.inhaltHtml).not.toContain('Quelltext bearbeiten')
      expect(artikel.inhaltHtml).not.toContain('cite_ref-1')
      expect(artikel.inhaltHtml).not.toContain('[1]')
      expect(artikel.inhaltHtml).not.toContain('[1, 2]')
      expect(artikel.inhaltHtml).not.toContain('citation needed')
      expect(artikel.inhaltHtml).not.toContain('Abgerufen am')
      expect(artikel.inhaltHtml).not.toContain('Abgerufen von')
      expect(artikel.inhaltHtml).not.toContain('Kategorien')
      expect(artikel.inhaltHtml).not.toContain('Navigationsleiste')
      expect(artikel.inhaltHtml).not.toContain('reflist')

      expect(artikel.reintext).not.toContain('Springe zum Inhalt')
      expect(artikel.reintext).not.toContain('Quelltext bearbeiten')
      expect(artikel.reintext).not.toContain('[1]')
      expect(artikel.reintext).not.toContain('[1, 2]')
      expect(artikel.reintext).not.toContain('citation needed')
      expect(artikel.reintext).not.toContain('Abgerufen am')
      expect(artikel.reintext).not.toContain('Abgerufen von')
      expect(artikel.reintext).not.toContain('Kategorien')
      expect(artikel.reintext).not.toContain('Navigationsleiste')

      // Echter Inhalt muss bleiben
      expect(artikel.inhaltHtml).toContain('Dies ist ein historischer Bericht über eine Entdeckung.')
      expect(artikel.reintext).toContain('Dies ist ein historischer Bericht über eine Entdeckung.')

      // Relative Links müssen zu absoluten URLs aufgelöst sein
      expect(artikel.inhaltHtml).toContain('href="https://de.wikipedia.org/wiki/Albert_Einstein"')
      expect(artikel.inhaltHtml).toContain('target="_blank"')

      // Fragment-Links (#...) und Sprungmarken-IDs müssen erhalten bleiben
      expect(artikel.inhaltHtml).toContain('href="#Geschichte"')
      expect(artikel.inhaltHtml).toContain('id="Geschichte"')
    })

    it('entfernt Wikipedia Inhaltsverzeichnis (TOC) vollständig aus dem extrahierten Artikel', () => {
      const html = `
        <!DOCTYPE html>
        <html>
          <body>
            <article>
              <h1>Quantenphysik</h1>
              <div id="toc" class="toc" role="navigation" aria-labelledby="mw-toc-heading">
                <h2 id="mw-toc-heading">Inhaltsverzeichnis</h2>
                <div id="toctitle" class="toctitle">TOC-Titel</div>
                <ul>
                  <li><a href="#abschnitt1">1 Einführung</a></li>
                  <li><a href="#abschnitt2">2 Experimente</a></li>
                </ul>
              </div>
              <nav id="mw-panel-toc" class="vector-toc mw-table-of-contents">
                <p>Vector TOC Item</p>
              </nav>
              <h2 id="abschnitt1">Einführung</h2>
              <p>Die Quantenmechanik beschreibt atomare Phänomene.</p>
              <h2 id="abschnitt2">Experimente</h2>
              <p>Zahlreiche Experimente belegen die Vorhersagen.</p>
            </article>
          </body>
        </html>
      `
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const artikel = artikelAusDomExtrahieren(doc, 'https://de.wikipedia.org/wiki/Quantenphysik')

      expect(artikel.inhaltHtml).not.toContain('Inhaltsverzeichnis')
      expect(artikel.inhaltHtml).not.toContain('TOC-Titel')
      expect(artikel.inhaltHtml).not.toContain('Vector TOC Item')
      expect(artikel.inhaltHtml).not.toContain('1 Einführung')
      expect(artikel.reintext).not.toContain('Inhaltsverzeichnis')
      expect(artikel.reintext).not.toContain('TOC-Titel')
      expect(artikel.reintext).not.toContain('Vector TOC Item')
      expect(artikel.reintext).toContain('Die Quantenmechanik beschreibt atomare Phänomene.')
      expect(artikel.reintext).toContain('Zahlreiche Experimente belegen die Vorhersagen.')
    })

    it('entfernt moderne News-Werbung wie inline-advertisement, advertisement-wrapper und sponsored-post', () => {
      const html = `
        <!DOCTYPE html>
        <html>
          <body>
            <article>
              <h1>Wichtige Entdeckung</h1>
              <div class="inline-advertisement">
                <p>Anzeige: Buchen Sie jetzt Ihren Urlaub!</p>
              </div>
              <p>Forscher haben neue Erkenntnisse publiziert.</p>
              <div class="advertisement-wrapper">
                <p>Werbebanner Mitte</p>
              </div>
              <div class="sponsored-post">
                <p>Gesponserter Beitrag: Tolle Angebote</p>
              </div>
              <p>Die Studie erschien im Fachjournal.</p>
            </article>
          </body>
        </html>
      `
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const artikel = artikelAusDomExtrahieren(doc, 'https://news.example.com/entdeckung')

      expect(artikel.inhaltHtml).not.toContain('Urlaub')
      expect(artikel.inhaltHtml).not.toContain('Werbebanner Mitte')
      expect(artikel.inhaltHtml).not.toContain('Gesponserter Beitrag')
      expect(artikel.reintext).toContain('Forscher haben neue Erkenntnisse publiziert.')
      expect(artikel.reintext).toContain('Die Studie erschien im Fachjournal.')
    })
  })

  describe('BROWSER_EXTRAKTION_CODE (WebView2 Runtime-Evaluate)', () => {
    it('ist vollständig autonom, wirft in isoliertem Kontext keinen ReferenceError und liefert strukturierten Kontrakt', async () => {
      const { BROWSER_EXTRAKTION_CODE } = await import('./lesemodusService')
      // Prüfen, dass der Code alle Konstanten und Hilfsfunktionen autonom deklariert
      expect(BROWSER_EXTRAKTION_CODE).toContain('ERLAUBTE_TAGS')
      expect(BROWSER_EXTRAKTION_CODE).toContain('ZU_ENTFERNENDE_TAGS')
      expect(BROWSER_EXTRAKTION_CODE).toContain('STOERUNG_MUSTER')
      expect(BROWSER_EXTRAKTION_CODE).toContain('CLUTTER_SELEKTOREN')
      expect(BROWSER_EXTRAKTION_CODE).toContain('artikelAusDomExtrahieren')
      expect(BROWSER_EXTRAKTION_CODE).toContain('baumBereinigen')

      // In einem isolierten iframe/Dokument ausführen
      const iframe = document.createElement('iframe')
      document.body.appendChild(iframe)
      const doc = iframe.contentDocument!
      doc.open()
      doc.write(`
        <!DOCTYPE html>
        <html>
          <head>
            <title>Iframe Artikel</title>
            <meta name="author" content="Test Autor" />
            <meta property="article:published_time" content="2026-10-18T10:00:00Z" />
          </head>
          <body>
            <article>
              <h1>Iframe Überschrift</h1>
              <p>Dies ist ein echter Absatz für den isolierten WebView2-Eval-Test mit ausreichend Textinhalt.</p>
            </article>
          </body>
        </html>
      `)
      doc.close()

      // Ausführung wie in WebView2 via Runtime.evaluate
      const evaluateFn = new iframe.contentWindow!.Function('return ' + BROWSER_EXTRAKTION_CODE)
      const result = evaluateFn()

      expect(result).toBeDefined()
      expect(result.ok).toBe(true)
      expect(result.artikel).toBeDefined()
      expect(result.artikel.titel).toBe('Iframe Überschrift')
      expect(result.artikel.autor).toBe('Test Autor')
      expect(result.artikel.datum).toBe('2026-10-18T10:00:00Z')
      expect(result.artikel.publiziertAm).toBe('2026-10-18T10:00:00Z')
      expect(result.artikel.reintext).toContain('Dies ist ein echter Absatz für den isolierten WebView2-Eval-Test')
      expect(result.artikel.wortanzahl).toBeGreaterThan(5)
      expect(result.artikel.lesedauerMinuten).toBe(1)
      expect(result.artikel.url).toBeDefined()
      iframe.remove()
    })

    it('erzeugt identische Extraktionsergebnisse wie artikelAusDomExtrahieren auf komplexen Seiten', async () => {
      const { BROWSER_EXTRAKTION_CODE, artikelAusDomExtrahieren } = await import('./lesemodusService')
      const html = `
        <!DOCTYPE html>
        <html>
          <head>
            <base href="https://example.com/paritaet" />
            <title>Paritäts-Test</title>
            <meta property="og:title" content="Wissenschaftlicher Artikel" />
          </head>
          <body>
            <article>
              <h1>Wissenschaftlicher Artikel</h1>
              <div class="ad-banner">Anzeige</div>
              <p>Erster relevanter Absatz über Forschungsmethoden.<sup class="reference"><a href="#note-1">[1]</a></sup></p>
              <div class="mw-editsection"><a href="/edit">Quelltext bearbeiten</a></div>
              <p>Zweiter relevanter Absatz mit <a href="/intern">internem Link</a>.</p>
              <div class="reflist"><ol><li id="note-1">Referenz 1</li></ol></div>
            </article>
          </body>
        </html>
      `
      const parser = new DOMParser()
      const doc = parser.parseFromString(html, 'text/html')
      const direktExtrahiert = artikelAusDomExtrahieren(doc, 'https://example.com/paritaet')

      const iframe = document.createElement('iframe')
      document.body.appendChild(iframe)
      const iDoc = iframe.contentDocument!
      iDoc.open()
      iDoc.write(html)
      iDoc.close()

      const evaluateFn = new iframe.contentWindow!.Function('return ' + BROWSER_EXTRAKTION_CODE)
      const evalResult = evaluateFn()
      iframe.remove()

      expect(evalResult.ok).toBe(true)
      expect(evalResult.artikel.titel).toBe(direktExtrahiert.titel)
      expect(evalResult.artikel.reintext).toBe(direktExtrahiert.reintext)
      expect(evalResult.artikel.inhaltHtml).toBe(direktExtrahiert.inhaltHtml)
      expect(evalResult.artikel.wortanzahl).toBe(direktExtrahiert.wortanzahl)
    })
  })

  describe('BROWSER_LESBAR_CODE (Schnellprüfung)', () => {
    it('evaluiert autonom und erkennt lesbare Seiten im isolierten Kontext', async () => {
      const { BROWSER_LESBAR_CODE } = await import('./lesemodusService')
      const iframe = document.createElement('iframe')
      document.body.appendChild(iframe)
      const doc = iframe.contentDocument!
      doc.open()
      doc.write('<!DOCTYPE html><html><body><article>Kurzer Artikel</article></body></html>')
      doc.close()

      const evaluateFn = new iframe.contentWindow!.Function('return ' + BROWSER_LESBAR_CODE)
      const result = evaluateFn()
      expect(result).toBe(true)
      iframe.remove()
    })
  })

  describe('istSeiteLesbarPruefen & artikelExtrahieren mit nativ.tabProtokoll', () => {
    it('ruft Runtime.evaluate für die Schnellprüfung auf', async () => {
      const { nativ } = await import('./nativ')
      const spy = vi.spyOn(nativ, 'tabProtokoll').mockResolvedValueOnce({
        result: { value: true },
      })

      const lesbar = await istSeiteLesbarPruefen('tab-123')
      expect(lesbar).toBe(true)
      expect(spy).toHaveBeenCalledWith(
        'tab-123',
        'Runtime.evaluate',
        expect.objectContaining({ returnByValue: true }),
      )
      spy.mockRestore()
    })

    it('gibt false zurück, wenn die Schnellprüfung fehlschlägt oder ExceptionDetails liefert', async () => {
      const { nativ } = await import('./nativ')
      const spyError = vi.spyOn(nativ, 'tabProtokoll').mockRejectedValueOnce(new Error('Fehler'))
      expect(await istSeiteLesbarPruefen('tab-error')).toBe(false)
      spyError.mockRestore()

      const spyException = vi.spyOn(nativ, 'tabProtokoll').mockResolvedValueOnce({
        exceptionDetails: { text: 'Uncaught ReferenceError', lineNumber: 1, columnNumber: 1 },
      })
      expect(await istSeiteLesbarPruefen('tab-exc')).toBe(false)
      spyException.mockRestore()
    })

    it('extrahiert Artikel über tabProtokoll mit strukturiertem { ok: true, artikel } Rückgabewert', async () => {
      const mockArtikel = {
        titel: 'Remote Titel',
        autor: 'Remote Autor',
        datum: '2026-10-17',
        publiziertAm: '2026-10-17',
        inhaltHtml: '<p>Remote Text</p>',
        reintext: 'Remote Text',
        wortanzahl: 2,
        lesedauerMinuten: 1,
        url: 'https://example.com/remote',
      }

      const { nativ } = await import('./nativ')
      const spy = vi.spyOn(nativ, 'tabProtokoll').mockResolvedValueOnce({
        result: { value: { ok: true, artikel: mockArtikel } },
      })

      const ergebnis = await artikelExtrahieren('tab-remote', 'https://example.com/remote')
      expect(ergebnis).toEqual(mockArtikel)
      spy.mockRestore()
    })

    it('unterstützt direkte Rückgabe von ExtrahierterArtikel zur Abwärtskompatibilität', async () => {
      const mockArtikel = {
        titel: 'Legacy Titel',
        autor: null,
        datum: null,
        inhaltHtml: '<p>Legacy Text</p>',
        reintext: 'Legacy Text',
        wortanzahl: 2,
        lesedauerMinuten: 1,
        url: 'https://example.com/legacy',
      }

      const { nativ } = await import('./nativ')
      const spy = vi.spyOn(nativ, 'tabProtokoll').mockResolvedValueOnce({
        result: { value: mockArtikel },
      })

      const ergebnis = await artikelExtrahieren('tab-legacy', 'https://example.com/legacy')
      expect(ergebnis).toEqual(mockArtikel)
      spy.mockRestore()
    })

    it('behandelt res.exceptionDetails als Fehler und fällt NICHT stillschweigend auf document zurück', async () => {
      const { nativ } = await import('./nativ')
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      const spy = vi.spyOn(nativ, 'tabProtokoll').mockResolvedValueOnce({
        result: { type: 'undefined' },
        exceptionDetails: {
          text: 'Uncaught ReferenceError',
          exception: { description: 'ReferenceError: Me is not defined' },
        },
      })

      await expect(artikelExtrahieren('tab-exc-test', 'https://example.com/exc')).rejects.toThrow(
        'ReferenceError: Me is not defined',
      )

      expect(consoleErrorSpy).toHaveBeenCalledWith(
        expect.stringContaining('[Lesemodus] CDP Runtime.evaluate Ausnahme im Tab:'),
        expect.anything(),
      )

      spy.mockRestore()
      consoleErrorSpy.mockRestore()
    })

    it('behandelt { ok: false, fehler } als Fehler und fällt NICHT auf document zurück', async () => {
      const { nativ } = await import('./nativ')
      const spy = vi.spyOn(nativ, 'tabProtokoll').mockResolvedValueOnce({
        result: {
          value: { ok: false, fehler: 'Artikelbereich konnte nicht ermittelt werden' },
        },
      })

      await expect(artikelExtrahieren('tab-fail-test', 'https://example.com/fail')).rejects.toThrow(
        'Artikelbereich konnte nicht ermittelt werden',
      )

      spy.mockRestore()
    })

    it('schützt das Tauri Host-Fenster davor, als Lesemodus-Inhalt extrahiert zu werden', async () => {
      const { nativ } = await import('./nativ')
      // Außerhalb von Tauri (tabProtokoll liefert null)
      const spy = vi.spyOn(nativ, 'tabProtokoll').mockResolvedValueOnce(null as any)

      // Host-Fenster simulieren
      const fakeRoot = document.createElement('div')
      fakeRoot.id = 'root'
      document.body.appendChild(fakeRoot)

      await expect(artikelExtrahieren('tab-host', 'https://example.com/host')).rejects.toThrow(
        'Für diese Seite konnte kein lesbarer Artikeltext extrahiert werden.',
      )

      fakeRoot.remove()
      spy.mockRestore()
    })

    it('bündelt parallele Aufrufe für denselben Tab in eine einzige Abfrage', async () => {
      const mockArtikel = {
        titel: 'Gleichzeitiger Artikel',
        autor: null,
        datum: null,
        inhaltHtml: '<p>Text</p>',
        reintext: 'Text',
        wortanzahl: 1,
        lesedauerMinuten: 1,
        url: 'https://example.com/parallel',
      }

      const { nativ } = await import('./nativ')
      let resolvePromise: (val: any) => void
      const deferred = new Promise((resolve) => {
        resolvePromise = resolve
      })
      const spy = vi.spyOn(nativ, 'tabProtokoll').mockReturnValueOnce(deferred as any)

      const p1 = artikelExtrahieren('tab-parallel', 'https://example.com/parallel')
      const p2 = artikelExtrahieren('tab-parallel', 'https://example.com/parallel')

      resolvePromise!({ result: { value: { ok: true, artikel: mockArtikel } } })
      const [res1, res2] = await Promise.all([p1, p2])

      expect(res1).toEqual(mockArtikel)
      expect(res2).toEqual(mockArtikel)
      expect(spy).toHaveBeenCalledTimes(1)
      spy.mockRestore()
    })

    it('behält ursprüngliche Netzwerk- oder Timeout-Fehler bei tabProtokoll-Rejection bei (z. B. AbortError)', async () => {
      const { nativ } = await import('./nativ')
      const spy = vi
        .spyOn(nativ, 'tabProtokoll')
        .mockRejectedValueOnce(new Error('AbortError: The operation was aborted due to timeout'))

      await expect(
        artikelExtrahieren('tab-timeout', 'https://example.com/timeout'),
      ).rejects.toThrow('AbortError: The operation was aborted due to timeout')

      spy.mockRestore()
    })
  })
})
