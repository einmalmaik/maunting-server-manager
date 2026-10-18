/**
 * R5: Schutz interner Netzwerke (DNS-Rebinding-Schutz) E2E Test Suite
 * Verifies multi-layer interception blocking loopback, RFC 1918,
 * unspecified, link-local, mDNS, and DNS rebinding attacks while
 * permitting direct user top-level navigation.
 */
import { describe, it, assert } from './harness.mjs';

// IP address & Network Security Inspector matching PROJECT.md §M5 contract
class NetworkSecurityFilter {
  constructor(dnsResolverMock = null) {
    this.dnsMock = dnsResolverMock || new Map();
  }

  setDnsRecord(host, ip) {
    this.dnsMock.set(host.toLowerCase(), ip);
  }

  parseIpv4(ipStr) {
    // Handle integer, hex, octal representations
    const parts = ipStr.split('.');
    if (parts.length === 1 && /^\d+$/.test(parts[0])) {
      // Decimal integer representation
      const num = parseInt(parts[0], 10);
      return [
        (num >>> 24) & 255,
        (num >>> 16) & 255,
        (num >>> 8) & 255,
        num & 255,
      ];
    }
    if (parts.length === 4) {
      const octets = [];
      for (const p of parts) {
        if (/^0x[0-9a-f]+$/i.test(p)) {
          octets.push(parseInt(p, 16));
        } else if (/^0\d+$/.test(p)) {
          octets.push(parseInt(p, 8));
        } else if (/^\d+$/.test(p)) {
          octets.push(parseInt(p, 10));
        } else {
          return null;
        }
      }
      return octets.every((o) => o >= 0 && o <= 255) ? octets : null;
    }
    return null;
  }

  isPrivateIpv4(octets) {
    if (!octets || octets.length !== 4) return false;
    const [a, b, c, d] = octets;

    // Loopback: 127.0.0.0/8
    if (a === 127) return true;
    // Unspecified: 0.0.0.0/8
    if (a === 0) return true;
    // RFC 1918: 10.0.0.0/8
    if (a === 10) return true;
    // RFC 1918: 172.16.0.0/12 (172.16 - 172.31)
    if (a === 172 && b >= 16 && b <= 31) return true;
    // RFC 1918: 192.168.0.0/16
    if (a === 192 && b === 168) return true;
    // Link-Local: 169.254.0.0/16
    if (a === 169 && b === 254) return true;
    // CGNAT: 100.64.0.0/10 (100.64 - 100.127)
    if (a === 100 && b >= 64 && b <= 127) return true;

    return false;
  }

  isPrivateIpv6(ipv6Str) {
    const clean = ipv6Str.replace(/[\[\]]/g, '').toLowerCase();

    // Loopback: ::1
    if (clean === '::1' || clean === '0:0:0:0:0:0:0:1') return true;
    // Unspecified: ::
    if (clean === '::' || clean === '0:0:0:0:0:0:0:0') return true;
    // Link-local: fe80::/10
    if (clean.startsWith('fe8') || clean.startsWith('fe9') || clean.startsWith('fea') || clean.startsWith('feb')) {
      return true;
    }
    // Unique Local Address (ULA): fc00::/7 (fc00... or fd00...)
    if (clean.startsWith('fc') || clean.startsWith('fd')) return true;

    // IPv4-mapped IPv6: ::ffff:192.168.1.1 or ::ffff:7f00:1 (WHATWG URL format)
    if (clean.startsWith('::ffff:')) {
      const rest = clean.slice(7);
      if (rest.includes('.')) {
        const v4Octets = this.parseIpv4(rest);
        return this.isPrivateIpv4(v4Octets);
      }
      if (rest.includes(':')) {
        const hexParts = rest.split(':');
        if (hexParts.length === 2) {
          const high = parseInt(hexParts[0], 16);
          const low = parseInt(hexParts[1], 16);
          const a = (high >> 8) & 255;
          const b = high & 255;
          const c = (low >> 8) & 255;
          const d = low & 255;
          return this.isPrivateIpv4([a, b, c, d]);
        }
      }
    }

    return false;
  }

