import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import React from 'react'
import { BRAND_CATALOG, getBrandIcon } from './brandCatalog'

describe('brandCatalog - Multi-Brand Vector Logo System', () => {
  it('contains at least 1000 high-quality brand vector definitions', () => {
    expect(BRAND_CATALOG.length).toBeGreaterThanOrEqual(1000)
    // Verify each brand has name, keywords, and valid svg component
    for (const brand of BRAND_CATALOG) {
      expect(brand.name).toBeTruthy()
      expect(brand.keywords.length).toBeGreaterThanOrEqual(1)
      expect(typeof brand.svg).toBe('function')
    }
  })

  it('resolves core requested ecosystem brands correctly', () => {
    const requested = [
      { input: ['MSM', 'panel.mauntingstudios.de'], expectedAria: 'Maunting Server Manager (MSM)' },
      { input: ['NBM', 'nbm-modding.de'], expectedAria: 'NBM' },
      { input: ['nbm-js', 'https://nbmjs.org'], expectedAria: 'NBM' },
      { input: ['nbm', ''], expectedAria: 'NBM' },
      { input: ['NBM Package Manager', 'https://nbmjs.org/docs'], expectedAria: 'NBM' },
      { input: ['Otto', 'https://www.otto.de/login'], expectedAria: 'OTTO' },
      { input: ['Amazon', 'https://aws.amazon.com/console'], expectedAria: 'Amazon / AWS' },
      { input: ['Stripe', 'https://dashboard.stripe.com'], expectedAria: 'Stripe' },
      { input: ['Seb-Hosting', 'https://seb-hosting.de'], expectedAria: 'Seb-Hosting' },
      { input: ['Nitrado', 'https://server.nitrado.net'], expectedAria: 'Nitrado' },
      { input: ['Discord', 'https://discord.com/app'], expectedAria: 'Discord' },
      { input: ['Backblaze', 'https://secure.backblaze.com/b2_buckets.htm'], expectedAria: 'Backblaze / B2' },
    ]

    for (const item of requested) {
      const IconComponent = getBrandIcon(item.input[0], item.input[1])
      const { container } = render(React.createElement(IconComponent))
      const svg = container.querySelector('svg')
      expect(svg, `Expected SVG for ${item.input[0]}`).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(item.expectedAria)
    }
  })

  it('resolves voice server providers', () => {
    const voiceServers = [
      { name: 'TeamSpeak', url: 'ts3.voice.de', expected: 'TeamSpeak' },
      { name: 'Mumble', url: 'mumble.info', expected: 'Mumble' },
      { name: 'Skype', url: 'skype.com', expected: 'Skype' },
      { name: 'Signal', url: 'signal.org', expected: 'Signal' },
      { name: 'Viber', url: 'viber.com', expected: 'Viber' },
      { name: 'Matrix', url: 'element.io', expected: 'Matrix / Element' },
    ]

    for (const vs of voiceServers) {
      const IconComponent = getBrandIcon(vs.name, vs.url)
      const { container } = render(React.createElement(IconComponent))
      const svg = container.querySelector('svg')
      expect(svg).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(vs.expected)
    }
  })

  it('resolves national and international cloud & server hosters', () => {
    const hosters = [
      { name: 'Hetzner', url: 'console.hetzner.cloud', expected: 'Hetzner' },
      { name: 'netcup', url: 'customercontrolpanel.de', expected: 'netcup' },
      { name: 'STRATO', url: 'strato.de', expected: 'STRATO' },
      { name: 'IONOS', url: 'login.ionos.de', expected: 'IONOS / 1&1' },
      { name: 'Contabo', url: 'my.contabo.com', expected: 'Contabo' },
      { name: 'ZAP-Hosting', url: 'zap-hosting.com', expected: 'ZAP-Hosting' },
      { name: 'G-Portal', url: 'g-portal.com', expected: 'G-PORTAL' },
      { name: 'Azure', url: 'portal.azure.com', expected: 'Microsoft Azure' },
      { name: 'Google Cloud', url: 'console.cloud.google.com', expected: 'Google Cloud / GCP' },
      { name: 'OVH', url: 'ovhcloud.com', expected: 'OVHcloud' },
      { name: 'Linode', url: 'cloud.linode.com', expected: 'Linode / Akamai' },
      { name: 'Vultr', url: 'my.vultr.com', expected: 'Vultr' },
      { name: 'Scaleway', url: 'console.scaleway.com', expected: 'Scaleway' },
      { name: 'DigitalOcean', url: 'cloud.digitalocean.com', expected: 'DigitalOcean' },
      { name: 'Cloudflare', url: 'dash.cloudflare.com', expected: 'Cloudflare' },
      { name: 'Hostinger', url: 'hpanel.hostinger.com', expected: 'Hostinger' },
      { name: 'GoDaddy', url: 'godaddy.com', expected: 'GoDaddy' },
      { name: 'Namecheap', url: 'namecheap.com', expected: 'Namecheap' },
      { name: 'Bluehost', url: 'my.bluehost.com', expected: 'Bluehost' },
      { name: 'Oracle Cloud', url: 'cloud.oracle.com', expected: 'Oracle Cloud' },
      { name: 'Alibaba Cloud', url: 'alibabacloud.com', expected: 'Alibaba Cloud' },
      { name: 'Fastly', url: 'manage.fastly.com', expected: 'Fastly' },
      { name: 'Fly.io', url: 'fly.io', expected: 'Fly.io' },
      { name: 'Render', url: 'dashboard.render.com', expected: 'Render' },
      { name: 'Railway', url: 'railway.app', expected: 'Railway' },
      { name: 'Heroku', url: 'dashboard.heroku.com', expected: 'Heroku' },
    ]

    for (const h of hosters) {
      const IconComponent = getBrandIcon(h.name, h.url)
      const { container } = render(React.createElement(IconComponent))
      const svg = container.querySelector('svg')
      expect(svg).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(h.expected)
    }
  })

  it('resolves S3 and cloud storage providers', () => {
    const storages = [
      { name: 'Wasabi', url: 'console.wasabisys.com', expected: 'Wasabi Hot Cloud Storage' },
      { name: 'MinIO', url: 'minio.mycluster.com', expected: 'MinIO' },
      { name: 'Storj', url: 'storj.io', expected: 'Storj' },
      { name: 'Nextcloud', url: 'cloud.company.de', expected: 'Nextcloud' },
      { name: 'ownCloud', url: 'owncloud.org', expected: 'ownCloud' },
      { name: 'Box', url: 'app.box.com', expected: 'Box' },
      { name: 'MEGA', url: 'mega.nz', expected: 'MEGA' },
      { name: 'pCloud', url: 'my.pcloud.com', expected: 'pCloud' },
      { name: 'Synology', url: 'quickconnect.to', expected: 'Synology' },
      { name: 'OneDrive', url: 'onedrive.live.com', expected: 'Microsoft OneDrive' },
      { name: 'Dropbox', url: 'dropbox.com', expected: 'Dropbox' },
      { name: 'Google Drive', url: 'drive.google.com', expected: 'Google Drive' },
    ]

    for (const s of storages) {
      const IconComponent = getBrandIcon(s.name, s.url)
      const { container } = render(React.createElement(IconComponent))
      const svg = container.querySelector('svg')
      expect(svg).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(s.expected)
    }
  })

  it('resolves food, beverages and global retail services', () => {
    const brands = [
      { name: 'Coca-Cola', url: 'coca-cola.com', expected: 'Coca-Cola' },
      { name: 'Fanta', url: 'fanta.de', expected: 'Fanta' },
      { name: 'Pepsi', url: 'pepsico.com', expected: 'Pepsi' },
      { name: 'Red Bull', url: 'redbull.com', expected: 'Red Bull' },
      { name: 'McDonalds', url: 'mcdonalds.de', expected: "McDonald's" },
      { name: 'Burger King', url: 'bk.com', expected: 'Burger King' },
      { name: 'Starbucks', url: 'starbucks.com', expected: 'Starbucks' },
      { name: 'Lieferando', url: 'lieferando.de', expected: 'Lieferando / Just Eat' },
      { name: 'MediaMarkt', url: 'mediamarkt.de', expected: 'MediaMarkt' },
      { name: 'Saturn', url: 'saturn.de', expected: 'Saturn' },
      { name: 'Zalando', url: 'zalando.de', expected: 'Zalando' },
      { name: 'AliExpress', url: 'aliexpress.com', expected: 'AliExpress' },
      { name: 'Temu', url: 'temu.com', expected: 'Temu' },
      { name: 'SHEIN', url: 'shein.com', expected: 'SHEIN' },
    ]

    for (const b of brands) {
      const IconComponent = getBrandIcon(b.name, b.url)
      const { container } = render(React.createElement(IconComponent))
      const svg = container.querySelector('svg')
      expect(svg).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(b.expected)
    }
  })

  it('resolves AI, Dev and Banking tools', () => {
    const techAndFinance = [
      { name: 'ChatGPT', url: 'chat.openai.com', expected: 'OpenAI / ChatGPT' },
      { name: 'Claude', url: 'claude.ai', expected: 'Anthropic' },
      { name: 'Perplexity', url: 'perplexity.ai', expected: 'Perplexity AI' },
      { name: 'Cursor', url: 'cursor.com', expected: 'Cursor' },
      { name: 'PayPal', url: 'paypal.com', expected: 'PayPal' },
      { name: 'Revolut', url: 'revolut.com', expected: 'Revolut' },
      { name: 'N26', url: 'n26.com', expected: 'N26' },
      { name: 'Trade Republic', url: 'traderepublic.com', expected: 'Trade Republic' },
      { name: 'Deutsche Bank', url: 'deutsche-bank.de', expected: 'Deutsche Bank' },
      { name: 'Sparkasse', url: 'sparkasse.de', expected: 'Sparkasse' },
    ]

    for (const t of techAndFinance) {
      const IconComponent = getBrandIcon(t.name, t.url)
      const { container } = render(React.createElement(IconComponent))
      const svg = container.querySelector('svg')
      expect(svg).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(t.expected)
    }
  })

  it('renders all catalog brand SVGs without runtime or markup errors', () => {
    for (const brand of BRAND_CATALOG) {
      const { container } = render(React.createElement(brand.svg))
      const svg = container.querySelector('svg')
      expect(svg, `Brand ${brand.name} did not render an SVG`).toBeTruthy()
      expect(svg?.getAttribute('viewBox')).toBe('0 0 24 24')
      expect(svg?.getAttribute('aria-label')).toBe(brand.name)
      const path = svg?.querySelector('path')
      expect(path, `Brand ${brand.name} missing path element`).toBeTruthy()
      expect(path?.getAttribute('d')?.length).toBeGreaterThan(5)
    }
  })

  it('prevents substring and compound-word false-positives across common vocabulary', () => {
    // "Steam" contains the letters "ea", but should NOT resolve to EA Games
    const steamIcon = getBrandIcon('Steam', 'steampowered.com')
    const { container: steamContainer } = render(React.createElement(steamIcon))
    expect(steamContainer.querySelector('svg')?.getAttribute('aria-label')).toBe('Steam')

    // "MongoDB" contains the letters "db", but should NOT resolve to Deutsche Bahn
    const mongoIcon = getBrandIcon('MongoDB Atlas', 'mongodb.com')
    const { container: mongoContainer } = render(React.createElement(mongoIcon))
    expect(mongoContainer.querySelector('svg')?.getAttribute('aria-label')).toBe('MongoDB')

    // Standalone "EA Games" SHOULD resolve to EA
    const eaIcon = getBrandIcon('EA Games', 'ea.com')
    const { container: eaContainer } = render(React.createElement(eaIcon))
    expect(eaContainer.querySelector('svg')?.getAttribute('aria-label')).toBe('EA')

    // "Community Forum" contains "unity", but should NOT resolve to Unity Engine
    const communityIcon = getBrandIcon('Community Forum', 'https://forum.mycommunity.org')
    const { container: commContainer } = render(React.createElement(communityIcon))
    expect(commContainer.querySelector('.lucide-key-round')).toBeTruthy()

    // "Lotto Hessen" contains "otto", but should NOT resolve to OTTO
    const lottoIcon = getBrandIcon('Lotto Hessen', 'https://lotto-hessen.de')
    const { container: lottoContainer } = render(React.createElement(lottoIcon))
    expect(lottoContainer.querySelector('.lucide-key-round')).toBeTruthy()

    // "Huber Spedition" contains "uber", but should NOT resolve to Uber
    const huberIcon = getBrandIcon('Huber Spedition', 'https://huber-transporte.de')
    const { container: huberContainer } = render(React.createElement(huberIcon))
    expect(huberContainer.querySelector('.lucide-key-round')).toBeTruthy()

    // "Auditorium Tickets" contains "audi", but should NOT resolve to Audi
    const auditoriumIcon = getBrandIcon('Auditorium Tickets', 'https://auditorium.de')
    const { container: audiContainer } = render(React.createElement(auditoriumIcon))
    expect(audiContainer.querySelector('.lucide-key-round')).toBeTruthy()

    // "Fantasy Football" contains "fanta", but should NOT resolve to Fanta
    const fantasyIcon = getBrandIcon('Fantasy Football', 'https://fantasy-league.com')
    const { container: fantaContainer } = render(React.createElement(fantasyIcon))
    expect(fantaContainer.querySelector('.lucide-key-round')).toBeTruthy()

    // "Megaport Network" contains "mega", but should NOT resolve to MEGA cloud
    const megaportIcon = getBrandIcon('Megaport Network', 'https://megaport.com')
    const { container: megaContainer } = render(React.createElement(megaportIcon))
    expect(megaContainer.querySelector('.lucide-key-round')).toBeTruthy()

    // "Thunderbolt Display" contains "bolt", but should NOT resolve to Bolt
    const thunderboltIcon = getBrandIcon('Thunderbolt Display', 'https://display.local')
    const { container: boltContainer } = render(React.createElement(thunderboltIcon))
    expect(boltContainer.querySelector('.lucide-key-round')).toBeTruthy()
  })

  it('prevents domain spoofing, subdomain phishing and hyphenated attacker domains', () => {
    const phishingAttempts = [
      { service: 'Fake Bank', url: 'https://fake-paypal.com' },
      { service: 'Scam Site', url: 'https://paypal.com-scam.net' },
      { service: 'Phishing Attack', url: 'https://paypal.com.evil-phishing.com' },
      { service: 'Subdomain Phish', url: 'https://apple.com.attacker.io' },
      { service: 'Google Phish', url: 'https://google.com.attacker.net/login' },
      { service: 'lotto.de', url: '' },
      { service: 'fake-paypal.com', url: '' },
    ]

    for (const pa of phishingAttempts) {
      const Icon = getBrandIcon(pa.service, pa.url)
      const { container } = render(React.createElement(Icon))
      expect(container.querySelector('.lucide-key-round'), `Expected neutral fallback for phishing attempt: ${pa.service} / ${pa.url}`).toBeTruthy()
    }
  })

  it('resolves international domains, multi-part TLDs and direct domain entries', () => {
    const legitimate = [
      { service: 'Amazon UK', url: 'https://www.amazon.co.uk', expectedAria: 'Amazon / AWS' },
      { service: '', url: 'https://www.amazon.co.uk', expectedAria: 'Amazon / AWS' },
      { service: 'Amazon Brazil', url: 'https://www.amazon.com.br', expectedAria: 'Amazon / AWS' },
      { service: 'otto.de', url: '', expectedAria: 'OTTO' },
      { service: 'paypal.com', url: '', expectedAria: 'PayPal' },
      { service: 'discord.com', url: '', expectedAria: 'Discord' },
      { service: 'github.com', url: '', expectedAria: 'GitHub' },
      { service: 'box.com', url: '', expectedAria: 'Box' },
      { service: 'dropbox.com', url: '', expectedAria: 'Dropbox' },
    ]

    for (const leg of legitimate) {
      const Icon = getBrandIcon(leg.service, leg.url)
      const { container } = render(React.createElement(Icon))
      const svg = container.querySelector('svg')
      expect(svg, `Expected SVG for ${leg.service} / ${leg.url}`).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(leg.expectedAria)
    }
  })

  it('resolves newly expanded DevOps, Database, and Fintech brands from the 1000-brand catalog', () => {
    const brands = [
      { name: 'Docker', url: 'https://hub.docker.com', expected: 'Docker' },
      { name: 'Kubernetes', url: 'https://kubernetes.io', expected: 'Kubernetes' },
      { name: 'InfluxDB', url: 'https://influxdata.com', expected: 'InfluxDB' },
      { name: 'Netdata', url: 'https://netdata.cloud', expected: 'Netdata' },
      { name: 'GitLab', url: 'https://gitlab.com', expected: 'GitLab' },
      { name: 'PostgreSQL', url: 'https://postgresql.org', expected: 'PostgreSQL' },
      { name: 'Dogecoin', url: 'https://dogecoin.com', expected: 'Dogecoin' },
    ]

    for (const b of brands) {
      const Icon = getBrandIcon(b.name, b.url)
      const { container } = render(React.createElement(Icon))
      const svg = container.querySelector('svg')
      expect(svg, `Expected SVG for ${b.name}`).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(b.expected)
    }
  })

  it('resolves package managers and language runtimes (NBM-JS, npm, pnpm, bun, yarn, composer, pypi, cargo, python, rust, go, ts, java, c#)', () => {
    const tools = [
      // NBM-JS package manager ecosystem
      { name: 'nbm-js', url: 'https://nbmjs.org', expected: 'NBM' },
      { name: 'NBM Package Manager', url: '', expected: 'NBM' },
      { name: 'nbm paketmanager', url: '', expected: 'NBM' },
      { name: 'nbmjs', url: 'https://nbmjs.org/docs', expected: 'NBM' },
      { name: '', url: 'https://nbmjs.com', expected: 'NBM' },

      // Package managers
      { name: 'npm', url: 'https://www.npmjs.com', expected: 'npm' },
      { name: 'pnpm', url: 'https://pnpm.io', expected: 'pnpm' },
      { name: 'Bun', url: 'https://bun.sh', expected: 'Bun' },
      { name: 'Yarn', url: 'https://yarnpkg.com', expected: 'Yarn' },
      { name: 'Composer', url: 'https://getcomposer.org', expected: 'Composer' },
      { name: 'PyPI', url: 'https://pypi.org', expected: 'PyPI' },
      { name: 'Cargo', url: 'https://crates.io', expected: 'Cargo' },

      // Programming languages
      { name: 'Python', url: 'https://python.org', expected: 'Python' },
      { name: 'Rust', url: 'https://rust-lang.org', expected: 'Rust' },
      { name: 'TypeScript', url: 'https://typescriptlang.org', expected: 'TypeScript' },
      { name: 'Go', url: 'https://golang.org', expected: 'Go' },
      { name: 'Java', url: 'https://java.com', expected: 'Java' },
      { name: 'C#', url: '', expected: 'C#' },
    ]

    for (const t of tools) {
      const Icon = getBrandIcon(t.name, t.url)
      const { container } = render(React.createElement(Icon))
      const svg = container.querySelector('svg')
      expect(svg, `Expected SVG for ${t.name} / ${t.url}`).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(t.expected)
    }
  })

  it('resolves server, networking, and devops tooling (NGINX, Proxmox, Portainer, WireGuard, OpenVPN, Tailscale, ZeroTier, Prometheus, Caddy, Apache, Traefik, HAProxy, Terraform)', () => {
    const devops = [
      { name: 'NGINX', url: 'https://nginx.org', expected: 'NGINX' },
      { name: 'Proxmox VE', url: 'https://proxmox.com', expected: 'Proxmox' },
      { name: 'Portainer', url: 'https://portainer.io', expected: 'Portainer' },
      { name: 'WireGuard', url: 'https://wireguard.com', expected: 'WireGuard' },
      { name: 'OpenVPN', url: 'https://openvpn.net', expected: 'OpenVPN' },
      { name: 'Tailscale', url: 'https://tailscale.com', expected: 'Tailscale' },
      { name: 'ZeroTier', url: 'https://zerotier.com', expected: 'ZeroTier' },
      { name: 'Prometheus', url: 'https://prometheus.io', expected: 'Prometheus' },
      { name: 'Caddy', url: 'https://caddyserver.com', expected: 'Caddy' },
      { name: 'Apache', url: 'https://httpd.apache.org', expected: 'Apache' },
      { name: 'Traefik', url: 'https://traefik.io', expected: 'Traefik' },
      { name: 'HAProxy', url: 'https://haproxy.org', expected: 'HAProxy' },
      { name: 'Terraform', url: 'https://terraform.io', expected: 'Terraform' },
    ]

    for (const d of devops) {
      const Icon = getBrandIcon(d.name, d.url)
      const { container } = render(React.createElement(Icon))
      const svg = container.querySelector('svg')
      expect(svg, `Expected SVG for ${d.name} / ${d.url}`).toBeTruthy()
      expect(svg?.getAttribute('aria-label')).toBe(d.expected)
    }
  })

  it('protects against single-letter phrase false-positives while allowing exact matches', () => {
    // Single-letter words in multi-word phrases should NOT hijack the whole service name
    const serverX = getBrandIcon('Server X', 'https://example.internal')
    const { container: xContainer } = render(React.createElement(serverX))
    expect(xContainer.querySelector('.lucide-key-round')).toBeTruthy()

    const nodeX = getBrandIcon('Node X', '')
    const { container: nodeContainer } = render(React.createElement(nodeX))
    expect(nodeContainer.querySelector('.lucide-key-round')).toBeTruthy()
  })

  it('verifies that core brands retain authentic brand colors rather than generic currentColor', () => {
    const coloredBrands = [
      { name: 'NBM', url: 'https://nbmjs.org', expectedColor: '#0284C7' },
      { name: 'NGINX', url: 'https://nginx.org', expectedColor: '#009639' },
      { name: 'Proxmox VE', url: 'https://proxmox.com', expectedColor: '#E57000' },
      { name: 'InfluxDB', url: 'https://influxdata.com', expectedColor: '#22ADF6' },
      { name: 'Traefik', url: 'https://traefik.io', expectedColor: '#24A1C1' },
      { name: 'HAProxy', url: 'https://haproxy.org', expectedColor: '#106DA7' },
      { name: 'Python', url: 'https://python.org', expectedColor: '#3776AB' },
    ]

    for (const cb of coloredBrands) {
      const Icon = getBrandIcon(cb.name, cb.url)
      const { container } = render(React.createElement(Icon))
      const svg = container.querySelector('svg')
      expect(svg, `Expected SVG for ${cb.name}`).toBeTruthy()
      expect(svg?.getAttribute('fill')).toBe(cb.expectedColor)
    }
  })

  it('falls back gracefully to neutral KeyRound for unknown services', () => {
    const unknownIcon = getBrandIcon('SomeCompletelyUnknownServer12345', 'https://mystery-server.example')
    const { container } = render(React.createElement(unknownIcon))
    // No svg with aria-label; neutral container with lucide-key-round
    const svg = container.querySelector('svg')
    expect(svg?.getAttribute('aria-label')).toBeFalsy()
    expect(container.querySelector('.lucide-key-round')).toBeTruthy()
  })
})

