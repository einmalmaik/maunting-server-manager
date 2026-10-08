/**
 * Karte oder Bankkonto anlegen und bearbeiten. Gespeichert wird erst, wenn
 * Prüfsumme (Luhn, IBAN) und Ablauf stimmen: ein Zahlendreher würde sonst
 * später in ein Bezahlformular eingefügt.
 */
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, Dropdown, Input, PasswordInput, Textarea } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

import { fehlerText } from './tresorFehler'
import type { VaultItem } from './vaultEintrag'
import { useVaultStore } from './vaultStore'
import { ibanAusEingabe, ibanStimmt, luhnStimmt, nummerGruppiert, nurZiffern, ohneSteuerzeichen, ZAHLUNG_KATEGORIE, type ZahlungAngaben } from './zahlung'

export type ZahlungArt = ZahlungAngaben['art']

interface Formular {
  bezeichnung: string
  inhaber: string
  nummer: string
  monat: string
  jahr: string
  pruefnummer: string
  iban: string
  bic: string
  notizen: string
}

function ausEintrag(item: VaultItem | undefined): Formular {
  const z = item?.zahlung
  return {
    bezeichnung: item?.service ?? '',
    inhaber: z?.inhaber ?? '',
    nummer: z?.art === 'karte' ? nummerGruppiert(z.nummer) : '',
    monat: z?.art === 'karte' && z.monat ? String(z.monat) : '',
    jahr: z?.art === 'karte' && z.jahr ? String(z.jahr) : '',
    pruefnummer: z?.art === 'karte' ? (z.pruefnummer ?? '') : '',
    iban: z?.art === 'konto' ? z.iban.replace(/(.{4})(?=.)/g, '$1 ') : '',
    bic: z?.art === 'konto' ? (z.bic ?? '') : '',
    notizen: item?.notes ?? '',
  }
}

type Fehler = Partial<Record<keyof Formular, string>>

/** Aus dem Formular die Angaben, oder was daran falsch ist. */
export function angabenAusFormular(art: ZahlungArt, f: Formular): { angaben: ZahlungAngaben } | { fehler: Fehler } {
  const fehler: Fehler = {}
  const inhaber = ohneSteuerzeichen(f.inhaber) || undefined
  if (art === 'konto') {
    const iban = ibanAusEingabe(f.iban)
    const bic = f.bic.replace(/\s/g, '').toUpperCase() || undefined
    if (!ibanStimmt(iban)) fehler.iban = 'iban'
    if (bic && !/^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$/.test(bic)) fehler.bic = 'bic'
    return Object.keys(fehler).length ? { fehler } : { angaben: { art, iban, inhaber, bic } }
  }
  const nummer = nurZiffern(f.nummer)
  if (!luhnStimmt(nummer)) fehler.nummer = 'nummer'
  const monat = f.monat ? Number(f.monat) : undefined
  const jahr = f.jahr ? Number(f.jahr) : undefined
  if (!monat !== !jahr) fehler[monat ? 'jahr' : 'monat'] = 'ablauf'
  const pruefnummer = f.pruefnummer.trim() || undefined
  if (pruefnummer && !/^\d{3,4}$/.test(pruefnummer)) fehler.pruefnummer = 'pruefnummer'
  return Object.keys(fehler).length ? { fehler } : { angaben: { art, nummer, inhaber, monat, jahr, pruefnummer } }
}

const MONATE = Array.from({ length: 12 }, (_, i) => ({ value: String(i + 1), label: String(i + 1).padStart(2, '0') }))

function jahre(gewaehlt: string): { value: string; label: string }[] {
  const jetzt = new Date().getFullYear()
  const liste = Array.from({ length: 16 }, (_, i) => String(jetzt + i))
  // Eine abgelaufene Karte behält ihr Jahr in der Auswahl.
  if (gewaehlt && !liste.includes(gewaehlt)) liste.unshift(gewaehlt)
  return liste.map((j) => ({ value: j, label: j }))
}

interface Props {
  art: ZahlungArt
  /** Bearbeiten; ohne Eintrag wird neu angelegt. */
  item?: VaultItem
  onSchliessen: () => void
}