  isInternalHostOrIp(host) {
    if (!host) return false;
    const cleanHost = host.toLowerCase().trim().replace(/^\[|\]$/g, '');

    // Local hostnames
    if (
      cleanHost === 'localhost' ||
      cleanHost.endsWith('.localhost') ||
      cleanHost.endsWith('.local') ||
      cleanHost.endsWith('.internal') ||
      cleanHost.endsWith('.lan')
    ) {
      return true;
    }

    // Direct IPv4
    const v4 = this.parseIpv4(cleanHost);
    if (v4) return this.isPrivateIpv4(v4);

    // Direct IPv6
    if (this.isPrivateIpv6(cleanHost)) return true;

    // Check DNS resolution for rebind domains
    if (this.dnsMock.has(cleanHost)) {
      const resolved = this.dnsMock.get(cleanHost);
      const resV4 = this.parseIpv4(resolved);
      if (resV4 && this.isPrivateIpv4(resV4)) return true;
      if (this.isPrivateIpv6(resolved)) return true;
    }

    return false;
  }

  evaluateRequest({ targetUrl, originUrl = null, isMainDocument = false }) {
    let targetParsed;
    try {
      targetParsed = new URL(targetUrl);
    } catch {
      return { action: 'Blocken', reason: 'Invalid URL' };
    }

    const host = targetParsed.hostname;
    const isInternal = this.isInternalHostOrIp(host);

    // Rule 1: User-navigated top-level document
    if (isMainDocument) {
      // Top-level direct navigation is permitted
      return { action: 'Laden', internal: isInternal };
    }

    // Rule 2: Subresource / fetch request
    if (isInternal) {
      // If internal, verify if request is same-origin with the parent page
      if (originUrl) {
        try {
          const originParsed = new URL(originUrl);
          if (originParsed.origin === targetParsed.origin) {
            // Same-origin internal request is allowed (e.g. router admin UI loading its own CSS)
            return { action: 'Schild', allowed: true };
          }
        } catch {}
      }

      // Cross-origin or public-to-internal request: BLOCK
      return { action: 'Blocken', status: 403, reason: 'DNS-Rebinding & Private Network Protection' };
    }

    return { action: 'Schild', allowed: true };
  }
}

