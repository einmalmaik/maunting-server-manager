# @maunting/design-dna

Versioned, repository-contained Design-DNA boundary for MSM. It exports the
shared visual tokens and their CSS contract. MSM owns interactive components in
`frontend/src/Singra/UI`, which re-exports the established panel primitives.
This keeps one accessibility and behavior implementation per component and does
not depend on the author's external Design-DNA workspace.

## Voice tokens

`voiceTokens` holds the voice palette as HSL triplets, identical to
`--dna-voice-glow|ice|think|fault|rest` in `tokens.css`. `voiceStateTones` maps
every swarm state to one of those keys. The swarm mixes its colour from that
table, and anything that shows the same state beside it (a status dot, a frame)
takes its tone from the same table — `hsl(var(--dna-voice-${tone}))` — so the
two cannot drift apart.

## The swarm — `@maunting/design-dna/swarm`

The voice figure of MSM: 9000 points of light that take a shape. A starry globe
at rest, the MSM logo (ring, four nodes, two orbits) while a tool runs, the
Earth — turned towards the place — during a regional analysis. Listening sends
waves inwards, speaking sends them outwards in time with the voice; thinking
twists the globe into a vortex, a fault frays it, an expired session lays it
down as a disc.

```js
// Always load it lazily: app start never waits for a shader.
const { createSwarm } = await import('@maunting/design-dna/swarm')

const swarm = createSwarm(hostElement, {
  state: 'listening',          // off | connecting | ready | listening | thinking | working | speaking | fault | expired
  level: () => meter.level(),  // 0..1, read once per frame — pass the real input/output level
  earth: null,                 // { latitude, longitude } turns the swarm into the Earth
})
swarm.update({ state: 'working' })
swarm.pulse()                  // a tool call started: one ring of light
swarm.destroy()
```

In MSM, `frontend/src/components/ai/voice/Schwarm.tsx` wraps this for React;
the web panel (`SprachAnsicht`) and the desktop/Android overlay
(`OverlayFenster`) both use that one component.

**Host box.** The swarm fills its host and sizes itself from the shorter side:
the globe radius is `GLOBE_FILL` (45 %) of half of it, which leaves room for
the flying points of listening and speaking. Towards the canvas edges
everything fades out (`EDGE_FADE`, from 72 % to 98 % of the way to the edge)
instead of being cut off, so a tight host never shows a hard border.

**Transparent hosts.** Points are blended premultiplied and cover a little more
than they shine (`POINT_COVER`). Over a dark panel that changes nothing; over a
light window behind a transparent overlay the points stay visible as points.
Do not put a backdrop behind the swarm to make it readable — it does not need
one.

**Renderers.** WebGL (1 or 2, GLSL ES 1.00, one draw call) is preferred. If
WebGL is missing or would only run in software, a Canvas 2D renderer draws the
same poses with 2400 points; without either, the swarm stays empty and never
throws. Resolution and point count drop on devices that cannot keep 45 fps; the
loop sleeps while the host is off-screen or the swarm is at rest, and a lost GPU
context is rebuilt. At rest means nothing would change the next frame: the
swarm is `off` (or motion is reduced), and every cross-fade, the level, a pulse
and the Earth have settled — an Earth that stands still after a session over a
region is a still image too.

**Reduced motion.** `prefers-reduced-motion` is followed, including later
changes: the swarm stops turning, the waves, flights and vortex stop, and the
Earth jumps to a place instead of swinging over. A change of state still
cross-fades between shapes, and the level only brightens the points. With
nothing to show, the loop sleeps even mid-conversation; it samples the level
every 100 ms and wakes as soon as someone speaks.

**Diagnostics.** The host carries `data-swarm-renderer` (`webgl`, `canvas`,
`none`), `data-swarm-fps` and `data-swarm-quality`, so the renderer and frame
rate can be read in the desktop and Android app, where there is no console.
`swarm.stats()` returns the same, `swarm.advance(seconds)` steps the motion
deterministically for tests and screenshots, and `simulatedSpeech(seed)` gives
a speech-like level without a microphone.
