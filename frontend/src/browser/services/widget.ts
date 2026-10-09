/**
 * Das Such-Widget unter Android (`SuchWidget.kt`) und Links aus anderen
 * Apps: was sie anstoßen, holt die Oberfläche beim Start und bei jedem neuen
 * Anstoß ab (`widget.rs`), einmal. Suche öffnet einen neuen Tab mit der
 * Adresszeile, Sprache sucht das Gesagte, ein Foto geht an die Bildsuche der
 * gewählten Suchmaschine, ein Link öffnet in einem neuen Tab.
 *
 * Umgekehrt erfährt das Widget, ob es die Kamera zeigen soll: nur, wenn die
 * gewählte Suchmaschine Bilder sucht.
 */
import { useEffect } from 'react'

import { toast } from '@/stores/toastStore'
import i18n from '@/i18n'

import { useEinstellungenStore } from './einstellungenStore'
import { nativ, widgetAnstoesse, type WidgetStart } from './nativ'
import { istWebseite } from './intern'
import { istAndroid } from './plattform'
import { baueZielUrl, suchmaschine } from './searchEngines'
import { useTabsStore } from './tabsStore'

export function widgetAusfuehren(start: WidgetStart, adresszeile: () => void): void {
  const tabs = useTabsStore.getState()
  const { suchmaschine: id, searxngUrl } = useEinstellungenStore.getState()
  if (start.art === 'link') {
    // Ob ein Tab die Adresse laden darf, prüft Rust beim Laden (`tabs::weg`).
    if (istWebseite(start.url)) tabs.neuerTab(start.url)
    return
  }
  if (start.art === 'text') {
    const ziel = baueZielUrl(start.text, id, searxngUrl)
    if (ziel) tabs.neuerTab(ziel)
    return
  }
  if (start.art === 'bild') {
    const bild = suchmaschine(id).bild
    if (bild) {
      tabs.bildsuche(bild)
      return
    }
    // Inzwischen eine Suchmaschine ohne Bildsuche gewählt: das Widget zeigt
    // die Kamera nicht mehr, und das Foto fällt weg (`widgetStand`).
    toast.error(i18n.t('browser.widget.keineBildsuche'))
    void nativ.widgetStand(false).catch(() => null)
    return
  }
  tabs.neuerTab()
  adresszeile()
  // Nach dem Zeichnen der neuen Startseite, die selbst ein Feld fokussiert.
  requestAnimationFrame(() => void nativ.tastaturZeigen().catch(() => null))
}

export function useWidget(adresszeile: () => void, startseite: () => void): void {
  const id = useEinstellungenStore((s) => s.suchmaschine)

  useEffect(() => {
    if (istAndroid()) void nativ.widgetStand(Boolean(suchmaschine(id).bild)).catch(() => null)
  }, [id])

  useEffect(() => {
    if (!istAndroid()) return
    const abholen = () =>
      void nativ
        .widgetStart()
        .then((start) => {
          if (!start) return
          startseite()
          widgetAusfuehren(start, adresszeile)
        })
        .catch(() => null)
    abholen()
    let aus = () => {}
    let weg = false
    void widgetAnstoesse(abholen).then((f) => (weg ? f() : (aus = f)))
    return () => {
      weg = true
      aus()
    }
  }, [adresszeile, startseite])
}
