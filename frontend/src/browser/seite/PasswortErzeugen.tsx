/**
 * Ein starkes Passwort für eine Registrierung oder einen Passwortwechsel. Setzt
 * der Nutzer den Fokus in ein eindeutig neues Passwortfeld, erzeugt der Browser
 * eines und setzt es ein; schickt er ab, speichert der Browser es einmal ohne
 * Rückfrage. Fokus oder Absenden per Skript der Seite lösen nichts davon aus.
 *
 * Bei gesperrtem Tresor geht es über den Posteingang (`tresorGesperrt.ts`):
 * dafür braucht der Browser weder das Master-Passwort noch einen Schlüssel des
 * Tresors. Erzeugt wird nur, wenn es danach auch gespeichert werden kann; ein
 * vorhandenes Passwort zu überschreiben fragt weiterhin.
 */
import { useEffect, useState, type ReactNode } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { hostVon } from '@/desktop/vault/hostVon'
import { generateSecurePassword } from '@/desktop/vault/vaultCrypto'
import { useVaultStore } from '@/desktop/vault/vaultStore'
import { toast } from '@/stores/toastStore'

import { speicherFrage, useFormulare } from '../services/formulare'
import { nativ } from '../services/nativ'
import { eingerichtet, speichern as gesperrtSpeichern } from '../services/tresorGesperrt'
import { Leiste } from './FormularRahmen'

const LEER = { benutzer: null, passwort: null }

/** Setzt ein erzeugtes Passwort ein; dasselbe wieder, wenn die Seite neu kam (Fehlversuch, zweiter Schritt). */
function einsetzen(tab: string, url: string) {
  const formulare = useFormulare.getState()
  const da = formulare.erzeugtFuer(tab, url)
  if (da && (da.passwort === null || da.eingesetzt)) return
  const passwort = da?.passwort ?? generateSecurePassword(20, true)
  // Vor dem Füllen merken: ein zweiter Lauf (StrictMode, Neuzeichnen) setzt nichts doppelt ein.
  formulare.erzeugtMerken(tab, url, passwort)
  nativ.tabFuellen(tab, url, { ...LEER, neu: passwort }).catch(() => undefined)
}

export function PasswortErzeugen({ tab, url, ersatz }: { tab: string; url: string; ersatz: ReactNode }) {
  const { t } = useTranslation()
  const offen = useVaultStore((s) => s.isUnlocked)
  const [gesperrtGeht, setGesperrtGeht] = useState<boolean | null>(null)
  const erzeugt = useFormulare((s) => s.erzeugtFuer(tab, url))
  const weg = useFormulare((s) => s.feldWeg)
  const kann = offen || gesperrtGeht === true
  const host = hostVon(url)

  useEffect(() => {
    if (!offen) void eingerichtet().then(setGesperrtGeht, () => setGesperrtGeht(false))
  }, [offen])
  useEffect(() => {
    if (kann) einsetzen(tab, url)
  }, [kann, tab, url])

  if (!offen && gesperrtGeht === null) return null
  if (!kann || !erzeugt?.passwort) return <>{ersatz}</>

  const eigenes = () => {
    useFormulare.getState().erzeugtMerken(tab, url, null)
    nativ.tabFuellen(tab, url, { ...LEER, neu: '' }).catch(() => undefined)
  }
  return (
    <Leiste text={t(offen ? 'browser.formular.erzeugen.eingesetzt' : 'browser.formular.erzeugen.eingesetztGesperrt', { host })} onSchliessen={() => weg(tab)}>
      <Button size="sm" variant="secondary" onClick={eigenes}>
        {t('browser.formular.erzeugen.eigenes')}
      </Button>
    </Leiste>
  )
}

/**
 * Speichert eine Anmeldung mit erzeugtem Passwort. Sie wird vorher aus dem
 * Store genommen, deshalb läuft das Speichern nur einmal. Was nicht ohne
 * Rückfrage geht (Überschreiben, Posteingang nicht eingerichtet, Fehler),
 * kommt als gewöhnliche Frage zurück.
 */
export async function erzeugtSpeichern(tab: string, t: TFunction): Promise<void> {
  const a = useFormulare.getState().abgeschicktNehmen(tab)
  if (!a) return
  const fragen = () => useFormulare.setState((s) => ({ abgeschickt: { ...s.abgeschickt, [tab]: s.abgeschickt[tab] ?? { ...a, erzeugt: false } } }))
  const host = hostVon(a.url)
  try {
    const tresor = useVaultStore.getState()
    if (tresor.isUnlocked) {
      const frage = speicherFrage(tresor.items, a)
      if (!frage) return
      if (frage.art === 'aendern') return fragen()
      await tresor.saveItem({ service: host, category: 'login', url: a.url, username: a.benutzer, password: a.passwort })
      toast.success(t('browser.formular.erzeugen.gespeichert', { host }))
    } else if (await eingerichtet()) {
      await gesperrtSpeichern(a.url, a.benutzer, a.passwort)
      toast.success(t('browser.formular.imEingang'))
    } else {
      fragen()
    }
  } catch {
    fragen()
  }
}

export function ErzeugtSpeichern({ tab }: { tab: string }) {
  const { t } = useTranslation()
  useEffect(() => {
    void erzeugtSpeichern(tab, t)
  }, [tab, t])
  return null
}
