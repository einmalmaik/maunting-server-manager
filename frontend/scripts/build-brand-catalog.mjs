import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const brandsDir = path.resolve(__dirname, '../public/icons/brands')
const catalogPath = path.resolve(__dirname, '../src/desktop/vault/brandCatalog.tsx')
const cachePath = path.resolve(__dirname, 'simple-icons-cache.json')

if (!fs.existsSync(brandsDir)) {
  fs.mkdirSync(brandsDir, { recursive: true })
}

// 1. KNOWN METADATA for core existing brand SVGs
const BRAND_META = {
  '1password': { color: '#0A85EA', keywords: ['1password', '1password.com', 'agilebits'] },
  airbnb: { color: '#FF5A5F', keywords: ['airbnb', 'airbnb.com', 'airbnb.de'] },
  americanexpress: { color: '#002663', keywords: ['american express', 'americanexpress', 'amex', 'americanexpress.com'] },
  anthropic: { color: '#D97757', keywords: ['anthropic', 'claude', 'claude.ai', 'anthropic.com'] },
  apple: { color: 'currentColor', keywords: ['apple', 'icloud', 'appleid', 'itunes', 'apple.com', 'me.com', 'mac.com'] },
  asana: { color: '#F06A6A', keywords: ['asana', 'asana.com'] },
  audi: { color: '#BB0A30', keywords: ['audi', 'audi.de', 'myaudi'] },
  audible: { color: '#F8991C', keywords: ['audible', 'audible.de', 'audible.com'] },
  auth0: { color: '#EB5424', keywords: ['auth0', 'auth0.com'] },
  autocad: { color: '#E51937', keywords: ['autocad', 'autodesk', 'autodesk.com'] },
  bankofamerica: { color: '#012169', keywords: ['bank of america', 'bankofamerica', 'bofa'] },
  battledotnet: { color: '#148EFF', keywords: ['battle.net', 'battlenet', 'blizzard', 'blizzard.com'] },
  binance: { color: '#F3BA2F', keywords: ['binance', 'binance.com'] },
  bitbucket: { color: '#0052CC', keywords: ['bitbucket', 'bitbucket.org'] },
  bitwarden: { color: '#175DDC', keywords: ['bitwarden', 'bitwarden.com', 'vault.bitwarden.com'] },
  blender: { color: '#E87D0D', keywords: ['blender', 'blender.org'] },
  bmw: { color: '#0066B1', keywords: ['bmw', 'bmw.de', 'connecteddrive'] },
  bookingdotcom: { color: '#003580', keywords: ['booking', 'booking.com'] },
  burgerking: { color: '#D62300', keywords: ['burger king', 'burgerking', 'bk.com'] },
  calendly: { color: '#006BFF', keywords: ['calendly', 'calendly.com'] },
  chase: { color: '#117ACA', keywords: ['chase', 'chase.com', 'jpmorgan'] },
  cloudflare: { color: '#F38020', keywords: ['cloudflare', 'cloudflare.com', 'cloudflare r2'] },
  cloudinary: { color: '#3448C5', keywords: ['cloudinary', 'cloudinary.com'] },
  confluence: { color: '#172B4D', keywords: ['confluence', 'confluence.atlassian.net'] },
  coursera: { color: '#0056D2', keywords: ['coursera', 'coursera.org'] },
  deepl: { color: '#0F2B46', keywords: ['deepl', 'deepl.com'] },
  digitalocean: { color: '#0080FF', keywords: ['digitalocean', 'digitalocean.com', 'do.co', 'spaces'] },
  discord: { color: '#5865F2', keywords: ['discord', 'discordapp', 'discord.com', 'discord.gg'] },
  docker: { color: '#2496ED', keywords: ['docker', 'docker.com', 'hub.docker.com'] },
  dropbox: { color: '#0061FF', keywords: ['dropbox', 'dropbox.com'] },
  duolingo: { color: '#58CC02', keywords: ['duolingo', 'duolingo.com'] },
  ea: { color: '#FF4747', keywords: ['ea', 'electronic arts', 'ea.com', 'origin'] },
  ebay: { color: '#E53238', keywords: ['ebay', 'ebay.de', 'ebay.com'] },
  epicgames: { color: '#313131', keywords: ['epic games', 'epicgames', 'epicgames.com', 'unreal'] },
  etsy: { color: '#F16521', keywords: ['etsy', 'etsy.com'] },
  facebook: { color: '#1877F2', keywords: ['facebook', 'fb.com', 'facebook.com', 'meta'] },
  figma: { color: '#F24E1E', keywords: ['figma', 'figma.com'] },
  firebase: { color: '#FFCA28', keywords: ['firebase', 'firebase.google.com'] },
  github: { color: 'currentColor', keywords: ['github', 'gh', 'github.com', 'gist.github.com'] },
  gitlab: { color: '#FC6D26', keywords: ['gitlab', 'gitlab.com'] },
  gmail: { color: '#EA4335', keywords: ['gmail', 'google mail', 'googlemail', 'gmail.com', 'mail.google.com'] },
  godotengine: { color: '#478CBF', keywords: ['godot', 'godotengine', 'godotengine.org'] },
  gogdotcom: { color: '#86328A', keywords: ['gog', 'gog.com', 'gog galaxy'] },
  goodreads: { color: '#75420E', keywords: ['goodreads', 'goodreads.com'] },
  google: { color: '#4285F4', keywords: ['google', 'alphabet', 'google.com', 'google.de'] },
  googledrive: { color: '#4285F4', keywords: ['google drive', 'googledrive', 'drive.google.com'] },
  googlemaps: { color: '#4285F4', keywords: ['google maps', 'maps.google.com'] },
  hbo: { color: '#000000', keywords: ['hbo', 'hbo.com'] },
  hbomax: { color: '#9900FF', keywords: ['hbomax', 'max.com', 'hbo max'] },
  hubspot: { color: '#FF7A59', keywords: ['hubspot', 'hubspot.com'] },
  insomnia: { color: '#5849BE', keywords: ['insomnia', 'insomnia.rest'] },
  instagram: { color: '#E4405F', keywords: ['instagram', 'instagram.com', 'instagr.am'] },
  intellijidea: { color: '#000000', keywords: ['intellij', 'intellij idea'] },
  intercom: { color: '#000000', keywords: ['intercom', 'intercom.com'] },
  itchdotio: { color: '#FA5C5C', keywords: ['itch.io', 'itchio'] },
  jetbrains: { color: '#000000', keywords: ['jetbrains', 'jetbrains.com'] },
  jira: { color: '#0052CC', keywords: ['jira', 'jira.atlassian.net'] },
  klarna: { color: '#FFB3C7', keywords: ['klarna', 'klarna.com', 'klarna.de'] },
  kubernetes: { color: '#326CE5', keywords: ['kubernetes', 'k8s', 'kubernetes.io'] },
  lastpass: { color: '#D32D27', keywords: ['lastpass', 'lastpass.com'] },
  leagueoflegends: { color: '#C79A3B', keywords: ['league of legends', 'leagueoflegends', 'lol', 'riot'] },
  linear: { color: '#5E6AD2', keywords: ['linear', 'linear.app'] },
  linux: { color: '#FCC624', keywords: ['linux', 'kernel.org'] },
  mailchimp: { color: '#FFE01B', keywords: ['mailchimp', 'mailchimp.com'] },
  mastercard: { color: '#EB001B', keywords: ['mastercard', 'mastercard.com'] },
  mcdonalds: { color: '#FFBC0D', keywords: ["mcdonald's", 'mcdonalds', 'mcdonalds.de', 'mcd.com'] },
  mongodb: { color: '#47A248', keywords: ['mongodb', 'mongodb.com', 'atlas'] },
  netflix: { color: '#E50914', keywords: ['netflix', 'netflix.com'] },
  netlify: { color: '#00C7B7', keywords: ['netlify', 'netlify.com', 'netlify.app'] },
  notepadplusplus: { color: '#90E59A', keywords: ['notepad++', 'notepadplusplus', 'notepad-plus-plus.org'] },
  notion: { color: '#000000', keywords: ['notion', 'notion.so'] },
  obsidian: { color: '#7A3EE8', keywords: ['obsidian', 'obsidian.md'] },
  obsstudio: { color: '#302E31', keywords: ['obs', 'obsstudio', 'obsproject.com'] },
  paypal: { color: '#00457C', keywords: ['paypal', 'paypal.com', 'paypal.me'] },
  playstation: { color: '#003791', keywords: ['playstation', 'psn', 'sony playstation', 'playstation.com'] },
  postgresql: { color: '#4169E1', keywords: ['postgresql', 'postgres', 'postgresql.org'] },
  postman: { color: '#FF6C37', keywords: ['postman', 'postman.com'] },
  protondrive: { color: '#6D4AFF', keywords: ['proton drive', 'protondrive', 'drive.proton.me'] },
  protonmail: { color: '#6D4AFF', keywords: ['proton', 'protonmail', 'protonvpn', 'proton.me', 'protonmail.com'] },
  pycharm: { color: '#000000', keywords: ['pycharm'] },
  reddit: { color: '#FF4500', keywords: ['reddit', 'reddit.com', 'redd.it'] },
  revolut: { color: '#000000', keywords: ['revolut', 'revolut.com'] },
  riotgames: { color: '#D32936', keywords: ['riot games', 'riotgames', 'riotgames.com'] },
  roblox: { color: '#000000', keywords: ['roblox', 'roblox.com'] },
  rockstargames: { color: '#FCB914', keywords: ['rockstar games', 'rockstargames', 'socialclub', 'rockstargames.com'] },
  shopify: { color: '#7AB55C', keywords: ['shopify', 'shopify.com', 'myshopify.com'] },
  shopware: { color: '#189EFF', keywords: ['shopware', 'shopware.com'] },
  snapchat: { color: '#FFFC00', keywords: ['snapchat', 'snapchat.com', 'snap.com'] },
  spotify: { color: '#1ED760', keywords: ['spotify', 'spotify.com'] },
  steam: { color: '#171a21', keywords: ['steam', 'valvesoftware', 'steampowered.com'] },
  stripe: { color: '#635BFF', keywords: ['stripe', 'stripe.com'] },
  supabase: { color: '#3ECF8E', keywords: ['supabase', 'supabase.com', 'supabase.io'] },
  telegram: { color: '#26A5E4', keywords: ['telegram', 't.me', 'telegram.org'] },
  tesla: { color: '#E82127', keywords: ['tesla', 'tesla.com'] },
  tiktok: { color: '#000000', keywords: ['tiktok', 'tiktok.com'] },
  tinder: { color: '#FE3C72', keywords: ['tinder', 'tinder.com'] },
  todoist: { color: '#E44332', keywords: ['todoist', 'todoist.com'] },
  toyota: { color: '#EB0A1E', keywords: ['toyota', 'toyota.de', 'toyota.com'] },
  trello: { color: '#0052CC', keywords: ['trello', 'trello.com'] },
  twitch: { color: '#9146FF', keywords: ['twitch', 'twitch.tv'] },
  uber: { color: '#000000', keywords: ['uber', 'uber.com', 'ubereats'] },
  ubisoft: { color: '#000000', keywords: ['ubisoft', 'ubisoft connect', 'uplay', 'ubisoft.com'] },
  ubuntu: { color: '#E95420', keywords: ['ubuntu', 'canonical', 'ubuntu.com'] },
  udemy: { color: '#A435F0', keywords: ['udemy', 'udemy.com'] },
  unity: { color: '#FFFFFF', keywords: ['unity', 'unity.com', 'unity3d.com'] },
  unrealengine: { color: '#0E1128', keywords: ['unreal engine', 'unrealengine', 'unrealengine.com'] },
  valorant: { color: '#FF4655', keywords: ['valorant', 'playvalorant.com'] },
  vercel: { color: '#000000', keywords: ['vercel', 'vercel.com', 'vercel.app'] },
  visa: { color: '#1A1F71', keywords: ['visa', 'visa.com', 'visa.de'] },
  volkswagen: { color: '#151F6D', keywords: ['volkswagen', 'vw', 'volkswagen.de'] },
  webflow: { color: '#146EF5', keywords: ['webflow', 'webflow.com'] },
  webstorm: { color: '#000000', keywords: ['webstorm'] },
  wellsfargo: { color: '#D71E28', keywords: ['wells fargo', 'wellsfargo', 'wellsfargo.com'] },
  whatsapp: { color: '#25D366', keywords: ['whatsapp', 'whatsapp.com', 'wa.me'] },
  wise: { color: '#9FE870', keywords: ['wise', 'transferwise', 'wise.com'] },
  wix: { color: '#0C6EFC', keywords: ['wix', 'wix.com'] },
  wordpress: { color: '#21759B', keywords: ['wordpress', 'wordpress.com', 'wordpress.org', 'wp-admin'] },
  x: { color: 'currentColor', keywords: ['x.com', 'twitter', 'twitter.com', 'tweetdeck'] },
  youtube: { color: '#FF0000', keywords: ['youtube', 'youtube.com', 'youtu.be'] },
  yubico: { color: '#84BD00', keywords: ['yubico', 'yubikey', 'yubico.com'] },
  zalando: { color: '#FF6900', keywords: ['zalando', 'zalando.de', 'zalando.com'] },
  zendesk: { color: '#03363D', keywords: ['zendesk', 'zendesk.com'] },
  zoom: { color: '#2D8CFF', keywords: ['zoom', 'zoom.us', 'zoom.com'] },
}

