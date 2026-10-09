/**
 * Karte oder Bankkonto aus dem Tresor in ein Bezahlformular. Nie ohne
 * Bestätigung: Windows Hello bzw. die Bildschirmsperre unter Android, sonst
 * das Master-Passwort des Tresors. Nur über HTTPS; `tab_fuellen` prüft das
 * noch einmal und füllt nur auf derselben Herkunft.
 *
 * Liegt das Feld in einem Rahmen (`rahmen`, etwa Stripe), nennen Leiste und
 * Rückfrage dessen Anbieter, und gefüllt wird nur dorthin.
 */
import { useMemo, useState, type FormEvent } from 'react'
import { CreditCard, Landmark } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, Dropdown, PasswordInput } from '@/Singra/UI'
import { pruefeBiometrieVerfuegbar, verifiziereBiometrie } from '@/desktop/tauri'
import { hostVon } from '@/desktop/vault/hostVon'
import { useVaultStore } from '@/desktop/vault/vaultStore'
import { ohneSteuerzeichen, verdeckt, zahlungsmittel, type ZahlungAngaben } from '@/desktop/vault/zahlung'
import { toast } from '@/stores/toastStore'

import { useFormulare } from '../services/formulare'
import { nativ, type Fuellen } from '../services/nativ'
import { Leiste } from './FormularRahmen'

type Art = ZahlungAngaben['art']

/** Was an die Seite geht; Namen ohne Steuer- und Richtungszeichen. */
export function zahlungWerte(z: ZahlungAngaben): Fuellen {
  const leer = { benutzer: null, passwort: null, neu: null }
  const inhaber = z.inhaber ? ohneSteuerzeichen(z.inhaber) || null : null
  if (z.art === 'konto') return { ...leer, konto: { iban: z.iban, inhaber, bic: z.bic ?? null } }
  return { ...leer, karte: { nummer: z.nummer, inhaber, monat: z.monat ?? null, jahr: z.jahr ?? null, pruefnummer: z.pruefnummer ?? null } }
}

