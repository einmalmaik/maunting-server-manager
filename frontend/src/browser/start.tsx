/**
 * Zweite Stufe: Schriften, Stylesheet, Transport. Dieselben wie in MSS
 * (`desktop/start.tsx`), damit die geteilten Seiten gleich aussehen.
 */
import { StrictMode } from 'react'
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

import { textBereit } from '@/i18n'
import '@/index.css'
import './browser.css'

import { ErrorBoundary } from '@/components/ErrorBoundary'
import { transportEinrichten } from '@/desktop/transport'
import { initOfflineSync } from '@/lib/offlineSync'
import { BrowserApp } from './BrowserApp'

transportEinrichten()
initOfflineSync()

void textBereit.finally(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ErrorBoundary>
        <BrowserApp />
      </ErrorBoundary>
    </StrictMode>,
  )
})
