import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './i18n'

/*
 * Schriften: lokal aus dem Build, nicht von Google.
 *
 * In index.html standen drei <link>-Zeilen, die die vier Familien zur Laufzeit
 * bei Google holten. Damit ging bei jedem Aufruf des Panels die IP-Adresse
 * jedes Besuchers an einen Dritten — ein Risiko, das nicht der Betreiber
 * gewählt hätte, sondern das Panel für ihn (LG München I, 20.01.2022,
 * Az. 3 O 17493/20). Ein Panel im abgeschotteten Netz bekam die Schriften
 * außerdem gar nicht und fiel auf system-ui zurück.
 *
 * Eingebunden sind genau die Schnitte, die die alte URL anforderte und die
 * tailwind.config.ts unter `fontFamily` benennt — jede weitere Datei wäre
 * Gewicht ohne Leser. Die Subset-Aufteilung (latin, latin-ext, cyrillic,
 * greek, vietnamese) bleibt bewusst erhalten: `unicode-range` entscheidet im
 * Browser, welche Datei überhaupt geladen wird, und MSM liefert unter anderem
 * eine russische Oberfläche aus.
 *
 * Hier und nicht per `@import` in index.css: dort zieht Vites CSS-Pipeline die
 * Stylesheets ein, bevor ein Plugin sie sehen kann, und der Build behielte die
 * `woff`-Rückfallebene (vite.config.ts, `fontsourceWoff2Only`).
 *
 * Wer eine Familie oder ein Gewicht ergänzt, ergänzt es auch in
 * tailwind.config.ts — und umgekehrt.
 */
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

import './index.css'
import { registerServiceWorker } from './utils/pwa'

// Initialize PWA
registerServiceWorker()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
