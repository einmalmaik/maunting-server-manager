import { describe, expect, it } from 'vitest'
import { sicheresZiel } from './sicheresZiel'

describe('sicheresZiel', () => {
  it('lässt Pfade dieses Panels durch', () => {
    expect(sicheresZiel('/chat')).toBe('/chat')
    expect(sicheresZiel('/servers/3?tab=konsole#unten')).toBe('/servers/3?tab=konsole#unten')
  })

  it('schickt nie auf eine fremde Seite', () => {
    for (const roh of [
      '//fremd.example',
      '/\\fremd.example',
      '/\\/fremd.example',
      '/\t/fremd.example',
      '/\n/fremd.example',
      'https://fremd.example',
      'javascript:alert(1)',
      'fremd.example',
    ]) {
      expect(sicheresZiel(roh), roh).toBe('/')
    }
  })

  it('nimmt ohne Ziel die Startseite', () => {
    expect(sicheresZiel(null)).toBe('/')
    expect(sicheresZiel('')).toBe('/')
  })
})
