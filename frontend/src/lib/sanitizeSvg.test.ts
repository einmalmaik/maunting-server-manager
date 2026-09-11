import { describe, it, expect } from 'vitest'
import { sanitizeSvg, getSafeAttachmentUrl } from './sanitizeSvg'

describe('sanitizeSvg', () => {
  it('allows safe, well-formed SVGs', () => {
    const safeSvg = '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="40" fill="red"/></svg>'
    const sanitized = sanitizeSvg(safeSvg)
    expect(sanitized).toContain('<circle')
    expect(sanitized).toContain('fill="red"')
  })

  it('removes <script> tags and payloads', () => {
    const malicious = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><rect width="10" height="10"/></svg>'
    const sanitized = sanitizeSvg(malicious)
    expect(sanitized).not.toContain('<script')
    expect(sanitized).not.toContain('alert(1)')
    expect(sanitized).toContain('<rect')
  })

  it('removes inline event handlers like onload, onerror, onclick', () => {
    const malicious = '<svg onload="alert(\'xss\')" xmlns="http://www.w3.org/2000/svg"><image href="x" onerror="alert(2)"/><circle onclick="alert(3)"/></svg>'
    const sanitized = sanitizeSvg(malicious)
    expect(sanitized).not.toContain('onload')
    expect(sanitized).not.toContain('onerror')
    expect(sanitized).not.toContain('onclick')
    expect(sanitized).not.toContain('alert')
  })

  it('removes javascript: and dangerous URI schemes in attributes', () => {
    const malicious = '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><text>Click</text></a></svg>'
    const sanitized = sanitizeSvg(malicious)
    expect(sanitized).not.toContain('javascript:')
  })

  it('rejects / neutralizes XXE and DTD entities', () => {
    const malicious = '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
    const sanitized = sanitizeSvg(malicious)
    expect(sanitized).toBe('')
  })

  it('removes dangerous elements like foreignObject, iframe, embed', () => {
    const malicious = '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject width="100" height="100"><iframe src="https://evil.com"></iframe></foreignObject><circle cx="5" cy="5" r="2"/></svg>'
    const sanitized = sanitizeSvg(malicious)
    expect(sanitized).not.toContain('<foreignObject')
    expect(sanitized).not.toContain('<iframe')
    expect(sanitized).toContain('<circle')
  })

  it('removes <set> and <animate> elements that could inject scripts or modify attributes', () => {
    const malicious = '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="4"/><set attributeName="onmouseover" to="alert(1)"/><animate attributeName="href" values="javascript:alert(1)"/></svg>'
    const sanitized = sanitizeSvg(malicious)
    expect(sanitized).not.toContain('<set')
    expect(sanitized).not.toContain('<animate')
    expect(sanitized).not.toContain('alert(1)')
    expect(sanitized).toContain('<circle')
  })

  it('neutralizes whitespace and carriage-return obfuscated URIs and non-image data URIs', () => {
    const malicious = '<svg xmlns="http://www.w3.org/2000/svg"><a href="java\r\nscript:alert(1)"><image href="data:text/html,<script>alert(2)</script>"/><text>XSS</text></a></svg>'
    const sanitized = sanitizeSvg(malicious)
    expect(sanitized).not.toContain('javascript:')
    expect(sanitized).not.toContain('data:text/html')
    expect(sanitized).not.toContain('alert')
  })

  it('removes dangerous <style> tags and obfuscated CSS scripts or imports', () => {
    const evilStyle = '<svg xmlns="http://www.w3.org/2000/svg"><style>circle { background: url("jav/*foo*/ascript:alert(1)"); }</style><circle cx="5" cy="5" r="4"/></svg>'
    const sanitized = sanitizeSvg(evilStyle)
    expect(sanitized).not.toContain('javascript:')
    expect(sanitized).not.toContain('alert(1)')
    expect(sanitized).toContain('<circle')

    const evilImport = '<svg xmlns="http://www.w3.org/2000/svg"><style>@/*x*/import url("https://evil.com/xss.css");</style><rect width="10" height="10"/></svg>'
    const sanitizedImport = sanitizeSvg(evilImport)
    expect(sanitizedImport).not.toContain('@import')
    expect(sanitizedImport).toContain('<rect')
  })

  it('removes <use> elements that can exploit external resources', () => {
    const maliciousUse = '<svg xmlns="http://www.w3.org/2000/svg"><use href="https://evil.com/exploit.svg#icon"/><circle cx="5" cy="5" r="4"/></svg>'
    const sanitized = sanitizeSvg(maliciousUse)
    expect(sanitized).not.toContain('<use')
    expect(sanitized).toContain('<circle')
  })

  it('handles empty and malformed input gracefully', () => {
    expect(sanitizeSvg('')).toBe('')
    // @ts-expect-error test invalid type
    expect(sanitizeSvg(null)).toBe('')
  })
})

