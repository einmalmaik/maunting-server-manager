/**
 * Der WebGL-Zeichner des Schwarms.
 *
 * Handgeschrieben und ohne `three.js`: der Schwarm ist ein einziger
 * Zeichenaufruf mit 9000 Punkten, dazu zwei Leuchtflecke. Eine
 * Szenenbibliothek brächte rund 600 KB, einen Szenengraphen und Beleuchtung,
 * von denen hier nichts gebraucht wird. Die Shader sind GLSL ES 1.00 und
 * laufen deshalb in WebGL 1 und 2 gleichermaßen, also auch in älteren
 * Android-WebViews.
 *
 * Wo ein Punkt steht, rechnet der Vertex-Shader — dieselbe Rechnung wie
 * `placeParticle` in `particles.js`, das der Canvas-Zeichner nimmt. Die
 * Punktdaten liegen einmal auf der Grafikkarte; je Bild wandern nur ein paar
 * Dutzend Zahlen hinüber.
 *
 * Alles wird vormultipliziert gemischt (`ONE, ONE_MINUS_SRC_ALPHA`), und jeder
 * Shader gibt ein gültiges Alpha aus: Leuchten deckt so viel, wie es hell ist.
 * Nur so sieht der Schein über einem durchsichtigen Fenster — dem Overlay auf
 * dem Desktop — genauso aus wie über dem dunklen Panel.
 */
import { EDGE_FADE, HISTORY_LENGTH, camera, radiusPx } from './motion.js'
import {
  ORBIT_A_AXIS,
  ORBIT_B_AXIS,
  POINT_COVER,
  SHARE,
  STRIDE,
  SWARM_SIZE,
  frameUniforms,
  highlights,
  particles,
  pointSizeCss,
} from './particles.js'

const PRECISION = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
`

// 3D-Simplex-Rauschen aus webgl-noise von Ian McEwan und Ashima Arts,
// MIT-Lizenz (https://github.com/ashima/webgl-noise).
const NOISE = `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(
    i.z + vec4(0.0, i1.z, i2.z, 1.0))
    + i.y + vec4(0.0, i1.y, i2.y, 1.0))
    + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
