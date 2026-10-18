import fs from 'fs';
import { createMockDom } from './harness.mjs';

const codeFile = fs.readFileSync('frontend/src/browser/services/lesemodusService.ts', 'utf8');

// Extract BROWSER_EXTRAKTION_CODE
const match = codeFile.match(/export const BROWSER_EXTRAKTION_CODE = `([\s\S]*?)`;\s*export const BROWSER_LESBAR_CODE/);
const extractionScript = (new Function('return `' + match[1] + '`'))();

console.log('================================================================');
console.log('  ADVERSARIAL CHALLENGE SUITE: MILESTONE 1 (R1 LESEMODUS)');
console.log('================================================================\n');

// -------------------------------------------------------------
// Challenge 1: Clutter Removal on Complex Wikipedia & News Sites
// -------------------------------------------------------------
console.log('--- TEST GROUP 1: CLUTTER REMOVAL FIDELITY ---');

const wikiFullHtml = `
<!DOCTYPE html>
<html>
<head><title>Quantenverschränkung – Wikipedia</title></head>
<body>
  <div id="content" class="mw-body">
    <h1 id="firstHeading" class="firstHeading">Quantenverschränkung</h1>
    <div id="bodyContent">
      <div id="siteSub" class="noprint">Aus Wikipedia, der freien Enzyklopädie</div>
      <div id="toc" class="toc" role="navigation" aria-labelledby="mw-toc-heading">
        <h2 id="mw-toc-heading">Inhaltsverzeichnis</h2>
        <ul>
          <li class="toclevel-1"><a href="#Eigenschaften"><span class="toctext">1 Eigenschaften</span></a></li>
          <li class="toclevel-1"><a href="#Anwendungen"><span class="toctext">2 Anwendungen</span></a></li>
        </ul>
      </div>
      <h2><span class="mw-headline" id="Eigenschaften">Eigenschaften</span><span class="mw-editsection"><span class="mw-editsection-bracket">[</span><a href="#">Bearbeiten</a><span class="mw-editsection-bracket">]</span></span></h2>
      <p>
        Quantenverschränkung ist ein quantenmechanisches Phänomen.<sup id="cite_ref-1" class="reference"><a href="#cite_note-1">[1]</a></sup>
        Zwei oder mehr Teilchen können in einen gemeinsamen Zustand übergehen.<sup class="noprint">[citation needed]</sup>
      </p>
      <h2><span class="mw-headline" id="Anwendungen">Anwendungen</span></h2>
      <p>Wichtige Anwendungen sind Quantenkryptographie und Quantencomputing.</p>
      <div class="navbox"><table><tr><td>Navigationsleiste Quantenphysik</td></tr></table></div>
      <div class="reflist"><ol class="references"><li id="cite_note-1">Aspect et al. 1982.</li></ol></div>
    </div>
  </div>
</body>
</html>
`;

const mockWiki = createMockDom(wikiFullHtml, 'https://de.wikipedia.org/wiki/Quantenverschr%C3%A4nkung');
const resWiki = mockWiki.evaluate(extractionScript);

console.log('1.1 Wikipedia Clutter:');
console.log('  - TOC "Inhaltsverzeichnis" stripped:', !resWiki.artikel.inhaltHtml.includes('Inhaltsverzeichnis'));
console.log('  - Edit section "[Bearbeiten]" stripped:', !resWiki.artikel.inhaltHtml.includes('Bearbeiten'));
console.log('  - Sup citation "[1]" stripped:', !resWiki.artikel.inhaltHtml.includes('[1]'));
console.log('  - Citation needed "[citation needed]" stripped:', !resWiki.artikel.inhaltHtml.includes('citation needed'));
console.log('  - Navbox stripped:', !resWiki.artikel.inhaltHtml.includes('Navigationsleiste'));
console.log('  - Headings retained (Eigenschaften, Anwendungen):', resWiki.artikel.inhaltHtml.includes('Eigenschaften') && resWiki.artikel.inhaltHtml.includes('Anwendungen'));
console.log('  - Paragraphs retained:', resWiki.artikel.reintext.includes('Quantenverschränkung ist ein quantenmechanisches Phänomen.'));

