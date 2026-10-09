// Die Übersetzungs-Engine im Worker (`engine.ts`). Ein klassischer Worker,
// kein Modul: `bergamot-translator.js` aus Firefox braucht den Skriptmodus
// (`this` ist dort das globale Objekt) und wird per `importScripts` geladen.
//
// Der Worker holt nichts aus dem Netz. WASM und Modelle bekommt er als Bytes,
// geprüft in `modelle.ts`; die Engine bekommt sie als `wasmBinary`.
/* global loadBergamot */

// Ausrichtung je Dateiart, wie in Firefox (translations-engine.worker.js).
const AUSRICHTUNG = { model: 256, lex: 64, vocab: 64, srcvocab: 64, trgvocab: 64 }
// Ein Modell, das so lange nicht gebraucht wurde, fällt aus dem Speicher.
const RUHE_MS = 5 * 60 * 1000

let bergamot = null
let dienst = null
const modelle = new Map()

function starten(glue, wasm) {
  importScripts(glue)
  return new Promise((fertig, fehler) => {
    const m = loadBergamot({
      wasmBinary: wasm,
      print: () => {},
      printErr: () => {},
      onAbort: () => fehler(new Error('engine')),
      onRuntimeInitialized: async () => {
        await Promise.resolve()
        bergamot = m
        dienst = new m.BlockingService({ cacheSize: 0 })
        fertig()
      },
    })
  })
}

function speicher(bytes, art) {
  const m = new bergamot.AlignedMemory(bytes.byteLength, AUSRICHTUNG[art])
  m.getByteArrayView().set(new Uint8Array(bytes))
  return m
}

function modellLaden(paar, dateien, name) {
  const [von, nach] = paar.split(/-(?=[^-]+$)/)
  const vokabeln = new bergamot.AlignedMemoryList()
  if (dateien.vocab) vokabeln.push_back(speicher(dateien.vocab, 'vocab'))
  else {
    vokabeln.push_back(speicher(dateien.srcvocab, 'srcvocab'))
    vokabeln.push_back(speicher(dateien.trgvocab, 'trgvocab'))
  }
  const einstellung = {
    'beam-size': 1,
    normalize: '1.0',
    'word-penalty': 0,
    'max-length-break': 128,
    'mini-batch-words': 1024,
    workspace: 128,
    'max-length-factor': '2.0',
    'skip-cost': true,
    'cpu-threads': 0,
    quiet: true,
    'quiet-translation': true,
    'gemm-precision': name.endsWith('intgemm8.bin') ? 'int8shiftAll' : 'int8shiftAlphaAll',
    alignment: 'soft',
  }
  const text = '\n' + Object.entries(einstellung).map(([k, v]) => `            ${k}: ${v}\n`).join('') + '            '
  const modell = new bergamot.TranslationModel(von, nach, text, speicher(dateien.model, 'model'), speicher(dateien.lex, 'lex'), vokabeln, null)
  modelle.set(paar, { modell, zuletzt: Date.now() })
}

function aufraeumen() {
  for (const [paar, m] of modelle) {
    if (Date.now() - m.zuletzt > RUHE_MS) {
      m.modell.delete()
      modelle.delete(paar)
    }
  }
}

// Leerraum am Rand und weiche Trennstriche stören die Engine; der Rand kommt
// danach wieder dran.
const RAND = /^(\s*)(.*?)(\s*)$/s

function uebersetzen(paare, texte) {
  const m = paare.map((p) => modelle.get(p))
  if (m.some((x) => !x)) throw new Error('modell')
  for (const x of m) x.zuletzt = Date.now()
  const teile = texte.map((t) => RAND.exec(t))
  const eingabe = new bergamot.VectorString()
  const optionen = new bergamot.VectorResponseOptions()
  let antwort = null
  try {
    for (const [, , kern] of teile) {
      eingabe.push_back(kern.replaceAll('\u00AD', ''))
      optionen.push_back({ qualityScores: false, alignment: false, html: false })
    }
    antwort =
      m.length === 1
        ? dienst.translate(m[0].modell, eingabe, optionen)
        : dienst.translateViaPivoting(m[0].modell, m[1].modell, eingabe, optionen)
    return teile.map(([, vor, , nach], i) => vor + antwort.get(i).getTranslatedText() + nach)
  } finally {
    eingabe.delete()
    optionen.delete()
    antwort?.delete()
  }
}

self.onmessage = async ({ data }) => {
  const { nr } = data
  try {
    aufraeumen()
    if (data.art === 'start') {
      if (!bergamot) await starten(data.glue, data.wasm)
      return postMessage({ nr })
    }
    if (data.art === 'hat') return postMessage({ nr, ergebnis: modelle.has(data.paar) })
    if (data.art === 'modell') {
      if (!modelle.has(data.paar)) modellLaden(data.paar, data.dateien, data.name)
      return postMessage({ nr })
    }
    if (data.art === 'uebersetzen') return postMessage({ nr, ergebnis: uebersetzen(data.paare, data.texte) })
    postMessage({ nr, fehler: 'art' })
  } catch (e) {
    postMessage({ nr, fehler: String((e && e.message) || e) })
  }
}