describe('getSafeAttachmentUrl', () => {
  it('returns null for empty, null, or undefined URLs', () => {
    expect(getSafeAttachmentUrl(null)).toBeNull()
    expect(getSafeAttachmentUrl(undefined)).toBeNull()
    expect(getSafeAttachmentUrl('')).toBeNull()
    expect(getSafeAttachmentUrl('   ')).toBeNull()
  })

  it('allows safe data URLs for images, audio, video, applications, and plain/csv text', () => {
    expect(getSafeAttachmentUrl('data:image/png;base64,iVBORw0KGgo=')).toBe('data:image/png;base64,iVBORw0KGgo=')
    expect(getSafeAttachmentUrl('data:image/jpeg;base64,/9j/4AAQSkZJRg==')).toBe('data:image/jpeg;base64,/9j/4AAQSkZJRg==')
    expect(getSafeAttachmentUrl('data:application/pdf;base64,JVBERi0xLjQK')).toBe('data:application/pdf;base64,JVBERi0xLjQK')
    expect(getSafeAttachmentUrl('data:text/plain;base64,SGFsbG8=')).toBe('data:text/plain;base64,SGFsbG8=')
    expect(getSafeAttachmentUrl('data:text/csv;base64,YSwx')).toBe('data:text/csv;base64,YSwx')
    expect(getSafeAttachmentUrl('data:audio/mp3;base64,SUQzBA==')).toBe('data:audio/mp3;base64,SUQzBA==')
    expect(getSafeAttachmentUrl('data:video/mp4;base64,AAAA')).toBe('data:video/mp4;base64,AAAA')
  })

  it('allows data URIs with missing MIME or empty MIME (e.g. data:;base64, or data:base64,)', () => {
    expect(getSafeAttachmentUrl('data:;base64,SGVsbG8=')).toBe('data:;base64,SGVsbG8=')
    expect(getSafeAttachmentUrl('data:base64,SGVsbG8=')).toBe('data:base64,SGVsbG8=')
  })

  it('allows blob, api, and https URLs', () => {
    expect(getSafeAttachmentUrl('blob:http://localhost:5173/uuid-123')).toBe('blob:http://localhost:5173/uuid-123')
    expect(getSafeAttachmentUrl('/api/social/media/media-1/download')).toBe('/api/social/media/media-1/download')
    expect(getSafeAttachmentUrl('https://example.com/file.pdf')).toBe('https://example.com/file.pdf')
    expect(getSafeAttachmentUrl('http://example.com/file.pdf')).toBe('http://example.com/file.pdf')
  })

  it('blocks dangerous executable, script, and HTML URL schemes', () => {
    expect(getSafeAttachmentUrl('javascript:alert(1)')).toBeNull()
    expect(getSafeAttachmentUrl('  java\tscript:alert(1)')).toBeNull()
    expect(getSafeAttachmentUrl('vbscript:msgbox(1)')).toBeNull()
    expect(getSafeAttachmentUrl('data:text/html,<script>alert(1)</script>')).toBeNull()
    expect(getSafeAttachmentUrl('data:text/javascript,alert(1)')).toBeNull()
    expect(getSafeAttachmentUrl('data:application/javascript,alert(1)')).toBeNull()
    expect(getSafeAttachmentUrl('data:application/xhtml+xml,<html/>')).toBeNull()
    expect(getSafeAttachmentUrl('data:text/xml,<xml/>')).toBeNull()
  })

  it('blocks unknown / unsupported URI schemes', () => {
    expect(getSafeAttachmentUrl('ftp://example.com/file.txt')).toBeNull()
    expect(getSafeAttachmentUrl('file:///etc/passwd')).toBeNull()
    expect(getSafeAttachmentUrl('chrome-extension://xyz')).toBeNull()
  })
})

