export const designDnaVersion: string
export const designTokens: Readonly<Record<string, string>>

export interface VoiceTokens {
  /** The swarm's points and the light at its centre. Same value as `--dna-focus`. */
  glow: string
  /** The brightest points: stars, logo nodes, the place on Earth. Same value as `--dna-primary`. */
  ice: string
  /** Thinking and working. */
  think: string
  /** Fault. Same value as `--dna-warning`. */
  fault: string
  /** Off and expired. */
  rest: string
}

/** HSL triplets (`"190 92% 62%"`), identical to `--dna-voice-*` in `tokens.css`. */
export const voiceTokens: Readonly<VoiceTokens>

/** The tone each swarm state glows in; the swarm and its status marks share it. */
export const voiceStateTones: Readonly<
  Record<
    'off' | 'connecting' | 'ready' | 'listening' | 'thinking' | 'working' | 'speaking' | 'fault' | 'expired',
    keyof VoiceTokens
  >
>
