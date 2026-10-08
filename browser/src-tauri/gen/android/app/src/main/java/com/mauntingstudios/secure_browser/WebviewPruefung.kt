package com.mauntingstudios.secure_browser

/**
 * Ob die WebView dieses Telefons den Browser tragen kann.
 *
 * Zwei Bedingungen: Die Oberfläche braucht Containerabfragen (Chrome 105), und
 * die Tabs brauchen eigene Profile, Skripte vor der Seite und einen
 * Rückkanal (`WebViewFeature.MULTI_PROFILE`, `DOCUMENT_START_SCRIPT`,
 * `WEB_MESSAGE_LISTENER`). Ohne Profile teilten Seiten Cookies und Speicher
 * mit der Oberfläche der App. Fehlt etwas, zeigt die App einen Hinweis statt
 * des Browsers (Entscheidung des Betreibers, 10/2026).
 */
object WebviewPruefung {
    const val MINDEST = 105

    fun chromeVersion(userAgent: String): Int? =
        Regex("Chrome/(\\d+)\\.").find(userAgent)?.groupValues?.get(1)?.toIntOrNull()

    /**
     * `funktionen`: ob die WebView alle nötigen Funktionen meldet. Ohne
     * erkennbare Fassung entscheiden allein die Funktionen.
     */
    fun zuAlt(userAgent: String, funktionen: Boolean): Boolean =
        !funktionen || (chromeVersion(userAgent)?.let { it < MINDEST } ?: false)
}
