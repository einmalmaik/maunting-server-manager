export type VoiceDebugCode = string

export function voiceDebug(code: VoiceDebugCode, detail?: Record<string, unknown>) {
  const hint = detail ? ` ${JSON.stringify(detail)}` : ""
  console.debug(`[voice] ${code}${hint}`)
  try {
    window.dispatchEvent(new CustomEvent("msm:voice-debug", { detail: { code, ...detail } }))
  } catch {}
}

export function voiceWarn(code: VoiceDebugCode, detail?: Record<string, unknown>) {
  const hint = detail ? ` ${JSON.stringify(detail)}` : ""
  console.warn(`[voice] ${code}${hint}`)
  try {
    window.dispatchEvent(new CustomEvent("msm:voice-debug", { detail: { code, ...detail } }))
  } catch {}
}

export function voiceError(code: VoiceDebugCode, detail?: Record<string, unknown>) {
  const hint = detail ? ` ${JSON.stringify(detail)}` : ""
  console.error(`[voice] ${code}${hint}`)
  try {
    window.dispatchEvent(new CustomEvent("msm:voice-debug", { detail: { code, ...detail } }))
  } catch {}
}
