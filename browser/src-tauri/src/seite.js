// Läuft in jedem Rahmen jeder Seite, vor allen Skripten der Seite (seite.rs).
(() => {
  // Die Brücke wird hier eingefangen und aus `window` entfernt, bevor ein
  // Skript der Seite läuft: die Seite sieht sie nie, und sie verriete die
  // Einbettung. Unter Windows ist es `chrome.webview` der WebView2, unter
  // Android `msbKanal` (`addWebMessageListener` in `Tab.kt`).
  const bruecke = (window.chrome && window.chrome.webview) || window.msbKanal
  const android = bruecke === window.msbKanal
  try {
    delete window.chrome.webview
  } catch (_) {}
  try {
    delete window.msbKanal
  } catch (_) {}
  if (!bruecke) return
  // Anmeldungen nur im obersten Rahmen, dessen Adresse der Browser kennt. In
  // Unterrahmen nur Zahlungsfelder: Kassen wie Stripe oder Adyen liegen in
  // iframes (`tabs::formular::Zahlrahmen`).
  const oben = window === window.top

  const senden = bruecke.postMessage.bind(bruecke)
  // Das Hallo öffnet den Rückweg: unter Android antwortet `Tab.kt` nur auf eine
  // Seite, die schon etwas geschickt hat, und ein Unterrahmen nennt so seine Herkunft.
  if (android || !oben) senden('{"t":"da"}')
  const wert = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
  const auswahlWert = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')
  const json = JSON.stringify
  const NEU = /regist|signup|sign-up|sign_up|join|create|erstell|confirm|repeat|wiederhol|bestätig|bestaetig/i
  const BENUTZER = /user|login|e-?mail|benutzer|konto|account|anmelde|identifier/i
  const BISHER = /current|old.?pass|altes|bisherig|aktuelles/i
  const REGISTRIEREN = /regist|sign ?up|(konto|account) (erstellen|anlegen)|create (an |your )?account|jetzt beitreten|join now/i
  const SENDEN = /anmeld|einlog|log ?in|sign ?in|sign ?up|weiter|next|continue|regist|erstell|create|submit|senden|bestätig/i

  const sichtbar = (el) => el.getClientRects().length > 0 && !el.disabled && !el.readOnly
  const merkmale = (el) => [el.name, el.id, el.getAttribute('autocomplete'), el.getAttribute('placeholder'), el.getAttribute('aria-label')].join(' ')
  const bereich = (el) => el.form || el.closest('form') || document
  // Eine Prüfnummer im Passwortfeld ist kein Passwort.
  const passwoerter = (b) => [...b.querySelectorAll('input[type=password]')].filter((el) => sichtbar(el) && !zahlRolle(el))

  // Zahlungsfelder: erst `autocomplete`, dann Name, Platzhalter und Beschriftung.
  const ZAHL_AUTOCOMPLETE = {
    'cc-number': 'nummer',
    'cc-name': 'name',
    'cc-exp': 'ablauf',
    'cc-exp-month': 'monat',
    'cc-exp-year': 'jahr',
    'cc-csc': 'pruefnummer',
  }
  const ZAHL_MUSTER = [
    ['iban', /iban/i],
    ['bic', /(^|[^a-z])bic|swift/i],
    ['kontoinhaber', /kontoinhaber|account.?holder|account.?owner/i],
    ['nummer', /card.?num|cc.?num|kartennummer/i],
    ['pruefnummer', /cvc|cvv|csc|security.?code|sicherheitscode|pr(ü|ue)fnummer|pr(ü|ue)fziffer/i],
    ['monat', /(exp|ablauf|valid|g(ü|ue)ltig).{0,12}(month|monat)|cc.?month/i],
    ['jahr', /(exp|ablauf|valid|g(ü|ue)ltig).{0,12}(year|jahr)|cc.?year/i],
    ['ablauf', /expir|exp.?date|ablauf|g(ü|ue)ltig bis|valid.?thru|mm\s*\/\s*(yy|jj)/i],
    ['name', /card.?holder|name.?on.?card|karteninhaber|cc.?name/i],
  ]
  const KONTO_ROLLEN = ['iban', 'bic', 'kontoinhaber']
  const ZAHL_TYPEN = ['text', 'tel', 'number', 'password', 'search', 'select-one']

  function zahlRolle(el) {
    if (!(el instanceof HTMLInputElement || el instanceof HTMLSelectElement) || !ZAHL_TYPEN.includes(el.type)) return null
    for (const teil of (el.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/)) {
      if (ZAHL_AUTOCOMPLETE[teil]) return ZAHL_AUTOCOMPLETE[teil]
    }
    const text = merkmale(el) + ' ' + [...(el.labels || [])].map((l) => l.textContent).join(' ')
    const treffer = ZAHL_MUSTER.find(([, muster]) => muster.test(text))
    return treffer ? treffer[0] : null
  }
  const zahlArt = (rolle) => (KONTO_ROLLEN.includes(rolle) ? 'konto' : 'karte')

  // Android liefert Nachrichten nur als Text.
  function nachricht(d) {
    if (typeof d !== 'string') return d
    try {
      return JSON.parse(d)
    } catch (_) {
      return null
    }
  }

  if (!oben) {
    let gemeldetHier = ''
    document.addEventListener(
      'focusin',
      (e) => {
        const rolle = zahlRolle(e.target)
        if (!rolle) return
        const meldung = json({ t: 'zahlung', art: zahlArt(rolle) })
        if (meldung !== gemeldetHier) senden((gemeldetHier = meldung))
      },
      true,
    )
    // Der Browser schickt Zahlungsdaten nur an Rahmen der Herkunft, die der
    // Nutzer bestätigt hat; ein Rahmen, der inzwischen woanders steht, füllt
    // nicht. Auch eine Fehlerseite nicht: verbietet das Ziel das Einbetten,
    // nennt die WebView2 als Absender die angefragte Adresse, die Herkunft
    // hier ist aber "null". Gefüllt wird der ganze Rahmen: bei Adyen liegt
    // jedes Feld in einem eigenen.
    bruecke.addEventListener('message', (e) => {
      const d = nachricht(e.data)
      if (d && d.t === 'fuellen' && (d.karte || d.konto) && d.herkunft === location.origin) zahlungFuellen(d, document)
    })
    return
  }

  function istBenutzer(el) {
    if (zahlRolle(el)) return false
    if (el.type === 'email') return true
    if (el.type !== 'text') return false
    return /username|email/.test(el.getAttribute('autocomplete') || '') || BENUTZER.test(merkmale(el))
  }

  // Der Knopf, den Enter auslöst (der erste Absendeknopf): sein Text sagt, wofür
  // das Formular ist. Ein zweiter Knopf „Registrieren“ neben „Anmelden“ zählt nicht.
  function hauptknopf(b) {
    if (b === document) return ''
    const k = b.querySelector('button:not([type]), button[type=submit], input[type=submit]')
    return k ? [k.textContent, k.value, merkmale(k)].join(' ') : ''
  }

  // Ein neues Passwort: Registrierung oder Wechsel, hier schlägt der Browser eines vor.
  // Das bisherige Passwort eines Wechselformulars ist nie neu: es wird nicht überschrieben.
  function istNeu(feld) {
    const autocomplete = feld.getAttribute('autocomplete') || ''
    if (autocomplete.includes('current-password') || BISHER.test(merkmale(feld))) return false
    if (autocomplete.includes('new-password')) return true
    const b = bereich(feld)
    const alle = passwoerter(b)
    // Bisheriges, neues, Wiederholung: das erste ist das bisherige.
    if (alle.length >= 3) return alle[0] !== feld
    if (alle.length === 2) return true
    if (REGISTRIEREN.test(hauptknopf(b))) return true
    return b !== document && NEU.test([b.id, b.name, b.getAttribute('action'), merkmale(feld)].join(' '))
  }

  // Eindeutig neu: so ausgezeichnet, mit Wiederholung oder mit „Registrieren“
  // als Hauptknopf. Nur dann erzeugt der Browser von selbst ein Passwort; ein
  // Anmeldeformular mit „create“ in der Adresse bekommt keins.
  function istSicherNeu(feld) {
    if (!istNeu(feld)) return false
    const b = bereich(feld)
    return (
      (feld.getAttribute('autocomplete') || '').includes('new-password') ||
      passwoerter(b).filter(istNeu).length >= 2 ||
      REGISTRIEREN.test(hauptknopf(b))
    )
  }

  // Das letzte Benutzerfeld vor dem Passwort, sonst das erste.
  function benutzerfeld(b, pw) {
    const felder = [...b.querySelectorAll('input')].filter((el) => sichtbar(el) && istBenutzer(el))
    const davor = pw ? felder.filter((el) => el.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING) : []
    return davor[davor.length - 1] || felder[0] || null
  }

  let zuletzt = null
  let zahlZuletzt = null
  let gemeldet = ''

  // Welcher Rahmen den Fokus hat: nur seine Herkunft, und nur, wenn er sich
  // sehen lässt und über HTTPS lädt. Ein Rahmen bekommt Zahlungsdaten nur,
  // wenn diese Meldung und seine eigene dieselbe Herkunft nennen.
  let rahmenGemeldet = null
  function rahmenMelden(el) {
    let herkunft = null
    if (el instanceof HTMLIFrameElement && wirklichSichtbar(el)) {
      try {
        herkunft = new URL(el.src, location.href).origin
      } catch (_) {}
      if (!herkunft || !herkunft.startsWith('https://')) herkunft = null
    }
    if (herkunft === rahmenGemeldet) return
    rahmenGemeldet = herkunft
    senden(json({ t: 'rahmen', herkunft }))
  }
  // Geht der Fokus in einen Rahmen, verliert das Fenster ihn; den Rahmen
  // nennt erst danach `activeElement`.
  window.addEventListener('blur', () => setTimeout(() => rahmenMelden(document.activeElement), 0))

  document.addEventListener(
    'focusin',
    (e) => {
      const el = e.target
      rahmenMelden(el)
      const rolle = zahlRolle(el)
      if (rolle) {
        zahlZuletzt = el
        const meldung = json({ t: 'zahlung', art: zahlArt(rolle) })
        if (meldung !== gemeldet) senden((gemeldet = meldung))
        return
      }
      if (!(el instanceof HTMLInputElement)) return
      const passwort = el.type === 'password'
      if (!passwort && !istBenutzer(el)) return
      zuletzt = el
      const pws = passwoerter(bereich(el))
      const meldung = json({ t: 'feld', passwort, neu: pws.some(istNeu), sicher: pws.some(istSicherNeu) })
      if (meldung === gemeldet) return
      gemeldet = meldung
      senden(meldung)
    },
    true,
  )

  // Nach dem Absenden: Passwort samt Benutzer, oder bei mehrstufiger
  // Anmeldung erst nur den Benutzer. Gespeichert wird erst nach Rückfrage.
  let abgeschickt = ''
  function absenden(b) {
    const pw = passwoerter(b).find((el) => wert.get.call(el))
    const feld = benutzerfeld(b, pw)
    const name = feld ? wert.get.call(feld).trim() : ''
    let meldung = ''
    if (pw) meldung = json({ t: 'absenden', benutzer: name, passwort: wert.get.call(pw), neu: istNeu(pw) })
    else if (name) meldung = json({ t: 'benutzer', wert: name })
    if (!meldung || meldung === abgeschickt) return
    abgeschickt = meldung
    senden(meldung)
  }

  document.addEventListener('submit', (e) => absenden(e.target), true)
  // Viele Seiten senden per Skript, ohne `submit`: Klick auf den Knopf oder Enter.
  document.addEventListener(
    'click',
    (e) => {
      const knopf = e.target instanceof Element && e.target.closest('button, input[type=submit], [role=button]')
      if (!knopf) return
      const sendet = knopf.type === 'submit' || SENDEN.test(knopf.textContent + ' ' + merkmale(knopf) + ' ' + (knopf.value || ''))
      if (sendet) absenden(bereich(knopf))
    },
    true,
  )
  document.addEventListener(
    'keydown',
    (e) => {
      const el = e.target
      if (e.key === 'Enter' && el instanceof HTMLInputElement && (el.type === 'password' || istBenutzer(el))) absenden(bereich(el))
    },
    true,
  )

  function setzen(el, text) {
    el.focus()
    wert.set.call(el, text)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  }

  // In einer Auswahl die Option, deren Wert oder Text einem der Kandidaten entspricht.
  function waehlen(el, kandidaten) {
    const option = [...el.options].find((o) => kandidaten.includes(o.value.trim()) || kandidaten.includes(o.text.trim()))
    if (!option) return
    el.focus()
    auswahlWert.set.call(el, option.value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  }

  // Zahlungsdaten gehen nur in Felder, die man sieht: ein verstecktes Feld
  // neben dem sichtbaren würde sie sonst still mitnehmen.
  function wirklichSichtbar(el) {
    if (!sichtbar(el)) return false
    const r = el.getBoundingClientRect()
    // Echte Eingabefelder sind größer; ein 10 × 8 px kleines Feld sieht niemand als solches.
    if (r.width < 20 || r.height < 12 || r.right + scrollX <= 0 || r.bottom + scrollY <= 0) return false
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false
    const stil = getComputedStyle(el)
    return stil.visibility === 'visible' && Number(stil.opacity) >= 0.1
  }

  // `b`: das Formular des Zahlungsfeldes mit dem Fokus, im Rahmen einer Kasse der ganze Rahmen.
  function zahlungFuellen(d, b) {
    const art = d.karte ? 'karte' : 'konto'
    const z = d.karte || d.konto
    const zwei = (n) => String(n).padStart(2, '0')
    for (const el of b.querySelectorAll('input, select')) {
      const rolle = zahlRolle(el)
      if (!rolle || zahlArt(rolle) !== art || !wirklichSichtbar(el)) continue
      const hinweis = (el.getAttribute('placeholder') || '') + ' ' + (el.maxLength > 0 ? el.maxLength : '')
      const vierstellig = /yyyy|jjjj/i.test(hinweis) || el.maxLength === 4 || el.maxLength >= 7
      let text = null
      if (rolle === 'nummer') text = z.nummer
      else if (rolle === 'name' || rolle === 'kontoinhaber') text = z.inhaber
      else if (rolle === 'pruefnummer') text = z.pruefnummer
      else if (rolle === 'iban') text = z.iban
      else if (rolle === 'bic') text = z.bic
      else if (rolle === 'monat' && z.monat) text = [zwei(z.monat), String(z.monat)]
      else if (rolle === 'jahr' && z.jahr) text = [String(z.jahr), zwei(z.jahr % 100)]
      else if (rolle === 'ablauf' && z.monat && z.jahr) text = zwei(z.monat) + '/' + (vierstellig ? z.jahr : zwei(z.jahr % 100))
      if (text == null) continue
      if (el instanceof HTMLSelectElement) waehlen(el, Array.isArray(text) ? text : [text])
      else if (Array.isArray(text)) setzen(el, rolle === 'jahr' && !vierstellig ? text[1] : text[0])
      else setzen(el, text)
    }
  }

  // Android lädt `blob:` und `data:` nicht selbst (`Herunterladen.kt`): die
  // Seite holt die Datei und gibt sie in Teilen weiter, jeden erst, wenn der
  // vorige geschrieben ist. Den Namen aus `download` kennt nur die Seite.
  const holen = window.fetch.bind(window)
  const quittungen = new Map()
  const TEIL = 256 * 1024
  const dateiGeben = async (nr, url) => {
    const a = [...document.querySelectorAll('a[download]')].find((x) => x.href === url)
    const name = a ? a.getAttribute('download') : ''
    const geben = (teil) => {
      senden(json({ t: 'teil', nr, name, ...teil }))
      return new Promise((weiter) => quittungen.set(nr, weiter))
    }
    try {
      const leser = (await holen(url)).body.getReader()
      for (;;) {
        const { done, value } = await leser.read()
        if (done) break
        for (let i = 0; i < value.length; i += TEIL) {
          const stueck = value.subarray(i, i + TEIL)
          let bin = ''
          for (let j = 0; j < stueck.length; j += 0x8000) bin += String.fromCharCode.apply(null, stueck.subarray(j, j + 0x8000))
          if (!(await geben({ daten: btoa(bin) }))) return leser.cancel()
        }
      }
      senden(json({ t: 'teil', nr, name, ende: true }))
    } catch (_) {
      senden(json({ t: 'teil', nr, fehler: true }))
    }
  }

  // Gefüllt wird nur auf Anweisung des Browsers, nach einem Klick in seiner Leiste.
  bruecke.addEventListener('message', (e) => {
    const d = nachricht(e.data)
    if (d && d.t === 'datei' && android) return void dateiGeben(d.nr, d.url)
    if (d && d.t === 'weiter' && quittungen.has(d.nr)) {
      const weiter = quittungen.get(d.nr)
      quittungen.delete(d.nr)
      return weiter(d.ok === true)
    }
    if (!d || d.t !== 'fuellen') return
    if (d.karte || d.konto) {
      if (zahlZuletzt && zahlZuletzt.isConnected) zahlungFuellen(d, bereich(zahlZuletzt))
      return
    }
    const b = zuletzt && zuletzt.isConnected ? bereich(zuletzt) : document
    const pws = passwoerter(b)
    if (typeof d.neu === 'string') {
      // Der Fokus bleibt, wo der Nutzer gerade tippt.
      const aktiv = document.activeElement
      for (const el of pws.filter(istNeu)) setzen(el, d.neu)
      if (aktiv instanceof HTMLElement && aktiv.isConnected) aktiv.focus()
      return
    }
    if (typeof d.benutzer === 'string') {
      const el = benutzerfeld(b, pws[0])
      if (el) setzen(el, d.benutzer)
    }
    if (typeof d.passwort === 'string' && pws[0]) setzen(pws[0], d.passwort)
  })
})()