export function ZahlungEinfuegen({ tab, url, rahmen, art }: { tab: string; url: string; rahmen?: string; art: Art }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const offen = useVaultStore((s) => s.isUnlocked)
  const items = useVaultStore((s) => s.items)
  const weg = useFormulare((s) => s.feldWeg)
  const passende = useMemo(() => (offen ? zahlungsmittel(items).filter((i) => i.zahlung.art === art) : []), [offen, items, art])
  const [wahl, setWahl] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  // Wartet auf das Master-Passwort, wenn es keine Biometrie gibt.
  const [passwortFrage, setPasswortFrage] = useState<{ text: string; fertig: (ok: boolean) => void } | null>(null)
  const host = hostVon(url)
  const anbieter = rahmen ? hostVon(rahmen) : null
  const leiste = { name: t('browser.formular.zahlung.leiste'), symbol: art === 'konto' ? Landmark : CreditCard, onSchliessen: () => weg(tab) }

  if (!url.startsWith('https://')) return <Leiste {...leiste} text={t('browser.formular.zahlung.unsicher', { host })} />
  if (!offen) {
    return (
      <Leiste {...leiste} text={t(`browser.formular.zahlung.${art}Gesperrt`, { host })}>
        <Button size="sm" variant="ghost" onClick={() => navigate('/tresor')}>
          {t('browser.formular.entsperren')}
        </Button>
      </Leiste>
    )
  }
  const eintrag = passende.find((i) => i.id === wahl) ?? passende[0]
  if (!eintrag) return null

  const bestaetigen = async (text: string): Promise<boolean> => {
    if (await pruefeBiometrieVerfuegbar()) return verifiziereBiometrie(text)
    return new Promise((fertig) => setPasswortFrage({ text, fertig }))
  }

  const einfuegen = async () => {
    setLaeuft(true)
    try {
      const name = `${eintrag.service} ${verdeckt(eintrag.zahlung)}`
      const frage = anbieter ? t('browser.formular.zahlung.helloRahmen', { name, host, rahmen: anbieter }) : t('browser.formular.zahlung.hello', { name, host })
      if (!(await bestaetigen(frage))) {
        toast.info(t('browser.formular.zahlung.nichtBestaetigt'))
        return
      }
      // Nach der Rückfrage frisch lesen: inzwischen gesperrt oder geändert.
      const tresor = useVaultStore.getState()
      const z = tresor.isUnlocked ? tresor.items.find((i) => i.id === eintrag.id && !i.trashedAt)?.zahlung : undefined
      if (!z || z.art !== art) return
      await nativ.tabFuellen(tab, url, zahlungWerte(z), rahmen)
      weg(tab)
    } catch {
      toast.error(t('browser.formular.gewechselt'))
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <Leiste {...leiste} text={anbieter ? t(`browser.formular.zahlung.${art}Rahmen`, { host, rahmen: anbieter }) : t(`browser.formular.zahlung.${art}Text`, { host })}>
      {passende.length > 1 && (
        <div className="w-60">
          <Dropdown
            aria-label={t('browser.formular.zahlung.auswahl')}
            value={eintrag.id}
            onChange={setWahl}
            options={passende.map((i) => ({ value: i.id, label: `${i.service} · ${verdeckt(i.zahlung)}` }))}
          />
        </div>
      )}
      <Button size="sm" disabled={laeuft} onClick={() => void einfuegen()}>
        {passende.length > 1 ? t('browser.formular.einfuegen') : t('browser.formular.zahlung.einfuegenAls', { name: verdeckt(eintrag.zahlung) })}
      </Button>
      {passwortFrage && (
        <PasswortFrage
          text={passwortFrage.text}
          onFertig={(ok) => {
            passwortFrage.fertig(ok)
            setPasswortFrage(null)
          }}
        />
      )}
    </Leiste>
  )
}

function PasswortFrage({ text, onFertig }: { text: string; onFertig: (ok: boolean) => void }) {
  const { t } = useTranslation()
  const [passwort, setPasswort] = useState('')
  const [fehler, setFehler] = useState<string | undefined>()
  const [prueft, setPrueft] = useState(false)

  const pruefen = async (e: FormEvent) => {
    e.preventDefault()
    setPrueft(true)
    try {
      const vault = useVaultStore.getState()
      if (await vault.masterPasswortStimmt(passwort)) return onFertig(true)
      if (!useVaultStore.getState().isUnlocked) {
        toast.error(t('browser.formular.zahlung.gesperrtNachFehlern'))
        return onFertig(false)
      }
      setPasswort('')
      setFehler(t('browser.formular.zahlung.falsch'))
    } finally {
      setPrueft(false)
    }
  }

  return (
    <Dialog open onOpenChange={(offen) => !offen && onFertig(false)}>
      <DialogContent className="max-w-sm">
        <DialogHeader className="px-4 py-3 pr-14">
          <DialogTitle className="text-sm">{t('browser.formular.zahlung.passwortTitel')}</DialogTitle>
        </DialogHeader>
        <form onSubmit={(e) => void pruefen(e)} noValidate>
          <div className="space-y-3 p-4">
            <p className="text-body-sm text-on-surface-variant">{t('browser.formular.zahlung.passwortText', { text })}</p>
            <PasswordInput
              label={t('browser.formular.zahlung.passwort')}
              value={passwort}
              onChange={(e) => {
                setPasswort(e.target.value)
                setFehler(undefined)
              }}
              error={fehler}
              autoComplete="current-password"
              autoFocus
            />
          </div>
          <DialogFooter className="justify-end">
            <Button type="button" variant="ghost" onClick={() => onFertig(false)}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={prueft || !passwort}>
              {t('browser.formular.zahlung.bestaetigen')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
