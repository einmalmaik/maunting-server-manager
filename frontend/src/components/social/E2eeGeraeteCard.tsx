import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Clock, Fingerprint, ShieldCheck, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  getE2eeGeraete,
  type E2eeGeraetItem,
  type ReapproveEigenesGeraetInput,
} from '@/api/social'
import { Button, Input, PasswordInput } from '@/Singra/UI'
import {
  eigenesGeraet,
  gebeGeraetFrei,
  geraeteZuruecksetzen,
  gibMichSelbstFrei,
  sicherheitsnummer,
  vertrauteGeraete,
} from '@/services/e2eeGeraet'
import { geraeteBezeichnung } from '@/services/geraeteBezeichnung'
import { FaktorWechsel, useZweitfaktor } from '@/components/auth/ZweitfaktorNachweis'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { GeraetEntfernenDialog } from './GeraetEntfernenDialog'

/**
 * Die Geräte, an die Nachrichten für dieses Konto verschlüsselt werden.
 *
 * Bis 09/2026 gab es diese Liste nicht, und das war eine Lücke: wer sich in
 * einem fremden Browser angemeldet hatte, ließ dort einen Schlüssel zurück, der
 * weiter Post bekam. Die blinde Mailbox steht bewusst jedem Angemeldeten offen,
 * der Geräteschlüssel ist also die einzige Schranke davor. Sichtbar war er
 * nirgends, und entfernen ließ er sich schon gar nicht.
 *
 * Seit 09/2026 sperrt Entfernen aus: die Sitzung des Geräts fällt sofort, und
 * meldet es sich wieder an, wartet es auf Freigabe. Freigeben und Entfernen
 * unterschreibt dieses Gerät (`gebeGeraetFrei`, `entferneGeraet`) — und kann
 * es deshalb nur, solange es selbst freigegeben ist.
 *
 * **Freigeben heißt auch nachziehen.** Ein Gerät kann freigegeben sein, ohne
 * dass eine Unterschrift darunter steht (Erstgerät, `wieder_freigeben`); die
 * Signaturkette nennt das „Ohne gültige Freigabe“, und bis 10/2026 gab es
 * dafür keinen Knopf. Jetzt steht er in beiden Fällen.
 *
 * **Entfernen fragt nach dem Nachweis.** Der Dialog (`GeraetEntfernenDialog`)
 * holt ihn nach der Kaskade aus dem Fehlerbericht: eingerichteter zweiter
 * Faktor, sonst Passwort, und nur bei einem Konto ohne beides die Sitzung.
 *
 * Das eigene Gerät steht in der Liste, hat aber keinen Knopf. Es würde sich
 * sofort wieder eintragen, und im selben Tab nicht einmal das:
 * `geraetVeroeffentlichen` meldet sich je Sitzung genau einmal — und der
 * Server weist den Selbstausschluss ab (`entfernen_hindernis`).
 */
