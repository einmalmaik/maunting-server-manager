/** What the swarm shows. `working` is thinking while a tool runs: the swarm forms the MSM logo. */
export type SwarmState =
  | 'off'
  | 'connecting'
  | 'ready'
  | 'listening'
  | 'thinking'
  | 'working'
  | 'speaking'
  | 'fault'
  | 'expired'

export const SWARM_STATES: readonly SwarmState[]

/** A place on Earth. While set, the swarm forms the Earth, turned towards it. */
export interface SwarmEarth {
  latitude: number
  longitude: number
}

export interface SwarmInput {
  state?: SwarmState
  /** Current loudness between 0 and 1. Read once per frame, never stored. */
  level?: () => number
  earth?: SwarmEarth | null
}

export interface SwarmOptions extends SwarmInput {
  /** `auto` prefers WebGL and falls back to Canvas 2D without a GPU. */
  renderer?: 'auto' | 'webgl' | 'canvas'
  /** Upper bound for the device pixel ratio. Defaults to 2. */
  maxPixelRatio?: number
  /** `auto` follows `prefers-reduced-motion`, including later changes. */
  reducedMotion?: boolean | 'auto'
  /** Start the animation loop right away. Defaults to `true`. */
  autoplay?: boolean
}

export interface SwarmStats {
  renderer: 'webgl' | 'canvas' | 'none'
  fps: number | null
  /** Resolution factor, lowered on devices that cannot keep 45 fps. */
  quality: number
  /** Share of the particles drawn, lowered together with the resolution. */
  detail: number
  width: number
  height: number
  pixelRatio: number
  reducedMotion: boolean
}

export interface Swarm {
  readonly renderer: 'webgl' | 'canvas' | 'none'
  update(input: SwarmInput): void
  /** A tool call started: one ring of light runs through the swarm. */
  pulse(): void
  /** Steps the motion in fixed 1/60 s steps and draws once. For tests and screenshots. */
  advance(seconds: number): void
  stats(): SwarmStats
  destroy(): void
}

export function createSwarm(host: HTMLElement, options?: SwarmOptions): Swarm

/** Speech-like loudness without a microphone: syllables and pauses, deterministic per seed. */
export function simulatedSpeech(seed?: number): (timeSeconds: number) => number
