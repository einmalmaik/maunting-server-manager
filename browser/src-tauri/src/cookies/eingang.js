// Der Teil des Cookie-Skripts, der autoconsent startet (cookies.rs). Davor
// stehen autoconsent selbst (`AutoConsent`, `filterCompactRules`), `SCHILD`
// ({ aktiv, ausnahmen }), `REGELN` als Text und `ZEICHEN`, der Ereignisname
// dieser Sitzung (seite.rs). Läuft in jedem Rahmen jeder
// Seite, vor den Skripten der Seite.

// Lehnt ab, nie zu: „Akzeptieren“ klickt autoconsent nur beim Zustimmen und
// in den höheren Stufen der Heuristik. Gemeldet wird nur eine gelungene
// Ablehnung für die Statistik der Startseite (`autoconsentDone`); ein Hinweis,
// der nur ausgeblendet wurde (`isCosmetic`), zählt nicht. Die übrigen
// Nachrichten fallen weg.
const KONFIG = {
  enabled: true,
  autoAction: 'optOut',
  isMainWorld: true,
  enablePrehide: true,
  enableCosmeticRules: true,
  enableGeneratedRules: true,
  enableHeuristicDetection: true,
  heuristicMode: 'reject',
  logs: { lifecycle: false, rulesteps: false, detectionsteps: false, evals: false, errors: false, messages: false, waits: false },
}

// Das Schild gilt je Seite: ein Rahmen fragt nach dem Host der obersten.
function obersterHost() {
  const ahnen = location.ancestorOrigins
  const herkunft = ahnen && ahnen.length > 0 ? ahnen[ahnen.length - 1] : location.origin
  if (!/^https?:\/\//.test(herkunft)) return null
  return new URL(herkunft).hostname.toLowerCase().replace(/^(www\.)+/, '')
}

// Wie `pausiert` in schild/mod.rs; dieselben Fälle in schildAusnahmen.faelle.json.
function pausiert(host, ausnahmen) {
  let rest = host
  for (;;) {
    if (ausnahmen.includes(rest)) return true
    const punkt = rest.indexOf('.')
    const eltern = rest.slice(punkt + 1)
    if (punkt < 0 || !eltern.includes('.')) return false
    rest = eltern
  }
}

// Eingefangen, bevor ein Skript der Seite sie ersetzen kann.
const ausloesen = window.dispatchEvent.bind(window)
const Ereignis = Event
function nachricht(m) {
  if (m && m.type === 'autoconsentDone' && !m.isCosmetic) ausloesen(new Ereignis(ZEICHEN))
  return Promise.resolve()
}

const host = obersterHost()
if (host && SCHILD.aktiv && !pausiert(host, SCHILD.ausnahmen)) {
  const regeln = filterCompactRules(JSON.parse(REGELN), { url: location.href, mainFrame: window === window.top })
  const consent = new AutoConsent(nachricht)
  consent.initialize(KONFIG, { autoconsent: [], compact: regeln })
}