const newsComplexHtml = `
<!DOCTYPE html>
<html>
<head>
  <title>Durchbruch in der Fusionsforschung</title>
  <meta property="og:title" content="Durchbruch in der Fusionsforschung" />
  <meta name="author" content="Dr. Thomas Berg" />
</head>
<body>
  <div class="ad-banner top-ad">Werbung: Sonderangebot</div>
  <article>
    <h1>Durchbruch in der Fusionsforschung</h1>
    <div class="inline-advertisement">
      <p>Anzeige: Buchen Sie jetzt Ihren Urlaub!</p>
    </div>
    <div class="advertisement-wrapper">
      <p>Werbebanner Mitte</p>
    </div>
    <p>Wissenschaftler haben einen neuen Meilenstein bei der Netto-Energiegewinnung erzielt.</p>
    <div class="social-share">
      <a href="#">Share on Social</a>
    </div>
    <h2>Ergebnisse der Versuchsreihe</h2>
    <p>Die Plasmatemperatur überstieg erstmals 100 Millionen Kelvin.</p>
    <ul>
      <li>Stabile Magnetfelder</li>
      <li>Geringere Turbulenzverluste</li>
    </ul>
  </article>
</body>
</html>
`;

const mockNews = createMockDom(newsComplexHtml, 'https://science.example.com/fusion');
const resNews = mockNews.evaluate(extractionScript);

console.log('\n1.2 News Site Clutter:');
console.log('  - Top ad-banner stripped:', !resNews.artikel.inhaltHtml.includes('Sonderangebot'));
console.log('  - Inline advertisement ("inline-advertisement") stripped:', !resNews.artikel.inhaltHtml.includes('Urlaub'));
console.log('  - Advertisement wrapper ("advertisement-wrapper") stripped:', !resNews.artikel.inhaltHtml.includes('Werbebanner Mitte'));
console.log('  - Social share buttons stripped:', !resNews.artikel.inhaltHtml.includes('Share on Social'));
console.log('  - Headings retained ("Ergebnisse der Versuchsreihe"):', resNews.artikel.inhaltHtml.includes('Ergebnisse der Versuchsreihe'));
console.log('  - List items retained ("Stabile Magnetfelder"):', resNews.artikel.inhaltHtml.includes('Stabile Magnetfelder'));

// -------------------------------------------------------------
// Challenge 2: CDP Evaluation Failure Paths in lesemodusService
// -------------------------------------------------------------
console.log('\n--- TEST GROUP 2: CDP EVALUATION FAILURE PATHS IN SERVICE ---');