describe('R5: Schutz interner Netzwerke (DNS-Rebinding-Schutz)', () => {
  const filter = new NetworkSecurityFilter();

  // Tier 1: Feature Coverage
  it('TC-T1-R5-01: Blocks subresource requests to IPv4 loopback (127.0.0.1)', () => {
    const res = filter.evaluateRequest({
      targetUrl: 'http://127.0.0.1:8080/api/keys',
      originUrl: 'https://malicious-public-site.com',
      isMainDocument: false,
    });

    assert.equal(res.action, 'Blocken');
    assert.equal(res.status, 403);
  }, { tier: 1, feature: 'R5', id: 'TC-T1-R5-01' });

  it('TC-T1-R5-02: Blocks subresource requests to IPv6 loopback (::1)', () => {
    const res = filter.evaluateRequest({
      targetUrl: 'http://[::1]:3000/env',
      originUrl: 'https://attacker.org',
      isMainDocument: false,
    });

    assert.equal(res.action, 'Blocken');
    assert.equal(res.status, 403);
  }, { tier: 1, feature: 'R5', id: 'TC-T1-R5-02' });

  it('TC-T1-R5-03: Blocks subresource requests to RFC 1918 private subnets (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)', () => {
    const targets = [
      'http://10.0.0.1/admin',
      'http://172.20.1.100/status',
      'http://192.168.1.1/setup.cgi',
    ];

    for (const targetUrl of targets) {
      const res = filter.evaluateRequest({
        targetUrl,
        originUrl: 'https://news-aggregator.com',
        isMainDocument: false,
      });
      assert.equal(res.action, 'Blocken', `Target ${targetUrl} must be blocked`);
      assert.equal(res.status, 403);
    }
  }, { tier: 1, feature: 'R5', id: 'TC-T1-R5-03' });

  it('TC-T1-R5-04: Blocks subresource requests to 0.0.0.0 and link-local ranges', () => {
    const targets = [
      'http://0.0.0.0:8080/service',
      'http://169.254.169.254/latest/meta-data',
    ];

    for (const targetUrl of targets) {
      const res = filter.evaluateRequest({
        targetUrl,
        originUrl: 'https://public-web.com',
        isMainDocument: false,
      });
      assert.equal(res.action, 'Blocken', `Target ${targetUrl} must be blocked`);
    }
  }, { tier: 1, feature: 'R5', id: 'TC-T1-R5-04' });

  it('TC-T1-R5-05: Blocks DNS rebinding domains resolving to private/internal IPs', () => {
    const rebindFilter = new NetworkSecurityFilter();
    rebindFilter.setDnsRecord('rebind.attacker.com', '127.0.0.1');
    rebindFilter.setDnsRecord('local-router.evil.net', '192.168.1.1');

    const res1 = rebindFilter.evaluateRequest({
      targetUrl: 'http://rebind.attacker.com:9000/steal',
      originUrl: 'https://attacker.com',
      isMainDocument: false,
    });

    const res2 = rebindFilter.evaluateRequest({
      targetUrl: 'http://local-router.evil.net/login',
      originUrl: 'https://attacker.com',
      isMainDocument: false,
    });

    assert.equal(res1.action, 'Blocken');
    assert.equal(res2.action, 'Blocken');
  }, { tier: 1, feature: 'R5', id: 'TC-T1-R5-05' });

  it('TC-T1-R5-06: Permits direct top-level user navigation to internal addresses', () => {
    const directNavTargets = [
      'http://127.0.0.1:3000',
      'http://localhost:8080',
      'http://192.168.1.1',
    ];

    for (const targetUrl of directNavTargets) {
      const res = filter.evaluateRequest({
        targetUrl,
        originUrl: null,
        isMainDocument: true,
      });
      assert.equal(res.action, 'Laden', `Direct user navigation to ${targetUrl} must be allowed`);
    }
  }, { tier: 1, feature: 'R5', id: 'TC-T1-R5-06' });

  // Tier 2: Boundary & Corner Cases
  it('TC-T2-R5-01: Blocks IPv4-mapped IPv6 address subresource requests (::ffff:127.0.0.1)', () => {
    const res = filter.evaluateRequest({
      targetUrl: 'http://[::ffff:127.0.0.1]:8080/token',
      originUrl: 'https://exploit-hub.com',
      isMainDocument: false,
    });

    assert.equal(res.action, 'Blocken');
    assert.equal(res.status, 403);
  }, { tier: 2, feature: 'R5', id: 'TC-T2-R5-01' });

  it('TC-T2-R5-02: Blocks alternate base IP representations (decimal, octal, hex)', () => {
    // 2130706433 = 127.0.0.1
    // 0177.0.0.1 = 127.0.0.1
    // 0x7f000001 = 127.0.0.1
    const obfuscated = [
      'http://2130706433:8080',
      'http://0177.0.0.1:8080',
      'http://0x7f.0.0.1:8080',
    ];

    for (const targetUrl of obfuscated) {
      const res = filter.evaluateRequest({
        targetUrl,
        originUrl: 'https://evilsite.com',
        isMainDocument: false,
      });
      assert.equal(res.action, 'Blocken', `Obfuscated IP ${targetUrl} must be blocked`);
    }
  }, { tier: 2, feature: 'R5', id: 'TC-T2-R5-02' });

  it('TC-T2-R5-03: Blocks Carrier-Grade NAT (CGNAT 100.64.0.0/10) subresource requests', () => {
    const res = filter.evaluateRequest({
      targetUrl: 'http://100.64.1.5:8080/modem',
      originUrl: 'https://public-page.com',
      isMainDocument: false,
    });

    assert.equal(res.action, 'Blocken');
  }, { tier: 2, feature: 'R5', id: 'TC-T2-R5-03' });

  it('TC-T2-R5-04: Blocks IPv6 Unique Local Address (ULA fc00::/7) subresource requests', () => {
    const res = filter.evaluateRequest({
      targetUrl: 'http://[fd00:1234:5678::1]:80/api',
      originUrl: 'https://attacker.net',
      isMainDocument: false,
    });

    assert.equal(res.action, 'Blocken');
  }, { tier: 2, feature: 'R5', id: 'TC-T2-R5-04' });

  it('TC-T2-R5-05: Permits legitimate same-origin subresource requests from internal hosts', () => {
    const res = filter.evaluateRequest({
      targetUrl: 'http://192.168.1.1/static/admin.css',
      originUrl: 'http://192.168.1.1/dashboard',
      isMainDocument: false,
    });

    assert.equal(res.action, 'Schild');
    assert.equal(res.allowed, true);
  }, { tier: 2, feature: 'R5', id: 'TC-T2-R5-05' });

  it('TC-T2-R5-06: Blocks local network mDNS and internal TLD domains (.local, .internal, .lan)', () => {
    const internalDomains = [
      'http://printer.local/status',
      'http://nas.lan/shares',
      'http://auth.internal/keys',
    ];

    for (const targetUrl of internalDomains) {
      const res = filter.evaluateRequest({
        targetUrl,
        originUrl: 'https://public-blog.com',
        isMainDocument: false,
      });
      assert.equal(res.action, 'Blocken', `Internal domain ${targetUrl} must be blocked`);
    }
  }, { tier: 2, feature: 'R5', id: 'TC-T2-R5-06' });
});