export function E2eeGeraeteCard() {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  // Ohne eigenes Passwort (nur Social Login) bestaetigt das Wort RESET.
  const isSocialAccount = user?.has_password === false
  const eigeneId = user?.id
  const [geraete, setGeraete] = useState<E2eeGeraetItem[] | null>(null)
  /**
   * Freigegebene Geräte, deren Freigabe **keine** Unterschrift trägt.
   *
   * `is_approved` ist ein Wort des Servers. Die Warnung im Chat glaubt ihm
   * nicht, sondern prüft die Signaturkette (`vertrauteGeraete`), und deshalb
   * konnte die Liste „freigegeben“ zeigen, während der Chat sagte: „Es bekommt
   * nichts.“ Genau dieser Widerspruch stand bis 10/2026 im Fehlerbericht.
   *
   * `null` heißt: noch nicht geprüft (oder die Prüfung ist gescheitert). Dann
   * steht hier kein Abzeichen — lieber nichts behaupten als etwas Falsches.
   */
  const [nichtGedeckt, setNichtGedeckt] = useState<Set<string> | null>(null)
  const [sicherheitsnummern, setSicherheitsnummern] = useState<Record<string, string>>({})
  const [fehler, setFehler] = useState(false)
  const [meineKennung, setMeineKennung] = useState<string | null>(null)
  /** Das Gerät, dessen Entfernen gerade bestätigt wird — null heißt: Dialog zu. */
  const [zuEntfernen, setZuEntfernen] = useState<E2eeGeraetItem | null>(null)
  const [laeuft, setLaeuft] = useState<string | null>(null)
  const [passwort, setPasswort] = useState('')
  const [confirmationWord, setConfirmationWord] = useState('')
  const faktor = useZweitfaktor()

  const laden = useCallback(async () => {
    if (!eigeneId) return
    try {
      // Direkt und nicht über `geraeteVon`: dessen Zehn-Minuten-Frist trägt den
      // Sendepfad, hier zeigte sie einen Stand von vor zehn Minuten an.
      // includeUnapproved = true, damit ausstehende Geräte freigegeben werden können.
      const liste = await getE2eeGeraete(eigeneId, true)
      setGeraete(liste)
      setFehler(false)
      // Welche freigegebenen Geräte deckt eine Unterschrift? Geprüft wird nur
      // die freigegebene Teilmenge: ein wartendes Gerät hat nie etwas bekommen
      // und darf durch bloßes Anzeigen auch nicht in die Vertrauensliste
      // wandern (der erste Abruf schreibt sie fest).
      const freigegeben = liste.filter((g) => g.is_approved !== false)
      try {
        const gedeckt = await vertrauteGeraete(eigeneId, freigegeben)
        const gedeckteIds = new Set(gedeckt.map((g) => g.device_id))
        setNichtGedeckt(
          new Set(freigegeben.filter((g) => !gedeckteIds.has(g.device_id)).map((g) => g.device_id)),
        )
      } catch {
        setNichtGedeckt(null)
      }
      const nummern: Record<string, string> = {}
      for (const item of liste) {
        if (item.public_key) {
          try {
            nummern[item.device_id] = await sicherheitsnummer(item.public_key)
          } catch {
            nummern[item.device_id] = ''
          }
        }
      }
      setSicherheitsnummern(nummern)
    } catch {
      setFehler(true)
    }
  }, [eigeneId])

  useEffect(() => {
    void laden()
  }, [laden])

  // Ein Gerät meldet sich, wird freigegeben oder entfernt: das Backend sagt es
  // allen offenen Listen des Kontos. Bis 5.0.3 erschien ein frisch
  // gekoppeltes Gerät erst nach dem Neuladen der Seite zum Freigeben.
  useEffect(() => {
    const beiEreignis = (e: Event) => {
      if ((e as CustomEvent<{ entity?: string }>).detail?.entity === 'e2ee_devices') void laden()
    }
    window.addEventListener('msm:sync-event', beiEreignis)
    return () => window.removeEventListener('msm:sync-event', beiEreignis)
  }, [laden])

  useEffect(() => {
    eigenesGeraet()
      .then((g) => setMeineKennung(g.kennung))
      .catch(() => setMeineKennung(null))
  }, [eigeneId])

  const freigeben = async (geraet: E2eeGeraetItem) => {
    setLaeuft(geraet.device_id)
    try {
      await gebeGeraetFrei(geraet)
      toast.success(t('profile.e2eeDevices.approved'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }


  const neuBeginnen = async () => {
    if (!passwort) return
    setLaeuft('reset')
    try {
      await geraeteZuruecksetzen(passwort)
      setPasswort('')
      toast.success(t('profile.e2eeDevices.resetDone'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }

  /**
   * Holt dieses Gerät zurück, weil es das einzige ist — ohne Neuanfang.
   *
   * Der Nachweis kommt von `useZweitfaktor`, also vom eingerichteten Faktor
   * oder vom Passwort; was das Konto nicht hat, wird nicht angeboten.
   *
   * **Der Nachweis wird hier geholt, nicht vom Aufrufer.** Bis 10/2026 stand
   * `await faktor.nachweis(…)` beim Knopf davor: brach der Passkey ab oder
   * scheiterte er, lief die Ablehnung ohne Laufanzeige und ohne Meldung ins
   * Leere, und die Knöpfe blieben während der ganzen Abfrage bedienbar. Jetzt
   * liegt beides in einem `try`, wie beim Neuanfang daneben.
   */
  const wiederFreigeben = async (nachweis: ReapproveEigenesGeraetInput | (() => Promise<ReapproveEigenesGeraetInput>)) => {
    setLaeuft('reapprove')
    try {
      const beleg = typeof nachweis === 'function' ? await nachweis() : nachweis
      await gibMichSelbstFrei(beleg)
      setPasswort('')
      setConfirmationWord('')
      faktor.setCode('')
      toast.success(t('profile.e2eeDevices.reapproveDone'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }

  const wiederFreigebenMitFaktor = async () => {
    if (!faktor.bereit) return
    await wiederFreigeben(() => faktor.nachweis('e2ee_reapprove'))
  }

  const neuBeginnenMitPasskey = async () => {
    setLaeuft('reset')
    try {
      await geraeteZuruecksetzen(await faktor.nachweis('e2ee_reset'))
      toast.success(t('profile.e2eeDevices.resetDone'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }

  const neuBeginnenMitTotp = async () => {
    if (!faktor.bereit) return
    setLaeuft('reset')
    try {
      await geraeteZuruecksetzen(await faktor.nachweis('e2ee_reset'))
      faktor.setCode('')
      toast.success(t('profile.e2eeDevices.resetDone'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }

  const neuBeginnenMitConfirmation = async () => {
    if (confirmationWord.trim().toUpperCase() !== 'RESET') return
    setLaeuft('reset')
    try {
      await geraeteZuruecksetzen({ confirmation: 'RESET' })
      setConfirmationWord('')
      toast.success(t('profile.e2eeDevices.resetDone'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }

  const meinEintrag = geraete?.find((g) => g.device_id === meineKennung)
  // Freigeben und Entfernen freigegebener Geräte unterschreibt dieses Gerät;
  // der Server nimmt das nur von einem freigegebenen an.
  const ichBinFrei = meinEintrag?.is_approved !== false
  // Gibt es noch ein anderes freigegebenes Gerät, ist die Unterschrift der
  // vorgesehene Weg — der Server lehnt „wieder zulassen" dann mit 403 ab.
  const andereFrei = geraete?.some(
    (g) => g.device_id !== meinEintrag?.device_id && g.is_approved !== false,
  ) === true

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="e2ee-devices-title">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h2 id="e2ee-devices-title" className="font-headline text-title-lg font-semibold text-on-surface">
          {t('profile.e2eeDevices.title')}
        </h2>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t('profile.e2eeDevices.description')}
      </p>

      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t('profile.e2eeDevices.approvalHelp')}
      </p>

      {meinEintrag && !ichBinFrei && (
        <div className="space-y-3 rounded-lg border border-status-warning/30 bg-status-warning/10 p-4">
          <p className="text-sm text-on-surface">{t('profile.e2eeDevices.thisDevicePending')}</p>
          <p className="text-sm text-on-surface-variant">
            {t(andereFrei ? 'profile.e2eeDevices.reapproveOtherDevice' : 'profile.e2eeDevices.reapproveHelp')}
          </p>
          <div className="space-y-2">
            <p className="text-sm font-medium text-on-surface">{t('profile.e2eeDevices.resetTitle')}</p>
            <p className="text-sm text-on-surface-variant">{t('profile.e2eeDevices.resetHelp')}</p>

            {faktor.wahl === 'passkey' ? (
              <div className="space-y-2 pt-2">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  {!andereFrei && (
                    <Button
                      variant="primary"
                      disabled={laeuft !== null}
                      onClick={() => void wiederFreigebenMitFaktor()}
                    >
                      <ShieldCheck className="h-4 w-4 mr-2" aria-hidden="true" />
                      {t('profile.e2eeDevices.reapprove')}
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    disabled={laeuft !== null}
                    onClick={() => void neuBeginnenMitPasskey()}
                  >
                    <Fingerprint className="h-4 w-4 mr-2" />
                    {t('profile.e2eeDevices.resetPasskey')}
                  </Button>
                </div>
                <FaktorWechsel faktor={faktor} disabled={laeuft !== null} />
              </div>
            ) : faktor.wahl === 'totp' ? (
              <div className="space-y-3 pt-2">
                <div className="space-y-1">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <Input
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      maxLength={6}
                      aria-label={t('profile.e2eeDevices.resetTotp')}
                      placeholder={t('profile.e2eeDevices.resetTotp')}
                      value={faktor.code}
                      onChange={(e) => faktor.setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                      className="font-mono tracking-widest"
                    />
                    {!andereFrei && (
                      <Button
                        variant="primary"
                        disabled={!faktor.bereit || laeuft !== null}
                        onClick={() => void wiederFreigebenMitFaktor()}
                      >
                        {t('profile.e2eeDevices.reapprove')}
                      </Button>
                    )}
                    <Button
                      variant="secondary"
                      disabled={!faktor.bereit || laeuft !== null}
                      onClick={() => void neuBeginnenMitTotp()}
                      className="text-error hover:bg-error/10 hover:text-error"
                    >
                      {t('profile.e2eeDevices.resetButton')}
                    </Button>
                  </div>
                </div>
                <FaktorWechsel faktor={faktor} disabled={laeuft !== null} />
              </div>
            ) : isSocialAccount ? (
              <div className="space-y-2 pt-2">
                <p className="text-xs text-on-surface-variant">
                  {t('profile.e2eeDevices.resetSocialHelp')}
                </p>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <Input
                    aria-label={t('profile.e2eeDevices.resetSocialConfirmation')}
                    placeholder={t('profile.e2eeDevices.resetSocialConfirmation')}
                    value={confirmationWord}
                    onChange={(e) => setConfirmationWord(e.target.value)}
                    className="min-w-0 flex-1 font-mono"
                  />
                  {!andereFrei && (
                    <Button
                      variant="primary"
                      disabled={confirmationWord.trim().toUpperCase() !== 'RESET' || laeuft !== null}
                      onClick={() => void wiederFreigeben({ confirmation: 'RESET' })}
                    >
                      {t('profile.e2eeDevices.reapprove')}
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    disabled={confirmationWord.trim().toUpperCase() !== 'RESET' || laeuft !== null}
                    onClick={() => void neuBeginnenMitConfirmation()}
                    className="text-error hover:bg-error/10 hover:text-error"
                  >
                    {t('profile.e2eeDevices.resetButton')}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center pt-2">
                <PasswordInput
                  autoComplete="current-password"
                  aria-label={t('profile.e2eeDevices.resetPassword')}
                  placeholder={t('profile.e2eeDevices.resetPassword')}
                  value={passwort}
                  onChange={(e) => setPasswort(e.target.value)}
                  className="min-w-0 flex-1"
                />
                {!andereFrei && (
                  <Button
                    variant="primary"
                    disabled={!passwort || laeuft !== null}
                    onClick={() => void wiederFreigeben({ password: passwort })}
                  >
                    {t('profile.e2eeDevices.reapprove')}
                  </Button>
                )}
                <Button
                  variant="secondary"
                  disabled={!passwort || laeuft !== null}
                  onClick={() => void neuBeginnen()}
                  className="text-error hover:bg-error/10 hover:text-error"
                >
                  {t('profile.e2eeDevices.resetButton')}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      {fehler && (
        <p className="text-sm text-on-surface-variant">
          {t('profile.e2eeDevices.loadFailed')}
        </p>
      )}

      {!fehler && geraete?.length === 0 && (
        <p className="text-sm text-on-surface-variant">
          {t('profile.e2eeDevices.empty')}
        </p>
      )}

      {!fehler && geraete && geraete.length > 0 && (
        <ul className="divide-y divide-outline-variant/30 border-t border-outline-variant/30 pt-2">
          {geraete.map((geraet) => {
            const istMeins = geraet.device_id === meineKennung
            const nummer = sicherheitsnummern[geraet.device_id]
            return (
              <li
                key={geraet.device_id}
                className="flex flex-col justify-between gap-4 py-3 sm:flex-row sm:items-center"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-on-surface">
                      {/* Ein Eintrag ohne Namen ist kein Name: das eigene Gerät
                          zeigt deshalb seine abgeleitete Bezeichnung, statt
                          sich selbst „unbenannt" zu nennen. Für fremde Geräte
                          füllt der Server den Standardnamen ein. */}
                      {geraet.label ||
                        (istMeins ? geraeteBezeichnung() : t('profile.e2eeDevices.unnamed'))}
                    </span>
                    {istMeins && (
                      <span className="inline-flex items-center rounded-full border border-secondary/20 bg-secondary/10 px-2 py-0.5 text-xs font-medium text-secondary">
                        {t('profile.e2eeDevices.thisDevice')}
                      </span>
                    )}
                    {geraet.is_approved === false && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-status-warning/30 bg-status-warning/10 px-2 py-0.5 text-xs font-medium text-status-warning">
                        <Clock className="w-3 h-3" />
                        {t('profile.e2eeDevices.pending')}
                      </span>
                    )}
                    {geraet.is_approved !== false && nichtGedeckt?.has(geraet.device_id) && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-error/30 bg-error/10 px-2 py-0.5 text-xs font-medium text-error">
                        <AlertTriangle className="w-3 h-3" aria-hidden="true" />
                        {t('profile.e2eeDevices.notCovered')}
                      </span>
                    )}
                  </div>
                  {/* Die Kennung steht im Klartext in jedem Umschlag. Sie hier
                      zu zeigen verrät nichts und ist der einzige Weg, zwei
                      unbenannte Browser auseinanderzuhalten. */}
                  <div className="flex flex-wrap items-center gap-x-2 font-mono text-xs text-on-surface-variant">
                    <span>{geraet.device_id.slice(0, 12)}</span>
                    {nummer && (
                      <>
                        <span className="text-outline-variant/60" aria-hidden="true">•</span>
                        <span className="text-on-surface-variant/80">
                          {t('profile.e2eeDevices.safetyNumber')}: {nummer}
                        </span>
                      </>
                    )}
                  </div>
                  {geraet.is_approved !== false && nichtGedeckt?.has(geraet.device_id) && (
                    <p className="max-w-2xl text-xs text-on-surface-variant">
                      {t('profile.e2eeDevices.notCoveredHelp')}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 self-start sm:self-auto">
                  {/*
                    Freigeben heißt zweierlei: ein wartendes Gerät zulassen und
                    eine Freigabe nachziehen, die keine Unterschrift trägt.
                    Bis 10/2026 stand hier nur `is_approved === false`, und
                    genau daran scheiterte der Fehlerbericht: das Web-Frontend
                    war freigegeben (Zustimmung des Servers), aber kein Gerät
                    hatte seinen Schlüssel unterschrieben. Auf dem Telefon stand
                    daneben „Ohne gültige Freigabe" — und kein Knopf.
                  */}
                  {!istMeins &&
                    ichBinFrei &&
                    (geraet.is_approved === false ||
                      nichtGedeckt?.has(geraet.device_id) === true) && (
                    <Button
                      variant="primary"
                      disabled={laeuft === geraet.device_id}
                      onClick={() => void freigeben(geraet)}
                    >
                      <CheckCircle2 className="h-4 w-4 mr-1" aria-hidden="true" />
                      {t('profile.e2eeDevices.approve')}
                    </Button>
                  )}
                  {/*
                    Jedes freigegebene Gerät entfernt jedes andere — auch das
                    Web-Frontend. Genau dafür ist dieser Knopf da: eine fremde
                    oder übernommene Browser-Sitzung muss von der MSS-App oder
                    dem Browser aus verschwinden können.
                  */}
                  {!istMeins && (ichBinFrei || geraet.is_approved === false) && (
                    <Button
                      variant="secondary"
                      disabled={laeuft === geraet.device_id}
                      onClick={() => setZuEntfernen(geraet)}
                      className="text-error hover:bg-error/10 hover:text-error"
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                      {t('profile.e2eeDevices.remove')}
                    </Button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {zuEntfernen && (
        <GeraetEntfernenDialog
          geraet={zuEntfernen}
          sicherheitsnummer={sicherheitsnummern[zuEntfernen.device_id]}
          onOpenChange={(offen) => {
            if (!offen) setZuEntfernen(null)
          }}
          onEntfernt={laden}
        />
      )}
    </section>
  )
}
