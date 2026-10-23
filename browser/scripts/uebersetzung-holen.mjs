// Holt die Übersetzungs-Engine von Firefox (Bergamot, MPL-2.0) in einer festen
// Fassung und schreibt die Liste der Sprachmodelle mit ihren Prüfsummen
// (`frontend/src/browser/uebersetzung/`).
//
// Die Engine-Datei bleibt unverändert, damit sie sich gegen Firefox
// vergleichen lässt. WASM und Modelle liegen nicht im Repo: der Browser lädt
// sie beim ersten Übersetzen von Mozilla und nimmt nur, was hier mit Größe und
// SHA-256 steht. Die Liste, die Remote Settings zur Laufzeit liefern würde,
// zählt nicht.
//
// Neue Fassung: COMMIT und GLUE_SHA256 anheben (letzter Commit an der Datei in
// mozilla-firefox/firefox), `node browser/scripts/uebersetzung-holen.mjs`, den
// Diff von `modelle.json` ansehen, dann die Tests in `uebersetzung/`.
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const COMMIT = '48d55cf7ec80093903e2ef7f58b61a84a22ef716'
const GLUE_SHA256 = 'faff1ef6285b0d26f01787776fd49299dfb756ecb9688aa990c250e66797b47d'
const LIZENZ_SHA256 = '1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5'
// Die Engine-Datei passt nur zu WASM derselben Fassung.
const WASM_RELEASE = 'v0.6.0'
// In diese Sprachen übersetzt der Browser: die Sprachen der App.
const ZIELE = ['de', 'en']

const QUELLE = `https://raw.githubusercontent.com/mozilla-firefox/firefox/${COMMIT}/toolkit/components/translations/bergamot-translator`
const SAMMLUNG = 'https://firefox.settings.services.mozilla.com/v1/buckets/main/collections'
const ziel = resolve(dirname(fileURLToPath(import.meta.url)), '../../frontend/src/browser/uebersetzung')

const sha256 = (daten) => createHash('sha256').update(daten).digest('hex')

async function holen(url, erwartet) {
  const antwort = await fetch(url)
  if (!antwort.ok) throw new Error(`${url}: ${antwort.status}`)
  const daten = Buffer.from(await antwort.arrayBuffer())
  if (erwartet && sha256(daten) !== erwartet) throw new Error(`Prüfsumme passt nicht: ${url}`)
  return daten
}

const datensaetze = async (sammlung) => (await (await fetch(`${SAMMLUNG}/${sammlung}/records`)).json()).data

// Fassungen wie „2.1“; Vorabfassungen („1.0a1“) zählen nicht.
const stabil = (r) => /^\d+(\.\d+)*$/.test(r.version)
const neuer = (a, b) => {
  const x = a.split('.').map(Number)
  const y = b.split('.').map(Number)
  for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) > (y[i] ?? 0)
  return false
}
const eintrag = (a) => ({ ort: a.location, groesse: a.size, sha256: a.hash })

// Die Filter sagen, für welche Plattform Firefox die Modelle anbietet. Wir
// folgen dem: Desktop nimmt die Fassung, die dort gilt; Android die eigene,
// kleinere Android-Fassung (etwa Japanisch, Koreanisch, vereinfachtes
// Chinesisch). Kanalfilter für Nightly/Beta zählen nicht.
const AUF_DESKTOP = "env.appinfo.OS != 'Android' || env.channel != 'release'"
const AUF_ANDROID = "env.appinfo.OS == 'Android'"
const filter = (r) => (r.filter_expression ?? '').replace(/\s+/g, ' ').trim()
const erlaubt = (r, android) => {
  const f = filter(r)
  return !f || f === (android ? AUF_ANDROID : AUF_DESKTOP)
}

async function auswahl(android) {
  const alle = (await datensaetze('translations-models')).filter(stabil).filter((r) => erlaubt(r, android))
  // Je Paar und Dateiart die neueste Fassung. Alle Dateien eines Paares
  // müssen aus derselben Fassung stammen.
  const beste = new Map()
  for (const r of alle) {
    // Jedes Paar hat Englisch auf einer Seite: alles nach Englisch, und von
    // Englisch in die übrigen Ziele (fr → de geht über Englisch).
    if (r.toLang !== 'en' && !(r.fromLang === 'en' && ZIELE.includes(r.toLang))) continue
    const k = `${r.fromLang}-${r.toLang}`
    const bisher = beste.get(k)
    if (!bisher || neuer(r.version, bisher.version)) beste.set(k, { version: r.version, dateien: {} })
    if (beste.get(k).version === r.version) beste.get(k).dateien[r.fileType] = { ...eintrag(r.attachment), name: r.attachment.filename }
  }
  const paare = {}
  for (const [k, { dateien }] of [...beste].sort(([a], [b]) => a.localeCompare(b))) {
    if (!dateien.model || !dateien.lex || !(dateien.vocab || (dateien.srcvocab && dateien.trgvocab))) continue
    paare[k] = dateien
  }
  return paare
}

const glue = await holen(`${QUELLE}/bergamot-translator.js`, GLUE_SHA256)
const lizenz = await holen(`${QUELLE}/LICENSE`, LIZENZ_SHA256)
writeFileSync(join(ziel, 'bergamot/bergamot-translator.js'), glue)
writeFileSync(join(ziel, 'bergamot/LICENSE'), lizenz)

const wasm = (await datensaetze('translations-wasm')).filter((r) => r.name === 'bergamot-translator' && r.release === WASM_RELEASE && stabil(r))
if (wasm.length !== 1) throw new Error(`WASM ${WASM_RELEASE}: ${wasm.length} Einträge`)

const paare = await auswahl(false)
const android = await auswahl(true)
// Nur Paare, deren Android-Fassung sich von der Desktop-Fassung unterscheidet.
const eigene = {}
for (const [k, dateien] of Object.entries(android)) {
  if (JSON.stringify(dateien) !== JSON.stringify(paare[k])) eigene[k] = dateien
}

const liste = { wasm: eintrag(wasm[0].attachment), paare, ...(Object.keys(eigene).length ? { android: eigene } : {}) }
writeFileSync(join(ziel, 'modelle.json'), JSON.stringify(liste, null, 2) + '\n')
console.log(`Engine ${COMMIT.slice(0, 8)}, WASM ${WASM_RELEASE}, ${Object.keys(paare).length} Paare, ${Object.keys(eigene).length} nur für Android in ${ziel}`)
