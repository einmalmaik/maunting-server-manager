import type { AiGeoCameraCommand, AiRegionalAnalysis, AiSatelliteLayer, AiSatelliteScene } from '@/api/ai'

type RecordValue = Record<string, unknown>

function isRecord(value: unknown): value is RecordValue {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function normalizeBbox(value: unknown, latitude: number, longitude: number): [number, number, number, number] {
  if (
    Array.isArray(value) &&
    value.length === 4 &&
    value.every((entry) => typeof entry === 'number' && Number.isFinite(entry))
  ) {
    const [minLongitude, minLatitude, maxLongitude, maxLatitude] = value as number[]
    if (
      minLongitude >= -180 && maxLongitude <= 180 &&
      minLatitude >= -90 && maxLatitude <= 90 &&
      minLongitude <= maxLongitude && minLatitude <= maxLatitude
    ) {
      return [minLongitude, minLatitude, maxLongitude, maxLatitude]
    }
  }

  const delta = 0.05
  return [
    Math.max(-180, longitude - delta),
    Math.max(-90, latitude - delta),
    Math.min(180, longitude + delta),
    Math.min(90, latitude + delta),
  ]
}

function normalizeWeather(value: unknown): AiRegionalAnalysis['weather'] | undefined {
  if (!isRecord(value)) return undefined

  const temperature = finiteNumber(value.temperature_celsius)
  const apparentTemperature = finiteNumber(value.apparent_temperature_celsius)
  const humidity = finiteNumber(value.humidity_percent)
  const precipitation = finiteNumber(value.precipitation_mm)
  const windSpeed = finiteNumber(value.wind_speed_kmh)
  const condition = text(value.condition)
  if (
    temperature === null || apparentTemperature === null || humidity === null ||
    precipitation === null || windSpeed === null || !condition
  ) {
    return undefined
  }

  return {
    temperature_celsius: temperature,
    apparent_temperature_celsius: apparentTemperature,
    humidity_percent: humidity,
    precipitation_mm: precipitation,
    wind_speed_kmh: windSpeed,
    condition,
  }
}

function normalizeLayers(value: unknown): Record<string, AiSatelliteLayer> | undefined {
  if (!isRecord(value)) return undefined

  const layers = Object.entries(value).flatMap(([key, entry]) => {
    if (!isRecord(entry)) return []
    const id = text(entry.id) || key
    const name = text(entry.name)
    const url = text(entry.url)
    if (!id || !name || !url) return []
    return [[id, {
      id,
      name,
      url,
      resolution: text(entry.resolution) || undefined,
      mission: text(entry.mission) || undefined,
      description: text(entry.description) || undefined,
    } satisfies AiSatelliteLayer] as const]
  })

  return layers.length > 0 ? Object.fromEntries(layers) : undefined
}

function normalizeScene(value: unknown): AiSatelliteScene | null {
  if (!isRecord(value)) return null

  const id = text(value.id)
  const mission = text(value.mission)
  const datetime = text(value.datetime)
  const previewUrl = text(value.preview_url)
  if (!id || !mission || !datetime || !previewUrl) return null

  return {
    id,
    mission,
    datetime,
    cloud_cover_percent: finiteNumber(value.cloud_cover_percent),
    preview_url: previewUrl,
    layers: normalizeLayers(value.layers),
  }
}

function normalizeSatellite(value: unknown): AiRegionalAnalysis['satellite'] | undefined {
  if (!isRecord(value) || !Array.isArray(value.scenes)) return undefined

  const scenes = value.scenes.flatMap((scene) => {
    const normalized = normalizeScene(scene)
    return normalized ? [normalized] : []
  })
  return {
    available: value.available === true,
    scenes,
    layers: normalizeLayers(value.layers),
  }
}

function normalizeNews(value: unknown): AiRegionalAnalysis['news'] | undefined {
  if (!Array.isArray(value)) return undefined

  return value.flatMap((entry) => {
    if (!isRecord(entry)) return []
    const item = {
      title: text(entry.title) || undefined,
      url: text(entry.url) || undefined,
      snippet: text(entry.snippet) || text(entry.description) || text(entry.content) || undefined,
      description: text(entry.description) || undefined,
      content: text(entry.content) || undefined,
      published_date: text(entry.published_date) || undefined,
    }
    return Object.values(item).some(Boolean) ? [item] : []
  })
}

function externalUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

function normalizeTraffic(value: unknown): AiRegionalAnalysis['traffic'] | undefined {
  if (!isRecord(value)) return undefined
  const status = value.status
  if (status !== 'available' && status !== 'not_configured' && status !== 'unavailable') return undefined

  const optionalNumber = (entry: unknown) => finiteNumber(entry) ?? undefined
  const reason = typeof value.reason === 'string' && [
    'invalid_key', 'traffic_not_enabled', 'no_coverage', 'rate_limited',
    'network_error', 'provider_error', 'invalid_response',
  ].includes(value.reason) ? value.reason as NonNullable<AiRegionalAnalysis['traffic']>['reason'] : undefined
  return {
    status,
    reason,
    current_speed_kmh: optionalNumber(value.current_speed_kmh),
    free_flow_speed_kmh: optionalNumber(value.free_flow_speed_kmh),
    current_travel_time_seconds: optionalNumber(value.current_travel_time_seconds),
    free_flow_travel_time_seconds: optionalNumber(value.free_flow_travel_time_seconds),
    confidence: optionalNumber(value.confidence),
    road_closure: value.road_closure === true ? true : value.road_closure === false ? false : undefined,
  }
}

function normalizePublicPosts(value: unknown): AiRegionalAnalysis['public_posts'] | undefined {
  if (!isRecord(value) || (value.status !== 'available' && value.status !== 'unavailable')) return undefined

  const reddit = Array.isArray(value.reddit) ? value.reddit.flatMap((entry) => {
    if (!isRecord(entry)) return []
    const title = text(entry.title)
    const snippet = text(entry.snippet)
    const url = externalUrl(entry.url)
    return title && snippet && url ? [{ title, snippet, url }] : []
  }) : []
  const bluesky = Array.isArray(value.bluesky) ? value.bluesky.flatMap((entry) => {
    if (!isRecord(entry)) return []
    const author = text(entry.author)
    const postText = text(entry.text)
    const url = externalUrl(entry.url)
    return author && postText && url ? [{ author, text: postText, url }] : []
  }) : []

  return { status: value.status, reddit, bluesky, untrusted: true }
}

/**
 * Normalisiert die WebSocket-Nutzlast, bevor sie in die Kartenansicht gelangt.
 * Alte Brückenformen (`region`, `lat`, `lon`) bleiben lesbar, aber ungültige
 * Koordinaten werden nie an MapLibre oder die Regionalansicht gereicht.
 */
export function normalizeRegionalAnalysis(value: unknown): AiRegionalAnalysis | null {
  if (!isRecord(value) || !isRecord(value.coordinates)) return null

  const latitude = finiteNumber(value.coordinates.latitude ?? value.coordinates.lat)
  const longitude = finiteNumber(value.coordinates.longitude ?? value.coordinates.lon)
  if (
    latitude === null || longitude === null ||
    latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180
  ) {
    return null
  }

  const cameraMode = value.camera && isRecord(value.camera) ? value.camera.mode : undefined
  const cameraAction = value.camera && isRecord(value.camera) ? value.camera.action : undefined
  const cameraCommandId = value.camera && isRecord(value.camera) ? text(value.camera.command_id) : ''
  return {
    status: value.status === 'error' ? 'error' : 'success',
    location: text(value.location) || text(value.region),
    country: text(value.country),
    coordinates: {
      latitude,
      longitude,
      bbox: normalizeBbox(value.coordinates.bbox, latitude, longitude),
    },
    weather: normalizeWeather(value.weather),
    satellite: normalizeSatellite(value.satellite),
    news: normalizeNews(value.news),
    news_status:
      value.news_status === 'pending' || value.news_status === 'available' ||
      value.news_status === 'not_allowed' || value.news_status === 'not_configured' ||
      value.news_status === 'unavailable'
        ? value.news_status
        : undefined,
    traffic: normalizeTraffic(value.traffic),
    public_posts: normalizePublicPosts(value.public_posts),
    camera: cameraMode === 'overview' || cameraMode === 'focus' || cameraMode === 'detail'
      ? {
          mode: cameraMode,
          action: cameraAction === 'zoom_in' || cameraAction === 'zoom_out' ||
            cameraAction === 'overview' || cameraAction === 'focus_location'
            ? cameraAction
            : undefined,
          command_id: cameraCommandId || undefined,
        }
      : undefined,
  }
}

export function normalizeGeoCameraCommand(value: unknown): AiGeoCameraCommand | null {
  if (!isRecord(value)) return null
  const action = value.action
  const commandId = text(value.command_id)
  if (
    (
      action !== 'zoom_in' && action !== 'zoom_out' &&
      action !== 'overview' && action !== 'focus_location'
    ) ||
    !commandId || commandId.length > 64
  ) {
    return null
  }
  if (action !== 'focus_location') return { action, command_id: commandId }

  if (!isRecord(value.coordinates)) return null
  const latitude = finiteNumber(value.coordinates.latitude)
  const longitude = finiteNumber(value.coordinates.longitude)
  if (
    latitude === null || longitude === null ||
    latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180
  ) {
    return null
  }
  const location = text(value.location)
  if (!location) return null
  return {
    action,
    command_id: commandId,
    location,
    country: text(value.country) || undefined,
    coordinates: {
      latitude,
      longitude,
      bbox: normalizeBbox(value.coordinates.bbox, latitude, longitude),
    },
  }
}

export function applyGeoCameraCommand(
  analysis: AiRegionalAnalysis | null,
  value: unknown,
): AiRegionalAnalysis | null {
  const command = normalizeGeoCameraCommand(value)
  if (!analysis || !command) return analysis
  return {
    ...analysis,
    ...(command.action === 'focus_location' && command.coordinates
      ? {
          location: command.location ?? analysis.location,
          country: command.country ?? analysis.country,
          coordinates: command.coordinates,
        }
      : {}),
    camera: {
      mode: command.action === 'overview'
        ? 'overview'
        : command.action === 'focus_location'
          ? 'detail'
          : (analysis.camera?.mode ?? 'focus'),
      action: command.action,
      command_id: command.command_id,
    },
  }
}

export function hasRegionalCoordinates(value: unknown): value is AiRegionalAnalysis {
  return normalizeRegionalAnalysis(value) !== null
}
