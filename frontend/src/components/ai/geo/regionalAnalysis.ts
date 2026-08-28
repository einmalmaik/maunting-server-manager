import type { AiRegionalAnalysis, AiSatelliteLayer, AiSatelliteScene } from '@/api/ai'

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
      content: text(entry.content) || undefined,
      published_date: text(entry.published_date) || undefined,
    }
    return Object.values(item).some(Boolean) ? [item] : []
  })
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
    camera: cameraMode === 'overview' || cameraMode === 'focus' || cameraMode === 'detail'
      ? { mode: cameraMode }
      : undefined,
  }
}

export function hasRegionalCoordinates(value: unknown): value is AiRegionalAnalysis {
  return normalizeRegionalAnalysis(value) !== null
}
