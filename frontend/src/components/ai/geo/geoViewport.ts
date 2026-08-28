import type { AiRegionalAnalysis } from '@/api/ai'

/**
 * Sentinel sucht mit dieser WGS84-Bounding-Box. Die Kartenansicht verwendet
 * exakt deren Mittelpunkt, damit Bild und Sentinel-Szene nicht auseinanderlaufen.
 */
export function sentinelViewportCenter(coordinates: AiRegionalAnalysis['coordinates']) {
  const [minLon, minLat, maxLon, maxLat] = coordinates.bbox
  return {
    centerLatitude: (minLat + maxLat) / 2,
    centerLongitude: (minLon + maxLon) / 2,
  }
}