`

const f = (x) => x.toFixed(6)
const LAST = HISTORY_LENGTH - 1

// Die Rechnung von `placeParticle`, Zeile für Zeile. Kommentare stehen dort.
const SWARM_VS = `
attribute vec3 aDirection;
attribute vec4 aRandom;
attribute vec3 aLogo;
attribute vec2 aFlags;
attribute vec3 aEarth;
uniform mat4 uPV;
uniform mat3 uView;
uniform mat3 uEarth;
uniform mat3 uLogoTurn;
uniform vec3 uTarget;
uniform float uEarthWeight;
uniform float uTime;
uniform float uCalm;
uniform float uLevel;
uniform float uHistory[${HISTORY_LENGTH}];
uniform vec3 uVortex;
uniform float uPulse;
uniform float uPulseRadius;
uniform float uPointSize;
uniform float uMaxPointSize;
uniform float uBrightness;
uniform vec3 uColor;
uniform vec3 uIce;
uniform float uRest;
uniform float uListening;
uniform float uThinking;
uniform float uWorking;
uniform float uSpeaking;
uniform float uFault;
uniform float uExpired;
uniform float uOff;
uniform float uConnecting;
varying vec3 vColor;
${NOISE}
const vec3 ORBIT_A = vec3(${ORBIT_A_AXIS.map(f).join(', ')});
const vec3 ORBIT_B = vec3(${ORBIT_B_AXIS.map(f).join(', ')});
float history(float x) {
  float i = clamp(x, 0.0, 1.0) * ${LAST}.0;
  float a = floor(i);
  return mix(uHistory[int(a)], uHistory[int(min(${LAST}.0, a + 1.0))], i - a);
}
vec3 around(vec3 p, vec3 k, float a) {
  float c = cos(a);
  float s = sin(a);
  return p * c + cross(k, p) * s + k * dot(k, p) * (1.0 - c);
}
vec3 logoPoint() {
  float w = aRandom.w;
  if (w < ${f(SHARE.ring)}) return around(aLogo, vec3(0.0, 0.0, 1.0), uTime * (0.54 + aRandom.x * 0.72));
  if (w < ${f(SHARE.nodes)}) return aLogo;
  if (w < ${f(SHARE.orbitA)}) return around(aLogo, ORBIT_A, uTime * 0.5);
  return around(aLogo, ORBIT_B, -uTime * 0.45);
}
void main() {
  float e = uEarthWeight;
  float t = uTime;
  float moving = 1.0 - uCalm;
  vec3 g = uView * aDirection;
  vec3 q = uEarth * aEarth;
  vec3 n = normalize(mix(g, q, e) + vec3(0.0, 0.0, 1e-5));
  float flow = snoise(aDirection * 2.2 + vec3(0.0, 0.0, t * 0.25));
  float angle = acos(clamp(n.z, -1.0, 1.0)) / 3.14159265;
  float front = smoothstep(-0.2, 1.0, n.z);
  float waveIn = uCalm > 0.5 ? uLevel : history(1.0 - angle);
  float waveOut = uCalm > 0.5 ? uLevel : history(angle);
  float life = fract(t * 0.85 + aRandom.x * 7.0);
  float flier = aRandom.w < ${f(SHARE.fliers)} ? moving : 0.0;
  float flyIn = flier * (1.0 - life) * uLevel;
  float flyOut = flier * life * history(life * 0.9);

  vec3 p = vec3(0.0);
  float total = 0.0;
  if (uRest > 0.001) {
    p += uRest * mix(g * (1.0 + 0.02 * flow), q * (1.0 + 0.008 * flow), e);
    total += uRest;
  }
  if (uListening > 0.001) {
    float a = 1.0 + moving * (0.05 + 0.3 * front) * waveIn + 0.02 * flow + flyIn * 1.1;
    float b = 1.0 + moving * (0.03 + 0.12 * front) * waveIn + flyIn * 0.5;
    p += uListening * mix(g * a, q * b, e);
    total += uListening;
  }
  if (uSpeaking > 0.001) {
    float a = 1.0 + moving * 0.2 * waveOut + 0.02 * flow + flyOut * 1.2;
    float b = 1.0 + moving * 0.08 * waveOut + flyOut * 0.55;
    p += uSpeaking * mix(g * a, q * b, e);
    total += uSpeaking;
  }
  if (uThinking > 0.001) {
    float turn = uVortex.x + uVortex.y * abs(aDirection.y) + uVortex.z * flow;
    float c = cos(turn);
    float s = sin(turn);
    vec3 w = vec3(c * aDirection.x + s * aDirection.z, aDirection.y, -s * aDirection.x + c * aDirection.z);
    float swell = 1.0 + moving * 0.07 * snoise(aDirection * 3.0 + t * 0.8);
    p += uThinking * mix(uView * w * swell, q, e);
    total += uThinking;
  }
  if (uWorking > 0.001) {
    vec3 logo = uLogoTurn * logoPoint();
    p += uWorking * mix(logo, aFlags.y > 0.5 ? logo : q * 0.8, e);
    total += uWorking;
  }
  if (uFault > 0.001) {
    float fray = snoise(aDirection * 1.7 + vec3(t * 1.6));
    vec3 jitter = vec3(snoise(aDirection + t), snoise(aDirection.yzx + t), 0.0);
    p += uFault * mix(g * (1.0 + 0.45 * fray) + 0.12 * jitter, q * (1.0 + 0.2 * fray) + 0.06 * jitter, e);
    total += uFault;
  }
  if (uExpired > 0.001) {
    p += uExpired * vec3(aDirection.x * 1.15, -0.62 + aDirection.y * 0.05, aDirection.z * 1.15);
    total += uExpired;
  }
  if (uConnecting > 0.001) {
    float reach = (1.25 + 0.55 * aRandom.x) * (1.0 - 0.08 * moving * sin(t * 1.7 + aRandom.y * 6.2831853));
    p += uConnecting * g * reach;
    total += uConnecting;
  }
  if (total > 1e-4) p /= total;

  vec4 clip = uPV * vec4(p, 1.0);
  gl_Position = clip;
  // Zum Rand der Leinwand hin verblassen, statt an ihm abzubrechen.
  vec2 ndc = abs(clip.xy / max(clip.w, 1e-3));
  float edge = 1.0 - smoothstep(${f(EDGE_FADE[0])}, ${f(EDGE_FADE[1])}, max(ndc.x, ndc.y));

  float onEarth = e * (1.0 - uWorking * aFlags.y) * (1.0 - uExpired) * (1.0 - uConnecting);
  float land = aFlags.x > 0.5 ? 1.0 : 0.0;
  float star = aRandom.z >= ${f(SHARE.star)} ? 1.0 - onEarth * (1.0 - land) : 0.0;
  float depth = smoothstep(-1.2, 1.1, p.z);
  float bright = (0.22 + 0.78 * depth * depth) * (0.5 + 0.5 * aRandom.y) * (1.0 + star * 1.4) * 0.8;
  float facing = smoothstep(-0.25, 0.35, q.z);
  bright *= 1.0 + ((land > 0.5 ? 1.35 : 0.14) * (0.04 + 0.96 * facing) - 1.0) * onEarth;
  float dist = acos(clamp(dot(aEarth, uTarget), -1.0, 1.0));
  float ping = fract(t * 0.6);
  float ring = (dist - ping * 0.35) * 40.0;
  float target = onEarth * (1.0 - smoothstep(0.0, 0.03, dist) + moving * 0.8 * exp(-ring * ring) * (1.0 - ping));
  bright *= 1.0 + 2.2 * (waveIn * uListening + waveOut * uSpeaking);
  bright *= 1.0 - flier * (uListening * (1.0 - life) * 0.8 * min(1.0, uLevel * 3.0)
    + uSpeaking * life * 0.8 * min(1.0, history(life * 0.9) * 3.0));
  float band = (q.y - sin(t * 1.3) * 0.85) * 8.0;
  bright *= 1.0 + 1.6 * uThinking * onEarth * exp(-band * band) * moving;
  float shell = 0.0;
  if (uPulse > 0.001) {
    float sr = (length(p.xy) - uPulseRadius) * 5.0;
    shell = uPulseRadius < 0.0 ? uPulse * 0.6 : uPulse * exp(-sr * sr);
  }
  bright *= 1.0 + 2.2 * shell;
  bright *= 1.0 - uConnecting * (0.25 + 0.45 * aRandom.x);
  bright *= (1.0 - 0.85 * uOff - 0.55 * uExpired) * uBrightness;

  float ice = clamp(0.25 * aRandom.x + star * 0.5 + target + shell * 0.6, 0.0, 1.0);
  vColor = mix(uColor, uIce, ice) * (bright + target * 2.5) * edge;
  // Nahe Punkte werden größer, aber nie zu Flecken: bei „verbindet" kommt
  // die Wolke der Kamera nahe.
  float perspective = clamp(4.4 / clip.w, 0.6, 2.2);
  float grain = 1.0 + onEarth * (land > 0.5 ? 0.2 : -0.3);
  gl_PointSize = clamp(uPointSize * (0.7 + 0.7 * aRandom.y) * (1.0 + star * 1.3 + target * 1.5) * grain * perspective, 1.0, uMaxPointSize);
}`

const SWARM_FS = `${PRECISION}
varying vec3 vColor;
void main() {
  vec2 q = gl_PointCoord * 2.0 - 1.0;
  float r2 = dot(q, q);
  if (r2 > 1.0) discard;
  vec3 c = 1.0 - exp(-vColor * exp(-r2 * 3.2));
  // Die Punkte decken etwas mehr, als sie leuchten: über Schwarz ändert das
  // nichts, über einem hellen Fenster hinter dem Overlay bleiben sie so als
  // Punkte sichtbar, statt im Weiß zu verschwinden.
  gl_FragColor = vec4(c, min(1.0, max(max(c.r, c.g), c.b) * ${f(POINT_COVER)}));
}`

// ── Leuchtflecke: das Licht in der Mitte, die Marke auf der Erde ─────────

const SPRITE_VS = `
attribute vec2 aCorner;
uniform mat4 uPV;
uniform vec3 uCenter;
uniform vec2 uResolution;
uniform float uSize;
varying vec2 vCorner;
void main() {
  vec4 c = uPV * vec4(uCenter, 1.0);
  gl_Position = c + vec4(aCorner * uSize / uResolution * 2.0 * c.w, 0.0, 0.0);
  vCorner = aCorner;
}`

const SPRITE_FS = `${PRECISION}
varying vec2 vCorner;
uniform vec3 uColor;
uniform float uStrength;
uniform float uCore;
void main() {
  float r = length(vCorner);
  if (r > 1.0) discard;
  float core = exp(-r * r * uCore);
  float glow = exp(-r * 4.5) * 0.45;
  float edge = 1.0 - smoothstep(0.75, 1.0, r);
  vec3 c = 1.0 - exp(-uColor * uStrength * (core * 1.6 + glow) * edge);
  gl_FragColor = vec4(c, max(max(c.r, c.g), c.b));
}`

function compile(gl, vertex, fragment) {
  const shader = (type, source) => {
    const s = gl.createShader(type)
    gl.shaderSource(s, source)
    gl.compileShader(s)
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS) && !gl.isContextLost()) {
      throw new Error(gl.getShaderInfoLog(s) || 'Shader lässt sich nicht übersetzen')
    }
    return s
  }
  const program = gl.createProgram()
  gl.attachShader(program, shader(gl.VERTEX_SHADER, vertex))
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, fragment))
  gl.linkProgram(program)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS) && !gl.isContextLost()) {
    throw new Error(gl.getProgramInfoLog(program) || 'Programm lässt sich nicht binden')
  }
  const uniforms = {}
  const count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS) || 0
  for (let i = 0; i < count; i += 1) {
    const info = gl.getActiveUniform(program, i)
    uniforms[info.name.replace(/\[0\]$/, '')] = gl.getUniformLocation(program, info.name)
  }
  const attributes = {}
  const attributeCount = gl.getProgramParameter(program, gl.ACTIVE_ATTRIBUTES) || 0
  for (let i = 0; i < attributeCount; i += 1) {
    const info = gl.getActiveAttrib(program, i)
    attributes[info.name] = gl.getAttribLocation(program, info.name)
  }
  return { program, uniforms, attributes }
}

export class GlRenderer {
  /**
   * Holt einen Kontext und baut alles auf — oder gibt `null` zurück.
   *
   * `failIfMajorPerformanceCaveat`: läuft WebGL nur in Software (kein Treiber,
   * Remote-Desktop, gesperrte GPU), zeichnet Canvas 2D denselben Schwarm
   * billiger. `allowSoftware` hebt das auf, für Messungen.
   */
  static create(canvas, { allowSoftware = false } = {}) {
    const attributes = {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
      powerPreference: 'default',
      failIfMajorPerformanceCaveat: !allowSoftware,
    }
    let gl = null
    try {
      gl = canvas.getContext('webgl2', attributes) || canvas.getContext('webgl', attributes)
    } catch {
      gl = null
    }
    if (!gl) return null
    const renderer = new GlRenderer(canvas, gl)
    try {
      renderer.build()
    } catch (error) {
      renderer.buildError = error
      renderer.release()
      return null
    }
    return renderer
  }

  constructor(canvas, gl) {
    this.kind = 'webgl'
    this.canvas = canvas
    this.gl = gl
    this.capacity = SWARM_SIZE
  }

  /** Programme und Puffer. Auch nach einem wiederhergestellten Kontext. */
  build() {
    const { gl } = this
    this.swarm = compile(gl, SWARM_VS, SWARM_FS)
    this.sprite = compile(gl, SPRITE_VS, SPRITE_FS)
    this.particleBuffer = this.buffer(particles())
    this.quadBuffer = this.buffer(new Float32Array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]))
    const range = gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)
    this.maxPointSize = range && range[1] ? range[1] : 64
  }

  buffer(data) {
    const { gl } = this
    const b = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, b)
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW)
    return b
  }

  attribute(location, buffer, size, stride, offset) {
    if (location === undefined || location < 0) return -1
    const { gl } = this
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.enableVertexAttribArray(location)
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, stride, offset)
    return location
  }

  draw(pose, view) {
    const { gl } = this
    if (gl.isContextLost()) return
    const { width, height, pixelRatio } = view
    const count = Math.round(SWARM_SIZE * Math.min(1, Math.max(0.3, view.detail ?? 1)))
    gl.viewport(0, 0, width, height)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)

    const pv = camera(width / height)
    const R = radiusPx(pv, 1, height)
    const resolution = [width, height]

    // Das Licht in der Mitte, hinter den Punkten.
    this.glow(pv, resolution, [0, 0, 0], R * 1.45, pose.color, 0.1 + 0.22 * pose.inner, 2.2)

    const u = frameUniforms(pose)
    const { swarm } = this
    const s = swarm.uniforms
    gl.useProgram(swarm.program)
    const stride = STRIDE * 4
    const on = [
      this.attribute(swarm.attributes.aDirection, this.particleBuffer, 3, stride, 0),
      this.attribute(swarm.attributes.aRandom, this.particleBuffer, 4, stride, 12),
      this.attribute(swarm.attributes.aLogo, this.particleBuffer, 3, stride, 28),
      this.attribute(swarm.attributes.aFlags, this.particleBuffer, 2, stride, 40),
      this.attribute(swarm.attributes.aEarth, this.particleBuffer, 3, stride, 48),
    ]
    gl.uniformMatrix4fv(s.uPV, false, pv)
    gl.uniformMatrix3fv(s.uView, false, u.view)
    gl.uniformMatrix3fv(s.uEarth, false, u.earth)
    gl.uniformMatrix3fv(s.uLogoTurn, false, u.logoTurn)
    gl.uniform3f(s.uTarget, u.target[0], u.target[1], u.target[2])
    gl.uniform1f(s.uEarthWeight, u.earthWeight)
    gl.uniform1f(s.uTime, u.time)
    gl.uniform1f(s.uCalm, u.calm)
    gl.uniform1f(s.uLevel, u.level)
    gl.uniform1fv(s.uHistory, u.history)
    gl.uniform3f(s.uVortex, u.vortex[0], u.vortex[1], u.vortex[2])
    gl.uniform1f(s.uPulse, u.pulse)
    gl.uniform1f(s.uPulseRadius, u.pulseRadius)
    gl.uniform1f(s.uPointSize, pointSizeCss(R / pixelRatio) * pixelRatio)
    gl.uniform1f(s.uMaxPointSize, Math.max(1, Math.min(this.maxPointSize, R * 0.25)))
    gl.uniform1f(s.uBrightness, u.brightness)
    gl.uniform3f(s.uColor, pose.color[0], pose.color[1], pose.color[2])
    gl.uniform3f(s.uIce, pose.ice[0], pose.ice[1], pose.ice[2])
    gl.uniform1f(s.uRest, u.rest)
    gl.uniform1f(s.uListening, u.listening)
    gl.uniform1f(s.uThinking, u.thinking)
    gl.uniform1f(s.uWorking, u.working)
    gl.uniform1f(s.uSpeaking, u.speaking)
    gl.uniform1f(s.uFault, u.fault)
    gl.uniform1f(s.uExpired, u.expired)
    gl.uniform1f(s.uOff, u.off)
    gl.uniform1f(s.uConnecting, u.connecting)
    gl.drawArrays(gl.POINTS, 0, count)
    for (const location of on) if (location >= 0) gl.disableVertexAttribArray(location)

    for (const light of highlights(pose)) {
      this.glow(pv, resolution, light.position, R * light.size, light.color, light.strength, light.core)
    }
  }

  glow(pv, resolution, center, size, color, strength, core) {
    if (strength <= 0) return
    const { gl, sprite } = this
    const s = sprite.uniforms
    gl.useProgram(sprite.program)
    const on = this.attribute(sprite.attributes.aCorner, this.quadBuffer, 2, 0, 0)
    gl.uniformMatrix4fv(s.uPV, false, pv)
    gl.uniform3f(s.uCenter, center[0], center[1], center[2])
    gl.uniform2f(s.uResolution, resolution[0], resolution[1])
    gl.uniform1f(s.uSize, size)
    gl.uniform3f(s.uColor, color[0], color[1], color[2])
    gl.uniform1f(s.uStrength, strength)
    gl.uniform1f(s.uCore, core)
    gl.drawArrays(gl.TRIANGLES, 0, 6)
    if (on >= 0) gl.disableVertexAttribArray(on)
  }

  /** Gibt den Kontext sofort frei, statt auf die Speicherbereinigung zu warten. */
  release() {
    try {
      this.gl.getExtension('WEBGL_lose_context')?.loseContext()
    } catch {
      // Ein Kontext, der schon weg ist, muss niemand mehr freigeben.
    }
  }
}
