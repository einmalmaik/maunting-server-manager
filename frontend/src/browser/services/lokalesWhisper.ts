/**
 * Lokales On-Device Whisper-Tiny für die Maunting Secure Browser Sprachsuche.
 *
 * Verarbeitet die Spracheingabe vollständig lokal im Browser via WebGPU / WebAssembly (WASM)
 * über Hugging Face Transformers.js (@huggingface/transformers) und onnxruntime-web.
 *
 * Performance- & Datenschutz-Garantie:
 * - 0 Byte Audiodaten verlassen das Gerät.
 * - Keine US-Server, keine Cloud-Sprach-APIs, kein Telemetrie-Tracking.
 * - Schnelle Inferenz durch:
 *   1. Greedy Decoding (num_beams: 1, temperature: 0) -> ca. 4x schneller als Beam Search.
 *   2. Token-Limit (max_new_tokens: 48) -> verhindert Hunderte unnötige Decoder-Schritte bei kurzen Suchanfragen.
 *   3. Stille-Trimming (VAD-Zuschnitt) -> schneidet stille Vor- und Nachlaufzeiten ab.
 *   4. Modell-Vorwärmung -> lädt das gecachte Modell im Hintergrund vor.
 */

export interface AudioAufnahmeHandle {
  stoppen: () => Promise<Float32Array>
  abbrechen: () => void
}

/**
 * Prüft, ob der Browser grundlegende MediaDevices- und WebAudio-Unterstützung hat.
 */
export function istLokalesWhisperMoeglich(): boolean {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false
  const hatMedia = Boolean(navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function')
  const hatAudioCtx = typeof window.AudioContext !== 'undefined' || typeof (window as unknown as { webkitAudioContext: unknown }).webkitAudioContext !== 'undefined'
  return hatMedia && hatAudioCtx
}

// Pipeline-Cache: Das Modell wird nur einmalig initialisiert und im Arbeitsspeicher gehalten
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let whisperPipelinePromise: Promise<any> | null = null

/**
 * Lädt die Whisper-Tiny Pipeline (q8 quantisiert, ~39 MB) on-demand.
 * Versucht zuerst hardwarebeschleunigtes WebGPU, fällt bei Inkompatibilität automatisch auf WASM zurück.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function ladeWhisperModell(onFortschritt?: (fortschritt: number) => void): Promise<any> {
  if (!whisperPipelinePromise) {
    whisperPipelinePromise = (async () => {
      const { pipeline, env } = await import('@huggingface/transformers')
      env.allowLocalModels = false

      // 1. WebGPU versuchen (schnellste Ausführung via GPU, minimale CPU-Last)
      try {
        return await pipeline('automatic-speech-recognition', 'onnx-community/whisper-tiny', {
          dtype: 'q8',
          device: 'webgpu',
          progress_callback: (info: { status: string; progress?: number }) => {
            if (info.status === 'progress' && typeof info.progress === 'number' && onFortschritt) {
              onFortschritt(Math.round(info.progress))
            }
          },
        })
      } catch (errWebgpu) {
        console.warn('[Whisper] WebGPU nicht verfügbar, wechsle auf WebAssembly (WASM):', errWebgpu)
        // 2. WebAssembly Fallback
        return await pipeline('automatic-speech-recognition', 'onnx-community/whisper-tiny', {
          dtype: 'q8',
          device: 'wasm',
          progress_callback: (info: { status: string; progress?: number }) => {
            if (info.status === 'progress' && typeof info.progress === 'number' && onFortschritt) {
              onFortschritt(Math.round(info.progress))
            }
          },
        })
      }
    })().catch((err) => {
      whisperPipelinePromise = null
      throw err
    })
  }
  return whisperPipelinePromise
}

/**
 * Wärmt das Modell im Hintergrund vor (lädt Cache und initialisiert Pipeline).
 */
export function vorwaermenWhisperModell(): void {
  if (typeof window === 'undefined') return
  void ladeWhisperModell().catch(() => {
    // Fehler werden beim tatsächlichen Klick abgefangen
  })
}

/**
 * Lineare Interpolation zur Abtastratenkonvertierung auf 16 kHz Mono für Whisper.
 */
export function resampleZu16k(audioBuffer: Float32Array, originalSampleRate: number): Float32Array {
  if (originalSampleRate === 16000 || audioBuffer.length === 0) return audioBuffer
  const ratio = originalSampleRate / 16000
  const neueLaenge = Math.round(audioBuffer.length / ratio)
  const result = new Float32Array(neueLaenge)
  for (let i = 0; i < neueLaenge; i++) {
    const position = i * ratio
    const index = Math.floor(position)
    const rest = position - index
    const naechster = index + 1 < audioBuffer.length ? audioBuffer[index + 1] : audioBuffer[index]
    result[i] = audioBuffer[index] * (1 - rest) + naechster * rest
  }
  return result
}

/**
 * Schneidet Stille am Anfang und Ende des Audios ab, um die Whisper-Berechnung drastisch zu beschleunigen.
 */
export function trimmeStille(audio: Float32Array, schwellenwert = 0.008): Float32Array {
  if (audio.length === 0) return audio
  let start = 0
  while (start < audio.length && Math.abs(audio[start]) < schwellenwert) {
    start++
  }
  let end = audio.length - 1
  while (end > start && Math.abs(audio[end]) < schwellenwert) {
    end--
  }
  // 150ms Marge (2400 Samples bei 16 kHz) davor und danach beibehalten
  const marge = 2400
  const bereinigterStart = Math.max(0, start - marge)
  const bereinigtesEnde = Math.min(audio.length, end + marge)
  if (bereinigterStart >= bereinigtesEnde) return audio
  return audio.slice(bereinigterStart, bereinigtesEnde)
}

/**
 * Startet die Mikrofon-Audioaufnahme mit Rauschunterdrückung und Samplerate-Normalisierung.
 */
export async function starteAudioAufnahme(): Promise<AudioAufnahmeHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      sampleRate: 16000,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  })

  const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
  const audioCtx = new AudioContextClass({ sampleRate: 16000 })
  const source = audioCtx.createMediaStreamSource(stream)
  // ScriptProcessorNode für breite Browserunterstützung
  const processor = audioCtx.createScriptProcessor(4096, 1, 1)

  const chunks: Float32Array[] = []

  processor.onaudioprocess = (e) => {
    const inputData = e.inputBuffer.getChannelData(0)
    chunks.push(new Float32Array(inputData))
  }

  source.connect(processor)
  processor.connect(audioCtx.destination)

  let beendet = false
  const aufraeumen = () => {
    if (beendet) return
    beendet = true
    try {
      source.disconnect()
      processor.disconnect()
    } catch {
      // Ignorieren falls bereits getrennt
    }
    for (const track of stream.getTracks()) {
      try {
        track.stop()
      } catch {
        // Ignorieren
      }
    }
    if (audioCtx.state !== 'closed') {
      void audioCtx.close()
    }
  }

  return {
    abbrechen: aufraeumen,
    stoppen: async () => {
      aufraeumen()
      const gesamtLaenge = chunks.reduce((sum, c) => sum + c.length, 0)
      const zusammen = new Float32Array(gesamtLaenge)
      let offset = 0
      for (const c of chunks) {
        zusammen.set(c, offset)
        offset += c.length
      }

      // Bei Geräten, die eine andere Abtastrate erzwingen (z. B. manche Mobiltelefone mit 44.1/48 kHz)
      if (audioCtx.sampleRate !== 16000) {
        return resampleZu16k(zusammen, audioCtx.sampleRate)
      }
      return zusammen
    },
  }
}