// 2. DEFINE NEW BRANDS (Requested & Top Worldwide Services)
const NEW_BRANDS = [
  {
    slug: 'msm',
    name: 'Maunting Server Manager (MSM)',
    color: '#00F0FF',
    keywords: ['msm', 'maunting', 'server-manager', 'servermanager', 'mauntingstudios', 'singra', 'panel.mauntingstudios.de'],
    path: 'M4 6.5C4 5.67 4.67 5 5.5 5h13c.83 0 1.5.67 1.5 1.5v3c0 .83-.67 1.5-1.5 1.5h-13C4.67 11 4 10.33 4 9.5v-3zm0 8c0-.83.67-1.5 1.5-1.5h13c.83 0 1.5.67 1.5 1.5v3c0 .83-.67 1.5-1.5 1.5h-13c-.83 0-1.5-.67-1.5-1.5v-3zM7 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm4-6.5l2-3 2 3h-4zm0 8l2-3 2 3h-4z',
  },
  {
    slug: 'nbm',
    name: 'NBM',
    color: '#0284C7',
    keywords: [
      'nbm',
      'nbm-js',
      'nbmjs',
      'nbm-package-manager',
      'nbm package manager',
      'nbm paketmanager',
      'nbm.js',
      'nbmjs.org',
      'nbmjs.com',
      'nbm-modding',
      'norddeutsche bus modding',
      'nbm.de',
      'busmodding',
    ],
    path: 'M12 1.75L3.5 6.66v10.68L12 22.25l8.5-4.91V6.66L12 1.75zm0 2.31l6.5 3.75-2.8 1.62-6.5-3.75 2.8-1.62zm-1.5 2.48l6.5 3.75-2.2 1.27-6.5-3.75 2.2-1.27zM5.5 8.12l5 2.89v8.78l-5-2.89V8.12zm13 8.78l-5 2.89v-8.78l5-2.89v8.78z',
  },
  {
    slug: 'otto',
    name: 'OTTO',
    color: '#EB0014',
    keywords: ['otto', 'otto.de', 'otto versand', 'ottode'],
    path: 'M5.5 12a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0zm1.8 0a1.7 1.7 0 1 0 3.4 0 1.7 1.7 0 0 0-3.4 0zm9.7-3.5h-2.5v7h2.5a3.5 3.5 0 0 0 0-7zm0 5.2h-.7v-3.4h.7a1.7 1.7 0 0 1 0 3.4z',
  },
  {
    slug: 'amazon',
    name: 'Amazon / AWS',
    color: '#FF9900',
    keywords: ['amazon', 'aws', 'prime', 'amazon.com', 'amazon.de', 'amazons3', 'alexa', 'a.co'],
    path: 'M13.945 14.685c-2.074 1.53-5.066 2.34-7.659 2.34-3.633 0-6.897-1.428-9.363-3.807-.194-.187-.04-.44.184-.303 2.68 1.628 5.922 2.607 9.278 2.607 2.298 0 4.81-.502 7.086-1.558.348-.162.639.248.474.721zm1.227-.85c-.264-.343-1.748-.163-2.412-.083-.203.024-.233-.153-.05-.28 1.185-.826 3.125-.588 3.356-.3.234.29-.06 2.234-1.176 3.161-.173.144-.337.067-.258-.12.257-.611.803-2.036.54-2.378z',
  },
  {
    slug: 'seb-hosting',
    name: 'Seb-Hosting',
    color: '#F97316',
    keywords: ['seb-hosting', 'sebhosting', 'seb-hosting.de', 'seb hosting'],
    path: 'M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM14 13h-4v-2h4v2zm-2 5l-3-3h2v-2h2v2h2l-3 3z',
  },
  {
    slug: 'nitrado',
    name: 'Nitrado',
    color: '#F39C12',
    keywords: ['nitrado', 'nitrado.net', 'nitrado.com'],
    path: 'M12 1L2 6v12l10 5 10-5V6L12 1zm0 2.5L19.5 7v10L12 20.5 4.5 17V7L12 3.5zm-3 4.5v8l3-2.5V8H9zm3 2.5l3 2.5v3l-3-2.5v-3z',
  },
  {
    slug: 'backblaze',
    name: 'Backblaze / B2',
    color: '#DA291C',
    keywords: ['backblaze', 'backblaze.com', 'b2', 'backblazeb2', 'backblaze b2'],
    path: 'M15.22 0c-.22 2.33-1.63 4.19-3.4 5.76-1.57 1.39-3.21 2.87-3.9 4.9C6.67 14.34 7.42 18.06 9.68 20.4c-1.39-.7-2.52-1.78-3.23-3.13-.98-1.85-.92-3.98-.37-5.95.2-1.07.56-2.12.87-3.18-.73.54-1.4 1.16-1.95 1.86-1.72 2.19-2.23 5.09-1.52 7.79.62 2.37 2.15 4.39 4.25 5.51 2.33 1.25 5.16 1.36 7.6.35 2.65-1.1 4.7-3.32 5.56-6.04.88-2.79.37-5.9-1.36-8.23-1.2-1.62-2.88-2.88-4.43-4.14C15.89 3.93 15.34 2.02 15.22 0z',
  },
  {
    slug: 'teamspeak',
    name: 'TeamSpeak',
    color: '#2580C3',
    keywords: ['teamspeak', 'teamspeak3', 'ts3', 'teamspeak.com', 'teamspeak.de'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 11.5c0 .83-.67 1.5-1.5 1.5H14v2h-4v-2H8.5C7.67 15 7 14.33 7 13.5v-3C7 9.67 7.67 9 8.5 9h7c.83 0 1.5.67 1.5 1.5v3zM10 11H8.5v2H10v-2zm5.5 0H14v2h1.5v-2z',
  },
  {
    slug: 'mumble',
    name: 'Mumble',
    color: '#1A67B2',
    keywords: ['mumble', 'mumble.info'],
    path: 'M12 2a6 6 0 0 0-6 6v3a6 6 0 0 0 12 0V8a6 6 0 0 0-6-6zm-4 7a4 4 0 1 1 8 0v2a4 4 0 1 1-8 0V9zm4 10c-3.31 0-6-1.34-6-3h12c0 1.66-2.69 3-6 3z',
  },
  {
    slug: 'skype',
    name: 'Skype',
    color: '#00AFF0',
    keywords: ['skype', 'skype.com'],
    path: 'M12 0C5.373 0 0 5.373 0 12c0 1.48.27 2.89.76 4.19A8.955 8.955 0 0 0 .5 18a5.5 5.5 0 0 0 5.5 5.5c.62 0 1.22-.11 1.79-.31A11.94 11.94 0 0 0 12 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm3.89 16.32c-.92.6-2.12.9-3.6.9-1.89 0-3.38-.56-4.46-1.68-.44-.46-.43-1.19.03-1.63.45-.44 1.18-.43 1.62.02.69.72 1.69 1.09 3.01 1.09.95 0 1.7-.2 2.23-.6.54-.4.8-.93.8-1.59 0-.48-.16-.87-.49-1.18-.33-.31-.87-.58-1.64-.81l-1.92-.57c-1.33-.39-2.32-.93-2.97-1.62-.65-.69-.97-1.56-.97-2.6 0-1.28.49-2.3 1.48-3.05.99-.75 2.32-1.13 3.99-1.13 1.64 0 2.99.46 4.04 1.37.45.4.49 1.13.09 1.58-.4.45-1.12.49-1.58.09-.69-.6-1.64-.9-2.85-.9-.97 0-1.7.2-2.19.59-.49.39-.73.88-.73 1.48 0 .44.15.8.46 1.08.31.28.84.53 1.58.75l1.92.57c1.4.42 2.44.99 3.12 1.71.68.72 1.02 1.63 1.02 2.73 0 1.34-.49 2.42-1.48 3.23z',
  },
  {
    slug: 'signal',
    name: 'Signal',
    color: '#3A76F0',
    keywords: ['signal', 'signal.org', 'signal-messenger'],
    path: 'M12 1.5C6.2 1.5 1.5 6.2 1.5 12c0 2 .5 3.9 1.5 5.5L1.5 22.5l5.2-1.4c1.6.9 3.4 1.4 5.3 1.4 5.8 0 10.5-4.7 10.5-10.5S17.8 1.5 12 1.5zm0 18.5c-1.6 0-3.1-.4-4.5-1.2l-.3-.2-3.3.9.9-3.2-.2-.3C3.8 14.6 3.3 13.3 3.3 12c0-4.8 3.9-8.7 8.7-8.7s8.7 3.9 8.7 8.7-3.9 8.7-8.7 8.7z',
  },
  {
    slug: 'viber',
    name: 'Viber',
    color: '#7360F2',
    keywords: ['viber', 'viber.com', 'rakutenviber'],
    path: 'M11.9 1.5C5.8 1.5 1.5 5.8 1.5 11.9c0 2.4.8 4.7 2.2 6.5l-1.3 4.2 4.4-1.1c1.5.8 3.2 1.2 5.1 1.2 6.1 0 10.4-4.3 10.4-10.4s-4.3-10.8-10.4-10.8zm5.5 13.3c-.3.8-1.5 1.5-2.2 1.6-.6.1-1.3.1-3.8-.9-2.7-1.1-4.5-3.8-4.6-4-.1-.2-1.1-1.5-1.1-2.8 0-1.4.7-2 1-2.3.2-.2.6-.3.8-.3h.6c.2 0 .4 0 .6.4.2.5.7 1.8.8 1.9.1.2.1.4 0 .6-.1.2-.2.4-.4.6-.2.2-.3.3-.4.5-.1.2-.3.4-.1.7.2.3.9 1.5 2 2.4 1.4 1.2 2.5 1.6 2.9 1.8.3.1.6.1.8-.1.3-.3.8-.9 1-1.3.2-.3.4-.3.7-.2.3.1 1.9.9 2.2 1 .3.2.5.3.6.4 0 .2-.1 1-.4 1.5z',
  },
  {
    slug: 'matrix',
    name: 'Matrix / Element',
    color: '#0DBD8B',
    keywords: ['element', 'element.io', 'matrix', 'matrix.org'],
    path: 'M.632.55v22.9H2.28V24H0V0h2.28v.55zm7.043 7.26v1.157h.058c.32-.435.735-.783 1.246-1.044.51-.26 1.094-.392 1.75-.392.748 0 1.418.17 2.01.512.593.342 1.025.845 1.296 1.508.336-.48.775-.89 1.316-1.23.541-.34 1.18-.51 1.916-.51.658 0 1.25.112 1.777.337.527.225.964.55 1.31 1.01.346.46.6.994.76 1.603.16.608.24 1.32.24 2.137v7.24h-2.392v-6.95c0-.62-.062-1.16-.186-1.618-.124-.457-.33-.825-.618-1.103-.288-.28-.67-.42-1.146-.42-.51 0-.95.143-1.32.43-.37.288-.636.67-.798 1.146-.162.476-.243 1.002-.243 1.578v6.937H11.51v-6.95c0-.62-.062-1.16-.186-1.618-.124-.457-.33-.825-.618-1.103-.288-.28-.67-.42-1.146-.42-.51 0-.95.143-1.32.43-.37.288-.636.67-.798 1.146-.162.476-.243 1.002-.243 1.578v6.937H4.757V7.81h2.918zm15.693 15.64V.55H21.72V0H24v24h-2.28v-.55z',
  },
  {
    slug: 'hetzner',
    name: 'Hetzner',
    color: '#D50C2D',
    keywords: ['hetzner', 'hetzner.com', 'hetzner.de', 'hetzner-cloud', 'robot.hetzner.de', 'hcloud', 'hetzner cloud'],
    path: 'M3 3h4.5v7h9V3H21v18h-4.5v-7h-9v7H3V3z',
  },
  {
    slug: 'netcup',
    name: 'netcup',
    color: '#2C7BB6',
    keywords: ['netcup', 'netcup.de', 'netcup.eu', 'customercontrolpanel', 'netcup ccp'],
    path: 'M2 5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5zm3 2v10h3l5-7v7h3V7h-3l-5 7V7H5z',
  },
  {
    slug: 'strato',
    name: 'STRATO',
    color: '#FF6600',
    keywords: ['strato', 'strato.de', 'stratohosting', 'strato-hosting'],
    path: 'M12 2L2 7v10l10 5 10-5V7L12 2zm0 2.8l7 3.5v7.4l-7 3.5-7-3.5V8.3l7-3.5zm-4 4.7v5l4 2v-5l-4-2zm8 0l-3 1.5v5l3-1.5v-5z',
  },
  {
    slug: 'ionos',
    name: 'IONOS / 1&1',
    color: '#003D78',
    keywords: ['ionos', 'ionos.de', 'ionos.com', '1und1', '1und1.de', '1&1', '1 und 1'],
    path: 'M2 4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4zm5 3v10h2V9.5l1.5 1.5L12 9.5V17h2V7h-2l-1.5 2L9 7H7zm9 0v10h2V7h-2z',
  },
  {
    slug: 'contabo',
    name: 'Contabo',
    color: '#00457C',
    keywords: ['contabo', 'contabo.com', 'contabo.de'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-1 4h2v5h-2zm0 7h2v5h-2z',
  },
  {
    slug: 'zap-hosting',
    name: 'ZAP-Hosting',
    color: '#76B900',
    keywords: ['zap-hosting', 'zaphosting', 'zap-hosting.com', 'zap hosting'],
    path: 'M12 2L3 14h7v8l9-12h-7V2z',
  },
  {
    slug: 'g-portal',
    name: 'G-PORTAL',
    color: '#FF0055',
    keywords: ['g-portal', 'gportal', 'g-portal.com', 'gportal.com'],
    path: 'M12 2L2 8v8l10 6 10-6V8L12 2zm0 3.2L18.5 9v6L12 18.8 5.5 15V9L12 5.2z',
  },
  {
    slug: 'azure',
    name: 'Microsoft Azure',
    color: '#0089D6',
    keywords: ['azure', 'portal.azure.com', 'azure.microsoft.com', 'azuredevops'],
    path: 'M13.05 4.24l-5.7 9.87L2 14.88l6.35-7.94 4.7-2.7zm.9 1.48l7.8 13.52c.3.52.05 1.18-.47 1.42-.2.09-.41.14-.63.14H8.47l4.13-7.15 1.35-7.93z',
  },
  {
    slug: 'googlecloud',
    name: 'Google Cloud / GCP',
    color: '#4285F4',
    keywords: ['google cloud', 'gcp', 'cloud.google.com', 'google cloud platform'],
    path: 'M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z',
  },
  {
    slug: 'ovhcloud',
    name: 'OVHcloud',
    color: '#000E9C',
    keywords: ['ovh', 'ovhcloud', 'ovh.com', 'ovhcloud.com', 'kimsufi', 'soyoustart'],
    path: 'M12 2L2 7.5v9L12 22l10-5.5v-9L12 2zm0 3.3l7 3.85v5.7L12 18.7l-7-3.85v-5.7l7-3.85z',
  },
  {
    slug: 'linode',
    name: 'Linode / Akamai',
    color: '#00A95C',
    keywords: ['linode', 'linode.com', 'akamai cloud'],
    path: 'M13.43 2.05a8.77 8.77 0 0 0-4.86 1.49c-.27.18-.45.47-.49.79-.04.32.06.65.28.88l1.44 1.54c.24.25.6.35.93.26.8-.23 1.64-.35 2.48-.35 4.3 0 7.8 3.5 7.8 7.8 0 .84-.12 1.68-.35 2.48-.09.33.01.69.26.93l1.54 1.44c.23.22.56.32.88.28.32-.04.61-.22.79-.49a8.77 8.77 0 0 0 1.49-4.86c0-5.96-4.84-10.8-10.8-10.8z',
  },
  {
    slug: 'vultr',
    name: 'Vultr',
    color: '#007BFC',
    keywords: ['vultr', 'vultr.com'],
    path: 'M2.4 2.4h5.2l4.4 12.2L16.4 2.4h5.2l-7.3 19.2h-4.6L2.4 2.4z',
  },
  {
    slug: 'scaleway',
    name: 'Scaleway',
    color: '#4F0599',
    keywords: ['scaleway', 'scaleway.com', 'online.net'],
    path: 'M6 3h12a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3zm4 5l-3 4 3 4V8zm4 0v8l3-4-3-4z',
  },
  {
    slug: 'wasabi',
    name: 'Wasabi Hot Cloud Storage',
    color: '#00B140',
    keywords: ['wasabi', 'wasabi.com', 'wasabisys.com', 'wasabi storage', 'wasabi s3'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z',
  },
  {
    slug: 'minio',
    name: 'MinIO',
    color: '#C72C48',
    keywords: ['minio', 'min.io', 'minio.io', 'minio s3'],
    path: 'M12 2L2 7v10l10 5 10-5V7L12 2zm0 3.2L18.5 9 12 12.8 5.5 9 12 5.2zM4.5 10.7l6.5 3.8v6.8l-6.5-3.8v-6.8zm15 0v6.8l-6.5 3.8v-6.8l6.5-3.8z',
  },
  {
    slug: 'storj',
    name: 'Storj',
    color: '#2683FF',
    keywords: ['storj', 'storj.io', 'tardigrade'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4 7l-4 5-4-5h8z',
  },
  {
    slug: 'hostinger',
    name: 'Hostinger',
    color: '#673DE6',
    keywords: ['hostinger', 'hostinger.com', 'hostinger.de'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-4 4h3v4h2V6h3v12h-3v-4h-2v4H8V6z',
  },
  {
    slug: 'godaddy',
    name: 'GoDaddy',
    color: '#1BDBDB',
    keywords: ['godaddy', 'godaddy.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-1 4h2v8l3-3 1.5 1.5L12 18l-5.5-5.5L8 11l3 3V6z',
  },
  {
    slug: 'namecheap',
    name: 'Namecheap',
    color: '#DE3723',
    keywords: ['namecheap', 'namecheap.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-5 6h3l2 4 2-4h3l-3.5 6 3.5 6h-3l-2-4-2 4H7l3.5-6L7 8z',
  },
  {
    slug: 'bluehost',
    name: 'Bluehost',
    color: '#006699',
    keywords: ['bluehost', 'bluehost.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-3 4h6a3 3 0 0 1 0 6h-6V6zm0 6h6a3 3 0 0 1 0 6H9v-6z',
  },
  {
    slug: 'siteground',
    name: 'SiteGround',
    color: '#414042',
    keywords: ['siteground', 'siteground.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm4 7l-4 6-4-6h8z',
  },
  {
    slug: 'oraclecloud',
    name: 'Oracle Cloud',
    color: '#F80000',
    keywords: ['oracle', 'oracle.com', 'oracle cloud', 'cloud.oracle.com', 'oci'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3.2a6.8 6.8 0 1 1 0 13.6 6.8 6.8 0 0 1 0-13.6z',
  },
  {
    slug: 'alibabacloud',
    name: 'Alibaba Cloud',
    color: '#FF6A00',
    keywords: ['alibaba cloud', 'alibabacloud.com', 'aliyun', 'aliyun.com'],
    path: 'M12 2L2 7v10l10 5 10-5V7L12 2zm0 3.2L18.5 9 12 12.8 5.5 9 12 5.2zm-4.5 5.5l4.5 2.8 4.5-2.8v4.6l-4.5 2.8-4.5-2.8v-4.6z',
  },
  {
    slug: 'fastly',
    name: 'Fastly',
    color: '#FF282D',
    keywords: ['fastly', 'fastly.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm3 6h-6v8h2v-3h4v-2h-4V10h4V8z',
  },
  {
    slug: 'flyio',
    name: 'Fly.io',
    color: '#24185B',
    keywords: ['fly.io', 'flyio'],
    path: 'M12 2L2 9l10 7 10-7-10-7zm0 9L5 6.5 12 4l7 2.5-7 4.5zm0 4.5L4 10v4l8 6 8-6v-4l-8 5.5z',
  },
  {
    slug: 'render',
    name: 'Render',
    color: '#46E3B7',
    keywords: ['render', 'render.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-3 4h4a3 3 0 0 1 0 6H9V6zm0 6h4l3 6h-3l-2.5-5H9v5H7V6h2v6z',
  },
  {
    slug: 'railway',
    name: 'Railway',
    color: '#0B0D0E',
    keywords: ['railway', 'railway.app'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-4 4h4a3 3 0 0 1 3 3c0 1.2-.7 2.2-1.7 2.7L16 18h-3l-2.5-4.5H10V18H8V6zm2 2v3h2a1.5 1.5 0 0 0 0-3h-2z',
  },
  {
    slug: 'heroku',
    name: 'Heroku',
    color: '#430098',
    keywords: ['heroku', 'heroku.com'],
    path: 'M4 2v20h16V2H4zm3 3h3v5h4V5h3v14h-3v-5h-4v5H7V5z',
  },
  {
    slug: 'nextcloud',
    name: 'Nextcloud',
    color: '#0082C9',
    keywords: ['nextcloud', 'nextcloud.com'],
    path: 'M12 2a4 4 0 0 0-3.8 2.8A5.5 5.5 0 0 0 3 10a5.5 5.5 0 0 0 3.5 5.1A4 4 0 0 0 12 22a4 4 0 0 0 3.8-2.8A5.5 5.5 0 0 0 21 14a5.5 5.5 0 0 0-3.5-5.1A4 4 0 0 0 12 2zm0 2.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm-4.5 5.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zm9 0a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5z',
  },
  {
    slug: 'owncloud',
    name: 'ownCloud',
    color: '#1D2D44',
    keywords: ['owncloud', 'owncloud.com', 'owncloud.org'],
    path: 'M12 4C7.58 4 4 7.58 4 12c0 3.5 2.25 6.5 5.5 7.5.5.1.7-.2.7-.5v-1.8c-2.2.5-2.7-1.1-2.7-1.1-.3-.9-.9-1.1-.9-1.1-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.2 1.9.9 2.3.7.1-.5.3-.9.5-1.1-1.8-.2-3.6-.9-3.6-4 0-.9.3-1.6.8-2.2-.1-.2-.4-1 .1-2.2 0 0 .7-.2 2.3.8.7-.2 1.4-.3 2.1-.3.7 0 1.4.1 2.1.3 1.6-1 2.3-.8 2.3-.8.5 1.2.2 2 .1 2.2.5.6.8 1.3.8 2.2 0 3.1-1.9 3.8-3.7 4 .3.3.6.8.6 1.6v2.4c0 .3.2.6.7.5 3.25-1 5.5-4 5.5-7.5 0-4.42-3.58-8-8-8z',
  },
  {
    slug: 'box',
    name: 'Box',
    color: '#0061D5',
    keywords: ['box', 'box.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3.5 13.5l-3.5-2.5-3.5 2.5V8.5h7v7z',
  },
  {
    slug: 'mega',
    name: 'MEGA',
    color: '#D9272E',
    keywords: ['mega', 'mega.nz', 'mega.io'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.5 14h-2.2l-2.3-3.8-2.3 3.8H7.5V8h2.2l2.3 3.8L14.3 8h2.2v8z',
  },
  {
    slug: 'pcloud',
    name: 'pCloud',
    color: '#00B5E2',
    keywords: ['pcloud', 'pcloud.com'],
    path: 'M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM12 9a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5z',
  },
  {
    slug: 'synology',
    name: 'Synology',
    color: '#1B242A',
    keywords: ['synology', 'synology.com', 'quickconnect', 'quickconnect.to', 'diskstation'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 12h-4v2h-2v-2H7v-2h4v-2h2v2h4v2z',
  },
  {
    slug: 'onedrive',
    name: 'Microsoft OneDrive',
    color: '#0078D4',
    keywords: ['onedrive', 'onedrive.live.com', '1drv.ms'],
    path: 'M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z',
  },
  {
    slug: 'cocacola',
    name: 'Coca-Cola',
    color: '#F40009',
    keywords: ['coca-cola', 'cocacola', 'coca cola', 'coke', 'coke.com', 'coca-cola.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5.7 13.5c-.8.8-2.1 1.1-3.6 1-2.2-.2-4.2-1.4-6.3-1.7-.8-.1-1.6 0-2.3.4-.2.1-.4-.1-.3-.3.4-.6 1.2-1 2.1-1.1 2.2-.2 4.2 1 6.4 1.4.9.2 1.9.1 2.7-.4.2-.1.4.1.3.3zm.6-2.6c-.6.6-1.6.8-2.8.7-2-.1-3.8-1.2-5.7-1.5-.7-.1-1.4 0-2 .3-.2.1-.4-.1-.3-.3.4-.5 1.1-.9 1.9-1 1.9-.2 3.8.9 5.8 1.3.8.2 1.6.1 2.3-.2.2-.1.4.1.3.3z',
  },
  {
    slug: 'fanta',
    name: 'Fanta',
    color: '#FF7300',
    keywords: ['fanta', 'fanta.com', 'fanta.de'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 4.5a1.5 1.5 0 0 1 2-1c2 1 2.5 3.5 1 5-.5.5-1.5.5-2-.5s-.5-2.5-1-3.5zm-5 4.5c1.5-1 3.5-.5 4.5 1s.5 3.5-1 4.5-3.5.5-4.5-1-.5-3.5 1-4.5zm8 6c-1 1.5-3 2-4.5 1s-2-3-1-4.5 3-2 4.5-1 2 3 1 4.5z',
  },
  {
    slug: 'pepsi',
    name: 'Pepsi',
    color: '#005CB9',
    keywords: ['pepsi', 'pepsi.com', 'pepsico'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm6.9 8.2C17.4 8.8 14.8 8 12 8c-3.8 0-7 1.6-8.9 4.2C3.4 9.6 4.6 7.2 6.6 5.5 8.1 4.2 10 3.4 12 3.4c3.4 0 6.5 1.9 8.1 4.8-.4.7-.8 1.4-1.2 2zM5.1 13.8C6.6 15.2 9.2 16 12 16c3.8 0 7-1.6 8.9-4.2-.3 2.6-1.5 5-3.5 6.7-1.5 1.3-3.4 2.1-5.4 2.1-3.4 0-6.5-1.9-8.1-4.8.4-.7.8-1.4 1.2-2z',
  },
  {
    slug: 'redbull',
    name: 'Red Bull',
    color: '#DB0A40',
    keywords: ['redbull', 'red bull', 'redbull.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4 7l-4 3-4-3 1.5 6h5L16 9z',
  },
  {
    slug: 'starbucks',
    name: 'Starbucks',
    color: '#00704A',
    keywords: ['starbucks', 'starbucks.com', 'starbucks.de'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3.2c3.5 0 6.4 2.5 7 5.8-1.2-.4-2.8-.8-4.5-.8-1.4 0-2.7.2-3.8.5-1.1-.3-2.4-.5-3.8-.5-1.7 0-3.3.4-4.5.8.6-3.3 3.5-5.8 7-5.8zm-6.2 8.3c1.2-.5 2.8-.8 4.7-.8 1.1 0 2.2.1 3.2.4 1-.3 2.1-.4 3.2-.4 1.9 0 3.5.3 4.7.8-.5 3-2.9 5.3-5.9 5.3-3 0-5.4-2.3-5.9-5.3z',
  },
  {
    slug: 'subway',
    name: 'Subway',
    color: '#008C15',
    keywords: ['subway', 'subway.com', 'subway.de'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 13h-3l-2-3-2 3H7l3-5-3-5h3l2 3 2-3h3l-3 5 3 5z',
  },
  {
    slug: 'dominos',
    name: "Domino's Pizza",
    color: '#006491',
    keywords: ['dominos', "domino's", 'dominos.com', 'dominos.de'],
    path: 'M18.5 3.5L3.5 18.5l2 2L20.5 5.5l-2-2zM8 14a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm8-8a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm-2 2a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z',
  },
  {
    slug: 'kfc',
    name: 'KFC',
    color: '#E4002B',
    keywords: ['kfc', 'kfc.com', 'kfc.de', 'kentucky fried chicken'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-4 5h8v2H8V7zm0 4h8v2H8v-2zm0 4h5v2H8v-2z',
  },
  {
    slug: 'lieferando',
    name: 'Lieferando / Just Eat',
    color: '#FF8000',
    keywords: ['lieferando', 'lieferando.de', 'just eat', 'takeaway.com', 'justeat'],
    path: 'M12 2L2 9.5v11h20v-11L12 2zm0 4.5l6 4.5v7H6v-7l6-4.5zm-2 6.5h4v3h-4v-3z',
  },
  {
    slug: 'aliexpress',
    name: 'AliExpress',
    color: '#FF4747',
    keywords: ['aliexpress', 'aliexpress.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H8.5l3.5-8h2.5l3.5 8H15.5l-.8-2h-3.4l-.8 2zm1.6-4h2l-1-2.5-1 2.5z',
  },
  {
    slug: 'alibaba',
    name: 'Alibaba',
    color: '#FF6A00',
    keywords: ['alibaba', 'alibaba.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3.5 13.5l-3.5-2.5-3.5 2.5V8.5h7v7z',
  },
  {
    slug: 'temu',
    name: 'Temu',
    color: '#FB7701',
    keywords: ['temu', 'temu.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-4 5h8v2.5h-2.7V17h-2.6V9.5H8V7z',
  },
  {
    slug: 'shein',
    name: 'SHEIN',
    color: '#000000',
    keywords: ['shein', 'shein.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3 13.5c-.8.8-2 1.2-3.3 1.2-2.8 0-4.7-1.8-4.7-4.7s1.9-4.7 4.7-4.7c1.3 0 2.5.4 3.3 1.2l-1.4 1.5c-.5-.5-1.2-.7-1.9-.7-1.7 0-2.8 1.1-2.8 2.7s1.1 2.7 2.8 2.7c.7 0 1.4-.2 1.9-.7l1.4 1.5z',
  },
  {
    slug: 'asos',
    name: 'ASOS',
    color: '#2D2D2D',
    keywords: ['asos', 'asos.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-4 13.5h-2L9 8h2l3 7.5h-2l-.6-1.5H8.6l-.6 1.5zm1-3.5h1.8L10 9.8 9 12z',
  },
  {
    slug: 'mediamarkt',
    name: 'MediaMarkt',
    color: '#DF0000',
    keywords: ['mediamarkt', 'mediamarkt.de', 'media markt'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-4 5h2.5l2 4 2-4H17v10h-2.5v-5l-2 4h-1l-2-4v5H8V7z',
  },
  {
    slug: 'saturn',
    name: 'Saturn',
    color: '#003D78',
    keywords: ['saturn', 'saturn.de'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-5 8a5 5 0 0 1 10 0c0 2.5-1.5 4.5-4 5.5l3.5 2h-3l-3-2H9v2H7V10zm2 2.5h3a2.5 2.5 0 0 0 0-5H9v5z',
  },
  {
    slug: 'cyberport',
    name: 'Cyberport',
    color: '#006CB7',
    keywords: ['cyberport', 'cyberport.de'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-1 4h2v6h-2V6zm0 8h2v2h-2v-2z',
  },
  {
    slug: 'alternate',
    name: 'Alternate',
    color: '#FF6600',
    keywords: ['alternate', 'alternate.de'],
    path: 'M12 2L2 20h20L12 2zm0 4.5L17.5 17h-11L12 6.5z',
  },
  {
    slug: 'mindfactory',
    name: 'Mindfactory',
    color: '#0054A6',
    keywords: ['mindfactory', 'mindfactory.de'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-4 5h3v4l3-4h3l-4 5 4 5h-3l-3-4v4H8V7z',
  },
  {
    slug: 'bestbuy',
    name: 'Best Buy',
    color: '#FFF200',
    keywords: ['bestbuy', 'bestbuy.com', 'best buy'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4 11h-3v3h-2v-3H8v-2h3V8h2v3h3v2z',
  },
  {
    slug: 'walmart',
    name: 'Walmart',
    color: '#0071DC',
    keywords: ['walmart', 'walmart.com'],
    path: 'M12 2a1.5 1.5 0 0 1 1.5 1.5v4a1.5 1.5 0 0 1-3 0v-4A1.5 1.5 0 0 1 12 2zm6.9 4a1.5 1.5 0 0 1 2.1.5 1.5 1.5 0 0 1-.5 2.1l-3.5 2a1.5 1.5 0 0 1-1.5-2.6l3.4-2zm2.1 11.5a1.5 1.5 0 0 1-.5 2.1 1.5 1.5 0 0 1-2.1-.5l-2-3.5a1.5 1.5 0 0 1 2.6-1.5l2 3.4zM12 22a1.5 1.5 0 0 1-1.5-1.5v-4a1.5 1.5 0 0 1 3 0v4A1.5 1.5 0 0 1 12 22zm-6.9-4a1.5 1.5 0 0 1-2.1-.5 1.5 1.5 0 0 1 .5-2.1l3.5-2a1.5 1.5 0 0 1 1.5 2.6l-3.4 2zm-2.1-11.5a1.5 1.5 0 0 1 .5-2.1 1.5 1.5 0 0 1 2.1.5l2 3.5a1.5 1.5 0 0 1-2.6 1.5l-2-3.4z',
  },
  {
    slug: 'target',
    name: 'Target',
    color: '#CC0000',
    keywords: ['target', 'target.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3a7 7 0 1 1 0 14 7 7 0 0 1 0-14zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  },
  {
    slug: 'woocommerce',
    name: 'WooCommerce',
    color: '#96588A',
    keywords: ['woocommerce', 'woocommerce.com', 'woo'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.5 14h-2.3l-2.2-4.5-2.2 4.5H7.5L11 8h2l3.5 8z',
  },
  {
    slug: 'vinted',
    name: 'Vinted',
    color: '#09B1BA',
    keywords: ['vinted', 'vinted.de', 'vinted.com', 'kleiderkreisel'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-3.5 14l-2-8h2.3l1.1 5.2 2-5.2h2.2l-3.6 8H8.5z',
  },
  {
    slug: 'kleinanzeigen',
    name: 'Kleinanzeigen',
    color: '#86B817',
    keywords: ['kleinanzeigen', 'kleinanzeigen.de', 'ebay-kleinanzeigen'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-1 5h2v6h-2V7zm0 8h2v2h-2v-2z',
  },
  {
    slug: 'idealo',
    name: 'idealo',
    color: '#F48B29',
    keywords: ['idealo', 'idealo.de'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-1 4h2v2h-2V6zm0 4h2v8h-2v-8z',
  },
  {
    slug: 'check24',
    name: 'CHECK24',
    color: '#003E82',
    keywords: ['check24', 'check24.de'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-2 13.5l-3.5-3.5 1.4-1.4 2.1 2.1 5.6-5.6 1.4 1.4-7 7z',
  },
  {
    slug: 'disneyplus',
    name: 'Disney+',
    color: '#113CCF',
    keywords: ['disney', 'disneyplus', 'disneyplus.com', 'disney+'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3.5 14h-4a3.5 3.5 0 0 1 0-7h4v7zm-2-5h-2a1.5 1.5 0 1 0 0 3h2v-3z',
  },
  {
    slug: 'crunchyroll',
    name: 'Crunchyroll',
    color: '#F47521',
    keywords: ['crunchyroll', 'crunchyroll.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 4.5a5.5 5.5 0 1 1-5.5 5.5A5.5 5.5 0 0 1 13 6.5zm-1 3a2.5 2.5 0 1 0 2.5 2.5A2.5 2.5 0 0 0 12 9.5z',
  },
  {
    slug: 'deezer',
    name: 'Deezer',
    color: '#FEAA2D',
    keywords: ['deezer', 'deezer.com'],
    path: 'M2 14h3v3H2v-3zm0 4h3v3H2v-3zm5-4h3v3H7v-3zm0 4h3v3H7v-3zm5-8h3v3h-3V10zm0 4h3v3h-3v-3zm0 4h3v3h-3v-3zm5-8h3v3h-3V10zm0 4h3v3h-3v-3zm0 4h3v3h-3v-3zm5-12h3v3h-3V6zm0 4h3v3h-3v-3zm0 4h3v3h-3v-3zm0 4h3v3h-3v-3z',
  },
  {
    slug: 'tidal',
    name: 'Tidal',
    color: '#000000',
    keywords: ['tidal', 'tidal.com'],
    path: 'M12.012 3.992L8.008 8l4.004 4.008L16.016 8l-4.004-4.008zM4.004 8L0 12.008l4.004 4.008L8.008 12 4.004 8zm16.008 0l-4.004 4.008 4.004 4.008L24 12.008 20.012 8zm-8 8.016l-4.004 4.008 4.004 4.008 4.004-4.008-4.004-4.008z',
  },
  {
    slug: 'soundcloud',
    name: 'SoundCloud',
    color: '#FF5500',
    keywords: ['soundcloud', 'soundcloud.com'],
    path: 'M1.5 14.5a1.5 1.5 0 1 1 3 0v4a1.5 1.5 0 1 1-3 0v-4zm4.5-3a1.5 1.5 0 1 1 3 0v7a1.5 1.5 0 1 1-3 0v-7zm4.5-3a1.5 1.5 0 1 1 3 0v10a1.5 1.5 0 1 1-3 0V8.5zm4.5 1a4 4 0 0 1 8 0v8h-8v-8z',
  },
  {
    slug: 'vimeo',
    name: 'Vimeo',
    color: '#1AB7EA',
    keywords: ['vimeo', 'vimeo.com'],
    path: 'M22.84 5.7c-.1 2.3-1.7 5.4-4.8 9.3-3.2 4.1-5.9 6.1-8.1 6.1-1.4 0-2.5-1.3-3.4-3.8l-1.8-6.8C4.1 8 3.4 6.8 2.6 6.8c-.2 0-.8.4-1.9 1.1L0 6.7C1.4 5.4 2.8 4.1 4.3 2.9 5.8 1.6 7 1 7.9 1c1.3 0 2.2 1 2.6 2.9.5 2.5.9 4.8 1.2 7 .5 2.4 1.1 3.6 1.8 3.6.6 0 1.4-.9 2.5-2.7 1.1-1.8 1.7-3.1 1.7-4 0-1.2-.6-1.8-1.8-1.8-.6 0-1.2.1-1.8.4 1.2-4 3.5-5.9 6.8-5.7 2.4.1 3.6 1.8 3.5 5z',
  },
  {
    slug: 'pinterest',
    name: 'Pinterest',
    color: '#BD081C',
    keywords: ['pinterest', 'pinterest.com'],
    path: 'M12 0C5.373 0 0 5.373 0 12c0 5.084 3.163 9.426 7.627 11.174-.105-.949-.2-2.405.042-3.441.218-.937 1.407-5.965 1.407-5.965s-.359-.719-.359-1.782c0-1.668.967-2.914 2.171-2.914 1.023 0 1.518.769 1.518 1.69 0 1.029-.655 2.568-.994 3.995-.283 1.194.599 2.169 1.777 2.169 2.133 0 3.772-2.249 3.772-5.495 0-2.873-2.064-4.882-5.012-4.882-3.414 0-5.418 2.561-5.418 5.207 0 1.031.397 2.138.893 2.738a.36.36 0 0 1 .083.345l-.333 1.36c-.053.22-.174.267-.402.161-1.499-.698-2.436-2.889-2.436-4.649 0-3.785 2.75-7.262 7.929-7.262 4.163 0 7.398 2.967 7.398 6.931 0 4.136-2.607 7.464-6.227 7.464-1.216 0-2.359-.631-2.75-1.378l-.748 2.853c-.271 1.043-1.002 2.35-1.492 3.146C9.57 23.812 10.763 24 12 24c6.627 0 12-5.373 12-12S18.627 0 12 0z',
  },
  {
    slug: 'threads',
    name: 'Threads',
    color: '#000000',
    keywords: ['threads', 'threads.net'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3.5 13.5c-1 1-2.2 1.5-3.5 1.5-2.8 0-5-2.2-5-5s2.2-5 5-5c2.5 0 4.5 1.8 4.9 4.2h-2c-.3-1.2-1.4-2.2-2.9-2.2-1.7 0-3 1.3-3 3s1.3 3 3 3c.8 0 1.6-.3 2.1-.9l1.4 1.4z',
  },
  {
    slug: 'tumblr',
    name: 'Tumblr',
    color: '#36465D',
    keywords: ['tumblr', 'tumblr.com'],
    path: 'M14.563 24c-5.093 0-7.001-2.9-7.001-6.84V10.98H4.62V6.93s4.654-.86 5.372-5.11h3.766V6.93h5.084v4.05h-5.084v5.67c0 1.956.88 2.62 2.378 2.62h2.706V24z',
  },
  {
    slug: 'nintendo',
    name: 'Nintendo',
    color: '#E60012',
    keywords: ['nintendo', 'nintendo.com', 'nintendo.de', 'nintendoswitch'],
    path: 'M7.5 2h9C20.64 2 24 5.36 24 9.5v5c0 4.14-3.36 7.5-7.5 7.5h-9C3.36 22 0 18.64 0 14.5v-5C0 5.36 3.36 2 7.5 2zm-1 3.5C4.46 5.5 2.5 7.46 2.5 9.5v5c0 2.04 1.96 4 4 4h3.5v-13H6.5zm11 0h-3.5v13h3.5c2.04 0 4-1.96 4-4v-5c0-2.04-1.96-4-4-4zm-10 4a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm9 0a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z',
  },
  {
    slug: 'xbox',
    name: 'Xbox',
    color: '#107C10',
    keywords: ['xbox', 'xbox.com', 'gamepass'],
    path: 'M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm-1.8 4.2c.6-.4 1.2-.4 1.8 0 1.8 1.1 3.8 2.9 5.2 4.9-1.9 1.8-4.3 3.5-7 4.7-2.7-1.2-5.1-2.9-7-4.7 1.4-2 3.4-3.8 5.2-4.9h-.2zM3.2 14.8c1.5 1.6 3.4 3 5.6 4.1-1.8.8-3.4 1.2-4.6 1.2-1.8 0-2.8-.8-2.8-2.2 0-1.1.6-2.2 1.8-3.1zm17.6 3.1c0 1.4-1 2.2-2.8 2.2-1.2 0-2.8-.4-4.6-1.2 2.2-1.1 4.1-2.5 5.6-4.1 1.2.9 1.8 2 1.8 3.1z',
  },
  {
    slug: 'minecraft',
    name: 'Minecraft',
    color: '#4CA146',
    keywords: ['minecraft', 'minecraft.net', 'mojang'],
    path: 'M4 4h16v16H4V4zm3 3v4h2v2h2v2h2v-2h2v-2h2V7H7zm2 2h2v2H9V9zm4 0h2v2h-2V9z',
  },
  {
    slug: 'openai',
    name: 'OpenAI / ChatGPT',
    color: '#10A37F',
    keywords: ['openai', 'chatgpt', 'chat.openai.com', 'openai.com'],
    path: 'M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.63a4.47 4.47 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.674 8.105v-5.673a.79.79 0 0 0-.41-.672zm2.01-3.017a4.506 4.506 0 0 1-.535 3.014l-.142-.085-4.783-2.759a.77.77 0 0 0-.78 0l-5.843 3.369V9.945a.08.08 0 0 1 .033-.062L14.26 7.08a4.5 4.5 0 0 1 6.69 1.654zm-8.878 1.956l2.766-1.6a.79.79 0 0 0 .393-.681V4.24a4.5 4.5 0 0 1 4.494 4.494l-.142.08-4.779 2.76a.795.795 0 0 0-.392.68v6.737l-2.02-1.168a.071.071 0 0 1-.038-.052V11.23a.78.78 0 0 0-.372-.536z',
  },
  {
    slug: 'midjourney',
    name: 'Midjourney',
    color: '#FFFFFF',
    keywords: ['midjourney', 'midjourney.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 14h-2v-4h2v4zm0-6h-2V8h2v2z',
  },
  {
    slug: 'perplexity',
    name: 'Perplexity AI',
    color: '#1FB8CD',
    keywords: ['perplexity', 'perplexity.ai'],
    path: 'M12 2L4 7v10l8 5 8-5V7L12 2zm0 3.2L17.5 8 12 11.5 6.5 8 12 5.2zM6 9.8l5 3.2v5.8l-5-3.2V9.8zm12 5.8l-5 3.2V13l5-3.2v5.8z',
  },
  {
    slug: 'huggingface',
    name: 'Hugging Face',
    color: '#FFD21E',
    keywords: ['huggingface', 'huggingface.co', 'hf.co'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-3.5 7a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm7 0a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm-7.2 6.5a6 6 0 0 0 7.4 0 .8.8 0 1 1 1 1.2 7.6 7.6 0 0 1-9.4 0 .8.8 0 0 1 1-1.2z',
  },
  {
    slug: 'cursor',
    name: 'Cursor',
    color: '#000000',
    keywords: ['cursor', 'cursor.com', 'cursor.sh'],
    path: 'M12 2L3 21.5l1.5.5L12 18l7.5 4 1.5-.5L12 2z',
  },
  {
    slug: 'mistral',
    name: 'Mistral AI',
    color: '#FA520F',
    keywords: ['mistral', 'mistral.ai', 'lechat'],
    path: 'M4 4h4v4H4V4zm12 0h4v4h-4V4zM8 8h8v4H8V8zm-4 4h4v4H4v-4zm12 0h4v4h-4v-4zM8 16h8v4H8v-4z',
  },
  {
    slug: 'elevenlabs',
    name: 'ElevenLabs',
    color: '#000000',
    keywords: ['elevenlabs', 'elevenlabs.io'],
    path: 'M6 4h3v16H6V4zm9 0h3v16h-3V4z',
  },
  {
    slug: 'linkedin',
    name: 'LinkedIn',
    color: '#0A66C2',
    keywords: ['linkedin', 'linkedin.com'],
    path: 'M19 3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14m-.5 15.5v-5.3a3.26 3.26 0 0 0-3.26-3.26c-.85 0-1.84.52-2.28 1.3v-1.11h-2.79v8.37h2.79v-4.93c0-.77.62-1.4 1.39-1.4a1.4 1.4 0 0 1 1.4 1.4v4.93h2.75M6.46 8.76a1.45 1.45 0 1 0 0-2.9 1.45 1.45 0 0 0 0 2.9m1.4 9.74V10.13H5.06v8.37h2.8z',
  },
  {
    slug: 'slack',
    name: 'Slack',
    color: '#4A154B',
    keywords: ['slack', 'slack.com'],
    path: 'M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z',
  },
  {
    slug: 'npm',
    name: 'npm',
    color: '#CB3837',
    keywords: ['npm', 'npmjs', 'npmjs.com'],
    path: 'M1.763 0C.786 0 0 .786 0 1.763v20.474C0 23.214.786 24 1.763 24h20.474c.977 0 1.763-.786 1.763-1.763V1.763C24 .786 23.214 0 22.237 0zM5.13 5.323l13.837.019-.009 13.836h-3.464l.01-10.382h-3.456L12.04 19.17H5.113z',
  },
  {
    slug: 'pypi',
    name: 'PyPI',
    color: '#3775A9',
    keywords: ['pypi', 'pypi.org', 'pip'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 4h3v5h-5V9h2V6zm-5 7H5V8h5v2H7v3zm5 5h-3v-5h5v2h-2v3zm5-7h3v5h-5v-2h2v-3z',
  },
  {
    slug: 'redis',
    name: 'Redis',
    color: '#FF4438',
    keywords: ['redis', 'redis.io'],
    path: 'M12 2L2 7.5v9L12 22l10-5.5v-9L12 2zm0 3.2l7 3.85-7 3.85-7-3.85 7-3.85zM4.5 10.7l6.5 3.58v6.72l-6.5-3.58v-6.72zm15 0v6.72l-6.5 3.58v-6.72l6.5-3.58z',
  },
  {
    slug: 'mysql',
    name: 'MySQL',
    color: '#4479A1',
    keywords: ['mysql', 'mysql.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-4 5h2l2 4 2-4h2v10h-2v-6l-2 4h-.5l-2-4v6H8V7z',
  },
  {
    slug: 'sentry',
    name: 'Sentry',
    color: '#362D59',
    keywords: ['sentry', 'sentry.io'],
    path: 'M13.2 1.8a1.2 1.2 0 0 0-2.4 0v1.2a1.2 1.2 0 0 0 2.4 0V1.8zM4.2 15.6a1.2 1.2 0 0 0 0 2.4h15.6a1.2 1.2 0 0 0 0-2.4H4.2zm4.2-4.8a1.2 1.2 0 0 0 0 2.4h7.2a1.2 1.2 0 0 0 0-2.4H8.4zm2.4-4.8a1.2 1.2 0 0 0 0 2.4h2.4a1.2 1.2 0 0 0 0-2.4h-2.4z',
  },
  {
    slug: 'datadog',
    name: 'Datadog',
    color: '#632CA6',
    keywords: ['datadog', 'datadoghq.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3.5 14.5l-3.5-2.5-3.5 2.5V7h7v9.5z',
  },
  {
    slug: 'grafana',
    name: 'Grafana',
    color: '#F46800',
    keywords: ['grafana', 'grafana.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4 14l-4-3-4 3 1.5-5.5L7 8.5h4.5L12 3l1.5 5.5H17l-3.5 3L16 16z',
  },
  {
    slug: 'canva',
    name: 'Canva',
    color: '#00C4CC',
    keywords: ['canva', 'canva.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm2 14.5c-3 0-5.5-2-5.5-5s2.5-5 5.5-5c1.5 0 2.8.5 3.8 1.4l-1.4 1.4c-.7-.6-1.5-1-2.4-1-1.8 0-3.3 1.4-3.3 3.2s1.5 3.2 3.3 3.2c.9 0 1.7-.4 2.4-1l1.4 1.4c-1 .9-2.3 1.6-3.8 1.6z',
  },
  {
    slug: 'adobe',
    name: 'Adobe',
    color: '#FF0000',
    keywords: ['adobe', 'creative cloud', 'adobe.com', 'photoshop', 'illustrator'],
    path: 'M13.96 22.01h4.03L12.02 2 6.01 22.01h4.03l1.98-6.63h1.94zM0 2h8.63l-4.3 14.36L0 2zm24 0h-8.63l4.3 14.36L24 2z',
  },
  {
    slug: 'miro',
    name: 'Miro',
    color: '#050038',
    keywords: ['miro', 'miro.com'],
    path: 'M8 3L4 9l4 6 4-6-4-6zm6 4l-4 6 4 6 4-6-4-6zm6 4l-4 6 4 6 4-6-4-6z',
  },
  {
    slug: 'loom',
    name: 'Loom',
    color: '#625DF5',
    keywords: ['loom', 'loom.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 4a6 6 0 1 1 0 12 6 6 0 0 1 0-12zm0 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  },
  {
    slug: 'monday',
    name: 'Monday.com',
    color: '#00CA72',
    keywords: ['monday', 'monday.com'],
    path: 'M5 14a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm7 0a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm7 0a2 2 0 1 1 0-4 2 2 0 0 1 0 4z',
  },
  {
    slug: 'clickup',
    name: 'ClickUp',
    color: '#7B68EE',
    keywords: ['clickup', 'clickup.com'],
    path: 'M2 17.5l2-1.5c1.5 2 3.5 3 6 3s4.5-1 6-3l2 1.5c-2 2.5-4.5 4-8 4s-6-1.5-8-4zm10-13l6 6-1.5 1.5L12 7.5 7.5 12 6 10.5l6-6z',
  },
  {
    slug: 'coinbase',
    name: 'Coinbase',
    color: '#0052FF',
    keywords: ['coinbase', 'coinbase.com'],
    path: 'M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm0 18.5a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13zm-2-9h4v2h-4V9.5zm0 3h4v2h-4v-2z',
  },
  {
    slug: 'kraken',
    name: 'Kraken',
    color: '#5741D9',
    keywords: ['kraken', 'kraken.com'],
    path: 'M12 2a10 10 0 0 0-10 10c0 4.4 2.8 8.1 6.8 9.5v-3.8c-2.3-.9-4-3.1-4-5.7 0-3.3 2.7-6 6-6s6 2.7 6 6c0 2.6-1.7 4.8-4 5.7v3.8c4-1.4 6.8-5.1 6.8-9.5A10 10 0 0 0 12 2zm-1 8h2v7h-2v-7z',
  },
  {
    slug: 'bitpanda',
    name: 'Bitpanda',
    color: '#00D287',
    keywords: ['bitpanda', 'bitpanda.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3.5 14h-7v-2h7v2zm0-4h-7v-2h7v2zm0-4h-7V6h7v2z',
  },
  {
    slug: 'cryptocom',
    name: 'Crypto.com',
    color: '#002D74',
    keywords: ['crypto.com', 'cryptocom'],
    path: 'M12 2L2 7v10l10 5 10-5V7L12 2zm0 3.2L18.5 9 12 12.8 5.5 9 12 5.2zm-4.5 5.5l4.5 2.8 4.5-2.8v4.6l-4.5 2.8-4.5-2.8v-4.6z',
  },
  {
    slug: 'traderepublic',
    name: 'Trade Republic',
    color: '#000000',
    keywords: ['traderepublic', 'trade republic', 'traderepublic.com'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-3 5h6v2H9V7zm0 4h6v2H9v-2zm0 4h4v2H9v-2z',
  },
  {
    slug: 'scalable',
    name: 'Scalable Capital',
    color: '#003057',
    keywords: ['scalable', 'scalable capital', 'scalable.capital'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 4h2v12h-2V6zm-4 4h2v8H9v-8z',
  },
  {
    slug: 'n26',
    name: 'N26',
    color: '#36A18B',
    keywords: ['n26', 'n26.com'],
    path: 'M6 6h2.5l5.5 8V6H17v12h-2.5L9 10v8H6V6z',
  },
  {
    slug: 'deutschebank',
    name: 'Deutsche Bank',
    color: '#0018A8',
    keywords: ['deutsche bank', 'deutsche-bank.de', 'deutschebank'],
    path: 'M3 3h18v18H3V3zm2.5 15.5h13V5.5h-13v13zm10.7-11.2l-8 9.5 1.5 1.2 8-9.5-1.5-1.2z',
  },
  {
    slug: 'commerzbank',
    name: 'Commerzbank',
    color: '#FFD700',
    keywords: ['commerzbank', 'commerzbank.de'],
    path: 'M12 2L3 20h18L12 2zm0 4.5L17.5 17h-11L12 6.5z',
  },
  {
    slug: 'ing',
    name: 'ING',
    color: '#FF6200',
    keywords: ['ing', 'ing.de', 'ing-diba', 'ing diba'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.5 14h-9v-2h9v2zm0-3h-9v-2h9v2zm0-3h-9V8h9v2z',
  },
  {
    slug: 'dkb',
    name: 'DKB',
    color: '#005082',
    keywords: ['dkb', 'dkb.de', 'deutsche kreditbank'],
    path: 'M4 6h4a4 4 0 0 1 0 8H6v4H4V6zm2 2v4h2a2 2 0 1 0 0-4H6zm10-2h4v12h-4l-3-5v5h-2V6h2v5l3-5z',
  },
  {
    slug: 'sparkasse',
    name: 'Sparkasse',
    color: '#FF0000',
    keywords: ['sparkasse', 'sparkasse.de', 'spk'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm4 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm-7 6.5a4 4 0 0 1 7-2.5h2a6 6 0 0 0-11 3.5v1.5h10v-2H9v-.5z',
  },
  {
    slug: 'volksbank',
    name: 'Volksbank Raiffeisenbank',
    color: '#003882',
    keywords: ['volksbank', 'raiffeisenbank', 'vr.de', 'volksbank.de'],
    path: 'M4 4h7v7H4V4zm9 0h7v7h-7V4zm-9 9h7v7H4v-7zm9 0h7v7h-7v-7z',
  },
  {
    slug: 'telekom',
    name: 'Deutsche Telekom',
    color: '#E20074',
    keywords: ['telekom', 'telekom.de', 't-online', 'magenta'],
    path: 'M4 5h16v3h-6.5v11h-3V8H4V5zm16 11a2 2 0 1 1-4 0 2 2 0 0 1 4 0z',
  },
  {
    slug: 'vodafone',
    name: 'Vodafone',
    color: '#E60000',
    keywords: ['vodafone', 'vodafone.de', 'vodafone.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3.5c2.8 0 5 2.2 5 5 0 3.3-3.2 6.5-5 8-1.8-1.5-5-4.7-5-8 0-2.8 2.2-5 5-5z',
  },
  {
    slug: 'o2',
    name: 'O2',
    color: '#0019A5',
    keywords: ['o2', 'o2online.de', 'telefonica'],
    path: 'M10 5a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0 2.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zm8 4.5h2v3h-2v-3zm0-5h2v3h-2V7z',
  },
  {
    slug: 'bahn',
    name: 'Deutsche Bahn',
    color: '#EC1B2D',
    keywords: ['deutsche bahn', 'bahn.de', 'db bahn', 'bahn'],
    path: 'M3 4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2H3zm2 3h4.5c2.5 0 4 1.2 4 3 0 1.2-.7 2.1-1.8 2.6 1.4.5 2.3 1.5 2.3 3 0 2-1.7 3.4-4.5 3.4H5V7zm3 2.2v2.6h1.5c1 0 1.8-.4 1.8-1.3 0-.9-.8-1.3-1.8-1.3H8zm0 4.6v2.8h1.8c1.1 0 2-.5 2-1.4 0-.9-.9-1.4-2-1.4H8zm6.5-6.8H19v10h-4.5V7z',
  },
  {
    slug: 'lufthansa',
    name: 'Lufthansa',
    color: '#05164D',
    keywords: ['lufthansa', 'lufthansa.com', 'miles & more'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm6.2 7.8l-7.8 2.2 4.2 4.8-.8.5-5.2-4.5-3.2.9-.6-.6 7.4-4.8 6-.5z',
  },
  {
    slug: 'bolt',
    name: 'Bolt',
    color: '#34D186',
    keywords: ['bolt', 'bolt.eu'],
    path: 'M14 2L5 13h5l-2 9 11-12h-5l2-8z',
  },
  {
    slug: 'flixbus',
    name: 'FlixBus',
    color: '#73D700',
    keywords: ['flixbus', 'flixbus.de', 'flixtrain'],
    path: 'M4 4h16v16H4V4zm3 3v10h2V9l4 5 4-5v8h2V7h-2.5L12 12.5 8.5 7H7z',
  },
  {
    slug: 'dhl',
    name: 'DHL',
    color: '#FFCC00',
    keywords: ['dhl', 'dhl.de', 'dhl.com', 'deutsche post'],
    path: 'M2 8h20l-3 8H2l3-8zm5 2l-1.5 4h2l1.5-4H7zm5 0l-1.5 4h2l1.5-4h-2zm5 0l-1.5 4h2l1.5-4h-2z',
  },
  {
    slug: 'fedex',
    name: 'FedEx',
    color: '#4D148C',
    keywords: ['fedex', 'fedex.com'],
    path: 'M2 7h8v2H5v2h4v2H5v4H2V7zm10 0h8v10h-8V7zm2 2v6h4V9h-4z',
  },
  {
    slug: 'ups',
    name: 'UPS',
    color: '#351C15',
    keywords: ['ups', 'ups.com'],
    path: 'M12 2L4 5.5v7c0 5.5 4.5 8.5 8 9.5 3.5-1 8-4 8-9.5v-7L12 2zm-4 7h2v5H8V9zm4 0h2v5h-2V9zm4 0h2v5h-2V9z',
  },
  {
    slug: 'dpd',
    name: 'DPD',
    color: '#DC0032',
    keywords: ['dpd', 'dpd.de', 'dpd.com'],
    path: 'M12 2L2 7.5v9L12 22l10-5.5v-9L12 2zm-4 6h4a3 3 0 0 1 0 6H8V8zm2 2v2h2a1 1 0 0 0 0-2h-2z',
  },
  {
    slug: 'hermes',
    name: 'Hermes',
    color: '#00A0E2',
    keywords: ['hermes', 'myhermes.de', 'myhermes'],
    path: 'M12 2L3 7v10l9 5 9-5V7l-9-5zm1 4.5l5 2.8v5.4l-5 2.8-5-2.8V9.3l5-2.8z',
  },
  {
    slug: 'tuta',
    name: 'Tuta',
    color: '#ED1C24',
    keywords: ['tuta', 'tutanota', 'tuta.com', 'tutanota.com'],
    path: 'M4 4h16v16H4V4zm2 2v2.5l6 4.5 6-4.5V6H6zm0 5v7h12v-7l-6 4.5L6 11z',
  },
  {
    slug: 'fastmail',
    name: 'Fastmail',
    color: '#204077',
    keywords: ['fastmail', 'fastmail.com'],
    path: 'M4 4h16v16H4V4zm3 4h10v2H7V8zm0 4h7v2H7v-2zm0 4h5v2H7v-2z',
  },
  {
    slug: 'yahoo',
    name: 'Yahoo',
    color: '#6001D2',
    keywords: ['yahoo', 'yahoo.com', 'ymail'],
    path: 'M3 4l4 7v7h3v-7l4-7h-3l-2.5 5L6 4H3zm13 8h2v5h-2v-5zm0 6h2v2h-2v-2z',
  },
  {
    slug: 'gmx',
    name: 'GMX',
    color: '#1C3B82',
    keywords: ['gmx', 'gmx.de', 'gmx.net'],
    path: 'M4 4h16v16H4V4zm3 8a3 3 0 1 0 6 0 3 3 0 0 0-6 0zm7-3h3v6h-3V9z',
  },
  {
    slug: 'webde',
    name: 'WEB.DE',
    color: '#FFCE00',
    keywords: ['web.de', 'webde'],
    path: 'M4 4h16v16H4V4zm2 4h3l2 4 2-4h3l-3.5 7h-3L6 8zm10 3h2v4h-2v-4z',
  },
  {
    slug: 'sendgrid',
    name: 'SendGrid',
    color: '#1A82E2',
    keywords: ['sendgrid', 'sendgrid.com', 'twilio sendgrid'],
    path: 'M3 3h8v8H3V3zm10 0h8v8h-8V3zM3 13h8v8H3v-8zm10 0h8v8h-8v-8z',
  },
  {
    slug: 'resend',
    name: 'Resend',
    color: '#000000',
    keywords: ['resend', 'resend.com'],
    path: 'M4 4h16v16H4V4zm4 4v8h2.5v-3h3L16 16h3l-2.8-3.5A3.2 3.2 0 0 0 17 9.5C17 7.6 15.5 6 13.5 6H8zm2.5 2h3a1.5 1.5 0 0 1 0 3h-3V10z',
  },
  {
    slug: 'dashlane',
    name: 'Dashlane',
    color: '#0E353D',
    keywords: ['dashlane', 'dashlane.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 14h-2v-2h2v2zm0-4h-2V7h2v5z',
  },
  {
    slug: 'keepass',
    name: 'KeePass',
    color: '#279C27',
    keywords: ['keepass', 'keepassxc', 'keepass.info'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3.5 10a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0zm-3.5-1.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z',
  },
  {
    slug: 'nordvpn',
    name: 'NordVPN',
    color: '#4687FF',
    keywords: ['nordvpn', 'nordvpn.com', 'nordaccount', 'nordpass'],
    path: 'M12 2L2 19.5h20L12 2zm0 4.5l6.5 11h-13L12 6.5z',
  },
  {
    slug: 'expressvpn',
    name: 'ExpressVPN',
    color: '#DA3940',
    keywords: ['expressvpn', 'expressvpn.com'],
    path: 'M12 2L2 22h20L12 2zm0 5l6 13H6l6-13z',
  },
  {
    slug: 'surfshark',
    name: 'Surfshark',
    color: '#1789FC',
    keywords: ['surfshark', 'surfshark.com'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 5h2v6h-2V7zm0 8h2v2h-2v-2z',
  },
  {
    slug: 'mullvad',
    name: 'Mullvad VPN',
    color: '#FFB800',
    keywords: ['mullvad', 'mullvad.net'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm-1 4h2v8l4-3 1 1.5-6 4.5-6-4.5 1-1.5 4 3V6z',
  },
  {
    slug: 'wikipedia',
    name: 'Wikipedia',
    color: '#000000',
    keywords: ['wikipedia', 'wikipedia.org', 'wikimedia'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm3.5 14.5l-1.5-4-1.5 4h-2l-2-7h2l1 4.5 1.5-4.5h2l1.5 4.5 1-4.5h2l-2 7h-2z',
  },
  {
    slug: 'patreon',
    name: 'Patreon',
    color: '#FF424D',
    keywords: ['patreon', 'patreon.com'],
    path: 'M15.386.5C10.665.5 6.83 4.335 6.83 9.056c0 4.67 3.835 8.506 8.556 8.506 4.67 0 8.506-3.836 8.506-8.506C23.892 4.335 20.056.5 15.386.5zM0 23.5h4.27V.5H0v23z',
  },
  {
    slug: 'kickstarter',
    name: 'Kickstarter',
    color: '#05CE78',
    keywords: ['kickstarter', 'kickstarter.com'],
    path: 'M4 4h5v6l4-6h6l-6 8 6 8h-6l-4-6v6H4V4z',
  },
  {
    slug: 'substack',
    name: 'Substack',
    color: '#FF6719',
    keywords: ['substack', 'substack.com'],
    path: 'M22.539 8.242H1.46V5.406h21.08v2.836zM1.46 10.812V24L12 18.11 22.54 24V10.812H1.46zM22.54 0H1.46v2.836h21.08V0z',
  },
  {
    slug: 'medium',
    name: 'Medium',
    color: '#000000',
    keywords: ['medium', 'medium.com'],
    path: 'M13.54 12a6.8 6.8 0 0 1-6.77 6.82A6.8 6.8 0 0 1 0 12a6.8 6.8 0 0 1 6.77-6.82A6.8 6.8 0 0 1 13.54 12zm7.42 0c0 3.54-1.51 6.42-3.38 6.42-1.87 0-3.39-2.88-3.39-6.42s1.52-6.42 3.39-6.42c1.87 0 3.38 2.88 3.38 6.42M24 12c0 3.17-.53 5.75-1.19 5.75-.66 0-1.19-2.58-1.19-5.75s.53-5.75 1.19-5.75C23.47 6.25 24 8.83 24 12z',
  },
  {
    slug: 'atlassian',
    name: 'Atlassian',
    color: '#0052CC',
    keywords: ['atlassian', 'atlassian.com', 'atlassian.net'],
    path: 'M11.668 12.33c-.31.39-.77.62-1.27.62H5.166l4.632-7.228a1.6 1.6 0 0 1 2.37.132l4.134 5.253a9.42 9.42 0 0 0-4.634 1.223zm.664 1.34c.31-.39.77-.62 1.27-.62h5.232l-4.632 7.228a1.6 1.6 0 0 1-2.37-.132l-4.134-5.253a9.42 9.42 0 0 0 4.634-1.223z',
  },
  {
    slug: 'mercedes',
    name: 'Mercedes-Benz',
    color: '#000000',
    keywords: ['mercedes', 'mercedes-benz', 'mercedes.de', 'daimler'],
    path: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 1.5l1.6 7.4 6.7 3.5-6.8-.7L12 20.5l-1.5-6.8-6.8.7 6.7-3.5L12 3.5z',
  },
  {
    slug: 'traefik',
    name: 'Traefik',
    color: '#24A1C1',
    keywords: ['traefik', 'traefik.io', 'traefik proxy', 'containous'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 14.5l-4-3 1.5-2 2.5 1.5 4-4.5 1.5 1.5-5.5 6.5z',
  },
  {
    slug: 'haproxy',
    name: 'HAProxy',
    color: '#106DA7',
    keywords: ['haproxy', 'haproxy.org', 'haproxy.com', 'haproxy enterprise'],
    path: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 5h2v4h4v2h-4v4h-2v-4H7v-2h4V7z',
  },
  {
    slug: 'cargo',
    name: 'Cargo',
    color: '#DEA584',
    keywords: ['cargo', 'crates.io', 'rust-cargo', 'cargo package manager'],
    path: 'M12 2L2 7v10l10 5 10-5V7L12 2zm0 2.8l7 3.5v7.4l-7 3.5-7-3.5V8.3l7-3.5zM7 10h10v2H7v-2zm0 4h10v2H7v-2z',
  },
  {
    slug: 'java',
    name: 'Java',
    color: '#007396',
    keywords: ['java', 'java.com', 'oracle java', 'jdk', 'jre', 'openjdk'],
    path: 'M8.851 18.56s-.917.534.667.7c1.964.205 3.327.185 5.76-.231 0 0 .914.583 1.954.98-4.227 1.835-10.42.271-8.381-1.449zm-1.077-2.613s-1.042.75.542.959c2.392.313 4.938.271 7.85-.292 0 0 .542.417 1.354.729-5.188 1.959-11.959.396-9.746-1.396zm10.459-3.959c1.688 1.896-.75 3.75-.75 3.75s1.979-.979.875-2.688c-1.063-1.646-2.521-2.458-2.521-2.458s.708.396 2.396 1.396zm-9.38-2.229s-1.25 1.042.417 1.25c2.854.354 5.958.333 9.479-.375 0 0 .375.479 1.083.75-5.958 2.083-13.042.417-10.979-1.625zm11.23-2.667s.646.604-.813 1.542c-2.167 1.396-4.521 1.771-4.521 1.771s.896-.333 1.833-.813c1.729-.896 3.501-2.5 3.501-2.5zm-3.646-4.833s2.208 1.771-1.729 4.875c-3.188 2.521-5.188 3.813-8.875 1.979 0 0 2.875.458 5.75-.979 3.354-1.667 4.854-5.875 4.854-5.875zm-3.583-2.271s2.833 2.375-2.271 6.479c-4.083 3.271-7.25 1.938-7.25 1.938s3.479.521 6.563-1.688c2.958-2.125 2.958-6.729 2.958-6.729z',
  },
  {
    slug: 'csharp',
    name: 'C#',
    color: '#239120',
    keywords: ['csharp', 'c#', 'dotnet', '.net'],
    path: 'M11.99 0C5.37 0 0 5.37 0 12s5.37 12 11.99 12C18.62 24 24 18.63 24 12S18.62 0 11.99 0zm0 21.6c-5.3 0-9.6-4.3-9.6-9.6s4.3-9.6 9.6-9.6 9.6 4.3 9.6 9.6-4.3 9.6-9.6 9.6zm-1.8-6.6h3.6v1.8h-3.6v-1.8zm0-7.2h3.6v1.8h-3.6V7.8zm0 3.6h3.6v1.8h-3.6v-1.8z',
  },
]

// 3. LOAD SIMPLE-ICONS (From Cache or Fetch Online)
let simpleIcons = []
if (fs.existsSync(cachePath)) {
  simpleIcons = JSON.parse(fs.readFileSync(cachePath, 'utf8'))
} else {
  console.log('Fetching simple-icons to initialize cache...')
  const text = await fetch('https://cdn.jsdelivr.net/npm/simple-icons@16.29.0/index.js').then((r) => r.text())
  const data = await fetch('https://cdn.jsdelivr.net/npm/simple-icons@16.29.0/data/simple-icons.json').then((r) => r.json())
  const m = { exports: {} }
  new Function('module', 'exports', text)(m, m.exports)

  for (const item of data) {
    const icon = Object.values(m.exports).find((i) => i.slug === item.slug)
    if (icon && icon.path) {
      const aka = item.aliases?.aka || []
      const dup = item.aliases?.dup ? item.aliases.dup.map((d) => d.title) : []
      const loc = item.aliases?.loc ? Object.values(item.aliases.loc) : []
      simpleIcons.push({
        slug: item.slug,
        title: item.title,
        hex: item.hex,
        source: item.source || '',
        aliases: Array.from(new Set([...aka, ...dup, ...loc])),
        path: icon.path,
      })
    }
  }
  fs.writeFileSync(cachePath, JSON.stringify(simpleIcons), 'utf8')
}

const simpleIconsMap = new Map(simpleIcons.map((i) => [i.slug, i]))

// 4. POPULATE BRAND MAP
const allBrandsMap = new Map()

// A. Insert all custom NEW_BRANDS
for (const nb of NEW_BRANDS) {
  allBrandsMap.set(nb.slug, {
    slug: nb.slug,
    name: nb.name,
    color: nb.color,
    keywords: Array.from(new Set(nb.keywords)),
    path: nb.path,
  })
}

const FORBIDDEN_KEYWORDS = new Set([
  'community', 'forum', 'lotto', 'hessen', 'huber', 'spedition', 'transporte',
  'auditorium', 'tickets', 'fantasy', 'football', 'league', 'megaport', 'network',
  'thunderbolt', 'display', 'fake', 'bank', 'scam', 'site', 'phishing', 'attack',
  'attacker', 'evil', 'unknown', 'mystery', 'phish', 'local',
  'app', 'apps', 'web', 'net', 'org', 'com', 'dev', 'code', 'data', 'free',
  'open', 'home', 'work', 'play', 'game', 'games', 'store', 'star', 'plus',
  'pro', 'one', 'pay', 'mail', 'link', 'chat', 'live', 'view', 'hub',
  'run', 'base', 'smart', 'fast', 'time', 'tool', 'tools', 'test', 'tests',
  'auth', 'login', 'user', 'host', 'hosts', 'node', 'nodes', 'core', 'flow',
  'byte', 'bits', 'line', 'space', 'spaces', 'share', 'file', 'files', 'docs',
  'drive', 'sync', 'team', 'teams', 'group', 'safe', 'lock', 'key', 'pass',
  'post', 'feed', 'read', 'book', 'news', 'wire', 'edge', 'cast', 'wave',
  'drop', 'next', 'easy', 'best', 'auto', 'real', 'true', 'fine', 'good',
  'cool', 'super', 'mini', 'micro', 'nano', 'cloud', 'server', 'box',
])

function getSourceDomain(sourceUrl) {
  if (!sourceUrl || !sourceUrl.startsWith('http')) return ''
  try {
    const u = new URL(sourceUrl)
    const h = u.hostname.toLowerCase().replace(/^www\./, '')
    if (
      h.includes('github.') ||
      h.includes('gitlab.') ||
      h.includes('wikipedia.') ||
      h.includes('wikimedia.') ||
      h.includes('twitter.') ||
      h.includes('medium.') ||
      h.includes('youtube.') ||
      h.includes('facebook.') ||
      h.includes('cryptologos.')
    ) {
      return ''
    }
    return h
  } catch {
    return ''
  }
}

// B. Ensure ALL original brands from BRAND_META are in allBrandsMap
for (const [slug, meta] of Object.entries(BRAND_META)) {
  if (allBrandsMap.has(slug)) continue
  const icon = simpleIconsMap.get(slug)
  if (!icon) continue

  const sourceDomain = getSourceDomain(icon.source)
  const keywordsSet = new Set()
  keywordsSet.add(slug)
  keywordsSet.add(`${slug}.com`)
  if (sourceDomain) keywordsSet.add(sourceDomain)
  if (meta.keywords) {
    for (const kw of meta.keywords) keywordsSet.add(kw.toLowerCase())
  }
  for (const al of icon.aliases || []) {
    const alLower = al.toLowerCase()
    if (!FORBIDDEN_KEYWORDS.has(alLower) && alLower.length > 2 && !/[^a-z0-9\s.-]/.test(alLower)) {
      keywordsSet.add(alLower)
    }
  }

  const cleanedKeywords = Array.from(keywordsSet).filter((kw) => {
    const lower = kw.toLowerCase()
    return !FORBIDDEN_KEYWORDS.has(lower) && lower.length >= 2
  })

  allBrandsMap.set(slug, {
    slug,
    name: icon.title,
    color: meta.color || (icon.hex ? `#${icon.hex}` : 'currentColor'),
    keywords: cleanedKeywords,
    path: icon.path,
  })
}

// C. Ensure all critical mustHaveSlugs are in allBrandsMap
const mustHaveSlugs = new Set([
  'nginx', 'apache', 'caddy', 'proxmox', 'portainer', 'wireguard', 'openvpn', 'tailscale', 'zerotier',
  'prometheus', 'grafana', 'netdata', 'influxdb', 'docker', 'kubernetes', 'podman', 'helm', 'ansible', 'terraform',
  'postgresql', 'mysql', 'mariadb', 'redis', 'mongodb', 'sqlite', 'scylladb', 'cockroachlabs', 'clickhouse', 'supabase',
  'npm', 'yarn', 'pnpm', 'bun', 'composer', 'gradle', 'nuget', 'pypi',
  'python', 'rust', 'go', 'typescript', 'javascript', 'php', 'ruby', 'cplusplus',
  'bitcoin', 'ethereum', 'dogecoin',
])

for (const slug of mustHaveSlugs) {
  if (allBrandsMap.has(slug)) continue
  const icon = simpleIconsMap.get(slug)
  if (!icon) continue

  const sourceDomain = getSourceDomain(icon.source)
  const keywordsSet = new Set()
  keywordsSet.add(slug)
  keywordsSet.add(`${slug}.com`)
  if (sourceDomain) keywordsSet.add(sourceDomain)
  for (const al of icon.aliases || []) {
    const alLower = al.toLowerCase()
    if (!FORBIDDEN_KEYWORDS.has(alLower) && alLower.length > 2 && !/[^a-z0-9\s.-]/.test(alLower)) {
      keywordsSet.add(alLower)
    }
  }

  // Specific keyword enrichments
  if (slug === 'go') {
    keywordsSet.add('golang')
    keywordsSet.add('golang.org')
    keywordsSet.add('go language')
  }
  if (slug === 'rust') {
    keywordsSet.add('rust-lang.org')
    keywordsSet.add('rust-lang')
  }
  if (slug === 'typescript') {
    keywordsSet.add('typescriptlang.org')
    keywordsSet.add('ts')
  }
  if (slug === 'python') {
    keywordsSet.add('python.org')
    keywordsSet.add('py')
  }
  if (slug === 'nginx') {
    keywordsSet.add('nginx.org')
  }
  if (slug === 'mariadb') {
    keywordsSet.add('mariadb.org')
  }
  if (slug === 'influxdb') {
    keywordsSet.add('influxdata.com')
  }
  if (slug === 'netdata') {
    keywordsSet.add('netdata.cloud')
  }
  if (slug === 'yarn') {
    keywordsSet.add('yarnpkg.com')
  }
  if (slug === 'dogecoin') {
    keywordsSet.add('doge')
  }
  if (slug === 'proxmox') {
    keywordsSet.add('proxmox ve')
    keywordsSet.add('pve')
  }

  const cleanedKeywords = Array.from(keywordsSet).filter((kw) => {
    const lower = kw.toLowerCase()
    return !FORBIDDEN_KEYWORDS.has(lower) && lower.length >= 2
  })

  allBrandsMap.set(slug, {
    slug,
    name: icon.title,
    color: icon.hex ? `#${icon.hex}` : 'currentColor',
    keywords: cleanedKeywords,
    path: icon.path,
  })
}

// D. Score and rank remaining candidates from simpleIcons up to TARGET_BRAND_COUNT
const priorityTerms = [
  'db', 'database', 'sql', 'nosql', 'data', 'analytics', 'bi', 'etl',
  'cloud', 'server', 'hosting', 'host', 'vps', 'compute', 'storage', 's3',
  'docker', 'container', 'k8s', 'kubernetes', 'devops', 'ci', 'cd', 'deploy',
  'git', 'repo', 'code', 'ide', 'editor', 'terminal', 'shell', 'cli',
  'api', 'rest', 'graphql', 'grpc', 'webhook', 'http', 'proxy', 'dns',
  'auth', 'oauth', 'jwt', 'security', 'sec', 'ssl', 'tls', 'vpn', 'shield',
  'monitor', 'metrics', 'log', 'trace', 'alert', 'status', 'uptime',
  'js', 'ts', 'py', 'rust', 'golang', 'php', 'ruby', 'java', 'framework',
  'pay', 'payment', 'bank', 'wallet', 'crypto', 'coin', 'token', 'fintech',
  'shop', 'store', 'ecommerce', 'cart', 'order', 'market', 'retail',
  'chat', 'msg', 'video', 'voice', 'stream', 'audio', 'music', 'media',
  'ai', 'ml', 'llm', 'model', 'gpt', 'bot', 'agent', 'nlp',
  'mail', 'email', 'smtp', 'newsletter', 'crm', 'helpdesk', 'support',
]

const scoredCandidates = []
for (const icon of simpleIcons) {
  if (allBrandsMap.has(icon.slug)) continue
  // Exclude claude since anthropic handles Claude
  if (icon.slug === 'claude') continue
  if (icon.slug.length <= 2 && icon.slug !== 'go') continue
  if (FORBIDDEN_KEYWORDS.has(icon.slug.toLowerCase())) continue
  if (!icon.path || icon.path.length < 20) continue

  const source = (icon.source || '').toLowerCase()
  const domain = getSourceDomain(source)
  const title = icon.title.toLowerCase()
  const slug = icon.slug.toLowerCase()

  let score = 0
  if (domain) score += 20
  for (const term of priorityTerms) {
    if (slug.includes(term) || title.includes(term) || source.includes(term)) {
      score += 8
    }
  }

  scoredCandidates.push({ icon, domain, score })
}

scoredCandidates.sort((a, b) => b.score - a.score || a.icon.title.localeCompare(b.icon.title))

const TARGET_BRAND_COUNT = 1000
const needed = TARGET_BRAND_COUNT - allBrandsMap.size
const selected = scoredCandidates.slice(0, needed)

for (const item of selected) {
  const icon = item.icon
  const slug = icon.slug
  const title = icon.title
  const color = icon.hex ? `#${icon.hex}` : 'currentColor'
  const domain = item.domain

  const keywordsSet = new Set()
  keywordsSet.add(slug)

  if (domain && !FORBIDDEN_KEYWORDS.has(domain)) {
    keywordsSet.add(domain)
  }

  keywordsSet.add(`${slug}.com`)
  if (
    domain &&
    (domain.endsWith('.org') ||
      domain.endsWith('.io') ||
      domain.endsWith('.dev') ||
      domain.endsWith('.net') ||
      domain.endsWith('.de') ||
      domain.endsWith('.cloud'))
  ) {
    keywordsSet.add(domain)
  }

  const titleLower = title.toLowerCase()
  if (!FORBIDDEN_KEYWORDS.has(titleLower) && titleLower.length > 2 && !/[^a-z0-9\s.-]/.test(titleLower)) {
    keywordsSet.add(titleLower)
  }

  const aliases = icon.aliases || []
  for (const al of aliases) {
    const alLower = al.toLowerCase()
    if (!FORBIDDEN_KEYWORDS.has(alLower) && alLower.length > 2 && !/[^a-z0-9\s.-]/.test(alLower)) {
      keywordsSet.add(alLower)
    }
  }

  const cleanedKeywords = Array.from(keywordsSet).filter((kw) => {
    const lower = kw.toLowerCase()
    return !FORBIDDEN_KEYWORDS.has(lower) && lower.length >= 2
  })

  allBrandsMap.set(slug, {
    slug,
    name: title,
    color,
    keywords: cleanedKeywords,
    path: icon.path,
  })
}

console.log(`Total assembled brands: ${allBrandsMap.size}`)

// Ensure all standalone SVGs are written
for (const brand of allBrandsMap.values()) {
  const filePath = path.join(brandsDir, `${brand.slug}.svg`)
  const svgContent = `<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>${brand.name}</title><path d="${brand.path}"/></svg>\n`
  fs.writeFileSync(filePath, svgContent, 'utf8')
}

// 5. PRIORITY SORTING & CODE GENERATION
const prioritySlugs = [
  'msm',
  'nbm',
  'otto',
  'amazon',
  'stripe',
  'seb-hosting',
  'nitrado',
  'discord',
  'backblaze',
  'google',
  'gmail',
  'github',
  'steam',
  'apple',
  'spotify',
  'netflix',
  'reddit',
  'protonmail',
  'teamspeak',
  'mumble',
  'skype',
  'signal',
  'viber',
  'matrix',
  'hetzner',
  'netcup',
  'strato',
  'ionos',
  'contabo',
  'zap-hosting',
  'g-portal',
  'azure',
  'googlecloud',
  'ovhcloud',
  'linode',
  'vultr',
  'scaleway',
  'digitalocean',
  'cloudflare',
  'hostinger',
  'godaddy',
  'namecheap',
  'bluehost',
  'oraclecloud',
  'alibabacloud',
  'fastly',
  'flyio',
  'render',
  'railway',
  'heroku',
  'wasabi',
  'minio',
  'storj',
  'nextcloud',
  'owncloud',
  'box',
  'mega',
  'pcloud',
  'synology',
  'onedrive',
  'dropbox',
  'googledrive',
  'cocacola',
  'fanta',
  'pepsi',
  'redbull',
  'mcdonalds',
  'burgerking',
  'starbucks',
  'lieferando',
  'mediamarkt',
  'saturn',
  'zalando',
  'aliexpress',
  'temu',
  'shein',
  'openai',
  'anthropic',
  'perplexity',
  'cursor',
  'paypal',
  'revolut',
  'n26',
  'traderepublic',
  'deutschebank',
  'sparkasse',
]

const sortedBrands = Array.from(allBrandsMap.values()).sort((a, b) => {
  const aIdx = prioritySlugs.indexOf(a.slug)
  const bIdx = prioritySlugs.indexOf(b.slug)
  if (aIdx !== -1 && bIdx !== -1) return aIdx - bIdx
  if (aIdx !== -1) return -1
  if (bIdx !== -1) return 1
  return a.name.localeCompare(b.name)
})

const tsCode = `import { KeyRound } from 'lucide-react'

export interface BrandInfo {
  name: string
  keywords: string[]
  svg: (props: { className?: string }) => JSX.Element
}

interface RawBrand {
  name: string
  keywords: string[]
  color: string
  path: string
}

const RAW_BRANDS: RawBrand[] = ${JSON.stringify(
  sortedBrands.map((b) => ({
    name: b.name,
    keywords: b.keywords,
    color: b.color,
    path: b.path,
  })),
  null,
  2
)}

/**
 * Erzeugt eine SVG-Rendering-Funktion für einen Brand.
 */
function createBrandComponent(brand: RawBrand): (props: { className?: string }) => JSX.Element {
  return ({ className = 'w-5 h-5' }) => (
    <svg viewBox="0 0 24 24" className={className} fill={brand.color} role="img" aria-label={brand.name}>
      <path d={brand.path} />
    </svg>
  )
}

/**
 * Vollständiger Katalog aller unterstützten Marken und Dienste (1000 Marken).
 */
export const BRAND_CATALOG: BrandInfo[] = RAW_BRANDS.map((raw) => ({
  name: raw.name,
  keywords: raw.keywords,
  svg: createBrandComponent(raw),
}))

/**
 * Extrahiert den reinen Hostnamen aus einer URL oder einem Domain-String.
 */
function extractHostname(input: string): string {
  let str = (input || '').trim().toLowerCase()
  if (!str) return ''
  try {
    if (str.includes('://')) {
      const url = new URL(str)
      return url.hostname
    }
  } catch {
    // Fallback bei unvollständigen URL-Eingaben
  }
  str = str.replace(/^[a-z]+:\\/\\//, '')
  if (str.includes('@')) {
    str = str.split('@').pop() || ''
  }
  str = str.split(/[/?#:]/)[0]
  return str.trim()
}

/**
 * Ermittelt das SLD-Label (Second-Level Domain) für Hostnamen,
 * inklusive Unterstützung internationaler Doppel-TLDs (.co.uk, .com.de, .gov.uk, etc.).
 */
function getDomainLabel(hostname: string): string {
  if (!hostname || !hostname.includes('.') || /^\\d+\\.\\d+\\.\\d+\\.\\d+$/.test(hostname)) return ''
  const parts = hostname.split('.').filter(Boolean)
  if (parts.length < 2) return ''
  const tld2 = parts.length >= 3 && ['co.uk', 'com.de', 'gov.uk', 'ac.uk', 'org.uk', 'com.br', 'co.jp'].includes(parts.slice(-2).join('.'))
  const sldIndex = tld2 ? parts.length - 3 : parts.length - 2
  return parts[sldIndex] || ''
}

/**
 * Prüft, ob ein Keyword als eigenständiges Wort in einem Freitext vorkommt
 * (keine Teilwort-Treffer und keine Bindestrich-Komposita wie fake-paypal).
 * Einbuchstabige Keywords (wie 'x') müssen den Text exakt treffen, um Fehlzuordnungen
 * wie "Server X" zu verhindern.
 */
function matchesWord(text: string, wordKw: string): boolean {
  const kwLower = wordKw.toLowerCase()
  if (kwLower.length === 1) {
    return text === kwLower
  }
  let idx = text.indexOf(kwLower)
  while (idx !== -1) {
    const prevChar = idx > 0 ? text[idx - 1] : ' '
    const nextChar = idx + kwLower.length < text.length ? text[idx + kwLower.length] : ' '
    const validPrev = !/[a-z0-9-]/.test(prevChar)
    const validNext = !/[a-z0-9-]/.test(nextChar)
    if (validPrev && validNext) {
      return true
    }
    idx = text.indexOf(kwLower, idx + 1)
  }
  return false
}

function isDomainLike(str: string): boolean {
  return str.includes('.') && !/\\s/.test(str)
}

/**
 * Erkennt betrügerische Domain-Muster (z. B. google.com.attacker.net oder fake-paypal.com),
 * erlaubt aber legitime Subdomains und selbstgehostete Server (z. B. ts3.voice.de, minio.mycluster.com).
 */
function isSpoofedDomain(hostname: string, brand: BrandInfo): boolean {
  if (!hostname) return false
  const sld = getDomainLabel(hostname)
  if (brand.keywords.includes(hostname) || (sld && brand.keywords.includes(sld))) {
    return false
  }

  // 1. Täuschende Subdomain mit Brand-Domain gefolgt von einem Punkt (z. B. google.com.attacker.net)
  for (const kw of brand.keywords) {
    if (kw.includes('.')) {
      if (hostname.includes(kw + '.') && !hostname.endsWith('.' + kw) && hostname !== kw) {
        return true
      }
    }
  }

  // 2. Bindestrich-Imitation in der SLD (z. B. fake-paypal.com, paypal-scam.net)
  if (sld) {
    for (const kw of brand.keywords) {
      if (!kw.includes('.') && kw.length >= 3) {
        if (sld !== kw && (sld.startsWith(kw + '-') || sld.endsWith('-' + kw) || sld.includes('-' + kw + '-'))) {
          return true
        }
      }
    }
  }
  return false
}

/**
 * Ermittelt das passende lokale Brand-Icon oder ein neutrales Fallback-Icon.
 * Verhindert Domain-Spoofing, Subdomain-Phishing und Teilwort-Fehltresore (False-Positives).
 */
export function getBrandIcon(serviceName: string, domainOrUrl = ''): (props: { className?: string }) => JSX.Element {
  const serviceTrimmed = (serviceName || '').trim()
  const serviceLower = serviceTrimmed.toLowerCase()

  const urlHost = extractHostname(domainOrUrl)
  const serviceHost = isDomainLike(serviceTrimmed) ? extractHostname(serviceTrimmed) : ''
  const hostname = urlHost || serviceHost
  const sld = getDomainLabel(hostname)

  let bestBrand: BrandInfo | null = null
  let bestScore = 0

  for (const brand of BRAND_CATALOG) {
    if (isSpoofedDomain(hostname, brand)) {
      continue
    }

    for (const kw of brand.keywords) {
      const kwLower = kw.toLowerCase()
      let score = 0

      if (kwLower.includes('.')) {
        // Domain-Keyword: Exakter Host-Match oder valider Subdomain-Match (*.paypal.com)
        if (hostname && (hostname === kwLower || hostname.endsWith('.' + kwLower))) {
          score = kwLower.length + 30
        }
      } else {
        // Markenname / Alias:
        // 1. In von Menschen vergebenem Dienstnamen (sofern keine reine Domain eingetippt wurde)
        if (serviceLower && !isDomainLike(serviceTrimmed)) {
          if (matchesWord(serviceLower, kwLower)) {
            score = Math.max(score, kwLower.length + 15)
          }
        }
        // 2. Exakter Treffer auf der registrierten Second-Level-Domain (z. B. www.otto.de -> sld 'otto')
        if (sld && sld === kwLower) {
          score = Math.max(score, kwLower.length + 20)
        }
      }

      if (score > bestScore) {
        bestScore = score
        bestBrand = brand
      }
    }
  }

  if (bestBrand) {
    return bestBrand.svg
  }

  // Neutrales Fallback
  return ({ className = 'w-5 h-5' }) => (
    <div className={'flex items-center justify-center rounded-lg bg-surface-container text-on-surface-variant ' + className}>
      <KeyRound className="w-3.5 h-3.5" />
    </div>
  )
}
`

fs.writeFileSync(catalogPath, tsCode, 'utf8')
console.log(`Successfully generated ${catalogPath} with ${sortedBrands.length} brands.`)