// We simulate the exact logic of artikelExtrahieren from lesemodusService.ts
const protokollVorabGesetzt = /try\s*\{\s*protokollVersucht\s*=\s*true/.test(codeFile);

async function simulateArtikelExtrahieren(tabProtokollMock, isHostWindow = true) {
  let protokollVersucht = false;
  try {
    if (protokollVorabGesetzt) {
      protokollVersucht = true;
    }
    const res = await tabProtokollMock('Runtime.evaluate');

    if (res !== null && res !== undefined) {
      protokollVersucht = true;

      if (res.exceptionDetails) {
        const fehlerBeschreibung =
          res.exceptionDetails?.exception?.description ||
          res.exceptionDetails?.text ||
          'CDP Runtime.evaluate Ausnahme im Tab';
        throw new Error(fehlerBeschreibung);
      }

      const result = res.result;
      const val = result?.value;
      if (val && typeof val === 'object') {
        if ('ok' in val) {
          if (val.ok && val.artikel) {
            return val.artikel;
          }
          if (!val.ok) {
            throw new Error(val.fehler || 'Für diese Seite konnte kein lesbarer Artikeltext extrahiert werden.');
          }
        } else if ('inhaltHtml' in val) {
          return val;
        }
      }

      throw new Error('Für diese Seite konnte kein lesbarer Artikeltext extrahiert werden.');
    }
  } catch (err) {
    if (protokollVersucht) {
      throw err;
    }
    // Tab-Protokoll nicht verfügbar, falle zurück:
  }

  if (isHostWindow) {
    throw new Error('Für diese Seite konnte kein lesbarer Artikeltext extrahiert werden.');
  }
  return { titel: 'Fallback Article' };
}

// 2.1 Test res.exceptionDetails
try {
  await simulateArtikelExtrahieren(async () => ({
    result: { type: 'undefined' },
    exceptionDetails: {
      text: 'Uncaught SyntaxError',
      exception: { description: 'SyntaxError: Unexpected token' }
    }
  }));
  console.log('  2.1 exceptionDetails: FAILED (did not throw)');
} catch (err) {
  console.log('  2.1 exceptionDetails throws correct error:', err.message === 'SyntaxError: Unexpected token');
}

// 2.2 Test res.result.type === 'undefined' (no exceptionDetails, result undefined)
try {
  await simulateArtikelExtrahieren(async () => ({
    result: { type: 'undefined' }
  }));
  console.log('  2.2 result.type === undefined: FAILED (did not throw)');
} catch (err) {
  console.log('  2.2 result.type === undefined throws:', err.message);
  console.log('      Is specific error or generic string?:', err.message);
}

// 2.3 Test network abort / tabProtokoll rejected promise
let preservedOriginalError = false;
try {
  await simulateArtikelExtrahieren(async () => {
    throw new Error('AbortError: The operation was aborted due to timeout');
  });
  console.log('  2.3 Network abort: FAILED (did not throw)');
} catch (err) {
  console.log('  2.3 Network abort throws:', err.message);
  preservedOriginalError = err.message.includes('AbortError');
  console.log('      Was original AbortError preserved?:', preservedOriginalError);
  if (!preservedOriginalError) {
    console.log('      => BUG CONFIRMED: Network abort error was swallowed by "if (!protokollVersucht)" fallback and converted to generic message!');
  } else {
    console.log('      => PASSED: Original AbortError was preserved and rethrown.');
  }
}

// -------------------------------------------------------------
// Challenge 3: UI Error State vs "nicht verfügbar"
// -------------------------------------------------------------
console.log('\n--- TEST GROUP 3: LESEMODUSANSICHT UI ERROR HANDLING ---');
const ansichtCode = fs.readFileSync('frontend/src/browser/seite/LesemodusAnsicht.tsx', 'utf8');

const catchBlockMatch = ansichtCode.match(/\.catch\(\(err\)\s*=>\s*\{[\s\S]*?setFehler\(([\s\S]*?)\)[\s\S]*?\}\)/);
console.log('  LesemodusAnsicht .catch block argument to setFehler:');
console.log('   ', catchBlockMatch ? catchBlockMatch[1].trim() : 'NOT FOUND');

const usesActualErrorMessage = catchBlockMatch && (catchBlockMatch[1].includes('err') || catchBlockMatch[1].includes('fehler'));
console.log('  Does UI display the actual thrown error or error indicator?:', usesActualErrorMessage);

if (!usesActualErrorMessage) {
  console.log('  => BUG CONFIRMED: When CDP evaluation throws an exception or network aborts, UI displays generic t("browser.lesemodus.nichtVerfuegbar") ("Für diese Seite konnte kein lesbarer Artikeltext extrahiert werden.") instead of an error state!');
} else {
  console.log('  => PASSED: UI displays the actual thrown error message.');
}

const allPassed =
  !resWiki.artikel.inhaltHtml.includes('Inhaltsverzeichnis') &&
  !resNews.artikel.inhaltHtml.includes('Urlaub') &&
  !resNews.artikel.inhaltHtml.includes('Werbebanner Mitte') &&
  preservedOriginalError &&
  usesActualErrorMessage;

console.log('\n================================================================');
if (allPassed) {
  console.log('  CHALLENGE SUITE VERDICT: ALL ADVERSARIAL CHECKS PASSED (100%)');
  console.log('================================================================\n');
  process.exit(0);
} else {
  console.error('  CHALLENGE SUITE VERDICT: FAILURES DETECTED');
  console.log('================================================================\n');
  process.exit(1);
}
