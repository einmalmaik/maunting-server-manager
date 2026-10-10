/**
 * Die Leiste über der Seite für Anmeldungen: einfügen, ein starkes Passwort
 * vorschlagen, nach dem Absenden speichern. Sie liegt über der Seite, nicht
 * darauf, damit die Seite sichtbar bleibt (AGENTS.md Punkt 121).
 *
 * Ohne Kopplung gibt es keinen Tresor und keine Leiste. Bei gesperrtem
 * Tresor wird nichts eingefügt; gespeichert wird dann über den Posteingang
 * (`tresorGesperrt.ts`), wenn dieser Browser dafür eingerichtet ist.
 *
 * Was angeboten wird, schalten drei Einstellungen (Passwörter und Zahlungen).
 * Ein schon erzeugtes Passwort wird immer gespeichert: es steht sonst nirgends.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Button, Dropdown } from '@/Singra/UI'
import { hostVon } from '@/desktop/vault/hostVon'
import { generateSecurePassword } from '@/desktop/vault/vaultCrypto'
import { useVaultStore } from '@/desktop/vault/vaultStore'
import { toast } from '@/stores/toastStore'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { anmeldungenFuer, speicherFrage, useFormulare, type Abgeschickt, type Feld } from '../services/formulare'
import { nativ } from '../services/nativ'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import { useAktiverTab } from '../services/tabsStore'
import { eingerichtet, speichern as gesperrtSpeichern } from '../services/tresorGesperrt'
import { Leiste } from './FormularRahmen'
import { ErzeugtSpeichern, PasswortErzeugen } from './PasswortErzeugen'
import { ZahlungEinfuegen } from './ZahlungEinfuegen'

export function FormularLeiste() {
  const tab = useAktiverTab()
  const gekoppelt = istGekoppelt(useSitzung((s) => s.stand))
  const feld = useFormulare((s) => (tab ? s.feld[tab.id] : undefined))
  const abgeschickt = useFormulare((s) => (tab ? s.abgeschickt[tab.id] : undefined))
  const ausfuellen = useEinstellungenStore((s) => s.ausfuellen)
  const erzeugen = useEinstellungenStore((s) => s.erzeugen)
  const zahlungen = useEinstellungenStore((s) => s.zahlungen)
  if (!tab || !gekoppelt || tab.privat) return null
  if (abgeschickt?.erzeugt) return <ErzeugtSpeichern key={`g-${tab.id}`} tab={tab.id} />
  if (abgeschickt) return ausfuellen ? <Speichern key={`s-${tab.id}`} tab={tab.id} a={abgeschickt} /> : null
  if (feld?.zahlung) {
    const schluessel = `z-${tab.id}-${feld.url}-${feld.rahmen}-${feld.zahlung}`
    return zahlungen ? <ZahlungEinfuegen key={schluessel} tab={tab.id} url={feld.url} rahmen={feld.rahmen} art={feld.zahlung} /> : null
  }
  const einfuegen = feld && ausfuellen ? <Einfuegen key={`e-${tab.id}-${feld.url}`} tab={tab.id} feld={feld} /> : null
  if (feld?.sicher && feld.aktiv && feld.passwort && erzeugen) {
    return <PasswortErzeugen key={`p-${tab.id}-${feld.url}`} tab={tab.id} url={feld.url} ersatz={einfuegen} />
  }
  return einfuegen
}

function Einfuegen({ tab, feld }: { tab: string; feld: Feld }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const offen = useVaultStore((s) => s.isUnlocked)
  const items = useVaultStore((s) => s.items)
  const weg = useFormulare((s) => s.feldWeg)
  const passende = offen ? anmeldungenFuer(items, feld.url) : []
  const [wahl, setWahl] = useState<string | null>(passende[0]?.id ?? null)
  const host = hostVon(feld.url)

  const fuellen = async (werte: Parameters<typeof nativ.tabFuellen>[2]) => {
    try {
      await nativ.tabFuellen(tab, feld.url, werte)
      weg(tab)
    } catch {
      toast.error(t('browser.formular.gewechselt'))
    }
  }

  const vorschlagen = feld.neu && (
    <Button
      size="sm"
      variant="secondary"
      onClick={() => {
        const neu = generateSecurePassword(20, true)
        // Wie ein von selbst erzeugtes: nach dem Absenden ohne Rückfrage gespeichert.
        useFormulare.getState().erzeugtMerken(tab, feld.url, neu)
        void fuellen({ benutzer: null, passwort: null, neu })
      }}
    >
      {t('browser.formular.vorschlagen')}
    </Button>
  )

  if (!offen) {
    return (
      <Leiste text={feld.neu ? t('browser.formular.neuGesperrt', { host }) : t('browser.formular.gesperrt', { host })} onSchliessen={() => weg(tab)}>
        {vorschlagen}
        <Button size="sm" variant="ghost" onClick={() => navigate('/tresor')}>
          {t('browser.formular.entsperren')}
        </Button>
      </Leiste>
    )
  }
  const eintrag = passende.find((i) => i.id === wahl) ?? passende[0]
  if (!eintrag) {
    return feld.neu ? <Leiste text={t('browser.formular.neu', { host })} onSchliessen={() => weg(tab)}>{vorschlagen}</Leiste> : null
  }
  return (
    <Leiste text={t('browser.formular.einfuegenText', { host })} onSchliessen={() => weg(tab)}>
      {passende.length > 1 && (
        <div className="w-56">
          <Dropdown
            aria-label={t('browser.formular.konto')}
            value={eintrag.id}
            onChange={setWahl}
            options={passende.map((i) => ({ value: i.id, label: i.username || t('browser.formular.ohneName') }))}
          />
        </div>
      )}
      <Button size="sm" onClick={() => void fuellen({ benutzer: eintrag.username, passwort: eintrag.password, neu: null })}>
        {passende.length > 1 || !eintrag.username ? t('browser.formular.einfuegen') : t('browser.formular.einfuegenAls', { name: eintrag.username })}
      </Button>
      {vorschlagen}
    </Leiste>
  )
}

function Speichern({ tab, a }: { tab: string; a: Abgeschickt }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const offen = useVaultStore((s) => s.isUnlocked)
  const items = useVaultStore((s) => s.items)
  const weg = useFormulare((s) => s.abgeschicktWeg)
  const [kannGesperrt, setKannGesperrt] = useState<boolean | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  const host = hostVon(a.url)
  const frage = offen ? speicherFrage(items, a) : null

  useEffect(() => {
    if (!offen) void eingerichtet().then(setKannGesperrt, () => setKannGesperrt(false))
  }, [offen])
  // Steht genau so schon im Tresor: nichts zu fragen.
  useEffect(() => {
    if (offen && frage === null) weg(tab)
  }, [offen, frage, tab, weg])

  const ausfuehren = async (tun: () => Promise<void>, erfolg: string) => {
    setLaeuft(true)
    try {
      await tun()
      weg(tab)
      toast.success(erfolg)
    } catch {
      toast.error(t('browser.formular.speichernFehler'))
    } finally {
      setLaeuft(false)
    }
  }
  const name = a.benutzer || t('browser.formular.ohneName')

  if (offen) {
    if (!frage) return null
    const vault = useVaultStore.getState()
    return frage.art === 'aendern' ? (
      <Leiste text={t('browser.formular.aendernFrage', { name, host })} onSchliessen={() => weg(tab)}>
        <Button size="sm" disabled={laeuft} onClick={() => void ausfuehren(() => vault.aendern(frage.eintrag.id, { password: a.passwort }), t('browser.formular.geaendert'))}>
          {t('browser.formular.aendern')}
        </Button>
      </Leiste>
    ) : (
      <Leiste text={t('browser.formular.speichernFrage', { name, host })} onSchliessen={() => weg(tab)}>
        <Button
          size="sm"
          disabled={laeuft}
          onClick={() =>
            void ausfuehren(
              () => vault.saveItem({ service: host, category: 'login', url: a.url, username: a.benutzer, password: a.passwort }),
              t('browser.formular.gespeichert'),
            )
          }
        >
          {t('browser.formular.speichern')}
        </Button>
      </Leiste>
    )
  }
  if (kannGesperrt === null) return null
  if (!kannGesperrt) {
    return (
      <Leiste text={t('browser.formular.speichernEinrichten', { host })} onSchliessen={() => weg(tab)}>
        <Button size="sm" variant="secondary" onClick={() => navigate('/tresor')}>
          {t('browser.formular.entsperren')}
        </Button>
      </Leiste>
    )
  }
  return (
    <Leiste text={t('browser.formular.speichernGesperrt', { name, host })} onSchliessen={() => weg(tab)}>
      <Button size="sm" disabled={laeuft} onClick={() => void ausfuehren(() => gesperrtSpeichern(a.url, a.benutzer, a.passwort), t('browser.formular.imEingang'))}>
        {t('browser.formular.speichern')}
      </Button>
    </Leiste>
  )
}