/**
 * Transkribiert 16 kHz Mono-Audio über das lokale Whisper-Tiny Modell mit optimierten Generation-Parametern.
 */
export async function transkribiereAudioMitWhisper(
  audio: Float32Array,
  sprache?: string,
  onFortschritt?: (fortschritt: number) => void
): Promise<string> {
  if (audio.length === 0) return ''

  // RMS-Prüfung: Prüfen, ob überhaupt gesprochen wurde
  let summeQuadrat = 0
  for (let i = 0; i < audio.length; i++) {
    summeQuadrat += audio[i] * audio[i]
  }
  const rms = Math.sqrt(summeQuadrat / audio.length)
  if (rms < 0.0005) {
    // Reines Rauschen oder Stille
    return ''
  }

  // Stille vor und nach der Sprache trimmen für minimale Latenz
  const getrimmt = trimmeStille(audio)
  const zuVerarbeiten = getrimmt.length > 0 ? getrimmt : audio

  const transcriber = await ladeWhisperModell(onFortschritt)
  const spracheCode = sprache?.toLowerCase().startsWith('en') ? 'english' : 'german'

  // Optimierte Inferenz-Parameter für Suchbegriffe (Greedy Search, max 48 Tokens)
  const ergebnis = await transcriber(zuVerarbeiten, {
    language: spracheCode,
    task: 'transcribe',
    return_timestamps: false,
    max_new_tokens: 48, // Kurze Suchbegriffe; verhindert Hunderte unnötige Rechenschritte
    chunk_length_s: 30,
    stride_length_s: 5,
    num_beams: 1, // Greedy Search: 4x schneller als Beam Search
    temperature: 0,
    do_sample: false,
  })

  let roh = ''
  if (typeof ergebnis === 'string') roh = ergebnis.trim()
  else if (ergebnis && typeof ergebnis.text === 'string') roh = ergebnis.text.trim()
  else if (Array.isArray(ergebnis) && ergebnis[0] && typeof ergebnis[0].text === 'string') {
    roh = ergebnis[0].text.trim()
  }
  return normalisiereSprachergebnis(roh)
}

/**
 * Bereinigt und korrigiert typische phonetische Fehlinterpretationen von Whisper-Tiny
 * bei Gaming-, Popkultur- und Abkürzungsbegriffen im gemischten Sprachgebrauch.
 */
export function normalisiereSprachergebnis(text: string): string {
  if (!text) return ''
  return text
    // Gaming & Popkultur
    .replace(/\b[wW]irtschaft\s*4\b/g, 'Witcher 4')
    .replace(/\b[wW]irtschaft\s*3\b/g, 'Witcher 3')
    .replace(/\b[tT]he\s+[wW]irtschaft\b/g, 'The Witcher')
    .replace(/\b[wW]itcher\s*4\b/g, 'Witcher 4')
    .replace(/\b[gG]\s*[iI]\s*[tT]\s*[hH]?[yY]?[aA]\s*6\b/gi, 'GTA 6')
    .replace(/\b[gG]\s*[iI]\s*[tT]\s*[hH]?[yY]?[aA]\b/gi, 'GTA')
    .replace(/\bgta\s*6\b/gi, 'GTA 6')
    .replace(/\bgta\s*5\b/gi, 'GTA 5')
    .replace(/\b[pP]laystation\s*5\b/gi, 'PlayStation 5')
    .replace(/\b[yY]outube\b/gi, 'YouTube')
    .replace(/\b[cC]hat\s*[gG][pP][tT]\b/gi, 'ChatGPT')
    .trim()
}
