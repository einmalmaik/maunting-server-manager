/**
 * Zweite Stufe des Bootstraps: ab hier darf der volle Modulgraph laden —
 * `main.tsx` hat den API-Origin bereits gesetzt.
 *
 * Schriften und Stylesheet sind dieselben wie im Panel (`src/main.tsx`
 * erklärt, warum die Schriften lokal liegen). Der Query-Parameter
 * `?fenster=overlay` wählt das Overlay-Fenster — ein Bundle, zwei Fenster.
 */
import React from 'react'
import ReactDOM from 'react-dom/client'

import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/manrope/600.css'
import '@fontsource/manrope/700.css'
import '@fontsource/manrope/800.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'

import '@/i18n'
import '@/index.css'

import { transportEinrichten } from './transport'
import { DesktopRoot } from './DesktopRoot'

transportEinrichten()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <DesktopRoot />
  </React.StrictMode>,
)