export function ZahlungDialog({ art, item, onSchliessen }: Props) {
  const { t } = useTranslation()
  const [anfang] = useState(() => ausEintrag(item))
  const [f, setF] = useState(anfang)
  const [fehler, setFehler] = useState<Fehler>({})
  const [laeuft, setLaeuft] = useState(false)
  const feld = (name: keyof Formular) => (wert: string) => {
    setF((alt) => ({ ...alt, [name]: wert }))
    setFehler((alt) => ({ ...alt, [name]: undefined }))
  }
  const meldung = (name: keyof Formular) => (fehler[name] ? t(`mss.vault.zahlung.fehler.${fehler[name]}`) : undefined)

  const schliessen = async () => {
    if (JSON.stringify(f) !== JSON.stringify(anfang)) {
      const verwerfen = await confirm({
        message: t('mss.vault.verwerfenFrage'),
        confirmText: t('mss.vault.verwerfen'),
        cancelText: t('mss.vault.weiterBearbeiten'),
        danger: true,
      })
      if (!verwerfen) return
    }
    onSchliessen()
  }

  const speichern = async (e: FormEvent) => {
    e.preventDefault()
    const ergebnis = angabenAusFormular(art, f)
    if ('fehler' in ergebnis) {
      setFehler(ergebnis.fehler)
      return
    }
    setLaeuft(true)
    try {
      const vault = useVaultStore.getState()
      const felder = { service: f.bezeichnung.trim(), notes: f.notizen.trim() || undefined, zahlung: ergebnis.angaben }
      if (item) await vault.aendern(item.id, felder)
      else await vault.saveItem({ ...felder, category: ZAHLUNG_KATEGORIE })
      toast.success(t('mss.vault.zahlung.gespeichert'))
      onSchliessen()
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.speichernFehlgeschlagen')))
    } finally {
      setLaeuft(false)
    }
  }

  const titel = t(item ? `mss.vault.zahlung.${art}Bearbeiten` : `mss.vault.zahlung.${art}Neu`)
  return (
    <Dialog open onOpenChange={(offen) => !offen && void schliessen()}>
      <DialogContent className="max-w-md max-h-[90dvh]">
        <DialogHeader className="px-4 py-3 pr-14">
          <DialogTitle className="text-sm">{titel}</DialogTitle>
        </DialogHeader>
        <form onSubmit={(e) => void speichern(e)} className="flex min-h-0 flex-1 flex-col" noValidate>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            <Input
              label={t('mss.vault.zahlung.bezeichnung')}
              value={f.bezeichnung}
              onChange={(e) => feld('bezeichnung')(e.target.value)}
              placeholder={t(`mss.vault.zahlung.${art}BezeichnungPlatzhalter`)}
              autoFocus
              required
              maxLength={100}
            />
            <Input
              label={t(`mss.vault.zahlung.${art}Inhaber`)}
              value={f.inhaber}
              onChange={(e) => feld('inhaber')(e.target.value)}
              autoComplete="off"
              maxLength={100}
            />
            {art === 'karte' ? (
              <KartenFelder f={f} feld={feld} fehler={meldung} />
            ) : (
              <>
                <Input
                  label={t('mss.vault.zahlung.iban')}
                  value={f.iban}
                  onChange={(e) => feld('iban')(e.target.value.toUpperCase())}
                  error={meldung('iban')}
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono"
                  required
                  maxLength={42}
                />
                <Input
                  label={t('mss.vault.zahlung.bic')}
                  value={f.bic}
                  onChange={(e) => feld('bic')(e.target.value.toUpperCase())}
                  error={meldung('bic')}
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono"
                  maxLength={11}
                />
              </>
            )}
            <Textarea label={t('mss.vault.notizBezeichnung')} rows={2} value={f.notizen} onChange={(e) => feld('notizen')(e.target.value)} />
          </div>
          <DialogFooter className="justify-end">
            <Button type="button" variant="ghost" onClick={() => void schliessen()}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={laeuft || !f.bezeichnung.trim()}>
              {t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function KartenFelder({ f, feld, fehler }: { f: Formular; feld: (n: keyof Formular) => (w: string) => void; fehler: (n: keyof Formular) => string | undefined }) {
  const { t } = useTranslation()
  const ablaufFehler = fehler('monat') ?? fehler('jahr')
  return (
    <>
      <Input
        label={t('mss.vault.zahlung.nummer')}
        value={f.nummer}
        onChange={(e) => feld('nummer')(e.target.value.replace(/[^\d\s-]/g, ''))}
        error={fehler('nummer')}
        inputMode="numeric"
        autoComplete="off"
        className="font-mono"
        required
        maxLength={23}
      />
      <div>
        <span id="zahlung-ablauf" className="text-sm font-medium text-foreground">
          {t('mss.vault.zahlung.ablauf')}
        </span>
        <div className="mt-1 grid grid-cols-2 gap-2">
          <Dropdown
            aria-label={t('mss.vault.zahlung.monat')}
            aria-invalid={!!ablaufFehler}
            value={f.monat || null}
            onChange={feld('monat')}
            options={MONATE}
            placeholder={t('mss.vault.zahlung.monat')}
          />
          <Dropdown
            aria-label={t('mss.vault.zahlung.jahr')}
            aria-invalid={!!ablaufFehler}
            value={f.jahr || null}
            onChange={feld('jahr')}
            options={jahre(f.jahr)}
            placeholder={t('mss.vault.zahlung.jahr')}
          />
        </div>
        {ablaufFehler && <p className="mt-1 text-label-sm text-status-destructive">{ablaufFehler}</p>}
      </div>
      <PasswordInput
        label={t('mss.vault.zahlung.pruefnummer')}
        value={f.pruefnummer}
        onChange={(e) => feld('pruefnummer')(e.target.value.replace(/\D/g, ''))}
        error={fehler('pruefnummer')}
        inputMode="numeric"
        autoComplete="off"
        className="font-mono"
        maxLength={4}
      />
      <p className="text-label-sm text-on-surface-variant">{t('mss.vault.zahlung.pruefnummerHinweis')}</p>
    </>
  )
}
