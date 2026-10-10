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

  // Was die Seite später überschreiben könnte, wird hier eingefangen, bevor
  // eines ihrer Skripte läuft. Sonst meldet sie ein verstecktes Feld als
  // sichtbar, liest die Nachricht des Browsers samt Zahlungsdaten mit oder
  // täuscht einen Klick vor. Aufgerufen wird nur über `anwenden`, nie über
  // `.call` (`Function.prototype.call` gehört der Seite).
  const anwenden = Reflect.apply
  const beschreibung = Object.getOwnPropertyDescriptor
  const getter = (vorlage, name) => {
    const d = vorlage && beschreibung(vorlage, name)
    return d && d.get
  }
  const lesenAls = JSON.parse
  const ohneVorlage = Object.setPrototypeOf
  const istListe = Array.isArray
  const zahl = Number
  const rechtecke = HTMLElement.prototype.getClientRects
  const rechteck = HTMLElement.prototype.getBoundingClientRect
  const sichtPruefen = HTMLElement.prototype.checkVisibility
  const stilVon = window.getComputedStyle
  const stilWert = CSSStyleDeclaration.prototype.getPropertyValue
  const anzahl = getter(window.DOMRectList && DOMRectList.prototype, 'length')
  const MASSE = ['width', 'height', 'right', 'bottom']
  const massGetter = MASSE.map((n) => getter(window.DOMRectReadOnly && DOMRectReadOnly.prototype, n))
  const rollX = getter(window, 'scrollX')
  const rollY = getter(window, 'scrollY')
  const daten = getter(MessageEvent.prototype, 'data')
  const aktivierung = navigator.userActivation
  const aktivLesen = getter(aktivierung && Object.getPrototypeOf(aktivierung), 'isActive')
  // Hat der Nutzer gerade geklickt oder getippt? Ein Fokus oder Absenden per
  // Skript zählt nicht. Skripte des Browsers laufen deshalb ohne Nutzergeste
  // (`ohne_geste` in `tabs/desktop/webview2.rs`).
  const nutzerAktiv = () => !!aktivLesen && anwenden(aktivLesen, aktivierung, []) === true
  // Echte Rechtecke über die eingefangenen Getter; die Attrappen der Tests
  // sind schlichte Objekte. Keine Methoden von `Array.prototype`: auch die
  // gehören der Seite.
  const ueber = (g, o, name) => {
    try {
      return anwenden(g, o, [])
    } catch (_) {
      return o[name]
    }
  }
  const mass = (r, i) => (massGetter[i] ? ueber(massGetter[i], r, MASSE[i]) : r[MASSE[i]])
  const lage = (g) => (g ? anwenden(g, window, []) : 0)

  // Beide Plattformen schicken Text (`PostWebMessageAsString`, Android
  // `postMessage`). Gelesen wird über die eingefangenen Funktionen in Objekte
  // ohne Prototyp: fragt der Code nach einem Feld, das fehlt, fände er sonst
  // einen Getter, den die Seite auf `Object.prototype` gelegt hat, und der
  // sähe die ganze Nachricht.
  function nachricht(e) {
    let d
    try {
      d = anwenden(daten, e, [])
    } catch (_) {
      d = e && e.data
    }
    if (typeof d !== 'string') return null
    try {
      return lesenAls(d, (_, v) => (v !== null && typeof v === 'object' && !istListe(v) ? ohneVorlage(v, null) : v))
    } catch (_) {
      return null
    }
  }
  const NEU = /regist|signup|sign-up|sign_up|join|create|erstell|confirm|repeat|wiederhol|bestätig|bestaetig/i
  const BENUTZER = /user|login|e-?mail|benutzer|konto|account|anmelde|identifier/i
  const BISHER = /current|old.?pass|altes|bisherig|aktuelles/i
  const REGISTRIEREN = /regist|sign ?up|(konto|account) (erstellen|anlegen)|create (an |your )?account|jetzt beitreten|join now/i
  const SENDEN = /anmeld|einlog|log ?in|sign ?in|sign ?up|weiter|next|continue|regist|erstell|create|submit|senden|bestätig/i

  const sichtbar = (el) => {
    const liste = anwenden(rechtecke, el, [])
    return (anzahl ? ueber(anzahl, liste, 'length') : liste.length) > 0 && !el.disabled && !el.readOnly
  }
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
      const d = nachricht(e)
      // Unter Android: der Rahmen steht nach einer Weiterleitung auf einer
      // gesperrten Seite (`rahmen_gesperrt`), die Anfrage hat das nicht gesehen.
      if (d && d.t === 'gesperrt') {
        window.stop()
        return window.location.replace('about:blank')
      }
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
      const meldung = json({ t: 'feld', passwort, neu: pws.some(istNeu), sicher: pws.some(istSicherNeu), aktiv: nutzerAktiv() })
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
    if (pw) meldung = json({ t: 'absenden', benutzer: name, passwort: wert.get.call(pw), neu: istNeu(pw), aktiv: nutzerAktiv() })
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
    anwenden(wert.set, el, [text])
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  }

  // In einer Auswahl die Option, deren Wert oder Text einem der Kandidaten entspricht.
  function waehlen(el, kandidaten) {
    const option = [...el.options].find((o) => kandidaten.includes(o.value.trim()) || kandidaten.includes(o.text.trim()))
    if (!option) return
    el.focus()
    anwenden(auswahlWert.set, el, [option.value])
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  }

  // Zahlungsdaten gehen nur in Felder, die man sieht: ein verstecktes Feld
  // neben dem sichtbaren würde sie sonst still mitnehmen.
  function wirklichSichtbar(el) {
    if (!sichtbar(el)) return false
    const r = anwenden(rechteck, el, [])
    // Echte Eingabefelder sind größer; ein 10 × 8 px kleines Feld sieht niemand als solches.
    if (!(mass(r, 0) >= 20 && mass(r, 1) >= 12 && mass(r, 2) + lage(rollX) > 0 && mass(r, 3) + lage(rollY) > 0)) return false
    if (sichtPruefen && !anwenden(sichtPruefen, el, [{ opacityProperty: true, visibilityProperty: true }])) return false
    const stil = anwenden(stilVon, window, [el])
    return anwenden(stilWert, stil, ['visibility']) === 'visible' && zahl(anwenden(stilWert, stil, ['opacity'])) >= 0.1
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
      if (el instanceof HTMLSelectElement) waehlen(el, istListe(text) ? text : [text])
      else if (istListe(text)) setzen(el, rolle === 'jahr' && !vierstellig ? text[1] : text[0])
      else setzen(el, text)
    }
  }

  // Übersetzen (`tabs/uebersetzung.rs`): die Seite gibt ihren Text nur auf
  // Anfrage des Browsers heraus, in Stücken, und bekommt nur Text zurück. Er
  // geht per `nodeValue` bzw. als Attribut hinein, nie als HTML. Was die Seite
  // inzwischen selbst geändert hat, bleibt, wie sie es will.
  const NICHT_UEBERSETZEN =
    'script,style,noscript,template,code,pre,kbd,samp,var,textarea,select,svg,math,[translate="no"],.notranslate,[contenteditable]:not([contenteditable="false"])'
  const UEBERSETZT_ATTRIBUTE = ['alt', 'placeholder', 'title', 'aria-label']
  const STUECK_TEXTE = 100
  const STUECK_ZEICHEN = 32 * 1024
  const TEXT_MAX = 2000
  const BUCHSTABE = /\p{L}/u
  // Je Ziel: Knoten, Attribut (null: Text), Original, Übersetzung.
  let arbeit = null

  function textSammeln() {
    const ziele = []
    const nehmen = (knoten, attribut, text) => {
      if (text && text.length <= TEXT_MAX && BUCHSTABE.test(text)) ziele.push([knoten, attribut, text, null])
    }
    const wurzel = document.body || document.documentElement
    const lauf = document.createTreeWalker(wurzel, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (k) => (k.nodeType === 1 && k.matches(NICHT_UEBERSETZEN) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    })
    for (let k = lauf.currentNode; k; k = lauf.nextNode()) {
      if (k.nodeType === 3) nehmen(k, null, k.nodeValue)
      else for (const a of UEBERSETZT_ATTRIBUTE) if (k.hasAttribute(a)) nehmen(k, a, k.getAttribute(a))
    }
    return ziele
  }

  const jetzt = (z) => (z[1] ? z[0].getAttribute(z[1]) : z[0].nodeValue)
  const schreiben = (z, wert) => (z[1] ? z[0].setAttribute(z[1], wert) : (z[0].nodeValue = wert))

  function naechstesStueck() {
    const texte = []
    let zeichen = 0
    arbeit.stueck = []
    while (arbeit.pos < arbeit.ziele.length && texte.length < STUECK_TEXTE) {
      const z = arbeit.ziele[arbeit.pos]
      if (texte.length && zeichen + z[2].length > STUECK_ZEICHEN) break
      texte.push(z[2])
      zeichen += z[2].length
      arbeit.stueck.push(z)
      arbeit.pos++
    }
    return texte
  }

  function zurueck() {
    if (arbeit) for (const z of arbeit.ziele) if (z[3] !== null && jetzt(z) === z[3]) schreiben(z, z[2])
    arbeit = null
  }

  function uebersetzen(d) {
    if (typeof d.nr !== 'number') return
    if (d.schritt === 'original') return zurueck()
    if (d.schritt === 'start') {
      zurueck()
      arbeit = { ziele: textSammeln(), pos: 0, stueck: [] }
    } else if (d.schritt === 'weiter') {
      if (!arbeit || !istListe(d.texte) || d.texte.length !== arbeit.stueck.length) return
      arbeit.stueck.forEach((z, i) => {
        const u = d.texte[i]
        if (typeof u === 'string' && jetzt(z) === z[2]) {
          schreiben(z, u)
          z[3] = u
        }
      })
    } else return
    const sprache = (document.documentElement.getAttribute('lang') || '').slice(0, 35).replace(/[^A-Za-z0-9_-]/g, '')
    senden(json({ t: 'texte', nr: d.nr, sprache, texte: naechstesStueck() }))
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

  // Gefüllt und übersetzt wird nur auf Anweisung des Browsers, nach einem Klick in seiner Leiste.
  bruecke.addEventListener('message', (e) => {
    const d = nachricht(e)
    if (d && d.t === 'datei' && android) return void dateiGeben(d.nr, d.url)
    if (d && d.t === 'uebersetzen') return void uebersetzen(d)
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
